# -*- coding: utf-8 -*-
"""
Тесты безопасности.

Покрывают:
  - Защиту от SQL-инъекций (ORM Django использует параметризованные запросы).
  - Защиту от XSS (автоэкранирование шаблонов Django).
  - CSRF на POST-вьюхах без токена.
  - Ограничения HTTP-методов (@require_http_methods).
  - Security-заголовки ответа (X-Content-Type-Options, X-Frame-Options, Referrer-Policy).
  - Защиту URL-поля формы от opasных схем (javascript:, data:).
  - Отсутствие утечки данных при 404 на несуществующих объектах.
  - Проверку, что админка закрыта от анонимов.

Тесты не модифицируют реальные данные PostgreSQL — используется транзакционная TestCase.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.db import connection

from protocols.models import KnownParticipant, Protocol
from protocols.forms import LoadProtocolForm
from .factories import make_known_participant, make_protocol, make_result


class DatabaseIsPostgresTests(TestCase):
    """Проект настроен на PostgreSQL — это важно для тестов безопасности."""

    def test_engine_is_postgres(self):
        self.assertIn('postgresql', connection.settings_dict['ENGINE'])

    def test_vendor(self):
        self.assertEqual(connection.vendor, 'postgresql')


class SqlInjectionTests(TestCase):
    """
    Django ORM защищает от SQL-инъекций, но на всякий случай проверяем
    все точки, где пользовательский ввод попадает в запрос.
    """

    def setUp(self):
        self.p_ok = make_protocol(title='Настоящий протокол')
        make_known_participant(last_name='Иванов', first_name='Иван')

    def test_home_search_with_sql_payload(self):
        payloads = [
            "' OR 1=1 --",
            "'; DROP TABLE protocols_protocol; --",
            "\\'; DELETE FROM protocols_knownparticipant; --",
            "1' UNION SELECT NULL --",
            "%27%20OR%201=1",
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                resp = self.client.get(reverse('protocols:home') + f'?q={payload}')
                self.assertEqual(resp.status_code, 200)
                # БД цела
                self.assertTrue(Protocol.objects.filter(pk=self.p_ok.pk).exists())
                self.assertTrue(KnownParticipant.objects.exists())

    def test_export_year_non_numeric_returns_400(self):
        resp = self.client.get(
            reverse('protocols:export_season_excel') + "?year=%27OR1%3D1&season=spring"
        )
        # Должно вернуть 400, а не выполнять что-то.
        self.assertEqual(resp.status_code, 400)

    def test_protocol_pk_is_int_only(self):
        # URL path-converter <int:pk> не пропустит строку
        resp = self.client.get('/protocol/abc/')
        self.assertEqual(resp.status_code, 404)


class XssTests(TestCase):
    def test_xss_in_protocol_title_is_escaped(self):
        evil = '<script>alert("xss")</script>'
        p = make_protocol(title=evil)
        resp = self.client.get(reverse('protocols:home'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertNotIn(evil, body)
        self.assertIn('&lt;script&gt;', body)

    def test_xss_in_search_query_is_escaped(self):
        resp = self.client.get(
            reverse('protocols:home') + '?q=<script>alert(1)</script>'
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertNotIn('<script>alert(1)</script>', body)

    def test_xss_in_result_raw_line_escaped(self):
        p = make_protocol(title='p')
        make_result(p, raw_line='<img src=x onerror=alert(1)>')
        resp = self.client.get(
            reverse('protocols:protocol_detail', args=[p.pk])
        )
        body = resp.content.decode('utf-8')
        self.assertNotIn('<img src=x onerror=alert(1)>', body)


class CsrfProtectionTests(TestCase):
    """
    Проверяем, что POST-вьюхи требуют CSRF.
    По умолчанию Client отключает CSRF — поэтому включаем enforce.
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.protocol = make_protocol()

    def test_home_post_without_csrf_forbidden(self):
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://example.com/x'},
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_protocol_without_csrf_forbidden(self):
        resp = self.client.post(
            reverse('protocols:delete_protocol', args=[self.protocol.pk]),
        )
        self.assertEqual(resp.status_code, 403)

    def test_set_title_without_csrf_forbidden(self):
        resp = self.client.post(
            reverse('protocols:set_protocol_title', args=[self.protocol.pk]),
            data={'title': 'x'},
        )
        self.assertEqual(resp.status_code, 403)


class HttpMethodRestrictionTests(TestCase):
    def test_export_results_only_get(self):
        p = make_protocol()
        resp = self.client.post(reverse('protocols:export_results', args=[p.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_known_participants_only_get(self):
        resp = self.client.post(reverse('protocols:known_participants'))
        self.assertEqual(resp.status_code, 405)

    def test_set_title_only_post(self):
        p = make_protocol()
        resp = self.client.get(reverse('protocols:set_protocol_title', args=[p.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_delete_only_post(self):
        p = make_protocol()
        resp = self.client.get(reverse('protocols:delete_protocol', args=[p.pk]))
        self.assertEqual(resp.status_code, 405)


class SecurityHeadersTests(TestCase):
    def test_home_has_security_headers(self):
        resp = self.client.get(reverse('protocols:home'))
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(resp['X-Frame-Options'], 'DENY')
        self.assertEqual(resp['Referrer-Policy'], 'same-origin')


@override_settings(DEBUG=False)
class ProductionHardeningTests(TestCase):
    """
    В DEBUG=False проверяем, что страницы не выдают стек-трейсы и отладочную
    информацию (Django 500-ошибки не должны просачиваться).
    """

    def test_404_no_debug_info(self):
        resp = self.client.get('/this-path-does-not-exist/')
        self.assertEqual(resp.status_code, 404)
        body = resp.content.decode('utf-8', errors='ignore')
        # Django debug 404 содержит «URLconf» — этого не должно быть.
        self.assertNotIn('URLconf', body)


class UrlValidationTests(TestCase):
    """URLField должен отвергать опасные схемы."""

    def test_reject_javascript_scheme(self):
        form = LoadProtocolForm(data={'url': 'javascript:alert(1)'})
        self.assertFalse(form.is_valid())

    def test_reject_plain_text(self):
        form = LoadProtocolForm(data={'url': 'not a url at all'})
        self.assertFalse(form.is_valid())

    def test_reject_file_scheme(self):
        # file:// валиден URLField'ом? Django URLValidator по умолчанию
        # разрешает http/https/ftp/ftps — поэтому file должен быть отклонён.
        form = LoadProtocolForm(data={'url': 'file:///etc/passwd'})
        self.assertFalse(form.is_valid())


class AdminProtectionTests(TestCase):
    def test_admin_anonymous_redirects_to_login(self):
        resp = self.client.get('/admin/')
        # Django: 302 на /admin/login/
        self.assertIn(resp.status_code, (302, 301))

    def test_admin_login_page_available(self):
        resp = self.client.get('/admin/login/')
        self.assertEqual(resp.status_code, 200)


class UploadLimitsTests(TestCase):
    def test_data_upload_max_configured(self):
        from django.conf import settings
        # Лимит установлен (DoS-защита)
        self.assertLessEqual(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, 10 * 1024 * 1024)
