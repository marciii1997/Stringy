"""Optical projection of a hand-wound rectangular board, in field coordinates."""
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

def simulation(route, size=512, thread_scale=1., opacity=None):
    settings = route["settings"]
    dims = np.array(settings["rectangle_mm"])
    scale = (size-1) / max(dims)
    width, height = np.round(dims*scale).astype(int) + 1
    points = np.array([(pin["x_mm"], pin["y_mm"]) for pin in route["anchors"]])
    path = np.array(route["path"])
    side = np.array([1 if direction == "ccw" else -1 for direction in route["winding_sides"]])
    thread = settings["thread_mm"] * thread_scale
    transmission = 1 - (settings["opacity"] if opacity is None else opacity)
    radius = (settings["shaft_mm"] + thread) / 2
    delta = np.diff(points[path], axis=0)
    length = np.linalg.norm(delta, axis=1)
    unit = delta/length[:, None]
    offset = radius * (side[:-1]-side[1:]) / length
    perpendicular = unit[:, ::-1] * np.array([-1, 1])
    normal = offset[:, None]*unit + np.sqrt(1-offset**2)[:, None]*perpendicular
    starts = (points[path[:-1]] + radius*side[:-1,None]*normal + dims/2)*scale
    ends = (points[path[1:]] + radius*side[1:,None]*normal + dims/2)*scale

    samples = 4
    gray = np.ones((height*samples, width*samples), dtype=np.float32)
    for start, end in zip(starts, ends):
        start = (start+.5)*samples-.5
        end = (end+.5)*samples-.5
        grid = gray
        if abs(end[0]-start[0]) < abs(end[1]-start[1]):
            start, end, grid = start[::-1], end[::-1], gray.T
        if start[0] > end[0]:
            start, end = end, start
        x = np.arange(max(0, int(np.ceil(start[0]))), min(grid.shape[1], int(end[0])+1))
        slope = (end[1]-start[1]) / (end[0]-start[0])
        y = start[1] + (x-start[0])*slope
        half = thread*scale*samples*np.hypot(1, slope)/2
        for shift in range(-int(np.ceil(half)), int(np.ceil(half))+2):
            row = np.floor(y).astype(int)+shift
            covered = (abs(row-y)<=half) & (row>=0) & (row<grid.shape[0])
            grid[row[covered], x[covered]] *= transmission
    # The 3 mm opaque heads hide both ends and the short wrap around each shaft.
    for point in (points+dims/2)*scale:
        center = tuple(np.rint(((point+.5)*samples-.5)*16).astype(int))
        cv2.circle(gray, center, round(1.5*scale*samples*16), 87/255, -1, cv2.LINE_8, 4)
    return np.rint(cv2.resize(gray, (width,height), interpolation=cv2.INTER_AREA)*255).astype("uint8")


def target_array(target, shape):
    return np.asarray(Image.open(BytesIO(target)).convert("L").resize(
        (shape[1], shape[0]), Image.Resampling.LANCZOS), dtype=np.float32) / 255


def correlation(x, y):
    x = x - x.mean()
    y = y - y.mean()
    return float(np.mean(x*y) / max(1e-6, np.sqrt(np.mean(x*x)*np.mean(y*y))))


def detail_windows(reference):
    """Overlapping regions with enough contrast to measure local likeness."""
    h, w = reference.shape
    for cy in (.2, .4, .6, .8):
        for cx in (.2, .4, .6, .8):
            box = np.s_[int((cy-.16)*h):int((cy+.16)*h), int((cx-.16)*w):int((cx+.16)*w)]
            if reference[box].std() >= .045:
                yield box


def assess(target, image, regions=()):
    source = target_array(target, image.shape)
    image = image.astype(np.float32) / 255
    blur = lambda a, sigma: cv2.GaussianBlur(a, (0, 0), sigma)
    source, image = blur(source, 1.5), blur(image, 1.5)
    detail = correlation(source-blur(source, 7), image-blur(image, 7))
    white = source >= .94
    white_ink = float((1-image[white]).mean()) if white.any() else 0.
    tone = float(np.mean((source-image)**2)**.5)
    whole = correlation(source, image)
    local = [correlation(source[box], image[box]) for box in detail_windows(source)]
    local_mean = float(np.mean(local)) if local else whole
    metrics = dict(local_structure=local_mean, whole=whole, detail=detail, tone_error=tone, white_ink=white_ink)
    h,w = image.shape
    for name, (x,y,u,v) in regions:
        box = np.s_[int(h*y):int(h*v), int(w*x):int(w*u)]
        metrics[name] = correlation(source[box], image[box])
    metrics["score"] = .5*whole + .5*detail - .4*tone - .6*white_ink
    return metrics


def png(image):
    buffer = BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    return buffer.getvalue()
