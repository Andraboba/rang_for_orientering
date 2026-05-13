# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Django-приложение для обработки протоколов соревнований по спортивному ориентированию. Загружает протоколы с WinOrient, Orgeo (`orgeo.ru`) и SportIdent Online (`sportident.online`), парсит результаты, считает очки и экспортирует сезонный зачёт в Excel.

## Commands

```bash
# Запуск сервера разработки
python manage.py runserver

# Миграции
python manage.py migrate
python manage.py makemigrations

# Запуск всех тестов
python manage.py test protocols

# Запуск одного файла тестов
python manage.py test protocols.tests.test_orgeo_parser

# Запуск одного теста
python manage.py test protocols.tests.test_orgeo_parser.OrgeoParserTest.test_parse_orgeo_basic

# Покрытие тестами
coverage run manage.py test protocols && coverage report

# Анализ безопасности
bandit -r protocols/

# Аудит зависимостей
pip-audit
```

## Environment

Требуется файл `.env` в корне проекта:

```
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=True
POSTGRES_DB=django_protocols
POSTGRES_USER=postgres
POSTGRES_PASSWORD=...
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

База данных: PostgreSQL (не SQLite). Для тестов используется та же PostgreSQL (`TestCase` с транзакциями).

## Architecture

### Единственное приложение: `protocols`

Всё приложение находится в `protocols/`. Нет разбиения на несколько Django apps.

### Модели (`protocols/models.py`)

- **`Event`** — соревнование (дата, сезон, коэффициент, `orgeo_event_id`).
- **`ChampionshipLevel`** — уровень первенства с коэффициентом (Первенство Москвы, России и т.д.).
- **`Protocol`** — загруженный протокол. Хранит исходный HTML (`raw_html`) целиком, в т.ч. несколько таблиц групп через разделитель. Имеет `effective_coefficient` (из `championship_level` или `importance`).
- **`KnownParticipant`** — база известных участников (фамилия + имя). Флаг `veteran` исключает из сезонного зачёта.
- **`ProtocolResult`** — одна строка результата. `weighted_points = points × effective_coefficient`.
- **`SeasonRanking`** / **`SeasonRankingEntry`** — рейтинг сезона с периодом `date_from`/`date_to`.

### Сервисы (`protocols/services/`)

| Файл | Назначение |
|---|---|
| `parser.py` | Парсинг WinOrient: два формата — `sfr=0` (pre/h2) и `sfr=1` (HTML-таблица). Формула очков: `(2 - t_actual/t_leader) × 1000`. |
| `orgeo_parser.py` | Парсинг одной HTML-страницы группы Orgeo. Фильтрует по `team_name` и/или `base_pairs` (известные участники). |
| `orgeo_browser.py` | Загрузка ВСЕХ групп/дней с Orgeo через HTTP (без Playwright). Поддерживает любые форматы URL Orgeo (live/#/alias, /online/id и т.д.). Главная функция: `fetch_orgeo_tables()`. Разделитель: `ORGEO_TABLES_DELIMITER`. |
| `sportident_parser.py` | Загрузка и парсинг SportIdent Online. `fetch_sportident_tables()` → список HTML-чанков, `parse_sportident()` — парсинг одного чанка. |
| `season_export.py` | Экспорт сезонного зачёта в Excel (openpyxl). Два листа: «Места» (по 3 колонки на старт) и «Баллы». Дата протокола берётся из `event.date` или `protocol.event_date`. |
| `title_cleaner.py` | Удаляет служебные фразы («ПРОТОКОЛ РЕЗУЛЬТАТОВ», «Федерация...») из названий. |
| `date_from_title.py` | Извлекает дату из строкового названия протокола. |

### Ключевые соглашения

**Хранение нескольких HTML в одном поле.** Orgeo и SportIdent хранят все группы в `Protocol.raw_html`, разделённые константами `ORGEO_TABLES_DELIMITER` / `SPORTIDENT_TABLES_DELIMITER`. При парсинге поле сначала сплитится по разделителю, затем каждый чанк обрабатывается отдельно.

**Фильтрация участников.** Парсеры принимают `base_pairs: list[tuple[str, str]]` (фамилия, имя) и `team_name: str`. Логика: если `team_name` задан — берём участников команды ИЛИ известных; если только `base_pairs` — только известных; если оба пусты — всех. Сравнение без учёта регистра.

**Очки.** Единая формула во всех парсерах: `(2 - t_actual / t_leader) × 1000`, минимум 1. Статусы (DNS, DNF, DSQ, превышено КВ) → 0 очков.

**Защита от SSRF.** `fetch_url()` и `_fetch_html_best_effort()` принимают только `http`/`https` схемы.

### Тесты (`protocols/tests/`)

Фабрики для создания объектов — `factories.py` (без factory_boy). Тесты используют `TestCase` (транзакции откатываются). Тест-файлы соответствуют модулям: `test_orgeo_parser.py`, `test_orgeo_browser.py`, `test_season_export.py`, `test_views.py`, `test_security.py` и т.д.