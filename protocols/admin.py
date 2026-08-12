# -*- coding: utf-8 -*-
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import (
    ChampionshipLevel,
    Event,
    KnownParticipant,
    Protocol,
    ProtocolGroupLevel,
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


class ProtocolGroupLevelInline(admin.TabularInline):
    model = ProtocolGroupLevel
    extra = 1
    autocomplete_fields = ('championship_level',)
    verbose_name = 'Уровень для отдельной группы'
    verbose_name_plural = 'Уровни для отдельных групп'


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'event_date', 'championship_level', 'importance', 'source_url', 'format_sfr', 'created_at')
    list_filter = ('format_sfr', 'encoding_utf8', 'championship_level')
    list_editable = ('championship_level', 'importance')
    search_fields = ('title', 'source_url')
    date_hierarchy = 'event_date'
    autocomplete_fields = ('championship_level',)
    inlines = [ProtocolGroupLevelInline]
    fieldsets = (
        (None, {
            'fields': ('event', 'championship_level', 'source_url', 'title', 'event_date', 'raw_html', 'encoding_utf8', 'format_sfr'),
        }),
        ('Коэффициент для зачёта сезона', {
            'fields': ('importance',),
            'description': (
                'Выберите уровень первенства выше — его коэффициент будет использоваться. '
                'Либо задайте коэффициент здесь, если уровень не выбран. '
                'Если у части групп статус другой (например, первенство России только у МЖ14 и МЖ16), '
                'добавьте эти группы в блоке «Уровни для отдельных групп» ниже.'
            ),
        }),
    )


@admin.register(ProtocolResult)
class ProtocolResultAdmin(admin.ModelAdmin):
    list_display = ('protocol', 'group_name', 'place', 'place_out_of', 'last_name', 'first_name', 'rank', 'points')
    list_filter = ('protocol',)
    search_fields = ('last_name', 'first_name', 'rank')


@admin.register(KnownParticipant)
class KnownParticipantAdmin(admin.ModelAdmin):
    list_display = ('last_name', 'first_name', 'group_name', 'group_filter', 'split_by_group', 'veteran', 'excluded')
    list_filter = ('veteran', 'excluded', 'split_by_group')
    list_editable = ('group_name', 'group_filter', 'split_by_group', 'veteran', 'excluded')
    search_fields = ('last_name', 'first_name', 'group_name')
    actions = ['mark_as_veteran', 'unmark_veteran', 'mark_as_excluded', 'unmark_excluded']

    fieldsets = (
        (None, {
            'fields': ('last_name', 'first_name', 'group_name'),
        }),
        ('Полные тёзки', {
            'fields': ('group_filter', 'split_by_group'),
            'description': (
                'Если в протоколах встречается однофамилец с тем же именем из другой '
                'возрастной группы, укажите группы этого участника через запятую: '
                '«М21+» — все группы М от 21 и старше, либо точные названия («М60», '
                '«Юноши до 17 лет»). Результаты в чужих группах будут отброшены.'
            ),
        }),
        ('Ветеран / Сезонный зачёт', {
            'fields': ('veteran',),
            'description': 'Включите «Ветеран», чтобы участник попадал в ветеранские листы экспорта, а не в основной зачёт.',
        }),
        ('Исключение из зачёта', {
            'fields': ('excluded',),
            'description': (
                'Включите «Не учитывать никогда» для тех, кто бежал за команду разово '
                'и в состав не входит: такие участники не ищутся при обработке протоколов '
                'и не попадают ни в основной зачёт, ни в ветеранский.'
            ),
        }),
    )

    @admin.action(description='Отметить как ветеранов (в ветеранские листы)')
    def mark_as_veteran(self, request, queryset):
        updated = queryset.update(veteran=True)
        self.message_user(request, f'Отмечено как ветеранов: {updated}.')

    @admin.action(description='Убрать из ветеранов (в зачёт сезона)')
    def unmark_veteran(self, request, queryset):
        updated = queryset.update(veteran=False)
        self.message_user(request, f'Убрано из ветеранов: {updated}.')

    @admin.action(description='Не учитывать никогда (исключить из всех зачётов)')
    def mark_as_excluded(self, request, queryset):
        updated = queryset.update(excluded=True)
        self.message_user(request, f'Исключено из зачётов: {updated}.')

    @admin.action(description='Вернуть в зачёт (снять исключение)')
    def unmark_excluded(self, request, queryset):
        updated = queryset.update(excluded=False)
        self.message_user(request, f'Возвращено в зачёт: {updated}.')


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
