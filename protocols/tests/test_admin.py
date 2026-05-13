# -*- coding: utf-8 -*-
"""Тесты админки protocols/admin.py."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse

from protocols.admin import KnownParticipantAdmin
from protocols.models import KnownParticipant
from .factories import (
    make_championship_level,
    make_event,
    make_known_participant,
    make_protocol,
    make_ranking,
    make_ranking_entry,
    make_result,
)


User = get_user_model()


class AdminSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username='admin', password='admin12345', email='a@a.a'
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_changelists_load(self):
        names = [
            'protocols_event', 'protocols_championshiplevel', 'protocols_protocol',
            'protocols_protocolresult', 'protocols_knownparticipant',
            'protocols_seasonranking', 'protocols_seasonrankingentry',
        ]
        for n in names:
            with self.subTest(model=n):
                resp = self.client.get(f'/admin/protocols/{n.replace("protocols_", "")}/')
                self.assertEqual(resp.status_code, 200)

    def _make_request(self):
        req = RequestFactory().post('/')
        req.user = self.admin
        setattr(req, 'session', {})
        setattr(req, '_messages', FallbackStorage(req))
        return req

    def test_mark_veteran_action(self):
        kp = make_known_participant()
        ma = KnownParticipantAdmin(KnownParticipant, admin_site=None)
        req = self._make_request()
        ma.mark_as_veteran(req, KnownParticipant.objects.all())
        kp.refresh_from_db()
        self.assertTrue(kp.veteran)
        ma.unmark_veteran(req, KnownParticipant.objects.all())
        kp.refresh_from_db()
        self.assertFalse(kp.veteran)

    def test_seasonranking_admin_export_link(self):
        from protocols.admin import SeasonRankingAdmin
        from protocols.models import SeasonRanking
        r = make_ranking()
        adm = SeasonRankingAdmin(SeasonRanking, admin_site=None)
        html = adm.export_excel_link(r)
        self.assertIn('ranking_id=', html)
        self.assertIn(str(r.pk), html)
