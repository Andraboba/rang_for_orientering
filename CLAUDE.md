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
- **`KnownParticipant`** — база известных участников (фамилия + имя). Флаг `veteran` переносит участника в ветеранские листы экспорта; флаг `excluded` («не учитывать никогда») убирает его отовсюду — из поиска при обработке протоколов и из обоих зачётов (для тех, кто бежал за команду разово). Флаг `club_only` («только за клуб») — участник находится только по названию команды, но не по базе: на первенстве России он бежит за свой регион, и такие результаты не берутся. Полные тёзки разводятся двумя полями: `group_filter` отбрасывает результаты чужого однофамильца (`«М21+»` — группы М от 21 и старше, либо точные названия групп), а `split_by_group` разделяет в зачёте двух своих с одинаковым ФИО (отец и сын) на отдельные строки с группой.
- **`ProtocolGroupLevel`** — уровень первенства для отдельной группы протокола. Нужен, когда статус старта зависит от группы (в Зеленограде первенство России было только у МЖ14 и МЖ16, остальные группы — всероссийские). Задаётся inline в админке протокола.
- **`ProtocolResult`** — одна строка результата. `weighted_points = points × Protocol.coefficient_for_group(group_name)`: коэффициент группы, если для неё задан свой уровень, иначе общий коэффициент протокола.
- **`SeasonRanking`** / **`SeasonRankingEntry`** — рейтинг сезона с периодом `date_from`/`date_to`.

### Сервисы (`protocols/services/`)

| Файл | Назначение |
|---|---|
| `source_router.py` | **Единая точка входа.** По ссылке определяет вид источника (`KIND_*`), загружает (`load_from_url()` → `LoadedSource`) и выбирает парсер (`parse_payload()`). Views не знают о конкретных парсерах. |
| `parser.py` | Парсинг WinOrient: два формата — `sfr=0` (pre/h2) и `sfr=1` (HTML-таблица). В `pre` результат ищется по виду значения (последнее `ЧЧ:ММ:СС`, место — число после него), поэтому колонки «ШТРАФ»/«Отставание»/«Вып» не мешают. Эстафетные группы (строка `<b>N </b>` перед командой) разбирает `process_mixed_pre()`, в т.ч. вперемешку с личными. Формула очков: `(2 - t_actual/t_leader) × 1000`. |
| `orgeo_parser.py` | Парсинг одной HTML-страницы группы Orgeo. Номера колонок — по заголовку таблицы (`_detect_columns`: бывают «Онлайн-КП» и «FINISH»). Фильтрует по `team_name` и/или `base_pairs` (известные участники). |
| `orgeo_browser.py` | Загрузка ВСЕХ групп/дней с Orgeo через HTTP (без Playwright). Поддерживает любые форматы URL Orgeo (`live/#/alias`, `/online/id`, `/event/id`, `/online/finish/id`). Главная функция: `fetch_orgeo_tables()`. Разделитель: `ORGEO_TABLES_DELIMITER`. |
| `sportident_parser.py` | Загрузка и парсинг SportIdent Online. `fetch_sportident_tables()` → список HTML-чанков, `parse_sportident()` — парсинг одного чанка. |
| `sportorg_parser.py` | HTML-экспорт SportOrg (o-club.ru). Таблиц в HTML нет — данные лежат в `raceCompressed` = base64(gzip(JSON)). Парсится JSON, не HTML. Эстафета определяется по `race.data.race_type`. |
| `pdf_summary_parser.py` | PDF по текстовому слою, без OCR: сумма многодневки (o-site.spb.ru, o-club.ru), протокол одного старта (колонка «Результат», группа — «Ж14, 23 КП» или «1этап (…)») и эстафета SportOrg (колонка «Ком.рез-т»). Колонки режутся по позициям из строки заголовка таблицы. Несколько PDF одного старта хранятся в одном протоколе через `PDF_PART_DELIMITER` и разбираются по отдельности. |
| `season_export.py` | Экспорт сезонного зачёта в Excel (openpyxl). Два листа: «Места» (по 3 колонки на старт) и «Баллы». Дата протокола берётся из `event.date` или `protocol.event_date`. |
| `title_cleaner.py` | Удаляет служебные фразы («ПРОТОКОЛ РЕЗУЛЬТАТОВ», «Федерация...») из названий. |
| `date_from_title.py` | Извлекает дату из строкового названия протокола. |

