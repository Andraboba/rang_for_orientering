# -*- coding: utf-8 -*-
"""Тесты моделей приложения protocols."""
from datetime import date

from django.db import IntegrityError
from django.test import TestCase

from protocols.models import (
    ChampionshipLevel,
    Event,
    KnownParticipant,
    Protocol,
    ProtocolGroupLevel,
    ProtocolResult,
    SeasonRanking,
    SeasonRankingEntry,
)
from protocols.services.season_export import build_points_data_from_protocols
from .factories import (
    make_championship_level,
    make_event,
    make_known_participant,
    make_protocol,
    make_ranking,
    make_ranking_entry,
    make_result,
)


class EventModelTests(TestCase):
    def test_str_with_date(self):
        ev = make_event(title='Весна', date=date(2026, 3, 1))
        self.assertEqual(str(ev), 'Весна (2026-03-01)')

    def test_str_without_date(self):
        ev = make_event(title='Без даты', date=None)
        self.assertEqual(str(ev), 'Без даты')

    def test_default_importance(self):
        ev = make_event()
        self.assertEqual(ev.importance, 1.0)

    def test_ordering(self):
        e1 = make_event(title='A', date=date(2020, 1, 1))
        e2 = make_event(title='B', date=date(2026, 1, 1))
        events = list(Event.objects.all())
        self.assertEqual(events[0], e2)
        self.assertEqual(events[1], e1)

    def test_season_choices(self):
        ev = make_event(season=Event.SEASON_AUTUMN)
        self.assertEqual(ev.get_season_display(), 'Осень')


class ChampionshipLevelTests(TestCase):
    def test_str(self):
        lvl = make_championship_level(name='ПМ', coefficient=1.5)
        self.assertIn('ПМ', str(lvl))
        self.assertIn('1.5', str(lvl))

    def test_ordering_by_order_then_name(self):
        # Миграция 0007 создаёт дефолтные уровни — очищаем для чистоты теста.
        ChampionshipLevel.objects.all().delete()
        a = make_championship_level(name='B', order=2)
        b = make_championship_level(name='A', order=1)
        c = make_championship_level(name='C', order=1)
        lst = list(ChampionshipLevel.objects.all())
        self.assertEqual(lst, [b, c, a])


class ProtocolModelTests(TestCase):
    def test_str_uses_title(self):
        p = make_protocol(title='Кубок Москвы')
        self.assertEqual(str(p), 'Кубок Москвы')

    def test_str_fallback_to_url(self):
        p = make_protocol(title='', source_url='https://x.y/z')
        self.assertEqual(str(p), 'https://x.y/z')

    def test_str_fallback_to_pk(self):
        p = make_protocol(title='', source_url='')
        self.assertEqual(str(p), f'Протокол #{p.pk}')

    def test_effective_coefficient_uses_level(self):
        lvl = make_championship_level(coefficient=2.0)
        p = make_protocol(championship_level=lvl, importance=1.0)
        self.assertEqual(p.effective_coefficient, 2.0)

    def test_effective_coefficient_fallback_to_importance(self):
        p = make_protocol(championship_level=None, importance=1.5)
        self.assertEqual(p.effective_coefficient, 1.5)

    def test_effective_coefficient_default_when_zero(self):
        p = make_protocol(championship_level=None, importance=0.0)
        self.assertEqual(p.effective_coefficient, 1.0)

    def test_delete_cascades_results(self):
        p = make_protocol()
        make_result(p)
        self.assertEqual(ProtocolResult.objects.count(), 1)
        p.delete()
        self.assertEqual(ProtocolResult.objects.count(), 0)

    def test_level_set_null_on_delete(self):
        lvl = make_championship_level()
        p = make_protocol(championship_level=lvl)
        lvl.delete()
        p.refresh_from_db()
        self.assertIsNone(p.championship_level)


class KnownParticipantTests(TestCase):
    def test_str(self):
        kp = make_known_participant(last_name='Петров', first_name='Пётр')
        self.assertEqual(str(kp), 'Петров Пётр')

    def test_unique_together(self):
        make_known_participant(last_name='A', first_name='B')
        with self.assertRaises(IntegrityError):
            KnownParticipant.objects.create(last_name='A', first_name='B')

    def test_veteran_default_false(self):
        kp = make_known_participant()
        self.assertFalse(kp.veteran)


