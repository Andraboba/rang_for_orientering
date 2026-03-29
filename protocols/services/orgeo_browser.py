# -*- coding: utf-8 -*-
"""
Загрузка страниц Orgeo — обходим все группы соревнования.

Playwright НЕ нужен!
Сайт orgeo.ru использует HTML framesets. Данные в каждой группе отдаются
обычным HTTP GET-запросом — никакого JS-рендеринга нет.

Поддерживаемые форматы входного URL:
  1. https://orgeo.ru/live/#/winter26          (live-ссылка с alias)
  2. https://orgeo.ru/live/#/48979             (live-ссылка с числовым ID)
  3. https://orgeo.ru/online/48979             (прямая ссылка frameset)
  4. https://orgeo.ru/online/finish/48979?...  (прямая ссылка на группу)

Структура URL:
  Главная:  https://orgeo.ru/online/{event_id}
            → <frameset> с двумя фреймами (header + main)
  Header:   https://orgeo.ru/online/top?id={event_id}&s={start_id}
            → содержит ссылки на все группы
  Группа:   https://orgeo.ru/online/finish/{event_id}?s={start_id}&tz=3&d={group_code}
            → полная HTML-таблица результатов
  JSON API: https://orgeo.ru/online/top/?id={alias_or_id}&api=json
            → {event_id, alias, sub_id, dists, ...}

Алгоритм:
  1. resolve_orgeo_url() — нормализуем любой URL в /online/{event_id}
  2. Загружаем frameset → извлекаем URL header-фрейма
  3. Загружаем header → парсим все ссылки на группы
  4. Загружаем каждую группу обычным requests.get()
  5. Собираем все HTML в список
"""

import re
import logging
from typing import Tuple, Optional, List
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

# Разделитель для хранения нескольких HTML в одном поле (все группы одного старта).
ORGEO_TABLES_DELIMITER = "<!-- ORGEO_TABLE -->"

# Таймаут на один HTTP-запрос (секунды)
_REQUEST_TIMEOUT = 15


