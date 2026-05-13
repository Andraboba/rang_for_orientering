# -*- coding: utf-8 -*-
"""Тесты services/title_cleaner.py."""
from django.test import SimpleTestCase

from protocols.services.title_cleaner import clean_protocol_title


class CleanProtocolTitleTests(SimpleTestCase):
    def test_empty(self):
        self.assertEqual(clean_protocol_title(''), '')

    def test_none(self):
        self.assertEqual(clean_protocol_title(None), '')

    def test_non_string(self):
        self.assertEqual(clean_protocol_title(123), '')

    def test_removes_fso_header(self):
        txt = 'Федерация Спортивного Ориентирования г. Москвы Кубок Москвы'
        out = clean_protocol_title(txt)
        self.assertNotIn('Федерация', out)
        self.assertIn('Кубок Москвы', out)

    def test_removes_mifi_section(self):
        txt = 'Секция Спортивного Ориентирования МИФИ весна 2026'
        out = clean_protocol_title(txt)
        self.assertNotIn('МИФИ', out)
        self.assertIn('весна 2026', out)

    def test_removes_protocol_results_phrase(self):
        txt = 'ПРОТОКОЛ РЕЗУЛЬТАТОВ Кубок Москвы'
        self.assertEqual(clean_protocol_title(txt), 'Кубок Москвы')

    def test_strips_extra_spaces(self):
        self.assertEqual(clean_protocol_title('  foo   bar  '), 'foo bar')

    def test_preserves_plain_title(self):
        self.assertEqual(clean_protocol_title('Первенство МИФИ 2026'), 'Первенство МИФИ 2026')
