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
