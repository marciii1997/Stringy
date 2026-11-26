import os
from datetime import date, timedelta
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw

from .layout_search import fingerprint
from .management.commands.process_art import process_one
from .models import Artwork, Calendar


@skipUnless(os.getenv("STRINGY_RELEASE_CHECK"), "requires compiled Rust optimizer")
class ReleaseFlow(TestCase):
    def test_photo_through_published_calendar(self):
        self.client.force_login(get_user_model().objects.create_superuser("maker"))
        recipient = get_user_model().objects.create_user("recipient")
        image = Image.new("L", (96, 96), 255)
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 15, 80, 85), fill=64)
        draw.ellipse((22, 28, 34, 40), fill=228)
        draw.ellipse((55, 28, 67, 40), fill=228)
        for x in range(28, 62, 4):
            draw.line((x, 48, x + 10, 74), fill=168)
        photo = BytesIO()
        image.save(photo, "PNG")
        self.client.post(
            reverse("upload"), {"photo": SimpleUploadedFile("Geheim.png", photo.getvalue())}
        )
        from .layout_search import automatic

        def stop_after_checkpoint(art, source, folder, renew, save):
            def save_then_stop(**values):
                save(**values)
                return False

            return automatic(art, source, folder, renew, save_then_stop)

        with patch("studio.layout_search.automatic", side_effect=stop_after_checkpoint):
            self.assertTrue(process_one())
        paused = Artwork.objects.get()
        self.assertEqual(paused.state, "running")
        self.assertEqual(len(paused.route["layout_search"]["trials"]), 1)
        Artwork.objects.filter(pk=paused.pk).update(
            claimed_at=timezone.now() - timedelta(minutes=10)
        )
        self.assertTrue(process_one())
        art = Artwork.objects.get()
        self.assertEqual(art.state, "ready", art.error)
        route = art.route
        self.assertEqual(route["version_hash"], fingerprint(route))
        self.assertGreaterEqual(route["placement"]["min_pitch_mm"], 16)
        self.assertLessEqual(len(route["anchors"]), 350)
        self.assertEqual(len(route["winding_sides"]), art.count + 1)
        self.assertGreater(route["physical_quality"]["whole"], 0.5)
        self.assertGreaterEqual(
            route["physical_quality"]["score"], route["layout_search"]["trials"][0]["score"]
        )
        self.assertLess(route["physical_quality"]["white_ink"], 0.15)
        xml = ElementTree.fromstring(
            self.client.post(
                reverse("laser", args=[art.pk]),
                {"mode": "hole", "feature_mm": 1, "margin_mm": 5, "labels": "all"},
            ).content
        )
        self.assertEqual(xml.get("width"), "500mm")
        for pin in route["anchors"]:
            hole = xml.find(f".//*[@id='anker-{pin['id']}']")
            self.assertAlmostEqual(float(hole.get("cx")), 250 + pin["x_mm"], places=3)
            self.assertAlmostEqual(float(hole.get("cy")), 250 + pin["y_mm"], places=3)
        self.client.post(
            reverse("give", args=[art.pk]), {"recipient": recipient.pk, "instant_open": "on"}
        )
        gift = Calendar.objects.get()
        self.client.post(reverse("new_version", args=[art.pk]))
        self.assertEqual(Artwork.objects.latest("pk").previous_id, art.pk)
        self.assertEqual(Calendar.objects.get().artwork_id, art.pk)
        step = reverse("thread_step", args=[gift.pk, 1])
        for _ in range(2):
            result = self.client.post(
                step, {"step": 0, "action": "next", "preview": "1"}, HTTP_ACCEPT="application/json"
            ).json()
            self.assertEqual(result["step"], 1)
        self.assertEqual(
            self.client.get(reverse("door", args=[gift.pk, 1])).context["playback"]["initial"], 1
        )
        self.client.post(reverse("publish_calendar", args=[gift.pk]))
        self.client.force_login(recipient)
        with patch("django.utils.timezone.localdate", return_value=date(gift.year, 12, 1)):
            door = self.client.get(reverse("door", args=[gift.pk, 1]))
            self.assertNotContains(door, "Geheim")
            self.assertEqual(
                door.context["playback"]["path"], route["path"][: gift.bounds(1)[1] + 1]
            )
            self.assertEqual(self.client.get(reverse("door", args=[gift.pk, 2])).status_code, 404)
            self.assertEqual(
                self.client.get(reverse("asset", args=[art.pk, "route"])).status_code, 302
            )
