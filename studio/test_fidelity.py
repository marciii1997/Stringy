from collections import Counter
from itertools import pairwise
from types import SimpleNamespace
from unittest import TestCase

import numpy as np

from .fidelity import add_loops, insert_visits
from .nail_clearance import plan_winding_sides


class WindingFidelityTests(TestCase):
    def test_reverse_travel_preserves_the_physical_tangent(self):
        original = {
            "anchors": [{"x_mm": x, "y_mm": y} for x, y in ((0, 0), (40, 0), (20, 30))],
            "settings": {"shaft_mm": 1.2, "thread_mm": 0.2},
            "path": [0, 1, 2, 0],
            "winding_sides": ["cw", "ccw", "ccw", "cw"],
        }
        reversed_route = {**original, "path": original["path"][::-1]}
        plan_winding_sides(reversed_route, original)
        self.assertEqual(reversed_route["winding_sides"], ["ccw", "cw", "cw", "ccw"])
        self.assertEqual(reversed_route["winding_clearance"]["uncertified_steps"], 0)

    def test_insertions_obey_chord_and_nail_limits(self):
        legal = ~np.eye(12, dtype=bool)
        search = SimpleNamespace(legal=legal, center_gap=legal * 8.0, limit=2)
        route = {"path": list(range(12)), "settings": {"max_nail_load": 8}}
        rng = np.random.default_rng(58)
        for operation in (insert_visits, add_loops):
            before = route["path"]
            after = operation(search, route, legal.astype(float), 24, rng)
            self.assertEqual((after[0], after[-1]), (before[0], before[-1]))
            self.assertGreater(len(after), len(before))
            self.assertLessEqual(len(after), len(before) + 24)
            chords = Counter(tuple(sorted(edge)) for edge in pairwise(after))
            self.assertTrue(all(a != b and uses <= search.limit for (a, b), uses in chords.items()))
            load = Counter(after[1:]) + Counter(after[:-1])
            self.assertLessEqual(max(load.values()), route["settings"]["max_nail_load"])
            route["path"] = after
