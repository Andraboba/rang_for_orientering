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


# Кросс-лонг (IX этап Спортмастер PRO): после места идёт колонка «Вып» с разрядом,
# у снятых результат пустой.
CROSS_LONG_HTML = """<h2>Ж12, 10 КП, 3 км</h2>
<pre>
<u><b>№п/п Фамилия, имя              Коллектив            Квал Номер ГР   Результат Место Вып  Прим </b></u>
   1 Гордиенко Мария           МКСШОР  "Восток"     I    1010 2015 00:19:31      1  I
   2 Антонова Дарья            ЦСиО Самбо-70        I    1009 2015 00:21:31      2  I
   6 Нестерова Ксения          ЦСиО Самбо-70        I    1014 2014 00:28:21      6  III
  19 Харламова Любава          МКСШОР  "Восток"     III  1002 2014
</pre>
"""

# Покровская горизонталь: эстафеты двух участников и личная группа О2
# (с колонкой «ШТРАФ») в одном протоколе.
POKROVSKAYA_HTML = """<h1>Традиционные соревнования "Покровская Горизонталь"<br>
ПРОТОКОЛ РЕЗУЛЬТАТОВ ЭСТАФЕТЫ</h1>
<h2>Ж10, 7 КП</h2>
<pre><b>     №   Фамилия, имя              Коллектив            Квал    Время      Время   Прим  Место
                                                              на этапе   команды       </b>
<b>1  </b>
    41  Темкина Виктория          СК Фотон                    00:24:38   00:24:38         1
    42  Кукина Екатерина          СК Фотон             IIю    00:18:30   00:43:08         1
<b>2  </b>
    51  Ольшина Павла             Искатель                    00:24:36   00:24:36         2
    52  Ольшина Кира              Искатель                    00:21:20   00:45:56         2
<b>6  </b>
  3061  Федорова Ольга            Ориента-Голд         IIIю  3.13.12.2
  3062  Музыченко Мария           СК Фотон             IIIю  3.13.12.2

Главный судья                                              Лукин А., СС2К
</pre>
<h2>Ж12, 10 КП</h2>
<pre><b>     №   Фамилия, имя              Коллектив            Квал    Время      Время   Прим  Место
                                                              на этапе   команды       </b>
<b>1  </b>
   191  Мартынова Анна            CRAFT-ОРИЕНТА        I      00:19:03   00:19:03
   192  Зимакова Екатерина        Ориента-СКРУМ        I      00:20:40   00:39:43
<b>2  </b>
   101  Провоторова Дарья         О-Фрязино            I      00:19:30   00:19:30         2
   102  Гордиенко Мария           О-Фрязино            I      00:20:56   00:40:26         2
<b>3  </b>
   171  Нестерова Ксения          СК Фотон             I      00:22:28   00:22:28         3
   172  Антонова Дарья            СК Фотон             I      00:20:45   00:43:13         3
</pre>
<h2>М10, 8 КП</h2>
<pre><b>     №   Фамилия, имя              Коллектив            Квал    Время      Время   Прим  Место
                                                              на этапе   команды       </b>
<b>1  </b>
  1051  Трошин Илья               CRAFT-ОРИЕНТА        IIIю   00:22:06   00:22:06         1
  1052  Наумов Дмитрий            CRAFT-ОРИЕНТА        IIIю   00:17:49   00:39:55         1
<b>5  </b>
  1151  Гринькин Андрей           СК Фотон                    00:19:50   00:19:50         5
  1152  Абрамов Иван              СК Фотон             IIIю   00:29:18   00:27:38         5
</pre>
<h2>О2, 13 КП</h2>
<pre>
<u><b>№п/п Фамилия, имя              Коллектив            Квал Номер ГР    ШТРАФ РезультатОтставан    Место Прим </b></u>
   1 Асылхузин Ринат           Королев, лично            6090 1963  00:00 00:23:06   +00:00      1
   3 Муротьян Светлана         Лосиный остров            6074 1986  00:00 00:27:15   +04:09   =  2
  10 Левич Оксана              СК Фотон                  6070 1975  00:00 00:35:49   +12:43     10
</pre>
"""

# Эстафета памяти Горюнова: четыре этапа на двоих, у строки есть номер этапа.
GORUNOV_HTML = """<h2>М+Ж</h2>
<pre><b>     №   Фамилия, имя              Коллектив            Квал  ГР     Время      Время   Прим  Место
                                                                   на этапе   команды       </b>
<b>1  </b>
  100 1 Цветков Максим            M&M                  МС   1991   00:18:13   00:18:13         1
  100 2 Трубкина Марина           M&M                  МС   1996   00:18:41   00:36:54         1
  100 3 Цветков Максим            M&M                  МС   1991   00:18:12   00:55:06         1
  100 4 Трубкина Марина           M&M                  МС   1996   00:21:36   01:16:42         1
<b>11 </b>
  108 1 Юрчук Андрей              СК Фотон             КМС  2006   00:27:15   00:27:15        11
  108 2 Повышева Анастасия        СК Фотон             КМС  2009   00:18:43   00:45:58        11
  108 3 Юрчук Андрей              СК Фотон             КМС  2006   00:19:43   01:05:41        11
  108 4 Повышева Анастасия        СК Фотон             КМС  2009   00:20:58   01:26:39        11
</pre>
"""


