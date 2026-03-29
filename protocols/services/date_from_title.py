# -*- coding: utf-8 -*-
"""
Извлечение даты соревнований из названия протокола (WinOrient и др.).
"""
import re
from datetime import date
from typing import Optional

# Месяцы по-русски (именительный и родительный)
RU_MONTHS = {
    'января': 1, 'январь': 1,
    'февраля': 2, 'февраль': 2,
    'марта': 3, 'март': 3,
    'апреля': 4, 'апрель': 4,
    'мая': 5, 'май': 5,
    'июня': 6, 'июнь': 6,
    'июля': 7, 'июль': 7,
    'августа': 8, 'август': 8,
    'сентября': 9, 'сентябрь': 9,
    'октября': 10, 'октябрь': 10,
    'ноября': 11, 'ноябрь': 11,
    'декабря': 12, 'декабрь': 12,
}


def parse_date_from_title(title: str) -> Optional[date]:
    """
    Пытается извлечь дату из названия протокола.
    Поддерживаемые форматы:
    - 15.03.2024, 15.03.24
    - 2024-03-15
    - 15 марта 2024, 15 марта 24
    - 15.03 (текущий год)
    Возвращает date или None.
    """
    if not title or not isinstance(title, str):
        return None
    text = title.strip()
    if not text:
        return None

    # ДД.ММ.ГГГГ или ДД.ММ.ГГ
    m = re.search(r'\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b', text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 50 else 1900
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return date(y, mo, d)
            except ValueError:
                pass

    # ГГГГ-ММ-ДД
    m = re.search(r'\b(20\d{2})-(\d{1,2})-(\d{1,2})\b', text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return date(y, mo, d)
            except ValueError:
                pass

    # ДД месяц ГГГГ / ДД месяц ГГ
    for month_name, month_num in RU_MONTHS.items():
        m = re.search(
            r'\b(\d{1,2})\s+' + re.escape(month_name) + r'\s+(\d{2,4})\b',
            text,
            re.IGNORECASE,
        )
        if m:
            d, y = int(m.group(1)), int(m.group(2))
            if y < 100:
                y += 2000 if y < 50 else 1900
            if 1 <= d <= 31:
                try:
                    return date(y, month_num, d)
                except ValueError:
                    pass

    # ДД.ММ без года — текущий год
    m = re.search(r'\b(\d{1,2})\.(\d{1,2})\b', text)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return date(date.today().year, mo, d)
            except ValueError:
                pass

    return None
