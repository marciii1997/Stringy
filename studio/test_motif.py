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

    def test_black_background_is_saved_and_only_used_in_background_profiles(self):
        self.client.force_login(self.admin)
        data = {**self.data(), "background_color": "black", "background": 100}
        self.client.post(reverse("motif", args=[self.art.pk]), data)
        variants = Artwork.objects.filter(previous=self.art)
        self.assertEqual(variants.filter(preparation__background_color="black").count(), 3)
        for variant in variants:
            self.assertEqual(
                variant.preparation["background"] == 100,
                variant.preparation.get("background_color") == "black",
            )
        custom = variants.get(preparation__profile="custom")
        self.assertEqual(
            self.client.get(reverse("motif", args=[custom.pk]))
            .context["mix"]["background_color"]
            .value(),
            "black",
        )

    def test_black_background_respects_strength_subject_and_padding(self):
        self.art.zoom = 0.5
        self.art.preparation = {
            **DEFAULTS,
            "background_color": "black",
            "pad": True,
            "strokes": [{"layer": "subject", "radius": 0.2, "points": [[0.5, 0.5]]}],
        }
        for strength, expected in [(100, 255), (50, 128), (0, None)]:
            self.art.preparation["background"] = strength
            _, ink = guide_for(self.art, self.analysis, 100)
            if expected is None:
                self.assertIsNone(ink)
            else:
                self.assertEqual(ink.getpixel((50, 50)), 0)
                self.assertEqual(ink.getpixel((30, 50)), expected)
                self.assertEqual(ink.getpixel((50, 5)), expected)

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
        guide, ink = guide_for(self.art, self.analysis, 100)
        self.assertIsNone(ink)
        red, green, blue = guide.getpixel((50, 50))
        self.assertAlmostEqual(red, 255 * DEFAULTS["clothing"] / 100, delta=2)
        self.assertGreater(green, 100)
        self.assertLess(blue, 20)  # Flat paint inside the mask has no contour.
        self.assertGreater(guide.getchannel("B").getextrema()[1], 20)
        self.assertEqual(guide.getpixel((99, 99))[1], 0)
        self.assertEqual(Image.open(BytesIO(self.analysis.masks)).getbbox(), None)

    def test_style_comparison_only_changes_the_foreground_style(self):
        self.client.force_login(self.admin)
        strokes = [{"layer": "subject", "radius": 0.1, "points": [[0.5, 0.5]]}]
        data = {
            **self.data(),
            "action": "styles",
            "style": "poster",
            "background": 100,
            "background_color": "gradient",
            "strokes": json.dumps(strokes),
        }
        response = self.client.post(reverse("motif", args=[self.art.pk]), data)
        variants = list(Artwork.objects.filter(previous=self.art).order_by("pk"))
        self.assertEqual([v.preparation["style"] for v in variants], ["natural", "smooth", "poster"])
        for variant in variants:
            self.assertEqual(variant.preparation["strokes"], strokes)
            self.assertEqual(variant.preparation["background_color"], "gradient")
            self.assertEqual(variant.preparation["background"], 100)
            self.assertEqual(variant.preparation["profile"], "styles")
        self.assertContains(self.client.get(response.url), "Sanft vereinfacht")
        form = self.client.get(reverse("motif", args=[variants[-1].pk])).context["mix"]
        self.assertEqual(form["style"].value(), "poster")
        self.assertEqual(form["background_color"].value(), "gradient")

    def test_transparent_subject_composites_over_canvas_aligned_gradient(self):
        # Left transparent, middle half opaque, right opaque. Source luminance = 102.
        photo = Image.new("RGBA", (100, 100), (102, 102, 102, 255))
        alpha = Image.new("L", photo.size, 255)
        alpha.paste(0, (0, 0, 35, 100))
        alpha.paste(128, (35, 0, 65, 100))
        photo.putalpha(alpha)
        root = Artwork.objects.create(title="Freigestellt", source=encoded(photo, "PNG"))
        # Upgrade a pre-existing cached analysis, including its old segmentation.
        stale = MotifAnalysis.objects.create(
            original=root,
            version=1,
            dimensions=[100, 100],
            faces=[],
            masks=self.analysis.masks,
            photo=self.analysis.photo,
        )
        analysis = analysis_for(root)
        self.assertEqual(analysis.pk, stale.pk)
        self.assertEqual(analysis.version, 2)
        self.assertEqual(Image.open(BytesIO(analysis.masks)).getchannel("R").tobytes(), alpha.tobytes())
        root.preparation = {
            **DEFAULTS,
            "background": 100,
            "background_color": "gradient",
            "style": "natural",
        }
        guide, ink = guide_for(root, analysis, 100)
        self.assertEqual(guide.getchannel("R").getextrema(), (255, 255))
        self.assertAlmostEqual(ink.getpixel((0, 50)), 132, delta=2)
        self.assertAlmostEqual(ink.getpixel((49, 50)), (0.12 + 0.6) * 0.5 * 255, delta=2)
        self.assertEqual(ink.getpixel((80, 50)), 153)
        root.zoom = 0.5
        _, padded = guide_for(root, analysis, 100)
        self.assertEqual(ink.getpixel((0, 50)), padded.getpixel((0, 50)))

    def test_style_alone_changes_only_pixels_in_the_subject_mask(self):
        self.art.preparation = {k: 0 for k in DEFAULTS}
        self.art.preparation.update(
            style="poster", strokes=[{"layer": "subject", "radius": 0.15, "points": [[0.5, 0.5]]}]
        )
        guide, ink = guide_for(self.art, self.analysis, 100)
        self.assertEqual(guide.getpixel((50, 50))[0], 255)
        self.assertEqual(ink.getpixel((50, 50)), 102)
        self.assertEqual(guide.getpixel((5, 5))[0], 0)
        self.assertEqual(ink.getpixel((5, 5)), 0)
