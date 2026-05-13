# -*- coding: utf-8 -*-
"""
Парсер сканированных PDF-протоколов с двухэтапными результатами (Пролог + Финиш).

Ожидаемый формат таблицы:
  п/п | Фамилия, Имя, Отчество | Дата рожд. | Разр | Команда | Номер | Пролог(место) | Финиш(место) | Сумма | Место

Дисквалификация: в колонке времени стоит "п.л. X.Y.Z", в колонке Место — "х".
Возвращает два набора результатов: для Пролога и для Финиша.

Требования:
  pip install pymupdf pytesseract pillow
  Tesseract OCR с русским языком: winget install UB-Mannheim.TesseractOCR
"""

import os
import re
from typing import Dict, List, Optional

import fitz  # PyMuPDF

# Стандартный путь Tesseract на Windows (UB-Mannheim installer).
# Если tesseract.exe не в PATH, pytesseract найдёт его здесь.
_TESSERACT_WIN_PATH = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Время HH:MM:SS за которым сразу идёт (место)
_TIME_PLACE_RE = re.compile(r'(\d{1,2}:\d{2}:\d{2})\s*\((\d+)\)')
# Простое время HH:MM:SS без места — для поиска якоря при нечитаемой дате
_TIME_RE = re.compile(r'\d{1,2}:\d{2}:\d{2}')

# Дата рождения DD.MM.YYYY — якорь строки данных
_DATE_RE = re.compile(r'\b(\d{2}\.\d{2}\.\d{4})\b')

# Заголовок группы: ДЕВУШКИ/ЮНОШИ/... (до/от N лет)
# Принимает варианты OCR: круглые скобки, квадратные, или вообще без скобок.
_GROUP_RE = re.compile(
    r'((?:ДЕВУШКИ|ДЕВОЧКИ|ЮНОШИ|ЮНИОРКИ|ЮНИОРЫ|МУЖЧИНЫ|ЖЕНЩИНЫ|МАЛЬЧИКИ)'
    r'(?:'
    r'\s*[\(\[]\s*[^\)\]\n]{3,30}[\)\]]'           # (до 15 лет) или [до 15 лет]
    r'|\s+(?:до|от)\s+\d{1,2}\s+(?:лет|год[аов]?)' # без скобок: до 15 лет
    r')?)',
    re.IGNORECASE,
)

# Ключевые слова заголовка соревнования
_TITLE_KW_RE = re.compile(r'ПЕРВЕНСТВО|ЧЕМПИОНАТ|КУБОК|СОРЕВНОВАНИЕ', re.IGNORECASE)

# Типичные OCR-замены для содержимого скобок в названии группы:
# «до» Tesseract может прочитать как qo/go/ao/до; «лет» → net/ner/нет; «года» → roa/roga
_GROUP_BRACKET_FIXES = [
    (re.compile(r'\b(?:qo|go|ao|ао)\b', re.IGNORECASE), 'до'),
    # «лет» — буква «л» часто путается с «п» → «пет», и с латинскими net/ner
    (re.compile(r'\b(?:net|ner|нет|нef|nef|пет)\b', re.IGNORECASE), 'лет'),
    (re.compile(r'\b(?:roa|roga|roga|года)\b', re.IGNORECASE), 'года'),
    (re.compile(r'\b(?:от)\b', re.IGNORECASE), 'от'),
]

# Таблица замены латинских гомоглифов на кириллицу для имён/фамилий.
# Tesseract с lang='rus' всё равно иногда выдаёт латинские буквы там, где они
# визуально неотличимы от кириллических (C/С, a/а, e/е, o/о и т.д.).
_HOMOGLYPH_TABLE = str.maketrans({
    # Прописные
    'A': 'А', 'B': 'В', 'C': 'С', 'E': 'Е', 'H': 'Н',
    'I': 'И', 'K': 'К', 'M': 'М', 'O': 'О', 'P': 'Р',
    'T': 'Т', 'X': 'Х', 'Y': 'У',
    # Строчные
    'a': 'а', 'c': 'с', 'e': 'е', 'o': 'о',
    'p': 'р', 'x': 'х', 'y': 'у',
    # 'i' → 'й': спорная замена (может сломать «и» в середине слова, напр. «Михаил»).
    # Включена, т.к. в русских именах «й» у конца слова встречается чаще, чем «и»
    # в позиции, где OCR выдаёт латинскую «i».
    'i': 'й',
})


def _fix_homoglyphs(name: str) -> str:
    """Заменяет латинские буквы-гомоглифы на кириллические в имени/фамилии."""
    return name.translate(_HOMOGLYPH_TABLE)


