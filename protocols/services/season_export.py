# -*- coding: utf-8 -*-
"""
Экспорт за сезон в Excel: четыре листа.
1) Места              — обычные участники (не ветераны), места по стартам.
2) Баллы              — итоговые очки обычных участников.
3) Ветераны — Места   — то же для ветеранов.
4) Ветераны — Баллы   — итоговые очки ветеранов.

Листы «Баллы» строятся формулами: очки по каждому старту, итог — сумма лучших
N стартов, где N задаётся ячейкой B1 прямо в Excel (без повторной выгрузки).
"""
import re
from collections import defaultdict
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..models import (
    KnownParticipant,
    Protocol,
    ProtocolResult,
    SeasonRanking,
    SeasonRankingEntry,
)

# Тёзки, которых нужно разделять по группам, задаются флагом
# KnownParticipant.split_by_group. Этот набор оставлен для участников,
# заведённых до появления флага.
GROUP_SPLIT_EXCEPTIONS = {
    ('поздновский', 'сергей'),
}


def _split_by_group_pairs():
    """Пары (фамилия, имя), которых в зачёте разделяем по группам."""
    from .group_filter import _normalize  # локальный импорт: единая нормализация

    pairs = set(GROUP_SPLIT_EXCEPTIONS)
    pairs.update(
        (_normalize(last_name), _normalize(first_name))
        for last_name, first_name in KnownParticipant.objects.filter(
            split_by_group=True
        ).values_list('last_name', 'first_name')
    )
    return pairs


def _normalize_group_name(name: str) -> str:
    """
    Нормализует название группы: убирает дефис между буквой и цифрой.
    М-12 → М12, Ж-14 → Ж14, М-45+ → М45+ и т.д.
    """
    return re.sub(r'([А-Яа-яA-Za-z])-(\d)', r'\1\2', (name or '').strip())


def _build_name_disambiguation(protocol_ids):
    """
    Определяет, нужно ли разделять участников по группам.
    Разделение всегда применяется к ФИО из GROUP_SPLIT_EXCEPTIONS.
    Группы нормализуются: М-12 и М12 считаются одной группой.
    """
    if not protocol_ids:
        return {}
    split_pairs = _split_by_group_pairs()
    rows = (
        ProtocolResult.objects.filter(protocol_id__in=protocol_ids, team_member=True)
        .values('last_name', 'first_name', 'group_name')
    )
    groups_by_name = defaultdict(set)
    for r in rows:
        key = (r['last_name'], r['first_name'])
        group_name = _normalize_group_name(r.get('group_name') or '')
        if group_name:
            groups_by_name[key].add(group_name)
    split_flags = {}
    for key, groups in groups_by_name.items():
        ln, fn = key
        normalized = ((ln or '').strip().lower(), (fn or '').strip().lower())
        split_flags[key] = normalized in split_pairs
    return split_flags


def _display_person_name(last_name, first_name, group_name, need_split):
    """Формирует отображаемое имя участника с учётом разбиения по группе."""
    if need_split:
        group_suffix = _normalize_group_name(group_name) or 'без группы'
        return last_name, f'{first_name} ({group_suffix})'
    return last_name, first_name


def _veteran_name_pairs():
    """Пары (фамилия, имя) в нижнем регистре для участников с флагом «Ветеран»."""
    return set(
        (ln.strip().lower(), fn.strip().lower())
        for ln, fn in KnownParticipant.objects.filter(veteran=True).values_list('last_name', 'first_name')
    )


