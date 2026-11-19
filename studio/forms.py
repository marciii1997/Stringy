import math
from typing import ClassVar

from django import forms

from .models import Artwork


class NailWidget(forms.TextInput):
    template_name = "studio/nail_widget.html"

    class Media:
        js = ("studio/nails.js",)
        css: ClassVar = {"all": ("studio/nails.css",)}


class CalculationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.data.get("nail_mode") in ("ring", "free"):
            self.fields["nail_positions"].widget.attrs["data-nail-mode"] = self.data["nail_mode"]

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
                or math.hypot(*p) > 1 + 1e-12
            ):
                raise forms.ValidationError("Jeder Nagel muss innerhalb des Kreises liegen.")
            if any(math.dist(p, earlier) < 1e-6 for earlier in points[:index]):
                raise forms.ValidationError("Zwei Nägel liegen auf derselben Position.")
        return points

    def clean(self):
        data = super().clean()
        if data.get("nail_positions"):
            data["anchors"] = len(data["nail_positions"])
        elif "nail_positions" in data and data.get("anchors", 24) < 24:
            self.add_error("anchors", "Für den Randkreis sind mindestens 24 Nägel erforderlich.")
        return data

    class Meta:
        model = Artwork
        fields = (
            "title",
            "anchors",
            "nail_positions",
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
        labels: ClassVar = {"diameter": "Ankerkreisdurchmesser (mm)"}
        help_texts: ClassVar = {
            "diameter": "Für ein Werkstück mit Ø 600 mm und 5 mm Rand den Ankerkreis auf 590 mm setzen.",
            "quality_search": "Berechnet zwei Pfade bei 512 und 1024 Pixeln und wählt anhand der Fadensimulation. Für einen schnellen Entwurf ausschalten.",
            "passes": "Versetzt frühere Anker, tauscht kreisweit Verbindungen aus und sucht weitere Fadenzüge. 0 überspringt die Nachbesserung.",
            "zoom": "1 entspricht der kurzen Bildseite. 2 halbiert den Ausschnitt. Werte unter 1 erweitern den Ausschnitt über das Foto hinaus.",
            "center_x": "0 ist links, 100 rechts.",
            "center_y": "0 ist oben, 100 unten.",
            "gamma": "Werte unter 1 hellen die Mitteltöne auf. 1 übernimmt die Helligkeit des Fotos.",
            "detail": "0 gewichtet alle Bildbereiche gleich. Höhere Werte bevorzugen lokale Kontraste, etwa Augen und Fellzeichnung.",
            "opacity": "1 entspricht deckendem Schwarz. Kleinere Werte simulieren durchscheinenden Faden.",
            "thread": "Durchmesser des verwendeten Fadens in Millimetern.",
        }
