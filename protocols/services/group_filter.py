# -*- coding: utf-8 -*-
"""
Проверка, относится ли группа протокола к участнику базы.

Нужно для полных тёзок: например, в одних и тех же соревнованиях бегут
Скоробогатов Сергей из М60 (наш ветеран) и Скоробогатов Сергей из М16
(другой человек из другого города). Фамилии и имена совпадают, различает их
только возрастная группа.

Формат правил (поле KnownParticipant.group_filter), через запятую:
  «М60»                — только эта группа;
  «М21+»               — группы М от 21 и старше, включая элиту (МЭ, Мужчины);
  «М18-»               — группы М до 18 включительно (юношеские);
  «Юноши до 17 лет»    — точное название, как в протоколе.

Пустое правило — участник принимается в любой группе.
"""
import re
from typing import Dict, List, Optional, Tuple

# Женские и мужские обозначения групп в русских и латинских протоколах.
_FEMALE_PREFIXES = ("ж", "w", "д")
_MALE_PREFIXES = ("м", "m", "ю")

# Возраст, с которого группа считается взрослой: МЭ, Мужчины, Элита и т.п.
_ELITE_AGE = 21

_ELITE_WORDS = ("э", "элита", "мужчины", "женщины", "elite")
_JUNIOR_WORDS = ("юнош", "девуш", "мальчик", "девоч", "до ")


def _normalize(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split()).strip().lower()


def _parse_group(group_name: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Разбирает название группы в (пол, возраст).

    «М60» → ('м', 60); «Ж21АК» → ('ж', 21); «МЭ - Мужчины» → ('м', 21);
    «Юноши до 17 лет» → ('м', 17); «М14-18Б» → ('м', 14).
    Возраст None, если в названии его нет (Open, РСД, О2).
    """
    name = _normalize(group_name)
    if not name:
        return None, None

    sex = None
    if name.startswith(_FEMALE_PREFIXES) or "девуш" in name or "девоч" in name:
        sex = "ж"
    elif name.startswith(_MALE_PREFIXES) or "юнош" in name or "мальчик" in name:
        sex = "м"
    # «Мужчины»/«Женщины» без префикса группы.
    if name.startswith("женщин"):
        sex = "ж"
    elif name.startswith("мужчин"):
        sex = "м"

    if sex is None:
        # Open, О1, РСД: возрастной группы нет, судить о человеке нельзя.
        return None, None

    # Первое число в названии — возраст группы.
    m_age = re.search(r"\d{1,2}", name)
    age = int(m_age.group(0)) if m_age else None

    # Элита без числа: МЭ, ЖЭ, Мужчины, Женщины.
    if age is None and any(word in name for word in _ELITE_WORDS):
        if not any(word in name for word in _JUNIOR_WORDS):
            age = _ELITE_AGE

    return sex, age


def _rule_matches(rule: str, group_name: str) -> bool:
    rule = _normalize(rule)
    group = _normalize(group_name)
    if not rule:
        return True

    if rule[-1] in "+-":
        rule_sex, rule_age = _parse_group(rule[:-1])
        group_sex, group_age = _parse_group(group)
        if rule_age is None or group_age is None:
            return False
        if rule_sex and group_sex and rule_sex != group_sex:
            return False
        if rule.endswith("+"):
            return group_age >= rule_age
        return group_age <= rule_age

    return group == rule


def split_rules(group_filter: str) -> List[str]:
    return [part.strip() for part in (group_filter or "").split(",") if part.strip()]


def participant_group_rules() -> dict:
    """
    {(фамилия, имя) в нижнем регистре: правило} для участников, у которых
    задан фильтр групп. Пустой словарь — если тёзок в базе нет.
    """
    from ..models import KnownParticipant

    return {
        (last_name.strip().lower(), first_name.strip().lower()): rules
        for last_name, first_name, rules in (
            KnownParticipant.objects.exclude(group_filter='')
            .values_list('last_name', 'first_name', 'group_filter')
        )
    }


def filter_by_group_rules(rows: List[dict], rules: Optional[dict] = None) -> List[dict]:
    """
    Убирает из разобранных результатов чужие строки: те, где фамилия и имя
    совпали с участником базы, но группа не входит в его фильтр.
    """
    rules = participant_group_rules() if rules is None else rules
    if not rules:
        return rows
    return [
        row for row in rows
        if group_matches_rules(
            row.get('group_name', ''),
            rules.get(
                (
                    row['last_name'].strip().lower(),
                    row['first_name'].strip().lower(),
                ),
                '',
            ),
        )
    ]


def group_matches_rules(group_name: str, group_filter: str) -> bool:
    """
    True, если группа подходит хотя бы под одно правило.
    Пустой фильтр — подходит всё.

    Результат с неизвестной группой (парсер её не отдал) считается подходящим:
    иначе участник потерял бы реальные старты из-за особенностей источника.
    """
    rules = split_rules(group_filter)
    if not rules:
        return True
    if not _normalize(group_name):
        return True

    # Группы без пола и возраста (Open, О1, РСД) не позволяют судить о человеке —
    # такие старты оставляем, чтобы не потерять реальные результаты участника.
    sex, age = _parse_group(group_name)
    if sex is None and age is None:
        return True

    return any(_rule_matches(rule, group_name) for rule in rules)
