"""Viewing-scale diagnostics for a rendered route and its own prepared target."""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image


def view_quality(target_png, preview_png, priority=None, rectangular=False):
    with Image.open(BytesIO(target_png)) as target, Image.open(BytesIO(preview_png)) as preview:
        tone = np.asarray(target.convert("L"), dtype=np.float32) / 255
        render = (
            cv2.resize(
                np.asarray(preview.convert("L"), dtype=np.float32),
                target.size,
                interpolation=cv2.INTER_AREA,
            )
            / 255
        )
    size = max(tone.shape)
    tone, render = [cv2.GaussianBlur(a, (0, 0), size / 256) for a in (tone, render)]

    def mean(a):
        return cv2.GaussianBlur(a, (0, 0), size / 128)

    mu, nu = mean(tone), mean(render)
    covariance = mean(tone * render) - mu * nu
    variance = np.maximum(0, mean(tone * tone) - mu * mu) + np.maximum(
        0, mean(render * render) - nu * nu
    )
    ssim = (
        (2 * mu * nu + 0.01**2)
        * (2 * covariance + 0.03**2)
        / ((mu**2 + nu**2 + 0.01**2) * (variance + 0.03**2))
    )
    axis = np.linspace(-1, 1, size, dtype=np.float32)
    inside = np.ones(tone.shape, dtype=bool) if rectangular else axis[:, None] ** 2 + axis**2 < 0.95**2
    weight = None if priority is None else 1 + 3 * np.asarray(priority, dtype=np.float32)[inside] / 255
    return {
        "ssim": float(np.clip(np.average(ssim[inside], weights=weight), -1, 1)),
        "tone_rmse": float(np.sqrt(np.average((tone[inside] - render[inside]) ** 2, weights=weight))),
    }
