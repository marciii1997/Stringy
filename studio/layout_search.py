"""Compare complete, windable routes on the same physical rendering."""

import hashlib
import json
import subprocess
import time

import cv2
import numpy as np

from studio.auto_nails import compact_route
from studio.handcraft import improve_winding_order, photo_layout
from studio.nail_clearance import placement_report, plan_winding_sides
from studio.physical import assess, png, simulation, target_array


def residual_layout(route, target, view):
    positions = np.array(route["settings"]["nail_positions"])
    dims = np.array(route["settings"]["rectangle_mm"])
    points = positions * dims / 2
    error = view.astype(np.float32) / 255 - target_array(target, view.shape)
    deficit = cv2.GaussianBlur(error, (0, 0), 4)
    height, width = deficit.shape
    load = np.bincount(route["path"], minlength=len(points))
    movable = set(np.flatnonzero(np.max(abs(positions), axis=1) < 0.999))
    move_limit = max(0, len(movable) - 8)
    for index in np.argsort(deficit.ravel())[::-11][:800]:
        y, x = divmod(index, width)
        if deficit[y, x] < 0.05 or not movable:
            break
        point = (np.array([x / (width - 1), y / (height - 1)]) - 0.5) * dims
        distances = np.linalg.norm(points - point, axis=1)
        nearby = np.flatnonzero(distances < 16.01)
        if len(nearby) > 1 or (len(nearby) and nearby[0] not in movable):
            continue
        if np.any(abs(point) > dims / 2 - 8):
            continue
        nail = nearby[0] if len(nearby) else min(movable, key=lambda n: (load[n], distances[n]))
        points[nail] = point
        movable.remove(nail)
        if len(movable) <= move_limit:
            break
    return (points * 2 / dims).tolist()


