# -*- coding: utf-8 -*-
"""
Парсер PDF-протоколов с суммой многодневки (текстовый слой, без OCR).

Поддерживаются два семейства вёрстки:

  1) o-site.spb.ru («ККП-сумма 5 дней»):
     № п/п Фамилия, имя  номер квал Коллектив  1 день (очки) … Сумма(Очков)

  2) o-club.ru («СУММА 4-х дней», «СУММА»):
     № Фамилия, имя  Коллектив  ГР  Разряд  Номер  Сумма  Место  День 1 …

Кроме сумм разбираются и PDF одного старта (WinOrient/SportOrg):

  3) личный протокол — колонка «Результат» вместо «Сумма», группа задаётся
     строкой «Ж14, 23 КП, 4.3 км» или этапом клубной эстафеты «1этап (М16, М45)»;

  4) эстафета SportOrg — строка с номером команды, под ней по строке на этап,
     колонки «Результат» (этап) и «Ком.рез-т» (время команды).

Колонки в тексте PDF выровнены пробелами, поэтому строки режутся по границам,
взятым из строки заголовка таблицы, — это надёжнее регулярных выражений,
т.к. и коллектив, и фамилия могут содержать пробелы, цифры и запятые.

Очки приводятся к шкале проекта (лидер группы = 1000):
  - если в протоколе есть сумма очков — она нормируется к лидеру группы,
    потому что именно она определяет итоговые места в многодневке
    (участник, снятый в одном дне, сохраняет очки за остальные дни);
  - если сумма дана только временем — работает общая формула проекта
    (2 - t/t_лидера) × 1000.

В Protocol.raw_html сохраняется текстовый слой PDF с префиксом
PDF_SUMMARY_PREFIX, чтобы протокол можно было перепарсить с другим фильтром
без повторной загрузки файла.
"""
import re
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PDF_SUMMARY_PREFIX = "<!--PDF_SUMMARY_TEXT-->"

# Несколько PDF одного старта (два файла Кубка клубов, дни первенства)
# хранятся в одном протоколе через этот разделитель и разбираются по отдельности.
PDF_PART_DELIMITER = "<!--PDF_PART-->"

_ALLOWED_URL_SCHEMES = ("http", "https")

# Ключевые слова заголовка таблицы → логическое имя колонки.
# Порядок важен: более длинные ключи проверяются первыми.
_COLUMN_KEYWORDS = (
    ("Фамилия", "name"),
    ("Коллектив", "team"),
    ("Разряд", "qual"),
    ("квал", "qual"),
    ("Номер", "bib"),
    ("номер", "bib"),
    ("Сумма", "total"),
    # Одиночный протокол: вместо суммы — результат дня.
    ("Результат", "total"),
    ("Место", "place"),
    ("ГР", "year"),
)

# Начало колонок с результатами по дням: «1 день», «День 1», либо одиночная цифра.
_DAY_HEADER_RE = re.compile(r"(?:\d+\s*день|День\s*\d+)", re.IGNORECASE)

