"""Suggest interior winding points from errors in a completed baseline simulation."""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image


def suggest_positions(target_png, preview_png, existing, limit, diameter_mm, priority=None, rectangle=None):
    # Work in canvas coordinates, after cropping, masking and tone preparation.
    with Image.open(BytesIO(target_png)) as target, Image.open(BytesIO(preview_png)) as preview:
        size = 256

        width, height = (tuple(max(2, round(size * d / max(rectangle))) for d in rectangle)
                         if rectangle else (size, size))

        def luminance(image):
            return (
                cv2.resize(
                    np.asarray(image.convert("L"), dtype=np.float32),
                    (width, height),
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
            np.asarray(priority, dtype=np.float32), (width, height), interpolation=cv2.INTER_AREA
        )
        score *= 1 + 3 * focus / 255
    rx, ry = (width - 1) / 2, (height - 1) / 2
    y, x = np.ogrid[:height, :width]
    if rectangle:
        score[(abs(x - rx) > .94 * rx) | (abs(y - ry) > .94 * ry)] = 0
    else:
        score[(x - rx) ** 2 + (y - ry) ** 2 > (0.94 * rx) ** 2] = 0
    spacing = max(5.0, size * 5 / (max(rectangle) if rectangle else diameter_mm),
                  size / (5 * max(1, limit) ** 0.5))

    def exclude(px, py):
        score[(x - px) ** 2 + (y - py) ** 2 < spacing**2] = 0

    for px, py in existing:
        exclude((px + 1) * rx, (py + 1) * ry)
    result = []
    for _ in range(limit):
        py, px = np.unravel_index(int(score.argmax()), score.shape)
        if score[py, px] < 0.002:
            break
        result.append([float(px / rx - 1), float(py / ry - 1)])
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


def normalized_positions(route, dims):
    w, h = dims
    return [[2 * p["x_mm"] / w, 2 * p["y_mm"] / h] for p in route["anchors"]]


def compact_route(route, dims):
    pins = normalized_positions(route, dims)
    used = set(route["path"])
    if len(used) < 3:
        return False
    s = route["settings"]
    s["nail_positions"] = pins
    s["start_anchor"] = sum(i in used for i in range(route["path"][0]))
    s.update(base_ring=0, min_anchor_gap=1)
    keep_used_nails(route, 0)
    return True
