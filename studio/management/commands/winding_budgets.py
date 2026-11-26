import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from studio.physical import png, simulation
from studio.winding_study import polish, registered_regions, shorten


class Command(BaseCommand):
    help = "Optimiert zwei kürzere Kalenderpfade auf dem geprüften Nagelplan."

    def add_arguments(self, parser):
        for name in ("route", "target", "output"):
            parser.add_argument("--" + name, type=Path, required=True)

    def handle(self, route, target, output, **options):
        initial = json.loads(route.read_bytes())
        target = target.read_bytes()
        regions = registered_regions(initial, target)
        if len(initial["path"]) <= 2700:
            raise CommandError("Die Ausgangsversion muss mehr als 2.700 Züge haben.")
        for budget in (2400, 2700):
            candidate = shorten(initial, target, budget, regions)
            candidate = polish(candidate, target, regions, 32)
            dest = output / str(budget)
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "route.json").write_text(json.dumps(candidate))
            (dest / "simulation.png").write_bytes(png(simulation(candidate, 1024)))