class PreColumnsTests(SimpleTestCase):
    """Результат ищется по виду значения, а не по позиции в строке."""

    def test_place_before_qualification_column(self):
        base = [('Антонова', 'Дарья'), ('Нестерова', 'Ксения')]
        r = parse_and_process(CROSS_LONG_HTML, False, 'Фотон', base)
        by_name = {x['last_name']: x for x in r}
        self.assertEqual(by_name['Антонова']['place'], '2')
        # (2 - 21:31 / 19:31) * 1000
        self.assertEqual(by_name['Антонова']['points'], 897)
        self.assertEqual(by_name['Нестерова']['points'], 547)
        self.assertEqual(by_name['Нестерова']['group_name'], 'Ж12')
        self.assertEqual(by_name['Нестерова']['place_out_of'], 4)

    def test_line_without_result_is_not_scored(self):
        r = parse_and_process(CROSS_LONG_HTML, False, '', [('Харламова', 'Любава')])
        self.assertEqual(r[0]['place'], 'снят')
        self.assertEqual(r[0]['points'], 0)

    def test_penalty_column_is_skipped(self):
        r = parse_and_process(POKROVSKAYA_HTML, False, '', [('Левич', 'Оксана')])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['group_name'], 'О2')
        self.assertEqual(r[0]['place'], '10')
        # (2 - 35:49 / 23:06) * 1000
        self.assertEqual(r[0]['points'], 449)

    def test_tie_place(self):
        r = parse_and_process(POKROVSKAYA_HTML, False, '', [('Муротьян', 'Светлана')])
        self.assertEqual(r[0]['place'], '2')


class PreRelayTests(SimpleTestCase):
    """Эстафеты WinOrient: строка «<b>N </b>» перед командой, по строке на этап."""

    def _by_name(self, html, team_name='Фотон', base=None):
        results = parse_and_process(html, False, team_name, base if base is not None else [])
        return {(x['last_name'], x['first_name']): x for x in results}

    def test_team_members_share_team_points(self):
        r = self._by_name(POKROVSKAYA_HTML)
        for name in (('Темкина', 'Виктория'), ('Кукина', 'Екатерина')):
            self.assertEqual(r[name]['points'], 1000)
            self.assertEqual(r[name]['place'], '1')
            self.assertEqual(r[name]['place_out_of'], 3)
            self.assertEqual(r[name]['group_name'], 'Ж10')
            self.assertIn('(эстафета)', r[name]['raw_line'])

    def test_disqualified_team_gets_zero(self):
        r = self._by_name(POKROVSKAYA_HTML)
        self.assertEqual(r[('Музыченко', 'Мария')]['place'], 'снят')
        self.assertEqual(r[('Музыченко', 'Мария')]['points'], 0)

    def test_leader_is_fastest_team_even_out_of_competition(self):
        # Команда вне конкурса (без места) быстрее всех, поэтому у следующих места 2 и 3.
        r = self._by_name(POKROVSKAYA_HTML)
        # (2 - 43:13 / 39:43) * 1000
        self.assertEqual(r[('Нестерова', 'Ксения')]['points'], 911)
        self.assertEqual(r[('Нестерова', 'Ксения')]['place'], '3')

    def test_misprinted_team_time_does_not_exceed_leader(self):
        # Время команды 00:27:38 напечатано с ошибкой: сумма этапов — 00:49:08.
        r = self._by_name(POKROVSKAYA_HTML)
        self.assertEqual(r[('Абрамов', 'Иван')]['points'], 769)

    def test_incomplete_team_is_not_the_leader(self):
        # Вышел только первый участник, а время команды у него уже напечатано.
        html = POKROVSKAYA_HTML.replace(
            '<b>5  </b>',
            '<b>4  </b>\n'
            '  1121  Чиненов Данила            Москомпас-Ориента Ма        00:10:00   00:10:00\n'
            '<b>5  </b>',
        )
        r = self._by_name(html, base=[('Чиненов', 'Данила')])
        self.assertEqual(r[('Чиненов', 'Данила')]['place'], 'снят')
        self.assertEqual(r[('Абрамов', 'Иван')]['points'], 769)

    def test_individual_group_in_relay_protocol(self):
        r = self._by_name(POKROVSKAYA_HTML)
        self.assertEqual(r[('Левич', 'Оксана')]['points'], 449)
        self.assertNotIn('(эстафета)', r[('Левич', 'Оксана')]['raw_line'])

    def test_known_participant_from_other_club(self):
        r = self._by_name(POKROVSKAYA_HTML, team_name='', base=[('Ольшина', 'Кира')])
        self.assertEqual(list(r), [('Ольшина', 'Кира')])

    def test_stage_numbers_and_each_member_once(self):
        base = []
        r = self._by_name(GORUNOV_HTML, base=base)
        self.assertEqual(set(r), {('Юрчук', 'Андрей'), ('Повышева', 'Анастасия')})
        # (2 - 1:26:39 / 1:16:42) * 1000
        self.assertTrue(all(x['points'] == 870 for x in r.values()))
        self.assertTrue(all(x['place'] == '11' for x in r.values()))
        self.assertTrue(all(x['group_name'] == 'М+Ж' for x in r.values()))
        self.assertIn(('Юрчук', 'Андрей'), base)


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
