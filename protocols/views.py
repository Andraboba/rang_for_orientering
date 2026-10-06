# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from urllib.parse import quote
import re

from .models import Protocol, ProtocolResult, KnownParticipant, SeasonRanking, ChampionshipLevel
from .forms import (
    LoadProtocolForm,
    PDFProtocolLoadForm,
    ProcessProtocolForm,
    SetProtocolEventDateForm,
    SeasonExportDateRangeForm,
    EditProtocolTitleForm,
)
from .services.source_router import (
    KIND_ORGEO,
    KIND_PDF_OCR,
    KIND_PDF_SUMMARY,
    KIND_SPORTIDENT,
    KIND_SPORTORG,
    KIND_WINORIENT,
    load_from_url,
    parse_payload,
    show_all_team_name,
)
from .services.group_filter import filter_by_group_rules
from .services.season_export import (
    excluded_name_pairs,
    export_season_excel,
    export_season_excel_by_period,
    export_season_excel_by_date_range,
)
from .services.date_from_title import parse_date_from_title
from .services.title_cleaner import clean_protocol_title


def home(request):
    """Главная: список протоколов и форма загрузки по URL."""
    q = request.GET.get('q', '').strip()
    protocols = Protocol.objects.all()
    if q:
        protocols = protocols.filter(
            Q(title__icontains=q) | Q(source_url__icontains=q)
        )
    protocols = protocols[:30]
    form = LoadProtocolForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        championship_level = form.cleaned_data['championship_level']

        # Вид источника (Orgeo, SportIdent, SportOrg, PDF, WinOrient)
        # определяется автоматически — пользователю достаточно вставить ссылку.
        loaded = load_from_url(
            form.cleaned_data['url'],
            encoding_utf8=form.cleaned_data['encoding_utf8'],
            format_sfr=form.cleaned_data['format_sfr'],
        )
        for warning in loaded.warnings:
            messages.warning(request, warning)
        if loaded.error:
            messages.error(request, loaded.error)
            return redirect('protocols:home')

        raw_title = loaded.title or loaded.url[:80]
        protocol = Protocol.objects.create(
            source_url=loaded.url,
            source_kind=loaded.kind,
            title=clean_protocol_title(raw_title) or raw_title,
            raw_html=loaded.payload,
            encoding_utf8=loaded.encoding_utf8,
            format_sfr=loaded.format_sfr,
            championship_level=championship_level,
        )
        if loaded.event_date:
            try:
                from datetime import datetime

                protocol.event_date = datetime.strptime(
                    loaded.event_date, '%Y-%m-%d'
                ).date()
                protocol.save(update_fields=['event_date'])
            except (ValueError, TypeError):
                pass
        messages.success(
            request,
            'Протокол загружен. Введите название команды для обработки.',
        )
        return redirect('protocols:protocol_detail', pk=protocol.pk)
    return render(
        request,
        'protocols/home.html',
        {
            'form': form,
            'protocols': protocols,
            'championship_levels': ChampionshipLevel.objects.all().order_by('order', 'name'),
        },
    )


