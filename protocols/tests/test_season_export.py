# -*- coding: utf-8 -*-
"""Тесты services/season_export.py."""
from datetime import date
from io import BytesIO

from django.test import TestCase
from openpyxl import load_workbook

from protocols.services.season_export import (
    build_places_data,
    build_points_data,
    build_points_data_from_protocols,
    export_season_excel,
    export_season_excel_by_date_range,
    export_season_excel_by_period,
    get_protocols_for_date_range,
    get_protocols_for_period,
    get_protocols_for_ranking,
)
from protocols.models import Event
from .factories import (
    make_championship_level,
    make_event,
    make_known_participant,
    make_protocol,
    make_ranking,
    make_ranking_entry,
    make_result,
)


class GetProtocolsTests(TestCase):
    def setUp(self):
        self.event_spring = make_event(
            title='Весна 2026', date=date(2026, 4, 1), season=Event.SEASON_SPRING
        )
        self.event_autumn = make_event(
            title='Осень 2026', date=date(2026, 10, 1), season=Event.SEASON_AUTUMN
        )
        self.p_spring = make_protocol(event=self.event_spring)
        self.p_autumn = make_protocol(event=self.event_autumn)
        # Протокол без события, но с event_date
        self.p_free = make_protocol(event=None, event_date=date(2026, 4, 15))

    def test_for_period_spring(self):
        qs = get_protocols_for_period(2026, Event.SEASON_SPRING)
        self.assertIn(self.p_spring, qs)
        self.assertNotIn(self.p_autumn, qs)

    def test_for_date_range_covers_both_event_and_free(self):
        qs = get_protocols_for_date_range(date(2026, 3, 1), date(2026, 5, 31))
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(self.p_spring.id, ids)
        self.assertIn(self.p_free.id, ids)
        self.assertNotIn(self.p_autumn.id, ids)

    def test_for_ranking_dates(self):
        r = make_ranking(date_from=date(2026, 3, 1), date_to=date(2026, 5, 31))
        qs = get_protocols_for_ranking(r)
        self.assertIn(self.p_spring, qs)
        self.assertNotIn(self.p_autumn, qs)

    def test_for_ranking_year_season(self):
        r = make_ranking(
            date_from=None, date_to=None,
            year=2026, season=Event.SEASON_AUTUMN,
        )
        qs = get_protocols_for_ranking(r)
        self.assertIn(self.p_autumn, qs)

    def test_for_ranking_empty(self):
        r = make_ranking(
            date_from=None, date_to=None, year=None, season=''
        )
        self.assertEqual(list(get_protocols_for_ranking(r)), [])


class BuildDataTests(TestCase):
    def setUp(self):
        self.event = make_event(title='E', date=date(2026, 4, 1), season=Event.SEASON_SPRING)
        self.lvl = make_championship_level(coefficient=2.0)
        self.p = make_protocol(event=self.event, championship_level=self.lvl)
        make_result(
            self.p, group_name='М14', last_name='Иванов', first_name='Иван',
            place='1', place_out_of=3, points=1000,
        )
        make_result(
            self.p, group_name='М14', last_name='Петров', first_name='Пётр',
            place='2', place_out_of=3, points=500,
        )
        make_result(
            self.p, group_name='М14', last_name='Сидоров', first_name='Сидр',
            place='3', place_out_of=3, points=100,
        )

    def test_build_places_data(self):
        people, data, starts = build_places_data([self.p])
        self.assertEqual(len(people), 3)
        self.assertEqual(len(starts), 1)
        triple = data[('Иванов', 'Иван')][(self.p.id, 1)]
        self.assertEqual(triple[0], '1')

    def test_build_places_data_excludes_veterans(self):
        make_known_participant(last_name='Иванов', first_name='Иван', veteran=True)
        people, _d, _s = build_places_data([self.p])
        self.assertNotIn(('Иванов', 'Иван'), people)
        self.assertEqual(len(people), 2)

    def test_build_points_from_protocols_applies_coefficient(self):
        data = build_points_data_from_protocols([self.p])
        iv = next(x for x in data if x[0] == 'Иванов')
        # 1000 * 2.0 = 2000
        self.assertEqual(iv[2], 2000.0)

    def test_build_points_from_protocols_empty(self):
        self.assertEqual(build_points_data_from_protocols([]), [])

    def test_build_points_data_uses_ranking_entries(self):
        r = make_ranking()
        kp = make_known_participant(last_name='Иванов', first_name='Иван')
        make_ranking_entry(r, kp, points=42.0)
        rows = build_points_data(r)
        self.assertEqual(rows, [('Иванов', 'Иван', 42.0)])

    def test_build_points_data_excludes_veteran(self):
        r = make_ranking()
        kp = make_known_participant(last_name='V', first_name='W', veteran=True)
        make_ranking_entry(r, kp, points=99.0)
        self.assertEqual(build_points_data(r), [])


class MultipleStartsPerProtocolTests(TestCase):
    def test_exception_splits_by_group(self):
        """Особый случай: фамилия из GROUP_SPLIT_EXCEPTIONS встречается в >1 группах."""
        event = make_event()
        p = make_protocol(event=event)
        make_result(p, group_name='G1', last_name='Поздновский', first_name='Сергей')
        make_result(p, group_name='G2', last_name='Поздновский', first_name='Сергей')
        people, _d, _s = build_places_data([p])
        # должно быть 2 варианта отображаемого имени
        names = {fn for _ln, fn in people}
        self.assertTrue(any('G1' in n for n in names))
        self.assertTrue(any('G2' in n for n in names))


class ExportExcelTests(TestCase):
    def _make_filled(self):
        event = make_event(date=date(2026, 4, 1), season=Event.SEASON_SPRING)
        p = make_protocol(event=event)
        make_result(p, last_name='Иванов', first_name='Иван', place='1', points=1000)
        r = make_ranking(date_from=date(2026, 3, 1), date_to=date(2026, 5, 31))
        kp = make_known_participant(last_name='Иванов', first_name='Иван')
        make_ranking_entry(r, kp, points=1000.0)
        return r

    def test_export_season_excel_returns_bytes(self):
        r = self._make_filled()
        content = export_season_excel(r)
        self.assertIsInstance(content, (bytes, bytearray))
        wb = load_workbook(BytesIO(content))
        self.assertIn('Места', wb.sheetnames)
        self.assertIn('Баллы', wb.sheetnames)

    def test_export_by_period(self):
        self._make_filled()
        content = export_season_excel_by_period(2026, Event.SEASON_SPRING)
        wb = load_workbook(BytesIO(content))
        self.assertIn('Места', wb.sheetnames)

    def test_export_by_date_range(self):
        self._make_filled()
        content = export_season_excel_by_date_range(
            date(2026, 3, 1), date(2026, 5, 31)
        )
        wb = load_workbook(BytesIO(content))
        self.assertIn('Места', wb.sheetnames)

    def test_export_with_no_data(self):
        r = make_ranking(date_from=date(2026, 3, 1), date_to=date(2026, 5, 31))
        content = export_season_excel(r)
        wb = load_workbook(BytesIO(content))
        self.assertEqual(wb['Места'].max_row, 1)  # только заголовок
