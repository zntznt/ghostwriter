"""The compact factory layout (tools/compact.py): rows on shelves, belts routed between them."""

import unittest
import warnings
from collections import Counter

from generators import factory
from tests.test_tools import assert_powered, footprint
from tools import compact
from tools.check import check
from tools.route import reaches

ITEMS = [{"item": "electronic-circuit", "rate": 5}, {"item": "automation-science-pack", "rate": 1}]


def laid_out(**params):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = factory.rows_and_lines(**params)
        got = compact.build(p)
        return p, got, got.bp.to_dict()["blueprint"]


def area(bp) -> int:
    tiles = [t for e in bp["entities"] for t in footprint(e)]
    xs, ys = [x for x, _ in tiles], [y for _, y in tiles]
    return (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)


class Compact(unittest.TestCase):
    def test_checks_clean_powered_and_nothing_overlaps(self):
        for params in ITEMS:
            _, got, bp = laid_out(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)
            tiles = Counter(t for e in bp["entities"] for t in footprint(e))
            self.assertEqual([t for t, n in tiles.items() if n > 1], [], params)
            self.assertEqual(area(bp), got.area, params)

    def test_every_port_gets_what_it_takes_and_nothing_else(self):
        # Followed belt by belt from each input and each row's product: a port is reached
        # by every item it takes, and by no item it doesn't.
        for params in ITEMS:
            _, got, bp = laid_out(**params)
            tile_of = {e.id: (int(e.tile_position[0]), int(e.tile_position[1])) for e in got.bp.entities if e.id}
            sources = [(tile_of[ident], item) for ident, item in got.inputs]
            sources += [(tile, item) for kind, tile, item in got.ports if kind == "out"]
            wants = {tile: items for kind, tile, items in got.ports if kind == "in"}
            fed = {tile: set() for tile in wants}
            for tile, item in sources:
                for t in reaches(bp, tile) & wants.keys():
                    fed[t].add(item)
            self.assertEqual(fed, wants, params)

    def test_smaller_than_on_the_bus(self):
        for params in ITEMS:
            _, got, _ = laid_out(**params)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bus = factory.build(**params).to_dict()["blueprint"]
            self.assertLess(got.area, area(bus), params)

    def test_flow_test_feeds_the_bus_and_drains_the_outlets(self):
        params = ITEMS[0]
        p, got, _ = laid_out(**params)
        (flow,) = compact.flow_tests(p, got)
        ids = {e.id for e in got.bp.entities if e.id}
        self.assertLessEqual({f.entity for f in flow.feed} | set(flow.drain), ids)
        self.assertEqual({f.item for f in flow.feed}, {"iron-plate", "copper-plate"})
        self.assertEqual(flow.expect, {"electronic-circuit": 5.0})

    def test_a_search_with_no_time_left_falls_back_saying_why(self):
        # budget=: seconds for the whole search; with none left it lays nothing out.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(**ITEMS[0])
            with self.assertRaisesRegex(compact.CompactError, "budget"):
                compact.build(p, budget=0)
            bp = factory.build(**ITEMS[0], layout="compact", budget=0)
        self.assertNotIn("compact", bp.label)  # the bus layout's

    def test_a_search_keeps_to_its_budget(self):
        # The slowest request measured (about 95 s unbounded, 78 of them in negotiations
        # that never routed): bounded, it returns within its budget and a negotiation's cap.
        import time

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="electronic-circuit", rate=15)
            t = time.monotonic()
            try:
                compact.build(p, budget=3)
            except compact.CompactError:
                pass
        self.assertLess(time.monotonic() - t, 3 + compact.NEGOTIATION + 2)

    def test_fluids_fall_back_to_the_bus_saying_why(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="advanced-circuit", rate=1, oil=1)
            with self.assertRaisesRegex(compact.CompactError, "fluids"):
                compact.build(p)
            bp = factory.build(item="advanced-circuit", rate=1, oil=1, layout="compact")
        self.assertNotIn("compact", bp.label)  # the bus layout's


if __name__ == "__main__":
    unittest.main()
