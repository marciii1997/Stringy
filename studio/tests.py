from datetime import UTC, date, datetime
from io import BytesIO
from itertools import pairwise
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from PIL import Image

from .models import Artwork, Calendar


class CalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.person = get_user_model().objects.create_user(username="winter")
        cls.other = get_user_model().objects.create_user(username="gast")
        cls.admin = get_user_model().objects.create_superuser(username="atelier")
        cls.path = [n % 360 for n in range(298)]
        cls.art = Artwork.objects.create(
            title="Geheimes Katzenmotiv",
            source=b"private",
            count=297,
            route={"path": cls.path},
            state="ready",
        )
        cls.gift = Calendar.objects.create(
            title="Lichterzeit", artwork=cls.art, recipient=cls.person, year=2026
        )

    def setUp(self):
        self.client.force_login(self.person)

    def test_split_preserves_every_segment(self):
        ranges = [self.gift.bounds(day) for day in range(1, 25)]
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], self.art.count)
        self.assertTrue(all(left[1] == right[0] for left, right in pairwise(ranges)))
        self.assertEqual({end - start for start, end in ranges}, {12, 13})

    def test_release_at_berlin_midnight(self):
        for instant, expected in [
            ("2026-11-30T22:59:59", 0),
            ("2026-11-30T23:00:00", 1),
            ("2026-12-23T23:00:00", 24),
            ("2027-01-01T00:00:00", 24),
        ]:
            with patch(
                "django.utils.timezone.now",
                return_value=datetime.fromisoformat(instant).replace(tzinfo=UTC),
            ):
                self.assertEqual(self.gift.unlocked(), expected)

    @patch("django.utils.timezone.localdate", return_value=date(2026, 12, 1))
    def test_recipient_only_receives_released_prefix(self, _):
        url = reverse("door", args=[self.gift.pk, 1])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, self.art.title)
        self.assertEqual(res.context["playback"]["path"], self.path[:14])
        self.assertEqual(self.client.get(reverse("door", args=[self.gift.pk, 2])).status_code, 404)
        self.assertEqual(self.client.post(reverse("complete", args=[self.gift.pk, 2])).status_code, 404)
        for kind in ("route", "preview", "target"):
            self.assertEqual(
                self.client.get(reverse("asset", args=[self.art.pk, kind])).status_code, 302
            )
        self.client.force_login(self.other)
        for name, args in [("calendar", [self.gift.pk]), ("door", [self.gift.pk, 1])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)
        self.assertEqual(self.client.post(reverse("complete", args=[self.gift.pk, 1])).status_code, 404)

    @patch("django.utils.timezone.localdate", return_value=date(2026, 12, 24))
    def test_progress_is_sequential_idempotent_and_csrf_protected(self, _):
        for day in (3, 1, 1, 2):
            url = reverse("complete", args=[self.gift.pk, day])
            self.assertEqual(self.client.post(url).status_code, 302)
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.finished, 2)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.person)
        self.assertEqual(csrf_client.post(reverse("complete", args=[self.gift.pk, 3])).status_code, 403)

    def test_admin_upload_and_private_simulation(self):
        self.client.force_login(self.admin)
        data = BytesIO()
        Image.new("RGB", (32, 32), "black").save(data, format="PNG")
        photo = SimpleUploadedFile("test.png", data.getvalue(), content_type="image/png")
        res = self.client.post(
            reverse("admin:studio_artwork_add"),
            {
                "photo": photo,
                "title": "Nacht",
                "diameter": 600,
                "thread": 0.25,
                "anchors": 360,
                "budget": 12000,
                "passes": 3,
                "detail": 1,
                "zoom": 1,
                "center_x": 50,
                "center_y": 50,
                "gamma": 1,
                "opacity": 1,
            },
        )
        self.assertEqual(res.status_code, 302)
        upload = Artwork.objects.get(title="Nacht")
        self.assertEqual(upload.source, data.getvalue())
        self.assertEqual(upload.state, "queued")
        self.assertEqual(self.client.get(reverse("simulation", args=[self.art.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("asset", args=[self.art.pk, "route"])).json()["path"], self.path
        )

    @patch("django.utils.timezone.localdate", return_value=date(2026, 12, 1))
    def test_two_calendars_have_independent_progress(self, _):
        second = Calendar.objects.create(
            title="Stern", artwork=self.art, recipient=self.person, year=2026
        )
        self.assertContains(self.client.get("/"), "Stern")
        self.assertContains(self.client.get("/"), "Lichterzeit")
        self.client.post(reverse("complete", args=[second.pk, 1]))
        second.refresh_from_db()
        self.gift.refresh_from_db()
        self.assertEqual((second.finished, self.gift.finished), (1, 0))

    def test_recalculate_reuses_photo_and_preserves_assigned_calendar(self):
        url = reverse("recalculate", args=[self.art.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        form = self.client.get(url).context["form"]
        data = form.initial.copy()
        self.assertEqual(data["diameter"], self.art.diameter)
        self.assertFalse(self.client.post(url, {**data, "zoom": 0}).context["form"].is_valid())
        data.update(title="Feines Porträt", zoom=2, center_x=61, gamma=0.75, thread=0.15)
        result = self.client.post(url, data)
        variant = Artwork.objects.get(previous=self.art)
        self.assertRedirects(result, reverse("simulation", args=[variant.pk]))
        self.assertEqual(variant.source, self.art.source)
        self.assertEqual(
            (variant.zoom, variant.center_x, variant.gamma, variant.thread), (2, 61, 0.75, 0.15)
        )
        self.assertEqual(variant.state, Artwork.State.QUEUED)
        self.gift.refresh_from_db()
        self.art.refresh_from_db()
        self.assertEqual(self.gift.artwork_id, self.art.pk)
        self.assertEqual(self.art.route["path"], self.path)
        self.assertEqual(self.art.zoom, 1)


    def test_handcraft_creates_a_bounded_variant(self):
        from .handcraft import MAX_NAILS, SEGMENTS, THREAD_MM

        self.client.force_login(self.admin)
        url = reverse("handcraft", args=[self.art.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)
        variant = Artwork.objects.get(previous=self.art)
        self.assertRedirects(response, reverse("simulation", args=[variant.pk]))
        self.assertTrue(variant.preparation["handcraft"])
        self.assertEqual((variant.anchors, variant.budget, variant.thread), (MAX_NAILS, SEGMENTS, THREAD_MM))
        self.assertEqual(variant.auto_nails, 0)
        self.assertFalse(variant.quality_search)
        self.gift.refresh_from_db()
        self.assertEqual(self.gift.artwork_id, self.art.pk)


class WorkerTests(TestCase):
    def test_stale_claim_is_recovered_while_active_claim_is_preserved(self):
        from datetime import timedelta

        from django.utils import timezone

        from studio.management.commands.process_art import process_one

        now = timezone.now()
        stale = Artwork.objects.create(
            title="Alt", state="running", source=b"x", claimed_at=now - timedelta(minutes=7)
        )
        active = Artwork.objects.create(title="Aktiv", state="running", source=b"x", claimed_at=now)
        with self.settings(STRINGY_BIN="/missing-stringy-test-binary"):
            self.assertTrue(process_one())
            self.assertFalse(process_one())
        stale.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(stale.state, "failed")
        self.assertEqual(active.state, "running")


class QualityTests(TestCase):
    def test_reference_measure_distinguishes_a_spatially_wrong_image(self):
        from .motif import encoded
        from .quality import view_quality

        target = Image.new("L", (128, 128), 255)
        target.paste(0, (30, 40, 55, 80))
        wrong = target.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        target_png = encoded(target, "PNG")
        exact = view_quality(target_png, target_png)
        shifted = view_quality(target_png, encoded(wrong, "PNG"))
        self.assertAlmostEqual(exact["ssim"], 1, places=5)
        self.assertEqual(exact["tone_rmse"], 0)
        self.assertLess(shifted["ssim"], exact["ssim"])
        self.assertGreater(shifted["tone_rmse"], 0)

    def test_worker_selects_the_better_reference_and_preserves_it_after_replay(self):
        import json
        from pathlib import Path

        from .management.commands.process_art import process_one
        from .motif import encoded

        good = encoded(Image.new("L", (64, 64), 120), "PNG")
        bad = encoded(Image.new("L", (64, 64), 20), "PNG")
        old = Artwork.objects.create(
            title="Alt", source=good, state="ready", route={"settings": {}, "path": [0, 8]}
        )
        art = Artwork.objects.create(title="Neu", source=good, previous=old)

        def simulate(command, deadline, renew):
            folder = Path(command[command.index("--output") + 1])
            if "--replay" in command:
                (folder / "preview.png").write_bytes(bad)
            else:
                size = int(command[command.index("--raster-size") + 1])
                (folder / "target.png").write_bytes(good)
                (folder / "preview.png").write_bytes(good if size == 512 else bad)
                (folder / "path.json").write_text(
                    json.dumps({"settings": {"raster_size": size}, "path": [0, 9]})
                )
            return True

        with patch("studio.management.commands.process_art.run_optimizer", side_effect=simulate):
            self.assertTrue(process_one())
        art.refresh_from_db()
        self.assertEqual(art.state, "ready")
        self.assertEqual(art.route["settings"]["raster_size"], 512)
        self.assertEqual(art.preview, good)
        self.assertEqual([r["raster"] for r in art.route["quality_trials"]], [512, 1024])
        self.assertGreater(art.route["view_quality"]["ssim"], art.route["previous_quality"]["ssim"])