# OCR иногда вставляет пробел внутри слова — склеиваем части ключевых слов групп.
_KEYWORD_SPLITS = [
    (re.compile(r'(?i)девуш\s+ки'), 'ДЕВУШКИ'),
    (re.compile(r'(?i)девоч\s+ки'), 'ДЕВОЧКИ'),
    (re.compile(r'(?i)юнош\s+и'), 'ЮНОШИ'),
    (re.compile(r'(?i)юниор\s+ки'), 'ЮНИОРКИ'),
    (re.compile(r'(?i)юниор\s+ы'), 'ЮНИОРЫ'),
    (re.compile(r'(?i)мужчи\s+ны'), 'МУЖЧИНЫ'),
    (re.compile(r'(?i)женщи\s+ны'), 'ЖЕНЩИНЫ'),
    (re.compile(r'(?i)мальчи\s+ки'), 'МАЛЬЧИКИ'),
]


def _repair_keywords(line: str) -> str:
    """Склеивает ключевые слова групп, разорванные OCR-пробелом."""
    for pattern, replacement in _KEYWORD_SPLITS:
        line = pattern.sub(replacement, line)
    return line


def _fix_group_name(raw: str) -> str:
    """Исправляет типичные OCR-ошибки в названии группы."""
    m = re.match(r'([^(]+)\(([^)]+)\)', raw)
    if not m:
        return raw
    prefix = m.group(1).strip().capitalize()
    # Сначала приводим всё содержимое к нижнему регистру (убирает «дО» → «до»)
    inside = m.group(2).strip().lower()
    for pattern, replacement in _GROUP_BRACKET_FIXES:
        inside = pattern.sub(replacement, inside)
    return f'{prefix} ({inside})'


