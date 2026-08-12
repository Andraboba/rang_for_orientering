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
    # Нулевое время означает отсутствие результата («00:00:00» у не стартовавших),
    # иначе формула выдала бы больше 1000 очков.
    if t_leader_sec <= 0 or t_actual_sec <= 0:
        return 0
    points = int((2 - t_actual_sec / t_leader_sec) * 1000)
    return min(1000, max(1, points))


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


def _get_place(cells: List[str]) -> str:
    """
    Определяет место участника из строки таблицы.
    Orgeo имеет два формата:
      А) с колонкой «Место» в cells[6] (явное место, число)
      Б) без колонки «Место» — место = cells[0] (П/п в отсортированном списке)
    Формат определяется по тому, является ли cells[6] числом.
    """
    if len(cells) > 6:
        explicit = cells[6].strip()
        if explicit.isdigit():
            return explicit
    return cells[0].strip() if cells else ''


def _find_leader_time(rows_html: List[str]) -> Optional[int]:
    """
    Находит время лидера (1 место) в группе.
    Лидер — первая строка с местом "1" и нормальным временем в колонке «Результат».
    """
    for row in rows_html:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells) < 5:
            continue
        cells_text = [_strip_tags(c) for c in cells]
        place = _get_place(cells_text)
        if place == '1':
            result_str = cells_text[4].strip()
            return _seconds_from_time(result_str)
    return None


# ── Эстафета ─────────────────────────────────────────────────────────────────

# Имена-заглушки для «пустых» ног эстафеты (команда не заявила бегуна на этап).
_RELAY_NO_RUNNER = frozenset({'нет', '-', '—', 'нет/нет', 'dns', 'dsq'})


def _is_relay_group(data_rows: List[str]) -> bool:
    """
    Возвращает True, если таблица — эстафета.
    Признак: две подряд непустые строки с одинаковым цифровым значением в cells[0].
    В индивидуальных стартах cells[0] = П/п (последовательный, всегда разный).
    В эстафете cells[0] = место команды (одинаковое для всех ног одной команды).
    """
    prev_place = None
    for row in data_rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if not cells:
            prev_place = None
            continue
        cells_text = [_strip_tags(c).strip() for c in cells]
        if not any(cells_text):
            prev_place = None
            continue
        place = cells_text[0]
        if place and place.isdigit() and place == prev_place:
            return True
        prev_place = place
    return False


def _split_relay_blocks(data_rows: List[str]) -> List[List[str]]:
    """Разбивает строки эстафетной таблицы на блоки — по одному на команду."""
    blocks: List[List[str]] = []
    current: List[str] = []
    for row in data_rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        has_data = bool(cells and any(_strip_tags(c).strip() for c in cells))
        if has_data:
            current.append(row)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    return blocks


def _parse_relay_team_block(block_rows: List[str]) -> dict:
    """
    Парсит блок строк одной команды эстафеты.
    Возвращает: {place, relay_team_name, members, total_time_sec}.
    members — список (last_name, first_name, member_club).
    total_time_sec — время последнего этапа (накопленное итоговое время команды).
    """
    place = ''
    relay_team_name = ''
    members: List[Tuple[str, str, str]] = []  # (ln, fn, club)

    for row in block_rows:
        cells_html = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells_html) < 3:
            continue
        cells = [_strip_tags(c).strip() for c in cells_html]

        # Место команды — cells[0], одинаково для всех ног
        if not place and cells[0].isdigit():
            place = cells[0]

        # ФИ бегуна — cells[2], клуб бегуна — cells[3]
        full_name = cells[2] if len(cells) > 2 else ''
        member_club = cells[3] if len(cells) > 3 else ''
        if full_name and full_name.lower().strip() not in _RELAY_NO_RUNNER:
            ln, fn = _split_name(full_name)
            if ln.strip():
                members.append((ln.strip(), fn.strip(), member_club.strip()))

        # Название эстафетной команды — cells[3] первой строки
        if not relay_team_name and len(cells) > 3 and cells[3]:
            relay_team_name = cells[3]

    # Итоговое время = время в cells[4] последней строки блока
    total_time_sec: Optional[int] = None
    last_cells_html = re.findall(
        r'<td[^>]*>(.*?)</td>', block_rows[-1], re.IGNORECASE | re.DOTALL
    )
    if len(last_cells_html) >= 5:
        last_cells = [_strip_tags(c).strip() for c in last_cells_html]
        t_str = last_cells[4]
        if t_str and not _is_disqualified(t_str):
            total_time_sec = _seconds_from_time(t_str)

    return {
        'place': place,
        'relay_team_name': relay_team_name,
        'members': members,
        'total_time_sec': total_time_sec,
    }