def excluded_name_pairs():
    """
    Пары (фамилия, имя) участников с флагом «Не учитывать никогда».
    Такие не попадают ни в основной зачёт, ни в ветеранский.
    """
    return set(
        (ln.strip().lower(), fn.strip().lower())
        for ln, fn in KnownParticipant.objects.filter(excluded=True).values_list('last_name', 'first_name')
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


def build_places_data(protocols, veterans_only=False):
    """
    Строит данные для таблицы «места»: по каждому протоколу у участника — (место, участников в группе, разряд).
    veterans_only=False → только обычные участники (не ветераны).
    veterans_only=True  → только ветераны.
    Возвращает:
      - people_sorted: список участников
      - data: {(last_name, first_name): {(protocol_id, day): (place, out_of, rank)}}
      - start_columns: [(protocol_obj, day), ...] для заголовка Excel
    """
    veteran_set = _veteran_name_pairs()
    excluded_set = excluded_name_pairs()
    protocol_ids = [p.id for p in protocols]
    split_by_group = _build_name_disambiguation(protocol_ids)
    results = (
        ProtocolResult.objects.filter(protocol_id__in=protocol_ids, team_member=True)
        .order_by('protocol_id', 'id')
        .values(
            'protocol_id', 'day', 'last_name', 'first_name', 'group_name',
            'place', 'place_out_of', 'rank',
        )
    )
    people_set = set()
    by_person = defaultdict(dict)
    seen_key_start = set()
    for r in results:
        ln, fn = r['last_name'], r['first_name']
        name_key = (ln.strip().lower(), fn.strip().lower())
        if name_key in excluded_set:
            continue
        is_veteran = name_key in veteran_set
        if veterans_only and not is_veteran:
            continue
        if not veterans_only and is_veteran:
            continue
        need_split = split_by_group.get((ln, fn), False)
        key = _display_person_name(ln, fn, r.get('group_name'), need_split)
        people_set.add(key)
        start = (r['protocol_id'], r['day'])
        if (key, start) in seen_key_start:
            continue
        seen_key_start.add((key, start))
        place = r['place'] or ''
        place_out_of = r['place_out_of']
        place_out_of_str = str(place_out_of) if place_out_of is not None else ''
        rank = (r['rank'] or '').strip()
        by_person[key][start] = (place, place_out_of_str, rank)
    people_sorted = sorted(people_set, key=lambda x: (x[0].lower(), x[1].lower()))
    return people_sorted, dict(by_person), build_start_columns(protocols)


def build_start_columns(protocols):
    """
    Старты периода: [(протокол, день), ...] по порядку протоколов. Каждый день
    многодневки — отдельный старт; протокол без результатов даёт одну колонку.
    """
    days_by_protocol = defaultdict(set)
    for protocol_id, day in (
        ProtocolResult.objects.filter(
            protocol_id__in=[p.id for p in protocols], team_member=True
        ).values_list('protocol_id', 'day').distinct()
    ):
        days_by_protocol[protocol_id].add(day)
    return [
        (p, day)
        for p in protocols
        for day in sorted(days_by_protocol.get(p.id) or {1})
    ]


def build_points_data(ranking, veterans_only=False):
    """
    Участники рейтинга сезона с баллами из SeasonRankingEntry (от лучшего к худшему).
    veterans_only=False → только не-ветераны.
    veterans_only=True  → только ветераны.
    """
    qs = (
        SeasonRankingEntry.objects.filter(ranking=ranking)
        .select_related('participant')
        .order_by('-points', 'participant__last_name', 'participant__first_name')
    )
    qs = qs.exclude(participant__excluded=True)
    if veterans_only:
        qs = qs.filter(participant__veteran=True)
    else:
        qs = qs.exclude(participant__veteran=True)
    return [
        (e.participant.last_name, e.participant.first_name, e.points)
        for e in qs
    ]


def build_points_matrix(protocols, veterans_only=False):
    """
    Взвешенные очки (ProtocolResult.points × коэффициент) по каждому старту:
    {(фамилия, имя): {(protocol_id, день): очки}}. Каждый день многодневки —
    отдельный старт; несколько результатов одного дня складываются.
    veterans_only=False → только не-ветераны, True → только ветераны.
    """
    if not protocols:
        return {}

    veteran_set = _veteran_name_pairs()
    excluded_set = excluded_name_pairs()
    protocol_ids = [p.id for p in protocols]
    split_by_group = _build_name_disambiguation(protocol_ids)
    rows = (
        ProtocolResult.objects.filter(protocol_id__in=protocol_ids, team_member=True)
        .select_related('protocol', 'protocol__championship_level')
        # Переопределения уровня по группам — одним запросом, без N+1.
        .prefetch_related('protocol__group_levels__championship_level')
    )
    # per_start[person_key][protocol_id] = суммарные взвешенные очки за старт
    per_start = defaultdict(lambda: defaultdict(float))
    for r in rows:
        ln, fn = r.last_name, r.first_name
        name_key = (ln.strip().lower(), fn.strip().lower())
        if name_key in excluded_set:
            continue
        is_veteran = name_key in veteran_set
        if veterans_only and not is_veteran:
            continue
        if not veterans_only and is_veteran:
            continue
        need_split = split_by_group.get((ln, fn), False)
        person_key = _display_person_name(ln, fn, r.group_name, need_split)
        per_start[person_key][(r.protocol_id, r.day)] += float(r.weighted_points)
    return {person: dict(starts) for person, starts in per_start.items()}


def _best_starts_totals(per_start, max_starts=None):
    """Сумма лучших max_starts стартов каждого участника (None — всех)."""
    totals = {}
    for person_key, starts in per_start.items():
        points_list = sorted(starts.values(), reverse=True)
        if max_starts is not None:
            points_list = points_list[:max_starts]
        totals[person_key] = sum(points_list)
    return totals


def build_points_data_from_protocols(protocols, max_starts=None, veterans_only=False):
    """
    Сумма очков по результатам протоколов за период: по каждому участнику
    суммируем (ProtocolResult.points × коэффициент старта).
    veterans_only=False → только не-ветераны.
    veterans_only=True  → только ветераны.
    Если max_starts задан — берём только лучшие N стартов каждого участника.
    Возвращает: list of (last_name, first_name, total_points)
    """
    totals = _best_starts_totals(build_points_matrix(protocols, veterans_only), max_starts)
    return [
        (last_name, first_name, total)
        for (last_name, first_name), total in sorted(
            totals.items(),
            key=lambda item: (-item[1], item[0][0].lower(), item[0][1].lower()),
        )
    ]


def _protocol_display_title(p):
    """Название протокола для заголовка таблицы (очищенное)."""
    from .title_cleaner import clean_protocol_title
    raw = p.title or (p.event.title if p.event else '')
    return clean_protocol_title(raw) or f'Протокол {p.pk}'


def _write_places_sheet_three_columns(ws_places, start_columns, people_sorted, place_map):
    """Пишет лист «Места»: на каждый старт по 3 колонки — место, участников в группе, разряд."""
    header = ['Участник']
    starts_per_protocol = defaultdict(int)
    for p, _start_idx in start_columns:
        starts_per_protocol[p.id] += 1
    for p, start_idx in start_columns:
        title = _protocol_display_title(p)
        suffix = f' — день {start_idx}' if starts_per_protocol[p.id] > 1 else ''
        header.append(f'{title}{suffix} — место')
        header.append(f'{title}{suffix} — участников')
        header.append(f'{title}{suffix} — разряд')
    ws_places.append(header)
    for col in range(1, len(header) + 1):
        cell = ws_places.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    for last_name, first_name in people_sorted:
        row_data = [f'{last_name} {first_name}']
        person_data = place_map.get((last_name, first_name), {})
        for p, start_idx in start_columns:
            triple = person_data.get((p.id, start_idx), ('', '', ''))
            row_data.extend([triple[0], triple[1], triple[2]])
        ws_places.append(row_data)

    ws_places.column_dimensions['A'].width = 25
    col = 2
    for _ in start_columns:
        for _ in range(3):
            ws_places.column_dimensions[get_column_letter(col)].width = 12
            col += 1


def _write_points_sheet(ws, points_list, label='Баллы'):
    """Пишет лист «Баллы»: участник → итоговые очки (от лучшего к худшему)."""
    ws.append(['Участник', label])
    for col in (1, 2):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
    for last_name, first_name, points in points_list:
        ws.append([f'{last_name} {first_name}', round(float(points), 2)])
    ws.column_dimensions['A'].width = 25
    ws.column_dimensions['B'].width = 22


# Лист «Баллы» с формулами: число зачётных стартов меняется прямо в Excel.
STARTS_INPUT_CELL = 'B1'
_POINTS_HEADER_ROW = 3
_POINTS_FIRST_COLUMN = 5  # A — место, B — участник, C — итог, D — стартов, дальше старты


def _best_n_formula(first_cell, last_cell, starts_cell):
    """
    Сумма лучших N значений строки, N — в ячейке starts_cell (не больше числа стартов).
    ROW(INDIRECT("1:k")) даёт {1..k}, LARGE берёт k лучших, SUMPRODUCT их складывает —
    работает и в старом Excel без формул массива, и в LibreOffice.
    """
    values = f'{first_cell}:{last_cell}'
    k = f'MIN({starts_cell},COUNT({values}))'
    return f'=IF({k}<1,0,SUMPRODUCT(LARGE({values},ROW(INDIRECT("1:"&{k})))))'


def _write_points_formula_sheet(ws, protocols, veterans_only=False, max_starts=None):
    """
    Лист «Баллы»: очки участника по каждому старту (с коэффициентом уровня),
    итог — формула «сумма лучших N стартов», N — в ячейке B1. Места считаются
    формулой RANK, поэтому при смене N итог и места пересчитываются сами;
    строки отсортированы по итогу для N, заданного при выгрузке.
    Каждый день многодневки — отдельный старт и отдельный столбец.
    """
    start_columns = build_start_columns(protocols)
    days_per_protocol = defaultdict(int)
    for p, _day in start_columns:
        days_per_protocol[p.id] += 1
    total_starts = len(start_columns)
    default_n = min(max_starts, total_starts) if max_starts else total_starts
    per_start = build_points_matrix(protocols, veterans_only=veterans_only)
    totals = _best_starts_totals(per_start, default_n)
    people = sorted(totals, key=lambda key: (-totals[key], key[0].lower(), key[1].lower()))

    starts_cell = f'${STARTS_INPUT_CELL[0]}${STARTS_INPUT_CELL[1:]}'
    ws['A1'] = 'Зачётных стартов:'
    ws['A1'].font = Font(bold=True)
    ws[STARTS_INPUT_CELL] = default_n
    ws[STARTS_INPUT_CELL].font = Font(bold=True, color='0000FF')
    ws[STARTS_INPUT_CELL].fill = PatternFill('solid', start_color='FFFF00')
    ws[STARTS_INPUT_CELL].alignment = Alignment(horizontal='center')
    ws['C1'] = (
        f'из {total_starts}. Впишите другое число — итог и места пересчитаются. '
        'В столбцах стартов — очки с коэффициентом уровня.'
    )
    ws['C1'].font = Font(italic=True, color='808080')

    first_col = _POINTS_FIRST_COLUMN
    last_col = first_col + max(total_starts, 1) - 1
    header = ['Место', 'Участник', f'="Баллы (лучшие "&{starts_cell}&")"', 'Стартов']
    for p, day in start_columns:
        event_date = (p.event.date if p.event else None) or p.event_date
        prefix = f'{event_date:%d.%m} ' if event_date else ''
        suffix = f' — день {day}' if days_per_protocol[p.id] > 1 else ''
        header.append(f'{prefix}{_protocol_display_title(p)}{suffix}')
    # Строка 2 — отступ между настройкой и таблицей.
    for col, title in enumerate(header, start=1):
        cell = ws.cell(row=_POINTS_HEADER_ROW, column=col, value=title)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    first_row = _POINTS_HEADER_ROW + 1
    last_row = _POINTS_HEADER_ROW + len(people)
    first_letter, last_letter = get_column_letter(first_col), get_column_letter(last_col)
    for row, person in enumerate(people, start=first_row):
        values_first, values_last = f'{first_letter}{row}', f'{last_letter}{row}'
        ws.cell(row=row, column=1, value=f'=RANK(C{row},$C${first_row}:$C${last_row})')
        ws.cell(row=row, column=2, value=f'{person[0]} {person[1]}')
        ws.cell(row=row, column=3, value=_best_n_formula(values_first, values_last, starts_cell))
        ws.cell(row=row, column=4, value=f'=COUNT({values_first}:{values_last})')
        for offset, (p, day) in enumerate(start_columns):
            points = per_start[person].get((p.id, day))
            if points is not None:
                ws.cell(row=row, column=first_col + offset, value=round(points, 2))
        for col in [3] + list(range(first_col, last_col + 1)):
            ws.cell(row=row, column=col).number_format = '0.00'

    ws.freeze_panes = ws.cell(row=first_row, column=3)
    if people:
        # Фильтр в заголовке — чтобы пересортировать таблицу после смены N.
        ws.auto_filter.ref = f'A{_POINTS_HEADER_ROW}:{last_letter}{last_row}'
    ws.row_dimensions[_POINTS_HEADER_ROW].height = 60
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 25
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 9
    for col in range(first_col, last_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14


def _write_ranking_or_formula_points(ws, ranking, protocols, veterans_only=False):
    """
    Если у рейтинга есть сохранённые итоги (SeasonRankingEntry) — пишем их как есть,
    иначе — лист с формулами по протоколам периода.
    """
    saved = build_points_data(ranking, veterans_only=veterans_only) if ranking else []
    if saved:
        _write_points_sheet(ws, saved)
    else:
        _write_points_formula_sheet(ws, protocols, veterans_only=veterans_only)


def export_season_excel(ranking):
    """
    Excel с четырьмя листами: «Места», «Баллы», «Ветераны — Места», «Ветераны — Баллы».
    Период рейтинга: по date_from/date_to или по year+season.
    """
    protocols = list(get_protocols_for_ranking(ranking))

    wb = Workbook()

    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map, start_columns = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, start_columns, people_sorted, place_map)

    ws_points = wb.create_sheet('Баллы', 1)
    _write_ranking_or_formula_points(ws_points, ranking, protocols)

    ws_vet_places = wb.create_sheet('Ветераны — Места', 2)
    vet_people, vet_place_map, _ = build_places_data(protocols, veterans_only=True)
    _write_places_sheet_three_columns(ws_vet_places, start_columns, vet_people, vet_place_map)

    ws_vet_points = wb.create_sheet('Ветераны — Баллы', 3)
    _write_ranking_or_formula_points(ws_vet_points, ranking, protocols, veterans_only=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def export_season_excel_by_period(year, season):
    """
    Экспорт по году и сезону.
    Четыре листа: «Места», «Баллы», «Ветераны — Места», «Ветераны — Баллы».
    """
    ranking = SeasonRanking.objects.filter(year=year, season=season).first()
    protocols = list(get_protocols_for_period(year, season))

    wb = Workbook()

    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map, start_columns = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, start_columns, people_sorted, place_map)

    ws_points = wb.create_sheet('Баллы', 1)
    _write_ranking_or_formula_points(ws_points, ranking, protocols)

    ws_vet_places = wb.create_sheet('Ветераны — Места', 2)
    vet_people, vet_place_map, _ = build_places_data(protocols, veterans_only=True)
    _write_places_sheet_three_columns(ws_vet_places, start_columns, vet_people, vet_place_map)

    ws_vet_points = wb.create_sheet('Ветераны — Баллы', 3)
    _write_ranking_or_formula_points(ws_vet_points, ranking, protocols, veterans_only=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def export_season_excel_by_date_range(date_from, date_to, max_starts=None):
    """
    Экспорт за выбранный период (из формы «Экспорт за период»).
    Четыре листа: «Места», «Баллы», «Ветераны — Места», «Ветераны — Баллы».
    Если max_starts задан — в зачёт идут только лучшие N стартов каждого участника.
    """
    protocols = list(get_protocols_for_date_range(date_from, date_to))

    wb = Workbook()

    # ── Лист 1: Места ──
    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map, start_columns = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, start_columns, people_sorted, place_map)

    # ── Лист 2: Баллы (число зачётных стартов меняется в ячейке B1) ──
    ws_points = wb.create_sheet('Баллы', 1)
    _write_points_formula_sheet(ws_points, protocols, max_starts=max_starts)

    # ── Лист 3: Ветераны — Места ──
    ws_vet_places = wb.create_sheet('Ветераны — Места', 2)
    vet_people, vet_place_map, _ = build_places_data(protocols, veterans_only=True)
    _write_places_sheet_three_columns(ws_vet_places, start_columns, vet_people, vet_place_map)

    # ── Лист 4: Ветераны — Баллы ──
    ws_vet_points = wb.create_sheet('Ветераны — Баллы', 3)
    _write_points_formula_sheet(
        ws_vet_points, protocols, veterans_only=True, max_starts=max_starts
    )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
