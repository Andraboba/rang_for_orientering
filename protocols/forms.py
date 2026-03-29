# -*- coding: utf-8 -*-
from django import forms


class SetProtocolEventDateForm(forms.Form):
    """Форма указания даты соревнований для протокола (WinOrient)."""
    event_date = forms.DateField(
        label='Дата соревнований',
        required=True,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
        }),
    )
    protocol_id = forms.IntegerField(widget=forms.HiddenInput())


class SeasonExportDateRangeForm(forms.Form):
    """Форма выбора периода для экспорта за сезон (календарь)."""
    date_from = forms.DateField(
        label='Период с',
        required=True,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
        }),
    )
    date_to = forms.DateField(
        label='по',
        required=True,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
        }),
    )

    def clean(self):
        data = super().clean()
        date_from = data.get('date_from')
        date_to = data.get('date_to')
        if date_from and date_to and date_from > date_to:
            self.add_error('date_to', 'Дата «по» должна быть не раньше даты «с».')
        return data


class LoadProtocolForm(forms.Form):
    """Форма загрузки протокола по URL."""
    url = forms.URLField(
        label='Ссылка на протокол',
        widget=forms.URLInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://...',
        })
    )
    encoding_utf8 = forms.BooleanField(
        label='Кодировка UTF-8 (иначе Windows-1251)',
        required=False,
        initial=False,
    )
    format_sfr = forms.BooleanField(
        label='Формат SFR (таблица HTML)',
        required=False,
        initial=False,
    )


class EditProtocolTitleForm(forms.Form):
    """Форма редактирования названия протокола пользователем."""
    title = forms.CharField(
        label='Название протокола',
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Введите или отредактируйте название',
        }),
    )
    protocol_id = forms.IntegerField(widget=forms.HiddenInput())


class ProcessProtocolForm(forms.Form):
    """Форма обработки уже загруженного протокола по названию команды или по базе участников (Orgeo)."""
    team_name = forms.CharField(
        label='Название команды (или часть фамилии/имени)',
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Например: СК Фотон или оставьте пустым для Orgeo — поиск по базе участников',
        })
    )
    protocol_id = forms.IntegerField(widget=forms.HiddenInput())
