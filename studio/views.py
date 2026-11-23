from io import BytesIO

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from PIL import Image

from .forms import CalculationForm
from .laser import LaserForm, laser_svg
from .models import Artwork, Calendar


@never_cache
@login_required
def home(request):
    calendars = Calendar.objects.filter(recipient=request.user).order_by("-year", "title")
    return render(request, "studio/home.html", {"calendars": calendars})


def owned(request, pk):
    return get_object_or_404(
        Calendar.objects.select_related("artwork").defer(
            "artwork__source", "artwork__preview", "artwork__target", "artwork__route"
        ),
        pk=pk,
        recipient=request.user,
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
        },
    )


def opened(request, pk, day):
    gift = owned(request, pk)
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
    return render(
        request,
        "studio/door.html",
        {
            "gift": gift,
            "day": day,
            "sequence": path[start:],
            "active": active,
            "current": path[cursor],
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
    Calendar.objects.filter(pk=pk, recipient=request.user, finished=day - 1, step=expected).update(
        step=target
    )
    return redirect(reverse("door", args=[pk, day]) + "#faedeln")


@require_POST
@login_required
def complete(request, pk, day):
    gift = opened(request, pk, day)
    Calendar.objects.filter(pk=gift.pk, recipient=request.user, finished=day - 1).update(
        finished=day, step=0
    )
    return redirect("calendar", pk=pk)


def private_artwork(view):
    return never_cache(
        staff_member_required(permission_required("studio.view_artwork", raise_exception=True)(view))
    )


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
        variant.preparation = art.preparation
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