def parse_orgeo_relay(
    html: str,
    base_pairs: List[Tuple[str, str]],
    team_name: str = '',
) -> List[Dict]:
    """
    Парсит HTML страницы ЭСТАФЕТНОЙ группы Orgeo.

    Все участники одной команды получают одинаковые очки, вычисленные по
    итоговому времени команды (время последнего этапа).

    Фильтрация:
      1) team_name задан → включаем команды, у которых team_name входит в название
         эстафетной команды ИЛИ хотя бы один участник в base_pairs;
      2) только base_pairs → хотя бы один участник команды в базе;
      3) оба пусты → все команды.
    """
    results: List[Dict] = []
    known = {
        (str(ln).strip().lower(), str(fn).strip().lower())
        for ln, fn in base_pairs
    }
    team_name_lower = (team_name or '').strip().lower()
    group_name = _extract_group_from_h2(html)

    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.IGNORECASE | re.DOTALL)
    if not rows:
        return results

    blocks = _split_relay_blocks(rows[1:])  # rows[0] = заголовок таблицы
    if not blocks:
        return results

    teams = [_parse_relay_team_block(b) for b in blocks]

    # Время лидера — команда с place='1' и валидным итоговым временем
    leader_time_sec: Optional[int] = None
    for team in teams:
        if team['place'] == '1' and team['total_time_sec']:
            leader_time_sec = team['total_time_sec']
            break

    num_teams = len(teams)

    for team in teams:
        members = team['members']  # [(ln, fn, club), ...]
        if not members:
            continue
        relay_team = team['relay_team_name']

        # Фильтрация — всегда по-участниково:
        # участник попадает в результаты, если его клуб (cells[3]) совпадает
        # с team_name ИЛИ он есть в базе known. '*' — показать всех.
        if team_name_lower == '*':
            filtered = [(ln, fn) for ln, fn, _club in members]
        elif team_name_lower:
            filtered = [
                (ln, fn) for ln, fn, club in members
                if team_name_lower in club.lower()
                or (ln.lower(), fn.lower()) in known
            ]
        elif known:
            filtered = [
                (ln, fn) for ln, fn, _club in members
                if (ln.lower(), fn.lower()) in known
            ]
        else:
            filtered = [(ln, fn) for ln, fn, _club in members]

        if not filtered:
            continue

        # Очки команды
        place = team['place']
        total_time = team['total_time_sec']
        if not place or not total_time or not leader_time_sec:
            points = 0
        elif place == '1':
            points = 1000
        else:
            points = _calc_points(total_time, leader_time_sec)

        place_display = place if place else '-'

        for last_name, first_name in filtered:
            raw_line = (
                f"{place_display} из {num_teams} (эстафета), "
                f"{last_name} {first_name}, очки:{points}"
            )
            results.append({
                'group_name': group_name,
                'place': place_display,
                'place_out_of': num_teams,
                'last_name': last_name,
                'first_name': first_name,
                'points': points,
                'raw_line': raw_line,
            })

    return results


# ── Индивидуальный старт ──────────────────────────────────────────────────────

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

    # ── Автодетект эстафеты: если несколько подряд непустых строк — это эстафета ──
    if _is_relay_group(data_rows):
        return parse_orgeo_relay(html, base_pairs, team_name)

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

        # ── Фильтрация ('*' — показать всех) ──
        if team_name == '*':
            pass
        elif team_name:
            team_matches = team_name.lower() in team_cell.lower()
            known_matches = key_normalized in known
            if not team_matches and not known_matches:
                continue
        elif known:
            if key_normalized not in known:
                continue

        result_str = cells[4].strip() if len(cells) > 4 else ''
        # Место: Format A — явная колонка cells[6], Format B — П/п cells[0]
        place_col = _get_place(cells)

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