# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from urllib.parse import urlparse, parse_qs, urlencode, quote
import re

from .models import Protocol, ProtocolResult, KnownParticipant, SeasonRanking
from .forms import (
    LoadProtocolForm,
    ProcessProtocolForm,
    SetProtocolEventDateForm,
    SeasonExportDateRangeForm,
    EditProtocolTitleForm,
)
from .services.parser import (
    fetch_url,
    extract_title,
    parse_and_process,
)
from .services.orgeo_parser import parse_orgeo, fetch_orgeo_document
from .services.orgeo_browser import fetch_orgeo_with_browser, fetch_orgeo_tables, ORGEO_TABLES_DELIMITER, resolve_orgeo_url
from .services.season_export import export_season_excel, export_season_excel_by_period, export_season_excel_by_date_range
from .services.date_from_title import parse_date_from_title
from .services.title_cleaner import clean_protocol_title


def home(request):
    """Главная: список протоколов и форма загрузки по URL."""
    q = request.GET.get('q', '').strip()
    protocols = Protocol.objects.all()
    if q:
        protocols = protocols.filter(Q(title__icontains=q) | Q(source_url__icontains=q))
    protocols = protocols[:30]
    form = LoadProtocolForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        url = form.cleaned_data['url']
        encoding_utf8 = form.cleaned_data['encoding_utf8']
        format_sfr = form.cleaned_data['format_sfr']

        url_to_fetch = url
        orgeo_title = None
        orgeo_date = None  # дата соревнования (YYYY-MM-DD) из API Orgeo

        # Orgeo: resolve_orgeo_url нормализует любую ссылку
        # (live/#/winter26, live/#/48979, online/48979, finish/...) в /online/{id}
        # и возвращает название и дату соревнования.
        if 'orgeo.ru' in url:
            app_url, orgeo_title, orgeo_date, resolve_err = resolve_orgeo_url(url)
            if resolve_err:
                messages.error(request, f'Orgeo: {resolve_err}')
                return redirect('protocols:home')

            # Запомним нормализованный URL для сохранения в БД.
            url = app_url

            # Загружаем все группы соревнования.
            tables, err = fetch_orgeo_tables(app_url)
            if tables:
                text = ORGEO_TABLES_DELIMITER.join(tables)
                err = None
            else:
                if err:
                    messages.warning(request, f'Orgeo (сеть): {err}. Пробуем загрузку страницы.')
                text, err = fetch_orgeo_with_browser(app_url)
                if err or not text:
                    text, err = fetch_orgeo_document(app_url)
            encoding_utf8 = True
            format_sfr = False
        else:
            text, err = fetch_url(url_to_fetch, encoding_utf8=encoding_utf8)
        if err:
            messages.error(request, f'Ошибка загрузки: {err}')
            return redirect('protocols:home')
        # Проверка: для Orgeo в сохранённом HTML должна быть таблица.
        if 'orgeo.ru' in url and text:
            if 'Фамилия' not in text and 'П/п' not in text and '<table' not in text:
                messages.warning(
                    request,
                    'Страница Orgeo загружена без таблицы результатов. '
                    'Установите Playwright в то же окружение, откуда запускаете Django: '
                    'pip install playwright, затем: playwright install chromium',
                )
        # Для Orgeo используем название из API, для остальных — extract_title.
        if 'orgeo.ru' in url and orgeo_title:
            title = orgeo_title
        else:
            title = extract_title(text, format_sfr)
        raw_title = title or url[:80]
        protocol = Protocol.objects.create(
            source_url=url,
            title=clean_protocol_title(raw_title) or raw_title,
            raw_html=text,
            encoding_utf8=encoding_utf8,
            format_sfr=format_sfr,
        )
        if orgeo_date:
            try:
                from datetime import datetime
                protocol.event_date = datetime.strptime(orgeo_date, '%Y-%m-%d').date()
                protocol.save(update_fields=['event_date'])
            except (ValueError, TypeError):
                pass
        messages.success(request, 'Протокол загружен. Введите название команды для обработки.')
        return redirect('protocols:protocol_detail', pk=protocol.pk)
    return render(request, 'protocols/home.html', {
        'form': form,
        'protocols': protocols,
    })


