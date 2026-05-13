# -*- coding: utf-8 -*-
"""Тесты services/orgeo_parser.py."""
from unittest.mock import patch

from django.test import SimpleTestCase

from protocols.services.orgeo_parser import (
    _calc_points,
    _extract_group_from_h2,
    _is_disqualified,
    _seconds_from_time,
    _split_name,
    _strip_tags,
    fetch_orgeo_document,
    parse_orgeo,
)


class HelperTests(SimpleTestCase):
    def test_strip_tags(self):
        self.assertEqual(_strip_tags('<b>hi</b> <i>world</i>'), 'hi world')

    def test_strip_tags_newlines(self):
        self.assertEqual(_strip_tags('a\nb\r c'), 'a b c')

    def test_extract_group_from_h2_with_word(self):
        html = '<h2>Протокол: Группа Ж17</h2>'
        self.assertEqual(_extract_group_from_h2(html), 'Ж17')

    def test_extract_group_from_h2_fallback(self):
        html = '<h2>Ж17</h2>'
        self.assertEqual(_extract_group_from_h2(html), 'Ж17')

    def test_extract_group_from_h2_missing(self):
        self.assertEqual(_extract_group_from_h2('<p>no</p>'), '')

    def test_split_name_two(self):
        self.assertEqual(_split_name('Иванов Иван'), ('Иванов', 'Иван'))

    def test_split_name_three(self):
        self.assertEqual(_split_name('Иванов Иван Петрович'), ('Иванов', 'Иван Петрович'))

    def test_split_name_one(self):
        self.assertEqual(_split_name('Иванов'), ('Иванов', ''))

    def test_split_name_empty(self):
        self.assertEqual(_split_name(''), ('', ''))

    def test_is_disqualified_match(self):
        self.assertTrue(_is_disqualified('не старт'))
        self.assertTrue(_is_disqualified('снят'))
        self.assertTrue(_is_disqualified('сошёл'))
        self.assertTrue(_is_disqualified('превышено КВ'))

    def test_is_disqualified_no_match(self):
        self.assertFalse(_is_disqualified('00:12:34'))
        self.assertFalse(_is_disqualified(''))

    def test_seconds_from_time_comma(self):
        self.assertEqual(_seconds_from_time('00:10:30,5'), 630)

    def test_seconds_from_time_short(self):
        self.assertIsNone(_seconds_from_time(''))
        self.assertIsNone(_seconds_from_time('12'))

    def test_calc_points(self):
        self.assertEqual(_calc_points(100, 100), 1000)
        self.assertEqual(_calc_points(200, 100), 1)  # min 1
        self.assertEqual(_calc_points(100, 0), 0)


ORGEO_HTML_TEMPLATE = """
<html>
<h2>Группа М14</h2>
<table>
<tr><td>П/п</td><td>№</td><td>ФИО</td><td>Команда</td><td>Результат</td><td>Отст.</td><td>Место</td><td>Старт</td></tr>
<tr><td>1</td><td>101</td><td>Иванов Иван</td><td>СК Фотон</td><td>00:10:00</td><td>+00:00</td><td>1</td><td>11:00</td></tr>
<tr><td>2</td><td>102</td><td>Петров Пётр</td><td>СК Фотон</td><td>00:15:00</td><td>+00:05:00</td><td>2</td><td>11:02</td></tr>
<tr><td>3</td><td>103</td><td>Сидоров Сидр</td><td>Другие</td><td>не старт</td><td></td><td></td><td></td></tr>
</table>
</html>
"""


class ParseOrgeoTests(SimpleTestCase):
    def test_parse_by_team(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='Фотон')
        self.assertEqual(len(r), 2)
        names = {(x['last_name'], x['first_name']) for x in r}
        self.assertIn(('Иванов', 'Иван'), names)
        self.assertIn(('Петров', 'Пётр'), names)

    def test_parse_leader_1000(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='Фотон')
        leader = next(x for x in r if x['place'] == '1')
        self.assertEqual(leader['points'], 1000)

    def test_parse_non_leader_points_formula(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='Фотон')
        second = next(x for x in r if x['place'] == '2')
        # 15 мин против 10 мин лидера: (2 - 900/600)*1000 = 500
        self.assertEqual(second['points'], 500)

    def test_parse_filter_by_known_participants(self):
        # team_name пуст, фильтр по базе (case-insensitive)
        r = parse_orgeo(
            ORGEO_HTML_TEMPLATE,
            base_pairs=[('иванов', 'иван')],
            team_name='',
        )
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['last_name'], 'Иванов')

    def test_parse_no_filter_returns_all(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='')
        self.assertEqual(len(r), 3)

    def test_parse_disqualified_gets_zero_points(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='')
        dq = next(x for x in r if x['last_name'] == 'Сидоров')
        self.assertEqual(dq['points'], 0)
        self.assertIn('не старт', dq['place'].lower())

    def test_parse_group_name_extracted(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='')
        self.assertTrue(all(x['group_name'] == 'М14' for x in r))

    def test_parse_no_rows(self):
        self.assertEqual(parse_orgeo('<html>no tables</html>', [], ''), [])

    def test_group_size_in_raw_line(self):
        r = parse_orgeo(ORGEO_HTML_TEMPLATE, base_pairs=[], team_name='')
        self.assertTrue(all(x['place_out_of'] == 3 for x in r))


class FetchOrgeoDocumentTests(SimpleTestCase):
    @patch('protocols.services.orgeo_parser.fetch_url')
    def test_no_iframe(self, mock_fetch):
        mock_fetch.return_value = ('<html></html>', None)
        html, err = fetch_orgeo_document('https://orgeo.ru/x')
        self.assertEqual(html, '<html></html>')
        self.assertIsNone(err)
        mock_fetch.assert_called_once()

    @patch('protocols.services.orgeo_parser.fetch_url')
    def test_with_iframe(self, mock_fetch):
        main = '<html><iframe src="/inner"></iframe></html>'
        inner = '<table>inner</table>'
        mock_fetch.side_effect = [(main, None), (inner, None)]
        html, err = fetch_orgeo_document('https://orgeo.ru/x')
        self.assertEqual(html, inner)
        self.assertIsNone(err)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch('protocols.services.orgeo_parser.fetch_url')
    def test_fetch_error(self, mock_fetch):
        mock_fetch.return_value = (None, 'timeout')
        html, err = fetch_orgeo_document('https://orgeo.ru/x')
        self.assertEqual(err, 'timeout')

    @patch('protocols.services.orgeo_parser.fetch_url')
    def test_iframe_fetch_failure_returns_outer(self, mock_fetch):
        main = '<html><iframe src="/inner"></iframe></html>'
        mock_fetch.side_effect = [(main, None), (None, 'oops')]
        html, err = fetch_orgeo_document('https://orgeo.ru/x')
        self.assertEqual(html, main)
        self.assertEqual(err, 'oops')