def _seconds(s: str) -> Optional[int]:
    if not s:
        return None
    parts = re.split(r'[:\s]+', s.strip())
    try:
        if len(parts) >= 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2][:2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None
    return None


def _calc_points(t_actual: int, t_leader: int) -> int:
    if t_leader <= 0:
        return 0
    return max(1, int((2 - t_actual / t_leader) * 1000))


def _ocr_page(page: fitz.Page) -> str:
    """OCR одной страницы; возвращает строку текста."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            'Для парсинга PDF установите зависимости:\n'
            '  pip install pytesseract pillow\n'
            'И Tesseract OCR с русским языком:\n'
            '  winget install UB-Mannheim.TesseractOCR\n'
            '(при установке убедитесь, что выбран русский языковой пакет)'
        ) from exc

    # Указываем путь явно, если tesseract не в PATH (типичная ситуация на Windows)
    if os.name == 'nt' and os.path.isfile(_TESSERACT_WIN_PATH):
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_WIN_PATH

    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
    img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    return pytesseract.image_to_string(img, lang='rus', config='--psm 6')


def _parse_row(line: str) -> Optional[Dict]:
    """
    Разбирает строку данных из OCR-текста.
    Возвращает dict или None если строка не является строкой участника.
    """
    line = line.strip()

    # Строка данных начинается с порядкового номера
    m_num = re.match(r'^(\d{1,3})\s+', line)
    if not m_num:
        return None

    # Якорь 1: дата рождения DD.MM.YYYY (надёжный)
    m_date = _DATE_RE.search(line, m_num.end())
    if m_date:
        name_raw = line[m_num.end():m_date.start()].strip()
        rest = line[m_date.end():]
    else:
        # Якорь 2: первое время HH:MM:SS — если дата нечитаема (OCR-мусор)
        m_time = _TIME_RE.search(line, m_num.end())
        if not m_time:
            return None
        name_raw = line[m_num.end():m_time.start()].strip()
        rest = line[m_time.start():]

    # ФИО — только ведущие кириллические слова (≥2 букв); OCR-мусор, разряд,
    # команда — обрезаются при первом нечитаемом/не-кириллическом слове.
    name_parts = []
    for word in name_raw.split():
        if re.match(r'^[А-ЯЁа-яё]{2,}$', word):
            name_parts.append(word)
        else:
            break

    if len(name_parts) < 2:
        return None
    last_name = _fix_homoglyphs(name_parts[0])
    first_name = _fix_homoglyphs(name_parts[1])  # отчество не используем

    # Времена с местами в скобках: Пролог(место) и Финиш(место)
    staged = _TIME_PLACE_RE.findall(rest)  # [(time_str, place_str), ...]

    prologue: Dict = {'time_str': None, 'place': None, 'dsq': True}
    finish: Dict = {'time_str': None, 'place': None, 'dsq': True}

    if len(staged) >= 1:
        prologue = {'time_str': staged[0][0], 'place': int(staged[0][1]), 'dsq': False}
    if len(staged) >= 2:
        finish = {'time_str': staged[1][0], 'place': int(staged[1][1]), 'dsq': False}

    return {
        'last_name': last_name,
        'first_name': first_name,
        'prologue': prologue,
        'finish': finish,
    }


def _build_stage_results(
    participants: List[Dict],
    stage_key: str,
    group_name: str,
) -> List[Dict]:
    """
    Строит список ProtocolResult-совместимых dict для одного этапа.
    stage_key: 'prologue' или 'finish'.
    """
    total = len(participants)

    # Время лидера — минимум среди финишировавших
    valid_times = [
        _seconds(p[stage_key]['time_str'])
        for p in participants
        if not p[stage_key]['dsq'] and p[stage_key]['time_str']
    ]
    valid_times_clean = [t for t in valid_times if t is not None]
    t_leader = min(valid_times_clean) if valid_times_clean else None

    # Количество участников с результатом (для place_out_of)
    finishers = len(valid_times_clean)

    results = []
    for p in participants:
        stage = p[stage_key]
        if stage['dsq']:
            place_str = 'снят'
            points = 0
            place_out_of = None
        else:
            place_str = str(stage['place']) if stage['place'] else '-'
            t_actual = _seconds(stage['time_str'])
            if t_actual is not None and t_leader is not None:
                points = _calc_points(t_actual, t_leader)
            else:
                points = 0
            place_out_of = finishers

        raw_line = (
            f"{place_str} из {place_out_of or total} , "
            f"{p['last_name']} {p['first_name']}, очки:{points}"
        )
        results.append({
            'group_name': group_name,
            'place': place_str,
            'place_out_of': place_out_of,
            'last_name': p['last_name'],
            'first_name': p['first_name'],
            'points': points,
            'raw_line': raw_line,
        })
    return results


class PDFParseResult:
    def __init__(self):
        self.title: str = ''
        self.event_date: str = ''       # строка DD.MM.YYYY или YYYY-MM-DD
        self.prologue_results: List[Dict] = []
        self.finish_results: List[Dict] = []
        self.groups_found: List[str] = []
        self.page_errors: List[str] = []  # страницы, которые не удалось обработать
        self.raw_ocr_text: str = ''       # сырой OCR-текст всех страниц для отладки


def parse_pdf_protocol(pdf_bytes: bytes) -> PDFParseResult:
    """
    Основная функция: принимает байты PDF-файла.
    Возвращает PDFParseResult с результатами Пролога и Финиша.
    Требует установленного Tesseract OCR с русским языком (rus.traineddata).
    """
    result = PDFParseResult()

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')

    current_group: str = ''
    # group_name → список участников
    groups: Dict[str, List[Dict]] = {}

    ocr_pages = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            text = _ocr_page(page)
        except Exception as exc:
            result.page_errors.append(f'Страница {page_idx + 1}: {exc}')
            ocr_pages.append(f'=== Страница {page_idx + 1} [ОШИБКА OCR: {exc}] ===\n')
            continue
        ocr_pages.append(f'=== Страница {page_idx + 1} ===\n{text}')

        for line in text.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Нормализуем строку для определения заголовков:
            # 1) склеиваем ключевые слова, разорванные OCR пробелом («ДЕВУШ КИ» → «ДЕВУШКИ»)
            # 2) заменяем латинские гомоглифы на кириллицу
            line_norm = _fix_homoglyphs(_repair_keywords(line_stripped))

            # Извлекаем заголовок соревнования (первая подходящая строка)
            if not result.title and _TITLE_KW_RE.search(line_norm) and len(line_norm) < 150:
                result.title = line_norm
                m_date = re.search(r'(\d{2}\.\d{2}\.\d{4})', line_norm)
                if m_date:
                    result.event_date = m_date.group(1)
                continue

            # Дата соревнования из строки-шапки (если ещё не нашли)
            if not result.event_date:
                m_date = re.search(r'(\d{2}\.\d{2}\.\d{4})', line_norm)
                if m_date and not _DATE_RE.fullmatch(line_norm):
                    result.event_date = m_date.group(1)

            # Определяем, не является ли строка заголовком группы.
            # Строки с датой рождения DD.MM.YYYY — это строки участников, не заголовки.
            m_group = _GROUP_RE.search(line_norm)
            if m_group and len(line_norm) < 120 and not _DATE_RE.search(line_norm):
                raw_group = m_group.group(1).strip()
                current_group = _fix_group_name(raw_group)
                if current_group not in groups:
                    groups[current_group] = []
                    result.groups_found.append(current_group)
                continue

            # Строка с данными участника — парсим оригинальную строку (с цифрами/датами)
            if current_group:
                row = _parse_row(line_stripped)
                if row:
                    groups[current_group].append(row)

    result.raw_ocr_text = '\n'.join(ocr_pages)

    # Строим итоговые результаты для каждой группы
    for group_name, participants in groups.items():
        if not participants:
            continue
        result.prologue_results.extend(
            _build_stage_results(participants, 'prologue', group_name)
        )
        result.finish_results.extend(
            _build_stage_results(participants, 'finish', group_name)
        )

    return result
