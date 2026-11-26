"""Compare nail layouts on one fixed image crop and save the best as a new artwork."""

import copy
import json
import math
import subprocess
import tempfile
from collections import Counter
from io import BytesIO
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from studio.auto_nails import compact_route, normalized_positions, suggest_positions
from studio.management.commands.process_art import optimizer_command
from studio.models import Artwork
from studio.motif import DEFAULTS, analysis_for, encoded, guide_for
from studio.quality import view_quality

LIMIT = 350
HEAD_MM = 3.0


def importance(target, position):
    """Look slightly inward from the perimeter to avoid sampling padded corners."""
    x, y = position
    h, w = target.shape
    values = []
    for inset in (0.99, 0.94, 0.86):
        px, py = (
            round((1 + inset * x) * (w - 1) / 2),
            round((1 + inset * y) * (h - 1) / 2),
        )
        values.append(target[py, px])
    return 255 - min(values)


def adapt_rim(pins, target, count):
    # Reserve distributed return points, then fill the strongest remaining arcs.
    keep = set(
        np.linspace(0, len(pins), min(count // 4, len(pins)), endpoint=False, dtype=int)
    )
    for i in sorted(
        range(len(pins)), key=lambda i: importance(target, pins[i]), reverse=True
    ):
        if len(keep) >= count:
            break
        keep.add(i)
    return [pins[i] for i in sorted(keep)]


def with_nail_heads(preview, route, dims):
    # Approximate the visible opaque nail heads on the high-resolution thread preview.
    image = np.array(Image.open(BytesIO(preview)).convert("L"))
    h, w = image.shape
    for x, y in normalized_positions(route, dims):
        center = round((x + 1) * (w - 1) * 8), round((y + 1) * (h - 1) * 8)
        cv2.circle(
            image,
            center,
            round(8 * HEAD_MM * (w - 1) / dims[0]),
            87,
            -1,
            cv2.LINE_AA,
            4,
        )
    return image


def material_score(reference, image, focus, rectangular):
    q = view_quality(reference, encoded(Image.fromarray(image), "PNG"), focus, rectangular)
    target = np.asarray(Image.open(BytesIO(reference)).convert("L"))
    sh, sw = target.shape
    white = target >= 245
    if not rectangular:
        yy, xx = np.ogrid[-1 : 1 : sh * 1j, -1 : 1 : sw * 1j]
        white &= xx**2 + yy**2 <= 1
    rendered = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
    intrusion = float((255 - rendered[white]).mean() / 255) if white.any() else 0
    return q["ssim"] - q["tone_rmse"] - 0.25 * intrusion, {
        **q,
        "white_darkness": intrusion,
    }


def optimize(original, folder, log, review_dir=None):
    art = copy.copy(original)
    art.nail_positions = []
    art.passes = max(3, original.passes)
    dims = art.field_size
    perimeter_mm = 2 * sum(dims) if art.rectangle else math.pi * dims[0]
    rim_count = min(LIMIT, int(perimeter_mm / (HEAD_MM + 1)))
    source = folder / "source"
    source.write_bytes(original.source)
    guided = (
        any(art.preparation.get(k) for k in DEFAULTS)
        or art.preparation.get("style", "natural") != "natural"
    )
    analysis = analysis_for(original) if guided else None
    guides = {}
    trials = []
    reference = focus = best = None

    def trial(name, positions, raster):
        nonlocal reference, focus, best
        if positions and not 3 <= len(positions) <= LIMIT:
            raise CommandError("Ungültige Nagelzahl vor der Suche.")
        art.nail_positions = positions
        art.anchors = len(positions) or rim_count
        cmd = optimizer_command(art, source, folder, raster)
        if not art.rectangle:
            cmd.extend(["--shaft-mm", str(art.shaft_mm)])
        if analysis is not None:
            if raster not in guides:
                guide, ink = guide_for(art, analysis, raster)
                gpath = folder / f"guide-{raster}.png"
                guide.save(gpath)
                guides[raster] = ["--guide", str(gpath)]
                if ink is not None:
                    ipath = folder / f"ink-{raster}.png"
                    ink.save(ipath)
                    guides[raster].extend(["--background-ink", str(ipath)])
                if reference is None:
                    focus = guide.getchannel("G")
            cmd.extend(guides[raster])
        try:
            subprocess.run(cmd, check=True, timeout=1800)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if best is None:
                raise CommandError(
                    f"{name}: Optimierer fehlgeschlagen ({exc})."
                ) from exc
            trials.append(
                {"layout": name, "raster": raster, "error": type(exc).__name__}
            )
            log(
                f"{name}: {type(exc).__name__}. Bereits berechnete Pfade bleiben verfügbar."
            )
            return None
        route = json.loads((folder / "path.json").read_bytes())
        full = normalized_positions(route, dims)
        preview = (folder / "preview.png").read_bytes()
        target = (folder / "target.png").read_bytes()
        if reference is None:
            reference = target
            if review_dir is not None:
                (review_dir / "target.png").write_bytes(reference)
        if compact_route(route, dims):
            material = with_nail_heads(preview, route, dims)
            score, metrics = material_score(
                reference, material, focus, bool(art.rectangle)
            )
            count = len(route["anchors"])
            assert (
                count
                == len(set(route["path"]))
                == len(route["settings"]["nail_positions"])
                <= LIMIT
            )
            if review_dir is not None:
                stem = f"variant-{len(trials) + 1:02d}"
                (review_dir / f"{stem}-threads.png").write_bytes(preview)
                Image.fromarray(material).save(review_dir / f"{stem}-with-nails.png")
                (review_dir / f"{stem}-route.json").write_text(
                    json.dumps(route), encoding="utf-8"
                )
                metrics.update(
                    files=stem,
                    min_anchor_spacing_mm=min(
                        math.hypot(a["x_mm"] - b["x_mm"], a["y_mm"] - b["y_mm"])
                        for a, b in combinations(route["anchors"], 2)
                    ),
                    max_anchor_visits=max(Counter(route["path"]).values()),
                )
            trials.append(
                dict(
                    layout=name,
                    raster=raster,
                    count=len(route["segments"]),
                    nails=count,
                    score=score,
                    **metrics,
                )
            )
            log(
                f"{name}: {count} Nägel, {len(route['segments'])} Schritte, Bildwert {score:.4f}"
            )
            rank = (score, -count)
            if best is None or rank > best[0]:
                best = rank, route, preview, target, positions, name
        return full, preview

    rim, preview = trial("Randplan", [], 512)
    reference_array = np.asarray(Image.open(BytesIO(reference)).convert("L"))
    # An isolated bright error in the preview can otherwise attract a dark nail head
    # in a completely white patch. Keep bright edges when a nearby detail needs it.
    local_dark = cv2.erode(reference_array, np.ones((9, 9), np.uint8))
    h, w = local_dark.shape

    def additions(render, existing):
        candidates = suggest_positions(
            reference,
            render,
            existing,
            LIMIT - len(existing),
            max(dims),
            focus,
            art.rectangle,
        )
        return [
            [x, y]
            for x, y in candidates
            if local_dark[round((y + 1) * (h - 1) / 2), round((x + 1) * (w - 1) / 2)]
            < 242
        ]

    for n in (min(230, len(rim)), min(160, len(rim) * 2 // 3)):
        perimeter = adapt_rim(rim, reference_array, n)
        extra = additions(preview, perimeter)
        trial(f"{n} Randpositionen und Innenpunkte", perimeter + extra, 512)
    if best is None:
        raise CommandError(f"Motiv {original.pk} ergibt keinen nutzbaren Pfad.")
    _, route, preview, _, _, _ = best
    # Reassign capacity from unused nails to detail missing in the best simulation.
    retained = route["settings"]["nail_positions"]
    extra = additions(preview, retained)
    if extra:
        trial("Innenpunkte aus verbleibendem Bildfehler", retained + extra, 768)
    trial(f"Feinraster · {best[5]}", best[4], 1024)
    _, route, preview, target, _, name = best
    route["optimization_350"] = {
        "winner": name,
        "assumed_head_mm": HEAD_MM,
        "trials": trials,
    }
    if review_dir is not None:
        (review_dir / "comparison.json").write_text(
            json.dumps(route["optimization_350"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    route["view_quality"] = view_quality(reference, preview, focus, bool(art.rectangle))
    return {
        "route": route,
        "target": target,
        "preview": preview,
        "count": len(route["segments"]),
        "anchors": len(route["anchors"]),
        "nail_positions": route["settings"]["nail_positions"],
        "auto_nails": 0,
        "passes": art.passes,
        "quality_search": False,
    }


class Command(BaseCommand):
    help = "Berechnet Varianten mit höchstens 350 tatsächlich benutzten Nägeln je Bild."

    def add_arguments(self, parser):
        parser.add_argument(
            "ids",
            nargs="*",
            type=int,
            help="Motiv-IDs, standardmäßig die zwei jüngsten Originale",
        )
        parser.add_argument(
            "--review-dir",
            type=Path,
            help=(
                "Exportiert Bildvorschauen, Fadenpfade und Bewertungen je Variante. "
                "Berechnet den Vergleich bei Bedarf erneut und behält vorhandene Motive."
            ),
        )

    def handle(self, *args, **options):
        ids = options["ids"] or list(
            Artwork.objects.filter(previous=None)
            .order_by("-pk")
            .values_list("pk", flat=True)[:2]
        )
        if not ids:
            raise CommandError("Keine Originale vorhanden.")
        for pk in dict.fromkeys(ids):
            original = Artwork.objects.get(pk=pk)
            completed = Artwork.objects.filter(
                previous_id=pk, state="ready", route__optimization_350__isnull=False
            )
            prior = completed.values_list("pk", flat=True).first()
            review_root = options["review_dir"]
            if prior and review_root is None:
                self.stdout.write(f"Motiv {pk}: fertige Variante /atelier/{prior}/")
                continue
            self.stdout.write(f"Motiv {pk}: {original.title}")
            review_dir = None
            if review_root is not None:
                review_root.mkdir(parents=True, exist_ok=True)
                review_dir = Path(tempfile.mkdtemp(prefix=f"motiv-{pk}-", dir=review_root))
                self.stdout.write(f"Vergleichsdateien: {review_dir}")
            with tempfile.TemporaryDirectory() as d:
                result = optimize(original, Path(d), self.stdout.write, review_dir)
            with transaction.atomic():
                winner = completed.first()
                if winner is None:
                    winner = copy.copy(original)
                    winner.pk = None
                    winner.title = f"{original.title[:77]} · {result['anchors']} Nägel"
                    winner.previous = original
                    winner.state = Artwork.State.READY
                    winner.error = ""
                    winner.claimed_at = winner.comparison = None
                    for k, v in result.items():
                        setattr(winner, k, v)
                    winner.save(force_insert=True)
            self.stdout.write(self.style.SUCCESS(f"Gespeichert: /atelier/{winner.pk}/"))
