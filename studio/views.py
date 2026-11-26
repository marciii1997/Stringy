from io import BytesIO
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from PIL import Image

from .forms import CalculationForm, GiftForm, PhotoUploadForm
from . import handcraft
from .laser import LaserForm, laser_svg
from .models import Artwork, Calendar, advent_year


def accessible_calendars(user):
    return Calendar.objects.filter(Q(tester=user) | Q(tester__isnull=True, recipient=user))


@never_cache
@login_required
def home(request):
    calendars = accessible_calendars(request.user).order_by("-year", "title")
    return render(request, "studio/home.html", {"calendars": calendars})


def owned(request, pk):
    return get_object_or_404(
        accessible_calendars(request.user).select_related("artwork").defer(
            "artwork__source", "artwork__preview", "artwork__target", "artwork__route"
        ),
        pk=pk,
    )


@never_cache
@login_required
def calendar(request, pk):
    gift = owned(request, pk)
    return render(
        request,
        "studio/calendar.html",
        {
            "gift": gift,
            "days": range(1, 25),
            "unlocked": gift.unlocked(),
            "next_day": gift.finished + 1,
        },
    )


def opened(request, pk, day):
    gift = owned(request, pk)
    if request.method == "POST" and (request.POST.get("preview") == "1") != bool(gift.tester_id):
        raise Http404
    if not 1 <= day <= gift.unlocked():
        raise Http404
    return gift


@never_cache
@login_required
def door(request, pk, day):
    gift = opened(request, pk, day)
    start, end = gift.bounds(day)
    # Only the released prefix crosses the recipient's trust boundary.
    path = gift.artwork.route["path"][: end + 1]
    active = day == gift.finished + 1
    cursor = start + gift.step if active else end
    directions = gift.artwork.route.get("winding_sides", [])
    def winding_direction(index):
        if 0 <= index < len(directions):
            return {"cw": "im Uhrzeigersinn", "ccw": "gegen den Uhrzeigersinn"}.get(directions[index])
        return None

    return render(
        request,
        "studio/door.html",
        {
            "gift": gift,
            "day": day,
            "sequence": path[start:],
            "day_directions": [
                (pin, winding_direction(index)) for index, pin in enumerate(path[start:], start)
            ] if len(directions) >= end + 1 else None,
            "active": active,
            "current": path[cursor],
            "current_direction": winding_direction(cursor),
            "following_direction": winding_direction(cursor + 1) if cursor < end else None,
            "following": path[cursor + 1] if cursor < end else None,
            "count": end - start,
            "playback": {
                "path": path,
                "start": start,
                "initial": cursor - start,
                **playback_layout(gift.artwork, path),
                "opacity": gift.artwork.opacity,
                "width": gift.artwork.thread / max(gift.artwork.field_size),
            },
        },
    )


@require_POST
@login_required
def thread_step(request, pk, day):
    gift = opened(request, pk, day)
    start, end = gift.bounds(day)
    try:
        expected = int(request.POST["step"])
        delta = {"next": 1, "back": -1}[request.POST["action"]]
    except (ValueError, KeyError):
        return HttpResponseBadRequest("Ungültiger Fädelschritt.")
    target = expected + delta
    if not (0 <= expected <= end - start and 0 <= target <= end - start):
        return HttpResponseBadRequest("Schritt außerhalb dieses Türchens.")
    accessible_calendars(request.user).filter(pk=pk, tester_id=gift.tester_id, finished=day - 1, step=expected).update(
        step=target
    )
    return redirect(reverse("door", args=[pk, day]) + "#faedeln")


@require_POST
@login_required
def complete(request, pk, day):
    gift = opened(request, pk, day)
    accessible_calendars(request.user).filter(pk=gift.pk, tester_id=gift.tester_id, finished=day - 1).update(
        finished=day, step=0
    )
    return redirect("calendar", pk=pk)


@require_POST
@staff_member_required
@permission_required("studio.add_calendar", raise_exception=True)
def publish_calendar(request, pk):
    with transaction.atomic():
        gift = get_object_or_404(Calendar.objects.select_for_update(), pk=pk, tester=request.user)
        gift.tester = None
        gift.finished = gift.step = 0
        gift.save(update_fields=["tester", "finished", "step"])
    messages.success(request, f"„{gift.title}“ ist jetzt im Konto von {gift.recipient}. Die Türchen öffnen sich im Advent {gift.year}.")
    return redirect("atelier")


def private_artwork(view):
    return never_cache(
        staff_member_required(permission_required("studio.view_artwork", raise_exception=True)(view))
    )


@private_artwork
def atelier(request):
    motifs = Artwork.objects.only("title", "state", "count").order_by("-pk")
    return render(request, "studio/atelier.html", {"page": Paginator(motifs, 18).get_page(request.GET.get("seite"))})


@private_artwork
@permission_required("studio.add_artwork", raise_exception=True)
def upload(request):
    form = PhotoUploadForm(request.POST if request.method == "POST" else None, request.FILES)
    if request.method == "POST" and form.is_valid():
        photo = form.cleaned_data["photo"]
        photo.seek(0)
        source = photo.read()
        with Image.open(BytesIO(source)) as image:
            w, h = image.size
            if image.getexif().get(274) in (5, 6, 7, 8):
                w, h = h, w
        width, height = (max(100, round(490 * side / max(w, h)) + 10) for side in (w, h))
        art = Artwork.objects.create(
            title=Path(photo.name).stem[:100] or "Mein Foto", source=source,
            canvas_format=Artwork.Format.CUSTOM, canvas_width=width, canvas_height=height,
            thread=handcraft.THREAD_MM, shaft_mm=handcraft.SHAFT_MM, opacity=handcraft.OPACITY,
            budget=handcraft.SEGMENTS, anchors=handcraft.MAX_NAILS, detail=4, passes=3,
            quality_search=False, preparation={"handcraft": True, "automatic_layout": True},
        )
        return redirect("simulation", pk=art.pk)
    return render(request, "studio/upload.html", {"form": form})


