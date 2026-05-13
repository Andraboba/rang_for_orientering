# -*- coding: utf-8 -*-
"""Тесты services/date_from_title.py."""
from datetime import date

from django.test import SimpleTestCase

from protocols.services.date_from_title import parse_date_from_title


class ParseDateFromTitleTests(SimpleTestCase):
    def test_empty(self):
        self.assertIsNone(parse_date_from_title(''))
        self.assertIsNone(parse_date_from_title(None))
        self.assertIsNone(parse_date_from_title(123))

    def test_dot_format_full_year(self):
        self.assertEqual(parse_date_from_title('Кубок 15.03.2024'), date(2024, 3, 15))

    def test_dot_format_short_year_2000s(self):
        self.assertEqual(parse_date_from_title('01.01.24'), date(2024, 1, 1))

    def test_dot_format_short_year_1900s(self):
        self.assertEqual(parse_date_from_title('01.01.99'), date(1999, 1, 1))

    def test_iso_format(self):
        self.assertEqual(parse_date_from_title('2024-03-15 событие'), date(2024, 3, 15))

    def test_invalid_date_numbers(self):
        self.assertIsNone(parse_date_from_title('32.13.2024'))

    def test_russian_month(self):
        self.assertEqual(
            parse_date_from_title('15 марта 2024'), date(2024, 3, 15)
        )

    def test_russian_month_nominative(self):
        self.assertEqual(
            parse_date_from_title('1 январь 2020'), date(2020, 1, 1)
        )

    def test_ru_month_short_year(self):
        self.assertEqual(parse_date_from_title('5 мая 24'), date(2024, 5, 5))

    def test_dot_format_without_year(self):
        result = parse_date_from_title('15.03')
        self.assertEqual(result, date(date.today().year, 3, 15))

    def test_no_date(self):
        self.assertIsNone(parse_date_from_title('просто текст без даты'))

    def test_leap_year_invalid(self):
        self.assertIsNone(parse_date_from_title('29.02.2023'))