_TIME_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_DATE_RE = re.compile(r"(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# «16-20.06.2026» — диапазон дней многодневки.
_DATE_RANGE_RE = re.compile(r"(\d{1,2})\s*[-–]\s*(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})")

# Заголовок группы: отдельная короткая строка — Ж10RR, М14-18Б, ЖЭ*, O1.
_GROUP_RE = re.compile(r"^[A-ZА-ЯЁa-zа-яё][\wА-Яа-яЁё\-*+/]{0,11}$")

# Строки, которые похожи на группу, но ей не являются.
_NOT_GROUP_WORDS = {
    "сумма", "место", "код", "итого", "стр", "страница", "кв", "результаты",
}

# Заголовок группы одиночного протокола: «Ж14, 23 КП, 4.3 км», «Новички, 6 КП, 0,9 км».
_COURSE_GROUP_RE = re.compile(r"^\s*([^,]{1,30}?),\s*\d+\s*КП\b", re.IGNORECASE)
# Этап клубной эстафеты, у которого свои места: «1этап (М16, М45, Ж35)».
_STAGE_GROUP_RE = re.compile(r"^\s*(\d+)\s*этап\b\s*(.*?)\s*$", re.IGNORECASE)

# Эстафета SportOrg: в заголовке есть колонка с временем команды.
_RELAY_HEADER_RE = re.compile(r"Ком\.?\s*рез", re.IGNORECASE)
_BIRTH_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TEAM_NUMBER_RE = re.compile(r"^\d+$")

# Служебные строки шапки, которые не идут в название протокола.
_META_LINE_RE = re.compile(r"^(?:код\s+дисциплины|кв\s*[-—]|главный|секретарь|судья)", re.IGNORECASE)


def is_pdf_summary_payload(raw: str) -> bool:
    """True, если Protocol.raw_html хранит текст PDF-суммы."""
    return bool(raw) and raw.lstrip().startswith(PDF_SUMMARY_PREFIX)


def looks_like_pdf_url(url: str) -> bool:
    """True, если ссылка ведёт на PDF-файл."""
    return urlparse(url or "").path.lower().endswith(".pdf")


def fetch_pdf_bytes(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Скачивает PDF. Разрешены только http/https (защита от SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        return None, f"Недопустимая схема URL: {parsed.scheme!r}."
    try:
        req = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ProtocolParser/1.0)"},
        )
        with urlopen(req, timeout=60) as resp:  # nosec B310 - схема проверена выше
            data = resp.read()
    except (URLError, HTTPError, OSError) as e:
        return None, str(e)
    if not data.startswith(b"%PDF"):
        return None, "По ссылке не PDF-файл."
    return data, None


def extract_pdf_text(pdf_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Извлекает текстовый слой PDF (без OCR)."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - зависимость объявлена в requirements
        return None, f"Не установлен pymupdf: {exc}"
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
    except Exception as exc:  # noqa: BLE001 - PyMuPDF кидает разные типы
        return None, f"Не удалось прочитать PDF: {exc}"
    if not (text or "").strip():
        return None, (
            "В PDF нет текстового слоя (скан). "
            "Загрузите такой файл через форму загрузки PDF с распознаванием."
        )
    return text, None


def _find_columns(header: str) -> Optional[Dict[str, Tuple[int, int]]]:
    """
    Строит границы колонок по строке заголовка таблицы.
    Возвращает {имя_колонки: (начало, конец)} или None, если заголовок не распознан.
    """
    marks: List[Tuple[int, str]] = []
    used = set()
    for keyword, column in _COLUMN_KEYWORDS:
        if column in used:
            continue
        pos = header.find(keyword)
        if pos >= 0:
            marks.append((pos, column))
            used.add(column)

    if "name" not in used or "total" not in used:
        return None

    # Первая колонка — порядковый номер (он же место в протоколах без колонки «Место»).
    marks.append((0, "num"))

    # Колонки по дням не разбираем — важна только граница, где они начинаются.
    m_day = _DAY_HEADER_RE.search(header)
    if m_day:
        marks.append((m_day.start(), "days"))

    marks.sort()
    columns: Dict[str, Tuple[int, int]] = {}
    for idx, (pos, column) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(header) + 200
        columns[column] = (pos, end)
    return columns


def _cell(line: str, bounds: Optional[Tuple[int, int]], slack: int = 0) -> str:
    """
    Значение ячейки. slack расширяет границы: колонки в данных бывают сдвинуты
    относительно заголовка на символ-другой (например, «№ п/п Фамилия» в ККП).
    """
    if not bounds:
        return ""
    start = max(0, bounds[0] - slack)
    return line[start:bounds[1] + slack].strip()


def _int_prefix(value: str) -> str:
    """Первое целое число из ячейки: «1 Р» → «1», «30 =» → «30»."""
    m = re.search(r"\d+", value or "")
    return m.group(0) if m else (value or "").strip()[:20]


def _seconds(value: str) -> Optional[int]:
    m = _TIME_RE.search(value or "")
    if not m:
        return None
    hours, minutes, seconds = m.group(0).split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _number(value: str) -> Optional[float]:
    m = _NUMBER_RE.search((value or "").replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _calc_points(t_actual_sec: float, t_leader_sec: float) -> int:
    """Единая формула проекта: (2 - t/t_лидера) × 1000, минимум 1."""
    if t_leader_sec <= 0:
        return 0
    return max(1, int((2 - t_actual_sec / t_leader_sec) * 1000))


def _split_name(raw: str) -> Optional[Tuple[str, str]]:
    """«Рой Елизавета» → («Рой», «Елизавета»). Отчество отбрасывается."""
    parts = [p for p in re.split(r"[\s,]+", (raw or "").strip()) if p]
    words = [p for p in parts if re.match(r"^[А-ЯЁа-яёA-Za-z][А-Яа-яЁёA-Za-z\-']*$", p)]
    if len(words) < 2:
        return None
    return words[0], words[1]


def _is_group_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or " " in stripped:
        return False
    if stripped.lower() in _NOT_GROUP_WORDS:
        return False
    if stripped[0].isdigit():
        return False
    return bool(_GROUP_RE.match(stripped))


def _heading_group(line: str) -> Optional[str]:
    """
    Группа из заголовка одиночного протокола:
    «Ж14, 23 КП, 4.3 км» → «Ж14», «1этап (М16, М45, Ж35)» → «1 этап (М16, М45, Ж35)».
    """
    m_stage = _STAGE_GROUP_RE.match(line)
    if m_stage:
        number, rest = m_stage.groups()
        return f"{number} этап {rest}".strip()
    m_course = _COURSE_GROUP_RE.match(line)
    if m_course:
        return m_course.group(1).strip()
    return None


def extract_summary_meta(text: str) -> Tuple[str, Optional[str]]:
    """
    Название протокола и дата (YYYY-MM-DD) из шапки PDF.
    Для диапазона дат («16-20.06.2026») берётся последний день многодневки.
    """
    title_parts: List[str] = []
    event_date: Optional[str] = None

    for line in (text or "").splitlines()[:20]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("№") or _is_group_line(stripped) or _heading_group(stripped):
            break
        if not event_date:
            m_range = _DATE_RANGE_RE.search(stripped)
            if m_range:
                _first, last, month, year = m_range.groups()
                event_date = f"{year}-{int(month):02d}-{int(last):02d}"
            else:
                m_date = _DATE_RE.search(stripped)
                if m_date:
                    day, month, year = m_date.groups()
                    event_date = f"{year}-{int(month):02d}-{int(day):02d}"
        if _META_LINE_RE.match(stripped):
            continue
        # Из строки с датой в название берём только текстовую часть.
        cleaned = _DATE_RANGE_RE.sub("", stripped)
        cleaned = _DATE_RE.sub("", cleaned).strip(" ,–-")
        if cleaned and len(title_parts) < 3:
            title_parts.append(cleaned)

    return ", ".join(title_parts).strip(", "), event_date


def _parse_rows(text: str) -> List[Dict]:
    """
    Разбирает текст PDF в «сырые» строки участников.
    Возвращает список dict: group_name, place, last_name, first_name, team,
    total_points, total_sec.
    """
    columns: Optional[Dict[str, Tuple[int, int]]] = None
    total_in_points = False
    single_result = False
    group_name = ""
    rows: List[Dict] = []

    for line in (text or "").splitlines():
        if not line.strip():
            continue

        if line.strip().startswith("№"):
            found = _find_columns(line)
            if found:
                columns = found
                total_header = _cell(line, columns.get("total")).lower()
                # «Сумма(Очков)» — в колонке суммы стоят очки, а не только время.
                total_in_points = "очк" in total_header
                # Протокол одного старта: в колонке только время или статус
                # («п.п.3.13.12.2» — номер пункта правил, а не очки).
                single_result = total_header.startswith("результат")
            continue

        heading = _heading_group(line)
        if heading:
            group_name = heading
            continue

        if _is_group_line(line):
            group_name = line.strip()
            continue

        if not columns or not re.match(r"^\s*\d+\s", line):
            continue

        # Допуск в 3 символа: в ККП колонка с фамилией сдвинута относительно
        # заголовка, а лишние цифры номера отсеет _split_name.
        name = _split_name(_cell(line, columns.get("name"), slack=3))
        if not name:
            continue
        last_name, first_name = name

        # Время в данных выровнено по правому краю и может начинаться
        # левее заголовка «Результат».
        total_raw = _cell(line, columns.get("total"), slack=2 if single_result else 0)
        total_sec = _seconds(total_raw)
        total_points: Optional[float] = None
        total_days: Optional[int] = None
        m_brackets = re.search(r"\(([^)]*)\)", total_raw)
        if single_result:
            pass
        elif total_in_points:
            # Формат «02:03:58 (419.4)»: очки — в скобках.
            total_points = _number(m_brackets.group(1)) if m_brackets else None
        elif total_sec is not None:
            # Формат «00:46:42 (2)»: в скобках — сколько дней зачтено.
            days = _number(m_brackets.group(1)) if m_brackets else None
            total_days = int(days) if days is not None else None
        else:
            total_points = _number(total_raw)

        place = _cell(line, columns.get("place")) or _cell(line, columns.get("num"))
        place = _int_prefix(place) or "-"
        if single_result and total_sec is None:
            place = "снят"
        rows.append(
            {
                "group_name": group_name,
                "place": place,
                "last_name": last_name,
                "first_name": first_name,
                "team": _cell(line, columns.get("team")),
                "total_points": total_points,
                "total_sec": total_sec,
                "total_days": total_days,
                "is_sum": not single_result,
            }
        )
    return rows


def _is_relay_text(text: str) -> bool:
    return any(
        line.strip().startswith("№") and _RELAY_HEADER_RE.search(line)
        for line in (text or "").splitlines()
    )


def _relay_leg(line: str) -> Optional[Dict]:
    """
    Строка этапа эстафеты SportOrg:
    «Никольский Михаил Павлович  Москва  I  1409 04.03.2012 00:09:05  00:09:05  1  0».
    После даты рождения: время этапа, время команды, у первого этапа — место и очки.
    Вместо времени бывает статус: «Непр. отметка», «ДИСКВ.», «п.п.3.13.12.2».
    Строки этапов идут с отступом — так они отличаются от колонтитула страницы
    «ПО SportOrg … 02.10.2026 22:09:22», где тоже есть дата.
    """
    if not line[:1].isspace():
        return None
    tokens = line.split()
    date_idx = next((i for i, t in enumerate(tokens) if _BIRTH_DATE_RE.match(t)), None)
    if date_idx is None:
        return None
    name = _split_name(" ".join(tokens[:date_idx]))
    if not name:
        return None

    after = tokens[date_idx + 1:]
    time_idx = [i for i, t in enumerate(after) if _TIME_RE.fullmatch(t)]
    place = ""
    if len(time_idx) >= 2:
        place = next((t for t in after[time_idx[1] + 1:] if t.isdigit()), "")
    finished = len(time_idx) >= 2 and all(
        _TIME_RE.fullmatch(t) or t.isdigit() for t in after
    )
    return {
        "last_name": name[0],
        "first_name": name[1],
        "team": line,
        "team_sec": _seconds(after[time_idx[1]]) if len(time_idx) >= 2 else None,
        "place": place,
        "finished": finished,
    }


def _relay_leader(times: List[Optional[int]], places: List[str]) -> Optional[int]:
    """Время команды-победителя; если 1-го места нет — лучшее время полной команды."""
    for t_team, place in zip(times, places):
        if place == "1" and t_team:
            return t_team
    return min((t for t in times if t), default=None)


def _parse_relay_rows(text: str) -> List[Dict]:
    """
    Эстафета SportOrg. Команда засчитана, если все этапы пробежаны без снятия;
    все её участники получают очки по времени команды на последнем этапе.
    Возвращает строки в формате _parse_rows с уже проставленными очками.
    """
    group_name = ""
    teams: List[Tuple[str, List[Dict]]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("№"):
            continue
        if _is_group_line(stripped):
            group_name = stripped
            continue
        if _TEAM_NUMBER_RE.match(stripped):
            teams.append((group_name, []))
            continue
        if teams:
            leg = _relay_leg(line)
            if leg:
                teams[-1][1].append(leg)

    by_group: Dict[str, List[List[Dict]]] = {}
    for group, legs in teams:
        if legs:
            by_group.setdefault(group, []).append(legs)

    rows: List[Dict] = []
    for group, group_teams in by_group.items():
        # Неполная команда (вышли не все участники) не финишировала,
        # даже если пробежанные этапы без снятий.
        full_legs = max(len(legs) for legs in group_teams)

        def team_time(legs: List[Dict]) -> Optional[int]:
            if len(legs) < full_legs or not all(leg["finished"] for leg in legs):
                return None
            return legs[-1]["team_sec"]

        times = [team_time(legs) for legs in group_teams]
        places = [next((leg["place"] for leg in legs if leg["place"]), "") for legs in group_teams]
        leader = _relay_leader(times, places)
        for legs, t_team, place in zip(group_teams, times, places):
            points = _calc_points(t_team, leader) if t_team and place and leader else 0
            seen = set()
            for leg in legs:
                key = (leg["last_name"].lower(), leg["first_name"].lower())
                if key in seen:
                    continue
                seen.add(key)
                if t_team:
                    place_display = place or "в/к"
                else:
                    place_display = "снят"
                rows.append(
                    {
                        "group_name": group,
                        "place": place_display,
                        "place_out_of": len(group_teams),
                        "last_name": leg["last_name"],
                        "first_name": leg["first_name"],
                        "team": leg["team"],
                        "points": points,
                        "is_relay": True,
                    }
                )
    return rows


def _score_rows(rows: List[Dict]) -> None:
    """Проставляет points и place_out_of по группам (лидер группы = 1000)."""
    groups: Dict[str, List[Dict]] = {}
    for row in rows:
        groups.setdefault(row["group_name"], []).append(row)

    for group_rows in groups.values():
        # Приоритет — сумма очков организатора: она определяет места в многодневке.
        best_points = max(
            (r["total_points"] for r in group_rows if r["total_points"]),
            default=None,
        )
        # Сумма времени сравнима только у тех, кто прошёл все дни:
        # у остальных в скобках стоит меньшее число зачтённых дней.
        max_days = max((r.get("total_days") or 0 for r in group_rows), default=0)

        def _full_distance(row: Dict) -> bool:
            return bool(row["total_sec"]) and (row.get("total_days") or 0) >= max_days

        leader_sec = min(
            (r["total_sec"] for r in group_rows if _full_distance(r)),
            default=None,
        )
        if best_points:
            scored = sum(1 for r in group_rows if (r["total_points"] or 0) > 0)
        else:
            scored = sum(1 for r in group_rows if _full_distance(r))

        for row in group_rows:
            if best_points:
                points = int(round(1000 * (row["total_points"] or 0) / best_points))
            elif leader_sec and _full_distance(row):
                points = _calc_points(row["total_sec"], leader_sec)
            else:
                points = 0
            row["points"] = max(points, 0)
            # У незачётных строк «место из N» не показываем — как в остальных парсерах.
            row["place_out_of"] = (scored or None) if row["points"] else None


def parse_pdf_summary(
    payload: str,
    base_pairs: List[Tuple[str, str]],
    team_name: str = "",
) -> List[Dict]:
    """
    Парсит текст PDF-суммы (содержимое Protocol.raw_html) и фильтрует участников.

    Фильтрация — как в остальных парсерах проекта:
      - team_name='*' — все участники;
      - team_name задан — участники команды ИЛИ известные участники;
      - иначе — только известные участники (base_pairs);
      - если и база пуста — все.
    """
    text = payload or ""
    if text.lstrip().startswith(PDF_SUMMARY_PREFIX):
        text = text.lstrip()[len(PDF_SUMMARY_PREFIX):]

    rows: List[Dict] = []
    for part, day in zip(*_parts_with_days(text)):
        if _is_relay_text(part):
            part_rows = _parse_relay_rows(part)
        else:
            part_rows = _parse_rows(part)
            _score_rows(part_rows)
        for row in part_rows:
            row["day"] = day
        rows.extend(part_rows)

    team_filter = (team_name or "").strip().lower()
    show_all = team_filter == "*"
    known = {
        (str(ln).strip().lower(), str(fn).strip().lower()) for ln, fn in base_pairs
    }

    results: List[Dict] = []
    for row in rows:
        last_name = row["last_name"]
        first_name = row["first_name"]
        key = (last_name.lower(), first_name.lower())

        if show_all:
            pass
        elif team_filter:
            if team_filter not in (row["team"] or "").lower() and key not in known:
                continue
        elif known:
            if key not in known:
                continue

        pair = (last_name, first_name)
        if pair not in base_pairs:
            base_pairs.append(pair)
            known.add(key)

        place = row["place"]
        place_out_of = row["place_out_of"]
        place_part = f"{place} из {place_out_of}" if place_out_of else place
        if row.get("is_relay"):
            place_part += " (эстафета)"
        elif row.get("is_sum", True):
            place_part += " (сумма)"
        results.append(
            {
                "group_name": row["group_name"],
                "place": place,
                "place_out_of": place_out_of,
                "last_name": last_name,
                "first_name": first_name,
                "points": row["points"],
                "raw_line": f"{place_part}, {last_name} {first_name}, очки:{row['points']}",
                "day": row["day"],
            }
        )
    return results


def _part_date(part: str) -> Optional[str]:
    """
    Дата из шапки части (дд.мм.гггг или гггг-мм-дд). Ищется только до строки
    заголовка таблицы — ниже бывают даты рождения участников.
    """
    for line in part.splitlines():
        if line.strip().startswith("№"):
            break
        m_date = _DATE_RE.search(line)
        if m_date:
            day, month, year = m_date.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
        m_iso = _ISO_DATE_RE.search(line)
        if m_iso:
            return m_iso.group(0)
    return None


def _parts_with_days(text: str) -> Tuple[List[str], List[int]]:
    """
    Части протокола и номер дня каждой. День определяется датой в шапке части:
    два файла одного дня (Кубок клубов) — один старт, дни первенства — разные.
    Часть без даты считается отдельным днём.
    """
    parts = text.split(PDF_PART_DELIMITER)
    numbers: Dict[str, int] = {}
    days = []
    for index, part in enumerate(parts):
        key = _part_date(part) or f"part-{index}"
        days.append(numbers.setdefault(key, len(numbers) + 1))
    return parts, days


def fetch_pdf_summary(url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Загружает PDF по ссылке и готовит содержимое для Protocol.

    Возвращает (payload, title, event_date, error), где payload — текст PDF
    с префиксом PDF_SUMMARY_PREFIX, event_date — строка YYYY-MM-DD.
    """
    pdf_bytes, err = fetch_pdf_bytes(url)
    if err or not pdf_bytes:
        return None, None, None, f"Не удалось скачать PDF: {err or 'пустой ответ'}"

    text, err = extract_pdf_text(pdf_bytes)
    if err or not text:
        return None, None, None, err or "PDF без текста."

    if not parse_pdf_summary(text, base_pairs=[], team_name="*"):
        return None, None, None, (
            "В PDF не найдена таблица с колонкой «Сумма» или «Результат» — "
            "этот вид протокола пока не поддерживается."
        )

    title, event_date = extract_summary_meta(text)
    return f"{PDF_SUMMARY_PREFIX}{text}", title, event_date, None
