from datetime import date

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class MotifAnalysis(models.Model):
    version = models.PositiveSmallIntegerField(default=2)
    original = models.OneToOneField("Artwork", on_delete=models.CASCADE)
    photo = models.BinaryField()
    masks = models.BinaryField()
    faces = models.JSONField(default=list)
    dimensions = models.JSONField()


class Artwork(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "In Warteschlange"
        RUNNING = "running", "Wird berechnet"
        READY = "ready", "Bereit"
        FAILED = "failed", "Fehlgeschlagen"

    title = models.CharField("Interner Motivname", max_length=100)
    source = models.BinaryField()
    nail_positions = models.JSONField("Nagelanordnung", default=list, blank=True)
    anchors = models.PositiveIntegerField(
        "Anker", default=360, validators=[MinValueValidator(3), MaxValueValidator(720)]
    )
    diameter = models.FloatField(
        "Durchmesser (mm)", default=600, validators=[MinValueValidator(100), MaxValueValidator(2000)]
    )
    thread = models.FloatField(
        "Fadenstärke (mm)", default=0.25, validators=[MinValueValidator(0.05), MaxValueValidator(2)]
    )
    detail = models.FloatField(
        "Detailgewicht", default=1, validators=[MinValueValidator(0), MaxValueValidator(20)]
    )
    zoom = models.FloatField(
        "Vergrößerung", default=1, validators=[MinValueValidator(0.01), MaxValueValidator(5)]
    )
    center_x = models.FloatField(
        "Bildmitte horizontal (%)", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    center_y = models.FloatField(
        "Bildmitte vertikal (%)", default=50, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    gamma = models.FloatField(
        "Helligkeit (Gamma)", default=1, validators=[MinValueValidator(0.3), MaxValueValidator(3)]
    )
    quality_search = models.BooleanField("Qualitätsvergleich mit feinem Rechenraster", default=True)
    passes = models.PositiveSmallIntegerField(
        "Nachbesserungsrunden", default=3, validators=[MaxValueValidator(12)]
    )
    opacity = models.FloatField(
        "Fadendeckkraft", default=1, validators=[MinValueValidator(0.05), MaxValueValidator(1)]
    )
    preparation = models.JSONField(default=dict, editable=False)
    comparison = models.UUIDField(null=True, db_index=True, editable=False)
    previous = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, editable=False)
    budget = models.PositiveIntegerField(
        "Maximale Verbindungen",
        default=12000,
        validators=[MinValueValidator(24), MaxValueValidator(12000)],
    )
    state = models.CharField("Status", max_length=10, choices=State, default=State.QUEUED)
    claimed_at = models.DateTimeField(null=True)
    error = models.TextField("Fehler", blank=True)
    preview = models.BinaryField(default=bytes)
    target = models.BinaryField(default=bytes)
    route = models.JSONField(default=dict)
    count = models.PositiveIntegerField("Verbindungen", default=0)

    class Meta:
        verbose_name = "Motiv"
        verbose_name_plural = "Motive"

    def __str__(self):
        return f"{self.title} (#{self.pk})"


def advent_year():
    today = timezone.localdate()
    return today.year + (today.month == 12 and today.day > 24)


class Calendar(models.Model):
    title = models.CharField(
        "Kalendername",
        max_length=100,
        help_text="Für die beschenkte Person sichtbar. Wähle einen Namen, der das Motiv geheim hält.",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Beschenkte Person", on_delete=models.PROTECT
    )
    artwork = models.ForeignKey(
        Artwork,
        verbose_name="Motiv",
        on_delete=models.PROTECT,
        limit_choices_to={"state": "ready", "count__gte": 24},
    )
    year = models.PositiveIntegerField(
        "Adventsjahr", default=advent_year, validators=[MinValueValidator(2000), MaxValueValidator(9999)]
    )
    step = models.PositiveIntegerField("Verbindungen im aktuellen Türchen", default=0, editable=False)
    finished = models.PositiveSmallIntegerField("Fertige Türchen", default=0, editable=False)

    class Meta:
        verbose_name = "Adventskalender"
        verbose_name_plural = "Adventskalender"

    def __str__(self):
        return self.title

    def unlocked(self, today=None):
        elapsed = ((today or timezone.localdate()) - date(self.year, 12, 1)).days
        return max(0, min(24, elapsed + 1))

    def bounds(self, day):
        # Adjacent slices share the anchor at the boundary.
        each, extra = divmod(self.artwork.count, 24)
        start = (day - 1) * each + min(day - 1, extra)
        return start, start + each + (day <= extra)
