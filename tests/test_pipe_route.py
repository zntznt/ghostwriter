"""Pipes on a tile grid (tools/pipe_route.py): a fluid line joined through pipes and
pipe-to-ground pairs that never touches another fluid's."""

import unittest

from tools import pipes
from tools.pipe_route import Fluids, route_line
from tools.route import E, N, S, W

BOUNDS = (-20, -20, 20, 20)


def entities(fluids: Fluids, extra=()) -> dict:
    """A blueprint of everything placed, for pipes.networks."""
    got = []
    for (x, y), (name, d) in {**fluids.placed, **dict(extra)}.items():
        e = {"name": name, "position": {"x": x + 0.5, "y": y + 0.5}}
        if d:
            e["direction"] = int(d)
        got.append(e)
    return {"entities": got}


def port(fluids, fluid, tile):
    """A pipe standing for a row's port: it connects on every side."""
    fluids.fix(fluid, tile, "pipe")
    return {tile}


class PipeRoute(unittest.TestCase):
    def test_joins_its_terminals_into_one_network(self):
        f = Fluids()
        a, b, c = port(f, "water", (0, 0)), port(f, "water", (12, 5)), port(f, "water", (-6, 9))
        route_line(f, "water", [(a, ((1, 0), E)), (b, ((11, 5), W)), (c, ((-6, 8), N))],
                   lambda x, y: True, BOUNDS)
        nets = pipes.networks(entities(f))
        self.assertEqual(len({nets[(0, 0)], nets[(12, 5)], nets[(-6, 9)]}), 1)

    def test_never_touches_another_fluid(self):
        # A petroleum gas pipe right across the way: water goes round or under it, and no
        # water pipe stands where it would join the gas.
        f = Fluids()
        for y in range(-5, 6):
            f.fix("petroleum-gas", (5, y), "pipe")
        a, b = port(f, "water", (0, 0)), port(f, "water", (10, 0))
        route_line(f, "water", [(a, ((1, 0), E)), (b, ((9, 0), W))], lambda x, y: True, BOUNDS)
        nets = pipes.networks(entities(f))
        self.assertEqual(nets[(0, 0)], nets[(10, 0)])
        gas = {nets[(5, y)] for y in range(-5, 6)}
        self.assertEqual(len(gas), 1)
        self.assertNotIn(nets[(0, 0)], gas)
        self.assertEqual(f.mixes(), [])

    def test_goes_under_what_it_cant_stand_on(self):
        # A wall of belts (taken tiles): only pipe-to-ground pairs pass it.
        f = Fluids()
        wall = {(5, y) for y in range(-20, 21)}
        a, b = port(f, "water", (0, 0)), port(f, "water", (10, 0))
        route_line(f, "water", [(a, ((1, 0), E)), (b, ((9, 0), W))], lambda x, y: (x, y) not in wall, BOUNDS)
        self.assertFalse(wall & set(f.placed))
        nets = pipes.networks(entities(f))
        self.assertEqual(nets[(0, 0)], nets[(10, 0)])

    def test_pairs_dont_overlap_others_on_their_line(self):
        # The game pairs a pipe-to-ground with the first opposite one within reach: a pair
        # of another fluid's along the same row, already there, must not be overlapped.
        f = Fluids()
        f.fix("petroleum-gas", (2, 0), "pipe-to-ground", W)
        f.fix("petroleum-gas", (8, 0), "pipe-to-ground", E)
        f.spans.append(("y", 0, 2, 8))
        wall = {(5, y) for y in range(-20, 21)}
        a, b = port(f, "water", (0, 2)), port(f, "water", (10, 2))
        route_line(f, "water", [(a, ((0, 1), N)), (b, ((10, 1), N))], lambda x, y: (x, y) not in wall, BOUNDS)
        for axis, line, lo, hi in f.spans:
            for other in f.spans:
                if other != (axis, line, lo, hi) and other[:2] == (axis, line):
                    self.assertTrue(hi < other[2] or other[3] < lo, (axis, line, lo, hi, other))
        nets = pipes.networks(entities(f))
        self.assertEqual(nets[(0, 2)], nets[(10, 2)])
        self.assertNotEqual(nets[(2, 0)], nets[(0, 2)])

    def test_refuses_what_it_cant_join(self):
        f = Fluids()
        a, b = port(f, "water", (0, 0)), port(f, "water", (30, 0))
        with self.assertRaises(ValueError):
            route_line(f, "water", [(a, ((1, 0), E)), (b, ((29, 0), W))], lambda x, y: True, BOUNDS)


if __name__ == "__main__":
    unittest.main()
