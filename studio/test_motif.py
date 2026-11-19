import json
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .forms import CalculationForm
from .models import Artwork, MotifAnalysis
from .motif import DEFAULTS, analysis_for, encoded, framing, guide_for


class MotifTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.art = Artwork.objects.create(
            title="Zwei Motive", source=encoded(Image.new("RGB", (100, 60), "gray"), "PNG")
        )
        cls.admin = get_user_model().objects.create_superuser(username="atelier")
        cls.person = get_user_model().objects.create_user(username="gast")
        blank = Image.new("RGB", (100, 60))
        cls.analysis = MotifAnalysis.objects.create(
            original=cls.art,
            photo=cls.art.source,
            masks=encoded(blank, "PNG"),
            dimensions=[100, 60],
            faces=[{"cx": x, "cy": 0.25, "w": 0.2, "h": 0.45, "kind": "person"} for x in (0.2, 0.8)],
        )

    def data(self):
        return {
            **{k: getattr(self.art, k) for k in CalculationForm.Meta.fields},
            **DEFAULTS,
            "action": "compare",
            "strokes": "[]",
            "pad": "on",
        }

    def test_group_is_private_and_steps_keep_their_own_parameters(self):
        self.client.force_login(self.admin)
        url = reverse("motif", args=[self.art.pk])
        response = self.client.post(url, self.data())
        self.assertEqual(response.status_code, 302)
        variants = list(Artwork.objects.filter(previous=self.art))
        self.assertEqual(len(variants), 6)
        self.assertEqual(len({v.comparison for v in variants}), 1)
        self.assertContains(self.client.get(response.url), "Eigene Mischung")
        self.assertEqual(
            [v.preparation["edges"] for v in variants if v.preparation["profile"] == "baseline"], [0]
        )
        self.assertTrue(all(v.source == self.art.source and v.preparation["pad"] for v in variants))
        self.assertEqual(analysis_for(variants[0]).pk, self.analysis.pk)
        self.assertEqual(self.art.preparation, {})
        self.client.force_login(self.person)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get(response.url).status_code, 302)

    def test_bad_strokes_do_not_queue_jobs(self):
        self.client.force_login(self.admin)
        data = self.data()
        data["strokes"] = json.dumps([{"layer": "focus", "radius": 0.1, "points": [[2, 0]]}])
        res = self.client.post(reverse("motif", args=[self.art.pk]), data)
        self.assertContains(res, "Pinselkoordinaten")
        self.assertFalse(Artwork.objects.filter(previous=self.art).exists())

    def test_group_framing_and_guidance_match_a_padded_source(self):
        crop = framing(self.analysis)
        self.assertLess(crop["zoom"], 1)
        self.art.zoom = crop["zoom"]
        self.art.center_x, self.art.center_y = crop["center_x"], crop["center_y"]
        self.art.preparation = dict(
            **DEFAULTS,
            pad=True,
            strokes=[
                {"layer": k, "radius": 0.1, "points": [[0.5, 0.25]]}
                for k in ("subject", "focus", "clothing")
            ],
        )
        guide = guide_for(self.art, self.analysis, 100)
        red, green, blue = guide.getpixel((50, 50))
        self.assertAlmostEqual(red, 255 * DEFAULTS["clothing"] / 100, delta=2)
        self.assertGreater(green, 100)
        self.assertLess(blue, 20)  # Flat paint inside the mask has no contour.
        self.assertGreater(guide.getchannel("B").getextrema()[1], 20)
        self.assertEqual(guide.getpixel((99, 99))[1], 0)
        self.assertEqual(Image.open(BytesIO(self.analysis.masks)).getbbox(), None)
