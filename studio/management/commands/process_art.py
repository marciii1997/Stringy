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
    try:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "bild"
            source.write_bytes(art.source)
            command = [
                settings.STRINGY_BIN,
                str(source),
                "--output",
                temp,
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
            if any(art.preparation.get(key) for key in DEFAULTS):
                guide = folder / "guide.png"
                weights, ink = guide_for(art, analysis_for(art))
                weights.save(guide)
                if ink is not None:
                    ink_path = folder / "background-ink.png"
                    ink.save(ink_path)
                    command.extend(["--background-ink", str(ink_path)])
                command.extend(["--guide", str(guide)])
            process = subprocess.Popen(command)
            try:
                for _ in range(30):
                    try:
                        code = process.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        heartbeat = timezone.now()
                        if not claim.update(claimed_at=heartbeat):
                            return True
                        claim = Artwork.objects.filter(pk=pk, claimed_at=heartbeat)
                    else:
                        if code:
                            raise subprocess.CalledProcessError(code, command)
                        break
                else:
                    raise subprocess.TimeoutExpired(command, 1800)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
            route = json.loads((folder / "path.json").read_bytes())
            if art.preparation:
                route["preparation"] = art.preparation
            claim.update(
                state=Artwork.State.READY,
                preview=(folder / "preview.png").read_bytes(),
                target=(folder / "target.png").read_bytes(),
                route=route,
                count=len(route["path"]) - 1,
            )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        claim.update(
            state=Artwork.State.FAILED, error=f"Berechnung fehlgeschlagen ({type(exc).__name__})."
        )
    return True


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
