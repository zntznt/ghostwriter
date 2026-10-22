"""Belt pieces for the compact layout (tools/belts.py)."""

import unittest

from generators import factory
from tools import belts
from tools.route import E, N


class Comb(unittest.TestCase):
    TARGETS = [(12, -3, N, "c"), (4, -3, N, "a"), (8, -3, N, "b")]

    def test_a_branch_under_each_target_but_the_last_which_the_trunk_takes(self):
        place, reserve, branches = belts.comb(lambda x, y: True, (0, 0), E, self.TARGETS)
        self.assertEqual([(h, t[3]) for h, _, t in branches], [((4, -2), "a"), ((8, -2), "b"), ((8, 0), "c")])
        splitters = [(x, y) for kind, x, y, _ in place if kind == "splitter"]
        self.assertEqual(splitters, [(3, 0), (7, 0)])
        # Nothing feeds a splitter's other input.
        self.assertEqual(reserve, [(2, -1), (6, -1)])

    def test_it_doesnt_fit_through_a_taken_tile(self):
        self.assertIsNone(belts.comb(lambda x, y: (x, y) != (5, 0), (0, 0), E, self.TARGETS))


class Merge(unittest.TestCase):
    def test_rows_merge_through_stacked_splitters_with_one_output_empty(self):
        canvas, claimed, conns = factory.Canvas(), {}, []
        hs = [(0, -10), (5, -2), (9, -2)]
        trunk = belts.merge(canvas, canvas.free, hs, "transport-belt", "splitter", claimed, conns, "copper-cable")
        self.assertEqual(trunk, (0, -15))
        self.assertEqual([(c.start, c.goal) for c in conns], [((5, -2), (1, -10)), ((9, -2), (1, -13))])
        self.assertEqual([tile for name, tile, _ in canvas.items if name == "splitter"], [(0, -11), (0, -14)])
        # Each splitter's east output stays empty.
        self.assertEqual({canvas.tiles[(1, -12)], canvas.tiles[(1, -15)]},
                         {"merge copper-cable 1", "merge copper-cable 2"})

    def test_no_room_says_so(self):
        canvas = factory.Canvas()
        with self.assertRaisesRegex(belts.Blocked, "no room to merge"):
            belts.merge(canvas, lambda x, y: x == 0, [(0, -10), (5, -2)], "transport-belt", "splitter", {}, [],
                        "copper-cable")


if __name__ == "__main__":
    unittest.main()
