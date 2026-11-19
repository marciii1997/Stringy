import base64
import uuid

from django import forms
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CalculationForm
from .models import Artwork
from .motif import DEFAULTS, LAYERS, PROFILES, analysis_for, framing
from .views import private_artwork


def slider(label):
    return forms.IntegerField(
        label=label, min_value=0, max_value=100, widget=forms.NumberInput(attrs={"type": "range"})
    )


class MixForm(forms.Form):
    background = slider("Hintergrund aufhellen")
    clothing = slider("Kleidung aufhellen")
    focus = slider("Fokus gewichten")
    highlights = slider("Helle Details im Fokus schützen")
    edges = slider("Konturen gewichten")
    pad = forms.BooleanField(label="Bildrand mit Weiß ergänzen", required=False)
    strokes = forms.JSONField(widget=forms.HiddenInput, required=False)
    action = forms.ChoiceField(choices=[("single", "Variante"), ("compare", "Vergleich")])

    def clean_strokes(self):
        strokes = self.cleaned_data["strokes"]
        if strokes is None:
            return []

        def scalar(n, low, high):
            return type(n) in (int, float) and low <= n <= high

        if not isinstance(strokes, list) or len(strokes) > 128:
            raise forms.ValidationError("Maximal 128 Pinselstriche sind möglich.")
        count = 0
        for s in strokes:
            if (
                not isinstance(s, dict)
                or set(s) != {"layer", "radius", "points"}
                or s["layer"] not in LAYERS
                or not scalar(s["radius"], 0.005, 0.2)
            ):
                raise forms.ValidationError("Ungültiger Pinselstrich.")
            points = s["points"]
            if not isinstance(points, list) or not points:
                raise forms.ValidationError("Ungültige Pinselkoordinaten.")
            count += len(points)
            if count > 16000 or not all(
                isinstance(p, list) and len(p) == 2 and all(scalar(v, 0, 1) for v in p) for p in points
            ):
                raise forms.ValidationError("Zu viele oder ungültige Pinselkoordinaten.")
        return strokes


@private_artwork
@permission_required("studio.add_artwork", raise_exception=True)
def editor(request, pk):
    art = get_object_or_404(Artwork.objects.defer("preview", "target", "route", "source"), pk=pk)
    analysis = analysis_for(art)
    data = request.POST if request.method == "POST" else None
    calc = CalculationForm(data, initial={k: getattr(art, k) for k in CalculationForm.Meta.fields})
    mix = MixForm(data, initial={**DEFAULTS, **art.preparation})
    if data is not None and calc.is_valid() and mix.is_valid():
        values = mix.cleaned_data
        compare = values["action"] == "compare"
        group = uuid.uuid4() if compare else None
        source = art.source
        with transaction.atomic():
            for name in PROFILES if compare else ("custom",):
                _, active = PROFILES[name]
                prep = {k: values[k] if k in active else 0 for k in DEFAULTS}
                prep.update(profile=name, strokes=values["strokes"], pad=values["pad"])
                variant = Artwork.objects.create(
                    **calc.cleaned_data, previous=art, source=source, preparation=prep, comparison=group
                )
        return redirect("comparison", group=group) if compare else redirect("simulation", pk=variant.pk)
    return render(
        request,
        "studio/motif.html",
        {
            "art": art,
            "calc": calc,
            "mix": mix,
            "sliders": [mix[k] for k in DEFAULTS],
            "config": {
                "photo": base64.b64encode(analysis.photo).decode(),
                "masks": base64.b64encode(analysis.masks).decode(),
                "framing": framing(analysis),
                "faces": analysis.faces,
                "dimensions": analysis.dimensions,
            },
        },
    )


@private_artwork
def comparison(request, group):
    variants = list(
        Artwork.objects.filter(comparison=group)
        .only("title", "state", "count", "preparation", "previous_id")
        .order_by("pk")
    )
    if not variants:
        raise Http404
    for art in variants:
        art.label = PROFILES[art.preparation["profile"]][0]
    return render(
        request,
        "studio/comparison.html",
        {
            "variants": variants,
            "first": variants[0],
            "pending": any(a.state in (Artwork.State.QUEUED, Artwork.State.RUNNING) for a in variants),
        },
    )
