"""Millimetre SVG toolpaths for preparing the anchor board."""

import math
from decimal import Decimal
from xml.etree import ElementTree as ET

from django import forms


class LaserForm(forms.Form):
    mode = forms.ChoiceField(
        label="Ankerpositionen",
        choices=[
            ("mark", "Mittelpunkte mit Kreuzen markieren"),
            ("hole", "Lochkonturen als Kreise anlegen"),
        ],
    )
    feature_mm = forms.DecimalField(
        label="Kreuzbreite oder Lochdurchmesser (mm)",
        initial=1,
        min_value=Decimal("0.1"),
        max_value=20,
        decimal_places=2,
    )
    margin_mm = forms.DecimalField(
        label="Rand vom Ankermittelpunkt zur Außenkante (mm)",
        initial=5,
        min_value=Decimal("0.1"),
        max_value=100,
        decimal_places=2,
    )
    labels = forms.ChoiceField(
        label="Nummern gravieren",
        choices=[
            ("ten", "0 und jeden zehnten Anker"),
            ("all", "Alle Anker"),
            ("zero", "Nur Anker 0"),
            ("none", "Ohne Nummern"),
        ],
    )
    outline = forms.BooleanField(label="Außenkreis als Schnittkontur hinzufügen", required=False)

    def __init__(self, *args, diameter, count, **kwargs):
        super().__init__(*args, **kwargs)
        self.pitch = diameter * math.sin(math.pi / count)
        self.fields["feature_mm"].initial = min(1, round(self.pitch * 0.6, 2))

    def clean(self):
        data = super().clean()
        width, margin = data.get("feature_mm"), data.get("margin_mm")
        if width is not None and float(width) >= self.pitch:
            self.add_error(
                "feature_mm", f"Der Abstand zweier Ankermittelpunkte beträgt {self.pitch:.3f} mm."
            )
        if width is not None and margin is not None and margin <= width / 2:
            self.add_error(
                "margin_mm", "Der Rand muss größer als die halbe Kreuzbreite bzw. der Lochradius sein."
            )
        return data


# Single-line digits keep engraved labels independent of fonts and text import.
STROKES = (
    (0, 0, 1, 0),
    (1, 0, 1, 1),
    (1, 1, 1, 2),
    (1, 2, 0, 2),
    (0, 2, 0, 1),
    (0, 1, 0, 0),
    (0, 1, 1, 1),
)
DIGITS = ("012345", "12", "01643", "01623", "5612", "05623", "056432", "012", "0123456", "015623")


def number_path(number, x, y, scale):
    digits = str(number)
    x -= scale * (1 + 1.5 * (len(digits) - 1)) / 2
    y -= scale
    segments = []
    for digit in digits:
        for stroke in DIGITS[int(digit)]:
            x1, y1, x2, y2 = STROKES[int(stroke)]
            segments.append(
                f"M{x + x1 * scale:.4f},{y + y1 * scale:.4f} L{x + x2 * scale:.4f},{y + y2 * scale:.4f}"
            )
        x += 1.5 * scale
    return " ".join(segments)


def laser_svg(route, mode, feature_mm, margin_mm, labels, outline=False):
    diameter = route["settings"]["diameter_mm"]
    anchors = route["anchors"]
    span = diameter + 2 * float(margin_mm)
    center = span / 2
    half = float(feature_mm) / 2
    root = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width=f"{span:g}mm",
        height=f"{span:g}mm",
        viewBox=f"0 0 {span:g} {span:g}",
        fill="none",
    )

    def layer(name, color):
        return ET.SubElement(root, "g", {"id": name, "stroke": color, "stroke-width": "0.05"})

    holes = mode == "hole"
    positions = layer(
        "anker-lochkonturen" if holes else "anker-markierungen", "#ff0000" if holes else "#000000"
    )
    numbers = layer("nummern-gravur", "#0000ff") if labels != "none" else None
    # Keep upright digits clear of both the anchor and the label across the 0/last seam.
    radius = diameter / 2
    label_radius = radius - half - 4
    numbered = [
        a["id"]
        for a in anchors
        if labels == "all" or a["id"] == 0 or (labels == "ten" and a["id"] % 10 == 0)
    ]
    scale = 1.1
    if len(numbered) > 1:
        gap = min(numbered[1], len(anchors) - numbered[-1])
        scale = min(scale, 2 * label_radius * math.sin(math.pi * gap / len(anchors)) / 5.5)
    for anchor in anchors:
        dx, dy = anchor["x_mm"], anchor["y_mm"]
        x, y = center + dx, center + dy
        anchor_id = f"anker-{anchor['id']}"
        if holes:
            ET.SubElement(
                positions, "circle", id=anchor_id, cx=f"{x:.4f}", cy=f"{y:.4f}", r=f"{half:.4f}"
            )
        else:
            ET.SubElement(
                positions,
                "path",
                id=anchor_id,
                d=f"M{x - half:.4f},{y:.4f} h{2 * half:.4f} M{x:.4f},{y - half:.4f} v{2 * half:.4f}",
            )
        if numbers is not None and anchor["id"] in numbered:
            ET.SubElement(
                numbers,
                "path",
                id=f"nummer-{anchor['id']}",
                d=number_path(
                    anchor["id"],
                    center + dx * label_radius / radius,
                    center + dy * label_radius / radius,
                    scale,
                ),
            )
    if outline:
        ET.SubElement(
            layer("aussenkontur", "#00ff00"),
            "circle",
            cx=f"{center:g}",
            cy=f"{center:g}",
            r=f"{center:g}",
        )
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
