# -*- coding: utf-8 -*-
"""Тесты форм (protocols/forms.py)."""
from datetime import date

from django.test import TestCase

from protocols.forms import (
    EditProtocolTitleForm,
    LoadProtocolForm,
    ProcessProtocolForm,
    SeasonExportDateRangeForm,
    SetProtocolEventDateForm,
)
from .factories import make_championship_level


class SetProtocolEventDateFormTests(TestCase):
    def test_valid(self):
        form = SetProtocolEventDateForm(
            data={'event_date': '2026-04-01', 'protocol_id': 1}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['event_date'], date(2026, 4, 1))

    def test_missing_date(self):
        form = SetProtocolEventDateForm(data={'protocol_id': 1})
        self.assertFalse(form.is_valid())
        self.assertIn('event_date', form.errors)

    def test_invalid_date(self):
        form = SetProtocolEventDateForm(
            data={'event_date': 'not-a-date', 'protocol_id': 1}
        )
        self.assertFalse(form.is_valid())

    def test_missing_protocol_id(self):
        form = SetProtocolEventDateForm(data={'event_date': '2026-04-01'})
        self.assertFalse(form.is_valid())
        self.assertIn('protocol_id', form.errors)


class SeasonExportDateRangeFormTests(TestCase):
    def test_valid(self):
        form = SeasonExportDateRangeForm(
            data={'date_from': '2026-01-01', 'date_to': '2026-12-31'}
        )
        self.assertTrue(form.is_valid())

    def test_from_after_to(self):
        form = SeasonExportDateRangeForm(
            data={'date_from': '2026-12-31', 'date_to': '2026-01-01'}
        )
        self.assertFalse(form.is_valid())
        self.assertIn('date_to', form.errors)

    def test_required(self):
        form = SeasonExportDateRangeForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn('date_from', form.errors)
        self.assertIn('date_to', form.errors)


class LoadProtocolFormTests(TestCase):
    def test_valid_minimal(self):
        form = LoadProtocolForm(data={'url': 'https://example.com/p'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_url(self):
        form = LoadProtocolForm(data={'url': 'not a url'})
        self.assertFalse(form.is_valid())

    def test_url_required(self):
        form = LoadProtocolForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn('url', form.errors)

    def test_championship_level_choice(self):
        lvl = make_championship_level()
        form = LoadProtocolForm(
            data={'url': 'https://example.com/p', 'championship_level': lvl.pk}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['championship_level'], lvl)

    def test_bool_defaults(self):
        form = LoadProtocolForm(data={'url': 'https://example.com/p'})
        self.assertTrue(form.is_valid())
        self.assertFalse(form.cleaned_data['encoding_utf8'])
        self.assertFalse(form.cleaned_data['format_sfr'])

    def test_invalid_javascript_url_rejected(self):
        """Безопасность: javascript: URL должен быть отклонён URLField."""
        form = LoadProtocolForm(data={'url': 'javascript:alert(1)'})
        self.assertFalse(form.is_valid())


class EditProtocolTitleFormTests(TestCase):
    def test_valid(self):
        form = EditProtocolTitleForm(data={'title': 'Новое', 'protocol_id': 1})
        self.assertTrue(form.is_valid())

    def test_title_optional(self):
        form = EditProtocolTitleForm(data={'title': '', 'protocol_id': 1})
        self.assertTrue(form.is_valid())

    def test_title_max_length(self):
        form = EditProtocolTitleForm(
            data={'title': 'x' * 301, 'protocol_id': 1}
        )
        self.assertFalse(form.is_valid())


class ProcessProtocolFormTests(TestCase):
    def test_valid_without_team(self):
        form = ProcessProtocolForm(data={'protocol_id': 1, 'team_name': ''})
        self.assertTrue(form.is_valid())

    def test_valid_with_team(self):
        form = ProcessProtocolForm(
            data={'protocol_id': 1, 'team_name': 'СК Фотон'}
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['team_name'], 'СК Фотон')

    def test_protocol_id_required(self):
        form = ProcessProtocolForm(data={'team_name': 'X'})
        self.assertFalse(form.is_valid())
