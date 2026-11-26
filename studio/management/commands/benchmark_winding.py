"""Compare registered routes using a common target, geometry, and tangent renderer."""

import json
import time
from copy import deepcopy
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw

from studio.nail_clearance import placement_report, plan_winding_sides
from studio.physical import assess, simulation
from studio.winding_study import CAT_REGIONS as CAT


class Command(BaseCommand):
    help = "Vergleicht gespeicherte Pfade auf demselben registrierten Bildausschnitt."

    def add_arguments(self, parser):
        parser.add_argument("--target", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument(
            "--search-seconds",
            type=int,
            default=0,
            help="Gleiches Zeitbudget pro Pfadsuche auf der ersten Route",
        )
        parser.add_argument(
            "--cat", action="store_true", help="Regionen der registrierten Katzenreferenz"
        )
        parser.add_argument("routes", nargs="+", type=Path)

    def handle(self, target, output, cat, routes, search_seconds, **options):
        output.mkdir(exist_ok=True, parents=True)
        target_bytes = target.read_bytes()
        report = []
        panels = [("Vorlage", Image.open(target).convert("L"))]
        geometry = None
        for path in routes:
            route = json.loads(path.read_bytes())
            field = route["settings"]["rectangle_mm"]
            if geometry and field != geometry:
                raise CommandError("Abweichendes Nagelfeld")
            geometry = field
            if not route.get("winding_sides"):
                plan_winding_sides(route)
            image = simulation(route)
            metrics = assess(target_bytes, image, CAT if cat else ())
            report.append(
                dict(
                    file=str(path),
                    **metrics,
                    nails=len(route["anchors"]),
                    steps=len(route["path"]) - 1,
                    **placement_report(route),
                    **route["winding_clearance"],
                )
            )
            profiles = {}
            for width, opacity in ((0.8, 0.75), (1.2, 1.0)):
                view = simulation(route, thread_scale=width, opacity=opacity)
                profiles[str(width * route["settings"]["thread_mm"])] = assess(
                    target_bytes, view, CAT if cat else ()
                )
            report[-1]["profiles"] = profiles
            if len(report) == 1 and search_seconds > 0:
                report[-1]["search_comparison"] = race(route, target_bytes, search_seconds)
            panels.append((path.parent.name, Image.fromarray(image)))
        sheet = Image.new("RGB", (300 * len(panels), 350), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (name, img) in enumerate(panels):
            img.thumbnail((294, 322))
            sheet.paste(img, (i * 300, 22))
            draw.text((i * 300 + 2, 3), name, fill="black")
        sheet.save(output / "vergleich.png")
        (output / "messung.json").write_text(json.dumps(report, indent=2))


def race(route, target, seconds):
    from studio.optical import OpticalRefinement

    results = []
    for three_cut in (False, True):
        candidate = deepcopy(route)
        search = OpticalRefinement(candidate, target, candidate["settings"]["rectangle_mm"])
        path = candidate["path"]
        image = simulation(candidate)
        score = assess(target, image)["score"]
        uncertain = candidate["winding_clearance"]["uncertified_steps"]
        end = time.monotonic() + seconds
        attempts = 0
        while time.monotonic() < end:
            candidate["path"] = search.propose(path, image, 12, three_cut=three_cut, physical=True)
            plan_winding_sides(candidate)
            view = simulation(candidate)
            quality = assess(target, view)["score"]
            if time.monotonic() > end:
                break
            attempts += 1
            risk = candidate["winding_clearance"]["uncertified_steps"]
            if quality > score and risk <= uncertain:
                path, image, score, uncertain = candidate["path"], view, quality, risk
        results.append(
            {
                "cuts": 3 if three_cut else 2,
                "seconds": seconds,
                "attempts": attempts,
                "score": score,
            }
        )
    return results
