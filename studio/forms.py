import math
from typing import ClassVar

from django import forms
from django.contrib.auth import get_user_model

from .models import Artwork


class NailWidget(forms.TextInput):
    template_name = "studio/nail_widget.html"

    class Media:
        js = ("studio/canvas.js", "studio/nails.js")
        css: ClassVar = {"all": ("studio/nails.css",)}


class CalculationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["canvas_format"].required = False
        self.fields["diameter"].required = False
        self.fields["shaft_mm"].required = False
        if self.is_bound and self.data.get("nail_mode") in ("ring", "free"):
            self.fields["nail_positions"].widget.attrs["data-nail-mode"] = self.data["nail_mode"]

    def clean_canvas_format(self):
        return self.cleaned_data["canvas_format"] or Artwork.Format.CIRCLE

    def clean_auto_nails(self):
        return self.cleaned_data["auto_nails"] or 0

    def clean_nail_positions(self):
        points = self.cleaned_data["nail_positions"]
        if self.data.get("nail_mode") == "ring" or (
            self.data.get("nail_mode") != "free" and points in (None, [])
        ):
            return []
        if not isinstance(points, list) or not 3 <= len(points) <= 720:
            raise forms.ValidationError("Platziere zwischen 3 und 720 Nägel auf der Leinwand.")
        for index, p in enumerate(points):
            if (
                not isinstance(p, list)
                or len(p) != 2
                or not all(type(v) in (int, float) and -1 <= v <= 1 for v in p)
                or (self.cleaned_data.get("canvas_format") == Artwork.Format.CIRCLE and math.hypot(*p) > 1 + 1e-12)
            ):
                raise forms.ValidationError("Jeder Nagel muss innerhalb des Nagelfelds liegen.")
            if any(math.dist(p, earlier) < 1e-6 for earlier in points[:index]):
                raise forms.ValidationError("Zwei Nägel liegen auf derselben Position.")
        return points

    def clean(self):
        data = super().clean()
        shape = data.get("canvas_format")
        if data.get("shaft_mm") is None and "shaft_mm" not in self.errors:
            data["shaft_mm"] = 1.2
        if shape == Artwork.Format.CIRCLE:
            if data.get("diameter") is None:
                self.add_error("diameter", "Bitte den Ankerkreisdurchmesser eingeben.")
            data["canvas_width"], data["canvas_height"] = 297, 420
        else:
            data["diameter"] = 600  # Only used by circular canvases.
            if shape in (Artwork.Format.A3_PORTRAIT, Artwork.Format.A3_LANDSCAPE):
                data["canvas_width"], data["canvas_height"] = (
                    (297, 420) if shape == Artwork.Format.A3_PORTRAIT else (420, 297)
                )
            elif shape == Artwork.Format.CUSTOM:
                for field in ("canvas_width", "canvas_height"):
                    if data.get(field) is None and field not in self.errors:
                        self.add_error(field, "Bitte ein Werkstückmaß zwischen 100 und 2000 mm eingeben.")
        if data.get("nail_positions"):
            data["anchors"] = len(data["nail_positions"])
        elif "nail_positions" in data and data.get("anchors", 24) < 24:
            self.add_error("anchors", "Für den Randplan sind mindestens 24 Nägel erforderlich.")
        if data.get("anchors", 0) + data.get("auto_nails", 0) > 720:
            self.add_error(
                "auto_nails", "Grundnägel und Zusatznägel dürfen zusammen höchstens 720 ergeben."
            )
        return data

    class Meta:
        model = Artwork
        fields = (
            "title",
            "canvas_format",
            "canvas_width",
            "canvas_height",
            "shaft_mm",
            "anchors",
            "nail_positions",
            "auto_nails",
            "diameter",
            "thread",
            "budget",
            "passes",
            "quality_search",
            "detail",
            "opacity",
            "zoom",
            "center_x",
            "center_y",
            "gamma",
        )
        widgets: ClassVar = {"nail_positions": NailWidget}
        labels: ClassVar = {
            "diameter": "Ankerkreisdurchmesser (mm)",
            "nail_positions": "Grundplan für die Nägel",
            "anchors": "Anzahl Grundnägel",
        }
        help_texts: ClassVar = {
            "diameter": "Für ein Werkstück mit Ø 600 mm und 5 mm Rand den Ankerkreis auf 590 mm setzen.",
            "shaft_mm": "Gemessener Durchmesser des Schafts unter dem Kopf. Bei Rechtecken plant Stringy zusätzlich 0,5 mm Abstand zum Faden ein.",
            "canvas_format": "Beim Rechteck liegen die äußeren Nagelmitten 5 mm von jeder Werkstückkante entfernt. Nagel 0 des Randplans liegt oben links.",
            "quality_search": "Berechnet zwei Pfade bei 512 und 1024 Pixeln und wählt anhand der Fadensimulation. Für einen schnellen Entwurf ausschalten.",
            "passes": "Versetzt frühere Anker, tauscht kreisweit Verbindungen aus und sucht weitere Fadenzüge. 0 überspringt die Nachbesserung.",
            "zoom": "1 füllt das Nagelfeld ohne Verzerrung aus. 2 halbiert den Ausschnitt. Werte unter 1 erweitern den Ausschnitt über das Foto hinaus.",
            "center_x": "0 ist links, 100 rechts.",
            "center_y": "0 ist oben, 100 unten.",
            "gamma": "Werte unter 1 hellen die Mitteltöne auf. 1 übernimmt die Helligkeit des Fotos.",
            "detail": "0 gewichtet alle Bildbereiche gleich. Höhere Werte bevorzugen lokale Kontraste, etwa Augen und Fellzeichnung.",
            "opacity": "1 entspricht deckendem Schwarz. Kleinere Werte simulieren durchscheinenden Faden.",
            "thread": "Durchmesser des verwendeten Fadens in Millimetern.",
        }


class PhotoUploadForm(forms.Form):
    photo = forms.ImageField(
        label="Dein Foto",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="JPG, PNG oder WebP · bis 10 MB",
    )

    def clean_photo(self):
        return validate_photo(self.cleaned_data["photo"])


def validate_photo(photo):
    if photo.image.format not in {"JPEG", "PNG", "WEBP"} or photo.size > 10 * 1024**2 or max(photo.image.size) > 8192:
        raise forms.ValidationError("Wähle ein JPG-, PNG- oder WebP-Bild bis 10 MB und 8192 Pixel je Seite.")
    return photo


class GiftForm(forms.Form):
    recipient = forms.ModelChoiceField(
        label="Für wen ist der Kalender?",
        queryset=get_user_model().objects.filter(is_active=True).order_by("username"),
        empty_label="Person auswählen",
    )
    title = forms.CharField(label="Name auf dem Kalender", max_length=100, required=False,
                            initial="Ein Faden im Advent", help_text="Der Name ist für die beschenkte Person sichtbar.")

    def clean_title(self):
        return self.cleaned_data["title"] or self.fields["title"].initial
