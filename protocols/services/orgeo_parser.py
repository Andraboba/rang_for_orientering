# -*- coding: utf-8 -*-
"""
Парсер протоколов Orgeo (страницы /online/finish/...).
Берём всех участников с страницы и фильтруем по известным (KnownParticipant) фамилия+имя.
Возвращаем тот же формат dict, что и parse_and_process из parser.py.
Также есть вспомогательная функция fetch_orgeo_document, которая
пытается "дойти" до фактической страницы с таблицей (через iframe).

Формат результата Orgeo (колонки таблицы):
  П/п | № | Фамилия, имя | Команда | Результат | Отставание | Место | Старт | ...сплиты...

Статусы участников (колонка «Результат»):
  - "00:39:39,0"   — нормальный финиш, время
  - "не старт"     — DNS (Did Not Start)
  - "сошел"        — DNF (Did Not Finish)
  - "превышено КВ" — DSQ time (время превышено)
  - "непр.отмет."  — неправильная отметка (Missing Punch)
  - "снят"         — дисквалификация

Очки по формуле из parser.py:  (2 - t_actual / t_leader) * 1000
  - Участник на 1 месте → 1000 очков
  - Участник без места или со статусом → 0 очков
"""

import re
from typing import List, Tuple, Dict, Optional
from urllib.parse import urljoin

from .parser import fetch_url


# ── Статусы, при которых участник НЕ финишировал нормально ──
_DISQUALIFIED_STATUSES = {
    'не старт',      # DNS
    'сошел',         # DNF
    'сошёл',         # DNF (ё)
    'превышено кв',  # DSQ time
    'непр.отмет.',   # Missing Punch
    'снят',          # Disqualified
    'дисквал',       # Disqualified (alt)
}


def _strip_tags(html_fragment: str) -> str:
    """Удалить HTML-теги и привести пробелы к одному."""
    text = re.sub(r'<[^>]+>', ' ', html_fragment)
    text = text.replace('\n', ' ').replace('\r', ' ')
    return ' '.join(text.split())


def _extract_group_from_h2(html: str) -> str:
    """
    Пытаемся вытащить название группы из <h2>Группа Ж17</h2>.
    Если не получилось — возвращаем пустую строку.
    """
    m = re.search(r'<h2[^>]*>(.*?)</h2>', html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ''
    text = _strip_tags(m.group(1))
    if 'Группа' in text:
        parts = text.split()
        try:
            idx = parts.index('Группа')
            return ' '.join(parts[idx + 1:])
        except ValueError:
            return text
    return text


def _split_name(full_name: str) -> Tuple[str, str]:
    """
    Разбиваем строку "Фамилия Имя" на (Фамилия, Имя).
    Если частей больше — всё, кроме первой, считаем именем.
    """
    parts = full_name.split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], ' '.join(parts[1:])


def _is_disqualified(result_str: str) -> bool:
    """Проверяет, является ли результат одним из статусов «не финишировал»."""
    lower = result_str.lower().strip()
    for status in _DISQUALIFIED_STATUSES:
        if status in lower:
            return True
    return False


def _seconds_from_time(s: str) -> Optional[int]:
    """
    Конвертирует строку вида "00:39:39,0" или "00:39:39" в секунды.
    Orgeo использует запятую для десятых: "00:39:39,0".
    """
    if not s or len(s) < 5:
        return None
    s = s.strip()
    # Убираем десятые/сотые после запятой: "00:39:39,0" → "00:39:39"
    s = re.sub(r'[,\.]\d+$', '', s)
    parts = re.split(r'[:\s]+', s)
    if len(parts) >= 3:
        try:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2][:2])
            return h * 3600 + m * 60 + sec
        except (ValueError, IndexError):
            pass
    if len(parts) == 2:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            pass
    return None


def _calc_points(t_actual_sec: int, t_leader_sec: int) -> int:
    """Очки по формуле (2 - t_actual/t_leader) * 1000, минимум 1 при наличии времени."""
    if t_leader_sec <= 0:
        return 0
    points = int((2 - t_actual_sec / t_leader_sec) * 1000)
    return max(1, points)


def _count_participants(rows_html: List[str]) -> int:
    """
    Считает количество участников в группе (все строки таблицы, кроме заголовка).
    Исключает пустые строки (без имени).
    """
    count = 0
    for row in rows_html:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells) >= 3:
            name = _strip_tags(cells[2]).strip()
            if name:
                count += 1
    return count


