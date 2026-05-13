# -*- coding: utf-8 -*-
"""
Фабрики для создания тестовых объектов моделей.
Сделаны без зависимостей от factory_boy — минимум кода.
"""
from datetime import date

from protocols.models import (
    ChampionshipLevel,
    Event,
    KnownParticipant,
    Protocol,
    ProtocolResult,
    SeasonRanking,
    SeasonRankingEntry,
)


def make_event(**kwargs):
    defaults = dict(
        title='Тестовое соревнование',
        date=date(2026, 4, 1),
        season=Event.SEASON_SPRING,
        importance=1.0,
    )
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def make_championship_level(**kwargs):
    defaults = dict(name='Первенство Москвы', coefficient=1.5, order=1)
    defaults.update(kwargs)
    return ChampionshipLevel.objects.create(**defaults)


def make_protocol(**kwargs):
    defaults = dict(
        source_url='https://example.com/protocol',
        title='Тестовый протокол',
        raw_html='',
        encoding_utf8=True,
        format_sfr=False,
    )
    defaults.update(kwargs)
    return Protocol.objects.create(**defaults)


def make_known_participant(last_name='Иванов', first_name='Иван', veteran=False):
    return KnownParticipant.objects.create(
        last_name=last_name, first_name=first_name, veteran=veteran
    )


def make_result(protocol, **kwargs):
    defaults = dict(
        group_name='М14',
        place='1',
        place_out_of=10,
        last_name='Иванов',
        first_name='Иван',
        points=1000,
        raw_line='1 из 10, Иванов Иван, очки:1000',
    )
    defaults.update(kwargs)
    return ProtocolResult.objects.create(protocol=protocol, **defaults)


def make_ranking(**kwargs):
    defaults = dict(
        year=2026,
        season=Event.SEASON_SPRING,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 5, 31),
    )
    defaults.update(kwargs)
    return SeasonRanking.objects.create(**defaults)


def make_ranking_entry(ranking, participant, **kwargs):
    defaults = dict(points=100.0, starts_count=3)
    defaults.update(kwargs)
    return SeasonRankingEntry.objects.create(
        ranking=ranking, participant=participant, **defaults
    )