def _make_session() -> requests.Session:
    """Создать сессию с заголовками."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    return session


def _get(url: str, session: Optional[requests.Session] = None) -> str:
    """GET-запрос с UTF-8. Возвращает HTML-строку или кидает исключение."""
    s = session or requests
    resp = s.get(url, timeout=_REQUEST_TIMEOUT)
    resp.encoding = "utf-8"
    resp.raise_for_status()
    return resp.text


def _extract_header_url(frameset_html: str, base_url: str) -> Optional[str]:
    """
    Из HTML frameset извлекаем src фрейма 'header'.
    <frame name="header" ... src="/online/top?id=49864&s=2" .../>
    """
    # Ищем с одинарными или двойными кавычками
    m = re.search(
        r'<frame[^>]+name=["\']header["\'][^>]+src=["\']([^"\']+)["\']',
        frameset_html, re.IGNORECASE,
    )
    if not m:
        # Может быть в обратном порядке: src перед name
        m = re.search(
            r'<frame[^>]+src=["\']([^"\']+)["\'][^>]+name=["\']header["\']',
            frameset_html, re.IGNORECASE,
        )
    if m:
        return urljoin(base_url, m.group(1))
    return None


def _extract_group_urls(header_html: str, base_url: str = "https://orgeo.ru") -> List[str]:
    """
    Из HTML header-фрейма извлекаем все ссылки на группы:
    <a href='/online/finish/49864?s=2&tz=3&d=Ж10' target='main'>Ж10</a>
    """
    # Одинарные ИЛИ двойные кавычки, параметр d= в URL
    hrefs = re.findall(
        r"""href=['"](/online/finish/\d+\?[^'"]*?d=[^'"]+)['"]""",
        header_html,
    )
    urls = []
    seen = set()
    for h in hrefs:
        full = urljoin(base_url, h)
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def _fetch_orgeo_meta(event_uid: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Получает метаданные соревнования из JSON API Orgeo.
    event_uid может быть alias (строка) или числовой ID.
    Возвращает (event_id, event_name, event_date, error).
    event_date — строка вида "2026-03-01" (только дата, без времени).
    """
    try:
        session = _make_session()
        resp = session.get(
            "https://orgeo.ru/online/top/",
            params={"id": event_uid, "api": "json"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        event_id = data.get("event_id")
        # Название и дата лежат в subs[0]
        event_name = None
        event_date = None
        subs = data.get("subs") or []
        if subs and isinstance(subs, list):
            event_name = subs[0].get("event_name")
            # datetime приходит как "2026-03-01 11:30:00", берём только дату
            dt_str = subs[0].get("datetime") or ""
            if dt_str:
                event_date = dt_str.split()[0]  # "2026-03-01"
        return str(event_id) if event_id else None, event_name, event_date, None
    except Exception as e:
        return None, None, None, str(e)


def resolve_orgeo_url(url: str) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """
    Нормализует любой URL Orgeo в формат https://orgeo.ru/online/{event_id}
    и возвращает название и дату соревнования.

    Поддерживаемые форматы:
      - https://orgeo.ru/live/#/winter26       → резолвим alias через JSON API
      - https://orgeo.ru/live/#/48979          → https://orgeo.ru/online/48979
      - https://orgeo.ru/live/#/48979/2        → https://orgeo.ru/online/48979
      - https://orgeo.ru/online/48979          → без изменений
      - https://orgeo.ru/online/finish/48979?… → https://orgeo.ru/online/48979

    Возвращает (normalized_url, event_title, event_date, error).
    event_date — строка "YYYY-MM-DD" или None.
    """
    url = url.strip()
    base_url = "https://orgeo.ru"

    # ── Извлекаем event_uid из URL ──
    event_uid = None

    # Формат 1: live-ссылка https://orgeo.ru/live/#/winter26  или  /#/48979
    m = re.search(r'/live/.*#/([^/]+)', url)
    if m:
        event_uid = m.group(1)

    # Формат 2: /online/finish/{id}?...
    if not event_uid:
        m = re.search(r'/online/finish/(\d+)', url)
        if m:
            event_uid = m.group(1)

    # Формат 3: /online/{id}
    if not event_uid:
        m = re.search(r'/online/(\d+)', url)
        if m:
            event_uid = m.group(1)

    if not event_uid:
        return "", None, None, f"Не удалось распознать формат URL Orgeo: {url}"

    # ── Дёргаем JSON API — получаем event_id + название + дату ──
    event_id, event_name, event_date, api_err = _fetch_orgeo_meta(event_uid)
    if api_err:
        # Если uid уже числовой — можно попробовать без API
        if event_uid.isdigit():
            logger.warning("Orgeo: JSON API недоступен (%s), используем ID напрямую", api_err)
            return f"{base_url}/online/{event_uid}", None, None, None
        return "", None, None, f"Ошибка резолва '{event_uid}': {api_err}"

    if not event_id:
        return "", None, None, f"JSON API не вернул event_id для '{event_uid}'"

    logger.info("Orgeo: '%s' → event_id=%s, name='%s', date='%s'", event_uid, event_id, event_name, event_date)
    return f"{base_url}/online/{event_id}", event_name, event_date, None


def _extract_main_frame_url(frameset_html: str, base_url: str) -> Optional[str]:
    """
    Резервный вариант: если header-фрейм не найден, извлекаем URL main-фрейма.
    <frame name="main" src="/online/finish/49864?s=2&d=Ж10&tz=3" .../>
    """
    m = re.search(
        r'<frame[^>]+name=["\']main["\'][^>]+src=["\']([^"\']+)["\']',
        frameset_html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<frame[^>]+src=["\']([^"\']+)["\'][^>]+name=["\']main["\']',
            frameset_html, re.IGNORECASE,
        )
    if m:
        return urljoin(base_url, m.group(1))
    return None


def fetch_orgeo_tables(url: str, timeout_ms: int = 30000) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Загружает ВСЕ группы соревнования по url.

    Принимает ЛЮБОЙ формат URL Orgeo:
      - https://orgeo.ru/live/#/winter26
      - https://orgeo.ru/live/#/48979
      - https://orgeo.ru/online/48979

    Возвращает (list[html], None) при успехе или (None, error_msg) при ошибке.
    Каждый элемент списка — полный HTML одной группы с таблицей результатов.
    """
    try:
        # ── Шаг 0: Нормализуем URL ──
        resolved_url, _title, _date, err = resolve_orgeo_url(url)
        if err:
            return None, err
        url = resolved_url
        logger.info("Orgeo: нормализованный URL: %s", url)

        session = _make_session()
        base_url = "https://orgeo.ru"

        # ── Шаг 1: Загружаем главную страницу (frameset) ──
        frameset_html = _get(url, session)

        # ── Шаг 2: Находим URL header-фрейма ──
        header_url = _extract_header_url(frameset_html, base_url)
        if not header_url:
            # Может, нам сразу дали URL группы (не frameset)?
            if "Фамилия" in frameset_html or "<table" in frameset_html:
                return [frameset_html], None
            return None, f"Не удалось найти header-фрейм в {url}"

        # ── Шаг 3: Загружаем header, извлекаем ссылки на группы ──
        header_html = _get(header_url, session)
        group_urls = _extract_group_urls(header_html, base_url)

        if not group_urls:
            # Попробуем хотя бы main-фрейм
            main_url = _extract_main_frame_url(frameset_html, base_url)
            if main_url:
                html = _get(main_url, session)
                return [html], None
            return None, "Не найдены ссылки на группы в header-фрейме"

        logger.info("Orgeo: найдено %d групп, загружаем...", len(group_urls))

        # ── Шаг 4: Загружаем каждую группу ──
        collected: List[str] = []
        errors: List[str] = []

        for g_url in group_urls:
            try:
                html = _get(g_url, session)
                if "Фамилия" in html or "<table" in html:
                    collected.append(html)
                else:
                    logger.warning("Orgeo: нет таблицы в ответе %s", g_url)
            except Exception as e:
                err_msg = f"{g_url}: {e}"
                logger.warning("Orgeo: ошибка загрузки группы: %s", err_msg)
                errors.append(err_msg)

        if collected:
            return collected, None
        return None, f"Не получено ни одной таблицы. Ошибки: {'; '.join(errors)}"

    except Exception as e:
        return None, str(e)


def fetch_orgeo_with_browser(url: str, timeout_ms: int = 25000) -> Tuple[Optional[str], Optional[str]]:
    """
    Обратная совместимость: загружает одну страницу Orgeo.
    Принимает любой формат URL (live, online, finish).
    Если URL — frameset, загружает main-фрейм.
    Если URL — уже страница группы, возвращает как есть.

    Возвращает (html, None) при успехе или (None, error_msg) при ошибке.
    """
    try:
        # Нормализуем URL
        resolved_url, _title, _date, err = resolve_orgeo_url(url)
        if err:
            return None, err
        url = resolved_url

        session = _make_session()

        html = _get(url, session)

        # Если в HTML уже есть таблица — это страница группы, возвращаем
        if "Фамилия" in html or "П/п" in html:
            return html, None

        # Иначе это frameset — ищем main-фрейм
        main_url = _extract_main_frame_url(html, "https://orgeo.ru")
        if main_url:
            main_html = _get(main_url, session)
            return main_html, None

        return html, None

    except Exception as e:
        return None, str(e)