def protocol_detail(request, pk):
    """Просмотр протокола и форма обработки по команде."""
    protocol = get_object_or_404(Protocol, pk=pk)
    kind = protocol.kind
    is_orgeo = kind == KIND_ORGEO
    is_sportident = kind == KIND_SPORTIDENT
    is_sportorg = kind == KIND_SPORTORG
    is_pdf_summary = kind == KIND_PDF_SUMMARY
    # PDF со сканом (OCR) — единственный источник, который нельзя перепарсить.
    is_pdf = kind == KIND_PDF_OCR
    is_winorient = kind == KIND_WINORIENT
    # Дату сами определяют Orgeo, SportOrg и PDF-суммы.
    needs_manual_event_date = kind in (KIND_WINORIENT, KIND_SPORTIDENT)

    form = ProcessProtocolForm(initial={'protocol_id': protocol.pk})
    form.fields['protocol_id'].initial = protocol.pk

    results_qs = protocol.results.select_related(
        'protocol',
        'protocol__championship_level',
    ).prefetch_related('protocol__group_levels__championship_level')
    # Для PDF-протоколов храним все результаты; «командные» — team_member=True.
    if is_pdf:
        results = results_qs.filter(team_member=True)
        all_pdf_results = results_qs.order_by('group_name', 'place')
    else:
        results = results_qs
        all_pdf_results = None

    # Для источников без API-даты (WinOrient/SportIdent):
    # дата соревнований — из названия или запрос пользователю.
    date_form = None
    show_date_form = False
    if needs_manual_event_date and not protocol.event_date:
        parsed_date = parse_date_from_title(protocol.title or '')
        date_form = SetProtocolEventDateForm(
            initial={
                'protocol_id': protocol.pk,
                'event_date': protocol.event_date or parsed_date,
            }
        )
        show_date_form = True

    # Режим "показать всех участников" для отладки/проверки.
    show_all = request.GET.get('show_all') == '1'
    if show_all and not is_pdf:
        parsed = parse_payload(
            kind,
            protocol.raw_html,
            base_pairs=[],
            team_name=show_all_team_name(kind),
            format_sfr=protocol.format_sfr,
        )
        protocol.results.all().delete()
        for r in parsed:
            ProtocolResult.objects.create(
                protocol=protocol,
                group_name=r.get('group_name', ''),
                day=r.get('day', 1),
                place=r.get('place', ''),
                place_out_of=r.get('place_out_of'),
                last_name=r['last_name'],
                first_name=r['first_name'],
                points=r.get('points', 0),
                raw_line=r.get('raw_line', ''),
            )
        results = protocol.results.select_related(
            'protocol',
            'protocol__championship_level',
        )

    if request.method == 'POST':
        # PDF-протоколы не перепарсиваются — результаты уже загружены при импорте.
        if is_pdf:
            messages.info(
                request,
                'PDF-протокол нельзя перепарсить — результаты загружены при импорте PDF. '
                'Для обновления загрузите PDF снова.',
            )
            return redirect('protocols:protocol_detail', pk=pk)

        form = ProcessProtocolForm(
            request.POST, initial={'protocol_id': protocol.pk}
        )
        form.fields['protocol_id'].initial = protocol.pk
        if form.is_valid():
            team_name = (form.cleaned_data.get('team_name') or '').strip()

            # Список уже известных участников (СК Фотон и др.).
            # Помеченных «не учитывать никогда» не ищем вовсе, а «только за клуб»
            # находятся лишь по названию команды, не по базе.
            base_pairs = list(
                KnownParticipant.objects.filter(excluded=False, club_only=False)
                .values_list('last_name', 'first_name')
            )
            excluded_pairs = excluded_name_pairs()

            # WinOrient не умеет «показать всех»: без команды и без базы
            # фильтровать не по чему.
            if is_winorient and not team_name and not base_pairs:
                messages.warning(
                    request,
                    'Введите название команды или добавьте участников в базу.',
                )
                return redirect('protocols:protocol_detail', pk=pk)

            # Парсер выбирается по виду источника; правила фильтрации общие.
            parsed = parse_payload(
                kind,
                protocol.raw_html,
                base_pairs=base_pairs,
                team_name=team_name,
                format_sfr=protocol.format_sfr,
            )

            # Исключённых отбрасываем, даже если они нашлись по названию команды.
            parsed = [
                r for r in parsed
                if (r['last_name'].strip().lower(), r['first_name'].strip().lower())
                not in excluded_pairs
            ]
            # У полных тёзок из разных групп берём только «свои» группы.
            parsed = filter_by_group_rules(parsed)

            protocol.results.all().delete()
            for r in parsed:
                ProtocolResult.objects.create(
                    protocol=protocol,
                    group_name=r.get('group_name', ''),
                    day=r.get('day', 1),
                    place=r.get('place', ''),
                    place_out_of=r.get('place_out_of'),
                    last_name=r['last_name'],
                    first_name=r['first_name'],
                    points=r.get('points', 0),
                    raw_line=r.get('raw_line', ''),
                )
            # Обновляем базу известных участников (для WinOrient логика как раньше).
            for last_name, first_name in base_pairs:
                if (last_name.strip().lower(), first_name.strip().lower()) in excluded_pairs:
                    continue
                KnownParticipant.objects.get_or_create(
                    last_name=last_name,
                    first_name=first_name,
                )
            messages.success(request, f'Найдено результатов: {len(parsed)}.')
            return redirect('protocols:protocol_detail', pk=pk)
    protocol_cleaned_title = (
        clean_protocol_title(protocol.title or '') or 'Протокол'
    )
    title_form = EditProtocolTitleForm(
        initial={
            'protocol_id': protocol.pk,
            'title': protocol.title or '',
        }
    )
    return render(
        request,
        'protocols/protocol_detail.html',
        {
            'protocol': protocol,
            'protocol_cleaned_title': protocol_cleaned_title,
            'title_form': title_form,
            'form': form,
            'results': results,
            'all_pdf_results': all_pdf_results,
            'is_orgeo': is_orgeo,
            'is_sportident': is_sportident,
            'is_sportorg': is_sportorg,
            'is_pdf_summary': is_pdf_summary,
            'is_pdf': is_pdf,
            'kind': kind,
            # Перепарсить «всех подряд» можно везде, кроме скана PDF.
            'can_show_all': not is_pdf and not is_winorient,
            # Многодневка: каждый день — отдельный старт, показываем номер дня.
            'show_day': protocol.results.values('day').distinct().count() > 1,
            'date_form': date_form,
            'show_date_form': show_date_form,
        },
    )


