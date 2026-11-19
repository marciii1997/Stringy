import math
from datetime import date
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Artwork, Calendar


class CanvasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username="admin")
        cls.person = get_user_model().objects.create_user(username="recipient")
        anchors = [
            {"id": i, "x_mm": 300 * math.sin(math.radians(i)), "y_mm": -300 * math.cos(math.radians(i))}
            for i in range(360)
        ]
        cls.art = Artwork.objects.create(
            title="Motiv",
            source=b"",
            state="ready",
            count=48,
            route={"anchors": anchors, "settings": {"diameter_mm": 600}, "path": list(range(48)) + [0]},
        )
        cls.gift = Calendar.objects.create(
            title="Advent", year=2026, recipient=cls.person, artwork=cls.art
        )

    def test_real_scale_and_opt_in_layers(self):
        self.client.force_login(self.admin)
        url = reverse("laser", args=[self.art.pk])
        data = {"mode": "mark", "feature_mm": 1, "margin_mm": 5, "labels": "ten"}
        xml = ET.fromstring(self.client.post(url, data).content)
        self.assertEqual(xml.get("width"), "610mm")
        self.assertEqual(xml.get("viewBox"), "0 0 610 610")
        self.assertEqual(len(xml.findall(".//{*}circle")), 0)
        self.assertEqual(len(xml.find(".//*[@id='anker-markierungen']")), 360)
        self.assertEqual(len(xml.find(".//*[@id='nummern-gravur']")), 36)
        data.update(mode="hole", feature_mm="1.4", labels="all", outline="on")
        xml = ET.fromstring(self.client.post(url, data).content)
        self.assertEqual(len(xml.find(".//*[@id='nummern-gravur']")), 360)
        holes = xml.find(".//*[@id='anker-lochkonturen']")
        for anchor, circle in zip(self.art.route["anchors"], holes, strict=True):
            self.assertEqual(circle.get("id"), f"anker-{anchor['id']}")
            self.assertAlmostEqual(float(circle.get("cx")), 305 + anchor["x_mm"], places=3)
            self.assertAlmostEqual(float(circle.get("cy")), 305 + anchor["y_mm"], places=3)
            self.assertEqual(float(circle.get("r")), 0.7)
        self.assertEqual(float(xml.find(".//*[@id='aussenkontur']/{*}circle").get("r")), 305)

    def test_invalid_geometry_and_private_export(self):
        url = reverse("laser", args=[self.art.pk])
        data = {"mode": "hole", "feature_mm": 6, "margin_mm": 1, "labels": "zero"}
        self.client.force_login(self.person)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.post(url, data)
        self.assertEqual(set(response.context["form"].errors), {"feature_mm", "margin_mm"})

    @patch("django.utils.timezone.localdate", return_value=date(2026, 12, 1))
    def test_resume_undo_stale_click_and_reset(self, _):
        self.client.force_login(self.person)
        url = reverse("thread_step", args=[self.gift.pk, 1])
        door = reverse("door", args=[self.gift.pk, 1])
        for _ in range(2):
            self.client.post(url, {"step": 0, "action": "next"})
        self.client.logout()
        self.client.force_login(self.person)
        response = self.client.get(door)
        self.assertEqual(response.context["playback"]["initial"], 1)
        self.assertEqual((response.context["current"], response.context["following"]), (1, 2))
        self.client.post(url, {"step": 1, "action": "back"})
        self.assertEqual(self.client.get(door).context["current"], 0)
        self.assertEqual(self.client.post(url, {"step": 0, "action": "back"}).status_code, 400)
        self.assertEqual(
            self.client.post(
                reverse("thread_step", args=[self.gift.pk, 2]), {"step": 0, "action": "next"}
            ).status_code,
            404,
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {"step": 0, "action": "next"}).status_code, 404)
        self.client.force_login(self.person)
        for step in (0, 1):
            self.client.post(url, {"step": step, "action": "next"})
        self.assertContains(self.client.get(door), "Türchen abschließen")
        self.client.post(reverse("complete", args=[self.gift.pk, 1]))
        self.client.post(url, {"step": 0, "action": "next"})
        self.gift.refresh_from_db()
        self.assertEqual((self.gift.finished, self.gift.step), (1, 0))

    @patch("django.utils.timezone.localdate", return_value=date(2026, 12, 1))
    def test_free_layout_is_shared_by_export_and_player_and_doors_limit_disclosure(self, _):
        layout = [[0, 0], [0.01, 0], [0, 0.5], [-0.9, 0]]
        self.art.nail_positions = layout
        self.art.anchors = len(layout)
        self.art.route = {
            "settings": {"diameter_mm": 600, "nail_positions": layout},
            "anchors": [{"id": i, "x_mm": x * 300, "y_mm": y * 300} for i, (x, y) in enumerate(layout)],
            "path": [0, 1, 2, 3] * 12 + [0],
        }
        self.art.save()
        self.client.force_login(self.admin)
        sim = self.client.get(reverse("simulation", args=[self.art.pk]))
        self.assertEqual(list(sim.context["playback"]["positions"].values()), layout)
        url = reverse("laser", args=[self.art.pk])
        data = {"mode": "hole", "feature_mm": 4, "margin_mm": 5, "labels": "all"}
        self.assertIn("feature_mm", self.client.post(url, data).context["form"].errors)
        svg = ET.fromstring(self.client.post(url, {**data, "feature_mm": 1, "margin_mm": 5}).content)
        for p in self.art.route["anchors"]:
            nail = svg.find(f".//*[@id='anker-{p['id']}']")
            self.assertAlmostEqual(float(nail.get("cx")), 305 + p["x_mm"])
            self.assertAlmostEqual(float(nail.get("cy")), 305 + p["y_mm"])
        self.client.force_login(self.person)
        playback = self.client.get(reverse("door", args=[self.gift.pk, 1])).context["playback"]
        self.assertEqual(playback["path"], [0, 1, 2])
        self.assertEqual(playback["positions"], {0: layout[0], 1: layout[1], 2: layout[2]})
