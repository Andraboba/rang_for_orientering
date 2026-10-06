# -*- coding: utf-8 -*-
"""
Тесты services/pdf_summary_parser.py — PDF с суммой многодневки.

Фикстуры — реальные строки из протоколов o-site.spb.ru и o-club.ru:
выравнивание колонок пробелами воспроизведено точно, потому что именно по нему
парсер определяет границы колонок.
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from protocols.services.pdf_summary_parser import (
    PDF_PART_DELIMITER,
    PDF_SUMMARY_PREFIX,
    extract_summary_meta,
    fetch_pdf_summary,
    is_pdf_summary_payload,
    looks_like_pdf_url,
    parse_pdf_summary,
)

# Формат o-site.spb.ru: сумма времени и сумма очков в скобках, место — колонка «№ п/п».
KKP_TEXT = """ККП-сумма 5 дней, 16-20.06.2026
Ж10RR

№ п/п Фамилия, имя           номер квал Коллектив              1 день   (очки)  2 день   (очки) Сумма(Очков)
   1 Рой Елизавета             3050 IIю  респ.Карелия         00:29:45 ( 37.3) 00:24:56 ( 85.1)02:03:58 (400.0)
   2 Иванникова Кристина       3426      СК Фотон, Москва     00:21:48 ( 80.8) 00:27:17 ( 74.2)02:12:44 (300.0)
   3 Райкова Нина              3568 IIIю г.Санкт-Петербург    00:24:47 ( 64.5) СНЯТ                     (200.0)
   4 Камынина Лариса           3462      г.Москва             СНЯТ             СНЯТ                       (0.0)
