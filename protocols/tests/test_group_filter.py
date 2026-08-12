# -*- coding: utf-8 -*-
"""Тесты services/group_filter.py — разделение полных тёзок по группам."""
from django.test import SimpleTestCase, TestCase

from protocols.services.group_filter import (
    _parse_group,
    filter_by_group_rules,
    group_matches_rules,
    participant_group_rules,
    split_rules,
)

from .factories import make_known_participant


class ParseGroupTests(SimpleTestCase):
    def test_simple_age_groups(self):
        self.assertEqual(_parse_group('М60'), ('м', 60))
        self.assertEqual(_parse_group('Ж16'), ('ж', 16))

    def test_group_with_letters(self):
        self.assertEqual(_parse_group('Ж21АК'), ('ж', 21))
        self.assertEqual(_parse_group('М14-18Б'), ('м', 14))
        self.assertEqual(_parse_group('М16*'), ('м', 16))

    def test_word_groups(self):
        self.assertEqual(_parse_group('Юноши до 17 лет'), ('м', 17))
        self.assertEqual(_parse_group('Девушки до 15 лет'), ('ж', 15))
        self.assertEqual(_parse_group('Мужчины'), ('м', 21))

    def test_elite_without_number(self):
        self.assertEqual(_parse_group('МЭ'), ('м', 21))
        self.assertEqual(_parse_group('МЭ - Мужчины'), ('м', 21))

    def test_groups_without_sex_are_unknown(self):
        # Open, О1, РСД: по названию нельзя понять, кто там бежал.
        self.assertEqual(_parse_group('Open A'), (None, None))
        self.assertEqual(_parse_group('О1'), (None, None))
        self.assertEqual(_parse_group('РСД'), (None, None))
        self.assertEqual(_parse_group(''), (None, None))


class SplitRulesTests(SimpleTestCase):
    def test_split(self):
        self.assertEqual(split_rules('М21+, М60 '), ['М21+', 'М60'])

    def test_empty(self):
        self.assertEqual(split_rules(''), [])
        self.assertEqual(split_rules(None), [])


class MatchRulesTests(SimpleTestCase):
    def test_empty_filter_accepts_everything(self):
        self.assertTrue(group_matches_rules('М16', ''))
        self.assertTrue(group_matches_rules('что угодно', ''))

    def test_exact_group(self):
        self.assertTrue(group_matches_rules('М60', 'М60'))
        self.assertFalse(group_matches_rules('М16', 'М60'))

    def test_exact_group_is_case_insensitive(self):
        self.assertTrue(group_matches_rules('м60', 'М60'))

    def test_open_range_accepts_older_groups(self):
        """«М21+» — ветеранские группы наши, юношеские чужие."""
        for group in ('М21', 'М21АК', 'М35', 'М55', 'М60', 'МЭ', 'Мужчины'):
            self.assertTrue(group_matches_rules(group, 'М21+'), group)
        for group in ('М12', 'М16', 'М16*', 'М18', 'Юноши до 17 лет'):
            self.assertFalse(group_matches_rules(group, 'М21+'), group)

    def test_range_respects_sex(self):
        self.assertFalse(group_matches_rules('Ж60', 'М21+'))
        self.assertTrue(group_matches_rules('Ж60', 'Ж21+'))

    def test_closed_range_keeps_junior_groups(self):
        """«М18-» — наш юноша, ветеранские группы тёзки отбрасываем."""
        for group in ('М12', 'М14', 'М14*', 'Мдо15', 'М18', 'Юноши до 15 лет'):
            self.assertTrue(group_matches_rules(group, 'М18-'), group)
        for group in ('М21', 'М40', 'М45', 'МЭ', 'Мужчины'):
            self.assertFalse(group_matches_rules(group, 'М18-'), group)

    def test_several_rules(self):
        self.assertTrue(group_matches_rules('Юноши до 17 лет', 'М16, Юноши до 17 лет'))
        self.assertTrue(group_matches_rules('М16', 'М16, Юноши до 17 лет'))
        self.assertFalse(group_matches_rules('М60', 'М16, Юноши до 17 лет'))

    def test_unknown_group_is_accepted(self):
        """Пустая группа или Open — не повод терять реальный старт участника."""
        self.assertTrue(group_matches_rules('', 'М21+'))
        self.assertTrue(group_matches_rules('О1', 'М21+'))
        self.assertTrue(group_matches_rules('Open A', 'М21+'))


class FilterRowsTests(TestCase):
    def setUp(self):
        veteran = make_known_participant(last_name='Скоробогатов', first_name='Сергей')
        veteran.group_filter = 'М21+'
        veteran.veteran = True
        veteran.save(update_fields=['group_filter', 'veteran'])

        self.rows = [
            {'last_name': 'Скоробогатов', 'first_name': 'Сергей', 'group_name': 'М60', 'points': 700},
            # Полный тёзка из другого города — юноша.
            {'last_name': 'Скоробогатов', 'first_name': 'Сергей', 'group_name': 'М16*', 'points': 1000},
            {'last_name': 'Скоробогатов', 'first_name': 'Сергей', 'group_name': 'Юноши до 17 лет', 'points': 900},
            {'last_name': 'Иванов', 'first_name': 'Иван', 'group_name': 'М16', 'points': 500},
        ]

    def test_rules_loaded_from_base(self):
        rules = participant_group_rules()
        self.assertEqual(rules[('скоробогатов', 'сергей')], 'М21+')

    def test_alien_rows_are_dropped(self):
        rows = filter_by_group_rules(self.rows)
        groups = [r['group_name'] for r in rows]
        self.assertEqual(groups, ['М60', 'М16'])

    def test_participants_without_filter_are_untouched(self):
        rows = filter_by_group_rules(self.rows)
        self.assertIn(
            {'last_name': 'Иванов', 'first_name': 'Иван', 'group_name': 'М16', 'points': 500},
            rows,
        )

    def test_no_rules_returns_rows_as_is(self):
        rows = filter_by_group_rules(self.rows, rules={})
        self.assertEqual(len(rows), 4)


class SplitByGroupTests(TestCase):
    """Двое своих с одинаковым ФИО (отец и сын) — отдельные строки зачёта."""

    def test_flag_adds_person_to_split_set(self):
        from protocols.services.season_export import _split_by_group_pairs

        participant = make_known_participant(last_name='Иванов', first_name='Пётр')
        self.assertNotIn(('иванов', 'пётр'), _split_by_group_pairs())

        participant.split_by_group = True
        participant.save(update_fields=['split_by_group'])
        self.assertIn(('иванов', 'пётр'), _split_by_group_pairs())

    def test_export_splits_names_by_group(self):
        from datetime import date

        from protocols.services.season_export import build_places_data

        from .factories import make_event, make_protocol, make_result

        participant = make_known_participant(last_name='Иванов', first_name='Пётр')
        participant.split_by_group = True
        participant.save(update_fields=['split_by_group'])

        protocol = make_protocol(event=make_event(date=date(2026, 4, 1)))
        make_result(protocol, group_name='М12', last_name='Иванов', first_name='Пётр')
        make_result(protocol, group_name='М40', last_name='Иванов', first_name='Пётр')

        people, _data, _starts = build_places_data([protocol])
        self.assertEqual(
            sorted(people), [('Иванов', 'Пётр (М12)'), ('Иванов', 'Пётр (М40)')]
        )