@require_http_methods(['POST'])
def set_protocol_title(request, pk):
    """Сохранить отредактированное пользователем название протокола."""
    protocol = get_object_or_404(Protocol, pk=pk)
    form = EditProtocolTitleForm(
        request.POST, initial={'protocol_id': protocol.pk}
    )
    if form.is_valid():
        new_title = (form.cleaned_data.get('title') or '').strip()
        protocol.title = new_title or None
        protocol.save(update_fields=['title'])
        messages.success(request, 'Название протокола сохранено.')
    else:
        messages.warning(request, 'Введите название.')
    return redirect('protocols:protocol_detail', pk=pk)


@require_http_methods(['POST'])
def set_protocol_event_date(request, pk):
    """Сохранить дату соревнований для протокола (WinOrient)."""
    protocol = get_object_or_404(Protocol, pk=pk)
    if protocol.kind == KIND_ORGEO:
        messages.warning(
            request, 'Для протоколов Orgeo дата берётся из события.'
        )
        return redirect('protocols:protocol_detail', pk=pk)
    form = SetProtocolEventDateForm(
        request.POST, initial={'protocol_id': protocol.pk}
    )
    if form.is_valid():
        protocol.event_date = form.cleaned_data['event_date']
        protocol.save(update_fields=['event_date'])
        messages.success(request, 'Дата соревнований сохранена.')
    else:
        messages.warning(request, 'Укажите корректную дату.')
    return redirect('protocols:protocol_detail', pk=pk)


@require_http_methods(['GET'])
def known_participants(request):
    """Список участников в базе (уже учтённых)."""
    participants = KnownParticipant.objects.all().order_by(
        'last_name', 'first_name'
    )
    return render(
        request,
        'protocols/known_participants.html',
        {
            'participants': participants,
            'excluded_count': sum(1 for p in participants if p.excluded),
        },
    )


@require_http_methods(['GET'])
def export_results(request, pk):
    """Скачать результаты протокола как TXT."""
    protocol = get_object_or_404(Protocol, pk=pk)
    results = protocol.results.all()
    lines = [r.raw_line for r in results]

    base_title = clean_protocol_title(protocol.title or '') or f'protocol_{pk}'
    safe_title = (
        re.sub(r'[^0-9A-Za-zА-Яа-я _\-\.]+', '', base_title).strip()
        or f'protocol_{pk}'
    )
    filename = safe_title.replace(' ', '_') + '.txt'

    content = '\n'.join(lines)
    # Используем бинарный тип, чтобы браузер не пытался отобразить файл,
    # а сразу предлагал скачать.
    response = HttpResponse(content, content_type='application/octet-stream')
    # Делаем корректный заголовок для не-ASCII имён файлов (RFC 5987).
    ascii_fallback = 'protocol.txt'
    quoted = quote(filename)
    response['Content-Disposition'] = (
        f"attachment; filename={ascii_fallback}; filename*=UTF-8''{quoted}"
    )
    return response


