import math

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from studio.forms import CalculationForm
from studio.layout_search import fingerprint
from studio.material_winding import fit_yarn
from studio.models import Artwork
from studio.physical import png, simulation


class Command(BaseCommand):
    help = (
        "Berechnet eine neue Garnvariante auf einem vorhandenen rechteckigen Nagelplan."
    )

    def add_arguments(self, parser):
        parser.add_argument("artwork", type=int)
        parser.add_argument("--mm", required=True, type=float)

    def handle(self, artwork, mm, **options):
        if not math.isfinite(mm) or not 0.05 <= mm <= 2:
            raise CommandError("Garnstärke in Millimetern zwischen 0,05 und 2 angeben.")
        try:
            art = Artwork.objects.get(pk=artwork, state=Artwork.State.READY)
        except Artwork.DoesNotExist as exc:
            raise CommandError("Fertiges Ausgangsmotiv nicht gefunden.") from exc
        route = art.route
        if (
            not art.rectangle
            or not art.target
            or not route.get("winding_sides")
            or not route.get("settings", {}).get("nail_positions")
            or len(route["anchors"]) > 350
            or not 24 <= art.count <= 3900
        ):
            raise CommandError(
                "Ein fertiger Handfädelplan mit bis zu 350 Nägeln und 3.900 Zügen wird benötigt."
            )
        recipe = {"revision": 1, "thread_mm": mm, "parent": fingerprint(route)}
        variants = Artwork.objects.filter(
            previous=art, preparation__material_recipe=recipe, state=Artwork.State.READY
        )
        existing = variants.first()
        if existing:
            self.stdout.write(reverse("simulation", args=[existing.pk]))
            return
        target = bytes(art.target)
        candidate = fit_yarn(
            route, target, bytes(art.source), mm, settings.STRINGY_BIN, art.count, 3
        )
        count = len(candidate["path"]) - 1
        preview = png(simulation(candidate, 1024))
        fields = {name: getattr(art, name) for name in CalculationForm.Meta.fields}
        width_label = f"{mm:.2f}".replace(".", ",")
        fields.update(
            title=f"{art.title[:65]} · {width_label} mm",
            thread=mm,
            budget=count,
            auto_nails=0,
            quality_search=False,
            nail_positions=candidate["settings"]["nail_positions"],
            anchors=len(candidate["anchors"]),
        )
        with transaction.atomic():
            locked = Artwork.objects.select_for_update().get(pk=art.pk)
            if fingerprint(locked.route) != recipe["parent"]:
                raise CommandError(
                    "Der Ausgangspfad hat sich während der Berechnung geändert."
                )
            result = variants.first()
            if not result:
                result = Artwork.objects.create(
                    **fields,
                    source=art.source,
                    target=target,
                    preview=preview,
                    previous=art,
                    route=candidate,
                    count=count,
                    state=Artwork.State.READY,
                    preparation={**art.preparation, "material_recipe": recipe},
                )
        self.stdout.write(reverse("simulation", args=[result.pk]))
