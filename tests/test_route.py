"""Belt routing on a tile grid (tools/route.py), and tracing where belts carry items."""

import unittest

from draftsman.constants import Direction

from tools.route import Work, reaches, route, route_all

N, E, S, W = Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST
BOX = (0, 0, 9, 9)


class Route(unittest.TestCase):
    def test_a_straight_run(self):
        path = route(lambda x, y: True, (2, 8), N, (2, 3), N, reach=5, bounds=BOX)
        self.assertEqual([(k, x, y, d) for k, x, y, d, *_ in path], [("belt", 2, y, N) for y in range(8, 2, -1)])

    def test_it_turns_to_reach_a_goal_beside_it(self):
        path = route(lambda x, y: True, (2, 8), N, (6, 4), E, reach=5, bounds=BOX)
        self.assertEqual(path[-1][:4], ("belt", 6, 4, E))
        self.assertEqual(len(path), 9)  # 4 up and 4 across, turning once
        for (_, x, y, d, *_), (_, nx, ny, *_) in zip(path, path[1:]):
            self.assertEqual((nx - x, ny - y), {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}[d])

    def test_it_goes_under_a_wall(self):
        wall = {(x, 5) for x in range(10)}
        path = route(lambda x, y: (x, y) not in wall, (3, 8), N, (3, 2), N, reach=5, bounds=BOX)
        kinds = [(k, x, y) for k, x, y, *_ in path]
        self.assertIn(("in", 3, 6), kinds)
        self.assertIn(("out", 3, 4), kinds)
        self.assertFalse(any((x, y) in wall for _, x, y, *_ in path))

    def test_a_wall_wider_than_its_reach_stops_it(self):
        wall = {(x, y) for x in range(10) for y in range(2, 8)}
        self.assertIsNone(route(lambda x, y: (x, y) not in wall, (3, 9), N, (3, 0), N, reach=5, bounds=BOX))

    def test_its_tunnels_keep_off_others_on_the_same_line(self):
        # An entrance pairs with the nearest exit ahead: a new pair may not overlap one.
        wall = {(x, 5) for x in range(10)}
        tunnels = [(3, 7, 3, 3)]  # already a pair in column 3 from row 7 to row 3
        path = route(lambda x, y: (x, y) not in wall and (x, y) not in {(3, 7), (3, 3)}, (3, 9), N, (3, 0), N,
                     reach=5, bounds=BOX, tunnels=tunnels)
        self.assertIsNotNone(path)
        self.assertFalse(any(k != "belt" and x == 3 for k, x, *_ in path), path)

    def test_the_goal_is_never_entered_from_its_front(self):
        # The goal faces south, and the start is south of it: it must come round.
        path = route(lambda x, y: True, (4, 8), N, (4, 3), S, reach=5, bounds=BOX)
        self.assertIsNotNone(path)
        self.assertEqual(path[-1][:4], ("belt", 4, 3, S))
        self.assertNotEqual(path[-2][1:3], (4, 4))


