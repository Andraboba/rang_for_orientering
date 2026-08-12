# -*- coding: utf-8 -*-
"""Тесты services/sportorg_parser.py (HTML-экспорт SportOrg, o-club.ru)."""
import base64
import gzip
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from protocols.services.sportorg_parser import (
    SPORTORG_DATA_PREFIX,
    decode_race,
    extract_race_b64,
    fetch_sportorg,
    is_sportorg_html,
    is_sportorg_payload,
    parse_sportorg,
)

FOTON = "77_СК Фотон, Москва ЮЗАО"
OTHER_TEAM = "76_Верный азимут, Ярославль"


def _race_html(race: dict) -> str:
    """Собирает страницу SportOrg с данными гонки, как это делает экспорт."""
    packed = base64.b64encode(gzip.compress(json.dumps(race).encode("utf-8")))
    return (
        "<html><head><title>Results - SportOrg</title></head><body>"
        '<div id="results-tables"></div>'
        f"<script>var raceCompressed = '{packed.decode('ascii')}';</script>"
        "</body></html>"
    )


def _personal_race() -> dict:
    """Личная гонка: одна группа, лидер и два участника «Фотона»."""
    people = [
        # (id, фамилия, имя, команда, место, время в мс, статус)
        ("p1", "Шелковникова", "Ульяна", OTHER_TEAM, 1, 1500000, 1),
        ("p2", "Антонова", "Дарья", FOTON, 2, 1800000, 1),
        ("p3", "Нестерова", "Ксения", FOTON, 3, 3000000, 1),
        ("p4", "Сидоров", "Пётр", OTHER_TEAM, -1, 0, 4),
        ("p5", "_Резерв", "", "_", -1, 0, 4),
    ]
    return {
        "data": {
            "title": "Макушка Лета 2026",
            "description": "Макушка Лета 2026<br>\nКросс-спринт<br>\nКВ - 60 Минут<br>\n",
            "start_datetime": "2026-07-31 10:00:00",
            "race_type": 0,
        },
        "groups": [{"id": "g1", "name": "Ж12"}],
        "organizations": [
            {"id": "o1", "name": OTHER_TEAM},
            {"id": "o2", "name": FOTON},
            {"id": "o3", "name": "_"},
        ],
        "persons": [
            {
                "id": pid,
                "surname": surname,
                "name": name,
                "group_id": "g1",
                "organization_id": {OTHER_TEAM: "o1", FOTON: "o2"}.get(team, "o3"),
                "bib": 0,
            }
            for pid, surname, name, team, _place, _msec, _status in people
        ],
        "results": [
            {
                "person_id": pid,
                "place": place,
                "result_msec": msec,
                "status": status,
                "status_comment": "" if status == 1 else "п.п.7.2.6",
            }
            for pid, _s, _n, _t, place, msec, status in people
        ],
    }


def _relay_race() -> dict:
    """
    Эстафета 2 этапа, три команды. bib = этап * 1000 + номер команды,
    результат последнего этапа — накопленное время команды.
    """
    members = [
        # (id, фамилия, bib, команда, время в мс, место, статус)
        ("r1", "Быстров", 1001, OTHER_TEAM, 1200000, 1, 1),
        ("r2", "Финишный", 2001, OTHER_TEAM, 2400000, 1, 1),
        ("r3", "Юрчук", 1002, FOTON, 1300000, 2, 1),
        ("r4", "Лесовой", 2002, FOTON, 3000000, 2, 1),
        # Третья команда не сдала второй этап — время неполное, зачёта нет.
        ("r5", "Одинокий", 1003, FOTON, 1250000, 3, 1),
    ]
    return {
        "data": {
            "title": "Макушка Лета 2026 - Эстафета",
            "description": "Макушка Лета 2026<br>\nКросс-эстафета 2 человека<br>\n",
            "start_datetime": "2026-07-31 10:00:00",
            "race_type": 3,
            "relay_leg_count": 2,
        },
        "groups": [{"id": "g1", "name": "Мужчины"}],
        "organizations": [
            {"id": "o1", "name": OTHER_TEAM},
            {"id": "o2", "name": FOTON},
        ],
        "persons": [
            {
                "id": pid,
                "surname": surname,
                "name": "Иван",
                "group_id": "g1",
                "organization_id": "o1" if team == OTHER_TEAM else "o2",
                "bib": bib,
            }
            for pid, surname, bib, team, _msec, _place, _status in members
        ],
        "results": [
            {
                "person_id": pid,
                "place": place,
                "result_msec": msec,
                "status": status,
                "status_comment": "",
            }
            for pid, _s, _b, _t, msec, place, status in members
        ],
    }