@private_artwork
@permission_required("studio.add_calendar", raise_exception=True)
def give(request, pk):
    art = get_object_or_404(Artwork.objects.only("title"), pk=pk, state=Artwork.State.READY, count__gte=24)
    form = GiftForm(request.POST if request.method == "POST" else None)
    year = advent_year()
    if form.is_valid():
        values = dict(form.cleaned_data)
        tester = request.user if values.pop("instant_open") else None
        with transaction.atomic():
            fields = dict(artwork=art, year=year, tester=tester, **values)
            gift = Calendar.objects.filter(**fields).first() or Calendar.objects.create(**fields)
        if tester:
            return redirect("calendar", pk=gift.pk)
        messages.success(request, f"„{gift.title}“ ist im Konto von {gift.recipient} für den Advent {year} bereit.")
        return redirect("atelier")
    return render(request, "studio/give.html", {"art": art, "form": form, "year": year})


def playback_layout(art, released=None):
    positions = art.winding_positions
    layout = {"anchors": len(positions) or art.anchors}
    if art.rectangle:
        layout["rectangle"] = art.rectangle
    if positions:
        ids = range(len(positions)) if released is None else sorted(set(released))
        layout["positions"] = {i: positions[i] for i in ids}
    if len(positions) > art.anchors:
        layout["firstExtra"] = art.anchors
    return layout


@private_artwork
def simulation(request, pk):
    art = get_object_or_404(Artwork.objects.defer("source", "target", "preview"), pk=pk)
    if art.state != Artwork.State.READY:
        return render(request, "studio/status.html", {"art": art})
    return render(
        request,
        "studio/simulation.html",
        {
            "art": art,
            "playback": {
                "path": art.route["path"],
                "start": 0,
                **playback_layout(art),
                "opacity": art.opacity,
                "width": art.thread / max(art.field_size),
            },
        },
    )


@private_artwork
@permission_required("studio.add_artwork", raise_exception=True)
@require_POST
def handcraft_variant(request, pk):
    art = get_object_or_404(Artwork, pk=pk, state=Artwork.State.READY)
    positions = art.winding_positions
    if len(positions) > handcraft.MAX_NAILS:
        positions = []
    values = {field: getattr(art, field) for field in CalculationForm.Meta.fields}
    values.update(
        title=f"{art.title[:82]} · Handfädeln",
        thread=handcraft.THREAD_MM,
        shaft_mm=handcraft.SHAFT_MM,
        opacity=handcraft.OPACITY,
        budget=handcraft.SEGMENTS,
        detail=4,
        passes=3,
        quality_search=False,
        auto_nails=0,
        anchors=len(positions) or min(art.anchors, handcraft.MAX_NAILS),
        nail_positions=positions,
    )
    variant = Artwork.objects.create(
        **values,
        source=art.source,
        previous=art,
        preparation={"handcraft": True, "pad": art.preparation.get("pad", False)},
    )
    return redirect("simulation", pk=variant.pk)


@private_artwork
@permission_required("studio.add_artwork", raise_exception=True)
def recalculate(request, pk):
    art = get_object_or_404(Artwork.objects.defer("target", "preview", "route", "source"), pk=pk)
    initial = {field: getattr(art, field) for field in CalculationForm.Meta.fields}
    form = CalculationForm(request.POST if request.method == "POST" else None, initial=initial)
    form.fields["nail_positions"].widget.attrs.update(
        {
            "data-photo-url": reverse("asset", args=[art.pk, "source"]),
            "data-pad": "1" if art.preparation.get("pad") else "0",
        }
    )
    if form.is_valid():
        variant = form.save(commit=False)
        variant.source = art.source
        variant.preparation = {key: value for key, value in art.preparation.items() if key != "handcraft"}
        variant.previous = art
        variant.save()
        return redirect("simulation", pk=variant.pk)
    return render(request, "studio/recalculate.html", {"form": form, "art": art})


@private_artwork
def laser(request, pk):
    art = get_object_or_404(
        Artwork.objects.only("title", "diameter", "anchors", "route", "nail_positions"),
        pk=pk,
        state=Artwork.State.READY,
    )
    form = LaserForm(
        request.POST if request.method == "POST" else None,
        diameter=art.diameter,
        rectangle=art.rectangle,
        count=art.winding_anchor_count,
        free_anchors=art.route.get("anchors") if art.winding_positions else None,
    )
    if form.is_valid():
        response = HttpResponse(laser_svg(art.route, **form.cleaned_data), content_type="image/svg+xml")
        response["Content-Disposition"] = f'attachment; filename="stringy-anker-{art.pk}.svg"'
        return response
    return render(request, "studio/laser.html", {"art": art, "form": form})


@private_artwork
def asset(request, pk, kind):
    if kind == "source":
        art = get_object_or_404(Artwork.objects.only("source"), pk=pk)
        with Image.open(BytesIO(art.source)) as photo:
            mime = Image.MIME[photo.format]
        return HttpResponse(bytes(art.source), content_type=mime)
    if kind not in ("preview", "target", "route"):
        raise Http404
    art = get_object_or_404(Artwork.objects.only(kind), pk=pk, state=Artwork.State.READY)
    if kind == "route":
        response = JsonResponse(art.route)
        response["Content-Disposition"] = 'attachment; filename="fadenpfad.json"'
        return response
    return HttpResponse(getattr(art, kind), content_type="image/png")
