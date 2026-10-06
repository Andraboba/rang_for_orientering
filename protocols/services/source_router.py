# -*- coding: utf-8 -*-
"""
Единая точка входа для загрузки протокола по ссылке.

Вид источника определяется автоматически, пользователю достаточно вставить URL:

  orgeo.ru/…            → Orgeo (live/#/id, /online/id, /event/id, /online/finish/id)
  sportident.online/…   → SportIdent Online
  *.pdf                 → PDF с суммой многодневки (текстовый слой)
  HTML с raceCompressed → экспорт SportOrg (o-club.ru и т.п.)
  остальной HTML        → WinOrient

Здесь же — обратный диспетчер: по виду протокола выбирается нужный парсер,
поэтому обработка «по команде/по базе участников» одинакова для всех источников.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .orgeo_browser import (
    ORGEO_TABLES_DELIMITER,
    fetch_orgeo_tables,
    fetch_orgeo_with_browser,
    orgeo_chunk_days,
    resolve_orgeo_url,
)
from .orgeo_parser import fetch_orgeo_document, parse_orgeo
from .parser import extract_title, fetch_url, parse_and_process
from .pdf_summary_parser import (
    PDF_SUMMARY_PREFIX,
    fetch_pdf_summary,
    is_pdf_summary_payload,
    looks_like_pdf_url,
    parse_pdf_summary,
)
from .sportident_parser import (
    SPORTIDENT_TABLES_DELIMITER,
    fetch_sportident_tables,
    parse_sportident,
)
from .sportorg_parser import (
    SPORTORG_DATA_PREFIX,
    fetch_sportorg,
    is_sportorg_html,
    is_sportorg_payload,
    parse_sportorg,
)

KIND_WINORIENT = "winorient"
KIND_ORGEO = "orgeo"
KIND_SPORTIDENT = "sportident"
KIND_SPORTORG = "sportorg"
KIND_PDF_SUMMARY = "pdf_summary"
KIND_PDF_OCR = "pdf_ocr"

KIND_CHOICES = (
    (KIND_WINORIENT, "WinOrient"),
    (KIND_ORGEO, "Orgeo"),
    (KIND_SPORTIDENT, "SportIdent Online"),
    (KIND_SPORTORG, "SportOrg (HTML-экспорт)"),
    (KIND_PDF_SUMMARY, "PDF: сумма многодневки"),
    (KIND_PDF_OCR, "PDF: скан (OCR)"),
)

# Разделитель чанков в raw_html для источников, где групп несколько.
_DELIMITERS = {
    KIND_ORGEO: ORGEO_TABLES_DELIMITER,
    KIND_SPORTIDENT: SPORTIDENT_TABLES_DELIMITER,
}


@dataclass
class LoadedSource:
    """Результат загрузки протокола по ссылке."""

    kind: str = KIND_WINORIENT
    url: str = ""
    payload: str = ""
    title: Optional[str] = None
    event_date: Optional[str] = None  # YYYY-MM-DD
    encoding_utf8: bool = False
    format_sfr: bool = False
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


def detect_kind(url: str) -> Optional[str]:
    """Вид источника по URL. None — определяется по содержимому страницы."""
    lowered = (url or "").lower()
    if "orgeo.ru" in lowered:
        return KIND_ORGEO
    if "sportident.online" in lowered:
        return KIND_SPORTIDENT
    if looks_like_pdf_url(url):
        return KIND_PDF_SUMMARY
    return None


def detect_kind_by_payload(raw: str, source_url: str = "") -> str:
    """
    Вид протокола по сохранённому содержимому — для записей, загруженных до
    появления поля Protocol.source_kind.
    """
    if is_sportorg_payload(raw):
        return KIND_SPORTORG
    if is_pdf_summary_payload(raw):
        return KIND_PDF_SUMMARY
    lowered = (source_url or "").lower()
    if "orgeo.ru" in lowered:
        return KIND_ORGEO
    if "sportident.online" in lowered:
        return KIND_SPORTIDENT
    if lowered.startswith("pdf_upload:"):
        return KIND_PDF_OCR
    return KIND_WINORIENT


def _load_orgeo(url: str) -> LoadedSource:
    loaded = LoadedSource(kind=KIND_ORGEO, url=url, encoding_utf8=True)
    app_url, title, event_date, days, err = resolve_orgeo_url(url)
    if err:
        loaded.error = f"Orgeo: {err}"
        return loaded

    if not event_date and days:
        for day in days:
            date_value = (day or {}).get("date") or ""
            if date_value:
                event_date = date_value
                break

    loaded.url = app_url
    loaded.title = title
    loaded.event_date = event_date

    tables, err = fetch_orgeo_tables(app_url)
    if tables:
        loaded.payload = ORGEO_TABLES_DELIMITER.join(tables)
        return loaded

    if err:
        loaded.warnings.append(f"Orgeo (сеть): {err}. Пробуем загрузку страницы.")
    text, err = fetch_orgeo_with_browser(app_url)
    if err or not text:
        text, err = fetch_orgeo_document(app_url)
    if err or not text:
        loaded.error = f"Orgeo: {err or 'пустой ответ'}"
        return loaded

    loaded.payload = text
    if "Фамилия" not in text and "П/п" not in text and "<table" not in text:
        loaded.warnings.append(
            "Страница Orgeo загружена без таблицы результатов. "
            "Установите Playwright в то же окружение, откуда запускаете Django: "
            "pip install playwright, затем: playwright install chromium"
        )
    return loaded


def _load_sportident(url: str) -> LoadedSource:
    loaded = LoadedSource(kind=KIND_SPORTIDENT, url=url, encoding_utf8=True)
    tables, title, err = fetch_sportident_tables(url)
    if err:
        loaded.error = f"SportIdent: {err}"
        return loaded
    loaded.payload = SPORTIDENT_TABLES_DELIMITER.join(tables or [])
    loaded.title = title
    return loaded


def _load_sportorg(url: str) -> LoadedSource:
    loaded = LoadedSource(kind=KIND_SPORTORG, url=url, encoding_utf8=True)
    payload, title, event_date, err = fetch_sportorg(url)
    if err:
        loaded.error = f"SportOrg: {err}"
        return loaded
    loaded.payload = payload or ""
    loaded.title = title
    loaded.event_date = event_date
    return loaded


def _load_pdf_summary(url: str) -> LoadedSource:
    loaded = LoadedSource(kind=KIND_PDF_SUMMARY, url=url, encoding_utf8=True)
    payload, title, event_date, err = fetch_pdf_summary(url)
    if err:
        loaded.error = f"PDF: {err}"
        return loaded
    loaded.payload = payload or ""
    loaded.title = title
    loaded.event_date = event_date
    return loaded


def _load_html(url: str, encoding_utf8: bool, format_sfr: bool) -> LoadedSource:
    """HTML: SportOrg определяется по содержимому, иначе WinOrient."""
    loaded = LoadedSource(
        kind=KIND_WINORIENT,
        url=url,
        encoding_utf8=encoding_utf8,
        format_sfr=format_sfr,
    )
    text, err = fetch_url(url, encoding_utf8=encoding_utf8)
    if err and "кодировк" in err.lower():
        # Кодировку не обязан угадывать пользователь: пробуем вторую.
        text, err = fetch_url(url, encoding_utf8=not encoding_utf8)
        if not err:
            loaded.encoding_utf8 = not encoding_utf8
    if err or text is None:
        loaded.error = f"Ошибка загрузки: {err or 'пустой ответ'}"
        return loaded

    if is_sportorg_html(text):
        return _load_sportorg(url)

    loaded.payload = text
    loaded.title = extract_title(text, format_sfr)
    return loaded


def load_from_url(
    url: str,
    encoding_utf8: bool = False,
    format_sfr: bool = False,
) -> LoadedSource:
    """
    Загружает протокол по ссылке любого поддерживаемого источника.
    Флаги encoding_utf8/format_sfr нужны только для WinOrient — для остальных
    источников кодировка и формат известны заранее.
    """
    url = (url or "").strip()
    kind = detect_kind(url)
    if kind == KIND_ORGEO:
        return _load_orgeo(url)
    if kind == KIND_SPORTIDENT:
        return _load_sportident(url)
    if kind == KIND_PDF_SUMMARY:
        return _load_pdf_summary(url)
    return _load_html(url, encoding_utf8, format_sfr)


def split_chunks(kind: str, raw: str) -> List[str]:
    """Делит raw_html на части (у Orgeo и SportIdent — по одной на группу)."""
    delimiter = _DELIMITERS.get(kind)
    if delimiter and delimiter in raw:
        return [chunk for chunk in raw.split(delimiter) if chunk.strip()]
    return [raw] if raw.strip() else []


def parse_payload(
    kind: str,
    raw: str,
    base_pairs: List[Tuple[str, str]],
    team_name: str = "",
    format_sfr: bool = False,
) -> List[Dict]:
    """
    Разбирает сохранённый протокол выбранным парсером.

    Правила фильтрации у всех парсеров одинаковы: '*' — все участники,
    непустая команда — участники команды ИЛИ известные, иначе — только известные.
    """
    if kind == KIND_SPORTORG:
        return parse_sportorg(raw, base_pairs=base_pairs, team_name=team_name)
    if kind == KIND_PDF_SUMMARY:
        return parse_pdf_summary(raw, base_pairs=base_pairs, team_name=team_name)

    results: List[Dict] = []
    chunks = split_chunks(kind, raw)
    # У Orgeo в одном протоколе все дни многодневки; каждый день — отдельный старт.
    days = orgeo_chunk_days(chunks) if kind == KIND_ORGEO else [1] * len(chunks)
    for chunk, day in zip(chunks, days):
        if kind == KIND_ORGEO:
            for row in parse_orgeo(chunk, base_pairs=base_pairs, team_name=team_name):
                row['day'] = day
                results.append(row)
        elif kind == KIND_SPORTIDENT:
            results.extend(
                parse_sportident(chunk, base_pairs=base_pairs, team_name=team_name)
            )
        else:
            results.extend(
                parse_and_process(chunk, format_sfr, team_name, base_pairs)
            )
    return results


def show_all_team_name(kind: str) -> str:
    """
    Фильтр «показать всех». Все парсеры понимают '*'; WinOrient показывает всех
    при пустой команде и пустой базе.
    """
    return "" if kind == KIND_WINORIENT else "*"


__all__ = [
    "KIND_CHOICES",
    "KIND_ORGEO",
    "KIND_PDF_OCR",
    "KIND_PDF_SUMMARY",
    "KIND_SPORTIDENT",
    "KIND_SPORTORG",
    "KIND_WINORIENT",
    "LoadedSource",
    "PDF_SUMMARY_PREFIX",
    "SPORTORG_DATA_PREFIX",
    "detect_kind",
    "detect_kind_by_payload",
    "load_from_url",
    "parse_payload",
    "show_all_team_name",
    "split_chunks",
]