class DetectionTests(SimpleTestCase):
    def test_is_sportorg_html(self):
        self.assertTrue(is_sportorg_html(_race_html(_personal_race())))

    def test_plain_html_is_not_sportorg(self):
        self.assertFalse(is_sportorg_html("<html><table></table></html>"))
        self.assertFalse(is_sportorg_html(""))

    def test_is_sportorg_payload(self):
        self.assertTrue(is_sportorg_payload(f"{SPORTORG_DATA_PREFIX}abc"))
        self.assertFalse(is_sportorg_payload("<html></html>"))

    def test_extract_and_decode(self):
        html = _race_html(_personal_race())
        race = decode_race(extract_race_b64(html))
        self.assertEqual(race["groups"][0]["name"], "Ж12")

    def test_decode_garbage_returns_none(self):
        self.assertIsNone(decode_race("не base64"))
        self.assertIsNone(decode_race(""))


class FetchTests(SimpleTestCase):
    @patch("protocols.services.sportorg_parser._fetch_html")
    def test_fetch_returns_payload_title_and_date(self, mock_fetch):
        mock_fetch.return_value = (_race_html(_personal_race()), None)
        payload, title, event_date, err = fetch_sportorg("https://o-club.ru/r.html")
        self.assertIsNone(err)
        self.assertTrue(payload.startswith(SPORTORG_DATA_PREFIX))
        self.assertEqual(title, "Макушка Лета 2026 — Кросс-спринт")
        self.assertEqual(event_date, "2026-07-31")

    @patch("protocols.services.sportorg_parser._fetch_html")
    def test_relay_title_not_duplicated(self, mock_fetch):
        mock_fetch.return_value = (_race_html(_relay_race()), None)
        _payload, title, _date, err = fetch_sportorg("https://o-club.ru/est.html")
        self.assertIsNone(err)
        self.assertEqual(title, "Макушка Лета 2026 - Эстафета — Кросс-эстафета 2 человека")

    @patch("protocols.services.sportorg_parser._fetch_html")
    def test_fetch_page_without_race_data(self, mock_fetch):
        mock_fetch.return_value = ("<html>пусто</html>", None)
        payload, _title, _date, err = fetch_sportorg("https://o-club.ru/x.html")
        self.assertIsNone(payload)
        self.assertIn("raceCompressed", err)

    @patch("protocols.services.sportorg_parser._fetch_html")
    def test_fetch_network_error(self, mock_fetch):
        mock_fetch.return_value = (None, "timeout")
        payload, _title, _date, err = fetch_sportorg("https://o-club.ru/x.html")
        self.assertIsNone(payload)
        self.assertIn("timeout", err)

    def test_fetch_rejects_non_http_scheme(self):
        payload, _title, _date, err = fetch_sportorg("file:///etc/passwd")
        self.assertIsNone(payload)
        self.assertIn("схема", err.lower())


