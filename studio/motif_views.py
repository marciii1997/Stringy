import base64
import uuid
from io import BytesIO

from django import forms
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from PIL import Image

from .forms import CalculationForm
from .models import Artwork
from .motif import (
    BACKGROUNDS,
    DEFAULTS,
    GRADIENT,
    LAYERS,
    PROFILES,
    STYLES,
    analysis_for,
    encoded,
    framing,
    framing_points,
    style_samples,
)
from .views import private_artwork


def slider(label):
    return forms.IntegerField(
        label=label, min_value=0, max_value=100, widget=forms.NumberInput(attrs={"type": "range"})
    )


class MixForm(forms.Form):
    style = forms.ChoiceField(
        label="Bildstil", choices=tuple(STYLES.items()), initial="natural", required=False
    )
    background_color = forms.ChoiceField(
        label="Hintergrundfarbe",
        choices=tuple(BACKGROUNDS.items()),
        initial="white",
        required=False,
    )
    background = slider("Hintergrundstärke")
    clothing = slider("Kleidung aufhellen")
    focus = slider("Fokus gewichten")
    highlights = slider("Helle Details im Fokus schützen")
    edges = slider("Konturen gewichten")
    pad = forms.BooleanField(label="Ausschnitt über den Bildrand hinaus erlauben", required=False)
    strokes = forms.JSONField(widget=forms.HiddenInput, required=False)
    action = forms.ChoiceField(
        choices=[
            ("single", "Variante"),
            ("compare", "Vergleich"),
            ("styles", "Stilvergleich"),
            ("threads", "Fadenstärkenvergleich"),
        ]
    )

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
        action = values["action"]
        group = None if action == "single" else uuid.uuid4()
        style = values["style"] or "natural"
        thread = calc.cleaned_data["thread"]
        if action == "threads":
            variants = [("threads", style, width) for width in (0.15, 0.25)]
        elif action == "styles":
            variants = [("styles", name, thread) for name in STYLES]
        else:
            profiles = PROFILES if action == "compare" else ("custom",)
            variants = [(name, style if name == "custom" else "natural", thread) for name in profiles]
        source = art.source
        with transaction.atomic():
            for name, style, width in variants:
                _, active = PROFILES.get(name, PROFILES["custom"])
                prep = {k: values[k] if k in active else 0 for k in DEFAULTS}
                if "background" in active:
                    prep["background_color"] = values["background_color"] or "white"
                prep.update(
                    style=style,
                    profile=name,
                    strokes=values["strokes"],
                    pad=values["pad"],
                )
                variant = Artwork.objects.create(
                    **{**calc.cleaned_data, "thread": width},
                    previous=art,
                    source=source,
                    preparation=prep,
                    comparison=group,
                )
        return redirect("comparison", group=group) if group else redirect("simulation", pk=variant.pk)
    with Image.open(BytesIO(analysis.photo)) as photo:
        samples = style_samples(photo)
        if photo.mode != "RGBA":
            del samples["natural"]
        styles = {key: base64.b64encode(encoded(gray, "PNG")).decode() for key, gray in samples.items()}
    corners = framing_points(analysis)
    return render(
        request,
        "studio/motif.html",
        {
            "art": art,
            "calc": calc,
            "mix": mix,
            "sliders": [mix[k] for k in DEFAULTS],
            "config": {
                "styles": styles,
                "gradient": GRADIENT,
                "photo": base64.b64encode(analysis.photo).decode(),
                "masks": base64.b64encode(analysis.masks).decode(),
                "framing": framing(analysis, corners),
                "framingCorners": corners,
                "faces": analysis.faces,
                "dimensions": analysis.dimensions,
            },
        },
    )


@private_artwork
def comparison(request, group):
    variants = list(
        Artwork.objects.filter(comparison=group)
        .only("title", "state", "count", "thread", "preparation", "previous_id")
        .order_by("pk")
    )
    if not variants:
        raise Http404
    for art in variants:
        profile = art.preparation["profile"]
        if profile == "threads":
            art.label = f"{art.thread:g} mm".replace(".", ",")
        elif profile == "styles":
            art.label = STYLES[art.preparation["style"]]
        else:
            art.label = PROFILES[profile][0]
    return render(
        request,
        "studio/comparison.html",
        {
            "variants": variants,
            "first": variants[0],
            "pending": any(a.state in (Artwork.State.QUEUED, Artwork.State.RUNNING) for a in variants),
        },
    )
