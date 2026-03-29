# -*- coding: utf-8 -*-
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import (
    ChampionshipLevel,
    Event,
    KnownParticipant,
    Protocol,
    ProtocolResult,
    SeasonRanking,
    SeasonRankingEntry,
)


@admin.register(ChampionshipLevel)
class ChampionshipLevelAdmin(admin.ModelAdmin):
    list_display = ('name', 'coefficient', 'order')
    list_editable = ('coefficient', 'order')
    ordering = ('order', 'name')
    search_fields = ('name',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'season', 'importance')
    list_filter = ('season',)
    ordering = ('-date',)


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'event_date', 'championship_level', 'importance', 'source_url', 'format_sfr', 'created_at')
    list_filter = ('format_sfr', 'encoding_utf8', 'championship_level')
    list_editable = ('championship_level', 'importance')
    search_fields = ('title', 'source_url')
    date_hierarchy = 'event_date'
    autocomplete_fields = ('championship_level',)
    fieldsets = (
        (None, {
            'fields': ('event', 'championship_level', 'source_url', 'title', 'event_date', 'raw_html', 'encoding_utf8', 'format_sfr'),
        }),
        ('Коэффициент для зачёта сезона', {
            'fields': ('importance',),
            'description': 'Выберите уровень первенства выше — его коэффициент будет использоваться. Либо задайте коэффициент здесь, если уровень не выбран.',
        }),
    )


@admin.register(ProtocolResult)
class ProtocolResultAdmin(admin.ModelAdmin):
    list_display = ('protocol', 'place', 'place_out_of', 'last_name', 'first_name', 'rank', 'points')
    list_filter = ('protocol',)
    search_fields = ('last_name', 'first_name', 'rank')


@admin.register(KnownParticipant)
class KnownParticipantAdmin(admin.ModelAdmin):
    list_display = ('last_name', 'first_name', 'veteran')
    list_filter = ('veteran',)
    list_editable = ('veteran',)
    search_fields = ('last_name', 'first_name')
    actions = ['mark_as_veteran', 'unmark_veteran']

    fieldsets = (
        (None, {
            'fields': ('last_name', 'first_name'),
        }),
        ('Ветеран / Сезонный зачёт', {
            'fields': ('veteran',),
            'description': 'Включите «Ветеран», чтобы участник не попадал в сезонный зачёт и экспорт по сезону.',
        }),
    )

    @admin.action(description='Отметить как ветеранов (не в зачёт сезона)')
    def mark_as_veteran(self, request, queryset):
        updated = queryset.update(veteran=True)
        self.message_user(request, f'Отмечено как ветеранов: {updated}.')

    @admin.action(description='Убрать из ветеранов (в зачёт сезона)')
    def unmark_veteran(self, request, queryset):
        updated = queryset.update(veteran=False)
        self.message_user(request, f'Убрано из ветеранов: {updated}.')


@admin.register(SeasonRanking)
class SeasonRankingAdmin(admin.ModelAdmin):
    list_display = ('date_from', 'date_to', 'year', 'season', 'created_at', 'export_excel_link')
    list_filter = ('year', 'season')
    ordering = ('-date_from', '-year', '-season')
    date_hierarchy = 'date_from'

    def export_excel_link(self, obj):
        url = reverse('protocols:export_season_excel') + f'?ranking_id={obj.pk}'
        return format_html('<a href="{}">Скачать Excel</a>', url)
    export_excel_link.short_description = 'Экспорт'


@admin.register(SeasonRankingEntry)
class SeasonRankingEntryAdmin(admin.ModelAdmin):
    list_display = ('ranking', 'participant', 'points', 'starts_count')
    list_filter = ('ranking',)
    search_fields = ('participant__last_name', 'participant__first_name')
