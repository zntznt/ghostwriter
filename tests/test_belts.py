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

    def test_under_another_taps_trunk_a_branch_runs_beside_it_from_its_splitter(self):
        # Two rows north, another tap's trunk: the branch's route starts on the splitter's
        # north output, items moving on east, and runs beside the trunk to get north of it.
        trunk = lambda x, y: y == -2 and x < 6  # noqa: E731
        place, _, branches = belts.comb(lambda x, y: not trunk(x, y), (0, 0), E, self.TARGETS, beside=trunk)
        self.assertEqual([(h, d, t[3]) for h, d, t in branches], [((4, -1), E, "a"), ((8, -2), N, "b"),
                                                                     ((8, 0), E, "c")])
        self.assertNotIn(("belt", 4, -1, N), place)

    def test_it_doesnt_fit_through_a_taken_tile(self):
        self.assertIsNone(belts.comb(lambda x, y: (x, y) != (5, 0), (0, 0), E, self.TARGETS))


class Adapter(unittest.TestCase):
    def test_a_port_taking_one_lane_is_side_loaded_from_that_side_with_a_stub_behind(self):
        # A fluid row's product belt carries coal on its near lane only: fed from below, the
        # coal would take both lanes and block the product. From the side, with a stub
        # belt pointing in from behind, it goes onto that lane alone (NOTES.md).
        for lane, side, facing in (("right", (6, -7), belts.W), ("left", (4, -7), E)):
            canvas, claimed, line = factory.Canvas(), {}, object()
            got = belts.adapter(canvas, canvas.free, 5, -8, -1, {lane: line}, "transport-belt", claimed, "row")
            self.assertEqual(got, [(line, (*side, facing, f"row {lane}"))], lane)
            placed = {tile: kw["direction"] for name, tile, kw in canvas.items}
            self.assertEqual(placed, {(5, -7): N, (5, -6): N}, lane)  # (5, -6) the stub

    def test_a_port_taking_both_lanes_of_one_line_is_fed_from_below(self):
        canvas, claimed, line = factory.Canvas(), {}, object()
        got = belts.adapter(canvas, canvas.free, 5, -8, -1, {"left": line, "right": line}, "transport-belt",
                            claimed, "row")
        self.assertEqual(got, [(line, (5, -7, N, "row"))])

    def test_a_port_beside_another_taking_two_lines_is_fed_round_from_beside_the_row(self):
        # Two such ports side by side can't both have a belt below fed from both sides: each
        # needs a tile under the other. The outer one turns in from a belt fed from both
        # sides beside it, its lanes kept through the turn: heading east, the north side is
        # the left lane; heading west, the south side.
        for turn, tee, heading, left, right in ((belts.W, (4, -7), E, (4, -8, belts.S), (4, -6, N)),
                                                (E, (6, -7), belts.W, (6, -6, N), (6, -8, belts.S))):
            canvas, claimed, a, b = factory.Canvas(), {}, object(), object()
            got = belts.adapter(canvas, canvas.free, 5, -8, -1, {"left": a, "right": b}, "transport-belt", claimed,
                                "row", turn=turn)
            self.assertEqual(got, [(a, (*left, "row left")), (b, (*right, "row right"))], turn)
            placed = {tile: kw["direction"] for name, tile, kw in canvas.items}
            self.assertEqual(placed, {(5, -7): N, tee: heading}, turn)


class Split(unittest.TestCase):
    def test_a_short_rows_comb_leads_up_past_a_taller_row_beside_it(self):
        # Utility science's plastic row (4 tiles) stands beside 19-tile circuit cells on its
        # shelf, the bus west of it: a comb a few tiles above its output can't pass them.
        canvas, conns = factory.Canvas(), []
        free = lambda x, y: canvas.free(x, y) and x >= 10 and not (x == 11 and -19 <= y <= 0)  # noqa: E731
        targets = [(14, -30, N, "a"), (18, -30, N, "b")]
        belts.split(canvas, free, (10, -1), targets, "transport-belt", "splitter", conns, "plastic-bar")
        self.assertEqual(sorted(c.goal for c in conns), [(14, -30), (18, -30)])
        self.assertTrue(all(c.start[1] < -19 for c in conns))


class Tap(unittest.TestCase):
    # A bus of two lines: iron at column 0 (its branch already across the bus at row -6, its
    # comb east of the yard) and copper at column 2, tapped next; the yard is columns 4 and 5.
    def tap(self, last, taken=(), close=True):
        canvas, conns, taps = factory.Canvas(), [], belts.Taps(rows=[(-6, 0)], close=close)
        for x in range(1, 5):
            canvas.take(x, -6, "tap iron-plate 0")
        yb = belts.tap(canvas, lambda x, y: canvas.free(x, y) and y not in taken, taps, list(range(-1, -11, -1)),
                       2, 4, 5, last, [(9, -12, N, "row 0 port 9")], "transport-belt", "splitter", conns,
                       "copper-plate")
        return yb, taps

    def test_south_of_a_western_lines_branch_it_takes_the_next_row(self):
        # It dives under the branch from its splitter's output: no rows between.
        yb, taps = self.tap(last=False, taken=(-10, -9))
        self.assertEqual(yb, -5)
        self.assertEqual(taps.rows, [(-6, 0), (-5, 2)])

    def test_north_of_one_it_leaves_room_to_come_up_between(self):
        # Its line comes up out of the tunnel north of the branch, then into its splitter (or
        # turns, at its last use): three rows north, or two.
        self.assertEqual(self.tap(last=False, taken=(-10,))[0], -9)
        self.assertEqual(self.tap(last=True, taken=(-10, -9))[0], -8)

    def test_unless_close_three_rows_from_any_other(self):
        self.assertEqual(self.tap(last=False, taken=(-10, -9), close=False)[0], -3)


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
