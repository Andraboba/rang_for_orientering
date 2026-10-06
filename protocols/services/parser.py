# -*- coding: utf-8 -*-
"""
Парсер протоколов WinOrient (и аналогов).
Два формата: sfr=0 (текст с h2-группами), sfr=1 (HTML-таблица с <tr>, <a>).
"""
import re
from typing import List, Tuple, Optional
from urllib.parse import urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Разрешённые схемы для загрузки по URL.
# Защита от SSRF/LFI: urlopen() по умолчанию умеет открывать file://, ftp:// и др. —
# пользовательский ввод не должен этого делать.
_ALLOWED_URL_SCHEMES = ('http', 'https')


def _validate_url_scheme(url: str) -> Optional[str]:
    """Возвращает None, если схема URL безопасна, иначе сообщение об ошибке."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return f'Недопустимая схема URL: {parsed.scheme!r}. Разрешены: http, https.'
    if not parsed.netloc:
        return 'URL не содержит имени хоста.'
    return None


def fetch_url(url: str, encoding_utf8: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """
    Загружает HTML по URL. Возвращает (text, error_message).
    Принимаем только http/https (защита от SSRF через file://, ftp:// и т.п.).
    """
    scheme_err = _validate_url_scheme(url)
    if scheme_err:
        return None, scheme_err
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; ProtocolParser/1.0)'})
        # Схема уже валидирована выше, но bandit B310 просит отметить явно.
        with urlopen(req, timeout=30) as resp:  # nosec B310 - scheme validated above
            raw = resp.read()
        encoding = 'utf-8' if encoding_utf8 else 'windows-1251'
        text = raw.decode(encoding)
        return text, None
    except (URLError, HTTPError, OSError) as e:
        return None, str(e)
    except UnicodeDecodeError as e:
        return None, f'Ошибка кодировки: {e}'


def _parse_format_pre(html: str) -> List[str]:
    """Формат sfr=0: строки с пробелом в начале или с h2."""
    lines = []
    for line in html.splitlines():
        if line.startswith(' ') or 'h2' in line:
            lines.append(line)
    return lines


def _parse_format_sfr(html: str) -> List[str]:
    """Формат sfr=1: только строки с <tr или <a."""
    lines = []
    for line in html.splitlines():
        if '<tr' in line or '<a' in line:
            lines.append(line)
    return lines


def _extract_title_pre(html: str) -> str:
    """Извлечь заголовок из формата с <h1>."""
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
    if m:
        title = re.sub(r'<[^>]+>', ' ', m.group(1)).replace('\n', ' ').strip()
        return ' '.join(title.split())
    return ''


def _parse_line_pre(line: str) -> Optional[list]:
    """
    Строка участника формата pre: «№п/п Фамилия Имя Коллектив … Результат … Место».
    Возвращает токены строки или None, если это не строка участника.
    """
    line = line.replace('\n', '').strip()
    if not line or 'h2' in line:
        return None
    parts = line.split()
    if len(parts) < 4 or not parts[0].isdigit():
        return None
    return parts


# Время результата «00:24:38». «+11:03» — отставание, «00:00» — штраф.
_PRE_TIME_RE = re.compile(r'^\d{1,2}:\d{2}:\d{2}$')
# Номер пункта правил вместо результата: «3.13.12.2», «п.п.7.2.6».
_PRE_RULE_RE = re.compile(r'^(?:п\.)*\d+(?:\.\d+){2,}$')
_PRE_GROUP_RE = re.compile(r'<h2[^>]*>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
# Строка-заголовок команды в эстафетном протоколе: «<b>12 </b>».
_RELAY_TEAM_RE = re.compile(r'^\s*<b>\s*\d+\s*</b>\s*$', re.IGNORECASE)


def _is_status_token(token: str) -> bool:
    return bool(_PRE_RULE_RE.match(token)) or token.upper() in ('СНЯТ', 'СОШЕЛ', 'СОШЁЛ')


def _has_status(parts: List[str]) -> bool:
    """Снят, сошёл или не стартовал («НЕ СТАРТ»)."""
    upper = [p.upper() for p in parts]
    if 'СТАРТ' in upper and 'НЕ' in upper:
        return True
    return any(_is_status_token(p) for p in parts)


def _group_from_h2(line: str) -> str:
    """«<h2>Ж12, 10 КП, 3 км</h2>» → «Ж12»."""
    m = _PRE_GROUP_RE.search(line)
    if not m:
        return ''
    text = ' '.join(re.sub(r'<[^>]+>', ' ', m.group(1)).split())
    return text.split(',')[0].strip()


def _pre_result(parts: List[str]) -> Tuple[str, Optional[str]]:
    """
    Место и время из строки личного протокола. Состав колонок бывает разный:
    «Результат Отставание Место», «ШТРАФ Результат Отставание Место»,
    «Результат Место Вып» — поэтому результат ищется по виду значения:
    последнее время ЧЧ:ММ:СС в строке, место — первое число после него.

    Возвращает (место, время). Место «снят» — статус или нет результата,
    «в/к» — вне конкурса, «-» — время есть, а места нет.
    """
    if _has_status(parts):
        return 'снят', None
    time_indices = [i for i, p in enumerate(parts) if _PRE_TIME_RE.match(p)]
    if not time_indices:
        return 'снят', None
    t_idx = time_indices[-1]
    for token in parts[t_idx + 1:]:
        if token.isdigit():
            return token, parts[t_idx]
    if any(p.lower() == 'в/к' for p in parts[t_idx + 1:]):
        return 'в/к', parts[t_idx]
    return '-', parts[t_idx]


def _parse_line_sfr(line: str) -> Optional[list]:
    """Парсинг строки формата SFR (HTML таблица)."""
    g = line.replace("\n", "")
    g = g.replace("<tr style='background: #FFFFFF;'><td><nobr>", " ")
    g = g.replace("</td><td><nobr>", " ")
    g = g.replace("</td><td class = 'cr'><nobr>", " ")
    g = g.replace("</td></tr", " ")
    g = g.replace("<tr  class = 'yl'><td><nobr>", " ")
    parts = list(filter(None, g.split(" ")))
    return parts if len(parts) > 4 else None


def _seconds_from_time(s: str) -> Optional[int]:
    """Конвертирует строку вида 00:12:44 в секунды."""
    if not s or len(s) < 5:
        return None
    s = s.strip()
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


def _calc_points(t_actual_sec: int, t_limit_sec: int) -> int:
    """Очки по формуле (2 - t_actual/t_limit) * 1000, минимум 1 при наличии времени."""
    if t_limit_sec <= 0:
        return 0
    points = int((2 - t_actual_sec / t_limit_sec) * 1000)
    return max(1, points)


def process_protocol_pre(
    lines: List[str],
    team_name: str,
    base_pairs: List[Tuple[str, str]],
) -> List[dict]:
    """
    Обработка формата sfr=0 (pre/h2).
    Возвращает список dict: group_name, place, place_out_of, last_name, first_name, points, raw_line.
    base_pairs модифицируется (добавляются новые участники).
    """
    # Группы — строки между заголовками h2; без h2 весь протокол — одна группа.
    groups: List[Tuple[str, List[Tuple[str, List[str]]]]] = []
    group_name, entries = '', []
    for line in lines:
        if 'h2' in line:
            if entries:
                groups.append((group_name, entries))
            group_name, entries = _group_from_h2(line), []
            continue
        parts = _parse_line_pre(line)
        if parts:
            entries.append((line, parts))
    if entries:
        groups.append((group_name, entries))

    # Нормализованная база для поиска без учёта регистра
    known_lower = {(ln.strip().lower(), fn.strip().lower()) for ln, fn in base_pairs}
    team_lower = team_name.strip().lower() if team_name else ''

    results = []
    for group_name, entries in groups:
        group_size = len(entries)
        # Протокол отсортирован по результату: лидер — первый, у кого есть время.
        t_leader = None
        for line, parts in entries:
            place, time_str = _pre_result(parts)
            t_actual = _seconds_from_time(time_str) if time_str else None
            if t_leader is None and t_actual:
                t_leader = t_actual

            key = (parts[1], parts[2])
            team_matches = bool(team_lower and team_lower in line.lower())
            known_matches = (key[0].lower(), key[1].lower()) in known_lower
            if not team_matches and not known_matches:
                continue

            points = _calc_points(t_actual, t_leader) if t_actual and t_leader else 0
            if key not in base_pairs:
                base_pairs.append(key)
            results.append({
                'group_name': group_name,
                'place': place,
                'place_out_of': group_size,
                'last_name': key[0],
                'first_name': key[1],
                'points': points,
                'raw_line': f"{place} из {group_size} , {key[0]} {key[1]}, очки:{points}",
            })
    return results


def _relay_member(parts: List[str]) -> Optional[dict]:
    """
    Строка этапа эстафеты. Варианты:
      «41  Темкина Виктория  СК Фотон  00:24:38  00:24:38  1»
      «104 2 Утина Анастасия  CRAFT  МС  2002  00:18:59  00:39:41  5»  (с номером этапа)
    Первое время — время этапа, второе — время команды, за ним — место.
    """
    if len(parts) < 3 or not parts[0].isdigit():
        return None
    idx = 2 if parts[1].isdigit() else 1
    if len(parts) < idx + 2:
        return None
    last_name, first_name = parts[idx], parts[idx + 1]
    if not last_name[0].isalpha() or not first_name[0].isalpha():
        return None

    time_indices = [i for i, p in enumerate(parts) if _PRE_TIME_RE.match(p)]
    place = ''
    if len(time_indices) >= 2:
        for token in parts[time_indices[1] + 1:]:
            if token.isdigit():
                place = token
                break
    return {
        'last_name': last_name,
        'first_name': first_name,
        'stage_sec': _seconds_from_time(parts[time_indices[0]]) if time_indices else None,
        'total_sec': (
            _seconds_from_time(parts[time_indices[1]]) if len(time_indices) >= 2 else None
        ),
        'place': place,
        'status': _has_status(parts),
    }


def _process_relay_pre(
    block: str,
    team_name: str,
    base_pairs: List[Tuple[str, str]],
) -> List[dict]:
    """
    Эстафетная группа WinOrient: перед каждой командой строка «<b>N </b>»,
    дальше по строке на этап. Все члены команды получают очки по итоговому
    времени команды; команда, у которой хоть один этап снят или не пробежан,
    очков не получает.
    """
    group_name = _group_from_h2(block)
    teams: List[List[Tuple[str, dict]]] = []
    for line in block.splitlines():
        if _RELAY_TEAM_RE.match(line):
            teams.append([])
            continue
        if not teams or not line.startswith(' '):
            continue
        member = _relay_member(line.split())
        if member:
            teams[-1].append((line, member))

    # Команда, в которой вышли не все участники, не финишировала.
    full_stages = max((len(members) for members in teams), default=0)

    def team_time(members: List[Tuple[str, dict]]) -> Optional[int]:
        stages = [m for _line, m in members]
        if len(stages) < full_stages or any(m['status'] or not m['stage_sec'] for m in stages):
            return None
        if not stages[-1]['total_sec']:
            return None
        # Время команды иногда напечатано с ошибкой (меньше суммы этапов),
        # поэтому берём большее из него и суммы этапов.
        return max(stages[-1]['total_sec'], sum(m['stage_sec'] for m in stages))

    times = [team_time(members) for members in teams]
    places = [
        next((m['place'] for _line, m in reversed(members) if m['place']), '')
        for members in teams
    ]
    # Лидер — команда на 1-м месте. Если первой стоит команда вне конкурса
    # (места у неё нет, а у следующих места 2, 3 …), лидер — самая быстрая.
    leader = next((t for t, place in zip(times, places) if place == '1' and t), None)
    if leader is None:
        leader = min((t for t in times if t), default=None)

    known_lower = {(ln.strip().lower(), fn.strip().lower()) for ln, fn in base_pairs}
    team_lower = team_name.strip().lower() if team_name else ''
    num_teams = len(teams)

    results = []
    for members, t_team, place in zip(teams, times, places):
        if t_team and place and leader:
            points = _calc_points(t_team, leader)
        else:
            points = 0
        place_display = (place or 'в/к') if t_team else 'снят'

        seen = set()
        for line, member in members:
            key = (member['last_name'], member['first_name'])
            key_lower = (key[0].lower(), key[1].lower())
            if key_lower in seen:
                continue  # в эстафете двух участников каждый бежит по два этапа
            if team_lower == '*' or (not team_lower and not known_lower):
                pass
            elif not (team_lower and team_lower in line.lower()) and key_lower not in known_lower:
                continue
            seen.add(key_lower)
            if key not in base_pairs:
                base_pairs.append(key)
            results.append({
                'group_name': group_name,
                'place': place_display,
                'place_out_of': num_teams,
                'last_name': key[0],
                'first_name': key[1],
                'points': points,
                'raw_line': (
                    f"{place_display} из {num_teams} (эстафета), "
                    f"{key[0]} {key[1]}, очки:{points}"
                ),
            })
    return results


def _split_h2_blocks(html: str) -> List[str]:
    """Части протокола от одного <h2> до следующего (шапка до первого h2 отбрасывается)."""
    starts = [m.start() for m in re.finditer(r'<h2', html, re.IGNORECASE)]
    return [
        html[start:starts[i + 1] if i + 1 < len(starts) else len(html)]
        for i, start in enumerate(starts)
    ]


def _has_relay_teams(html: str) -> bool:
    return any(_RELAY_TEAM_RE.match(line) for line in html.splitlines())


def process_mixed_pre(
    html: str,
    team_name: str,
    base_pairs: List[Tuple[str, str]],
) -> List[dict]:
    """
    Протокол с эстафетными группами. Бывает и смешанный: в «Покровской
    горизонтали» вместе с эстафетами идут личные группы О1/О2 — их разбирает
    обычный парсер.
    """
    results = []
    for block in _split_h2_blocks(html):
        if _has_relay_teams(block):
            results.extend(_process_relay_pre(block, team_name, base_pairs))
        else:
            results.extend(
                process_protocol_pre(_parse_format_pre(block), team_name, base_pairs)
            )
    return results


def process_protocol_sfr(
    lines: List[str],
    team_name: str,
    base_pairs: List[Tuple[str, str]],
) -> List[dict]:
    """
    Обработка формата sfr=1 (HTML таблица).
    """
    results = []
    dlin = []
    for i, line in enumerate(lines):
        if '<a href' in line:
            dlin.append(i)
    dlin.append(len(lines))

    known_lower = {(ln.strip().lower(), fn.strip().lower()) for ln, fn in base_pairs}
    team_lower = team_name.strip().lower() if team_name else ''

    for y in lines:
        m = _parse_line_sfr(y)
        if not m:
            continue
        team_matches = bool(team_lower and team_lower in y.lower())
        known_matches = (m[2].strip().lower(), m[3].strip().lower()) in known_lower
        if not team_matches and not known_matches:
            continue
        if "cнят" not in m and "снят" not in m:
            indices = [i for i in range(len(m)) if m[i] == m[0]]
            if len(indices) == 2:
                idx = int(indices[1])
                if m[0] == "1":
                    result = 1000
                else:
                    t_str = m[idx - 1] if idx - 1 >= 0 else ''
                    tlich = _seconds_from_time(t_str)
                    if tlich is None and len(t_str) >= 7:
                        try:
                            tlich = int(t_str[0] + t_str[1]) * 3600 + int(t_str[3] + t_str[4]) * 60 + int(t_str[6] + t_str[7])
                        except (ValueError, IndexError):
                            tlich = 0
                    plus_str = m[idx + 1] if idx + 1 < len(m) else ''
                    tlid = tlich
                    if plus_str:
                        if len(plus_str) == 5:
                            tlid = tlich - int(plus_str[1]) * 60 - int(plus_str[3] + plus_str[4])
                        else:
                            tlid = tlich - int(plus_str[1] + plus_str[2]) * 60 - int(plus_str[4] + plus_str[5])
                    result = _calc_points(tlich, tlid) if tlid else 0
            else:
                result = 0
        else:
            result = 0
        if (m[2], m[3]) not in base_pairs:
            base_pairs.append((m[2], m[3]))
        f = lines.index(y)
        group_size = None
        for i in range(len(dlin) - 1):
            if dlin[i] < f < dlin[i + 1]:
                group_size = dlin[i + 1] - dlin[i] - 2
                break
        place_str = "снят" if ("cнят" in m or "снят" in m) else m[0]
        raw_line = f"{place_str} из {group_size or '?'} , {m[2]} {m[3]}, очки:{result}"
        results.append({
            'group_name': '',
            'place': place_str,
            'place_out_of': group_size,
            'last_name': m[2],
            'first_name': m[3],
            'points': result,
            'raw_line': raw_line,
        })
    return results


def parse_and_process(
    html: str,
    format_sfr: bool,
    team_name: str,
    base_pairs: List[Tuple[str, str]],
) -> List[dict]:
    """
    Главная функция: парсит HTML и возвращает список результатов для команды.
    base_pairs — список (фамилия, имя) для учёта участников; будет дополнен.
    """
    if format_sfr:
        lines = _parse_format_sfr(html)
        return process_protocol_sfr(lines, team_name, base_pairs)
    if _has_relay_teams(html):
        return process_mixed_pre(html, team_name, base_pairs)
    lines = _parse_format_pre(html)
    return process_protocol_pre(lines, team_name, base_pairs)


def extract_title(html: str, format_sfr: bool) -> str:
    """Извлечь заголовок соревнований из HTML."""
    if not format_sfr:
        return _extract_title_pre(html)
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r'<[^>]+>', '', m.group(1)).strip()
    return ''