def protocol_detail(request, pk):
    """Просмотр протокола и форма обработки по команде."""
    protocol = get_object_or_404(Protocol, pk=pk)
    is_orgeo = bool(protocol.source_url and 'orgeo.ru' in protocol.source_url)
    is_winorient = not is_orgeo

    form = ProcessProtocolForm(initial={'protocol_id': protocol.pk})
    form.fields['protocol_id'].initial = protocol.pk
    results = protocol.results.all()

    # Для WinOrient: дата соревнований — из названия или запрос пользователю
    date_form = None
    show_date_form = False
    if is_winorient and not protocol.event_id:
        parsed_date = parse_date_from_title(protocol.title or '')
        date_form = SetProtocolEventDateForm(
            initial={
                'protocol_id': protocol.pk,
                'event_date': protocol.event_date or parsed_date,
            }
        )
        show_date_form = True

    # Режим "показать всех участников Orgeo" для отладки/проверки.
    show_all = request.GET.get('show_all') == '1'
    if show_all and is_orgeo:
        chunks = protocol.raw_html.split(ORGEO_TABLES_DELIMITER) if ORGEO_TABLES_DELIMITER in protocol.raw_html else [protocol.raw_html]
        parsed = []
        for html_chunk in chunks:
            if html_chunk.strip():
                parsed.extend(parse_orgeo(html_chunk, base_pairs=[], team_name=''))
        protocol.results.all().delete()
        for r in parsed:
            ProtocolResult.objects.create(
                protocol=protocol,
                group_name=r.get('group_name', ''),
                place=r.get('place', ''),
                place_out_of=r.get('place_out_of'),
                last_name=r['last_name'],
                first_name=r['first_name'],
                points=r.get('points', 0),
                raw_line=r.get('raw_line', ''),
            )
        results = protocol.results.all()

    if request.method == 'POST':
        form = ProcessProtocolForm(request.POST, initial={'protocol_id': protocol.pk})
        form.fields['protocol_id'].initial = protocol.pk
        if form.is_valid():
            team_name = (form.cleaned_data.get('team_name') or '').strip()

            # Список уже известных участников (СК Фотон и др.)
            base_pairs = list(
                KnownParticipant.objects.values_list('last_name', 'first_name')
            )

            # Для Orgeo: парсим все таблицы (все группы), фильтр по команде или по базе.
            if protocol.source_url and 'orgeo.ru' in protocol.source_url:
                chunks = protocol.raw_html.split(ORGEO_TABLES_DELIMITER) if ORGEO_TABLES_DELIMITER in protocol.raw_html else [protocol.raw_html]
                parsed = []
                for html_chunk in chunks:
                    if html_chunk.strip():
                        parsed.extend(parse_orgeo(html_chunk, base_pairs=base_pairs, team_name=team_name))
            else:
                # Старый режим для WinOrient и аналогов — по названию команды.
                if not team_name:
                    messages.warning(request, 'Введите название команды.')
                    return redirect('protocols:protocol_detail', pk=pk)
                parsed = parse_and_process(
                    protocol.raw_html,
                    protocol.format_sfr,
                    team_name,
                    base_pairs,
                )

            protocol.results.all().delete()
            for r in parsed:
                ProtocolResult.objects.create(
                    protocol=protocol,
                    group_name=r.get('group_name', ''),
                    place=r.get('place', ''),
                    place_out_of=r.get('place_out_of'),
                    last_name=r['last_name'],
                    first_name=r['first_name'],
                    points=r.get('points', 0),
                    raw_line=r.get('raw_line', ''),
                )
            # Обновляем базу известных участников (для WinOrient логика как раньше).
            for last_name, first_name in base_pairs:
                KnownParticipant.objects.get_or_create(
                    last_name=last_name,
                    first_name=first_name,
                )
            messages.success(request, f'Найдено результатов: {len(parsed)}.')
            return redirect('protocols:protocol_detail', pk=pk)
    protocol_cleaned_title = clean_protocol_title(protocol.title or '') or 'Протокол'
    title_form = EditProtocolTitleForm(initial={
        'protocol_id': protocol.pk,
        'title': protocol.title or '',
    })
    return render(request, 'protocols/protocol_detail.html', {
        'protocol': protocol,
        'protocol_cleaned_title': protocol_cleaned_title,
        'title_form': title_form,
        'form': form,
        'results': results,
        'is_orgeo': is_orgeo,
        'date_form': date_form,
        'show_date_form': show_date_form,
    })


@require_http_methods(['POST'])
def set_protocol_title(request, pk):
    """Сохранить отредактированное пользователем название протокола."""
    protocol = get_object_or_404(Protocol, pk=pk)
    form = EditProtocolTitleForm(request.POST, initial={'protocol_id': protocol.pk})
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
    if protocol.source_url and 'orgeo.ru' in protocol.source_url:
        messages.warning(request, 'Для протоколов Orgeo дата берётся из события.')
        return redirect('protocols:protocol_detail', pk=pk)
    form = SetProtocolEventDateForm(request.POST, initial={'protocol_id': protocol.pk})
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
    participants = KnownParticipant.objects.all().order_by('last_name', 'first_name')
    return render(request, 'protocols/known_participants.html', {
        'participants': participants,
    })


@require_http_methods(['GET'])
def export_results(request, pk):
    """Скачать результаты протокола как TXT."""
    protocol = get_object_or_404(Protocol, pk=pk)
    results = protocol.results.all()
    lines = [r.raw_line for r in results]

    base_title = clean_protocol_title(protocol.title or '') or f'protocol_{pk}'
    safe_title = re.sub(r'[^0-9A-Za-zА-Яа-я _\-\.]+', '', base_title).strip() or f'protocol_{pk}'
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
        content = export_season_excel_by_date_range(date_from, date_to)
        filename = f'season_{date_from}_{date_to}.xlsx'
        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = (
            f"attachment; filename*=UTF-8''{quote(filename)}"
        )
        return response
    return render(request, 'protocols/season_export.html', {'form': form})


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
        if getattr(ranking, 'date_from', None) and getattr(ranking, 'date_to', None):
            filename = f'season_{ranking.date_from}_{ranking.date_to}.xlsx'
            response = HttpResponse(
                content,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
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
            date_from = datetime.strptime(request.GET['date_from'], '%Y-%m-%d').date()
            date_to = datetime.strptime(request.GET['date_to'], '%Y-%m-%d').date()
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
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
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
        filename = f'season_ranking_{ranking_id}.xlsx' if ranking_id else 'season.xlsx'
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = (
        f"attachment; filename*=UTF-8''{quote(filename)}"
    )
    return response


@require_http_methods(['POST'])
def delete_protocol(request, pk):
    """Удалить протокол вместе с его результатами и вернуть на главную."""
    protocol = get_object_or_404(Protocol, pk=pk)
    protocol.delete()
    messages.success(request, 'Протокол удалён.')
    return redirect('protocols:home')