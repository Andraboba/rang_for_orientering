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

    def test_zero_time_is_not_a_result(self):
        self.assertEqual(_calc_points(0, 100), 0)

    def test_points_never_exceed_1000(self):
        self.assertEqual(_calc_points(50, 100), 1000)


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

    def test_star_shows_everyone_despite_known_base(self):
        """'*' — общий для всех парсеров способ показать всех участников."""
        r = parse_orgeo(
            ORGEO_HTML_TEMPLATE,
            base_pairs=[('Иванов', 'Иван')],
            team_name='*',
        )
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


# Первенство России, Волгоград: между «Командой» и результатом стоит колонка
# «Онлайн-КП», а сам результат называется «FINISH».
ORGEO_ONLINE_KP_HTML = """
<h2>Группа Ж18</h2>
<table cellspacing=0>
<tr><th>П/п</th><th>№</th><th>Фамилия, имя</th><th>Команда</th><th>Онлайн-КП</th><th>FINISH</th><th>Отставан</th><th>Место</th><th>Старт</th></tr>
<tr><td>1</td><td>163</td><td>Петрова Дарья</td><td><a href='/online/finish/53967?s=2&tz=3&t=Краснодарский край'>Краснодарский край</a></td><td></td><td class=b>00:35:56</td><td align=right>+00:00</td><td>1</td><td align=right>11:03:00</td></tr>
<tr><td>10</td><td>159</td><td>Повышева Анастасия</td><td><a href='/online/finish/53967?s=2&tz=3&t=г. Москва'>г. Москва</a></td><td></td><td class=b>00:41:26</td><td align=right>+05:30</td><td>10</td><td align=right>10:59:00</td></tr>
</table>
"""

ORGEO_ONLINE_KP_RELAY_HTML = """
<h2>Группа Ж18</h2>
<table cellspacing=0>
<tr><th>П/п</th><th>№</th><th>Фамилия, имя</th><th>Команда</th><th>Онлайн-КП</th><th>FINISH</th><th>Отставан</th><th>Старт</th></tr>
<tr><th colspan=8 class=relay></th></tr>
<tr><td>1</td><td>77.1</td><td>Евсикова Кристина</td><td>Московская обл.</td><td></td><td class=b>00:27:06</td><td align=right></td><td align=right>10:15:00</td></tr>
<tr><td>1</td><td>77.2</td><td>Олейниченкова Анна</td><td>Московская обл.</td><td></td><td class=b>00:55:24</td><td align=right></td><td align=right>10:15:00</td></tr>
<tr><td>1</td><td>77.3</td><td>Олейниченкова Светлана</td><td>Московская обл.</td><td></td><td class=b>01:21:41</td><td align=right>+00:00</td><td align=right>10:15:00</td></tr>
<tr><th colspan=8 class=relay></th></tr>
<tr><td>9</td><td>75.1</td><td>Банникова Ксения</td><td>г. Москва</td><td></td><td class=b>00:28:32</td><td align=right></td><td align=right>10:15:00</td></tr>
<tr><td>9</td><td>75.2</td><td>Петрухина Мария</td><td>г. Москва</td><td></td><td class=b>01:11:41</td><td align=right></td><td align=right>10:15:00</td></tr>
<tr><td>9</td><td>75.3</td><td>Повышева Анастасия</td><td>г. Москва</td><td></td><td class=b>01:37:17</td><td align=right>+15:36</td><td align=right>10:15:00</td></tr>
</table>
"""

