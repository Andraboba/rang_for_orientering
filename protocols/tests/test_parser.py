# -*- coding: utf-8 -*-
"""Тесты services/parser.py (WinOrient)."""
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase

from protocols.services import parser
from protocols.services.parser import (
    _calc_points,
    _seconds_from_time,
    extract_title,
    fetch_url,
    parse_and_process,
)


class HelperFunctionTests(SimpleTestCase):
    def test_seconds_from_time_hms(self):
        self.assertEqual(_seconds_from_time('01:02:03'), 3723)

    def test_seconds_from_time_mm_ss(self):
        self.assertEqual(_seconds_from_time('10:30'), 630)

    def test_seconds_from_time_empty(self):
        self.assertIsNone(_seconds_from_time(''))
        self.assertIsNone(_seconds_from_time('12'))

    def test_seconds_from_time_invalid(self):
        self.assertIsNone(_seconds_from_time('ab:cd:ef'))

    def test_calc_points_leader(self):
        # Бегун в то же время, что и лидер → 1000 очков
        self.assertEqual(_calc_points(100, 100), 1000)

    def test_calc_points_slower(self):
        # 1.5× времени лидера → (2 − 1.5)*1000 = 500
        self.assertEqual(_calc_points(150, 100), 500)

    def test_calc_points_zero_leader(self):
        self.assertEqual(_calc_points(100, 0), 0)

    def test_calc_points_minimum_one(self):
        # Очень медленный — минимум 1
        self.assertEqual(_calc_points(10000, 100), 1)


class ExtractTitleTests(SimpleTestCase):
    def test_extract_title_pre_h1(self):
        html = '<html><body><h1>Большой заголовок</h1></body></html>'
        self.assertEqual(extract_title(html, format_sfr=False), 'Большой заголовок')

    def test_extract_title_sfr_title_tag(self):
        html = '<html><head><title>Заголовок SFR</title></head></html>'
        self.assertEqual(extract_title(html, format_sfr=True), 'Заголовок SFR')

    def test_extract_title_not_found(self):
        self.assertEqual(extract_title('<html></html>', format_sfr=False), '')
        self.assertEqual(extract_title('<html></html>', format_sfr=True), '')


