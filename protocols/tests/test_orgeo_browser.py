# -*- coding: utf-8 -*-
"""Тесты services/orgeo_browser.py с замоканными сетевыми запросами."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from protocols.services.orgeo_browser import (
    ORGEO_TABLES_DELIMITER,
    _extract_group_urls,
    _extract_header_url,
    _extract_main_frame_url,
    _fetch_orgeo_meta,
    fetch_orgeo_tables,
    fetch_orgeo_with_browser,
    resolve_orgeo_url,
)


class DelimiterConstantTests(SimpleTestCase):
    def test_delimiter_is_string(self):
        self.assertIsInstance(ORGEO_TABLES_DELIMITER, str)
        self.assertTrue(ORGEO_TABLES_DELIMITER)


class ExtractUrlHelpersTests(SimpleTestCase):
    def test_extract_header_url_name_then_src(self):
        fs = '<frameset><frame name="header" src="/top?id=1&s=2"/></frameset>'
        self.assertEqual(
            _extract_header_url(fs, 'https://orgeo.ru'),
            'https://orgeo.ru/top?id=1&s=2',
        )

    def test_extract_header_url_src_then_name(self):
        fs = '<frame src="/top?id=1&s=2" name="header"/>'
        self.assertEqual(
            _extract_header_url(fs, 'https://orgeo.ru'),
            'https://orgeo.ru/top?id=1&s=2',
        )

    def test_extract_header_url_none(self):
        self.assertIsNone(_extract_header_url('<html/>', 'https://orgeo.ru'))

    def test_extract_main_frame_url(self):
        fs = '<frame name="main" src="/online/finish/1?d=Ж10"/>'
        self.assertEqual(
            _extract_main_frame_url(fs, 'https://orgeo.ru'),
            'https://orgeo.ru/online/finish/1?d=Ж10',
        )

    def test_extract_main_frame_url_src_then_name(self):
        fs = '<frame src="/online/finish/1?d=Ж10" name="main"/>'
        self.assertEqual(
            _extract_main_frame_url(fs, 'https://orgeo.ru'),
            'https://orgeo.ru/online/finish/1?d=Ж10',
        )

    def test_extract_group_urls(self):
        html = (
            "<a href='/online/finish/1?s=2&tz=3&d=Ж10'>Ж10</a>"
            "<a href=\"/online/finish/1?s=2&tz=3&d=М14\">М14</a>"
            "<a href='/online/finish/1?s=2&tz=3&d=Ж10'>Ж10 dup</a>"
        )
        urls = _extract_group_urls(html)
        self.assertEqual(len(urls), 2)
        self.assertTrue(any('Ж10' in u for u in urls))
        self.assertTrue(any('М14' in u for u in urls))


def _mock_session_responses(mapping):
    """Создаёт MagicMock-session, где session.get(url) возвращает заранее заготовленный текст."""
    def get(url, params=None, timeout=None):
        resp = MagicMock()
        if url in mapping:
            resp.text = mapping[url]
        elif params:
            # Для _fetch_orgeo_meta: возвращаем JSON
            resp.json.return_value = mapping.get('__json__', {})
        else:
            resp.text = ''
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        return resp
    session = MagicMock()
    session.get.side_effect = get
    session.headers = {}
    return session


class FetchOrgeoMetaTests(SimpleTestCase):
    @patch('protocols.services.orgeo_browser._make_session')
    def test_meta_happy_path(self, mock_make_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            'event_id': 49864,
            'subs': [
                {'sub_id': 2, 'event_name': 'Event', 'datetime': '2026-04-01 10:00', 'name': 'day1'},
                {'sub_id': 3, 'event_name': 'Event', 'datetime': '2026-04-02 10:00', 'name': 'day2'},
            ],
        }
        session = MagicMock()
        session.get.return_value = resp
        mock_make_session.return_value = session
        meta = _fetch_orgeo_meta('49864')
        self.assertEqual(meta['event_id'], '49864')
        self.assertEqual(meta['event_name'], 'Event')
        self.assertEqual(meta['event_date'], '2026-04-01')
        self.assertEqual(len(meta['days']), 2)
        self.assertIsNone(meta['error'])

    @patch('protocols.services.orgeo_browser._make_session')
    def test_meta_error(self, mock_make_session):
        session = MagicMock()
        session.get.side_effect = Exception('network down')
        mock_make_session.return_value = session
        meta = _fetch_orgeo_meta('x')
        self.assertIn('network down', meta['error'])


class ResolveOrgeoUrlTests(SimpleTestCase):
    @patch('protocols.services.orgeo_browser._fetch_orgeo_meta')
    def test_resolve_live_alias(self, mock_meta):
        mock_meta.return_value = {
            'event_id': '49864', 'event_name': 'N', 'event_date': '2026-04-01',
            'days': [{'sub_id': '2', 'name': 'd', 'date': '2026-04-01'}], 'error': None,
        }
        url, title, dt, days, err = resolve_orgeo_url('https://orgeo.ru/live/#/winter26')
        self.assertEqual(url, 'https://orgeo.ru/online/49864')
        self.assertEqual(title, 'N')
        self.assertEqual(dt, '2026-04-01')
        self.assertIsNone(err)

    @patch('protocols.services.orgeo_browser._fetch_orgeo_meta')
    def test_resolve_online_numeric(self, mock_meta):
        mock_meta.return_value = {
            'event_id': '49864', 'event_name': None, 'event_date': None,
            'days': [], 'error': None,
        }
        url, _t, _d, _days, err = resolve_orgeo_url('https://orgeo.ru/online/49864')
        self.assertEqual(url, 'https://orgeo.ru/online/49864')
        self.assertIsNone(err)

    @patch('protocols.services.orgeo_browser._fetch_orgeo_meta')
    def test_resolve_finish(self, mock_meta):
        mock_meta.return_value = {
            'event_id': '49864', 'event_name': None, 'event_date': None,
            'days': [], 'error': None,
        }
        url, *_ = resolve_orgeo_url('https://orgeo.ru/online/finish/49864?s=2&d=Ж10')
        self.assertEqual(url, 'https://orgeo.ru/online/49864')

    def test_resolve_unknown_format(self):
        url, title, dt, days, err = resolve_orgeo_url('https://example.com/unknown')
        self.assertEqual(url, '')
        self.assertIn('Не удалось распознать', err)

    @patch('protocols.services.orgeo_browser._fetch_orgeo_meta')
    def test_resolve_numeric_fallback_on_api_error(self, mock_meta):
        mock_meta.return_value = {
            'event_id': None, 'event_name': None, 'event_date': None,
            'days': [], 'error': 'api down',
        }
        url, *_vals, err = resolve_orgeo_url('https://orgeo.ru/online/12345')
        self.assertEqual(url, 'https://orgeo.ru/online/12345')
        self.assertIsNone(err)

    @patch('protocols.services.orgeo_browser._fetch_orgeo_meta')
    def test_resolve_alias_api_error(self, mock_meta):
        mock_meta.return_value = {
            'event_id': None, 'event_name': None, 'event_date': None,
            'days': [], 'error': 'fail',
        }
        url, _t, _d, _days, err = resolve_orgeo_url('https://orgeo.ru/live/#/aliasname')
        self.assertEqual(url, '')
        self.assertIn('fail', err)

    @patch('protocols.services.orgeo_browser._fetch_orgeo_meta')
    def test_resolve_api_no_event_id(self, mock_meta):
        mock_meta.return_value = {
            'event_id': None, 'event_name': None, 'event_date': None,
            'days': [], 'error': None,
        }
        url, _t, _d, _days, err = resolve_orgeo_url('https://orgeo.ru/live/#/aliasname')
        self.assertEqual(url, '')
        self.assertIn('event_id', err)


class FetchOrgeoTablesTests(SimpleTestCase):
    @patch('protocols.services.orgeo_browser.resolve_orgeo_url')
    def test_resolve_error_propagates(self, mock_resolve):
        mock_resolve.return_value = ('', None, None, [], 'boom')
        tables, err = fetch_orgeo_tables('https://orgeo.ru/broken')
        self.assertIsNone(tables)
        self.assertEqual(err, 'boom')

    @patch('protocols.services.orgeo_browser._make_session')
    @patch('protocols.services.orgeo_browser.resolve_orgeo_url')
    def test_multi_day_flow(self, mock_resolve, mock_make_session):
        mock_resolve.return_value = (
            'https://orgeo.ru/online/49864', 'N', '2026-04-01',
            [{'sub_id': '2', 'name': 'd', 'date': '2026-04-01'}], None,
        )

        header_html = "<a href='/online/finish/49864?s=2&tz=3&d=Ж10'>Ж10</a>"
        group_html = "<html>Фамилия <table>x</table></html>"

        def get(url, params=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if 'online/top' in url:
                resp.text = header_html
            else:
                resp.text = group_html
            return resp

        session = MagicMock()
        session.get.side_effect = get
        mock_make_session.return_value = session

        tables, err = fetch_orgeo_tables('https://orgeo.ru/online/49864')
        self.assertIsNone(err)
        self.assertEqual(len(tables), 1)
        self.assertIn('Фамилия', tables[0])

    @patch('protocols.services.orgeo_browser._make_session')
    @patch('protocols.services.orgeo_browser.resolve_orgeo_url')
    def test_frameset_fallback_path(self, mock_resolve, mock_make_session):
        # нет дней → фолбэк через frameset
        mock_resolve.return_value = (
            'https://orgeo.ru/online/49864', None, None, [], None,
        )
        frameset = (
            '<frameset>'
            '<frame name="header" src="/online/top?id=49864&s=1"/>'
            '</frameset>'
        )
        header = "<a href='/online/finish/49864?s=1&tz=3&d=Ж10'>Ж10</a>"
        group = '<html>Фамилия<table>x</table></html>'

        responses = {
            'https://orgeo.ru/online/49864': frameset,
            'https://orgeo.ru/online/top?id=49864&s=1': header,
            'https://orgeo.ru/online/finish/49864?s=1&tz=3&d=Ж10': group,
        }

        def get(url, params=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.text = responses.get(url, '')
            return resp

        session = MagicMock()
        session.get.side_effect = get
        mock_make_session.return_value = session

        tables, err = fetch_orgeo_tables('https://orgeo.ru/online/49864')
        self.assertIsNone(err)
        self.assertEqual(len(tables), 1)

    @patch('protocols.services.orgeo_browser._make_session')
    @patch('protocols.services.orgeo_browser.resolve_orgeo_url')
    def test_frameset_no_header_but_has_table(self, mock_resolve, mock_make_session):
        mock_resolve.return_value = (
            'https://orgeo.ru/online/1', None, None, [], None,
        )
        frameset = '<html>Фамилия<table></table></html>'
        responses = {'https://orgeo.ru/online/1': frameset}

        def get(url, params=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.text = responses.get(url, '')
            return resp

        session = MagicMock()
        session.get.side_effect = get
        mock_make_session.return_value = session

        tables, err = fetch_orgeo_tables('https://orgeo.ru/online/1')
        self.assertIsNone(err)
        self.assertEqual(tables, [frameset])


class FetchOrgeoWithBrowserTests(SimpleTestCase):
    @patch('protocols.services.orgeo_browser.resolve_orgeo_url')
    def test_resolve_error(self, mock_resolve):
        mock_resolve.return_value = ('', None, None, [], 'boom')
        html, err = fetch_orgeo_with_browser('https://x')
        self.assertIsNone(html)
        self.assertEqual(err, 'boom')

    @patch('protocols.services.orgeo_browser._make_session')
    @patch('protocols.services.orgeo_browser.resolve_orgeo_url')
    def test_direct_table_page(self, mock_resolve, mock_make_session):
        mock_resolve.return_value = ('https://orgeo.ru/online/1', None, None, [], None)
        resp = MagicMock()
        resp.text = '<html>Фамилия</html>'
        resp.raise_for_status = MagicMock()
        session = MagicMock()
        session.get.return_value = resp
        mock_make_session.return_value = session
        html, err = fetch_orgeo_with_browser('https://orgeo.ru/online/1')
        self.assertIsNone(err)
        self.assertIn('Фамилия', html)
