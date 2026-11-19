import json
from math import dist
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from PIL import Image

from .auto_nails import keep_used_nails, suggest_positions
from .management.commands.process_art import process_one
from .models import Artwork
from .motif import encoded


class AutomaticNailTests(TestCase):
    def test_proposals_follow_missing_detail_and_respect_existing_pins(self):
        target = Image.new("L", (256, 256), 255)
        target.paste(0, (70, 100, 100, 133))
        photo = encoded(target, "PNG")
        white = encoded(Image.new("L", target.size, 255), "PNG")
        self.assertEqual(suggest_positions(photo, photo, [], 8, 600), [])
        points = suggest_positions(photo, white, [], 8, 600)
        self.assertTrue(points)
        self.assertTrue(all(-0.52 < x < -0.15 and -0.29 < y < 0.10 for x, y in points))
        shifted = suggest_positions(photo, white, points, 8, 600)
        self.assertTrue(all(dist(a, b) >= 0.13 for a in points for b in shifted))

    def test_pruning_preserves_geometry_and_every_base_id(self):
        positions = [[0, 0], [0.5, 0.5], [-0.5, 0.5], [0, 0.4], [0.2, 0.1]]
        route = {
            "settings": {"nail_positions": positions},
            "anchors": [
                {"id": i, "x_mm": x * 300, "y_mm": y * 300} for i, (x, y) in enumerate(positions)
            ],
            "path": [0, 4, 2],
            "segments": [{"from": 0, "to": 4}, {"from": 4, "to": 2}],
        }
        self.assertEqual(keep_used_nails(route, 3), 1)
        self.assertEqual(route["path"], [0, 3, 2])
        self.assertEqual(route["settings"]["nail_positions"], positions[:3] + [positions[4]])
        self.assertEqual(route["anchors"][3], {"id": 3, "x_mm": 60, "y_mm": 30})
        self.assertEqual(route["segments"][1], {"from": 3, "to": 2})

    def test_extra_nails_are_rejected_if_the_render_is_worse(self):
        target = encoded(Image.new("L", (64, 64), 120), "PNG")
        art = Artwork.objects.create(source=target, title="Test", auto_nails=4, quality_search=False)

        def simulate(command, deadline, renew):
            folder = Path(command[command.index("--output") + 1])
            added = "--nail-positions" in command
            pixels = 250 if added else 130
            (folder / "target.png").write_bytes(target)
            (folder / "preview.png").write_bytes(encoded(Image.new("L", (64, 64), pixels), "PNG"))
            base = [[0, -1], [1, 0], [0, 1]]
            points = json.loads((folder / "nails.json").read_bytes()) if added else base
            route = {
                "settings": {"raster_size": 512, "anchors": len(points)},
                "path": [0, len(points) - 1],
                "anchors": [
                    {"id": i, "x_mm": 300 * x, "y_mm": 300 * y} for i, (x, y) in enumerate(points)
                ],
                "segments": [{"from": 0, "to": len(points) - 1}],
            }
            if added:
                route["settings"]["nail_positions"] = points
            (folder / "path.json").write_text(json.dumps(route))
            return True

        art.anchors = 3
        art.save()
        with patch("studio.management.commands.process_art.run_optimizer", side_effect=simulate):
            self.assertTrue(process_one())
        art.refresh_from_db()
        self.assertEqual(art.route["auto_nails"]["status"], "no_gain")
        self.assertEqual(art.route["auto_nails"]["added"], 0)
        self.assertEqual(art.winding_anchor_count, 3)
        self.assertEqual(art.route["path"], [0, 2])
