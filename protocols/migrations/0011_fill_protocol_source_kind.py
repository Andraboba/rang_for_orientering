# -*- coding: utf-8 -*-
"""Проставляет source_kind ранее загруженным протоколам."""
from django.db import migrations


def fill_source_kind(apps, schema_editor):
    from protocols.services.source_router import detect_kind_by_payload

    Protocol = apps.get_model('protocols', 'Protocol')
    for protocol in Protocol.objects.filter(source_kind='').iterator():
        protocol.source_kind = detect_kind_by_payload(
            protocol.raw_html or '', protocol.source_url or ''
        )
        protocol.save(update_fields=['source_kind'])


def clear_source_kind(apps, schema_editor):
    Protocol = apps.get_model('protocols', 'Protocol')
    Protocol.objects.update(source_kind='')


class Migration(migrations.Migration):

    dependencies = [
        ('protocols', '0010_protocol_source_kind'),
    ]

    operations = [
        migrations.RunPython(fill_source_kind, clear_source_kind),
    ]