"""

# Формат o-club.ru: сумма очков организатора, отдельная колонка «Место».
OCLUB_POINTS_TEXT = """Макушка Лета 2026
02.08.2026 Ярославская область, рп. Петровское
СУММА 4-х дней
O1
№    Фамилия, имя                   Коллектив            ГР    Разряд  Номер  Сумма           Место  День 1 - 30.07     День 2 - 31.07
1    Вольнов Алексей                50_Лыткарино-К2, Лыт 1989  б/р     7004   400             1      100   (00:57:55)   100   (00:31:25)
2    Сетков Иван                    76_Верный азимут Сшо 1981  I       7016   300             2      93.09 (01:01:55)   95.58 (00:29:33)
6    Белотелов Владимир             77_СК Фотон, Москва  1978  III     7005   200             6      52.66 (01:25:20)   15.02 (00:52:21)
55   Алексеев Алексей               77_СК Фотон, Москва  1980  б/р     7201   0               30 =   0     (03:19:52)   0     (Снят)
"""

# Формат o-club.ru: сумма временем, в скобках — сколько дней зачтено.
OCLUB_TIME_TEXT = """Макушка Лета 2026
Кросс-лонг, финал
Код дисциплины 0830031811Я
КВ - МЖ10, МЖ12 и РсД – 60 минут, для остальных групп – 150 минут
01.08.2026 Ярославская область, рп. Петровское, д. Каюрово
СУММА
Ж12
№    Фамилия, имя                   Коллектив            ГР    Разряд  Номер  Сумма           Место  1                  2
1    Шелковникова Ульяна            36_ATLET, Воронеж    2014  I       2843   00:40:00 (2)    1      00:20:00           00:20:00
2    Антонова Дарья                 77_СК Фотон, Москва  2015  II      2801   00:48:00 (2)    2      00:25:29           00:22:31
3    Хрусталёва Екатерина           50_Ногинск, Ногински 2014  I       2840   00:50:12 (2)    3      00:28:39           00:21:33
41   Гордиенко Мария                77_СК Фотон, Москва  2015  I       2809   00:20:43 (1)    41     п.п.3.13.12.2      00:20:43
"""


class HelperTests(SimpleTestCase):
    def test_looks_like_pdf_url(self):
        self.assertTrue(looks_like_pdf_url("https://o-site.spb.ru/x/kkp5sum.pdf"))
        self.assertTrue(looks_like_pdf_url("https://o-club.ru/a/sum.PDF?v=1"))
        self.assertFalse(looks_like_pdf_url("https://o-club.ru/results.html"))

    def test_is_pdf_summary_payload(self):
        self.assertTrue(is_pdf_summary_payload(f"{PDF_SUMMARY_PREFIX}текст"))
        self.assertFalse(is_pdf_summary_payload("<html></html>"))


class MetaTests(SimpleTestCase):
    def test_date_range_takes_last_day(self):
        title, event_date = extract_summary_meta(KKP_TEXT)
        self.assertEqual(title, "ККП-сумма 5 дней")
        self.assertEqual(event_date, "2026-06-20")

    def test_single_date_and_multiline_title(self):
        title, event_date = extract_summary_meta(OCLUB_POINTS_TEXT)
        self.assertEqual(event_date, "2026-08-02")
        self.assertIn("Макушка Лета 2026", title)
        self.assertIn("СУММА 4-х дней", title)

    def test_service_lines_are_skipped(self):
        title, event_date = extract_summary_meta(OCLUB_TIME_TEXT)
        self.assertEqual(event_date, "2026-08-01")
        self.assertNotIn("Код дисциплины", title)
        self.assertNotIn("КВ -", title)


class PointsFormatTests(SimpleTestCase):
    """Сумма очков организатора нормируется к лидеру группы."""

    def test_all_rows_parsed_with_groups(self):
        rows = parse_pdf_summary(OCLUB_POINTS_TEXT, base_pairs=[], team_name="*")
        self.assertEqual(len(rows), 4)
        self.assertEqual({r["group_name"] for r in rows}, {"O1"})

    def test_leader_gets_1000_and_others_are_proportional(self):
        rows = parse_pdf_summary(OCLUB_POINTS_TEXT, base_pairs=[], team_name="*")
        points = {r["last_name"]: r["points"] for r in rows}
        self.assertEqual(points["Вольнов"], 1000)   # 400 очков → лидер
        self.assertEqual(points["Сетков"], 750)     # 300 / 400
        self.assertEqual(points["Белотелов"], 500)  # 200 / 400
        self.assertEqual(points["Алексеев"], 0)     # 0 очков

    def test_place_column_is_used(self):
        rows = parse_pdf_summary(OCLUB_POINTS_TEXT, base_pairs=[], team_name="*")
        places = {r["last_name"]: r["place"] for r in rows}
        self.assertEqual(places["Белотелов"], "6")
        # «30 =» — делёж места, берём число.
        self.assertEqual(places["Алексеев"], "30")

    def test_filter_by_team_foton_finds_people(self):
        rows = parse_pdf_summary(OCLUB_POINTS_TEXT, base_pairs=[], team_name="Фотон")
        self.assertEqual(
            sorted(r["last_name"] for r in rows), ["Алексеев", "Белотелов"]
        )
        self.assertTrue(any(r["points"] > 0 for r in rows))

    def test_filter_by_known_participants(self):
        rows = parse_pdf_summary(
            OCLUB_POINTS_TEXT, base_pairs=[("Сетков", "Иван")], team_name=""
        )
        self.assertEqual([r["last_name"] for r in rows], ["Сетков"])

    def test_unscored_row_has_no_place_out_of(self):
        rows = parse_pdf_summary(OCLUB_POINTS_TEXT, base_pairs=[], team_name="*")
        zero = next(r for r in rows if r["last_name"] == "Алексеев")
        self.assertIsNone(zero["place_out_of"])


class TimeFormatTests(SimpleTestCase):
    """Сумма временем: работает общая формула проекта."""

    def test_points_by_time_formula(self):
        rows = parse_pdf_summary(OCLUB_TIME_TEXT, base_pairs=[], team_name="*")
        points = {r["last_name"]: r["points"] for r in rows}
        # Лидер 40:00. Антонова 48:00 → (2 - 2880/2400) * 1000 = 800.
        self.assertEqual(points["Шелковникова"], 1000)
        self.assertEqual(points["Антонова"], 800)

    def test_incomplete_days_are_not_scored(self):
        """Прошедший 1 день из 2 не должен стать лидером группы."""
        rows = parse_pdf_summary(OCLUB_TIME_TEXT, base_pairs=[], team_name="*")
        partial = next(r for r in rows if r["last_name"] == "Гордиенко")
        self.assertEqual(partial["points"], 0)
        self.assertEqual(max(r["points"] for r in rows), 1000)

    def test_place_out_of_counts_only_full_distance(self):
        rows = parse_pdf_summary(OCLUB_TIME_TEXT, base_pairs=[], team_name="*")
        leader = next(r for r in rows if r["last_name"] == "Шелковникова")
        self.assertEqual(leader["place_out_of"], 3)

    def test_filter_by_team_foton(self):
        rows = parse_pdf_summary(OCLUB_TIME_TEXT, base_pairs=[], team_name="Фотон")
        self.assertEqual(
            sorted(r["last_name"] for r in rows), ["Антонова", "Гордиенко"]
        )


class KkpFormatTests(SimpleTestCase):
    """Формат o-site.spb.ru: колонка «№ п/п» служит местом."""

    def test_names_are_not_truncated(self):
        rows = parse_pdf_summary(KKP_TEXT, base_pairs=[], team_name="*")
        self.assertEqual(
            sorted(r["last_name"] for r in rows),
            ["Иванникова", "Камынина", "Райкова", "Рой"],
        )
        first = next(r for r in rows if r["last_name"] == "Рой")
        self.assertEqual(first["first_name"], "Елизавета")

    def test_group_and_place_from_first_column(self):
        rows = parse_pdf_summary(KKP_TEXT, base_pairs=[], team_name="*")
        first = next(r for r in rows if r["last_name"] == "Рой")
        self.assertEqual(first["group_name"], "Ж10RR")
        self.assertEqual(first["place"], "1")

    def test_points_normalized_to_leader(self):
        rows = parse_pdf_summary(KKP_TEXT, base_pairs=[], team_name="*")
        points = {r["last_name"]: r["points"] for r in rows}
        self.assertEqual(points["Рой"], 1000)         # 400.0 → лидер
        self.assertEqual(points["Иванникова"], 750)   # 300.0 / 400.0
        # Снят в одном дне, но очки за остальные дни сохраняются.
        self.assertEqual(points["Райкова"], 500)
        self.assertEqual(points["Камынина"], 0)

    def test_filter_by_team_foton(self):
        rows = parse_pdf_summary(KKP_TEXT, base_pairs=[], team_name="Фотон")
        self.assertEqual([r["last_name"] for r in rows], ["Иванникова"])
        self.assertEqual(rows[0]["points"], 750)

    def test_base_pairs_extended(self):
        base_pairs = []
        parse_pdf_summary(KKP_TEXT, base_pairs=base_pairs, team_name="Фотон")
        self.assertIn(("Иванникова", "Кристина"), base_pairs)


# Одиночный протокол WinOrient в PDF (Кубок клубов, группы РД и новички):
# колонка «Результат», группа — строка «Родители, 6 КП, 0,9 км».
CLUB_CUP_KIDS_TEXT = """Кубок Федерации спортивного ориентирования г. Москвы среди клубов
13.09.2026, Стадион Медик

