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
            subprocess.run(
                [
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
                    "--max-segments",
                    str(art.budget),
                ],
                check=True,
                timeout=240,
            )
            route = json.loads((folder / "path.json").read_bytes())
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
