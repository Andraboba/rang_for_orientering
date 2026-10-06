# -*- coding: utf-8 -*-
"""Тесты services/source_router.py — автоопределение источника по ссылке."""
from unittest.mock import patch

from django.test import SimpleTestCase

from protocols.services.orgeo_browser import ORGEO_DAY_MARKER, ORGEO_TABLES_DELIMITER
from protocols.services.pdf_summary_parser import PDF_SUMMARY_PREFIX
from protocols.services.source_router import (
    KIND_ORGEO,
    KIND_PDF_OCR,
    KIND_PDF_SUMMARY,
    KIND_SPORTIDENT,
    KIND_SPORTORG,
    KIND_WINORIENT,
    detect_kind,
    detect_kind_by_payload,
    load_from_url,
    parse_payload,
    show_all_team_name,
    split_chunks,
)
from protocols.services.sportorg_parser import SPORTORG_DATA_PREFIX

from .test_pdf_summary_parser import OCLUB_POINTS_TEXT
from .test_sportorg_parser import _personal_race, _race_html


class DetectKindTests(SimpleTestCase):
    def test_orgeo_links(self):
        for url in (
            "https://orgeo.ru/live/#/50660/1",
            "https://orgeo.ru/online/50628",
            "https://orgeo.ru/event/50662",
            "https://orgeo.ru/online/finish/50662?s=1",
        ):
            self.assertEqual(detect_kind(url), KIND_ORGEO, url)

    def test_sportident_link(self):
        self.assertEqual(
            detect_kind("https://sportident.online/ol_new/?id=2831"), KIND_SPORTIDENT
        )

    def test_pdf_link(self):
        self.assertEqual(
            detect_kind("https://o-site.spb.ru/_races/kkp5sum.pdf"), KIND_PDF_SUMMARY
        )

    def test_html_needs_content_check(self):
        self.assertIsNone(detect_kind("https://o-club.ru/results.html?sportorg=1"))


class DetectByPayloadTests(SimpleTestCase):
    """Протоколы, загруженные до появления поля source_kind."""

    def test_sportorg_payload(self):
        self.assertEqual(
            detect_kind_by_payload(f"{SPORTORG_DATA_PREFIX}abc", ""), KIND_SPORTORG
        )

    def test_pdf_summary_payload(self):
        self.assertEqual(
            detect_kind_by_payload(f"{PDF_SUMMARY_PREFIX}текст", ""), KIND_PDF_SUMMARY
        )

    def test_legacy_urls(self):
        self.assertEqual(
            detect_kind_by_payload("<html/>", "https://orgeo.ru/online/1"), KIND_ORGEO
        )
        self.assertEqual(
            detect_kind_by_payload(
                "<html/>", "https://sportident.online/ol_new/?id=1"
            ),
            KIND_SPORTIDENT,
        )
        self.assertEqual(
            detect_kind_by_payload("текст", "pdf_upload:scan.pdf"), KIND_PDF_OCR
        )
        self.assertEqual(
            detect_kind_by_payload("<html/>", "https://example.com/p.htm"),
            KIND_WINORIENT,
        )