ПРОТОКОЛ РЕЗУЛЬТАТОВ
Родители, 6 КП, 0,9 км
№п/п Фамилия, имя              Коллектив            Квал Номер ГР   Результат Место
   1 Денисов Александр         ХОРОШЁВО                    39 2020 00:09:06      1
   2 Семидоцкая Мария          Лосиный остров              17 2019 00:09:07      2
   9 Аманова Анна              СК ФОТОН                    50 2018 00:10:34   =  8
  20 Панфил Владислав          МОСКОМПАС ОРИЕНТА           30 1997
"""

# Этапы Кубка клубов: на этапе бегут несколько групп, места общие.
# «п.п.3.13.12.2» в колонке результата — пункт правил, а не очки.
CLUB_CUP_STAGES_TEXT = """КУБОК ФЕДЕРАЦИИ СПОРТИВНОГО ОРИЕНТИРОВАНИЯ Г. МОСКВЫ СРЕДИ
КЛУБОВ
13.09.2026 стадион "Медик"
ПРОТОКОЛ РЕЗУЛЬТАТОВ
1этап (М16, М45, Ж35)
№    Фамилия, имя                   Коллектив            ГР    Разряд  Номер  Результат    Отставание  Место
1    Шигаев Григорий                16 Ориента-star      2011  КМС     16     00:19:38                 1
6    Сай Макар                      8 СК Фотон           2011  I       8      00:20:52     00:01:14    6
164  Арсеньев Петр                  8 СК Фотон           1974  б/р     598    п.п.3.13.12.2
"""

# Эстафета SportOrg (первенство России, Ульяновск): номер команды отдельной
# строкой, у первого этапа — место, у последнего — время команды.
SPORTORG_RELAY_TEXT = """Первенство России, Всероссийские соревнования
2026-10-02, Ульяновская область, г. Ульяновск
ПРЕДВАРИТЕЛЬНЫЙ ПРОТОКОЛ РЕЗУЛЬТАТОВ
МЖ14
 13 КП, 2.3 км