@require_http_methods(['GET', 'POST'])
def season_export_page(request):
    """Страница выбора периода (календарь) для экспорта за сезон."""
    form = SeasonExportDateRangeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        date_from = form.cleaned_data['date_from']
        date_to = form.cleaned_data['date_to']
        max_starts = form.cleaned_data.get('max_starts')
        content = export_season_excel_by_date_range(date_from, date_to, max_starts=max_starts)
        filename = f'season_{date_from}_{date_to}.xlsx'
        response = HttpResponse(
            content,
            content_type=(
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            ),
        )
        response['Content-Disposition'] = (
            f"attachment; filename*=UTF-8''{quote(filename)}"
        )
        return response
    return render(request, 'protocols/season_export.html', {'form': form})


@require_http_methods(['GET'])
def calendar_view(request):
    """Календарь соревнований: все протоколы с датами, сгруппированные по месяцам."""
    import calendar as cal_mod
    from collections import defaultdict
    from django.db.models import Q

    MONTHS_RU = [
        '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
    ]

    qs = Protocol.objects.filter(
        Q(event_date__isnull=False) | Q(event__date__isnull=False)
    ).select_related('event')

    dated_protocols = []
    for p in qs:
        d = (p.event.date if p.event else None) or p.event_date
        if d:
            dated_protocols.append((d, p))

    available_years = sorted(set(d.year for d, _ in dated_protocols), reverse=True)

    try:
        selected_year = int(request.GET['year']) if request.GET.get('year') else None
    except (ValueError, TypeError):
        selected_year = None

    if not selected_year or selected_year not in available_years:
        selected_year = available_years[0] if available_years else None

    months = []
    if selected_year:
        by_month = defaultdict(lambda: defaultdict(list))
        for d, p in dated_protocols:
            if d.year == selected_year:
                by_month[d.month][d.day].append(p)

        for month_num in range(1, 13):
            month_events = dict(by_month[month_num])
            has_events = bool(month_events)
            weeks_raw = cal_mod.monthcalendar(selected_year, month_num)
            weeks = []
            for week in weeks_raw:
                week_days = []
                for day in week:
                    if day == 0:
                        week_days.append({'day': 0, 'protocols': [], 'has_events': False})
                    else:
                        protos = month_events.get(day, [])
                        week_days.append({'day': day, 'protocols': protos, 'has_events': bool(protos)})
                weeks.append(week_days)
            months.append({
                'month_num': month_num,
                'month_name': MONTHS_RU[month_num],
                'weeks': weeks,
                'has_events': has_events,
                'events_list': sorted(month_events.items()),
            })

    return render(request, 'protocols/calendar.html', {
        'available_years': available_years,
        'selected_year': selected_year,
        'months': months,
    })


