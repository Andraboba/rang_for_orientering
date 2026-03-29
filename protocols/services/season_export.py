# -*- coding: utf-8 -*-
"""
Экспорт за сезон в Excel: две таблицы.
1) Места: первая строка — названия стартов, далее строки — участник и его места (по алфавиту).
2) Баллы: первая строка — заголовок, далее — участник и баллы (от лучшего к худшему).
"""
from collections import defaultdict
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

from ..models import (
    KnownParticipant,
    Protocol,
    ProtocolResult,
    SeasonRanking,
    SeasonRankingEntry,
)


def _veteran_name_pairs():
    """Пары (фамилия, имя) в нижнем регистре для участников с флагом «Ветеран» (не в зачёт сезона)."""
    return set(
        (ln.strip().lower(), fn.strip().lower())
        for ln, fn in KnownParticipant.objects.filter(veteran=True).values_list('last_name', 'first_name')
    )


def get_protocols_for_period(year, season):
    """Протоколы (старты) за указанный период: год + сезон (весна/осень)."""
    return (
        Protocol.objects.filter(
            event__date__year=year,
            event__season=season,
        )
        .select_related('event')
        .order_by('event__date', 'pk')
    )


def get_protocols_for_ranking(ranking):
    """Протоколы за период рейтинга: по точным датам (date_from/date_to) или по году и сезону."""
    if getattr(ranking, 'date_from', None) and getattr(ranking, 'date_to', None):
        return get_protocols_for_date_range(ranking.date_from, ranking.date_to)
    if getattr(ranking, 'year', None) and getattr(ranking, 'season', None):
        return get_protocols_for_period(ranking.year, ranking.season)
    return Protocol.objects.none()


def get_protocols_for_date_range(date_from, date_to):
    """
    Протоколы, дата которых попадает в [date_from, date_to].
    Дата берётся: из event.date (если протокол привязан к событию) или из protocol.event_date.
    """
    from django.db.models import Q, F
    from django.db.models.functions import Coalesce
    qs = (
        Protocol.objects.filter(
            Q(event__isnull=False, event__date__gte=date_from, event__date__lte=date_to)
            | Q(event__isnull=True, event_date__isnull=False, event_date__gte=date_from, event_date__lte=date_to)
        )
        .select_related('event')
    )
    return qs.annotate(
        _eff_date=Coalesce(F('event__date'), F('event_date'))
    ).order_by('_eff_date', 'pk')


def build_places_data(protocols):
    """
    Строит данные для таблицы «места»: по каждому протоколу у участника — (место, участников в группе, разряд).
    Ветераны не включаются. Возвращает: (people_sorted, data).
    """
    veteran_set = _veteran_name_pairs()
    protocol_ids = [p.id for p in protocols]
    results = (
        ProtocolResult.objects.filter(protocol_id__in=protocol_ids)
        .values('protocol_id', 'last_name', 'first_name', 'place', 'place_out_of', 'rank')
    )
    people_set = set()
    by_person = defaultdict(dict)
    for r in results:
        ln, fn = r['last_name'], r['first_name']
        if (ln.strip().lower(), fn.strip().lower()) in veteran_set:
            continue
        key = (ln, fn)
        people_set.add(key)
        pid = r['protocol_id']
        if pid not in by_person[key]:
            place = r['place'] or ''
            place_out_of = r['place_out_of']
            place_out_of_str = str(place_out_of) if place_out_of is not None else ''
            rank = (r['rank'] or '').strip()
            by_person[key][pid] = (place, place_out_of_str, rank)
    people_sorted = sorted(people_set, key=lambda x: (x[0].lower(), x[1].lower()))
    return people_sorted, dict(by_person)


def build_points_data(ranking):
    """
    Участники рейтинга сезона с баллами из SeasonRankingEntry (от лучшего к худшему).
    Ветераны (veteran=True) не включаются в зачёт.
    """
    entries = (
        SeasonRankingEntry.objects.filter(ranking=ranking)
        .exclude(participant__veteran=True)
        .select_related('participant')
        .order_by('-points', 'participant__last_name', 'participant__first_name')
    )
    return [
        (e.participant.last_name, e.participant.first_name, e.points)
        for e in entries
    ]


def build_points_data_from_protocols(protocols):
    """
    Сумма очков по результатам протоколов за период: по каждому участнику
    суммируем (ProtocolResult.points × коэффициент старта). Ветераны не включаются.
    Возвращает: list of (last_name, first_name, total_points)
    """
    if not protocols:
        return []
    from django.db.models import Sum, F, FloatField
    from django.db.models.functions import Coalesce
    from django.db.models.expressions import Value, ExpressionWrapper

    veteran_set = _veteran_name_pairs()
    protocol_ids = [p.id for p in protocols]
    results = (
        ProtocolResult.objects.filter(protocol_id__in=protocol_ids)
        .select_related('protocol', 'protocol__championship_level')
        .annotate(
            weighted=ExpressionWrapper(
                F('points')
                * Coalesce(
                    F('protocol__championship_level__coefficient'),
                    F('protocol__importance'),
                    Value(1.0),
                ),
                output_field=FloatField(),
            )
        )
        .values('last_name', 'first_name')
        .annotate(total=Sum('weighted'))
        .order_by('-total', 'last_name', 'first_name')
    )
    return [
        (r['last_name'], r['first_name'], float(r['total'] or 0))
        for r in results
        if (r['last_name'].strip().lower(), r['first_name'].strip().lower()) not in veteran_set
    ]