1 лист, всего листов 10
№п/п Фамилия, имя, отчество      Коллектив/Субъект РФ      Квал Номер Дата рожд. Результат Ком.рез-т Место Очки
1
    Никольский Михаил Павлович        Москва                         I     1409 04.03.2012 00:09:05      00:09:05      1    0
    Калинин Лукьян Олегович           Москва                         I     2409 07.03.2012 00:08:52      00:17:58
    Наумова Полина Павловна           Москва                         I     3409 23.05.2012 00:09:22      00:27:21
    Венина Екатерина Кирилловна       Москва                         I     4409 30.05.2012 00:09:24      00:36:45
2
    Панфилов Матвей Алексеевич        Смоленская область             I     1422 05.11.2012 00:09:06      00:09:06      2    0
    Кожин Андрей Евгеньевич           Смоленская область             I     2422 15.05.2012 00:08:48      00:17:55
    Борисова Екатерина Алексеевна     Смоленская область             I     3422 03.05.2013 00:09:52      00:27:48
    Разуванова Варвара Евгеньевна     Смоленская область             I     4422 03.09.2012 00:09:41      00:37:29
67
    Соколов Роман Данилович           Москва                         I     1447 18.11.2013 00:10:13      00:10:13
    Чернышов Николай Игоревич         Москва                         I     2447 05.03.2013 00:11:16      00:21:29
    Белотелова Анастасия Владимировна Москва                         I     3447 04.11.2012 п.п.3.13.12.2 п.п.3.13.12.2
    Билас Таисия Витальевна           Москва                         I     4447 08.09.2012 00:13:47      Непр. отметка
