# -*- coding: utf-8 -*-
"""
Экспорт за сезон в Excel: четыре листа.
1) Места              — обычные участники (не ветераны), места по стартам.
2) Баллы              — итоговые очки обычных участников.
3) Ветераны — Места   — то же для ветеранов.
4) Ветераны — Баллы   — итоговые очки ветеранов.
"""
import re
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
      - data: {(last_name, first_name): {(protocol_id, start_idx): (place, out_of, rank)}}
      - start_columns: [(protocol_obj, start_idx), ...] для заголовка Excel
    """
    veteran_set = _veteran_name_pairs()
    excluded_set = excluded_name_pairs()
    protocol_ids = [p.id for p in protocols]
    protocol_by_id = {p.id: p for p in protocols}
    split_by_group = _build_name_disambiguation(protocol_ids)
    results = (
        ProtocolResult.objects.filter(protocol_id__in=protocol_ids, team_member=True)
        .order_by('protocol_id', 'id')
        .values('protocol_id', 'last_name', 'first_name', 'group_name', 'place', 'place_out_of', 'rank')
    )
    people_set = set()
    by_person = defaultdict(dict)
    seen_key_pid = set()
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
        pid = r['protocol_id']
        if (key, pid) in seen_key_pid:
            continue
        seen_key_pid.add((key, pid))
        place = r['place'] or ''
        place_out_of = r['place_out_of']
        place_out_of_str = str(place_out_of) if place_out_of is not None else ''
        rank = (r['rank'] or '').strip()
        by_person[key][(pid, 1)] = (place, place_out_of_str, rank)
    people_sorted = sorted(people_set, key=lambda x: (x[0].lower(), x[1].lower()))
    start_columns = [(protocol_by_id[p.id], 1) for p in protocols]
    return people_sorted, dict(by_person), start_columns


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


def build_points_data_from_protocols(protocols, max_starts=None, veterans_only=False):
    """
    Сумма очков по результатам протоколов за период: по каждому участнику
    суммируем (ProtocolResult.points × коэффициент старта).
    veterans_only=False → только не-ветераны.
    veterans_only=True  → только ветераны.
    Если max_starts задан — берём только лучшие N стартов каждого участника.
    Возвращает: list of (last_name, first_name, total_points)
    """
    if not protocols:
        return []

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
        per_start[person_key][r.protocol_id] += float(r.weighted_points)

    totals = {}
    for person_key, starts in per_start.items():
        points_list = sorted(starts.values(), reverse=True)
        if max_starts is not None:
            points_list = points_list[:max_starts]
        totals[person_key] = sum(points_list)

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
    _write_points_sheet(
        ws_points,
        build_points_data(ranking) or build_points_data_from_protocols(protocols),
    )

    ws_vet_places = wb.create_sheet('Ветераны — Места', 2)
    vet_people, vet_place_map, _ = build_places_data(protocols, veterans_only=True)
    _write_places_sheet_three_columns(ws_vet_places, start_columns, vet_people, vet_place_map)

    ws_vet_points = wb.create_sheet('Ветераны — Баллы', 3)
    _write_points_sheet(
        ws_vet_points,
        build_points_data(ranking, veterans_only=True) or build_points_data_from_protocols(protocols, veterans_only=True),
    )

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
    _write_points_sheet(
        ws_points,
        (build_points_data(ranking) if ranking else []) or build_points_data_from_protocols(protocols),
    )

    ws_vet_places = wb.create_sheet('Ветераны — Места', 2)
    vet_people, vet_place_map, _ = build_places_data(protocols, veterans_only=True)
    _write_places_sheet_three_columns(ws_vet_places, start_columns, vet_people, vet_place_map)

    ws_vet_points = wb.create_sheet('Ветераны — Баллы', 3)
    _write_points_sheet(
        ws_vet_points,
        (build_points_data(ranking, veterans_only=True) if ranking else [])
        or build_points_data_from_protocols(protocols, veterans_only=True),
    )

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
    points_label = (
        f'Баллы (лучшие {max_starts} из {len(protocols)} стартов)'
        if max_starts and max_starts < len(protocols)
        else 'Баллы'
    )

    wb = Workbook()

    # ── Лист 1: Места ──
    ws_places = wb.active
    ws_places.title = 'Места'
    people_sorted, place_map, start_columns = build_places_data(protocols)
    _write_places_sheet_three_columns(ws_places, start_columns, people_sorted, place_map)

    # ── Лист 2: Баллы ──
    ws_points = wb.create_sheet('Баллы', 1)
    _write_points_sheet(
        ws_points,
        build_points_data_from_protocols(protocols, max_starts=max_starts),
        label=points_label,
    )

    # ── Лист 3: Ветераны — Места ──
    ws_vet_places = wb.create_sheet('Ветераны — Места', 2)
    vet_people, vet_place_map, _ = build_places_data(protocols, veterans_only=True)
    _write_places_sheet_three_columns(ws_vet_places, start_columns, vet_people, vet_place_map)

    # ── Лист 4: Ветераны — Баллы ──
    ws_vet_points = wb.create_sheet('Ветераны — Баллы', 3)
    _write_points_sheet(
        ws_vet_points,
        build_points_data_from_protocols(protocols, max_starts=max_starts, veterans_only=True),
        label=points_label,
    )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
