"""The compact factory layout (tools/compact.py): rows on shelves, belts routed between them."""

import re
import unittest
import warnings
from collections import Counter
from functools import cache

from generators import factory
from tests.test_tools import assert_powered, footprint
from tools import compact, pipes, rates
from tools.check import check
from tools.route import E, N, W, Work, reaches
from tools.shelves import MIN_CHANNEL, restore, shelve, widths

ITEMS = [{"item": "electronic-circuit", "rate": 5}, {"item": "automation-science-pack", "rate": 1}]
# Fluid rows fed by a supplied fluid (petroleum gas: oil isn't processed here, oil=0).
FLUID_ROWS = [{"item": "plastic-bar", "rate": 5}, {"item": "advanced-circuit", "rate": 1}]
# Fluid blocks, their lines at their west end: fluids made here, and a fluid product out at the top.
BLOCKS = [{"item": "sulfuric-acid", "rate": 10}, {"item": "lubricant", "rate": 10}]
# Oil processed here, cracking through a gate: a pump that runs while a tank on the oil line holds some.
OIL = [{"item": "advanced-circuit", "rate": 1, "oil": 1}]
# Shared products fused (production.fuse(shared=True)): cable into circuit cells while a cable
# row feeds the advanced circuits, and gears into both inserter and belt cells.
SHARED = [{"item": "advanced-circuit", "rate": 1, "cells": 2}, {"item": "logistic-science-pack", "rate": 1, "cells": 2}]
FLUIDS = {"plastic-bar": {"petroleum-gas"}, "advanced-circuit": {"petroleum-gas"},
          "sulfuric-acid": {"water", "petroleum-gas", "sulfuric-acid"}, "lubricant": {"heavy-oil", "lubricant"}}
OIL_FLUIDS = {"water", "crude-oil", "heavy-oil", "light-oil", "petroleum-gas"}
# Two ports side by side, each taking two lines: the robot frame row's corners.
ROBOT_FRAMES = {"item": "flying-robot-frame", "rate": 0.2}
# Two supplied items on one line's two lanes: circuit cells take copper on the left lane
# and iron on the right, 3.75 and 2.5 a second a port, so both ports fit one yellow belt.
LANES = {"item": "electronic-circuit", "rate": 5, "cells": 1}


def laid_out(**params):
    return _laid_out(tuple(sorted(params.items())))


@cache  # each request laid out once for all the tests here
def _laid_out(params):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = factory.rows_and_lines(**dict(params))
        got = compact.build(p)
        return p, got, got.bp.to_dict()["blueprint"]


def area(bp) -> int:
    tiles = [t for e in bp["entities"] for t in footprint(e)]
    xs, ys = [x for x, _ in tiles], [y for _, y in tiles]
    return (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)


