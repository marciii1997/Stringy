"""Re-fit a saved path to a different yarn on the same numbered board."""

import json
import subprocess
from collections import Counter
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .eye_detail import refine_eyes
from .fidelity import edits, gain_map, structural_gain
from .handcraft import improve_winding_order
from .layout_search import fingerprint
from .nail_clearance import placement_report
from .optical import OpticalRefinement
from .physical import assess, png, simulation
from .quality import view_quality
from .winding_study import (
    day_ends,
    difficulty,
    effort,
    measurements,
    rebuilt,
    registered_regions,
)


def chord_counts(path, search):
    counts = np.zeros(search.legal.shape, dtype=int)
    np.add.at(counts, (path[:-1], path[1:]), 1)
    return counts + counts.T


def repair(route, search, gain, rng):
    # Remove an obstructed chord by reversing the subpath between two cuts.
    safe = search.legal & (search.center_gap >= 3)
    for _ in range(120):
        hazards = route["winding_clearance"]["uncertain_edges"]
        if not hazards:
            return route
        p = np.array(route["path"])
        i = int(rng.choice([h["step"] - 1 for h in hazards]))
        a, b = p[i : i + 2]
        c, d = p[:-1], p[1:]
        counts = chord_counts(p, search)
        ok = (
            (abs(np.arange(len(c)) - i) > 1)
            & (a != d)
            & (b != c)
            & safe[a, c]
            & safe[b, d]
            & (counts[a, c] < search.limit)
            & (counts[b, d] < search.limit)
        )
        ix = np.flatnonzero(ok)
        if not len(ix):
            continue
        change = gain[a, c[ix]] + gain[b, d[ix]] - gain[c[ix], d[ix]]
        j = int(rng.choice(ix[np.argsort(change)[-8:]]))
        lo, hi = sorted((i, j))
        p[lo + 1 : hi + 1] = p[lo + 1 : hi + 1][::-1]
        path = p.tolist()
        if difficulty({"path": path})[0]:
            continue
        trial = rebuilt(route, path, preserve_tangents=True)
        if trial["winding_clearance"]["uncertified_steps"] < len(hazards):
            route = trial
    if route["winding_clearance"]["uncertified_steps"]:
        raise ValueError("Kein vollständig freier Fadenpfad für dieses Material gefunden.")
    return route


def evaluate(route, target, regions):
    view = simulation(route)
    m = assess(target, view, regions)
    m["ssim"] = view_quality(target, png(view), rectangular=True)["ssim"]
    # Give short routes a slight preference when optical quality is close.
    score = (
        m["ssim"]
        - 1.35 * m["tone_error"]
        - 1.3 * m["white_ink"]
        + 0.30 * m["local_structure"]
    )
    if regions:
        score += 0.35 * np.mean([m[n] for n, _ in regions])
    score -= 0.005 * (len(route["path"]) - 1) / 1000
    return float(score), m, view


def fit_yarn(route, target, source, diameter, binary, budget, white_protection):
    """Build a fresh physically bounded path, then repair and refine its tangents."""
    settings = route["settings"]
    regions = registered_regions(route, target)
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        photo, nails = folder / "photo", folder / "nails.json"
        photo.write_bytes(source)
        nails.write_text(json.dumps(settings["nail_positions"]))
        cmd = [
            binary,
            str(photo),
            "--output",
            tmp,
            "--nail-positions",
            str(nails),
            "--rectangle-mm",
            *map(str, settings["rectangle_mm"]),
            "--shaft-mm",
            str(settings["shaft_mm"]),
            "--thread-mm",
            str(diameter),
            "--opacity",
            str(settings["opacity"]),
            "--max-segments",
            str(budget),
            "--detail",
            "1",
            "--white-protection",
            str(white_protection),
            "--passes",
            "3",
            "--min-segment-mm",
            "12",
            "--max-nail-load",
            "48",
            "--max-chord-uses",
            "2",
            "--repeat-penalty",
            ".001",
            "--zoom",
            str(settings["zoom"]),
            "--gamma",
            str(settings["gamma"]),
            "--center-x",
            str(settings["center_x"]),
            "--center-y",
            str(settings["center_y"]),
        ]
        if settings.get("pad_crop"):
            cmd.append("--pad-crop")
        subprocess.run(cmd, check=True)
        r = json.loads((folder / "path.json").read_bytes())
    if r["anchors"] != route["anchors"]:
        raise ValueError(
            "Die Nagelkoordinaten stimmen nicht mit dem Werkstück überein."
        )
    improve_winding_order(r)
    path = []
    for nail in r["path"]:
        if len(path) >= 2 and nail == path[-2]:
            path.pop()
        else:
            path.append(nail)
    if len(path) < 25:
        raise ValueError("Zu wenige Züge für den Kalender.")
    r = rebuilt(r, path)
    search = OpticalRefinement(r, target, settings["rectangle_mm"])
    rng = np.random.default_rng(116)

    def gains(view):
        return gain_map(search, view, regions, {n: 0.22 for n, _ in regions})

    r = repair(r, search, gains(simulation(r)), rng)
    score, metrics, view = evaluate(r, target, regions)
    for i in range(70):
        gain = structural_gain(search, view, regions) if i % 3 else gains(view)
        path = edits(search, r, gain, (4, 8, 16, 22)[i % 4], rng, i % 3 != 2)
        if difficulty({"path": path})[0]:
            continue
        candidate = rebuilt(r, path, preserve_tangents=True)
        if candidate["winding_clearance"]["uncertified_steps"]:
            continue
        value, next_metrics, image = evaluate(candidate, target, regions)
        if value > score and next_metrics["white_ink"] <= metrics["white_ink"] + 0.0001:
            r, score, metrics, view = candidate, value, next_metrics, image
    r = refine_eyes(r, target, source=BytesIO(source))
    chords = Counter(tuple(sorted(pair)) for pair in pairwise(r["path"]))
    if max(chords.values()) > 2 or max(Counter(r["path"]).values()) > 24:
        raise ValueError("Die neue Route überschreitet die Wickelgrenzen.")
    r["placement"] = placement_report(r)
    profiles = measurements(r, target, regions)
    r["physical_quality"] = profiles[0]
    r["material_profiles"] = profiles[1:]
    r["detail_regions"] = regions
    r["day_ends"] = day_ends(effort(r))
    r["version_hash"] = fingerprint(r)
    return r
