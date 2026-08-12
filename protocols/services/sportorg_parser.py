# -*- coding: utf-8 -*-
"""
Загрузка и парсинг протоколов SportOrg (HTML-экспорт).

Такие страницы публикуются, например, на o-club.ru:
  - https://o-club.ru/.../20260730_results.html?sportorg=1

Таблицы в HTML отсутствуют: страница содержит пустой <div id="results-tables">,
который заполняет JavaScript. Данные лежат в переменной raceCompressed —
это base64(gzip(JSON)) со всей гонкой: persons, results, groups, organizations.
Поэтому парсим не HTML, а распакованный JSON — без браузера.

В Protocol.raw_html сохраняется base64-строка с префиксом SPORTORG_DATA_PREFIX,
чтобы протокол можно было перепарсить с другим фильтром без повторной загрузки.
"""
import base64
import binascii
import gzip
import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Маркер в начале Protocol.raw_html: дальше идёт base64(gzip(JSON)) гонки.
SPORTORG_DATA_PREFIX = "<!--SPORTORG_RACE_B64-->"

# Статус результата в SportOrg: 1 = OK (результат идёт в зачёт).
_STATUS_OK = 1

# Тип гонки в race.data.race_type: 3 — эстафета.
_RACE_TYPE_RELAY = 3

_RACE_COMPRESSED_RE = re.compile(
    r"raceCompressed\s*=\s*['\"]([A-Za-z0-9+/=]+)['\"]"
)

_ALLOWED_URL_SCHEMES = ("http", "https")


def is_sportorg_html(html: str) -> bool:
    """True, если HTML — экспорт SportOrg (содержит сжатые данные гонки)."""
    return bool(html) and bool(_RACE_COMPRESSED_RE.search(html))


def is_sportorg_payload(raw: str) -> bool:
    """True, если Protocol.raw_html хранит данные SportOrg."""
    return bool(raw) and raw.lstrip().startswith(SPORTORG_DATA_PREFIX)


def _fetch_html(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Загружает HTML-страницу. Разрешены только http/https (защита от SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        return None, f"Недопустимая схема URL: {parsed.scheme!r}."
    try:
        req = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ProtocolParser/1.0)"},
        )
        with urlopen(req, timeout=60) as resp:  # nosec B310 - схема проверена выше
            raw = resp.read()
    except (URLError, HTTPError, OSError) as e:
        return None, str(e)

    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), None


def extract_race_b64(html: str) -> Optional[str]:
    """Достаёт base64-строку raceCompressed из HTML SportOrg."""
    m = _RACE_COMPRESSED_RE.search(html or "")
    return m.group(1) if m else None


def decode_race(payload: str) -> Optional[Dict]:
    """
    Распаковывает данные гонки.
    Принимает как raw_html с префиксом, так и чистую base64-строку.
    """
    if not payload:
        return None
    data = payload.strip()
    if data.startswith(SPORTORG_DATA_PREFIX):
        data = data[len(SPORTORG_DATA_PREFIX):].strip()
    try:
        raw = gzip.decompress(base64.b64decode(data))
        return json.loads(raw.decode("utf-8"))
    except (binascii.Error, OSError, ValueError, UnicodeDecodeError):
        return None