### Ключевые соглашения

**Загрузка одной ссылкой.** Пользователь вставляет URL, вид источника определяется автоматически (`detect_kind()` по домену/расширению, SportOrg — по содержимому страницы). Вид сохраняется в `Protocol.source_kind`; у записей, загруженных до появления поля, он вычисляется свойством `Protocol.kind` через `detect_kind_by_payload()`. Флаги `encoding_utf8`/`format_sfr` в форме нужны только WinOrient.

**Что лежит в `raw_html`.** Поле хранит то, что нужно для повторного парсинга без сети:
- Orgeo / SportIdent — HTML всех групп через `ORGEO_TABLES_DELIMITER` / `SPORTIDENT_TABLES_DELIMITER`;
- SportOrg — `SPORTORG_DATA_PREFIX` + base64(gzip(JSON));
- PDF (текстовый слой) — `PDF_SUMMARY_PREFIX` + текст PDF; несколько файлов — через `PDF_PART_DELIMITER`;
- WinOrient — HTML как есть.

Поэтому перепарсить с другим фильтром можно любой протокол, кроме скана PDF (`pdf_ocr`), где результаты создаются при импорте.

**Фильтрация участников.** Парсеры принимают `base_pairs: list[tuple[str, str]]` (фамилия, имя) и `team_name: str`. Логика: если `team_name` задан — берём участников команды ИЛИ известных; если только `base_pairs` — только известных; если оба пусты — всех. `team_name='*'` — показать всех. Сравнение без учёта регистра.

Участники с `excluded=True` вырезаются на уровне вью: они не попадают в `base_pairs` и отбрасываются из результатов парсинга, даже если совпали по названию команды. Участники с `club_only=True` тоже не попадают в `base_pairs` (и в `known` при OCR-загрузке PDF), но остаются в результатах, если совпали по названию команды. В экспорте их отсекает `excluded_name_pairs()` во всех трёх `build_*`-функциях.

**Очки.** Единая формула: `(2 - t_actual / t_leader) × 1000`, минимум 1; лидер группы получает 1000. Статусы (DNS, DNF, DSQ, превышено КВ) → 0 очков. В эстафетах все члены команды получают очки по итоговому времени команды (в SportOrg это результат последнего этапа; команда без последнего этапа не получает очков). Команда, у которой хоть один этап снят («непр.отмет.», пункт правил) или вышли не все участники, очков не получает, даже если последний этап финишировал. В PDF-суммах, где организатор уже посчитал очки, они нормируются к лидеру группы — участник, снятый в одном дне, сохраняет очки за остальные; если сумма дана только временем, в зачёт идут те, кто прошёл все дни.

**Защита от SSRF.** Все загрузчики (`fetch_url()`, `_fetch_html_best_effort()`, `sportorg_parser._fetch_html()`, `pdf_summary_parser.fetch_pdf_bytes()`) принимают только `http`/`https` схемы.

### Тесты (`protocols/tests/`)

Фабрики для создания объектов — `factories.py` (без factory_boy). Тесты используют `TestCase` (транзакции откатываются). Тест-файлы соответствуют модулям: `test_orgeo_parser.py`, `test_orgeo_browser.py`, `test_sportorg_parser.py`, `test_pdf_summary_parser.py`, `test_source_router.py`, `test_season_export.py`, `test_views.py`, `test_security.py` и т.д.

Тесты не ходят в сеть: загрузчики мокаются, а данные берутся из фикстур внутри тест-файлов. В `test_pdf_summary_parser.py` фикстуры — реальные строки PDF с точным выравниванием пробелами (по нему определяются границы колонок), в `test_sportorg_parser.py` гонка собирается в base64(gzip(JSON)) функцией `_race_html()`. Фильтрацию проверяем на команде «СК Фотон» и на базе известных участников.