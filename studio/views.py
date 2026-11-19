from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, permission_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

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
    return render(
        request,
        "studio/door.html",
        {
            "gift": gift,
            "day": day,
            "sequence": path[start:],
            "count": end - start,
            "playback": {
                "path": path,
                "start": start,
                "anchors": gift.artwork.anchors,
                "width": gift.artwork.thread / gift.artwork.diameter,
            },
        },
    )


@require_POST
@login_required
def complete(request, pk, day):
    gift = opened(request, pk, day)
    Calendar.objects.filter(pk=gift.pk, recipient=request.user, finished=day - 1).update(finished=day)
    return redirect("calendar", pk=pk)


def private_artwork(view):
    return never_cache(
        staff_member_required(permission_required("studio.view_artwork", raise_exception=True)(view))
    )


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
                "anchors": art.anchors,
                "width": art.thread / art.diameter,
            },
        },
    )


@private_artwork
def asset(request, pk, kind):
    if kind not in ("preview", "target", "route"):
        raise Http404
    art = get_object_or_404(Artwork.objects.only(kind), pk=pk, state=Artwork.State.READY)
    if kind == "route":
        response = JsonResponse(art.route)
        response["Content-Disposition"] = 'attachment; filename="fadenpfad.json"'
        return response
    return HttpResponse(getattr(art, kind), content_type="image/png")
