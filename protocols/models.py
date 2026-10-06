# -*- coding: utf-8 -*-
from django.db import models


class Event(models.Model):
    """Отдельное соревнование/дистанция (сезон, важность, связь с Orgeo)."""

    SEASON_SPRING = 'spring'
    SEASON_AUTUMN = 'autumn'
    SEASON_CHOICES = (
        (SEASON_SPRING, 'Весна'),
        (SEASON_AUTUMN, 'Осень'),
    )

    title = models.CharField('Название соревнований', max_length=300)
    date = models.DateField('Дата проведения', null=True, blank=True)
    season = models.CharField(
        'Сезон',
        max_length=10,
        choices=SEASON_CHOICES,
        blank=True,
    )
    importance = models.FloatField(
        'Важность соревнования (вес)',
        default=1.0,
        help_text='Коэффициент важности, который задаёт администратор (1.0, 1.5, 2.0 и т.п.).',
    )
    orgeo_event_id = models.PositiveIntegerField(
        'ID события на Orgeo',
        null=True,
        blank=True,
        db_index=True,
        help_text='Числовой ID события на Orgeo (например 49864).',
    )

    class Meta:
        verbose_name = 'Соревнование'
        verbose_name_plural = 'Соревнования'
        ordering = ['-date', 'title']

    def __str__(self):
        return f'{self.title} ({self.date})' if self.date else self.title


class ChampionshipLevel(models.Model):
    """
    Уровень первенства: Первенство Москвы, Первенство России, Первенство и т.д.
    Для каждого уровня в админке задаётся свой коэффициент для зачёта сезона.
    """
    name = models.CharField('Название', max_length=150)
    coefficient = models.FloatField(
        'Коэффициент для зачёта сезона',
        default=1.0,
        help_text='Баллы с этого уровня умножаются на коэффициент (1.0, 1.5, 2.0 и т.д.).',
    )
    order = models.PositiveSmallIntegerField('Порядок', default=0, help_text='Меньше — выше в списке выбора.')

    class Meta:
        verbose_name = 'Уровень первенства'
        verbose_name_plural = 'Уровни первенства'
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.name} (×{self.coefficient})'


class Protocol(models.Model):
    """Загруженный протокол соревнований (WinOrient, Orgeo и др.)."""
    event = models.ForeignKey(
        Event,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='protocols',
        verbose_name='Соревнование',
    )
    championship_level = models.ForeignKey(
        ChampionshipLevel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='protocols',
        verbose_name='Уровень первенства',
        help_text='Первенство Москвы, Первенство России, Первенство и т.д. — коэффициент берётся из выбранного уровня.',
    )
    source_url = models.URLField('URL протокола', max_length=500, blank=True)
    source_kind = models.CharField(
        'Вид источника',
        max_length=20,
        blank=True,
        default='',
        help_text='Определяется автоматически при загрузке по ссылке.',
    )
    title = models.CharField('Название протокола', max_length=300, blank=True)
    raw_html = models.TextField('Исходный HTML', blank=True)
    encoding_utf8 = models.BooleanField('Кодировка UTF-8 (иначе windows-1251)', default=False)
    format_sfr = models.BooleanField(
        'Формат SFR (1 = таблица с <tr>, 0 = текст с h2)',
        default=False
    )
    created_at = models.DateTimeField('Дата загрузки', auto_now_add=True)
    importance = models.FloatField(
        'Коэффициент старта (если не выбран уровень первенства)',
        default=1.0,
        help_text='Используется, когда уровень первенства не выбран. Иначе берётся коэффициент уровня.',
    )
    # Дата соревнований: для Orgeo может браться из API; для WinOrient — из названия или ввод пользователя.
    event_date = models.DateField(
        'Дата соревнований',
        null=True,
        blank=True,
        help_text='Для протоколов без привязки к событию (например WinOrient): укажите вручную или она будет извлечена из названия.',
    )

    class Meta:
        verbose_name = 'Протокол'
        verbose_name_plural = 'Протоколы'
        ordering = ['-created_at']

    def __str__(self):
        return self.title or self.source_url or f'Протокол #{self.pk}'

    @property
    def group_coefficients(self) -> dict:
        """
        {название группы (в нижнем регистре): коэффициент} — переопределения
        уровня для отдельных групп протокола.
        """
        return {
            override.group_name.strip().lower(): float(
                override.championship_level.coefficient
            )
            for override in self.group_levels.all()
            if override.championship_level
        }

    def coefficient_for_group(self, group_name: str) -> float:
        """
        Коэффициент для конкретной группы: переопределение уровня, если оно
        задано, иначе общий коэффициент протокола.

        Нужно для соревнований, где статус зависит от группы: например,
        в Зеленограде первенство России только у МЖ14 и МЖ16, а остальные
        группы бегут всероссийские соревнования.
        """
        overrides = self.group_coefficients
        if overrides:
            coefficient = overrides.get((group_name or '').strip().lower())
            if coefficient:
                return coefficient
        return self.effective_coefficient

    @property
    def kind(self) -> str:
        """
        Вид источника протокола. Для записей, загруженных до появления поля
        source_kind, определяется по содержимому и ссылке.
        """
        if self.source_kind:
            return self.source_kind
        from .services.source_router import detect_kind_by_payload

        return detect_kind_by_payload(self.raw_html or '', self.source_url or '')

    @property
    def effective_coefficient(self) -> float:
        """Коэффициент старта: уровень первенства или importance, если уровень не задан."""
        if self.championship_level and self.championship_level.coefficient:
            return float(self.championship_level.coefficient)
        return float(self.importance or 1.0)


