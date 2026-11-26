"""Run same-board comparisons through the real engine and optical renderer."""

import json
import subprocess
import time
from pathlib import Path

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw

from studio.handcraft import optimizer_flags, photo_layout
from studio.nail_clearance import plan_winding_sides
from studio.physical import png, simulation
from studio.winding_study import crossings, measurements, polish, registered_regions


def fixtures():
    face = Image.new("L", (216, 256), 195)
    draw = ImageDraw.Draw(face)
    draw.ellipse((31, 26, 185, 235), fill=160)
    for x in (77, 138):
        draw.ellipse((x - 14, 101, x + 14, 111), fill=119)
        draw.ellipse((x - 4, 101, x + 4, 110), fill=77)
    draw.line([(105, 118), (98, 152), (113, 154)], fill=136, width=3)
    draw.arc((85, 166, 138, 183), 10, 170, fill=118, width=3)
    sky = Image.new("L", face.size, 252)
    draw = ImageDraw.Draw(sky)
    draw.polygon(
        [(0, 218), (55, 164), (87, 185), (142, 91), (215, 177), (215, 256), (0, 256)], fill=124
    )
    draw.polygon([(75, 207), (144, 107), (169, 159), (149, 144)], fill=57)
    lines = Image.new("L", face.size, 241)
    draw = ImageDraw.Draw(lines)
    for i in range(12):
        draw.arc(
            (12 + i * 4, 16 + i * 5, 200 - i * 4, 240 - i * 5), 18, 308, fill=40 + i * 5, width=2
        )
    return [("portraet", face), ("himmel", sky), ("konturen", lines)]


class Command(BaseCommand):
    help = "Vergleicht Zugzahlen und Vorwärtssuche am identischen Brett; ohne Foto mit drei Testmotiven."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--route", type=Path)
        parser.add_argument("--target", type=Path)
        parser.add_argument(
            "--seconds",
            type=int,
            default=0,
            help="Zeitbudget pro Suchmethode für Wiederholungen mit verschiedenen Startnägeln",
        )
        parser.add_argument("--verify", action="store_true")

    def handle(self, output, route, target, seconds, verify, **options):
        if seconds < 0:
            raise CommandError("Zeitbudget darf nicht negativ sein.")
        if bool(route) != bool(target):
            raise CommandError("Route und registrierte Vorlage zusammen angeben.")
        output.mkdir(parents=True, exist_ok=True)
        if route:
            settings_ = json.loads(route.read_bytes())["settings"]
            inputs = [
                (
                    "referenz",
                    Image.open(target).convert("L"),
                    settings_["nail_positions"],
                    settings_["rectangle_mm"],
                )
            ]
        else:
            inputs = [
                (name, im, photo_layout(png(np.asarray(im)), (415, 490)), (415, 490))
                for name, im in fixtures()
            ]
        report = []
        for name, img, pins, dims in inputs:
            directory = output / name
            directory.mkdir(exist_ok=True)
            img.save(directory / "ziel.png")
            target_bytes = (directory / "ziel.png").read_bytes()
            nails = directory / "pins.json"
            nails.write_text(json.dumps(pins))
            pictures = []
            budgets = (2400, 2700, 3000) if route else (1200,)
            for budget in budgets:
                for ahead in (False, True):
                    started = time.monotonic()
                    best = None
                    attempt = 0
                    while True:
                        work = directory / f"{budget}-{'vorausschau' if ahead else 'direkt'}"
                        work.mkdir(exist_ok=True)
                        cmd = [
                            settings.STRINGY_BIN,
                            str(directory / "ziel.png"),
                            "--output",
                            str(work),
                            "--nail-positions",
                            str(nails),
                            "--rectangle-mm",
                            *map(str, dims),
                            "--thread-mm",
                            ".2",
                            "--shaft-mm",
                            "1.2",
                            "--opacity",
                            ".85",
                            "--max-segments",
                            str(budget),
                            "--passes",
                            "3",
                            "--raster-size",
                            "320",
                            "--start-anchor",
                            str((attempt * 17) % len(pins)),
                            *optimizer_flags(),
                        ]
                        if ahead:
                            cmd.append("--lookahead")
                        subprocess.run(
                            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        r = json.loads((work / "path.json").read_bytes())
                        plan_winding_sides(r)
                        profiles = measurements(
                            r,
                            target_bytes,
                            registered_regions(r, target.read_bytes()) if route else (),
                        )
                        elapsed = time.monotonic() - started
                        if seconds and elapsed > seconds:
                            break
                        row = {
                            "motif": name,
                            "budget": budget,
                            "lookahead": ahead,
                            "elapsed": elapsed,
                            "steps": len(r["path"]) - 1,
                            "profiles": profiles,
                            "white_run_mm": float(crossings(r, target_bytes).max()),
                            "unsafe": r["winding_clearance"]["uncertified_steps"],
                        }
                        if (
                            best is None
                            or row["profiles"][0]["score"] > best["profiles"][0]["score"]
                        ):
                            best = row
                            (work / "winner.json").write_text(json.dumps(r))
                            (work / "physical.png").write_bytes(png(simulation(r)))
                        attempt += 1
                        if not seconds:
                            break
                    if best is None:
                        raise CommandError("Zeitbudget kürzer als ein vollständiger Lauf.")
                    best["completed"] = attempt
                    report.append(best)
                    pictures.append(Image.open(work / "physical.png").copy())
                    (output / "messung.json").write_text(json.dumps(report, indent=2))
                    minimum = {"portraet": 0.37, "himmel": 0.53, "konturen": 0.40}.get(name, 0.0)
                    if verify and (
                        best["steps"] != budget or best["profiles"][0]["score"] < minimum
                    ):
                        raise CommandError(f"Bildprüfung fehlgeschlagen: {name}, {budget}")
            if not route:
                leading = max(
                    (row for row in report if row["motif"] == name and "lookahead" in row),
                    key=lambda row: row["profiles"][0]["score"],
                )
                label = "vorausschau" if leading["lookahead"] else "direkt"
                winner = json.loads((directory / f"1200-{label}" / "winner.json").read_bytes())
                refined = polish(winner, target_bytes, rounds=24)
                view = simulation(refined)
                pictures.append(Image.fromarray(view))
                revised = {
                    "motif": name,
                    "stage": "polish",
                    "profiles": [refined["physical_quality"], *refined["material_profiles"]],
                    "unsafe": refined["winding_clearance"]["uncertified_steps"],
                    "white_run_mm": refined["polish"]["longest_white_mm"],
                }
                if verify and (
                    revised["unsafe"] > leading["unsafe"]
                    or revised["white_run_mm"] > leading["white_run_mm"] + 0.1
                    or revised["profiles"][0]["score"] < leading["profiles"][0]["score"] - 0.0011
                ):
                    raise CommandError(f"Nachbearbeitung verschlechtert {name}")
                report.append(revised)
                (output / "messung.json").write_text(json.dumps(report, indent=2))
            imgs = [img] + pictures
            sheet = Image.new("RGB", (200 * len(imgs), 230), "white")
            for i, im in enumerate(imgs):
                im.thumbnail((196, 230))
                sheet.paste(im, (200 * i, 0))
            sheet.save(directory / "vergleich.png")