class PersonalParsingTests(SimpleTestCase):
    def setUp(self):
        self.payload = SPORTORG_DATA_PREFIX + extract_race_b64(
            _race_html(_personal_race())
        )

    def test_show_all_returns_real_people_only(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        # Служебная запись «_Резерв» отброшена.
        self.assertEqual(len(rows), 4)
        self.assertNotIn("_Резерв", [r["last_name"] for r in rows])

    def test_leader_gets_1000_points(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        leader = next(r for r in rows if r["last_name"] == "Шелковникова")
        self.assertEqual(leader["points"], 1000)
        self.assertEqual(leader["place"], "1")
        self.assertEqual(leader["group_name"], "Ж12")

    def test_points_decrease_with_time(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        points = {r["last_name"]: r["points"] for r in rows}
        # (2 - 1800/1500) * 1000 = 800, (2 - 3000/1500) * 1000 → минимум 1
        self.assertEqual(points["Антонова"], 800)
        self.assertEqual(points["Нестерова"], 1)
        self.assertLessEqual(max(points.values()), 1000)

    def test_filter_by_team_foton_finds_people(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="Фотон")
        self.assertEqual(
            sorted(r["last_name"] for r in rows), ["Антонова", "Нестерова"]
        )

    def test_filter_by_team_is_case_insensitive(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="фОтОн")
        self.assertEqual(len(rows), 2)

    def test_filter_by_known_participants(self):
        rows = parse_sportorg(
            self.payload, base_pairs=[("Антонова", "Дарья")], team_name=""
        )
        self.assertEqual([r["last_name"] for r in rows], ["Антонова"])

    def test_team_filter_also_takes_known_participants(self):
        rows = parse_sportorg(
            self.payload,
            base_pairs=[("Шелковникова", "Ульяна")],
            team_name="Фотон",
        )
        names = sorted(r["last_name"] for r in rows)
        self.assertEqual(names, ["Антонова", "Нестерова", "Шелковникова"])

    def test_did_not_finish_gets_zero_points(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        dnf = next(r for r in rows if r["last_name"] == "Сидоров")
        self.assertEqual(dnf["points"], 0)
        self.assertIn(dnf["place"], ("снят", "н.старт"))
        self.assertIsNone(dnf["place_out_of"])

    def test_base_pairs_is_extended_with_found_people(self):
        base_pairs = []
        parse_sportorg(self.payload, base_pairs=base_pairs, team_name="Фотон")
        self.assertIn(("Антонова", "Дарья"), base_pairs)

    def test_garbage_payload_returns_empty(self):
        self.assertEqual(parse_sportorg("мусор", base_pairs=[], team_name="*"), [])


class RelayParsingTests(SimpleTestCase):
    def setUp(self):
        self.payload = SPORTORG_DATA_PREFIX + extract_race_b64(
            _race_html(_relay_race())
        )

    def test_relay_detected_and_members_share_team_points(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        by_name = {r["last_name"]: r for r in rows}
        # Лидер: 2400 сек. Вторая команда: (2 - 3000/2400) * 1000 = 750.
        self.assertEqual(by_name["Быстров"]["points"], 1000)
        self.assertEqual(by_name["Финишный"]["points"], 1000)
        self.assertEqual(by_name["Юрчук"]["points"], 750)
        self.assertEqual(by_name["Лесовой"]["points"], 750)

    def test_points_never_exceed_1000(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        self.assertLessEqual(max(r["points"] for r in rows), 1000)

    def test_team_without_last_leg_is_not_scored(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="*")
        lonely = next(r for r in rows if r["last_name"] == "Одинокий")
        self.assertEqual(lonely["points"], 0)
        self.assertEqual(lonely["place"], "снят")

    def test_relay_filter_by_team_foton(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="Фотон")
        self.assertEqual(
            sorted(r["last_name"] for r in rows), ["Лесовой", "Одинокий", "Юрчук"]
        )

    def test_relay_raw_line_mentions_relay(self):
        rows = parse_sportorg(self.payload, base_pairs=[], team_name="Фотон")
        scored = next(r for r in rows if r["points"])
        self.assertIn("эстафета", scored["raw_line"])