class ProtocolGroupLevel(models.Model):
    """
    Уровень первенства для отдельной группы протокола.

    Бывает, что в одном протоколе часть групп бежит первенство России, а
    остальные — всероссийские соревнования. Для таких групп коэффициент
    задаётся здесь и перекрывает уровень всего протокола.
    """

    protocol = models.ForeignKey(
        'Protocol',
        on_delete=models.CASCADE,
        related_name='group_levels',
        verbose_name='Протокол',
    )
    group_name = models.CharField(
        'Группа',
        max_length=50,
        help_text='Название группы ровно как в протоколе, например «Юноши до 15 лет».',
    )
    championship_level = models.ForeignKey(
        ChampionshipLevel,
        on_delete=models.CASCADE,
        related_name='group_overrides',
        verbose_name='Уровень первенства',
    )

    class Meta:
        verbose_name = 'Уровень группы в протоколе'
        verbose_name_plural = 'Уровни групп в протоколах'
        unique_together = [['protocol', 'group_name']]
        ordering = ['protocol', 'group_name']

    def __str__(self):
        return f'{self.group_name} → {self.championship_level}'


class KnownParticipant(models.Model):
    """Уже учтённый участник (фамилия + имя + группа) для исключения дублей."""
    last_name = models.CharField('Фамилия', max_length=100)
    first_name = models.CharField('Имя', max_length=100)
    group_name = models.CharField(
        'Группа',
        max_length=50,
        blank=True,
        default='',
        help_text='Например М12, Ж14. Заполните, если есть два участника с одинаковым ФИО в разных группах.',
    )
    veteran = models.BooleanField(
        'Ветеран (не в зачёт сезона)',
        default=False,
        help_text='Если включено, участник не попадает в сезонный зачёт и экспорт по сезону.',
    )
    excluded = models.BooleanField(
        'Не учитывать никогда',
        default=False,
        db_index=True,
        help_text=(
            'Участник не ищется при обработке протоколов и не попадает ни в один '
            'зачёт — ни в основной, ни в ветеранский. Для тех, кто бежал за команду '
            'разово и в состав не входит.'
        ),
    )
    club_only = models.BooleanField(
        'Только за клуб',
        default=False,
        help_text=(
            'Результат засчитывается, только если участник заявлен за клуб — '
            'совпало название команды, по которой обрабатывается протокол. '
            'По базе участников он не ищется: на первенствах России и других '
            'стартах, где он бежит за свой регион или клуб, его результаты не берутся.'
        ),
    )
    group_filter = models.CharField(
        'Допустимые группы',
        max_length=200,
        blank=True,
        default='',
        help_text=(
            'Заполните, если есть полный тёзка из другой возрастной группы. '
            'Через запятую: точные названия групп («М60», «Юноши до 17 лет») '
            'или диапазон — «М21+» (группы М от 21 и старше, включая МЭ) и '
            '«М18-» (группы М до 18 включительно). Результаты в других группах '
            'будут считаться чужими.'
        ),
    )

    split_by_group = models.BooleanField(
        'Разделять по группам',
        default=False,
        help_text=(
            'Включите, если в команде есть двое с одинаковыми фамилией и именем '
            '(например, отец и сын). В зачёте они пойдут отдельными строками '
            'с указанием группы.'
        ),
    )

    def matches_group(self, group_name: str) -> bool:
        """
        Подходит ли результат в этой группе данному участнику.
        Пустой фильтр — подходят любые группы.
        """
        from .services.group_filter import group_matches_rules

        return group_matches_rules(group_name, self.group_filter)

    class Meta:
        verbose_name = 'Участник (база)'
        verbose_name_plural = 'Участники (база)'
        unique_together = [['last_name', 'first_name', 'group_name']]

    def __str__(self):
        name = f'{self.last_name} {self.first_name}'
        if self.group_name:
            name = f'{name} ({self.group_name})'
        return f'{name} — не учитывается' if self.excluded else name