def _find_leader_time(rows_html: List[str]) -> Optional[int]:
    """
    Находит время лидера (1 место) в группе.
    Лидер — первая строка с местом "1" и нормальным временем в колонке «Результат».
    """
    for row in rows_html:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells) < 7:
            continue
        place = _strip_tags(cells[6]).strip()
        if place == '1':
            result_str = _strip_tags(cells[4]).strip()
            return _seconds_from_time(result_str)
    return None


def parse_orgeo(html: str, base_pairs: List[Tuple[str, str]], team_name: str = '') -> List[Dict]:
    """
    Парсит HTML Orgeo (страница одной группы) и возвращает список dict:
    group_name, place, place_out_of, last_name, first_name, points, raw_line.

    Фильтрация:
      1) если team_name задан — берём участников команды ИЛИ известных участников из базы;
      2) иначе, если base_pairs не пуст — берём только известных участников (сравнение без учёта регистра);
      3) если и то и другое пусто — берём всех.

    Очки: формула (2 - t / t_лидера) * 1000.
      - 1 место = 1000 очков
      - Статус (не старт, снят, превышено КВ, ...) = 0 очков
    """
    results: List[Dict] = []
    # Нормализуем базу для сравнения без учёта регистра и лишних пробелов (Orgeo может отдавать ИВАНОВ/иванов).
    known = set(
        (str(ln).strip().lower(), str(fn).strip().lower())
        for ln, fn in base_pairs
    )
    team_name = (team_name or '').strip()

    group_name = _extract_group_from_h2(html)

    # Находим все строки таблицы <tr>...</tr>
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.IGNORECASE | re.DOTALL)
    if not rows:
        return results

    # Первая строка — заголовки (П/п, №, ...), пропускаем
    data_rows = rows[1:]

    # ── Считаем кол-во участников в группе ──
    group_size = _count_participants(data_rows)

    # ── Находим время лидера для расчёта очков ──
    t_leader = _find_leader_time(data_rows)

    for row in data_rows:
        cells_html = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells_html) < 4:
            continue

        cells = [_strip_tags(c) for c in cells_html]
        # Колонки: [0]=П/п, [1]=№, [2]=ФИ, [3]=Команда, [4]=Результат, [5]=Отставание, [6]=Место, [7]=Старт, ...
        full_name = cells[2].strip()
        team_cell = cells[3].strip() if len(cells) > 3 else ''

        if not full_name:
            continue

        last_name, first_name = _split_name(full_name)
        last_name = last_name.strip()
        first_name = first_name.strip()
        key_normalized = (last_name.lower(), first_name.lower())

        # ── Фильтрация ──
        if team_name:
            team_matches = team_name.lower() in team_cell.lower()
            known_matches = key_normalized in known
            if not team_matches and not known_matches:
                continue
        elif known:
            if key_normalized not in known:
                continue

        result_str = cells[4].strip() if len(cells) > 4 else ''
        place_col = cells[6].strip() if len(cells) > 6 else ''

        # ── Определяем статус ──
        disqualified = _is_disqualified(result_str)

        # ── Считаем очки ──
        if disqualified or not place_col:
            # Нет места или статус → 0 очков
            points = 0
        elif place_col == '1':
            # Лидер всегда получает 1000
            points = 1000
        else:
            # Формула: (2 - t / t_лидера) * 1000
            t_actual = _seconds_from_time(result_str)
            if t_actual is not None and t_leader is not None and t_leader > 0:
                points = _calc_points(t_actual, t_leader)
            else:
                points = 0

        # ── Формируем place_str (человек может иметь место И быть снятым) ──
        if disqualified:
            # Показываем статус вместо места
            status_text = result_str.strip()
            place_display = status_text
        else:
            place_display = place_col if place_col else '-'

        raw_line = f"{place_display} из {group_size} , {last_name} {first_name}, очки:{points}"

        results.append(
            {
                'group_name': group_name,
                'place': place_display,
                'place_out_of': group_size,
                'last_name': last_name,
                'first_name': first_name,
                'points': points,
                'raw_line': raw_line,
            }
        )

    return results


def fetch_orgeo_document(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Загружает страницу Orgeo и, если в ней есть <iframe>, пытается догрузить
    содержимое по src этого iframe. Возвращает (html, error_message).
    """
    html, err = fetch_url(url, encoding_utf8=True)
    if err or not html:
        return html, err

    m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        return html, None

    iframe_src = m.group(1)
    iframe_url = urljoin('https://orgeo.ru', iframe_src)

    iframe_html, err2 = fetch_url(iframe_url, encoding_utf8=True)
    if err2 or not iframe_html:
        return html, err2

    return iframe_html, None