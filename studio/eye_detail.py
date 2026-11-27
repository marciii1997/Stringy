"""Fixed-budget edits for the two eyes of the registered cat photograph."""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps

from .nail_clearance import placement_report
from .optical import OpticalRefinement
from .physical import assess, png, simulation, target_array
from .quality import view_quality
from .winding_study import (
    CAT_REGIONS,
    PROFILES,
    crossings,
    day_ends,
    difficulty,
    effort,
    rebuilt,
    registered_cat_crop,
)

# Pupil ellipses and bright lower lids traced on the registered 433 x 512 crop.
EYES = (
    (
        (166, 245, 10, 5),
        [(146, 263), (163, 271), (186, 274), (190, 279), (161, 277), (144, 270)],
    ),
    (
        (312, 238, 8, 4),
        [(293, 261), (310, 255), (329, 244), (325, 253), (311, 262), (294, 271)],
    ),
)


def masks(shape):
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    x, y = (xx + 0.5) * 433 / w, (yy + 0.5) * 512 / h
    result = []
    for (cx, cy, rx, ry), points in EYES:
        pupil = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 < 1
        polygon = np.rint(np.array(points) * [w / 433, h / 512]).astype(np.int32)
        lid = cv2.fillPoly(np.zeros(shape, np.uint8), [polygon], 1)
        result.append(lid / lid.sum() - pupil / pupil.sum())
    return np.array(result)


def contrast(view, weights):
    blurred = cv2.GaussianBlur(view.astype(np.float32) / 255, (0, 0), 1.5)
    return np.sum(weights * blurred, axis=(1, 2))


def refine_eyes(route, target, alive=lambda: True, source=None):
    from .fidelity import edits, structural_gain

    if not registered_cat_crop(target) or not alive():
        return route
    prepared = target
    if source is not None:
        with Image.open(source) as photo, Image.open(BytesIO(prepared)) as crop:
            original = ImageOps.fit(
                ImageOps.exif_transpose(photo).convert("L"),
                (433, 512),
                method=Image.Resampling.LANCZOS,
            )
            registered = crop.convert("L").resize(
                original.size, Image.Resampling.LANCZOS
            )
            if (
                np.abs(
                    np.asarray(original, dtype=float)
                    - np.asarray(registered, dtype=float)
                ).mean()
                < 5
            ):
                target = png(np.asarray(original))
    view = simulation(route)
    weights = masks(view.shape)
    reference = contrast(target_array(target, view.shape) * 255, weights)

    def evaluate(image):
        scores = assess(target, image, CAT_REGIONS)
        scores["ssim"] = view_quality(target, png(image), rectangular=True)["ssim"]
        errors = np.abs(contrast(image, weights) - reference)
        return scores, errors

    def protects(scores, old, errors, old_errors):
        return (
            scores["ssim"] >= old["ssim"] - 0.001
            and scores["tone_error"] <= old["tone_error"] + 0.00065
            and scores["white_ink"] <= old["white_ink"] + 0.0003
            and all(scores[n] >= old[n] - 0.001 for n, _ in CAT_REGIONS)
            and np.all(errors <= old_errors + 0.001)
        )

    def merit(m, errors):
        return (
            m["ssim"]
            - 0.45 * errors.sum()
            + 0.22 * (m["left_eye"] + m["right_eye"])
            - 1.5 * m["tone_error"]
        )

    def profiles(candidate):
        return [
            evaluate(simulation(candidate, thread_scale=s, opacity=o))
            for s, o in PROFILES[1:]
        ]

    floor, initial_errors = evaluate(view)
    floor_profiles = profiles(route)
    search = OpticalRefinement(route, target, route["settings"]["rectangle_mm"])
    search_masks = masks(search.target.shape)
    rng = np.random.default_rng(992)
    working = winner = route
    best_gain = best_verified = merit(floor, initial_errors)
    allowed_risk = route["winding_clearance"]["uncertified_steps"]
    max_white = crossings(route, target).max() + 0.2
    for i in range(70):
        if not alive():
            break
        gain = structural_gain(search, view, CAT_REGIONS)
        luminance = (
            cv2.resize(view, search.target.shape[::-1], interpolation=cv2.INTER_AREA)
            / 255
        )
        sign = np.sign(reference - contrast(view, weights))
        sensitivity = -np.sum(sign[:, None, None] * search_masks, axis=0) * luminance
        delta = (
            sensitivity.ravel()[search.indices].mean(axis=1)
            * search.length
            * search.target.size
        )
        gain[search.a, search.b] += delta * (0.35, 0.65, 1.2)[i % 3]
        gain[search.b, search.a] = gain[search.a, search.b]
        path = edits(search, working, gain, (4, 8, 12, 18)[i % 4], rng, i % 3 != 2)
        if difficulty({"path": path})[0] or len(set(path)) < len(set(route["path"])):
            continue
        trial = rebuilt(working, path, preserve_tangents=True)
        if trial["winding_clearance"]["uncertified_steps"] > allowed_risk:
            continue
        image = simulation(trial)
        m, errors = evaluate(image)
        value = merit(m, errors)
        if (
            value <= best_gain
            or not protects(m, floor, errors, initial_errors)
            or np.any(errors > initial_errors)
            or m["tone_error"] > floor["tone_error"] + 0.0005
            or any(m[n] < floor[n] - 0.0008 for n, _ in CAT_REGIONS)
        ):
            continue
        if crossings(trial, target).max() > max_white:
            continue
        working, view, best_gain = trial, image, value
        if (
            value <= best_verified
            or np.any(errors >= initial_errors)
            or m["score"] < floor["score"]
        ):
            continue
        tested = profiles(trial)
        if not all(
            protects(new, old, e, base)
            for (new, e), (old, base) in zip(tested, floor_profiles)
        ):
            continue
        best_verified, winner = value, trial
        winner_errors = errors.tolist()
    if winner is route:
        return route
    profiles_at_prepared_target = [
        assess(
            prepared,
            simulation(winner, thread_scale=scale, opacity=opacity),
            CAT_REGIONS,
        )
        for scale, opacity in PROFILES
    ]
    winner["physical_quality"] = profiles_at_prepared_target[0]
    winner["material_profiles"] = profiles_at_prepared_target[1:]
    winner["eye_detail"] = {
        "absolute_contrast_error": winner_errors,
        "before": initial_errors.tolist(),
    }
    winner["day_ends"] = day_ends(effort(winner))
    winner["placement"] = placement_report(winner)
    # The predecessor's baseline remains a separate historical comparison.
    winner.pop("fidelity", None)
    return winner
