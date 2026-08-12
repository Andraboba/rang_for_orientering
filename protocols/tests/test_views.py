# -*- coding: utf-8 -*-
"""Тесты вью-функций protocols/views.py."""
from datetime import date
from unittest.mock import patch
from urllib.parse import unquote

from django.test import TestCase
from django.urls import reverse

from protocols.models import Event, KnownParticipant, Protocol, ProtocolResult
from protocols.services.pdf_summary_parser import PDF_SUMMARY_PREFIX
from protocols.services.source_router import KIND_PDF_SUMMARY, KIND_SPORTORG
from protocols.services.sportorg_parser import SPORTORG_DATA_PREFIX, extract_race_b64
from .test_pdf_summary_parser import OCLUB_POINTS_TEXT
from .test_sportorg_parser import _personal_race, _race_html
from .factories import (
    make_championship_level,
    make_event,
    make_known_participant,
    make_protocol,
    make_ranking,
    make_result,
)


class HomeViewTests(TestCase):
    def test_get_empty(self):
        resp = self.client.get(reverse('protocols:home'))
        self.assertEqual(resp.status_code, 200)

    def test_search_filter(self):
        make_protocol(title='Кубок Москвы')
        make_protocol(title='Совсем другое')
        resp = self.client.get(reverse('protocols:home') + '?q=Кубок')
        self.assertEqual(resp.status_code, 200)
        titles = [p.title for p in resp.context['protocols']]
        self.assertIn('Кубок Москвы', titles)
        self.assertNotIn('Совсем другое', titles)

    def test_search_is_safe_to_dangerous_chars(self):
        """SQL-инъекция через ?q= не должна падать или выполнять SQL."""
        make_protocol(title='OK')
        evil = "'; DROP TABLE protocols_protocol; --"
        resp = self.client.get(reverse('protocols:home') + f'?q={evil}')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Protocol.objects.filter(title='OK').exists())

    @patch('protocols.services.source_router.fetch_url')
    def test_post_generic_url(self, mock_fetch):
        mock_fetch.return_value = (
            '<html><h1>Тестовые соревнования</h1></html>', None
        )
        resp = self.client.post(
            reverse('protocols:home'),
            data={
                'url': 'https://example.com/protocol',
                'encoding_utf8': 'on',
                'format_sfr': '',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Protocol.objects.count(), 1)
        p = Protocol.objects.first()
        self.assertIn('Тестовые', p.title)

    @patch('protocols.services.source_router.fetch_url')
    def test_post_fetch_error(self, mock_fetch):
        mock_fetch.return_value = (None, 'boom')
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://example.com/x'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Protocol.objects.count(), 0)

    @patch('protocols.services.source_router.resolve_orgeo_url')
    def test_post_orgeo_resolve_error(self, mock_resolve):
        mock_resolve.return_value = ('', None, None, [], 'bad url')
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://orgeo.ru/live/#/bad'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Protocol.objects.count(), 0)

    @patch('protocols.services.source_router.fetch_orgeo_document')
    @patch('protocols.services.source_router.fetch_orgeo_with_browser')
    @patch('protocols.services.source_router.fetch_orgeo_tables')
    @patch('protocols.services.source_router.resolve_orgeo_url')
    def test_post_orgeo_tables_ok(self, mock_resolve, mock_tables, mock_browser, mock_doc):
        mock_resolve.return_value = (
            'https://orgeo.ru/online/1',
            'Event Name', '2026-04-01',
            [{'sub_id': '1', 'name': 'd', 'date': '2026-04-01'}],
            None,
        )
        mock_tables.return_value = (['<html>Фамилия <table/></html>'], None)
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://orgeo.ru/online/1'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Protocol.objects.count(), 1)
        p = Protocol.objects.first()
        self.assertEqual(p.title, 'Event Name')
        self.assertEqual(p.event_date, date(2026, 4, 1))

    @patch('protocols.services.source_router.fetch_sportident_tables')
    def test_post_sportident_error(self, mock_tables):
        mock_tables.return_value = (None, None, 'bad id')
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://sportident.online/ol_new/?id=bad'},
            follow=True,
        )
        self.assertEqual(Protocol.objects.count(), 0)

    @patch('protocols.services.source_router.fetch_sportident_tables')
    def test_post_sportident_ok(self, mock_tables):
        mock_tables.return_value = (['<html>t1</html>'], 'Title SI', None)
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://sportident.online/ol_new/?id=1'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Protocol.objects.count(), 1)

    @patch('protocols.services.source_router.fetch_url')
    def test_post_sportorg_html_detected_by_content(self, mock_fetch):
        """Ссылка на SportOrg-экспорт: вид, название и дата определяются сами."""
        html = _race_html(_personal_race())
        mock_fetch.return_value = (html, None)
        with patch(
            'protocols.services.sportorg_parser._fetch_html',
            return_value=(html, None),
        ):
            resp = self.client.post(
                reverse('protocols:home'),
                data={'url': 'https://o-club.ru/results.html?sportorg=1'},
            )
        self.assertEqual(resp.status_code, 302)
        protocol = Protocol.objects.get()
        self.assertEqual(protocol.source_kind, KIND_SPORTORG)
        self.assertEqual(protocol.event_date, date(2026, 7, 31))
        self.assertIn('Кросс-спринт', protocol.title)

    @patch('protocols.services.pdf_summary_parser.extract_pdf_text')
    @patch('protocols.services.pdf_summary_parser.fetch_pdf_bytes')
    def test_post_pdf_summary_link(self, mock_bytes, mock_text):
        """PDF с суммой многодневки грузится такой же ссылкой, без формы файла."""
        mock_bytes.return_value = (b'%PDF-1.4', None)
        mock_text.return_value = (OCLUB_POINTS_TEXT, None)
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://o-club.ru/sum.pdf'},
        )
        self.assertEqual(resp.status_code, 302)
        protocol = Protocol.objects.get()
        self.assertEqual(protocol.source_kind, KIND_PDF_SUMMARY)
        self.assertEqual(protocol.event_date, date(2026, 8, 2))

    @patch('protocols.services.pdf_summary_parser.fetch_pdf_bytes')
    def test_post_pdf_error_does_not_create_protocol(self, mock_bytes):
        mock_bytes.return_value = (None, '404 Not Found')
        resp = self.client.post(
            reverse('protocols:home'),
            data={'url': 'https://o-club.ru/missing.pdf'},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Protocol.objects.count(), 0)


