import json
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from studio.models import Artwork
from studio.motif import DEFAULTS, analysis_for, guide_for
from studio.quality import view_quality


def process_one():
    now = timezone.now()
    eligible = Q(state=Artwork.State.QUEUED) | Q(
        state=Artwork.State.RUNNING, claimed_at__lt=now - timedelta(minutes=6)
    )
    candidates = Artwork.objects.filter(eligible)
    pk = candidates.order_by("id").values_list("pk", flat=True).first()
    if pk is None or not candidates.filter(pk=pk).update(state=Artwork.State.RUNNING, claimed_at=now):
        return False
    claim = Artwork.objects.filter(pk=pk, claimed_at=now)
    art = claim.get()

    def renew():
        nonlocal claim
        heartbeat = timezone.now()
        if not claim.update(claimed_at=heartbeat):
            return False
        claim = Artwork.objects.filter(pk=pk, claimed_at=heartbeat)
        return True

    try:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "bild"
            source.write_bytes(art.source)
            guided = (
                any(art.preparation.get(key) for key in DEFAULTS)
                or art.preparation.get("style", "natural") != "natural"
            )
            analysis = analysis_for(art) if guided else None
            best_score = None
            results = []
            winner = {}
            deadline = time.monotonic() + 1800
            priority = None
            for size in (512, 1024) if art.quality_search else (512,):
                command = optimizer_command(art, source, folder, size)
                if analysis is not None:
                    weights, ink = guide_for(art, analysis, size)
                    guide = folder / "guide.png"
                    weights.save(guide)
                    command.extend(["--guide", str(guide)])
                    if ink is not None:
                        mask = folder / "ink.png"
                        ink.save(mask)
                        command.extend(["--background-ink", str(mask)])
                    if size == 512:
                        priority = weights.getchannel("G")
                try:
                    if not run_optimizer(command, deadline, renew):
                        return True
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                    if not winner:
                        raise
                    results.append({"raster": size, "error": type(exc).__name__})
                    break
                route = json.loads((folder / "path.json").read_bytes())
                preview = (folder / "preview.png").read_bytes()
                target = (folder / "target.png").read_bytes()
                if size == 512:
                    reference = target
                quality = view_quality(reference, preview, priority)
                count = len(route["path"]) - 1
                score = (quality["ssim"] - quality["tone_rmse"], -count)
                results.append({"raster": size, "count": count, **quality})
                if best_score is None or score > best_score:
                    best_score = score
                    route["view_quality"] = quality
                    if art.preparation:
                        route["preparation"] = art.preparation
                    winner = {"route": route, "count": count, "preview": preview, "target": target}
            if art.previous_id and art.quality_search:
                previous = (
                    Artwork.objects.filter(
                        pk=art.previous_id,
                        state=Artwork.State.READY,
                        anchors=art.anchors,
                        diameter=art.diameter,
                        thread=art.thread,
                        opacity=art.opacity,
                    )
                    .values_list("route", flat=True)
                    .first()
                )
                if (
                    previous
                    and previous.get("path")
                    and "settings" in previous
                    and time.monotonic() < deadline
                ):
                    archive = folder / "previous.json"
                    archive.write_text(
                        json.dumps({"settings": previous["settings"], "path": previous["path"]})
                    )
                    replay = [settings.STRINGY_BIN, "--replay", str(archive), "--output", str(folder)]
                    try:
                        if not run_optimizer(replay, deadline, renew):
                            return True
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                        pass  # The new path is complete even if the archive cannot be replayed.
                    else:
                        image = (folder / "preview.png").read_bytes()
                        winner["route"]["previous_quality"] = view_quality(reference, image, priority)
            winner["route"]["quality_trials"] = results
            claim.update(state=Artwork.State.READY, **winner)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        claim.update(
            state=Artwork.State.FAILED, error=f"Berechnung fehlgeschlagen ({type(exc).__name__})."
        )
    return True


def optimizer_command(art, source, folder, size):
    command = [
        settings.STRINGY_BIN,
        str(source),
        "--output",
        str(folder),
        "--raster-size",
        str(size),
        "--preview-size",
        "1024",
        "--anchors",
        str(art.anchors),
        "--diameter-mm",
        str(art.diameter),
        "--thread-mm",
        str(art.thread),
        "--passes",
        str(art.passes),
        "--zoom",
        str(art.zoom),
        "--center-x",
        str(art.center_x),
        "--center-y",
        str(art.center_y),
        "--gamma",
        str(art.gamma),
        "--detail",
        str(art.detail),
        "--opacity",
        str(art.opacity),
        "--max-segments",
        str(art.budget),
    ]
    if art.preparation.get("pad"):
        command.append("--pad-crop")
    return command


def run_optimizer(command, deadline, renew):
    with subprocess.Popen(command) as process:
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 1800)
                try:
                    code = process.wait(timeout=min(60, remaining))
                except subprocess.TimeoutExpired:
                    if not renew():
                        return False
                else:
                    if code:
                        raise subprocess.CalledProcessError(code, command)
                    return True
        finally:
            if process.poll() is None:
                process.kill()


class Command(BaseCommand):
    help = "Berechnet wartende Motive mit dem Stringy-Optimierer."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            worked = process_one()
            if options["once"]:
                return
            if not worked:
                time.sleep(2)
