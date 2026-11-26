from collections import Counter
from itertools import pairwise

from django.test import SimpleTestCase

from .handcraft import improve_winding_order
from .nail_clearance import placement_report


class HandcraftGeometryTests(SimpleTestCase):
    def test_closed_subpath_removes_an_immediate_return(self):
        path = [0, 1, 0, 2, 1, 3]
        def chords(p):
            return Counter(tuple(sorted(edge)) for edge in pairwise(p))
        route = {"path": path.copy(), "segments": [
            {"from": a, "to": b, "length_mm": 20 + a + b} for a, b in pairwise(path)
        ]}
        improve_winding_order(route)
        self.assertEqual(route["winding_order"]["after"][0], 0)
        self.assertEqual(chords(route["path"]), chords(path))
        self.assertEqual((route["path"][0], route["path"][-1]), (0, 3))
        self.assertEqual(route["segments"], [
            {"from": a, "to": b, "length_mm": 20 + a + b} for a, b in pairwise(route["path"])
        ])

    def test_position_error_bound_counts_each_traversal(self):
        route = {"path": [0, 1, 0], "anchors": [
            {"x_mm": x, "y_mm": y} for x, y in ((0, 0), (20, 0), (10, 2))
        ], "settings": {"shaft_mm": 1.2, "thread_mm": .2}}
        report = placement_report(route)
        self.assertAlmostEqual(report["min_shaft_margin_mm"], .6)
        self.assertEqual(report["uncertain_steps_by_radial_error_mm"]["0.15"], 0)
        self.assertEqual(report["uncertain_steps_by_radial_error_mm"]["0.5"], 2)

    def test_winding_direction_is_defined_for_each_visit(self):
        from .nail_clearance import plan_winding_sides
        route = {"path": [0, 1, 2, 0], "anchors": [
            {"x_mm": x, "y_mm": y} for x, y in ((0, 0), (40, 0), (20, 35))
        ], "settings": {"shaft_mm": 1.2, "thread_mm": .2}}
        plan_winding_sides(route)
        self.assertEqual(len(route["winding_sides"]), len(route["path"]))
        self.assertTrue(set(route["winding_sides"]) <= {"cw", "ccw"})
        self.assertEqual(route["winding_clearance"]["uncertified_steps"], 0)

    def test_tangent_renderer_preserves_highlights_and_depends_on_winding_side(self):
        import numpy as np
        from .physical import simulation, png, assess
        route = {"path": [0,1], "anchors":[{"x_mm":-15,"y_mm":0},{"x_mm":15,"y_mm":0}],
                 "settings":{"shaft_mm":1.2,"thread_mm":.2,"rectangle_mm":[40,20],"opacity":.85},
                 "winding_sides":["cw","cw"]}
        upper = simulation(route, 401)
        route["winding_sides"] = ["ccw","ccw"]
        lower = simulation(route, 401)
        self.assertTrue(np.all(upper[:60] == 255))
        self.assertLess(np.argmin(upper[:,200]), np.argmin(lower[:,200]))
        self.assertGreater(assess(png(upper), upper)["score"], assess(png(upper), lower)["score"])

    def test_effort_slices_and_legacy_days(self):
        from types import SimpleNamespace
        from .winding_study import day_ends
        from .models import Calendar
        import numpy as np
        costs = np.repeat([1., 3.], 120)
        ends = day_ends(costs)
        gift = SimpleNamespace(artwork=SimpleNamespace(count=240, route={"day_ends":ends}))
        bounds = [Calendar.bounds(gift, i) for i in range(1,25)]
        self.assertEqual(bounds[0][0],0)
        self.assertEqual(bounds[-1][1],240)
        self.assertTrue(all(b==c for (_,b),(c,_) in zip(bounds,bounds[1:])))
        self.assertLess(max(sum(costs[a:b]) for a,b in bounds),30)
        gift.artwork.route = {}
        self.assertEqual(Calendar.bounds(gift,1),(0,10))

    def test_facial_guard_checks_each_material_separately(self):
        from .winding_study import protected
        base = {"score":.5,"white_ink":.03,"left_eye":.8}
        profiles = [base]*3
        self.assertFalse(protected([base,base,{**base,"score":.6,"left_eye":.79}],profiles,[("left_eye",())]))