class ProtocolDetailViewTests(TestCase):
    def test_get_nonexistent_404(self):
        resp = self.client.get(
            reverse('protocols:protocol_detail', args=[999999])
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_winorient_shows_date_form(self):
        p = make_protocol(source_url='https://winorient.example/1')
        resp = self.client.get(reverse('protocols:protocol_detail', args=[p.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['show_date_form'])

    def test_get_orgeo_no_date_form(self):
        p = make_protocol(source_url='https://orgeo.ru/online/1', event_date=None)
        resp = self.client.get(reverse('protocols:protocol_detail', args=[p.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['show_date_form'])
        self.assertTrue(resp.context['is_orgeo'])

    def test_post_winorient_requires_team(self):
        p = make_protocol(source_url='https://winorient/1', raw_html='')
        resp = self.client.post(
            reverse('protocols:protocol_detail', args=[p.pk]),
            data={'team_name': '', 'protocol_id': p.pk},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

    def test_post_orgeo_processes(self):
        html = (
            '<h2>Группа М14</h2>'
            '<table>'
            '<tr><td>h</td><td>h</td><td>h</td><td>h</td><td>h</td><td>h</td><td>h</td></tr>'
            '<tr><td>1</td><td>1</td><td>Иванов Иван</td><td>Ф</td><td>00:10:00</td><td></td><td>1</td><td>11:00</td></tr>'
            '</table>'
        )
        p = make_protocol(source_url='https://orgeo.ru/online/1', raw_html=html)
        resp = self.client.post(
            reverse('protocols:protocol_detail', args=[p.pk]),
            data={'team_name': 'Ф', 'protocol_id': p.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(p.results.count(), 1)

    def test_show_all_mode(self):
        html = (
            '<h2>Группа М14</h2>'
            '<table>'
            '<tr><td>h</td><td>h</td><td>h</td><td>h</td><td>h</td><td>h</td><td>h</td></tr>'
            '<tr><td>1</td><td>1</td><td>Иванов Иван</td><td>Ф</td><td>00:10:00</td><td></td><td>1</td><td>11:00</td></tr>'
            '</table>'
        )
        p = make_protocol(source_url='https://orgeo.ru/online/1', raw_html=html)
        resp = self.client.get(
            reverse('protocols:protocol_detail', args=[p.pk]) + '?show_all=1'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(p.results.count(), 1)


class SportOrgProtocolProcessingTests(TestCase):
    """Обработка SportOrg-протокола на странице протокола — как это делает пользователь."""

    def setUp(self):
        payload = SPORTORG_DATA_PREFIX + extract_race_b64(_race_html(_personal_race()))
        self.protocol = make_protocol(
            source_url='https://o-club.ru/results.html?sportorg=1',
            source_kind=KIND_SPORTORG,
            raw_html=payload,
        )

    def test_detail_page_has_no_date_form(self):
        resp = self.client.get(
            reverse('protocols:protocol_detail', args=[self.protocol.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_sportorg'])
        self.assertTrue(resp.context['can_show_all'])

    def test_process_by_team_foton_saves_people(self):
        resp = self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': 'Фотон', 'protocol_id': self.protocol.pk},
        )
        self.assertEqual(resp.status_code, 302)
        saved = sorted(self.protocol.results.values_list('last_name', flat=True))
        self.assertEqual(saved, ['Антонова', 'Нестерова'])
        self.assertTrue(self.protocol.results.filter(points__gt=0).exists())

    def test_process_by_known_participants(self):
        make_known_participant(last_name='Антонова', first_name='Дарья')
        resp = self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': '', 'protocol_id': self.protocol.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            list(self.protocol.results.values_list('last_name', flat=True)),
            ['Антонова'],
        )

    def test_found_people_are_added_to_known_base(self):
        self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': 'Фотон', 'protocol_id': self.protocol.pk},
        )
        self.assertTrue(
            KnownParticipant.objects.filter(
                last_name='Нестерова', first_name='Ксения'
            ).exists()
        )

    def test_show_all_lists_everyone(self):
        self.client.get(
            reverse('protocols:protocol_detail', args=[self.protocol.pk])
            + '?show_all=1'
        )
        self.assertEqual(self.protocol.results.count(), 4)


class PdfSummaryProtocolProcessingTests(TestCase):
    """Обработка PDF-суммы, загруженной по ссылке."""

    def setUp(self):
        self.protocol = make_protocol(
            source_url='https://o-club.ru/sum.pdf',
            source_kind=KIND_PDF_SUMMARY,
            raw_html=f'{PDF_SUMMARY_PREFIX}{OCLUB_POINTS_TEXT}',
        )

    def test_process_by_team_foton_saves_people(self):
        resp = self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': 'Фотон', 'protocol_id': self.protocol.pk},
        )
        self.assertEqual(resp.status_code, 302)
        saved = sorted(self.protocol.results.values_list('last_name', flat=True))
        self.assertEqual(saved, ['Алексеев', 'Белотелов'])

    def test_pdf_summary_can_be_reparsed(self):
        """В отличие от скана, PDF-сумма перепарсивается без повторной загрузки."""
        url = reverse('protocols:protocol_detail', args=[self.protocol.pk])
        self.client.post(url, data={'team_name': 'Фотон', 'protocol_id': self.protocol.pk})
        self.assertNotIn(
            'Вольнов', self.protocol.results.values_list('last_name', flat=True)
        )

        self.client.post(url, data={'team_name': 'Лыткарино', 'protocol_id': self.protocol.pk})
        names = set(self.protocol.results.values_list('last_name', flat=True))
        # Участник новой команды найден; ранее найденные остались через базу участников.
        self.assertIn('Вольнов', names)
        self.assertNotIn('Сетков', names)

    def test_date_form_not_shown(self):
        resp = self.client.get(
            reverse('protocols:protocol_detail', args=[self.protocol.pk])
        )
        self.assertFalse(resp.context['show_date_form'])
        self.assertTrue(resp.context['is_pdf_summary'])


class ExcludedParticipantProcessingTests(TestCase):
    """Помеченные «не учитывать никогда» не попадают в результаты обработки."""

    def setUp(self):
        payload = SPORTORG_DATA_PREFIX + extract_race_b64(_race_html(_personal_race()))
        self.protocol = make_protocol(
            source_url='https://o-club.ru/results.html?sportorg=1',
            source_kind=KIND_SPORTORG,
            raw_html=payload,
        )
        excluded = make_known_participant(last_name='Нестерова', first_name='Ксения')
        excluded.excluded = True
        excluded.save(update_fields=['excluded'])

    def test_excluded_person_is_dropped_even_when_team_matches(self):
        self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': 'Фотон', 'protocol_id': self.protocol.pk},
        )
        saved = list(self.protocol.results.values_list('last_name', flat=True))
        self.assertEqual(saved, ['Антонова'])

    def test_excluded_person_is_not_searched_by_base(self):
        self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': '', 'protocol_id': self.protocol.pk},
        )
        self.assertFalse(self.protocol.results.filter(last_name='Нестерова').exists())

    def test_excluded_flag_survives_processing(self):
        self.client.post(
            reverse('protocols:protocol_detail', args=[self.protocol.pk]),
            data={'team_name': 'Фотон', 'protocol_id': self.protocol.pk},
        )
        participant = KnownParticipant.objects.get(
            last_name='Нестерова', first_name='Ксения'
        )
        self.assertTrue(participant.excluded)

    def test_known_participants_page_shows_status(self):
        resp = self.client.get(reverse('protocols:known_participants'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['excluded_count'], 1)
        self.assertContains(resp, 'не учитывается')


class SetProtocolTitleViewTests(TestCase):
    def test_only_post_allowed(self):
        p = make_protocol()
        resp = self.client.get(
            reverse('protocols:set_protocol_title', args=[p.pk])
        )
        self.assertEqual(resp.status_code, 405)

    def test_update_title(self):
        p = make_protocol(title='old')
        resp = self.client.post(
            reverse('protocols:set_protocol_title', args=[p.pk]),
            data={'title': 'new', 'protocol_id': p.pk},
        )
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.title, 'new')

    def test_404_for_missing_protocol(self):
        resp = self.client.post(
            reverse('protocols:set_protocol_title', args=[10 ** 9]),
            data={'title': 'x', 'protocol_id': 1},
        )
        self.assertEqual(resp.status_code, 404)


class SetProtocolEventDateViewTests(TestCase):
    def test_only_post_allowed(self):
        p = make_protocol()
        resp = self.client.get(
            reverse('protocols:set_protocol_event_date', args=[p.pk])
        )
        self.assertEqual(resp.status_code, 405)

    def test_update_date(self):
        p = make_protocol(source_url='https://winorient.example/1')
        resp = self.client.post(
            reverse('protocols:set_protocol_event_date', args=[p.pk]),
            data={'event_date': '2026-04-01', 'protocol_id': p.pk},
        )
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.event_date, date(2026, 4, 1))

    def test_orgeo_date_cannot_be_manually_set(self):
        p = make_protocol(source_url='https://orgeo.ru/online/1')
        resp = self.client.post(
            reverse('protocols:set_protocol_event_date', args=[p.pk]),
            data={'event_date': '2026-04-01', 'protocol_id': p.pk},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        p.refresh_from_db()
        self.assertIsNone(p.event_date)


class KnownParticipantsViewTests(TestCase):
    def test_list(self):
        make_known_participant(last_name='A', first_name='B')
        make_known_participant(last_name='Петров', first_name='Пётр')
        resp = self.client.get(reverse('protocols:known_participants'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['participants']).__len__(), 2)


class ExportResultsViewTests(TestCase):
    def test_txt_contains_lines(self):
        p = make_protocol(title='Кубок')
        make_result(p, raw_line='1 из 10, Иванов Иван, очки:1000')
        resp = self.client.get(reverse('protocols:export_results', args=[p.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Иванов', resp.content.decode('utf-8'))

    def test_content_disposition_has_utf8_name(self):
        p = make_protocol(title='Файл_с_кириллицей')
        make_result(p, raw_line='x')
        resp = self.client.get(reverse('protocols:export_results', args=[p.pk]))
        cd = resp['Content-Disposition']
        self.assertIn("filename*=UTF-8''", cd)


class SeasonExportPageTests(TestCase):
    def test_get(self):
        resp = self.client.get(reverse('protocols:season_export_page'))
        self.assertEqual(resp.status_code, 200)

    def test_post_valid_returns_xlsx(self):
        resp = self.client.post(
            reverse('protocols:season_export_page'),
            data={'date_from': '2026-01-01', 'date_to': '2026-12-31'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            'spreadsheetml',
            resp['Content-Type'],
        )

    def test_post_invalid_returns_form(self):
        resp = self.client.post(
            reverse('protocols:season_export_page'),
            data={'date_from': '2026-12-31', 'date_to': '2026-01-01'},
        )
        self.assertEqual(resp.status_code, 200)
        # В форме теперь ошибка
        self.assertTrue(resp.context['form'].errors)


class ExportSeasonExcelViewTests(TestCase):
    def test_no_params_400(self):
        resp = self.client.get(reverse('protocols:export_season_excel'))
        self.assertEqual(resp.status_code, 400)

    def test_year_season_ok(self):
        resp = self.client.get(
            reverse('protocols:export_season_excel')
            + '?year=2026&season=spring'
        )
        self.assertEqual(resp.status_code, 200)

    def test_year_season_bad_year(self):
        resp = self.client.get(
            reverse('protocols:export_season_excel')
            + '?year=abc&season=spring'
        )
        self.assertEqual(resp.status_code, 400)

    def test_date_range_ok(self):
        resp = self.client.get(
            reverse('protocols:export_season_excel')
            + '?date_from=2026-01-01&date_to=2026-12-31'
        )
        self.assertEqual(resp.status_code, 200)

    def test_date_range_bad(self):
        resp = self.client.get(
            reverse('protocols:export_season_excel')
            + '?date_from=not-a-date&date_to=2026-12-31'
        )
        self.assertEqual(resp.status_code, 400)

    def test_ranking_id_ok(self):
        r = make_ranking(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
        resp = self.client.get(
            reverse('protocols:export_season_excel') + f'?ranking_id={r.pk}'
        )
        self.assertEqual(resp.status_code, 200)

    def test_ranking_id_not_found(self):
        resp = self.client.get(
            reverse('protocols:export_season_excel') + '?ranking_id=999999'
        )
        self.assertEqual(resp.status_code, 404)

    def test_ranking_id_without_dates_falls_back_to_year_season(self):
        r = make_ranking(
            date_from=None, date_to=None,
            year=2026, season=Event.SEASON_SPRING,
        )
        resp = self.client.get(
            reverse('protocols:export_season_excel') + f'?ranking_id={r.pk}'
        )
        self.assertEqual(resp.status_code, 200)


class DeleteProtocolViewTests(TestCase):
    def test_only_post(self):
        p = make_protocol()
        resp = self.client.get(
            reverse('protocols:delete_protocol', args=[p.pk])
        )
        self.assertEqual(resp.status_code, 405)

    def test_delete_removes_protocol(self):
        p = make_protocol()
        resp = self.client.post(
            reverse('protocols:delete_protocol', args=[p.pk])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Protocol.objects.filter(pk=p.pk).exists())

    def test_delete_non_existent_404(self):
        resp = self.client.post(
            reverse('protocols:delete_protocol', args=[10 ** 9])
        )
        self.assertEqual(resp.status_code, 404)
