# -*- coding: utf-8 -*-
"""
Загрузка и парсинг результатов SportIdent Online.

Поддерживаем ссылки вида:
  - https://sportident.online/ol_new/?id=2743
  - https://sportident.online/ol_new/?id=2743&g=7
"""
import re
from html import unescape
from typing import List, Optional, Tuple, Dict
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

SPORTIDENT_TABLES_DELIMITER = "\n<!--SPORTIDENT_TABLE_DELIMITER-->\n"


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value, flags=re.IGNORECASE)
    value = unescape(value or "")
    return " ".join((value or "").replace("\xa0", " ").split()).strip()


def _extract_event_id(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        ids = q.get("id") or []
        return (ids[0] or "").strip() if ids else None
    except Exception:
        return None


def _extract_group_urls(main_html: str, base_url: str) -> List[str]:
    links = re.findall(
        r"<a[^>]+href=['\"]([^'\"]*\\?id=\d+&g=\d+[^'\"]*)['\"]",
        main_html,
        flags=re.IGNORECASE,
    )
    uniq = []
    seen = set()
    for href in links:
        abs_url = urljoin(base_url, href)
        if abs_url not in seen:
            seen.add(abs_url)
            uniq.append(abs_url)
    return uniq


_ALLOWED_URL_SCHEMES = ("http", "https")


def _fetch_html_best_effort(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Загружает HTML и декодирует с подбором кодировки.
    Для SportIdent это критично: встречаются смешанные/битые страницы.

    Безопасность: разрешаем только http/https (защита от SSRF/LFI через file://).
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        return None, f"Недопустимая схема URL: {parsed.scheme!r}."
    try:
        req = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ProtocolParser/1.0)"},
        )
        with urlopen(req, timeout=30) as resp:  # nosec B310 - scheme validated above
            raw = resp.read()
    except (URLError, HTTPError, OSError) as e:
        return None, str(e)

    for enc in ("utf-8", "cp1251", "windows-1251"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue

    # Последний безопасный fallback: не падаем, даже если есть битые байты.
    return raw.decode("windows-1251", errors="replace"), None


def _seconds_from_time(value: str) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    # SportIdent может выдавать время как 0:07:55,4 — сначала нормализуем.
    value = value.replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m = int(parts[0]), int(parts[1])
            sec_float = float(parts[2])
            return int(h * 3600 + m * 60 + sec_float)
        if len(parts) == 2:
            m = int(parts[0])
            sec_float = float(parts[1])
            return int(m * 60 + sec_float)
    except ValueError:
        return None
    return None


def _calc_points(t_actual_sec: int, t_leader_sec: int) -> int:
    if t_leader_sec <= 0:
        return 0
    points = int((2 - t_actual_sec / t_leader_sec) * 1000)
    return max(1, points)


def _normalize_status(cells_text: List[str]) -> Optional[str]:
    """
    Нормализует служебные статусы:
      - DSQ/DNF/MP -> "снят"
      - DNS/н.старт -> "н.старт"
    """
    merged = " ".join(((c or "").strip().lower()) for c in cells_text)
    if any(token in merged for token in ("dns", "н.старт", "н. старт", "не старт", "н/старт")):
        return "н.старт"
    if any(token in merged for token in ("dsq", "dnf", "mp")):
        return "снят"
    return None


def fetch_sportident_tables(url: str) -> Tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """
    Возвращает (html_chunks, title, error).
    Каждый chunk — HTML отдельной группы.
    """
    event_id = _extract_event_id(url)
    if not event_id:
        return None, None, "Не найден параметр id в ссылке SportIdent."

    base_url = f"https://sportident.online/ol_new/?id={event_id}"
    main_html, err = _fetch_html_best_effort(base_url)
    if err or not main_html:
        return None, None, f"Не удалось загрузить страницу соревнования: {err or 'пустой ответ'}"

    title = ""
    m_title = re.search(r"<title[^>]*>(.*?)</title>", main_html, re.IGNORECASE | re.DOTALL)
    if m_title:
        title = _clean_text(m_title.group(1))

    group_urls = _extract_group_urls(main_html, base_url)
    if not group_urls:
        # Иногда приходит сразу страница конкретной группы.
        if re.search(r"<tr[^>]+class=['\"]hoverRow['\"]", main_html, re.IGNORECASE):
            return [main_html], title, None
        return None, title, "Не найдены группы результатов (проверьте id соревнования)."

    chunks: List[str] = []
    for g_url in group_urls:
        g_html, g_err = _fetch_html_best_effort(g_url)
        if g_err or not g_html:
            continue
        chunks.append(g_html)

    if not chunks:
        return None, title, "Не удалось загрузить страницы групп SportIdent."
    return chunks, title, None


def parse_sportident(
    html: str,
    base_pairs: List[Tuple[str, str]],
    team_name: str = "",
) -> List[Dict]:
    """
    Парсит одну HTML-страницу группы SportIdent.
    Фильтр:
      - если указан team_name: по подстроке в команде ИЛИ по базе известных участников;
      - иначе по базе известных участников (base_pairs).
    """
    results: List[Dict] = []
    team_filter = (team_name or "").strip().lower()
    show_all = team_filter == "*"
    known = {
        (str(ln).strip().lower(), str(fn).strip().lower())
        for ln, fn in base_pairs
    }

    group_name = ""
    m_group = re.search(
        r"<div[^>]+class=['\"][^'\"]*title_grup[^'\"]*['\"][^>]*>(.*?)</div>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m_group:
        group_name = _clean_text(m_group.group(1))

    rows = re.findall(
        r"<tr[^>]+class=['\"][^'\"]*hoverRow[^'\"]*['\"][^>]*>(.*?)</tr>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    place_out_of = len(rows) if rows else None

    leader_time_sec = None
    for row in rows:
        cells = re.findall(
            r"<td[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL
        )
        if not cells:
            continue
        place_candidate = _clean_text(cells[0])
        if place_candidate != "1":
            continue
        for cell in cells:
            t = _clean_text(cell)
            sec = _seconds_from_time(t)
            if sec is not None:
                leader_time_sec = sec
                break
        if leader_time_sec is not None:
            break

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue

        cleaned_cells = [_clean_text(c) for c in cells]
        status_place = _normalize_status(cleaned_cells)
        place = status_place or cleaned_cells[0]
        full_name = _clean_text(cells[1])
        team = _clean_text(cells[2]) if len(cells) > 2 else ""

        if not full_name:
            continue
        name_parts = full_name.split()
        if len(name_parts) < 2:
            continue
        last_name = name_parts[0]
        first_name = " ".join(name_parts[1:])
        key = (last_name, first_name)
        key_normalized = (last_name.lower(), first_name.lower())

        if show_all:
            pass
        elif team_filter:
            team_matches = team_filter in team.lower()
            known_matches = key_normalized in known
            if not team_matches and not known_matches:
                continue
        else:
            if key_normalized not in known:
                continue

        if key not in base_pairs:
            base_pairs.append(key)
            known.add(key_normalized)

        points = 0
        runner_time_sec = None
        for cell in cells:
            txt = _clean_text(cell)
            sec = _seconds_from_time(txt)
            if sec is not None:
                runner_time_sec = sec
                break
        if status_place:
            points = 0
        elif runner_time_sec is not None and leader_time_sec is not None:
            points = _calc_points(runner_time_sec, leader_time_sec)

        place_out_of_value = None if status_place else place_out_of
        place_part = (
            f"{place or '?'} из {place_out_of_value}"
            if place_out_of_value
            else f"{place or '?'}"
        )
        raw_line = f"{place_part} , {last_name} {first_name}, очки:{points}"
        results.append(
            {
                "group_name": group_name,
                "place": place,
                "place_out_of": place_out_of_value,
                "last_name": last_name,
                "first_name": first_name,
                "points": points,
                "raw_line": raw_line,
            }
        )

    return results