class ProtocolResultTests(TestCase):
    def test_str_format(self):
        p = make_protocol()
        r = make_result(p, place='2', place_out_of=7, last_name='И', first_name='И', points=500)
        self.assertIn('2', str(r))
        self.assertIn('7', str(r))
        self.assertIn('500', str(r))

    def test_weighted_points_with_level(self):
        lvl = make_championship_level(coefficient=2.0)
        p = make_protocol(championship_level=lvl)
        r = make_result(p, points=500)
        self.assertEqual(r.weighted_points, 1000.0)

    def test_weighted_points_without_level(self):
        p = make_protocol(importance=1.5)
        r = make_result(p, points=400)
        self.assertEqual(r.weighted_points, 600.0)

    def test_weighted_points_without_protocol_field(self):
        # Ветка когда protocol_id is None
        r = ProtocolResult(last_name='X', first_name='Y', points=123)
        self.assertEqual(r.weighted_points, 123.0)


class GroupLevelOverrideTests(TestCase):
    """
    Статус старта может зависеть от группы: в Зеленограде первенство России
    было только у МЖ14 и МЖ16, остальные группы бежали всероссийские.
    """

    def setUp(self):
        self.russia = make_championship_level(name='Первенство России', coefficient=1.5)
        self.russian_cup = make_championship_level(name='Всероссийские', coefficient=1.25)
        self.protocol = make_protocol(championship_level=self.russian_cup)
        ProtocolGroupLevel.objects.create(
            protocol=self.protocol,
            group_name='Юноши до 15 лет',
            championship_level=self.russia,
        )

    def test_group_with_override_uses_its_level(self):
        self.assertEqual(
            self.protocol.coefficient_for_group('Юноши до 15 лет'), 1.5
        )

    def test_group_name_is_case_insensitive(self):
        self.assertEqual(
            self.protocol.coefficient_for_group('юноши до 15 ЛЕТ'), 1.5
        )

    def test_other_groups_use_protocol_level(self):
        self.assertEqual(self.protocol.coefficient_for_group('Ж21'), 1.25)
        self.assertEqual(self.protocol.coefficient_for_group(''), 1.25)

    def test_weighted_points_differ_by_group(self):
        junior = make_result(
            self.protocol, group_name='Юноши до 15 лет', points=1000
        )
        adult = make_result(self.protocol, group_name='Ж21', points=1000)
        self.assertEqual(junior.weighted_points, 1500.0)
        self.assertEqual(adult.weighted_points, 1250.0)

    def test_protocol_without_overrides_is_unaffected(self):
        plain = make_protocol(championship_level=self.russian_cup)
        self.assertEqual(plain.coefficient_for_group('Юноши до 15 лет'), 1.25)

    def test_season_points_use_group_coefficient(self):
        make_result(self.protocol, group_name='Юноши до 15 лет',
                    last_name='Юнош', first_name='Иван', points=1000)
        make_result(self.protocol, group_name='Ж21',
                    last_name='Взрослая', first_name='Анна', points=1000)
        rows = dict(
            (last_name, points)
            for last_name, _first_name, points in
            build_points_data_from_protocols([self.protocol])
        )
        self.assertEqual(rows['Юнош'], 1500.0)
        self.assertEqual(rows['Взрослая'], 1250.0)


class SeasonRankingTests(TestCase):
    def test_str_with_dates(self):
        r = make_ranking(date_from=date(2026, 3, 1), date_to=date(2026, 5, 31))
        self.assertIn('2026-03-01', str(r))
        self.assertIn('2026-05-31', str(r))

    def test_str_without_dates(self):
        r = make_ranking(date_from=None, date_to=None, year=2025, season=Event.SEASON_AUTUMN)
        self.assertIn('2025', str(r))
        self.assertIn('Осень', str(r))

    def test_entry_str(self):
        r = make_ranking()
        kp = make_known_participant()
        entry = make_ranking_entry(r, kp, points=42.5)
        self.assertIn('42.5', str(entry))

    def test_entry_unique_together(self):
        r = make_ranking()
        kp = make_known_participant()
        make_ranking_entry(r, kp)
        with self.assertRaises(IntegrityError):
            SeasonRankingEntry.objects.create(ranking=r, participant=kp, points=1.0)