class FetchUrlTests(SimpleTestCase):
    @patch('protocols.services.parser.urlopen')
    def test_fetch_url_utf8(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = 'Привет'.encode('utf-8')
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = resp
        text, err = fetch_url('https://x/', encoding_utf8=True)
        self.assertIsNone(err)
        self.assertEqual(text, 'Привет')

    @patch('protocols.services.parser.urlopen')
    def test_fetch_url_cp1251(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = 'Привет'.encode('windows-1251')
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = resp
        text, err = fetch_url('https://x/', encoding_utf8=False)
        self.assertIsNone(err)
        self.assertEqual(text, 'Привет')

    @patch('protocols.services.parser.urlopen')
    def test_fetch_url_network_error(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError('boom')
        text, err = fetch_url('https://x/')
        self.assertIsNone(text)
        self.assertIn('boom', err)

    def test_fetch_url_blocks_file_scheme(self):
        """SSRF/LFI защита: file:// не должен загружаться."""
        text, err = fetch_url('file:///etc/passwd')
        self.assertIsNone(text)
        self.assertIn('схема', err.lower())

    def test_fetch_url_blocks_ftp_scheme(self):
        text, err = fetch_url('ftp://example.com/x')
        self.assertIsNone(text)
        self.assertIn('схема', err.lower())

    def test_fetch_url_blocks_empty_netloc(self):
        text, err = fetch_url('http:///no-host')
        self.assertIsNone(text)
        self.assertIsNotNone(err)

    @patch('protocols.services.parser.urlopen')
    def test_fetch_url_decode_error(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b'\xff\xfe\xfa'  # не декодируется как UTF-8
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: False
        mock_urlopen.return_value = resp
        text, err = fetch_url('https://x/', encoding_utf8=True)
        self.assertIsNone(text)
        self.assertIn('кодировки', err)


class ParseAndProcessSfrTests(SimpleTestCase):
    """Формат SFR: строки-таблица, строки <tr…> и <a…>."""

    def _make_sfr_line(self, place, bib, last, first, team, time_str, place_again):
        # Формат строки SFR приблизительно соответствует шаблонам из _parse_line_sfr
        return (
            "<tr style='background: #FFFFFF;'><td><nobr>"
            f"{place}</td><td><nobr>{bib}</td><td><nobr>{last}</td><td><nobr>{first}"
            f"</td><td><nobr>{team}</td><td><nobr>{time_str}</td><td><nobr>{place_again}</td></tr>"
        )

    def test_parse_sfr_team_filter_match(self):
        html = "<a href='x'>group</a>\n"
        html += self._make_sfr_line('1', '1', 'Иванов', 'Иван', 'СКФотон', '00:10:00', '1') + "\n"
        results = parse_and_process(
            html, format_sfr=True, team_name='СКФотон', base_pairs=[]
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['last_name'], 'Иванов')
        self.assertEqual(results[0]['first_name'], 'Иван')


class ParseAndProcessPreTests(SimpleTestCase):
    """Формат pre (sfr=0): строки с пробелом в начале и маркер h2."""

    def test_parse_pre_leader_gets_1000(self):
        # Очень упрощённый pre-формат: номер, фамилия, имя, коллектив, разряд,
        # номер_чипа, год, время, место
        line = ' 1 Иванов Иван СКФотон 1ю 123 2005 00:10:00 1'
        html = '<html>h2 Группа</html>\n' + line + '\n'
        results = parse_and_process(
            html, format_sfr=False, team_name='СКФотон', base_pairs=[]
        )
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r['last_name'], 'Иванов')
        self.assertEqual(r['first_name'], 'Иван')
        self.assertEqual(r['place'], '1')
        self.assertEqual(r['points'], 1000)

    def test_parse_pre_no_match_returns_empty(self):
        line = ' 1 Иванов Иван СКФотон 1ю 123 2005 00:10:00 1'
        html = line + '\n'
        results = parse_and_process(
            html, format_sfr=False, team_name='ДругаяКоманда', base_pairs=[]
        )
        self.assertEqual(results, [])

    def test_parse_pre_base_pairs_updated(self):
        line = ' 1 Иванов Иван СКФотон 1ю 123 2005 00:10:00 1'
        html = line + '\n'
        base = []
        parse_and_process(html, format_sfr=False, team_name='СКФотон', base_pairs=base)
        self.assertIn(('Иванов', 'Иван'), base)

    def test_parse_pre_disqualified(self):
        line = ' 1 Иванов Иван СКФотон 1ю 123 2005 00:10:00 3.13.12.2'
        html = line + '\n'
        results = parse_and_process(
            html, format_sfr=False, team_name='СКФотон', base_pairs=[]
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['place'], 'снят')
        self.assertEqual(results[0]['points'], 0)


class DispatchTests(SimpleTestCase):
    @patch('protocols.services.parser.process_protocol_sfr')
    @patch('protocols.services.parser.process_protocol_pre')
    def test_parse_and_process_dispatch_sfr(self, mock_pre, mock_sfr):
        mock_sfr.return_value = ['sfr']
        mock_pre.return_value = ['pre']
        out = parse_and_process('<tr></tr>', True, 't', [])
        self.assertEqual(out, ['sfr'])
        mock_sfr.assert_called_once()
        mock_pre.assert_not_called()

    @patch('protocols.services.parser.process_protocol_sfr')
    @patch('protocols.services.parser.process_protocol_pre')
    def test_parse_and_process_dispatch_pre(self, mock_pre, mock_sfr):
        mock_sfr.return_value = ['sfr']
        mock_pre.return_value = ['pre']
        out = parse_and_process(' x\nh2', False, 't', [])
        self.assertEqual(out, ['pre'])
        mock_pre.assert_called_once()
        mock_sfr.assert_not_called()