def _race_title_and_date(race: Dict) -> Tuple[str, Optional[str]]:
    """
    Название и дата соревнования из race.data.
    Название собираем как «Заголовок — Дисциплина» (дисциплина лежит в description).
    """
    data = race.get("data") or {}
    title = (data.get("title") or "").strip()

    description = data.get("description") or ""
    # description: 'Макушка Лета 2026<br>\nКросс-спринт<br>\nКВ - 60 Минут<br>\n'
    parts = [
        re.sub(r"<[^>]+>", " ", chunk).strip()
        for chunk in re.split(r"<br\s*/?>", description)
    ]
    parts = [p for p in parts if p]
    # Вид программы — строка описания, которая не дублирует название и не КВ.
    discipline = ""
    title_lower = title.lower()
    for part in parts:
        part_lower = part.lower()
        if part_lower in title_lower or title_lower in part_lower:
            continue
        if part.upper().startswith("КВ"):
            continue
        discipline = part
        break
    if title and discipline:
        title = f"{title} — {discipline}"
    elif not title:
        title = discipline

    event_date = None
    start_dt = (data.get("start_datetime") or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", start_dt)
    if m:
        event_date = m.group(1)
    return title, event_date


def fetch_sportorg(url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Загружает страницу SportOrg по URL.

    Возвращает (payload, title, event_date, error), где payload — содержимое
    для Protocol.raw_html (префикс + base64), event_date — строка YYYY-MM-DD.
    """
    html, err = _fetch_html(url)
    if err or not html:
        return None, None, None, f"Не удалось загрузить страницу: {err or 'пустой ответ'}"

    b64 = extract_race_b64(html)
    if not b64:
        return None, None, None, "На странице не найдены данные SportOrg (raceCompressed)."

    race = decode_race(b64)
    if not race:
        return None, None, None, "Не удалось распаковать данные гонки SportOrg."

    title, event_date = _race_title_and_date(race)
    return f"{SPORTORG_DATA_PREFIX}{b64}", title, event_date, None


def _calc_points(t_actual_sec: float, t_leader_sec: float) -> int:
    """Единая формула проекта: (2 - t/t_лидера) × 1000, минимум 1."""
    if t_leader_sec <= 0:
        return 0
    return max(1, int((2 - t_actual_sec / t_leader_sec) * 1000))


def _is_real_person(person: Dict) -> bool:
    """Отсеивает служебные записи SportOrg («_Резерв», пустые строки)."""
    surname = (person.get("surname") or "").strip()
    name = (person.get("name") or "").strip()
    if not surname or not name:
        return False
    return not surname.startswith("_")


def _index_race(race: Dict) -> Tuple[Dict, Dict, Dict]:
    persons = {p["id"]: p for p in race.get("persons") or [] if p.get("id")}
    groups = {g["id"]: g for g in race.get("groups") or [] if g.get("id")}
    orgs = {o["id"]: o for o in race.get("organizations") or [] if o.get("id")}
    return persons, groups, orgs


def _matches_filter(
    last_name: str,
    first_name: str,
    team: str,
    known: set,
    team_filter: str,
    show_all: bool,
) -> bool:
    """
    Правило фильтрации, единое для всех парсеров проекта:
      - team_name='*' — все участники;
      - team_name задан — участники команды ИЛИ известные участники;
      - иначе — только известные участники;
      - если база пуста и команда не задана — все.
    """
    if show_all:
        return True
    key = (last_name.strip().lower(), first_name.strip().lower())
    if team_filter:
        return team_filter in (team or "").lower() or key in known
    if known:
        return key in known
    return True


def _parse_personal(
    race: Dict,
    base_pairs: List[Tuple[str, str]],
    team_name: str,
) -> List[Dict]:
    """Личные дисциплины: очки по времени, лидер группы = минимальное время."""
    persons, groups, orgs = _index_race(race)
    team_filter = (team_name or "").strip().lower()
    show_all = team_filter == "*"
    known = {
        (str(ln).strip().lower(), str(fn).strip().lower()) for ln, fn in base_pairs
    }

    # Время лидера и число финишировавших — по каждой группе.
    leader_sec: Dict[str, float] = {}
    finishers: Dict[str, int] = {}
    for result in race.get("results") or []:
        person = persons.get(result.get("person_id"))
        if not person or result.get("status") != _STATUS_OK:
            continue
        msec = result.get("result_msec") or 0
        if msec <= 0:
            continue
        gid = person.get("group_id") or ""
        finishers[gid] = finishers.get(gid, 0) + 1
        sec = msec / 1000.0
        if gid not in leader_sec or sec < leader_sec[gid]:
            leader_sec[gid] = sec

    results: List[Dict] = []
    for result in race.get("results") or []:
        person = persons.get(result.get("person_id"))
        if not person or not _is_real_person(person):
            continue

        last_name = (person.get("surname") or "").strip()
        first_name = (person.get("name") or "").strip()
        team = (orgs.get(person.get("organization_id")) or {}).get("name") or ""
        if not _matches_filter(last_name, first_name, team, known, team_filter, show_all):
            continue

        gid = person.get("group_id") or ""
        group_name = (groups.get(gid) or {}).get("name") or ""

        is_ok = result.get("status") == _STATUS_OK
        msec = result.get("result_msec") or 0
        if is_ok and msec > 0:
            place_value = result.get("place")
            place = str(place_value) if isinstance(place_value, int) and place_value > 0 else "-"
            place_out_of = finishers.get(gid)
            points = _calc_points(msec / 1000.0, leader_sec.get(gid, 0))
        else:
            place = _status_label(result)
            place_out_of = None
            points = 0

        key = (last_name, first_name)
        if key not in base_pairs:
            base_pairs.append(key)
            known.add((last_name.lower(), first_name.lower()))

        place_part = f"{place} из {place_out_of}" if place_out_of else place
        results.append(
            {
                "group_name": group_name,
                "place": place,
                "place_out_of": place_out_of,
                "last_name": last_name,
                "first_name": first_name,
                "points": points,
                "raw_line": f"{place_part} , {last_name} {first_name}, очки:{points}",
            }
        )
    return results


def _status_label(result: Dict) -> str:
    """Человекочитаемый статус для незачётного результата."""
    comment = (result.get("status_comment") or "").strip()
    text = f"{comment} {result.get('result') or ''}".lower()
    if any(token in text for token in ("не старт", "dns", "н/с")):
        return "н.старт"
    return "снят"


def _parse_relay(
    race: Dict,
    base_pairs: List[Tuple[str, str]],
    team_name: str,
) -> List[Dict]:
    """
    Эстафета: участники команды получают одинаковые очки по итоговому времени
    команды — как в parse_orgeo_relay.

    В SportOrg номер эстафетного участника кодирует команду и этап:
    bib = этап * 1000 + номер команды. Результат последнего этапа — накопленное
    время команды, его место — место команды. Команда считается финишировавшей,
    только если сдан последний этап: иначе накопленное время неполное и очки
    получились бы завышенными.
    """
    persons, groups, orgs = _index_race(race)
    leg_count = ((race.get("data") or {}).get("relay_leg_count")) or 0
    team_filter = (team_name or "").strip().lower()
    show_all = team_filter == "*"
    known = {
        (str(ln).strip().lower(), str(fn).strip().lower()) for ln, fn in base_pairs
    }

    # (group_id, номер команды) → список (этап, person, result)
    teams: Dict[Tuple[str, int], List[Tuple[int, Dict, Dict]]] = {}
    for result in race.get("results") or []:
        person = persons.get(result.get("person_id"))
        if not person or not _is_real_person(person):
            continue
        bib = person.get("bib") or 0
        if bib <= 0:
            continue
        key = (person.get("group_id") or "", bib % 1000)
        teams.setdefault(key, []).append((bib // 1000, person, result))

    # Число этапов: из настроек гонки, иначе — максимальный этап в протоколе.
    if leg_count <= 0:
        leg_count = max(
            (leg for members in teams.values() for leg, _p, _r in members),
            default=0,
        )

    # Итоговое время команды — результат последнего этапа.
    finals: Dict[Tuple[str, int], Tuple[Optional[float], Optional[int]]] = {}
    for key, members in teams.items():
        members.sort(key=lambda item: item[0])
        last_leg, _person, last_result = members[-1]
        msec = last_result.get("result_msec") or 0
        finished = (
            last_leg == leg_count
            and last_result.get("status") == _STATUS_OK
            and msec > 0
        )
        if not finished:
            finals[key] = (None, None)
            continue
        place_value = last_result.get("place")
        place = place_value if isinstance(place_value, int) and place_value > 0 else None
        finals[key] = (msec / 1000.0, place)

    # Лидер и число команд — по группе.
    # Лидер определяется по минимальному итоговому времени, а не по месту:
    # место на последнем этапе не всегда проставлено.
    leader_sec: Dict[str, float] = {}
    team_count: Dict[str, int] = {}
    for (gid, _team_no), (total_sec, _place) in finals.items():
        team_count[gid] = team_count.get(gid, 0) + 1
        if total_sec and (gid not in leader_sec or total_sec < leader_sec[gid]):
            leader_sec[gid] = total_sec

    results: List[Dict] = []
    for key, members in teams.items():
        gid, _team_no = key
        total_sec, place = finals[key]
        group_name = (groups.get(gid) or {}).get("name") or ""
        num_teams = team_count.get(gid)

        if total_sec:
            # Лидер группы получает ровно 1000 очков по общей формуле.
            points = _calc_points(total_sec, leader_sec.get(gid, 0))
            place_display = str(place) if place else "-"
        else:
            points = 0
            place_display = "снят"

        for _leg, person, _result in members:
            last_name = (person.get("surname") or "").strip()
            first_name = (person.get("name") or "").strip()
            team = (orgs.get(person.get("organization_id")) or {}).get("name") or ""
            if not _matches_filter(
                last_name, first_name, team, known, team_filter, show_all
            ):
                continue

            pair = (last_name, first_name)
            if pair not in base_pairs:
                base_pairs.append(pair)
                known.add((last_name.lower(), first_name.lower()))

            results.append(
                {
                    "group_name": group_name,
                    "place": place_display,
                    "place_out_of": num_teams,
                    "last_name": last_name,
                    "first_name": first_name,
                    "points": points,
                    "raw_line": (
                        f"{place_display} из {num_teams} (эстафета), "
                        f"{last_name} {first_name}, очки:{points}"
                    ),
                }
            )
    return results


def parse_sportorg(
    payload: str,
    base_pairs: List[Tuple[str, str]],
    team_name: str = "",
) -> List[Dict]:
    """
    Парсит данные SportOrg (содержимое Protocol.raw_html или base64-строку).
    Возвращает список dict, совместимый с ProtocolResult.
    Эстафета определяется автоматически по race.data.race_type.
    """
    race = decode_race(payload)
    if not race:
        return []
    race_type = ((race.get("data") or {}).get("race_type")) or 0
    if race_type == _RACE_TYPE_RELAY:
        return _parse_relay(race, base_pairs, team_name)
    return _parse_personal(race, base_pairs, team_name)