@require_http_methods(['GET'])
def export_season_excel_view(request):
    """
    Скачать Excel за сезон: два листа — «Места» (участники по алфавиту, места по стартам)
    и «Баллы» (участники по убыванию баллов).
    Период: ?ranking_id=... или ?year=...&season=spring|autumn.
    """
    ranking_id = request.GET.get('ranking_id')
    year = request.GET.get('year')
    season = request.GET.get('season', '').strip().lower()

    if ranking_id:
        ranking = get_object_or_404(SeasonRanking, pk=ranking_id)
        content = export_season_excel(ranking)
        if getattr(ranking, 'date_from', None) and getattr(
            ranking, 'date_to', None
        ):
            filename = (
                f'season_{ranking.date_from}_{ranking.date_to}.xlsx'
            )
            response = HttpResponse(
                content,
                content_type=(
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                ),
            )
            response['Content-Disposition'] = (
                f"attachment; filename*=UTF-8''{quote(filename)}"
            )
            return response
        year, season = ranking.year, ranking.season
    elif year and season in ('spring', 'autumn'):
        try:
            year_int = int(year)
        except (TypeError, ValueError):
            return HttpResponse(
                'Некорректный год. Используйте year=YYYY.',
                status=400,
                content_type='text/plain; charset=utf-8',
            )
        content = export_season_excel_by_period(year_int, season)
    elif request.GET.get('date_from') and request.GET.get('date_to'):
        try:
            from datetime import datetime

            date_from = datetime.strptime(
                request.GET['date_from'], '%Y-%m-%d'
            ).date()
            date_to = datetime.strptime(
                request.GET['date_to'], '%Y-%m-%d'
            ).date()
        except (ValueError, TypeError):
            return HttpResponse(
                'Некорректные даты. Используйте date_from=YYYY-MM-DD&date_to=YYYY-MM-DD.',
                status=400,
                content_type='text/plain; charset=utf-8',
            )
        content = export_season_excel_by_date_range(date_from, date_to)
        filename = f'season_{date_from}_{date_to}.xlsx'
        response = HttpResponse(
            content,
            content_type=(
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            ),
        )
        response['Content-Disposition'] = (
            f"attachment; filename*=UTF-8''{quote(filename)}"
        )
        return response
    else:
        return HttpResponse(
            'Укажите период: ranking_id=... или year=...&season=spring|autumn или date_from=...&date_to=...',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    season_display = 'Весна' if season == 'spring' else 'Осень'
    if year is not None and season:
        filename = f'season_{year}_{season_display}.xlsx'
    else:
        filename = (
            f'season_ranking_{ranking_id}.xlsx' if ranking_id else 'season.xlsx'
        )
    response = HttpResponse(
        content,
        content_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
    )
    response['Content-Disposition'] = (
        f"attachment; filename*=UTF-8''{quote(filename)}"
    )
    return response


@require_http_methods(['POST'])
def load_pdf_protocol(request):
    """
    Загрузка сканированного PDF-протокола с двухэтапными результатами.
    Создаёт два Protocol-объекта: «Пролог» и «Финиш».
    Требует Tesseract OCR (winget install UB-Mannheim.TesseractOCR).
    OCR 21 страницы занимает несколько минут — страница не реагирует, это нормально.
    """
    form = PDFProtocolLoadForm(request.POST, request.FILES)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for err in field_errors:
                messages.error(request, err)
        return redirect('protocols:home')

    pdf_file = request.FILES['pdf_file']
    title_override = (form.cleaned_data.get('title') or '').strip()
    event_date_override = form.cleaned_data.get('event_date')
    championship_level = form.cleaned_data.get('championship_level')

    try:
        from .services.pdf_parser import parse_pdf_protocol
        pdf_bytes = pdf_file.read()
        parsed = parse_pdf_protocol(pdf_bytes)
    except ImportError as exc:
        messages.error(request, str(exc))
        return redirect('protocols:home')
    except Exception as exc:
        messages.error(request, f'Ошибка разбора PDF: {exc}')
        return redirect('protocols:home')

    for page_err in parsed.page_errors:
        messages.warning(request, f'OCR: {page_err}')

    if not parsed.prologue_results and not parsed.finish_results:
        messages.warning(
            request,
            'В PDF не найдено результатов. '
            'Убедитесь, что Tesseract установлен с русским языком (rus.traineddata).'
        )
        return redirect('protocols:home')

    # База известных участников для автоматической отметки team_member=True.
    # Клуба в скане не видно, поэтому «только за клуб» здесь не отмечаем.
    known = {
        (ln.strip().lower(), fn.strip().lower())
        for ln, fn in KnownParticipant.objects.filter(club_only=False)
        .values_list('last_name', 'first_name')
    }

    base_title = title_override or parsed.title or pdf_file.name

    # Дата: из формы → из PDF → None
    event_date = event_date_override
    if not event_date and parsed.event_date:
        from datetime import datetime
        for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
            try:
                event_date = datetime.strptime(parsed.event_date, fmt).date()
                break
            except ValueError:
                continue

    source_label = f'pdf_upload:{pdf_file.name}'

    # Удаляем дубликаты от предыдущей загрузки того же файла.
    old_qs = Protocol.objects.filter(source_url=source_label)
    old_count = old_qs.count()
    if old_count:
        old_qs.delete()
        messages.info(request, f'Удалены {old_count} ранее загруженных протокол(а) из файла «{pdf_file.name}».')

    prologue_protocol = None
    finish_protocol = None

    raw_ocr = parsed.raw_ocr_text  # сохраняем в оба протокола для отладки

    if parsed.prologue_results:
        prologue_protocol = Protocol.objects.create(
            source_url=source_label,
            source_kind=KIND_PDF_OCR,
            title=f'{base_title} — Пролог',
            raw_html=raw_ocr,
            encoding_utf8=True,
            format_sfr=False,
            championship_level=championship_level,
            event_date=event_date,
        )
        team_count = 0
        for r in parsed.prologue_results:
            is_known = (r['last_name'].strip().lower(), r['first_name'].strip().lower()) in known
            if is_known:
                team_count += 1
            ProtocolResult.objects.create(
                protocol=prologue_protocol,
                group_name=r['group_name'],
                place=r['place'],
                place_out_of=r['place_out_of'],
                last_name=r['last_name'],
                first_name=r['first_name'],
                points=r['points'],
                raw_line=r['raw_line'],
                team_member=is_known,
            )
        messages.success(
            request,
            f'Пролог: {team_count} участников из базы, '
            f'всего в PDF: {len(parsed.prologue_results)}. '
            'Откройте протокол, чтобы добавить пропущенных.',
        )

    if parsed.finish_results:
        finish_protocol = Protocol.objects.create(
            source_url=source_label,
            source_kind=KIND_PDF_OCR,
            title=f'{base_title} — Финиш',
            raw_html=raw_ocr,
            encoding_utf8=True,
            format_sfr=False,
            championship_level=championship_level,
            event_date=event_date,
        )
        team_count = 0
        for r in parsed.finish_results:
            is_known = (r['last_name'].strip().lower(), r['first_name'].strip().lower()) in known
            if is_known:
                team_count += 1
            ProtocolResult.objects.create(
                protocol=finish_protocol,
                group_name=r['group_name'],
                place=r['place'],
                place_out_of=r['place_out_of'],
                last_name=r['last_name'],
                first_name=r['first_name'],
                points=r['points'],
                raw_line=r['raw_line'],
                team_member=is_known,
            )
        messages.success(
            request,
            f'Финиш: {team_count} участников из базы, '
            f'всего в PDF: {len(parsed.finish_results)}.',
        )

    if not prologue_protocol and not finish_protocol:
        messages.warning(request, 'В PDF не найдено результатов.')
        return redirect('protocols:home')

    if prologue_protocol:
        return redirect('protocols:protocol_detail', pk=prologue_protocol.pk)
    if finish_protocol:
        return redirect('protocols:protocol_detail', pk=finish_protocol.pk)
    return redirect('protocols:home')


@require_http_methods(['GET'])
def pdf_ocr_text(request, pk):
    """Показывает сырой OCR-текст PDF-протокола для диагностики."""
    protocol = get_object_or_404(Protocol, pk=pk)
    if protocol.kind != KIND_PDF_OCR:
        return HttpResponse('Не PDF протокол.', status=400, content_type='text/plain; charset=utf-8')
    return HttpResponse(
        protocol.raw_html or '(OCR-текст не сохранён — загрузите PDF снова)',
        content_type='text/plain; charset=utf-8',
    )


@require_http_methods(['POST'])
def pdf_update_team(request, pk):
    """
    Обновляет состав команды в PDF-протоколе:
    принимает список ID выбранных ProtocolResult и ставит им team_member=True,
    остальным — False.
    """
    protocol = get_object_or_404(Protocol, pk=pk)
    if protocol.kind != KIND_PDF_OCR:
        messages.error(request, 'Функция доступна только для PDF-протоколов.')
        return redirect('protocols:protocol_detail', pk=pk)

    selected_ids = set(request.POST.getlist('participant_ids'))
    # Сбрасываем все, затем включаем выбранные.
    protocol.results.update(team_member=False)
    added_to_db = 0
    if selected_ids:
        protocol.results.filter(pk__in=selected_ids).update(team_member=True)
        # Добавляем выбранных в базу участников, если их там ещё нет.
        selected_results = protocol.results.filter(pk__in=selected_ids)
        for r in selected_results:
            _, created = KnownParticipant.objects.get_or_create(
                last_name=r.last_name,
                first_name=r.first_name,
            )
            if created:
                added_to_db += 1

    msg = f'Состав команды обновлён: {len(selected_ids)} участников.'
    if added_to_db:
        msg += f' В базу добавлено новых: {added_to_db}.'
    messages.success(request, msg)
    return redirect('protocols:protocol_detail', pk=pk)


@require_http_methods(['POST'])
def delete_protocol(request, pk):
    """Удалить протокол вместе с его результатами и вернуть на главную."""
    protocol = get_object_or_404(Protocol, pk=pk)
    protocol.delete()
    messages.success(request, 'Протокол удалён.')
    return redirect('protocols:home')