class ProtocolResult(models.Model):
    """Одна строка результата после обработки протокола (место, очки, группа)."""
    protocol = models.ForeignKey(
        Protocol,
        on_delete=models.CASCADE,
        related_name='results',
        verbose_name='Протокол'
    )
    group_name = models.CharField('Группа (например Ж12, М14)', max_length=50, blank=True)
    place = models.CharField('Место (или "снят")', max_length=20, blank=True)
    place_out_of = models.PositiveIntegerField('Место из N', null=True, blank=True)
    rank = models.CharField(
        'Разряд',
        max_length=50,
        blank=True,
        help_text='Заполняется вручную (например МС, КМС, I разряд).',
    )
    last_name = models.CharField('Фамилия', max_length=100)
    first_name = models.CharField('Имя', max_length=100)
    participant = models.ForeignKey(
        KnownParticipant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='results',
        verbose_name='Участник (нормализованный)',
    )
    points = models.PositiveIntegerField('Очки', default=0)
    raw_line = models.CharField('Исходная строка', max_length=500, blank=True)
    team_member = models.BooleanField(
        'В составе команды',
        default=True,
        help_text='False — участник из PDF, не распознанный как член команды; можно включить вручную.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Результат'
        verbose_name_plural = 'Результаты'
        ordering = ['protocol', 'group_name', 'points']

    def __str__(self):
        return f'{self.place} из {self.place_out_of} — {self.last_name} {self.first_name}, очки: {self.points}'

    @property
    def weighted_points(self) -> float:
        """
        Баллы с учётом коэффициента старта: для группы может быть задан свой
        уровень первенства, иначе берётся коэффициент всего протокола.
        """
        if not self.protocol_id:
            return float(self.points or 0)
        return float(self.points or 0) * self.protocol.coefficient_for_group(
            self.group_name
        )


class SeasonRanking(models.Model):
    """Рейтинг за сезон с точными датами периода."""

    SEASON_SPRING = Event.SEASON_SPRING
    SEASON_AUTUMN = Event.SEASON_AUTUMN
    SEASON_CHOICES = Event.SEASON_CHOICES

    year = models.PositiveIntegerField('Год', null=True, blank=True)
    season = models.CharField('Сезон', max_length=10, choices=SEASON_CHOICES, blank=True)
    date_from = models.DateField('Период с', null=True, blank=True)
    date_to = models.DateField('Период по', null=True, blank=True)
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)

    class Meta:
        verbose_name = 'Рейтинг сезона'
        verbose_name_plural = 'Рейтинги сезона'
        ordering = ['-date_from', '-year', '-season']
        # unique_together убран: период задаётся точными датами date_from/date_to

    def __str__(self):
        if self.date_from and self.date_to:
            return f'Рейтинг {self.date_from} — {self.date_to}'
        return f'Рейтинг {self.year or "?"} {self.get_season_display() or ""}'.strip()


class SeasonRankingEntry(models.Model):
    """Итоговые очки одного участника в рамках рейтинга сезона."""

    ranking = models.ForeignKey(
        SeasonRanking,
        on_delete=models.CASCADE,
        related_name='entries',
        verbose_name='Рейтинг сезона',
    )
    participant = models.ForeignKey(
        KnownParticipant,
        on_delete=models.CASCADE,
        related_name='season_rankings',
        verbose_name='Участник',
    )
    points = models.FloatField('Очки за сезон', default=0.0)
    starts_count = models.PositiveIntegerField('Количество зачётных стартов', default=0)

    class Meta:
        verbose_name = 'Запись рейтинга сезона'
        verbose_name_plural = 'Записи рейтингов сезона'
        unique_together = [['ranking', 'participant']]
        ordering = ['-points', 'participant__last_name', 'participant__first_name']

    def __str__(self):
        return f'{self.participant}: {self.points} очков'