def _write_places_sheet_three_columns(ws_places, protocols, people_sorted, place_map):
    """Пишет лист «Места»: на каждый старт по 3 колонки — место, участников в группе, разряд."""
    protocol_ids = [p.id for p in protocols]
    # Заголовок: Участник | для каждого протокола: место, участников, разряд
    header = ['Участник']
    for p in protocols:
        title = _protocol_display_title(p)
        header.append(f'{title} — место')
        header.append(f'{title} — участников')
        header.append(f'{title} — разряд')
    ws_places.append(header)
    for col in range(1, len(header) + 1):
        cell = ws_places.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    for last_name, first_name in people_sorted:
        row_data = [f'{last_name} {first_name}']
        person_data = place_map.get((last_name, first_name), {})
        for pid in protocol_ids:
            triple = person_data.get(pid, ('', '', ''))
            row_data.extend([triple[0], triple[1], triple[2]])
        ws_places.append(row_data)

    ws_places.column_dimensions['A'].width = 25
    col = 2
    for _ in protocols:
        for _ in range(3):
            ws_places.column_dimensions[get_column_letter(col)].width = 12
            col += 1


def export_season_excel(ranking):
    """
    Формирует один Excel-файл с двумя листами:
    - «Места»: на каждый старт 3 колонки (место, участников в группе, разряд); участники по алфавиту.
    - «Баллы»: участник и баллы (от лучшего к худшему).
    Период рейтинга: по date_from/date_to или по year+season.
    """
    protocols = list(get_protocols_for_ranking(ranking))
    protocol_ids = [p.id for p in protocols]

    wb = Workbook()
    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, protocols, people_sorted, place_map)

    # --- Лист 2: Баллы --- (из результатов протоколов за период: сумма очков по стартам)
    ws_points = wb.create_sheet('Баллы', 1)
    ws_points.append(['Участник', 'Баллы'])
    for col in (1, 2):
        cell = ws_points.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
    points_list = build_points_data(ranking) or build_points_data_from_protocols(protocols)
    for last_name, first_name, points in points_list:
        ws_points.append([f'{last_name} {first_name}', points])
    ws_points.column_dimensions['A'].width = 25
    ws_points.column_dimensions['B'].width = 12

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def export_season_excel_by_period(year, season):
    """
    Экспорт по году и сезону без привязки к конкретному SeasonRanking.
    Таблица «Места» строится по протоколам периода.
    Таблица «Баллы» — по записям SeasonRanking для этого года и сезона (если есть).
    """
    ranking = SeasonRanking.objects.filter(year=year, season=season).first()
    protocols = list(get_protocols_for_period(year, season))
    wb = Workbook()
    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, protocols, people_sorted, place_map)

    # --- Лист 2: Баллы --- (сумма очков по протоколам периода; при наличии рейтинга — его записи)
    ws_points = wb.create_sheet('Баллы', 1)
    ws_points.append(['Участник', 'Баллы'])
    for col in (1, 2):
        cell = ws_points.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
    points_list = (build_points_data(ranking) if ranking else []) or build_points_data_from_protocols(protocols)
    for last_name, first_name, points in points_list:
        ws_points.append([f'{last_name} {first_name}', points])
    ws_points.column_dimensions['A'].width = 25
    ws_points.column_dimensions['B'].width = 12

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _protocol_display_title(p):
    """Название протокола для заголовка таблицы (очищенное)."""
    from .title_cleaner import clean_protocol_title
    raw = p.title or (p.event.title if p.event else '')
    return clean_protocol_title(raw) or f'Протокол {p.pk}'


def export_season_excel_by_date_range(date_from, date_to):
    """
    Экспорт за выбранный период (календарь): лист «Места» — по 3 колонки на старт (место, участников, разряд).
    """
    protocols = list(get_protocols_for_date_range(date_from, date_to))
    wb = Workbook()
    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, protocols, people_sorted, place_map)

    ws_points = wb.create_sheet('Баллы', 1)
    ws_points.append(['Участник', 'Баллы'])
    for col in (1, 2):
        cell = ws_points.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
    for last_name, first_name, points in build_points_data_from_protocols(protocols):
        ws_points.append([f'{last_name} {first_name}', points])
    ws_points.column_dimensions['A'].width = 25
    ws_points.column_dimensions['B'].width = 12

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