class LoadFromUrlTests(SimpleTestCase):
    @patch("protocols.services.source_router.fetch_orgeo_tables")
    @patch("protocols.services.source_router.resolve_orgeo_url")
    def test_orgeo_event_link_is_normalized(self, mock_resolve, mock_tables):
        mock_resolve.return_value = (
            "https://orgeo.ru/online/50662",
            "Эстафета 3 человека",
            "2026-06-02",
            [],
            None,
        )
        mock_tables.return_value = (["<html>Фамилия</html>"], None)
        loaded = load_from_url("https://orgeo.ru/event/50662")
        self.assertEqual(loaded.kind, KIND_ORGEO)
        self.assertEqual(loaded.url, "https://orgeo.ru/online/50662")
        self.assertEqual(loaded.event_date, "2026-06-02")
        self.assertTrue(loaded.encoding_utf8)
        self.assertIsNone(loaded.error)

    @patch("protocols.services.source_router.fetch_orgeo_tables")
    @patch("protocols.services.source_router.resolve_orgeo_url")
    def test_orgeo_date_falls_back_to_first_day(self, mock_resolve, mock_tables):
        mock_resolve.return_value = (
            "https://orgeo.ru/online/1",
            "Соревнование",
            None,
            [{"sub_id": "1", "date": "2026-06-05"}],
            None,
        )
        mock_tables.return_value = (["<html>Фамилия</html>"], None)
        loaded = load_from_url("https://orgeo.ru/live/#/1")
        self.assertEqual(loaded.event_date, "2026-06-05")

    @patch("protocols.services.source_router.resolve_orgeo_url")
    def test_orgeo_resolve_error(self, mock_resolve):
        mock_resolve.return_value = ("", None, None, [], "плохая ссылка")
        loaded = load_from_url("https://orgeo.ru/live/#/bad")
        self.assertIn("плохая ссылка", loaded.error)

    @patch("protocols.services.source_router.fetch_sportident_tables")
    def test_sportident(self, mock_tables):
        mock_tables.return_value = (["<html>1</html>", "<html>2</html>"], "SI", None)
        loaded = load_from_url("https://sportident.online/ol_new/?id=2831")
        self.assertEqual(loaded.kind, KIND_SPORTIDENT)
        self.assertEqual(loaded.title, "SI")
        self.assertEqual(len(split_chunks(loaded.kind, loaded.payload)), 2)

    @patch("protocols.services.source_router.fetch_url")
    def test_html_with_race_data_becomes_sportorg(self, mock_fetch):
        mock_fetch.return_value = (_race_html(_personal_race()), None)
        with patch(
            "protocols.services.sportorg_parser._fetch_html",
            return_value=(_race_html(_personal_race()), None),
        ):
            loaded = load_from_url("https://o-club.ru/results.html?sportorg=1")
        self.assertEqual(loaded.kind, KIND_SPORTORG)
        self.assertEqual(loaded.event_date, "2026-07-31")
        self.assertTrue(loaded.payload.startswith(SPORTORG_DATA_PREFIX))

    @patch("protocols.services.source_router.fetch_url")
    def test_plain_html_stays_winorient(self, mock_fetch):
        mock_fetch.return_value = ("<html><h1>Кубок</h1></html>", None)
        loaded = load_from_url("https://example.com/protocol.htm")
        self.assertEqual(loaded.kind, KIND_WINORIENT)
        self.assertIsNone(loaded.error)

    @patch("protocols.services.source_router.fetch_url")
    def test_html_fetch_error(self, mock_fetch):
        mock_fetch.return_value = (None, "timeout")
        loaded = load_from_url("https://example.com/protocol.htm")
        self.assertIn("timeout", loaded.error)

    @patch("protocols.services.source_router.fetch_url")
    def test_encoding_is_retried_automatically(self, mock_fetch):
        """Страницу в UTF-8 не должен спасать флажок в форме."""
        mock_fetch.side_effect = [
            (None, "Ошибка кодировки: 'charmap' codec can't decode byte 0x98"),
            ("<html><h1>Соревнования</h1></html>", None),
        ]
        loaded = load_from_url("https://o-club.ru/results.html", encoding_utf8=False)
        self.assertIsNone(loaded.error)
        self.assertTrue(loaded.encoding_utf8)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("protocols.services.source_router.fetch_url")
    def test_encoding_retry_reports_original_error(self, mock_fetch):
        mock_fetch.side_effect = [
            (None, "Ошибка кодировки: byte 0x98"),
            (None, "Ошибка кодировки: byte 0x98"),
        ]
        loaded = load_from_url("https://example.com/x.htm")
        self.assertIn("кодировки", loaded.error)

    @patch("protocols.services.pdf_summary_parser.extract_pdf_text")
    @patch("protocols.services.pdf_summary_parser.fetch_pdf_bytes")
    def test_pdf_link(self, mock_bytes, mock_text):
        mock_bytes.return_value = (b"%PDF-1.4", None)
        mock_text.return_value = (OCLUB_POINTS_TEXT, None)
        loaded = load_from_url("https://o-club.ru/sum.pdf")
        self.assertEqual(loaded.kind, KIND_PDF_SUMMARY)
        self.assertEqual(loaded.event_date, "2026-08-02")
        self.assertTrue(loaded.payload.startswith(PDF_SUMMARY_PREFIX))


class ParsePayloadTests(SimpleTestCase):
    def test_sportorg_filter_by_team(self):
        payload = SPORTORG_DATA_PREFIX + _race_html(_personal_race()).split("'")[1]
        rows = parse_payload(KIND_SPORTORG, payload, base_pairs=[], team_name="Фотон")
        self.assertEqual(len(rows), 2)

    def test_pdf_summary_filter_by_team(self):
        rows = parse_payload(
            KIND_PDF_SUMMARY,
            f"{PDF_SUMMARY_PREFIX}{OCLUB_POINTS_TEXT}",
            base_pairs=[],
            team_name="Фотон",
        )
        self.assertEqual(len(rows), 2)

    def test_orgeo_chunks_are_parsed_separately(self):
        chunk = (
            '<h2>М12</h2><table>'
            '<tr><th>Место</th></tr>'
            '<tr><td>1</td><td>Иванов Иван</td><td>2010</td><td>СК Фотон</td>'
            '<td>00:20:00</td></tr>'
            '</table>'
        )
        raw = ORGEO_TABLES_DELIMITER.join([chunk, chunk])
        self.assertEqual(len(split_chunks(KIND_ORGEO, raw)), 2)

    def test_orgeo_results_get_day_of_their_table(self):
        chunk = (
            '<h2>Группа М12</h2><table>'
            '<tr><th>П/п</th><th>№</th><th>Фамилия, имя</th><th>Команда</th><th>Результат</th></tr>'
            '<tr><td>1</td><td>101</td><td>Иванов Иван</td><td>СК Фотон</td><td>00:20:00</td></tr>'
            '</table>'
        )
        raw = ORGEO_TABLES_DELIMITER.join(
            [ORGEO_DAY_MARKER.format('2') + chunk, ORGEO_DAY_MARKER.format('3') + chunk]
        )
        rows = parse_payload(KIND_ORGEO, raw, base_pairs=[], team_name='Фотон')
        self.assertEqual([r['day'] for r in rows], [1, 2])

    def test_winorient_uses_format_flag(self):
        rows = parse_payload(
            KIND_WINORIENT,
            "<html></html>",
            base_pairs=[],
            team_name="Фотон",
            format_sfr=True,
        )
        self.assertEqual(rows, [])

    def test_empty_payload(self):
        self.assertEqual(split_chunks(KIND_ORGEO, "   "), [])


class ShowAllTeamNameTests(SimpleTestCase):
    def test_winorient_uses_empty_filter(self):
        """У WinOrient «все» — это пустая команда при пустой базе."""
        self.assertEqual(show_all_team_name(KIND_WINORIENT), "")

    def test_others_use_star(self):
        for kind in (KIND_ORGEO, KIND_SPORTIDENT, KIND_SPORTORG, KIND_PDF_SUMMARY):
            self.assertEqual(show_all_team_name(kind), "*")