class RouteAll(unittest.TestCase):
    def test_crossing_belts_share_no_tile(self):
        # Two belts across and one up through both: someone goes under.
        conns = [((0, 3), E, (9, 3), E), ((0, 5), E, (9, 5), E), ((4, 9), N, (4, 0), N)]
        paths = route_all([(s, h, g, f, lambda x, y: True) for s, h, g, f in conns], lambda x, y: True,
                          reach=5, bounds=BOX)
        self.assertIsNotNone(paths)
        tiles = [(x, y) for p in paths for _, x, y, _ in p]
        self.assertEqual(len(tiles), len(set(tiles)))
        for p, (_, _, goal, facing) in zip(paths, conns):
            self.assertEqual(p[-1][1:], (*goal, facing))

    def test_a_start_walled_in_by_a_greedy_first_route_is_freed(self):
        # Routed alone first, the long belt hugs the wall and takes the tile the
        # short one must start through; negotiating, it moves over.
        wall = {(x, 4) for x in range(1, 10)} | {(0, y) for y in range(0, 4)}
        free = lambda x, y: (x, y) not in wall  # noqa: E731
        conns = [((1, 3), E, (9, 0), N, lambda x, y: True), ((1, 2), N, (1, 0), N, lambda x, y: True)]
        paths = route_all(conns, free, reach=1, bounds=BOX)  # no undergrounds: reach 1
        self.assertIsNotNone(paths)
        tiles = [(x, y) for p in paths for _, x, y, _ in p]
        self.assertEqual(len(tiles), len(set(tiles)))

    def test_a_negotiation_is_bounded_by_steps_not_seconds(self):
        # The crossing above routes, but not once its work is spent: A* steps, which are
        # the same on any machine, so the same negotiation always ends the same way.
        conns = [((0, 3), E, (9, 3), E), ((0, 5), E, (9, 5), E), ((4, 9), N, (4, 0), N)]
        spec = [(s, h, g, f, lambda x, y: True) for s, h, g, f in conns]
        self.assertIsNone(route_all(spec, lambda x, y: True, reach=5, bounds=BOX, work=Work(0)))
        spent = []
        for _ in range(2):
            work = Work(10 ** 6)
            self.assertIsNotNone(route_all(spec, lambda x, y: True, reach=5, bounds=BOX, work=work))
            spent.append(work.spent)
        self.assertGreater(spent[0], 0)
        self.assertEqual(spent[0], spent[1])

    def test_work_parts_spend_the_whole_and_stop_with_it(self):
        whole = Work(100)
        part = whole.part(30)
        part.spend(20)
        self.assertEqual((whole.spent, whole.left, part.left), (20, 80, 10))
        part.spend(15)
        self.assertTrue(part.over())
        self.assertFalse(whole.over())
        other = whole.part(1000)
        other.spend(70)
        self.assertTrue(whole.over() and other.over())  # the whole spent, so its parts too
        # Seconds only as a safety: a search out of time stops whatever work it has left.
        self.assertTrue(Work(10 ** 6, seconds=0).over())
        self.assertTrue(Work(10 ** 6, seconds=0).part(10).over())
        self.assertFalse(Work(10 ** 6, seconds=60).over())


class Reaches(unittest.TestCase):
    def entity(self, name, x, y, d, **kw):
        w, h = (2, 1) if name == "splitter" and d in (N, S) else (1, 2) if name == "splitter" else (1, 1)
        return {"name": name, "position": {"x": x + w / 2, "y": y + h / 2}, "direction": d, **kw}

    def test_belts_undergrounds_and_splitters(self):
        ents = [self.entity("transport-belt", 2, 9, N), self.entity("underground-belt", 2, 8, N, type="input"),
                self.entity("underground-belt", 2, 5, N, type="output"), self.entity("splitter", 2, 4, N),
                self.entity("transport-belt", 2, 3, N), self.entity("transport-belt", 3, 3, E),
                self.entity("transport-belt", 4, 3, N), self.entity("transport-belt", 7, 9, N)]
        got = reaches({"entities": ents}, (2, 9))
        self.assertLessEqual({(2, 3), (3, 3), (4, 3), (4, 2)}, got)
        self.assertNotIn((7, 9), got)
        self.assertNotIn((2, 7), got)  # under the ground

    def test_a_side_load_carries_on_but_a_belt_facing_away_does_not(self):
        ents = [self.entity("transport-belt", 1, 5, E), self.entity("transport-belt", 2, 5, N),
                self.entity("transport-belt", 3, 5, E), self.entity("transport-belt", 4, 5, E)]
        got = reaches({"entities": ents}, (1, 5))
        self.assertIn((2, 4), got)  # onto the belt beside it, and on north
        self.assertNotIn((3, 5), got)

    def test_a_filter_splitter_sends_its_item_one_way_and_the_rest_the_other(self):
        # Filtered to plastic, priority left: plastic leaves by the left output only, coal by the right.
        ents = [self.entity("transport-belt", 2, 6, N),
                self.entity("splitter", 2, 5, N, filter={"type": "item", "name": "plastic-bar"},
                            output_priority="left"),
                self.entity("transport-belt", 2, 4, N), self.entity("transport-belt", 3, 4, N)]
        plastic = reaches({"entities": ents}, (2, 6), item="plastic-bar")
        coal = reaches({"entities": ents}, (2, 6), item="coal")
        self.assertIn((2, 4), plastic)
        self.assertNotIn((3, 4), plastic)
        self.assertIn((3, 4), coal)
        self.assertNotIn((2, 4), coal)
        self.assertLessEqual({(2, 4), (3, 4)}, reaches({"entities": ents}, (2, 6)))  # any item: both ways


if __name__ == "__main__":
    unittest.main()
