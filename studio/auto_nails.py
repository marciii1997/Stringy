"""Suggest interior winding points from errors in a completed baseline simulation."""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image


def suggest_positions(target_png, preview_png, existing, limit, diameter_mm, priority=None):
    # Work in canvas coordinates, after cropping, masking and tone preparation.
    with Image.open(BytesIO(target_png)) as target, Image.open(BytesIO(preview_png)) as preview:
        size = 256

        def luminance(image):
            return (
                cv2.resize(
                    np.asarray(image.convert("L"), dtype=np.float32),
                    (size, size),
                    interpolation=cv2.INTER_AREA,
                )
                / 255
            )

        target, preview = luminance(target), luminance(preview)

    def smooth(a, sigma):
        return cv2.GaussianBlur(a, (0, 0), sigma)

    residual = smooth(preview, 1) - smooth(target, 1)
    missing = np.maximum(residual, 0)
    # Corners and ends of strokes benefit from a change in winding direction.
    corners = np.sqrt(np.maximum(cv2.cornerMinEigenVal(smooth(target, 1), 5), 0))
    contrast = np.abs(target - smooth(target, 3))
    local_error = smooth(np.abs(residual), 2)
    score = (0.2 + 3 * corners + 1.5 * contrast) * (missing + local_error)
    if priority is not None:
        focus = cv2.resize(
            np.asarray(priority, dtype=np.float32), (size, size), interpolation=cv2.INTER_AREA
        )
        score *= 1 + 3 * focus / 255
    radius = (size - 1) / 2
    y, x = np.ogrid[:size, :size]
    score[(x - radius) ** 2 + (y - radius) ** 2 > (0.94 * radius) ** 2] = 0
    spacing = max(5.0, size * 5 / diameter_mm, size / (5 * max(1, limit) ** 0.5))

    def exclude(px, py):
        score[(x - px) ** 2 + (y - py) ** 2 < spacing**2] = 0

    for px, py in existing:
        # All candidates already lie inside 94% of the circle.
        if np.hypot(px, py) < 0.94 + spacing / radius:
            exclude((px + 1) * radius, (py + 1) * radius)
    result = []
    for _ in range(limit):
        py, px = np.unravel_index(int(score.argmax()), score.shape)
        if score[py, px] < 0.002:
            break
        result.append([float(px / radius - 1), float(py / radius - 1)])
        exclude(px, py)
    return result


def keep_used_nails(route, base_count):
    """Drop unused additions while keeping every base ID and the physical winding unchanged."""
    settings = route["settings"]
    positions = settings["nail_positions"]
    used = set(route["path"])
    keep = [i for i in range(len(positions)) if i < base_count or i in used]
    ids = {old: new for new, old in enumerate(keep)}
    settings["nail_positions"] = [positions[i] for i in keep]
    settings["anchors"] = len(keep)
    route["anchors"] = [{**route["anchors"][i], "id": ids[i]} for i in keep]
    route["path"] = [ids[i] for i in route["path"]]
    for segment in route["segments"]:
        segment["from"] = ids[segment["from"]]
        segment["to"] = ids[segment["to"]]
    return len(keep) - base_count