class Compact(unittest.TestCase):
    def test_shared_cells_keep_a_row_for_the_rest(self):
        # The cells take their consumers' share; the cable row is still there for the advanced
        # circuits, and the gear row is gone in green science.
        steps = {s.item: s for s in laid_out(**SHARED[0])[0]["steps"]}
        self.assertEqual(steps["electronic-circuit"].generator, "cell")
        self.assertEqual(steps["copper-cable"].generator, "assembly_row")
        steps = {s.item: s for s in laid_out(**SHARED[1])[0]["steps"]}
        self.assertNotIn("iron-gear-wheel", steps)
        self.assertEqual({steps[i].generator for i in ("inserter", "transport-belt")}, {"cell"})

    def test_checks_clean_powered_and_nothing_overlaps(self):
        for params in ITEMS + FLUID_ROWS + BLOCKS + OIL + SHARED + [LANES]:
            _, got, bp = laid_out(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)
            tiles = Counter(t for e in bp["entities"] for t in footprint(e))
            self.assertEqual([t for t, n in tiles.items() if n > 1], [], params)
            self.assertEqual(area(bp), got.area, params)

    def test_two_ports_side_by_side_taking_two_lines_each_lay_out(self):
        # A row on both sides of its machines (flying robot frames' four ingredients) has
        # two such ports at each corner; they fell back to the bus, as did everything
        # made from them (utility science, construction robots).
        _, got, bp = laid_out(**ROBOT_FRAMES)
        self.assertEqual(check({"blueprint": bp}), [])
        assert_powered(self, bp, ROBOT_FRAMES)
        tiles = Counter(t for e in bp["entities"] for t in footprint(e))
        self.assertEqual([t for t, n in tiles.items() if n > 1], [])
        self.assertEqual(area(bp), got.area)

    def test_two_supplied_items_one_a_lane_ride_one_line(self):
        # Both cells' copper (7.5 a second) and iron (5) fit one yellow belt's two lanes: one
        # line instead of two, so one bus column and one tap a shelf instead of two.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(**LANES)
        q = compact._shared(p)
        restore(q)
        raw = [ln for named in q["lines"].values() for ln in named if ln.supplied and not ln.fluid]
        self.assertEqual([ln.lanes for ln in raw], [{"left": ("copper-plate", 7.5), "right": ("iron-plate", 5.0)}])
        cells = [port for r in q["rows"] for port in r.ports if port.kind == "in"]
        self.assertEqual([set(map(id, port.feeds.values())) for port in cells], [{id(raw[0])}] * 2)
        restore(p)  # p's own lines stay as factory planned them
        self.assertEqual(sorted(ln.name for named in p["lines"].values() for ln in named if ln.supplied),
                         ["copper-plate", "iron-plate"])

    def test_lines_share_lanes_only_where_each_item_fits_one_and_lines_get_fewer(self):
        # At 15/s a cell port takes 5.6 copper a second: one to a lane, which would take four
        # lines where three carry it now; on express belts the same. From ore, coal shares a
        # line with each ore.
        cases = [LANES, {**LANES, "rate": 15}, {**LANES, "rate": 20, "belt": "express-transport-belt"},
                 {**LANES, "smelt": 1}, {"item": "military-science-pack", "rate": 0.5, "cells": 1}]
        for params in cases:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p = factory.rows_and_lines(**params)
            q = compact._shared(p)
            if params in cases[1:3]:
                self.assertIsNone(q, params)
                continue
            self.assertIsNotNone(q, params)

            def raw(x):
                return [ln for named in x["lines"].values() for ln in named if ln.supplied and not ln.fluid]

            self.assertLess(len(raw(q)), len(raw(p)), params)
            lane = rates.lane_rate(p["belt"])
            restore(q)
            for r in q["rows"]:
                for port in r.ports:
                    for side, item in port.lanes.items() if port.kind == "in" else ():
                        line = port.feeds[side]
                        if line.lanes:  # a shared line feeds only ports taking its items on its lanes
                            self.assertEqual(port.lanes, {s: i for s, (i, _) in line.lanes.items()}, params)
                        else:
                            self.assertEqual(line.name, item, params)
            for line in raw(q):
                self.assertTrue(all(d <= lane + 1e-9 for _, d in line.lanes.values()), (params, line.lanes))
            restore(p)

    def test_a_shared_line_enters_by_a_belt_onto_each_lane_and_taps_once(self):
        # Its first belt, side-loaded from both sides (NOTES.md): copper from the west onto
        # the left lane, iron from the east onto the right. The two inputs stay plain belts.
        _, got, _ = laid_out(**LANES)
        self.assertEqual([name for name, _, _ in got.bus], ["copper-plate+iron-plate"])
        (_, column, _), = got.bus
        at = {(int(e.tile_position[0]), int(e.tile_position[1])): e for e in got.bp.entities}
        tile_of = {item: next(t for t, e in at.items() if e.id == ident) for ident, item in got.inputs}
        self.assertEqual(tile_of, {"copper-plate": (column - 1, 0), "iron-plate": (column + 1, 0)})
        self.assertEqual([at[(column + dx, 0)].direction for dx in (-1, 0, 1)], [E, N, W])

    def test_merges_and_splits_keep_east_of_the_bus(self):
        # A comb laid west into the bus's columns boxed in its own branches, and the bus
        # couldn't run there either: utility science's plastic, whose comb must instead lead
        # north past the taller rows beside it (belts.split) and go east.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="utility-science-pack", rate=0.2, cells=2)
            compact._gated(p)
            shelves = next(s for s in sorted({tuple(map(tuple, shelve(p, w))) for w in widths(p["rows"])})
                           if len(s) == 7)
            got = compact._attempt(p, shelves, [MIN_CHANNEL + 9] * (len(shelves) + 1), False,
                                   Work(compact.NEGOTIATION))  # Blocked, before
        self.assertEqual(check({"blueprint": got.bp.to_dict()["blueprint"]}), [])

    def test_every_port_gets_what_it_takes_and_nothing_else(self):
        # Followed belt by belt from each input and each row's product: a port is reached
        # by every item it takes, and by no item it doesn't. A row's product belt that also
        # carries leftovers (plastic and coal) passes a filter splitter that keeps the coal.
        for params in ITEMS + FLUID_ROWS + BLOCKS + SHARED + [ROBOT_FRAMES, LANES]:
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
        for params in FLUID_ROWS + BLOCKS + OIL:
            _, got, bp = laid_out(**params)
            nets = pipes.networks(bp)
            fluids_of = {}
            for tile, fluid in got.fluid_ports:
                self.assertIn(tile, nets, (params, tile, fluid))
                fluids_of.setdefault(nets[tile], set()).add(fluid)
            self.assertTrue(all(len(f) == 1 for f in fluids_of.values()), (params, fluids_of))
            made = Counter(f for fs in fluids_of.values() for f in fs)
            self.assertTrue(all(n == 1 for n in made.values()), (params, made))
            self.assertEqual(made.keys(), OIL_FLUIDS if params.get("oil") else FLUIDS[params["item"]], params)

    def test_cracking_takes_its_oil_through_a_gate(self):
        # Each cracking block's oil comes through a pump wired to a tank on the oil line, set to
        # run while the tank holds more than the bus layout's gate level (factory.GATE_LEVEL).
        _, got, bp = laid_out(**OIL[0])
        number = {e["entity_number"]: e for e in bp["entities"]}
        pumps = [e for e in bp["entities"] if e["name"] == "pump"]
        self.assertEqual(len(pumps), 2)  # heavy oil and light oil cracking
        for pump in pumps:
            cond = pump["control_behavior"]["circuit_condition"]
            self.assertEqual((cond["comparator"], cond["constant"]), (">", factory.GATE_LEVEL))
            self.assertTrue(pump["control_behavior"].get("circuit_enabled"))
            wired = {number[a]["name"] for a, _, b, _ in bp["wires"] if pump["entity_number"] in (a, b)} | \
                {number[b]["name"] for a, _, b, _ in bp["wires"] if pump["entity_number"] in (a, b)}
            self.assertIn("storage-tank", wired)

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
                bus = factory.build(**params, layout="bus").to_dict()["blueprint"]
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
                compact.build(p, Work(compact.STEPS, seconds=0))
            bp = factory.build(**ITEMS[0], layout="compact", budget=0)
        self.assertNotIn("compact", bp.label)  # the bus layout's

    def test_a_slower_machine_lays_out_the_same(self):
        # The search is bounded by A* steps, not seconds, so a machine a thousand times
        # slower (a clock a thousand times faster) gives the same blueprint: its seconds
        # are only a safety, here out of reach. Cut by seconds, negotiations ended early.
        import time
        from unittest import mock

        def blueprint():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p = factory.rows_and_lines(**ITEMS[1])
                return compact.build(p, Work(compact.STEPS, seconds=10 ** 9)).bp.to_string()

        real = time.monotonic
        start = real()
        fast = blueprint()
        with mock.patch("time.monotonic", lambda: start + (real() - start) * 1000):
            slow = blueprint()
        self.assertEqual(slow, fast)

    def test_compact_is_the_default_and_the_bus_still_there(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertIn("compact", factory.build(**ITEMS[1]).label)
            self.assertNotIn("compact", factory.build(**ITEMS[1], layout="bus").label)
            (flow,) = factory.flow_tests(**ITEMS[1])
        self.assertTrue(flow.name.startswith("compact"))

    def test_the_bus_layout_is_kept_when_it_is_smaller(self):
        # As for processing units, whose tallest row sets both boxes: a compact layout no
        # smaller than the bus's isn't kept, and factory says so.
        import contextlib
        import io
        from dataclasses import replace
        from unittest import mock

        real, err = compact.build, io.StringIO()
        with mock.patch.object(compact, "build", lambda p, work, beat: replace(real(p, work, beat), area=10 ** 9)), \
                contextlib.redirect_stderr(err), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp = factory.build(**ITEMS[1], layout="compact", budget=39)  # a budget no other test asks for
        self.assertNotIn("compact", bp.label)
        self.assertIn("the bus layout is smaller", err.getvalue())

    def test_a_search_cut_by_steps_lays_out_the_same_on_any_machine(self):
        # Recorded on macOS, checked on CI's Linux: 200,000 steps stop electric engines' rows
        # as solved partway (their whole search takes 360,050), as compact.STEPS stops the
        # biggest requests.
        import hashlib

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="electric-engine-unit", rate=0.5, cells=0)
            work = Work(200_000, seconds=10 ** 9)
            got = compact.build(p, work)
        self.assertEqual((got.area, hashlib.sha1(got.bp.to_string().encode()).hexdigest()[:12]),
                         (1914, "a6eaeb376533"))
        self.assertLess(work.spent, 360_050)

    def test_the_last_trim_takes_channels_thinner_with_taps_closer_together(self):
        # Circuits at 5/s: iron and copper tapped into channel 0, each with a comb. Closer,
        # copper's tap sits two rows south of iron's trunk, its branches running beside it:
        # the channel is a row thinner (600 tiles with taps three rows apart). Each on a line
        # of its own here: on one line's two lanes they take one tap (486 tiles, LANES).
        from unittest import mock

        with mock.patch.object(compact, "_shared", lambda p: None), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            got = compact.build(factory.rows_and_lines(**LANES))
        self.assertEqual(got.area, 580)

    def test_a_bound_from_an_earlier_layout_loses_no_smaller_one(self):
        # beat skips only shelves whose rows and thinnest channels already take that much.
        # Held to it too, channels grown until the belts route lost electric engines' 1,540
        # tiles to the 1,587 of the rows sized for research.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="electric-engine-unit", rate=0.5, cells=0)
            self.assertEqual(compact.build(p, beat=1587).area, 1540)
            with self.assertRaisesRegex(compact.CompactError, "no layout smaller than 100 tiles"):
                compact.build(factory.rows_and_lines(**ITEMS[0]), beat=100)

    def test_a_search_keeps_to_its_seconds(self):
        # The safety: out of seconds, a search stops whatever steps it has left, a
        # negotiation too.
        import time

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="electronic-circuit", rate=15)
            t = time.monotonic()
            try:
                compact.build(p, Work(compact.STEPS, seconds=3))
            except compact.CompactError:
                pass
        self.assertLess(time.monotonic() - t, 3 + 2)

    def test_no_layout_is_smaller_than_its_lower_bound(self):
        # lower_bound: the rows' own tiles bound any layout of them at all, the shelves' any
        # layout of them this search can make; neither is ever above the area laid out.
        for params in ITEMS + FLUID_ROWS + BLOCKS + OIL + [LANES]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bound = compact.lower_bound(factory.rows_and_lines(**params))
            _, got, bp = laid_out(**params)
            self.assertEqual(bound["shelves"], bound["wide"] * bound["tall"], params)
            self.assertLessEqual(bound["rows"], bound["shelves"], params)
            self.assertLessEqual(bound["shelves"], got.area, params)
            self.assertEqual(got.area, area(bp), params)

    def test_the_shelf_bound_is_met_where_nothing_routes_past_the_least_channels(self):
        # Iron gears at 1/s: one row on one shelf, its channels at their least; the bound
        # is the box laid out, so no bound on shelves can be higher.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = factory.rows_and_lines(item="iron-gear-wheel", rate=1)
            bound = compact.lower_bound(p)
            got = compact.build(p)
        self.assertEqual(bound["shelves"], got.area)

    def test_lower_bound_leaves_the_rows_lines_as_laid_out(self):
        p, _, _ = laid_out(**ITEMS[0])
        before = [(id(q), {lane: id(ln) for lane, ln in q.feeds.items()}) for r in p["rows"] for q in r.ports]
        compact.lower_bound(p)
        self.assertEqual([(id(q), {lane: id(ln) for lane, ln in q.feeds.items()}) for r in p["rows"] for q in r.ports],
                         before)

    def test_a_compact_factory_says_how_close_to_its_lower_bound(self):
        import contextlib
        import io

        from tools.density import density

        err = io.StringIO()
        with contextlib.redirect_stderr(err), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp = factory.build(**ITEMS[1], budget=37)  # a budget no other test asks for
        line = next(ln for ln in err.getvalue().splitlines() if ln.startswith("factory: compact"))
        got = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", line)]
        tiles, least, wide, tall, rows_wide, rows_tall, bus, channels, gap, own = got
        self.assertRegex(line, r"^factory: compact, [\d,]+ tiles; at least [\d,]+ on shelves \(\d+ by \d+: rows \d+ by "
                               r"\d+, bus \d+, channels \d+\), within \d+%; the rows' own tiles [\d,]+$")
        self.assertEqual(tiles, density(bp.to_dict())[0]["area"])
        self.assertEqual((least, rows_wide + bus, rows_tall + channels), (wide * tall, wide, tall))
        self.assertEqual(gap, round(100 * (tiles / least - 1)))
        self.assertLessEqual(own, least)



if __name__ == "__main__":
    unittest.main()
