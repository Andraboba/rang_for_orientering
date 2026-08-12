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
