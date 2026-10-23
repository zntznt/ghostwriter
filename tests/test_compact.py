"""The compact factory layout (tools/compact.py): rows on shelves, belts routed between them."""

import unittest
import warnings
from collections import Counter

from generators import factory
from tests.test_tools import assert_powered, footprint
from tools import compact, pipes
from tools.check import check
from tools.route import reaches

ITEMS = [{"item": "electronic-circuit", "rate": 5}, {"item": "automation-science-pack", "rate": 1}]
# Fluid rows fed by a supplied fluid (petroleum gas: oil isn't processed here, oil=0).
FLUID_ROWS = [{"item": "plastic-bar", "rate": 5}, {"item": "advanced-circuit", "rate": 1}]
# Fluid blocks, their lines at their west end: fluids made here, and a fluid product out at the top.
BLOCKS = [{"item": "sulfuric-acid", "rate": 10}, {"item": "lubricant", "rate": 10}]
FLUIDS = {"plastic-bar": {"petroleum-gas"}, "advanced-circuit": {"petroleum-gas"},
          "sulfuric-acid": {"water", "petroleum-gas", "sulfuric-acid"}, "lubricant": {"heavy-oil", "lubricant"}}


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
        for params in ITEMS + FLUID_ROWS + BLOCKS:
            _, got, bp = laid_out(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)
            tiles = Counter(t for e in bp["entities"] for t in footprint(e))
            self.assertEqual([t for t, n in tiles.items() if n > 1], [], params)
            self.assertEqual(area(bp), got.area, params)

    def test_every_port_gets_what_it_takes_and_nothing_else(self):
        # Followed belt by belt from each input and each row's product: a port is reached
        # by every item it takes, and by no item it doesn't. A row's product belt that also
        # carries leftovers (plastic and coal) passes a filter splitter that keeps the coal.
        for params in ITEMS + FLUID_ROWS + BLOCKS:
            _, got, bp = laid_out(**params)
            tile_of = {e.id: (int(e.tile_position[0]), int(e.tile_position[1])) for e in got.bp.entities if e.id}
            sources = [(tile_of[ident], item) for ident, item in got.inputs]
            sources += [(tile, item) for kind, tile, item in got.ports if kind == "out"]
            sources += [(tile, left) for tile, left in got.leftovers]
            wants = {tile: items for kind, tile, items in got.ports if kind == "in"}
            rows_in = {tile for tile, _ in got.leftovers}  # a row's own input belt runs on to its product port
            fed = {tile: set() for tile in wants}
            for tile, item in sources:
                for t in reaches(bp, tile, item=item) & wants.keys():
                    if t not in rows_in or item in wants[t]:
                        fed[t].add(item)
            self.assertEqual(fed, wants, params)

    def test_each_fluid_line_is_one_network_and_no_network_two_fluids(self):
        for params in FLUID_ROWS + BLOCKS:
            _, got, bp = laid_out(**params)
            nets = pipes.networks(bp)
            fluids_of = {}
            for tile, fluid in got.fluid_ports:
                self.assertIn(tile, nets, (params, tile, fluid))
                fluids_of.setdefault(nets[tile], set()).add(fluid)
            self.assertTrue(all(len(f) == 1 for f in fluids_of.values()), (params, fluids_of))
            made = Counter(f for fs in fluids_of.values() for f in fs)
            self.assertTrue(all(n == 1 for n in made.values()), (params, made))
            self.assertEqual(made.keys(), FLUIDS[params["item"]], params)

    def test_a_fluid_product_leaves_at_the_top_into_a_sink(self):
        p, got, _ = laid_out(**BLOCKS[0])
        (flow,) = compact.flow_tests(p, got)
        ids = {e.id for e in got.bp.entities if e.id}
        self.assertEqual(sorted((f.fluid, f.side, f.sink) for f in flow.fluids),
                         [("petroleum-gas", "south", False), ("sulfuric-acid", "north", True),
                          ("water", "south", False)])
        self.assertLessEqual({f.entity for f in flow.fluids}, ids)
        self.assertEqual(flow.expect, {"sulfuric-acid": 10.0})

    def test_smaller_than_on_the_bus(self):
        for params in ITEMS + FLUID_ROWS + BLOCKS:
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
        # A supplied fluid enters at the bottom, from the south of its input pipe.
        p, got, _ = laid_out(**FLUID_ROWS[0])
        (flow,) = compact.flow_tests(p, got)
        ids = {e.id for e in got.bp.entities if e.id}
        self.assertEqual([(f.fluid, f.side, f.sink) for f in flow.fluids], [("petroleum-gas", "south", False)])
        self.assertLessEqual({f.entity for f in flow.fluids}, ids)

    def test_the_bus_ends_at_each_lines_last_tap_and_shelves_above_take_its_columns(self):
        # A raw line ends where its last channel taps it; lines reaching further north sit
        # further west, so a shelf above a line's last tap starts in its columns (and a
        # shelf never stands on a line still running beside it).
        moved = False
        for params in ITEMS:
            _, got, _ = laid_out(**params)
            norths = [north for _, _, north in got.bus]
            self.assertEqual(norths, sorted(norths), params)  # westmost reaches furthest
            for x0, south, north in got.shelves:
                running = [c for _, c, end in got.bus if end <= south]  # still running at the shelf's foot
                self.assertTrue(all(c + 1 < x0 for c in running), (params, x0, running))
                moved |= x0 < 2 * len(got.bus) + 2
        self.assertTrue(moved)  # automation science's gear shelf starts above iron's last tap

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

    def test_cracking_falls_back_to_the_bus_saying_why(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="advanced-circuit", rate=1, oil=1)
            with self.assertRaisesRegex(compact.CompactError, "cracking"):
                compact.build(p)
            bp = factory.build(item="advanced-circuit", rate=1, oil=1, layout="compact")
        self.assertNotIn("compact", bp.label)  # the bus layout's


if __name__ == "__main__":
    unittest.main()
