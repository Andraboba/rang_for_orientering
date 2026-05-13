# -*- coding: utf-8 -*-
"""Тесты services/sportident_parser.py."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from protocols.services.sportident_parser import (
    SPORTIDENT_TABLES_DELIMITER,
    _calc_points,
    _clean_text,
    _extract_event_id,
    _extract_group_urls,
    _normalize_status,
    _seconds_from_time,
    fetch_sportident_tables,
    parse_sportident,
)


class HelperTests(SimpleTestCase):
    def test_delimiter_is_string(self):
        self.assertTrue(SPORTIDENT_TABLES_DELIMITER)

    def test_clean_text(self):
        self.assertEqual(_clean_text('<b>&nbsp;Привет </b>мир\xa0\n'), 'Привет мир')

    def test_clean_text_empty(self):
        self.assertEqual(_clean_text(''), '')

    def test_extract_event_id_from_query(self):
        self.assertEqual(
            _extract_event_id('https://sportident.online/ol_new/?id=2743'), '2743'
        )

    def test_extract_event_id_missing(self):
        self.assertIsNone(_extract_event_id('https://sportident.online/ol_new/'))

    def test_extract_event_id_invalid_url(self):
        # не падает на мусоре
        self.assertIsNone(_extract_event_id('not a url'))

    def test_extract_group_urls(self):
        html = "<a href='?id=2743&g=7'>g7</a><a href='?id=2743&g=8'>g8</a>"
        urls = _extract_group_urls(html, 'https://sportident.online/ol_new/')
        self.assertEqual(len(urls), 2)
        self.assertIn('?id=2743&g=7', urls[0])

    def test_seconds_from_time_hms(self):
        self.assertEqual(_seconds_from_time('1:02:03'), 3723)

    def test_seconds_from_time_with_comma(self):
        self.assertEqual(_seconds_from_time('0:07:55,4'), 475)

    def test_seconds_from_time_mmss(self):
        self.assertEqual(_seconds_from_time('10:30'), 630)

    def test_seconds_from_time_invalid(self):
        self.assertIsNone(_seconds_from_time('abc'))
        self.assertIsNone(_seconds_from_time(''))

    def test_calc_points(self):
        self.assertEqual(_calc_points(100, 100), 1000)
        self.assertEqual(_calc_points(150, 100), 500)
        self.assertEqual(_calc_points(100, 0), 0)

    def test_normalize_status_dnf(self):
        self.assertEqual(_normalize_status(['1', 'Иванов', 'DNF']), 'снят')

    def test_normalize_status_dns(self):
        self.assertEqual(_normalize_status(['', 'Петров', 'DNS']), 'н.старт')

    def test_normalize_status_ok(self):
        self.assertIsNone(_normalize_status(['1', 'Иванов', '10:00']))


SPORTIDENT_GROUP_HTML = """
<html>
<div class="title_grup">Группа М14</div>
<table>
<tr class="hoverRow"><td>1</td><td>Иванов Иван</td><td>СК Фотон</td><td>10:00</td></tr>
<tr class="hoverRow"><td>2</td><td>Петров Пётр</td><td>СК Фотон</td><td>15:00</td></tr>
<tr class="hoverRow"><td></td><td>Сидоров Сидр</td><td>Другие</td><td>DNS</td></tr>
</table>
</html>
"""


class ParseSportidentTests(SimpleTestCase):
    def test_parse_all_star(self):
        r = parse_sportident(SPORTIDENT_GROUP_HTML, base_pairs=[], team_name='*')
        self.assertEqual(len(r), 3)
        self.assertEqual(r[0]['group_name'], 'Группа М14')

    def test_parse_by_team(self):
        r = parse_sportident(SPORTIDENT_GROUP_HTML, base_pairs=[], team_name='Фотон')
        self.assertEqual(len(r), 2)

    def test_parse_by_base(self):
        r = parse_sportident(
            SPORTIDENT_GROUP_HTML,
            base_pairs=[('иванов', 'иван')],
            team_name='',
        )
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['last_name'], 'Иванов')

    def test_parse_leader_1000(self):
        r = parse_sportident(SPORTIDENT_GROUP_HTML, base_pairs=[], team_name='*')
        leader = next(x for x in r if x['last_name'] == 'Иванов')
        self.assertEqual(leader['points'], 1000)

    def test_parse_status_zero(self):
        r = parse_sportident(SPORTIDENT_GROUP_HTML, base_pairs=[], team_name='*')
        dq = next(x for x in r if x['last_name'] == 'Сидоров')
        self.assertEqual(dq['points'], 0)
        self.assertEqual(dq['place'], 'н.старт')

    def test_parse_no_rows(self):
        self.assertEqual(
            parse_sportident('<html>no</html>', base_pairs=[], team_name='*'), []
        )

    def test_base_pairs_updated(self):
        base = []
        parse_sportident(SPORTIDENT_GROUP_HTML, base_pairs=base, team_name='Фотон')
        self.assertIn(('Иванов', 'Иван'), base)


class FetchSportidentTablesTests(SimpleTestCase):
    def test_missing_id(self):
        tables, title, err = fetch_sportident_tables('https://sportident.online/ol_new/')
        self.assertIsNone(tables)
        self.assertIn('id', err)

    @patch('protocols.services.sportident_parser._fetch_html_best_effort')
    def test_no_groups_and_no_table(self, mock_fetch):
        mock_fetch.return_value = ('<html><title>T</title></html>', None)
        tables, title, err = fetch_sportident_tables(
            'https://sportident.online/ol_new/?id=1'
        )
        self.assertIsNone(tables)
        self.assertEqual(title, 'T')
        self.assertIn('групп', err.lower())

    @patch('protocols.services.sportident_parser._fetch_html_best_effort')
    def test_single_page_with_table(self, mock_fetch):
        html = (
            '<html><title>Соревнование</title>'
            '<tr class="hoverRow"><td>1</td></tr></html>'
        )
        mock_fetch.return_value = (html, None)
        tables, title, err = fetch_sportident_tables(
            'https://sportident.online/ol_new/?id=1'
        )
        self.assertIsNone(err)
        self.assertEqual(tables, [html])
        self.assertEqual(title, 'Соревнование')

    @patch('protocols.services.sportident_parser._fetch_html_best_effort')
    def test_with_groups_success(self, mock_fetch):
        main = (
            "<html><title>N</title>"
            "<a href='?id=1&g=7'>g7</a><a href='?id=1&g=8'>g8</a></html>"
        )
        g7 = "<html>g7</html>"
        g8 = "<html>g8</html>"

        def side_effect(url):
            if url.endswith('?id=1'):
                return main, None
            if 'g=7' in url:
                return g7, None
            if 'g=8' in url:
                return g8, None
            return None, 'unknown'

        mock_fetch.side_effect = side_effect
        tables, title, err = fetch_sportident_tables(
            'https://sportident.online/ol_new/?id=1'
        )
        self.assertIsNone(err)
        self.assertEqual(sorted(tables), sorted([g7, g8]))
        self.assertEqual(title, 'N')

    @patch('protocols.services.sportident_parser._fetch_html_best_effort')
    def test_main_fetch_error(self, mock_fetch):
        mock_fetch.return_value = (None, 'timeout')
        tables, title, err = fetch_sportident_tables(
            'https://sportident.online/ol_new/?id=1'
        )
        self.assertIsNone(tables)
        self.assertIn('timeout', err)

    @patch('protocols.services.sportident_parser._fetch_html_best_effort')
    def test_all_group_fetches_fail(self, mock_fetch):
        main = "<html><a href='?id=1&g=7'>g7</a></html>"

        def side_effect(url):
            if url.endswith('?id=1'):
                return main, None
            return None, 'fail'

        mock_fetch.side_effect = side_effect
        tables, title, err = fetch_sportident_tables(
            'https://sportident.online/ol_new/?id=1'
        )
        self.assertIsNone(tables)
        self.assertIn('Не удалось', err)


class FetchHtmlBestEffortTests(SimpleTestCase):
    @patch('protocols.services.sportident_parser.urlopen')
    def test_utf8(self, mock_urlopen):
        from protocols.services.sportident_parser import _fetch_html_best_effort
        resp = MagicMock()
        resp.read.return_value = 'привет'.encode('utf-8')
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = resp
        text, err = _fetch_html_best_effort('https://x')
        self.assertIsNone(err)
        self.assertEqual(text, 'привет')

    @patch('protocols.services.sportident_parser.urlopen')
    def test_cp1251_fallback(self, mock_urlopen):
        from protocols.services.sportident_parser import _fetch_html_best_effort
        resp = MagicMock()
        resp.read.return_value = 'привет'.encode('windows-1251')
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = resp
        text, err = _fetch_html_best_effort('https://x')
        self.assertIsNone(err)
        self.assertEqual(text, 'привет')

    def test_fetch_blocks_file_scheme(self):
        from protocols.services.sportident_parser import _fetch_html_best_effort
        text, err = _fetch_html_best_effort('file:///etc/passwd')
        self.assertIsNone(text)
        self.assertIn('схема', err.lower())

    def test_fetch_blocks_ftp_scheme(self):
        from protocols.services.sportident_parser import _fetch_html_best_effort
        text, err = _fetch_html_best_effort('ftp://example.com/x')
        self.assertIsNone(text)
        self.assertIn('схема', err.lower())

    @patch('protocols.services.sportident_parser.urlopen')
    def test_network_error(self, mock_urlopen):
        from urllib.error import URLError
        from protocols.services.sportident_parser import _fetch_html_best_effort
        mock_urlopen.side_effect = URLError('down')
        text, err = _fetch_html_best_effort('https://x')
        self.assertIsNone(text)
        self.assertIn('down', err)