def fingerprint(route):
    data = [route["settings"], route["path"], route.get("winding_sides")]
    if "day_ends" in route:
        data.append(route["day_ends"])
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def automatic(art, source, folder, renew, save):
    from studio.winding_study import day_ends, effort, polish, registered_regions
    from studio.management.commands.process_art import (
        optimizer_command,
        refine_handcraft,
        run_optimizer,
    )

    deadline = time.monotonic() + 1800
    checkpoint = (art.route or {}).get("layout_search", {})
    best = art.route if checkpoint else None
    target = bytes(art.target) if checkpoint else None
    trials = checkpoint.get("trials", [])
    finalists = checkpoint.get("layouts", [])
    stages = [
        "standard",
        "seed41",
        "seed73",
        "finalist",
        "residual",
        "refine",
        "lookahead",
        "polish",
        "fidelity",
    ]
    if art.preparation.get("refine_saved_winding") and art.previous_id:
        previous = art.previous.route
        certified = previous.get("winding_clearance", {}).get(
            "uncertified_steps"
        ) == 0 and previous.get("winding_sides")
        stages = ["archive", "fidelity"] if certified else ["archive", "polish", "fidelity"]
        target = bytes(art.previous.target)
    for index in range(len(trials), len(stages)):
        stage = stages[index]
        if not renew():
            return
        if time.monotonic() >= deadline and best:
            break
        try:
            if stage == "archive":
                candidate = art.previous.route.copy()
                candidate.pop("calendar_import", None)
                candidate.pop("layout_search", None)
            elif stage in ("polish", "fidelity"):

                def active():
                    return time.monotonic() < deadline and renew()

                if stage == "polish":
                    candidate = polish(
                        best,
                        target,
                        registered_regions(best, target),
                        rounds=256 if art.preparation.get("refine_saved_winding") else 24,
                        alive=active,
                    )
                    trials.append({"stage": stage, **candidate["polish"]})
                else:
                    from studio.fidelity import optimize_fidelity

                    candidate = optimize_fidelity(best, target, active, source)
                    trials.append({"stage": stage, "selected": candidate is not best})
                if candidate["physical_quality"]["score"] >= best["physical_quality"]["score"]:
                    best = candidate
                else:
                    trials[-1]["selected"] = False
                best["layout_search"] = {"trials": trials}
                if not save(route=best, target=target):
                    return
                continue
            elif stage == "refine":
                candidate = json.loads(json.dumps(best))
                winner = {
                    "route": candidate,
                    "preview": png(simulation(candidate)),
                    "count": len(candidate["path"]) - 1,
                }
                if not refine_handcraft(
                    art, source, folder, winner, target, None, deadline, renew, physical=True
                ):
                    return
                candidate = winner["route"]
            else:
                if stage == "lookahead":
                    positions = best["settings"]["nail_positions"]
                elif stage == "residual":
                    positions = residual_layout(best, target, simulation(best))
                elif stage == "finalist":
                    positions = max(finalists, key=lambda entry: entry[0])[1]
                else:
                    seed = {"standard": 24, "seed41": 41, "seed73": 73}[stage]
                    positions = photo_layout(art.source, art.field_size, seed)
                art.nail_positions = positions
                command = optimizer_command(
                    art, source, folder, 320 if stage.startswith("seed") else 512
                )
                if stage == "lookahead":
                    command.append("--lookahead")
                if not run_optimizer(command, deadline, renew):
                    return
                candidate = json.loads((folder / "path.json").read_bytes())
                if len(candidate["path"]) < 25 or not compact_route(candidate, art.field_size):
                    raise ValueError("Pfad zu kurz")
                if target is None:
                    target = (folder / "target.png").read_bytes()
                if stage == "standard" and len(candidate["path"]) - 1 == art.budget:
                    original = {
                        "route": candidate,
                        "preview": (folder / "preview.png").read_bytes(),
                    }
                    if not refine_handcraft(
                        art, source, folder, original, target, None, deadline, renew
                    ):
                        return
                    candidate = original["route"]
                improve_winding_order(candidate)
            plan_winding_sides(candidate, art.previous.route if stage == "archive" else None)
            view = simulation(candidate)
            metrics = assess(target, view)
            if stage.startswith("seed"):
                finalists.append((metrics["score"], positions))
            trial = dict(stage=stage, **metrics)
            if best is None or (
                metrics["score"] > best["physical_quality"]["score"]
                and metrics["white_ink"] <= best["physical_quality"]["white_ink"] + 0.00025
                and candidate["winding_clearance"]["uncertified_steps"]
                <= best["winding_clearance"]["uncertified_steps"]
            ):
                profiles = [
                    assess(target, simulation(candidate, thread_scale=scale, opacity=opacity))
                    for scale, opacity in ((0.8, 0.75), (1.2, 1.0))
                ]
                previous = best.get("material_profiles", []) if best else []
                if all(
                    new["score"] >= old["score"] - 0.001 for new, old in zip(profiles, previous)
                ):
                    best = candidate
                    best["physical_quality"] = metrics
                    best["material_profiles"] = profiles
                    best["placement"] = placement_report(best)
                    trial["selected"] = True
            trials.append(trial)
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            if best is None:
                raise
            trials.append({"stage": stage, "error": type(exc).__name__})
            if stage.startswith("seed") and not finalists:
                finalists.append(
                    (best["physical_quality"]["score"], best["settings"]["nail_positions"])
                )
        best["layout_search"] = {"trials": trials, "layouts": finalists}
        if not save(route=best, target=target):
            return
    best["layout_search"].pop("layouts", None)
    best["day_ends"] = day_ends(effort(best))
    best["version_hash"] = fingerprint(best)
    best["preparation"] = art.preparation
    save(
        state="ready",
        route=best,
        preview=png(simulation(best, 1024)),
        target=target,
        anchors=len(best["anchors"]),
        nail_positions=best["settings"]["nail_positions"],
        count=len(best["path"]) - 1,
        budget=max(art.budget, len(best["path"]) - 1),
    )
