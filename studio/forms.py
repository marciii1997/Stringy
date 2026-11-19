from typing import ClassVar

from django import forms

from .models import Artwork


class CalculationForm(forms.ModelForm):
    class Meta:
        model = Artwork
        fields = (
            "title",
            "anchors",
            "diameter",
            "thread",
            "budget",
            "passes",
            "detail",
            "opacity",
            "zoom",
            "center_x",
            "center_y",
            "gamma",
        )
        labels: ClassVar = {"diameter": "Ankerkreisdurchmesser (mm)"}
        help_texts: ClassVar = {
            "diameter": "Für ein Werkstück mit Ø 600 mm und 5 mm Rand den Ankerkreis auf 590 mm setzen.",
            "passes": "Bis zu 12 Runden versetzen frühere Anker und suchen danach weitere Verbindungen. Mehr Runden benötigen mehr Rechenzeit.",
            "zoom": "1 entspricht der kurzen Bildseite. 2 halbiert den Ausschnitt. Werte unter 1 ergänzen Weißraum.",
            "center_x": "0 ist links, 100 rechts.",
            "center_y": "0 ist oben, 100 unten.",
            "gamma": "Werte unter 1 hellen die Mitteltöne auf. 1 übernimmt die Helligkeit des Fotos.",
            "detail": "0 gewichtet alle Bildbereiche gleich. Höhere Werte bevorzugen lokale Kontraste, etwa Augen und Fellzeichnung.",
            "opacity": "1 entspricht deckendem Schwarz. Kleinere Werte simulieren durchscheinenden Faden.",
            "thread": "Durchmesser des verwendeten Fadens in Millimetern.",
        }
