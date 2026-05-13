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
    """Парсинг одной строки формата pre (№п/п Фамилия ... Результат Место)."""
    line = line.replace('\n', '').strip()
    if not line or 'h2' in line:
        return None
    parts = list(filter(None, line.split()))
    if len(parts) < 4:
        return None
    # Ожидаем: номер, фамилия, имя/отчество..., коллектив, квал, номер, год, время, место [, прим]
    if len(parts[-3]) != 4:  # год не 4 цифры — убрать лишний элемент
        parts.pop(-2)
    # Место и результат: предпоследний — время или "Снят", последний — место
    return parts


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
    results = []
    dlin = []
    for i, line in enumerate(lines):
        if 'h2' in line:
            dlin.append(i)
    dlin.append(len(lines))

    # Нормализованная база для поиска без учёта регистра
    known_lower = {(ln.strip().lower(), fn.strip().lower()) for ln, fn in base_pairs}
    team_lower = team_name.strip().lower() if team_name else ''

    tlid = None
    for y in lines:
        m = _parse_line_pre(y)
        if not m:
            continue
        if len(m) < 5:
            continue
        if len(m[-3]) != 4:
            m.pop(-2)
        if m[0] == "1":
            tlid = _seconds_from_time(m[-2])
            if tlid is None and len(m[-2]) >= 7:
                try:
                    tlid = int(m[-2][0] + m[-2][1]) * 3600 + int(m[-2][3] + m[-2][4]) * 60 + int(m[-2][6] + m[-2][7])
                except (ValueError, IndexError):
                    pass
        key = (m[1], m[2])
        key_lower = (m[1].lower(), m[2].lower())
        team_matches = bool(team_lower and team_lower in y.lower())
        known_matches = key_lower in known_lower
        if not team_matches and not known_matches:
            continue
        if m[-1] in ("3.13.12.2", "п.3.13.12.3", "п.3.13.12.2", "п.п.7.2.6"):
            result = 0
        else:
            tlich = _seconds_from_time(m[-2])
            if tlich is None and len(m[-2]) >= 7:
                try:
                    tlich = int(m[-2][0] + m[-2][1]) * 3600 + int(m[-2][3] + m[-2][4]) * 60 + int(m[-2][6] + m[-2][7])
                except (ValueError, IndexError):
                    tlich = 0
            if tlid and tlich is not None:
                result = _calc_points(tlich, tlid)
            else:
                result = 0
        if key not in base_pairs:
            base_pairs.append(key)
        f = lines.index(y)
        group_size = None
        for i in range(len(dlin) - 1):
            if dlin[i] < f < dlin[i + 1]:
                group_size = dlin[i + 1] - dlin[i] - 1
                break
        place_str = "снят" if m[-1] in ("3.13.12.2", "п.3.13.12.3", "п.3.13.12.2", "п.п.7.2.6") else m[-1]
        raw_line = f"{place_str} из {group_size or '?'} , {m[1]} {m[2]}, очки:{result}"
        results.append({
            'group_name': '',
            'place': place_str,
            'place_out_of': group_size,
            'last_name': m[1],
            'first_name': m[2],
            'points': result,
            'raw_line': raw_line,
        })
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
    else:
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