# Кросс-эстафета, Москва: колонки «Место» нет, П/п — порядковый номер.
# Команда 5 снята за непр. отметку на третьем этапе, хотя последний этап
# финишировал быстрее лидера.
ORGEO_RELAY_DSQ_HTML = """
<h2>Группа МЖ20</h2>
<table cellspacing=0>
<tr><th>П/п</th><th>№</th><th>Фамилия, имя</th><th>Команда</th><th>Результат</th><th>Отставан</th><th>Старт</th></tr>
<tr><th colspan=8 class=relay></th></tr>
<tr><td>1</td><td>1104</td><td>Рубени Даниил</td><td>МосКомпас</td><td class=b>00:19:54,0</td><td align=right></td><td align=right>11:35:00</td></tr>
<tr><td>1</td><td>2104</td><td>Николич Никита</td><td>МосКомпас</td><td class=b>00:44:28,0</td><td align=right></td><td align=right>11:54:54</td></tr>
<tr><td>1</td><td>3104</td><td>Марченко Нина</td><td>О-Фрязино</td><td class=b>01:08:03,0</td><td align=right></td><td align=right>12:19:28</td></tr>
<tr><td>1</td><td>4104</td><td>Вдовина Софья</td><td>О-Фрязино</td><td class=b>01:29:49,0</td><td align=right>+00:00</td><td align=right>12:43:03</td></tr>
<tr><th colspan=8 class=relay></th></tr>
<tr><td>4</td><td>1105</td><td>Пахнин Игорь</td><td>МОСКОМПАС-ОРИЕНТА</td><td class=b>00:21:17,0</td><td align=right></td><td align=right>11:35:00</td></tr>
<tr><td>4</td><td>2105</td><td>Редькин Роман</td><td>МОСКОМПАС-ОРИЕНТА</td><td class=b>00:43:25,0</td><td align=right></td><td align=right>11:56:17</td></tr>
<tr><td>4</td><td>3105</td><td>Парыгина Елизавета</td><td>МОСКОМПАС-ОРИЕНТА</td><td class=b>01:07:41,0</td><td align=right></td><td align=right>12:18:25</td></tr>
<tr><td>4</td><td>4105</td><td>Неяглова Алина</td><td>МОСКОМПАС-ОРИЕНТА</td><td class=b>01:33:22,0</td><td align=right>+03:33</td><td align=right>12:42:41</td></tr>
<tr><th colspan=8 class=relay></th></tr>
<tr><td>5</td><td>1103</td><td>Юрчук Андрей</td><td>СК Фотон</td><td class=b>00:20:37,0</td><td align=right></td><td align=right>11:35:00</td></tr>
<tr><td>5</td><td>2103</td><td>Соколов Егор</td><td>СК Фотон</td><td class=b>00:42:39,0</td><td align=right></td><td align=right>11:55:37</td></tr>
<tr><td>5</td><td>3103</td><td>Хохлова Екатерина</td><td>Ориента-СКРУМ</td><td class=b>непр.отмет.</td><td align=right></td><td align=right>12:17:39</td></tr>
<tr><td>5</td><td>4103</td><td>Тайманова Светлана</td><td>Ориента-СКРУМ</td><td class=b>01:28:25,0</td><td align=right></td><td align=right>12:41:24</td></tr>
</table>
"""


class ColumnsFromHeaderTests(SimpleTestCase):
    """Номера колонок берутся из заголовка таблицы, а не по умолчанию."""

    def test_finish_column_after_online_kp(self):
        r = parse_orgeo(ORGEO_ONLINE_KP_HTML, base_pairs=[('Повышева', 'Анастасия')])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['place'], '10')
        # (2 - 41:26 / 35:56) * 1000
        self.assertEqual(r[0]['points'], 846)
        self.assertEqual(r[0]['group_name'], 'Ж18')

    def test_relay_total_time_from_finish_column(self):
        r = parse_orgeo(ORGEO_ONLINE_KP_RELAY_HTML, base_pairs=[('Повышева', 'Анастасия')])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['place'], '9')
        # (2 - 1:37:17 / 1:21:41) * 1000
        self.assertEqual(r[0]['points'], 809)


class RelayDisqualificationTests(SimpleTestCase):
    def test_team_with_mispunched_leg_gets_no_points(self):
        r = parse_orgeo(ORGEO_RELAY_DSQ_HTML, base_pairs=[], team_name='Фотон')
        self.assertEqual(
            {(x['last_name'], x['points']) for x in r},
            {('Юрчук', 0), ('Соколов', 0)},
        )
        self.assertTrue(all(x['place'] == 'непр.отмет.' for x in r))

    def test_valid_teams_are_scored_by_team_time(self):
        r = parse_orgeo(ORGEO_RELAY_DSQ_HTML, base_pairs=[], team_name='*')
        points = {x['last_name']: x['points'] for x in r}
        self.assertEqual(points['Рубени'], 1000)
        # (2 - 1:33:22 / 1:29:49) * 1000
        self.assertEqual(points['Неяглова'], 960)
        self.assertEqual(points['Тайманова'], 0)


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
