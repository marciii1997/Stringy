"""Private, local subject analysis and raster-aligned optimizer guidance."""

import math
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .models import Artwork, MotifAnalysis

cv2.setNumThreads(1)
DEFAULTS = {"background": 65, "clothing": 30, "focus": 65, "highlights": 50, "edges": 55}
PROFILES = {
    "baseline": ("Original", ()),
    "focus": ("Fokus und Lichter", ("focus", "highlights")),
    "background": ("Hintergrund", ("background",)),
    "edges": ("Konturen", ("edges",)),
    "combined": ("Hintergrund und Konturen", ("background", "edges")),
    "custom": ("Eigene Mischung", tuple(DEFAULTS)),
}
LAYERS = ("subject", "background", "focus", "unfocus", "clothing", "unclothe")


def encoded(image, fmt):
    stream = BytesIO()
    image.save(stream, fmt)
    return stream.getvalue()


def detect_faces(gray, cascade):
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + cascade + ".xml")
    return detector.detectMultiScale(cv2.equalizeHist(gray), 1.08, 5, minSize=(24, 24))


def portrait_focus(gray, focus, face_box):
    """Search only inside the upper or lower part of this face."""
    x, y, fw, fh = face_box
    for cascade, top, bottom, neighbors in [
        ("haarcascade_eye_tree_eyeglasses", 0.1, 0.62, 4),
        ("haarcascade_smile", 0.52, 1.0, 17),
    ]:
        y0 = max(0, y + round(fh * top))
        x0 = max(0, x)
        part = gray[y0 : y + round(fh * bottom), x0 : x + fw]
        if min(part.shape, default=0) < 12:
            continue
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + cascade + ".xml")
        for fx, fy, width, height in detector.detectMultiScale(part, 1.12, neighbors, minSize=(12, 8)):
            center = (x0 + fx + width // 2, y0 + fy + height // 2)
            cv2.ellipse(focus, center, (max(2, width // 2), max(2, height // 2)), 0, 0, 360, 255, -1)


def analyze(source):
    with Image.open(BytesIO(source)) as raw:
        photo = ImageOps.exif_transpose(raw).convert("RGBA")
    dimensions = photo.size
    photo.thumbnail((640, 640), Image.Resampling.LANCZOS)
    white = Image.new("RGB", photo.size, "white")
    white.paste(photo, mask=photo.getchannel("A"))
    photo = white
    rgb = np.asarray(photo)
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    faces = []
    if min(w, h) >= 24:
        for kind, cascade in [
            ("person", "haarcascade_frontalface_default"),
            ("cat", "haarcascade_frontalcatface_extended"),
        ]:
            for x, y, width, height in detect_faces(gray, cascade):
                # Both cascades can respond to the same face.
                if any(
                    abs(x + width / 2 - f["cx"] * w) < width * 0.5
                    and abs(y + height / 2 - f["cy"] * h) < height * 0.5
                    for f in faces
                ):
                    continue
                faces.append(
                    {
                        "kind": kind,
                        "cx": float(x + width / 2) / w,
                        "cy": float(y + height / 2) / h,
                        "w": float(width) / w,
                        "h": float(height) / h,
                    }
                )
    subject = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    focus = np.zeros_like(subject)
    clothing = np.zeros_like(subject)
    cv2.ellipse(
        subject,
        (w // 2, h // 2),
        (max(1, int(w * 0.38)), max(1, int(h * 0.46))),
        0,
        0,
        360,
        cv2.GC_PR_FGD,
        -1,
    )
    for f in faces:
        x, y = int(f["cx"] * w), int(f["cy"] * h)
        fw, fh = max(1, int(f["w"] * w)), max(1, int(f["h"] * h))
        cv2.ellipse(subject, (x, y + fh), (fw, fh * 2), 0, 0, 360, cv2.GC_PR_FGD, -1)
        cv2.ellipse(subject, (x, y), (max(1, fw // 3), max(1, fh // 3)), 0, 0, 360, cv2.GC_FGD, -1)
        cv2.ellipse(
            focus,
            (x, y),
            (int(fw * 0.56), int(fh * 0.65)),
            0,
            0,
            360,
            200 if f["kind"] == "person" else 255,
            -1,
        )
        if f["kind"] == "person":
            portrait_focus(gray, focus, (x - fw // 2, y - fh // 2, fw, fh))
            cv2.ellipse(clothing, (x, y + 2 * fh), (int(fw * 1.5), int(fh * 1.3)), 0, 0, 360, 255, -1)
    if min(w, h) >= 12:
        subject[[0, -1], :] = subject[:, [0, -1]] = cv2.GC_BGD
        bg, fg = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(rgb, subject, None, bg, fg, 4, cv2.GC_INIT_WITH_MASK)
        except cv2.error:
            pass  # Degenerate or near-uniform pictures still have editable masks.
    soft = ((subject % 2) * 255).astype(np.uint8)
    masks = Image.merge(
        "RGB",
        tuple(
            Image.fromarray(m).filter(ImageFilter.GaussianBlur(r))
            for m, r in [(soft, 1.6), (focus, 4), (clothing, 5)]
        ),
    )
    return {
        "photo": encoded(photo, "PNG"),
        "masks": encoded(masks, "PNG"),
        "faces": faces,
        "dimensions": dimensions,
    }


def analysis_for(art):
    root = art
    while root.previous_id:
        root = Artwork.objects.only("previous_id").get(pk=root.previous_id)
    cached = MotifAnalysis.objects.filter(original=root).first()
    if cached:
        return cached
    return MotifAnalysis.objects.get_or_create(original=root, defaults=analyze(root.source))[0]


def framing(analysis):
    w, h = analysis.dimensions
    if analysis.faces:
        corners = [
            (w * (f["cx"] + i * f["w"] * 0.6), h * (f["cy"] + j * f["h"] * 0.67))
            for f in analysis.faces
            for i in (-1, 1)
            for j in (-1, 1)
        ]
    else:
        subject = Image.open(BytesIO(analysis.masks)).getchannel("R")
        box = subject.point(lambda v: 255 if v >= 160 else 0).getbbox()
        if not box:
            return {"zoom": 1, "center_x": 50, "center_y": 50}
        corners = [
            (x * w / subject.width, y * h / subject.height)
            for x in (box[0], box[2])
            for y in (box[1], box[3])
        ]
    x, y = [(min(axis) + max(axis)) / 2 for axis in zip(*corners)]
    side = 2 * max(math.hypot(px - x, py - y) for px, py in corners)
    return {
        "zoom": max(0.01, min(5, math.floor(10000 * min(w, h) / side) / 10000)),
        "center_x": round(100 * x / w, 4),
        "center_y": round(100 * y / h, 4),
    }


def edited_masks(analysis, strokes):
    masks = list(Image.open(BytesIO(analysis.masks)).split())
    w, h = masks[0].size
    for stroke in strokes:
        layer = LAYERS.index(stroke["layer"])
        mask = masks[layer // 2]
        brush = Image.new("L", (w, h))
        draw = ImageDraw.Draw(brush)
        radius = max(1, round(stroke["radius"] * min(w, h)))
        points = [(round(x * (w - 1)), round(y * (h - 1))) for x, y in stroke["points"]]
        draw.line(points, fill=255, width=2 * radius)
        for x, y in points:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
        mask.paste(
            255 if layer % 2 == 0 else 0,
            mask=brush.filter(ImageFilter.GaussianBlur(max(0.5, radius * 0.12))),
        )
    return masks


def guide_for(art, analysis, size=512):
    params = art.preparation
    subject, focus, clothing = edited_masks(analysis, params.get("strokes", []))
    w, h = analysis.dimensions
    side = max(1, math.floor(min(w, h) / art.zoom + 0.5))

    def axis(length, center, preview_length):
        offset = math.floor(length * center / 100 - side / 2)
        if not params.get("pad") and art.zoom >= 1:
            offset = max(0, min(length - side, offset))
        start, end = max(0, offset), min(length, offset + side)

        def pixel(p):
            return math.floor((p - offset) * size / side + 0.5)

        return start * preview_length / length, end * preview_length / length, pixel(start), pixel(end)

    x, r, left, right = axis(w, art.center_x, subject.width)
    y, b, top, bottom = axis(h, art.center_y, subject.height)

    def raster(im, fill=0):
        values = np.full((size, size), fill, np.float32)
        if right > left and bottom > top:
            values[top:bottom, left:right] = (
                np.asarray(
                    im.resize((right - left, bottom - top), Image.Resampling.BILINEAR, box=(x, y, r, b)),
                    dtype=np.float32,
                )
                / 255
            )
        return values

    subject, focus, clothing = map(raster, (subject, focus, clothing))
    light = raster(Image.open(BytesIO(analysis.photo)).convert("L"), fill=1) ** art.gamma
    bg = params["background"] / 100 * (1 - subject)
    cloth = params["clothing"] / 100 * clothing * subject
    fade = 1 - (1 - bg) * (1 - cloth)
    priority = focus * params["focus"] / 100
    # A bright patch within a marked face (teeth, eye reflections) costs more to cross.
    priority += focus * params["highlights"] / 100 * np.clip((light - 0.65) / 0.3, 0, 1)
    contours = np.zeros_like(subject)
    if params["edges"]:
        # Sobel responses at two viewing scales suppress isolated pixel noise.
        # The Rust objective compares signed differences across these transitions.
        target_light = 1 - (1 - light) * (1 - fade)
        for sigma in (0.8, 2.4):
            smooth = cv2.GaussianBlur(target_light, (0, 0), sigma)
            dx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3, scale=sigma / 8)
            dy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3, scale=sigma / 8)
            contours = np.maximum(contours, cv2.magnitude(dx, dy))
        contours = np.clip(contours * 12, 0, 1)
        contours *= params["edges"] / 100 * np.maximum(0.15 + 0.85 * subject, focus)
    rgb = np.stack((fade, np.clip(priority, 0, 1), contours), axis=2)
    return Image.fromarray(np.rint(255 * rgb).astype(np.uint8))