"""


class SingleResultTests(SimpleTestCase):
    """PDF одного старта: колонка «Результат» вместо «Сумма»."""

    def _by_name(self, text, **kwargs):
        kwargs.setdefault("base_pairs", [])
        kwargs.setdefault("team_name", "*")
        return {r["last_name"]: r for r in parse_pdf_summary(text, **kwargs)}

    def test_group_from_course_heading(self):
        rows = self._by_name(CLUB_CUP_KIDS_TEXT)
        self.assertEqual({r["group_name"] for r in rows.values()}, {"Родители"})

    def test_points_by_time_and_tie_place(self):
        rows = self._by_name(CLUB_CUP_KIDS_TEXT)
        self.assertEqual(rows["Денисов"]["points"], 1000)
        # (2 - 10:34 / 9:06) * 1000
        self.assertEqual(rows["Аманова"]["points"], 838)
        self.assertEqual(rows["Аманова"]["place"], "8")
        self.assertNotIn("(сумма)", rows["Аманова"]["raw_line"])

    def test_no_result_is_not_scored(self):
        rows = self._by_name(CLUB_CUP_KIDS_TEXT)
        self.assertEqual(rows["Панфил"]["place"], "снят")
        self.assertEqual(rows["Панфил"]["points"], 0)

    def test_stage_heading_is_a_group(self):
        rows = self._by_name(CLUB_CUP_STAGES_TEXT)
        self.assertEqual(rows["Сай"]["group_name"], "1 этап (М16, М45, Ж35)")

    def test_rule_number_is_not_read_as_points(self):
        rows = self._by_name(CLUB_CUP_STAGES_TEXT)
        self.assertEqual(rows["Шигаев"]["points"], 1000)
        # (2 - 20:52 / 19:38) * 1000
        self.assertEqual(rows["Сай"]["points"], 937)
        self.assertEqual(rows["Арсеньев"]["points"], 0)
        self.assertEqual(rows["Арсеньев"]["place"], "снят")

    def test_filter_by_team_foton(self):
        rows = self._by_name(CLUB_CUP_STAGES_TEXT, team_name="Фотон")
        self.assertEqual(set(rows), {"Сай", "Арсеньев"})

    def test_meta_stops_before_course_heading(self):
        title, event_date = extract_summary_meta(CLUB_CUP_KIDS_TEXT)
        self.assertEqual(event_date, "2026-09-13")
        self.assertNotIn("КП", title)


class SportOrgRelayTests(SimpleTestCase):
    def _by_name(self, **kwargs):
        kwargs.setdefault("base_pairs", [])
        kwargs.setdefault("team_name", "*")
        return {r["last_name"]: r for r in parse_pdf_summary(SPORTORG_RELAY_TEXT, **kwargs)}

    def test_members_get_team_points(self):
        rows = self._by_name()
        for last_name in ("Никольский", "Калинин", "Наумова", "Венина"):
            self.assertEqual(rows[last_name]["points"], 1000)
            self.assertEqual(rows[last_name]["place"], "1")
        # (2 - 37:29 / 36:45) * 1000
        self.assertEqual(rows["Разуванова"]["points"], 980)
        self.assertEqual(rows["Разуванова"]["group_name"], "МЖ14")
        self.assertEqual(rows["Разуванова"]["place_out_of"], 3)
        self.assertIn("(эстафета)", rows["Разуванова"]["raw_line"])

    def test_team_with_disqualified_leg_gets_zero(self):
        rows = self._by_name()
        for last_name in ("Соколов", "Белотелова", "Билас"):
            self.assertEqual(rows[last_name]["points"], 0)
            self.assertEqual(rows[last_name]["place"], "снят")

    def test_page_header_inside_team_is_ignored(self):
        # Команда разорвана переходом страницы: колонтитул с датой — не этап.
        lines = SPORTORG_RELAY_TEXT.splitlines()
        cut = lines.index("2") - 1
        text = "\n".join(
            lines[:cut]
            + ["ПО SportOrg v1.8.0b1 https://sportorg.readthedocs.io/ru/latest/"
               "                                            02.10.2026 22:09:22"]
            + lines[cut:]
        )
        rows = {r["last_name"]: r for r in parse_pdf_summary(text, [], "*")}
        self.assertEqual(rows["Венина"]["points"], 1000)
        self.assertNotIn("ПО", rows)

    def test_incomplete_team_is_not_the_leader(self):
        # Двое из четырёх: время команды после второго этапа — не результат.
        text = SPORTORG_RELAY_TEXT + (
            "71\n"
            "    Петряев Андрей Сергеевич          Ульяновская область            I     1471 01.01.2012 00:05:00      00:05:00\n"
            "    Батурин Семён Иванович            Ульяновская область            I     2471 01.01.2012 00:05:00      00:10:00\n"
        )
        rows = {r["last_name"]: r for r in parse_pdf_summary(text, [], "*")}
        self.assertEqual(rows["Венина"]["points"], 1000)
        self.assertEqual(rows["Петряев"]["points"], 0)
        self.assertEqual(rows["Петряев"]["place"], "снят")

    def test_patronymic_is_dropped_and_base_filter_works(self):
        rows = self._by_name(base_pairs=[("Наумова", "Полина")], team_name="")
        self.assertEqual(list(rows), ["Наумова"])
        self.assertEqual(rows["Наумова"]["first_name"], "Полина")


class PdfPartsTests(SimpleTestCase):
    """Несколько PDF одного старта в одном протоколе разбираются по отдельности."""

    def test_parts_are_scored_separately(self):
        second_day = (
            "Родители, 6 КП, 0,9 км\n"
            "№п/п Фамилия, имя              Коллектив            Квал Номер ГР   Результат Место\n"
            "   9 Аманова Анна              СК ФОТОН                    50 2018 00:10:34      1\n"
        )
        payload = PDF_SUMMARY_PREFIX + CLUB_CUP_KIDS_TEXT + PDF_PART_DELIMITER + second_day
        rows = parse_pdf_summary(payload, base_pairs=[("Аманова", "Анна")], team_name="")
        self.assertEqual(sorted(r["points"] for r in rows), [838, 1000])

    def test_parts_of_one_date_are_one_day(self):
        # Два файла Кубка клубов от 13.09 — один старт.
        payload = CLUB_CUP_KIDS_TEXT + PDF_PART_DELIMITER + CLUB_CUP_STAGES_TEXT
        rows = parse_pdf_summary(payload, base_pairs=[], team_name="*")
        self.assertEqual({r["day"] for r in rows}, {1})

    def test_parts_of_different_dates_are_different_days(self):
        # Первенство России: эстафета 02.10 и личный старт 13.09 из другого файла.
        payload = SPORTORG_RELAY_TEXT + PDF_PART_DELIMITER + CLUB_CUP_STAGES_TEXT
        rows = parse_pdf_summary(payload, base_pairs=[], team_name="*")
        days = {r["group_name"]: r["day"] for r in rows}
        self.assertEqual(days, {"МЖ14": 1, "1 этап (М16, М45, Ж35)": 2})

    def test_relay_and_individual_parts(self):
        payload = SPORTORG_RELAY_TEXT + PDF_PART_DELIMITER + CLUB_CUP_STAGES_TEXT
        rows = parse_pdf_summary(payload, base_pairs=[], team_name="*")
        self.assertEqual({r["group_name"] for r in rows}, {"МЖ14", "1 этап (М16, М45, Ж35)"})


class FetchTests(SimpleTestCase):
    @patch("protocols.services.pdf_summary_parser.extract_pdf_text")
    @patch("protocols.services.pdf_summary_parser.fetch_pdf_bytes")
    def test_fetch_builds_payload(self, mock_bytes, mock_text):
        mock_bytes.return_value = (b"%PDF-1.7 ...", None)
        mock_text.return_value = (OCLUB_POINTS_TEXT, None)
        payload, title, event_date, err = fetch_pdf_summary("https://o-club.ru/s.pdf")
        self.assertIsNone(err)
        self.assertTrue(payload.startswith(PDF_SUMMARY_PREFIX))
        self.assertIn("Макушка Лета 2026", title)
        self.assertEqual(event_date, "2026-08-02")
        # Payload перепарсивается без повторной загрузки.
        self.assertEqual(
            len(parse_pdf_summary(payload, base_pairs=[], team_name="*")), 4
        )

    @patch("protocols.services.pdf_summary_parser.extract_pdf_text")
    @patch("protocols.services.pdf_summary_parser.fetch_pdf_bytes")
    def test_unsupported_pdf_reports_error(self, mock_bytes, mock_text):
        mock_bytes.return_value = (b"%PDF-1.7 ...", None)
        mock_text.return_value = ("Просто текст без таблицы", None)
        payload, _title, _date, err = fetch_pdf_summary("https://o-club.ru/x.pdf")
        self.assertIsNone(payload)
        self.assertIn("Сумма", err)

    @patch("protocols.services.pdf_summary_parser.fetch_pdf_bytes")
    def test_download_error(self, mock_bytes):
        mock_bytes.return_value = (None, "404")
        payload, _title, _date, err = fetch_pdf_summary("https://o-club.ru/x.pdf")
        self.assertIsNone(payload)
        self.assertIn("404", err)

    def test_rejects_non_http_scheme(self):
        payload, _title, _date, err = fetch_pdf_summary("file:///etc/passwd")
        self.assertIsNone(payload)
        self.assertIn("схема", err.lower())
