# -*- coding: utf-8 -*-
from django import template

from protocols.services.title_cleaner import clean_protocol_title

register = template.Library()


@register.filter
def clean_protocol_title_filter(value):
    """Очищенное название протокола для отображения (без служебных фраз)."""
    if value is None:
        return ''
    return clean_protocol_title(str(value))
