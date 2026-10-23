"""Run: python -m unittest discover tests"""

import copy
import inspect
import json
import math
import tempfile
import unittest
import warnings
from collections import Counter
from pathlib import Path

from draftsman.blueprintable import Blueprint, BlueprintBook, DeconstructionPlanner, UpgradePlanner

from generators import assembly_row, factory, fluid_block, fluid_row, platform, production, smelting_column
from generators.gated_requester import build
from tools import pipes, rates
from tools.check import check
from tools.codec import CodecError, decode, encode, root_key
from tools.factorio import data_dir
from tools.library import entries, is_entry, load_book, plan_path, save
from tools.settings import game_settings, known, registry
from validator.tests_file import library_tests

FIXTURES = Path(__file__).parent / "fixtures"
# The hand-built string from the planning handoff. Not in library/ until it
# is confirmed in game. Its requester chest uses 2.1 keys (see NOTES.md).
HANDOFF = (FIXTURES / "gated_requester_handoff.txt").read_text()
# Real exports from a 2.1.22 headless server, settings flipped from defaults.
GAME_21 = sorted((FIXTURES / "game-2.1.22").glob("*.txt"))


def generated() -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return build().to_dict()


def errors(data) -> list[str]:
    return [p for p in check(data) if "error" in p.split(": ")[0]]


class Codec(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        data = decode(HANDOFF)
        self.assertEqual(decode(encode(data)), data)

    def test_raw_json_accepted(self):
        import json

        self.assertEqual(decode(json.dumps(decode(HANDOFF))), decode(HANDOFF))

    def test_unknown_version_byte(self):
        with self.assertRaises(CodecError):
            decode("1" + HANDOFF.strip()[1:])


class GatedRequester(unittest.TestCase):
    def test_wires_match_handoff_example(self):
        # Connector IDs checked against defines.wire_connector_id in 2.0.77
        # and 2.1.22 headless servers: input red 1, output red 3, chest red 1.
        self.assertEqual(generated()["blueprint"]["wires"], [[3, 1, 2, 1], [2, 3, 1, 1]])
        self.assertEqual(decode(HANDOFF)["blueprint"]["wires"], [[3, 1, 2, 1], [2, 3, 1, 1]])

    def test_requester_uses_mode_field_2_1_migrates(self):
        chest = generated()["blueprint"]["entities"][0]
        self.assertEqual(chest["control_behavior"], {"circuit_mode_of_operation": 1})

    def test_generated_is_clean(self):
        self.assertEqual(check(generated()), [])


COLUMN_VARIANTS = [
    {},
    {"furnace": "steel-furnace"},
    {"furnace": "electric-furnace"},
    {"furnace": "electric-furnace", "pole": "small-electric-pole"},
    {"pole": "small-electric-pole"},
    {"sides": 1},
    {"recipe": "steel-plate", "belt": "turbo-transport-belt", "furnaces": 3},
    {"furnaces": 1},
]


def column(**params) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return smelting_column.build(**params).to_dict()["blueprint"]


def assert_powered(test: unittest.TestCase, bp: dict, label):
    """Every electric entity overlaps a pole's supply area and every pole is on
    one copper network. Independent of how a generator placed them."""
    from draftsman.data import entities as protos

    ents = bp["entities"]
    poles = {e["entity_number"]: e for e in ents if e["name"].endswith("electric-pole")}
    for e in ents:
        p = protos.raw[e["name"]]
        if p.get("energy_source", {}).get("type") != "electric":
            continue
        half = p["collision_box"][1][0]
        test.assertTrue(any(
            all(abs(e["position"][k] - q["position"][k]) < protos.raw[q["name"]]["supply_area_distance"] + half
                for k in "xy")
            for q in poles.values()), (label, e))
    group = {n: n for n in poles}

    def root(n):
        while group[n] != n:
            n = group[n]
        return n

    for a, ca, b, cb in bp.get("wires", []):
        if (ca, cb) != (5, 5):  # pole_copper; others are circuit wires
            test.assertTrue(ca in (1, 2, 3, 4) and cb in (1, 2, 3, 4), (label, a, ca, b, cb))
            continue
        group[root(a)] = root(b)
    test.assertEqual(len({root(n) for n in poles}), 1, label)


class SmeltingColumn(unittest.TestCase):
    def test_default_saturates_a_yellow_belt(self):
        # 24 stone furnaces per side fill one 7.5/s lane each: 15 iron-plate/s.
        names = Counter(e["name"] for e in column()["entities"])
        self.assertEqual(names, {"transport-belt": 144, "inserter": 96, "stone-furnace": 48,
                                 "medium-electric-pole": 12})

    def test_furnace_limit_follows_the_belts(self):
        limit = {
            ("stone-furnace", "iron-plate", "transport-belt"): 24,  # output lane: 7.5 / 0.3125
            ("steel-furnace", "iron-plate", "transport-belt"): 12,
            ("electric-furnace", "iron-plate", "transport-belt"): 12,
            ("stone-furnace", "stone-brick", "transport-belt"): 12,  # ore lane: 7.5 / (2 * 0.3125)
            ("steel-furnace", "steel-plate", "transport-belt"): 12,  # ore lane: 7.5 / (5 / 8)
            ("electric-furnace", "stone-brick", "transport-belt"): 12,  # both ore lanes
            ("stone-furnace", "iron-plate", "express-transport-belt"): 72,
        }
        for (furnace, recipe, belt), n in limit.items():
            self.assertEqual(smelting_column.plan(furnace, recipe, belt).limit, n, (furnace, recipe, belt))

    def test_variants_check_clean(self):
        for params in COLUMN_VARIANTS:
            self.assertEqual(check({"blueprint": column(**params)}), [], params)

    def test_every_powered_entity_is_on_one_pole_network(self):
        for params in COLUMN_VARIANTS:
            assert_powered(self, column(**params), params)

    def test_flow_tests_name_built_belts(self):
        for params in COLUMN_VARIANTS:
            bp = smelting_column.build(**params)
            ids = {e.id for e in bp.entities if e.id}
            for flow in smelting_column.flow_tests(**params):
                self.assertLessEqual({f.entity for f in flow.feed} | set(flow.drain), ids, params)

    def test_bad_sides(self):
        with self.assertRaises(ValueError):
            smelting_column.build(sides=3)

    def test_filed_column_is_still_what_build_makes(self):
        # The library copy and its tests file were written from build(); a
        # generator change that alters it needs a refile.
        filed = json.loads((LIBRARY / "smelting" / "smelting-column-48-stone-furnace-iron-plate.json").read_text())
        self.assertEqual({"blueprint": column()}, filed)


class Rates(unittest.TestCase):
    def test_recipe_defaults(self):
        gear = rates.recipe("iron-gear-wheel")  # 2.1 omits the default time and categories
        self.assertEqual((gear.time, gear.categories), (0.5, ("crafting",)))
        self.assertEqual(gear.ingredients, {"iron-plate": 2})
        self.assertEqual(rates.recipe("copper-cable").results, {"copper-cable": 2})

    def test_refuses_fluids_and_wrong_machines(self):
        with self.assertRaisesRegex(ValueError, "fluid"):
            rates.recipe("plastic-bar")
        with self.assertRaisesRegex(ValueError, "can't craft"):
            rates.crafts_per_second("assembling-machine-2", rates.recipe("iron-plate"))

    def test_belts_and_inserters(self):
        self.assertEqual(rates.lane_rate("transport-belt"), 7.5)
        self.assertEqual(rates.lane_rate("turbo-transport-belt"), 30)
        fast = rates.inserter_rate("fast-inserter", "transport-belt", True)
        self.assertGreater(fast, 2 * rates.inserter_rate("inserter", "transport-belt", True))

    def test_inserter_rates_match_settings_registry_version(self):
        version = json.loads(rates.INSERTER_RATES.read_text())["game_version"]
        self.assertEqual(version, registry()["game_version"])


ROW_VARIANTS = [
    {},
    {"recipe": "copper-cable"},
    {"recipe": "electronic-circuit", "machine": "assembling-machine-1"},
    {"recipe": "electronic-circuit"},  # three input inserters: poles in gap rows
    {"recipe": "inserter", "machine": "assembling-machine-1"},  # three ingredients: second belt
    {"recipe": "iron-gear-wheel", "machine": "assembling-machine-3", "belt": "express-transport-belt"},
    {"sides": 1, "machines": 2},
    {"pole": "small-electric-pole"},
]


def row(**params) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return assembly_row.build(**params).to_dict()["blueprint"]


class AssemblyRow(unittest.TestCase):
    def test_variants_check_clean_and_are_powered(self):
        for params in ROW_VARIANTS:
            bp = row(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)

    def test_inserters_follow_the_measured_rates(self):
        # Gears in assembling-machine-2: 1.5 crafts/s need 3 iron-plate/s in,
        # more than three basic inserters move with the margin; two fast ones do.
        p = assembly_row.plan()
        self.assertEqual((p.feeders, p.output), ([("fast-inserter", 2)], ("fast-inserter", 1)))
        self.assertEqual(p.limit, 5)  # 5 x 3 plates/s fills both lanes of a yellow belt
        # 2 plates/s: one fast inserter beats three basic ones; forcing basic gives three.
        self.assertEqual(assembly_row.plan(machine="assembling-machine-1").feeders, [("fast-inserter", 1)])
        self.assertEqual(assembly_row.plan(machine="assembling-machine-1", inserter="inserter").feeders,
                         [("inserter", 3)])

    def test_ingredients_spread_over_belts(self):
        p = assembly_row.plan(recipe="inserter", machine="assembling-machine-1")
        self.assertEqual(len(p.lanes), 2)
        self.assertEqual(p.feeders[1][0], "long-handed-inserter")
        self.assertEqual({i for ls in p.lanes for i in ls.values()},
                         {"electronic-circuit", "iron-gear-wheel", "iron-plate"})
        self.assertEqual(assembly_row.plan(recipe="electronic-circuit").gap, 1)

    def test_flow_tests_name_built_belts(self):
        for params in ROW_VARIANTS:
            bp = assembly_row.build(**params)
            ids = {e.id for e in bp.entities if e.id}
            for flow in assembly_row.flow_tests(**params):
                self.assertLessEqual({f.entity for f in flow.feed} | set(flow.drain), ids, params)

    def test_impossible_rows_say_why(self):
        with self.assertRaisesRegex(ValueError, "no input layout"):
            assembly_row.build(recipe="electronic-circuit", machine="assembling-machine-3")
        with self.assertRaisesRegex(ValueError, "fluid"):
            assembly_row.build(recipe="processing-unit")




FLUID_VARIANTS = [
    {},  # plastic: coal through a long-handed inserter, petroleum gas from the pipe
    {"recipe": "battery", "machines": 3},
    {"recipe": "explosives"},
    {"recipe": "processing-unit", "machines": 2},  # assembling-machine-2, gap rows for poles
    {"recipe": "solid-fuel-from-light-oil", "sides": 1, "machines": 2},
]


def fluid(**params) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fluid_row.build(**params).to_dict()["blueprint"]


class FluidRow(unittest.TestCase):
    def test_variants_check_clean_and_are_powered(self):
        for params in FLUID_VARIANTS:
            bp = fluid(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)

    def test_machines_face_the_pipe(self):
        # Chemical plant inputs are on its north face: turned east on the left, west on the right.
        bp = fluid()
        (pipe_x,) = {e["position"]["x"] for e in bp["entities"] if e["name"] == "pipe"}
        plants = [e for e in bp["entities"] if e["name"] == "chemical-plant"]
        self.assertEqual({e["direction"] for e in plants if e["position"]["x"] < pipe_x}, {4})
        self.assertEqual({e["direction"] for e in plants if e["position"]["x"] > pipe_x}, {12})

    def test_product_belt_carries_only_the_product(self):
        p = fluid_row.plan()
        self.assertEqual((p.near, p.far), (None, ["coal"]))
        self.assertEqual(p.feeders[1][0], "long-handed-inserter")
        self.assertEqual((p.fluid, p.fluid_rate), ("petroleum-gas", 120))  # 6 plants x 1 craft/s x 20

    def test_full_lanes_get_fast_outputs(self):
        # 15 explosives plants per side fill a yellow lane; basic inserters fall short there.
        self.assertEqual(fluid_row.plan(recipe="explosives").output[0], "fast-inserter")
        self.assertEqual(fluid_row.plan(recipe="explosives", machines=5).output[0], "inserter")

    def test_flow_tests_name_built_entities(self):
        for params in FLUID_VARIANTS:
            bp = fluid_row.build(**params)
            ids = {e.id for e in bp.entities if e.id}
            for flow in fluid_row.flow_tests(**params):
                named = {f.entity for f in flow.feed} | set(flow.drain) | {f.entity for f in flow.fluids}
                self.assertLessEqual(named, ids, params)

    def test_refuses_what_one_pipe_cant_do(self):
        with self.assertRaisesRegex(ValueError, "exactly one fluid"):
            fluid_row.plan(recipe="sulfur")  # water and petroleum gas
        with self.assertRaisesRegex(ValueError, "is a fluid"):
            fluid_row.plan(recipe="sulfuric-acid")  # a fluid product


def pipe_networks(bp: dict) -> dict:
    return pipes.networks(bp)


BLOCK_VARIANTS = [
    {},  # heavy oil cracking: water and heavy oil north, light oil south
    {"recipe": "light-oil-cracking"},
    {"recipe": "lubricant"},
    {"recipe": "advanced-oil-processing"},  # three outputs north, two inputs south
    {"recipe": "sulfur"},  # two fluids in, sulfur out on belts
    {"recipe": "sulfuric-acid", "machines": 3},  # items in on belts, a belt per machine
    {"recipe": "sulfuric-acid", "belt": "fast-transport-belt"},  # a belt per two machines
]


def block(**params) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fluid_block.build(**params).to_dict()["blueprint"]


class FluidBlock(unittest.TestCase):
    def test_variants_check_clean_and_are_powered(self):
        for params in BLOCK_VARIANTS:
            bp = block(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)

    def test_every_pipe_network_carries_one_fluid(self):
        # Each machine connection names its fluid; a network reaching two fluids would mix them.
        for params in BLOCK_VARIANTS:
            p = fluid_block.plan(**{k: v for k, v in params.items()})
            bp = block(**params)
            nets = pipe_networks(bp)
            fluids = {}
            for e in bp["entities"]:
                if e["name"] != p.machine:
                    continue
                x0 = int(e["position"]["x"] - p.size / 2)
                for side, conns in p.sides.items():
                    row = -1 if side == "north" else p.size
                    for offset, fluid in conns:
                        tile = (x0 + offset, row)
                        self.assertIn(tile, nets, (params, side, fluid))
                        fluids.setdefault(nets[tile], set()).add(fluid)
            self.assertTrue(all(len(f) == 1 for f in fluids.values()), (params, fluids))
            # And one network per fluid line, all machines on it.
            self.assertEqual(len(fluids), len(p.fluids("north")) + len(p.fluids("south")), params)

    def test_fluids_go_to_boxes_in_recipe_order(self):
        p = fluid_block.plan(recipe="sulfur")  # water, then petroleum gas
        self.assertEqual(p.sides["north"], [(0, "water"), (2, "petroleum-gas")])
        p = fluid_block.plan(recipe="advanced-oil-processing")
        self.assertEqual(p.sides["south"], [(1, "water"), (3, "crude-oil")])
        self.assertEqual([f for _, f in p.sides["north"]], ["heavy-oil", "light-oil", "petroleum-gas"])

    def test_belts_share_or_not(self):
        self.assertEqual(fluid_block.plan(recipe="sulfuric-acid").per_belt, 1)  # 10 sulfur/s > a yellow lane
        self.assertEqual(fluid_block.plan(recipe="sulfuric-acid", belt="fast-transport-belt").per_belt, 2)
        self.assertEqual(fluid_block.plan(recipe="sulfur").item_out, "sulfur")

    def test_flow_tests_name_built_entities(self):
        for params in BLOCK_VARIANTS:
            bp = fluid_block.build(**params)
            ids = {e.id for e in bp.entities if e.id}
            for flow in fluid_block.flow_tests(**params):
                named = {f.entity for f in flow.feed} | set(flow.drain) | {f.entity for f in flow.fluids}
                self.assertLessEqual(named, ids, params)

class Production(unittest.TestCase):
    def test_green_circuits(self):
        steps, inputs = production.solve("electronic-circuit", 5)
        self.assertEqual(inputs, {"iron-plate": 5, "copper-plate": 7.5})
        self.assertEqual([(s.item, s.demand) for s in steps], [("copper-cable", 15), ("electronic-circuit", 5)])
        for s in steps:
            self.assertGreaterEqual(s.rows * s.rate, s.demand, s)

    def test_rows_cover_demand_and_respect_the_tier(self):
        for item, rate, kw in (("inserter", 2, {}), ("transport-belt", 15, {}), ("advanced-circuit", 1, {}),
                               ("electronic-circuit", 5, {"machine": "assembling-machine-1"})):
            steps, _ = production.solve(item, rate, **kw)
            allowed = production.MACHINES[production.MACHINES.index(kw.get("machine", "assembling-machine-2")):]
            for s in steps:
                self.assertGreaterEqual(s.rows * s.rate + 1e-9, s.demand, (item, s))
                self.assertIn(s.params["machine"], allowed + ("chemical-plant",), (item, s))

    def test_inputs(self):
        _, inputs = production.solve("advanced-circuit", 1)  # plastic is made from petroleum gas and coal
        self.assertNotIn("plastic-bar", inputs)
        self.assertIn("petroleum-gas", inputs)
        steps, inputs = production.solve("electronic-circuit", 5, smelt=1)
        self.assertEqual(inputs, {"iron-ore": 5, "copper-ore": 7.5})
        self.assertEqual(steps[0].generator, "smelting_column")
        steps, inputs = production.solve("electronic-circuit", 5, raw="copper-cable")
        self.assertEqual(inputs, {"iron-plate": 5, "copper-cable": 15})
        self.assertEqual([s.item for s in steps], ["electronic-circuit"])

    def test_book_matches_parts(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = production.build(item="inserter", rate=2).to_dict()
        children = data["blueprint_book"]["blueprints"]
        self.assertEqual(len(children), len(production.parts(item="inserter", rate=2)))
        self.assertEqual(errors(data), [])

    def test_fluid_recipes_get_fluid_rows(self):
        steps, inputs = production.solve("advanced-circuit", 1)
        self.assertEqual({s.item: s.generator for s in steps}["plastic-bar"], "fluid_row")
        self.assertEqual((inputs["petroleum-gas"], inputs["coal"]), (20, 1))
        _, inputs = production.solve("processing-unit", 0.5)
        self.assertEqual(inputs["water"], 8.75)  # sulfur and acid made here: 0.25 x 30/2 + 2.5 x 100/50

    def test_long_book_descriptions_fit_the_game(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = production.build(item="flying-robot-frame", rate=0.2).to_dict()
        self.assertEqual(errors(data), [])
        self.assertIn("Supply", data["blueprint_book"]["description"])

    def test_oil_balance(self):
        # 10 plastic/s needs 100 petroleum gas/s: refining 100/97.5 crafts/s with all
        # heavy and light oil cracked down; water and crude oil follow.
        steps, inputs = production.solve("plastic-bar", 10, oil=1)
        self.assertAlmostEqual(inputs["crude-oil"], 100 * 100 / 97.5, places=6)
        self.assertAlmostEqual(inputs["water"], 135.897, places=3)
        self.assertEqual([s.item for s in steps[:3]],
                         ["advanced-oil-processing", "heavy-oil-cracking", "light-oil-cracking"])
        self.assertEqual(steps[0].params["machines"], 6)  # 1.03 crafts/s at 0.2 per refinery

    def test_oil_surplus_is_reported(self):
        _, inputs = production.solve("lubricant", 20, oil=1)  # heavy oil only: petroleum gas left over
        self.assertLess(inputs["petroleum-gas"], 0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertIn("left over", production.build(item="lubricant", rate=20, oil=1).description)

    def test_two_fluid_and_fluid_products_get_blocks(self):
        steps, inputs = production.solve("processing-unit", 0.5)
        kinds = {s.item: s.generator for s in steps}
        self.assertEqual((kinds["sulfur"], kinds["sulfuric-acid"]), ("fluid_block", "fluid_block"))
        self.assertIn("petroleum-gas", inputs)  # supplied without oil=1
        self.assertNotIn("sulfuric-acid", inputs)

    def test_unfeedable_item_says_how_to_supply_it(self):
        with self.assertRaisesRegex(ValueError, "raw=assembling-machine-1"):
            production.solve("assembling-machine-1", 0.5)

    def test_chain_packs_every_row(self):
        from tools.check import check

        params = {"item": "advanced-circuit", "rate": 1, "oil": 1}
        data, (flow,) = production.chain_tests(**params)
        book = production.build(**params).to_dict()["blueprint_book"]["blueprints"]
        entities = data["blueprint"]["entities"]
        self.assertEqual(len(entities), sum(len(b["blueprint"]["entities"]) for b in book))
        self.assertEqual([e["entity_number"] for e in entities], list(range(1, len(entities) + 1)))
        spots = [(e["position"]["x"], e["position"]["y"]) for e in entities]
        self.assertEqual(len(spots), len(set(spots)))
        self.assertEqual(check(data), [])
        # Every feed, drain and fluid line names a real entity.
        named = {e["entity_number"] for e in entities}
        self.assertLessEqual({f.entity for f in flow.feed + flow.fluids} | set(flow.drain), named)

    def test_chain_pools_intermediates_only(self):
        _, (flow,) = production.chain_tests(item="advanced-circuit", rate=1, oil=1)
        self.assertTrue(flow.at_least)
        self.assertEqual(flow.expect, {"advanced-circuit": 1.0})
        self.assertNotIn("advanced-circuit", flow.caps)
        self.assertIn("plastic-bar", flow.caps)
        pooled = {f.item for f in flow.feed if f.pooled}
        self.assertIn("electronic-circuit", pooled)
        self.assertNotIn("iron-plate", pooled)  # the book's own input
        self.assertTrue(all(f.pooled for f in flow.fluids if f.fluid == "petroleum-gas" and not f.sink))
        self.assertFalse(any(f.pooled for f in flow.fluids if f.fluid in ("crude-oil", "water")))
        # Cracking feeds (the only heavy and light oil users here) come last:
        # they get what the other rows leave.
        pooled = [f.fluid for f in flow.fluids if f.pooled]
        cracked = [k for k, fluid in enumerate(pooled) if fluid in ("heavy-oil", "light-oil")]
        self.assertTrue(cracked)
        self.assertEqual(cracked, list(range(len(pooled) - len(cracked), len(pooled))))

FACTORIES = [
    {"item": "electronic-circuit", "rate": 5},
    {"item": "advanced-circuit", "rate": 1, "oil": 1},  # refinery, cracking, a fluid row
    {"item": "electronic-circuit", "rate": 5, "smelt": 1},  # burner furnaces: coal comes up the bus too
]


def factory_built(**params):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bp, p = factory._build(factory.plan(**params))
        return bp, p, bp.to_dict()["blueprint"]


def footprint(e) -> list[tuple[int, int]]:
    """Tiles an entity in blueprint form covers, from its collision box and direction."""
    from draftsman.data import entities as protos

    (x1, y1), (x2, y2) = protos.raw[e["name"]]["collision_box"]
    w, h = math.ceil(x2 - x1), math.ceil(y2 - y1)
    if e.get("direction", 0) in (4, 12):
        w, h = h, w
    x0, y0 = round(e["position"]["x"] - w / 2), round(e["position"]["y"] - h / 2)
    return [(x0 + dx, y0 + dy) for dx in range(w) for dy in range(h)]


class Factory(unittest.TestCase):
    def test_checks_clean_powered_and_nothing_overlaps(self):
        for params in FACTORIES:
            _, _, bp = factory_built(**params)
            self.assertEqual(check({"blueprint": bp}), [], params)
            assert_powered(self, bp, params)
            tiles = Counter(t for e in bp["entities"] for t in footprint(e))
            self.assertEqual([t for t, n in tiles.items() if n > 1], [], params)

    def test_each_fluid_line_is_one_network_of_its_own(self):
        _, p, bp = factory_built(**FACTORIES[1])
        nets = pipe_networks(bp)
        fluids = {}
        for tile, fluid in p["fluid_tiles"].items():
            fluids.setdefault(nets[tile], set()).add(fluid)
        self.assertTrue(all(len(f) == 1 for f in fluids.values()), fluids)
        # Each fluid's bus pipes are one network (past a cracking gate's pump is another).
        made = Counter(f for f in fluids.values() for f in f)
        self.assertEqual(made, {"water": 1, "crude-oil": 1, "heavy-oil": 1, "light-oil": 1, "petroleum-gas": 1})

    def test_busy_lines_split_between_belts(self):
        # 15 cable/s would fill a yellow belt to the top: two lines, each from its own rows.
        _, p, _ = factory_built(item="electronic-circuit", rate=5)
        self.assertEqual(len(p["lines"]["copper-cable"]), 2)
        _, p, _ = factory_built(item="transport-belt", rate=15)  # 22.5 iron/s comes in on two belts
        self.assertEqual([round(ln.demand, 2) for ln in p["lines"]["iron-plate"]], [11.25, 11.25])
        self.assertEqual(len(p["lines"]["transport-belt"]), 2)  # and leaves on two

    def test_a_row_can_feed_two_lines(self):
        # 80 cable/s from three rows of 30 for two belts' worth of users: the middle
        # row splits as it leaves, half to each line.
        _, p, bp = factory_built(item="processing-unit", rate=1, oil=1, machine="assembling-machine-3",
                                 belt="express-transport-belt")
        outs = [len(q.outs) for r in p["rows"] if r.step.item == "copper-cable" for q in r.ports if q.kind == "out"]
        self.assertEqual(outs, [1, 2, 1])
        self.assertEqual(check({"blueprint": bp}), [])

    def test_flow_test_feeds_the_bottom_and_drains_the_top(self):
        for params in FACTORIES[:2]:
            bp, p, _ = factory_built(**params)
            ids = {e.id for e in bp.entities if e.id}
            (flow,) = factory.flow_tests(**params, layout="bus")
            named = {f.entity for f in flow.feed} | set(flow.drain) | {f.entity for f in flow.fluids}
            self.assertLessEqual(named, ids, params)
            self.assertEqual(flow.drain, [f"out {ln.name} {ln.column}" for ln in p["lines"][params["item"]]])
            self.assertEqual({f.item for f in flow.feed}, {ln.name for named in p["lines"].values()
                                                           for ln in named if ln.supplied and not ln.fluid})

    def test_description_fits_the_game(self):
        for params in FACTORIES:
            _, _, bp = factory_built(**params)
            self.assertLessEqual(len(bp["description"].encode()), 500, params)


class Platform(unittest.TestCase):
    def build(self, **params):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return platform.build(**params).to_dict()["blueprint"]

    def test_nothing_stands_in_a_railguns_line(self):
        # Railgun shots hit everything along their line (north), own turrets included.
        for params in ({}, {"railguns": 6, "guns": 9, "thrusters": 1}):
            bp = self.build(**params)
            rails = [e for e in bp["entities"] if e["name"] == "railgun-turret"]
            others = [e for e in bp["entities"] if e["name"] != "railgun-turret"]
            for r in rails:
                ahead = [e for e in others if abs(e["position"]["x"] - r["position"]["x"]) < 2.5
                         and e["position"]["y"] < r["position"]["y"]]
                self.assertEqual(ahead, [], params)

    def test_thrusters_have_empty_space_behind(self):
        bp = self.build(thrusters=3)
        tiles = {(t["position"]["x"], t["position"]["y"]) for t in bp["tiles"]}
        for e in bp["entities"]:
            if e["name"] == "thruster":
                x, y = e["position"]["x"], e["position"]["y"]
                behind = [(tx, ty) for tx, ty in tiles if abs(tx + 0.5 - x) < 2 and ty > y + 2]
                self.assertEqual(behind, [])

    def test_railguns_target_big_and_huge_in_the_games_format(self):
        bp = self.build()
        for e in bp["entities"]:
            if e["name"] == "railgun-turret":
                self.assertEqual([t["index"] for t in e["priority-list"]], list(range(1, 9)))
                self.assertTrue(all(t["name"].startswith(("big-", "huge-")) for t in e["priority-list"]))
                self.assertIs(e["ignore-unprioritised"], True)

    def test_nothing_stands_in_a_railguns_arc(self):
        # A railgun fires in a 72-degree arc ahead of it (turn_range 0.2); with gun
        # turrets in its row, none may have a corner inside that arc.
        for params in ({"railguns": 6, "guns": 9, "thrusters": 1, "layout": "front"},
                       {"railguns": 3, "guns": 8, "thrusters": 1, "layout": "front"},
                       {"railguns": 6, "guns": 9, "thrusters": 1}):
            bp = self.build(**params)
            rails = [e for e in bp["entities"] if e["name"] == "railgun-turret"]
            guns = [e for e in bp["entities"] if e["name"] == "gun-turret"]
            for r in rails:
                for g in guns:
                    for cx in (-1, 1):
                        for cy in (-1, 1):
                            dx = g["position"]["x"] + cx - r["position"]["x"]
                            ahead = r["position"]["y"] - (g["position"]["y"] + cy)
                            if ahead > 0:
                                self.assertGreater(math.degrees(math.atan2(abs(dx), ahead)), 36, (params, g))

    def test_front_layout_puts_gun_turrets_in_the_railguns_row(self):
        bp = self.build(railguns=3, guns=8, thrusters=1, layout="front")
        row = sorted((e["position"]["x"], e["name"]) for e in bp["entities"]
                     if e["name"] in ("railgun-turret", "gun-turret"))
        self.assertEqual([n[:4] for _, n in row], ["gun-", "gun-", "rail"] * 3 + ["gun-", "gun-"])
        tops = {e["name"]: e["position"]["y"] - (2.5 if e["name"] == "railgun-turret" else 1) for e in bp["entities"]
                if e["name"] in ("railgun-turret", "gun-turret")}
        self.assertEqual(tops["railgun-turret"], tops["gun-turret"])  # front edges flush
        with self.assertRaises(ValueError):
            self.build(layout="ring")

    def test_powered_and_check_clean(self):
        for params in ({"railguns": 6, "guns": 9, "thrusters": 1},
                       {"railguns": 6, "guns": 9, "thrusters": 1, "layout": "front"},
                       {"railguns": 20, "guns": 24, "thrusters": 1, "layout": "front"},
                       {"railguns": 2, "guns": 12, "thrusters": 1, "layout": "front"},
                       {"railguns": 20, "guns": 24, "thrusters": 1, "cargo_bays": 8, "margin": 16}):
            bp = self.build(**params)
            self.assertEqual(errors({"blueprint": bp}), [], params)
            assert_powered(self, bp, "platform")  # the railguns; gun turrets need no power

    def test_cargo_bays_stand_beside_the_hub_joined_to_it(self):
        # A bay adds hub slots only joined to the hub, directly or through other bays
        # (NOTES.md): they fill the hub's rows east and west, nearest first, on
        # foundation, clear of every other entity and of the hub (tiles -4..3).
        from draftsman.data import entities as protos

        def footprint(e):
            (x0, y0), (x1, y1) = protos.raw[e["name"]]["collision_box"]
            x, y = e["position"]["x"], e["position"]["y"]
            return {(tx, ty) for tx in range(math.floor(x + x0), math.ceil(x + x1))
                    for ty in range(math.floor(y + y0), math.ceil(y + y1))}

        bp = self.build(railguns=1, guns=2, thrusters=1, cargo_bays=8)
        bays = [e for e in bp["entities"] if e["name"] == "cargo-bay"]
        self.assertEqual(sorted((e["position"]["x"], e["position"]["y"]) for e in bays),
                         sorted((s * x, y) for x in (6, 10) for s in (1, -1) for y in (-2, 2)))
        tiles = {(t["position"]["x"], t["position"]["y"]) for t in bp["tiles"]}
        hub = {(x, y) for x in range(-4, 4) for y in range(-4, 4)}
        others = set().union(*(footprint(e) for e in bp["entities"] if e["name"] != "cargo-bay"))
        for b in bays:
            self.assertEqual(len(footprint(b)), 16)
            self.assertLessEqual(footprint(b), tiles)
            self.assertFalse(footprint(b) & (hub | others), b)
        self.assertEqual(errors({"blueprint": bp}), [])
        nearest = self.build(cargo_bays=3)["entities"]
        self.assertEqual(sorted((e["position"]["x"], e["position"]["y"]) for e in nearest if e["name"] == "cargo-bay"),
                         [(-6, -2), (6, -2), (6, 2)])

    def test_filed_platforms_are_what_the_generator_builds(self):
        # library/platforms/ holds the generator's output for these parameters: the 32-railgun
        # platform of platform-foundation, and the promethium platform of platform-bays with
        # the hub feeding its turrets (platform-feed).
        from tools.plan import FILED_PLATFORM, PROMETHIUM_PLATFORM

        filed = {}
        for path in filter(is_entry, (Path(__file__).parent.parent / "library" / "platforms").glob("*.json")):
            bp = json.loads(path.read_text())["blueprint"]
            filed[bp["label"]] = bp
        for params in (FILED_PLATFORM, PROMETHIUM_PLATFORM):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bp = platform.build(**{k: v for k, v in params.items()
                                       if k in inspect.signature(platform.build).parameters}).to_dict()["blueprint"]
            self.assertIn(bp["label"], list(filed), params)
            self.assertEqual(filed[bp["label"]], bp, params)

    def test_description_says_how_a_platform_with_bays_rebuilds(self):
        # The hub rebuilds only with construction-robotics researched, from what it
        # holds; bays are for that stock (NOTES.md). Without bays, nothing to say.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bays = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8).description
            plain = platform.build(railguns=32, guns=32, thrusters=1).description
        self.assertIn("construction-robotics", bays)
        self.assertIn("foundation", bays)
        self.assertNotIn("construction-robotics", plain)
        self.assertLessEqual(len(bays.encode()), 500)  # the game cuts the rest

    def test_label_counts_in_the_singular_and_plural(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertEqual(platform.build(railguns=1, guns=1, thrusters=1, cargo_bays=1).label,
                             "Platform: 1 railgun, 1 gun turret, 1 thruster, 1 cargo bay")
            self.assertEqual(platform.build(railguns=2, guns=0, thrusters=2).label,
                             "Platform: 2 railguns, 0 gun turrets, 2 thrusters")

    def test_margin_is_spare_foundation_at_the_sides(self):
        # Asteroids eat foundation from the side ends inward, every row alike (NOTES.md):
        # margin adds columns there. Nothing moves, and nothing changes in front or
        # behind, where the thrusters need empty space.
        def extent(bp):
            xs, ys = [t["position"]["x"] for t in bp["tiles"]], [t["position"]["y"] for t in bp["tiles"]]
            return min(xs), max(xs), min(ys), max(ys)

        plain = self.build(railguns=4, guns=6, thrusters=1)
        wide = self.build(railguns=4, guns=6, thrusters=1, margin=5)
        x0, x1, y0, y1 = extent(plain)
        self.assertEqual(extent(wide), (x0 - 5, x1 + 5, y0, y1))
        self.assertEqual(len(wide["tiles"]), len(plain["tiles"]) + 2 * 5 * (y1 - y0 + 1))
        self.assertEqual(wide["entities"], plain["entities"])
        with self.assertRaises(ValueError):
            self.build(margin=-1)

    def test_flight_takes_supplies_for_the_hub(self):
        # Spares the hub rebuilds from and repair packs it repairs with.
        [flight] = platform.flight_tests(supplies={"repair-pack": 100})
        self.assertEqual(flight.supplies, {"repair-pack": 100})
        self.assertEqual(platform.flight_tests()[0].supplies, {})

    def test_a_flight_can_hold_its_stock(self):
        # A measurement holds what it supplies (validator.flight.Flight.hold); a trip holds nothing.
        [flight] = platform.flight_tests(supplies={"railgun-turret": 10}, hold=["railgun-turret"])
        self.assertEqual(flight.hold, ["railgun-turret"])
        self.assertEqual(platform.flight_tests()[0].hold, [])

    def test_a_round_trip_can_take_a_shock_as_it_turns(self):
        # A gap in the railgun line where asteroids are thickest, for the hub to close.
        self.assertEqual(platform.flight_tests(start=0.0, turn=0.02, shock=8)[0].shock, 8)
        self.assertEqual(platform.flight_tests()[0].shock, 0)
        with self.assertRaises(ValueError):
            platform.flight_tests(shock=8)  # no turn to take it at

    def test_a_trip_can_set_out_from_home_by_way_of_stops(self):
        # home and via: it sets out from Nauvis orbit, flies the stops to the route's origin, makes
        # its trip, and comes home the same way (validator.flight.Flight).
        [flight] = platform.flight_tests(turn=0.02, home="nauvis", via=["fulgora", "aquilo"])
        self.assertEqual((flight.home, flight.via), ("nauvis", ["fulgora", "aquilo"]))
        self.assertEqual((platform.flight_tests()[0].home, platform.flight_tests()[0].via), ("", []))
        with self.assertRaises(ValueError):
            platform.flight_tests(via=["aquilo"])  # stops on the way from nowhere
        with self.assertRaises(ValueError):
            platform.flight_tests(start=0.1, turn=0.2, home="nauvis")  # from home it flies the whole route

    def test_a_round_trip_turns_and_a_fed_platform_can_ready_in_orbit(self):
        [flight] = platform.flight_tests(turn=0.02, fill=True, ammo_feed=True)
        self.assertEqual((flight.turn, flight.fill), (0.02, True))
        self.assertEqual((platform.flight_tests()[0].turn, platform.flight_tests()[0].fill), (0.0, False))
        with self.assertRaises(ValueError):
            platform.flight_tests(fill=True)  # nothing for the hub to fill without its feed
        with self.assertRaises(ValueError):
            platform.flight_tests(start=0.1, turn=0.05)  # turns before it starts
        with self.assertRaises(ValueError):
            platform.flight_tests(start=0.1, reverse=True, turn=0.2)  # a flight back has nowhere to turn

    def test_side_guns_stand_at_both_side_ends(self):
        # Asteroids eat foundation from the side ends inward, past the gun row's reach
        # (NOTES.md): side_guns stands that many gun turrets in a column at each outer
        # edge, from the gun row down, on foundation, clear of everything else and out
        # of every railgun's arc. Nothing else changes.
        params = {"railguns": 32, "guns": 32, "thrusters": 1, "cargo_bays": 8, "fuel": True, "reserve": True,
                  "power": 2}
        plain, sides = self.build(**params), self.build(**params, side_guns=4)
        self.assertEqual(sides["entities"][:len(plain["entities"])], plain["entities"])
        self.assertEqual(sides["tiles"], plain["tiles"])
        added = sides["entities"][len(plain["entities"]):]
        self.assertEqual([e["name"] for e in added], ["gun-turret"] * 8)
        floor = {(t["position"]["x"], t["position"]["y"]) for t in sides["tiles"]}
        west, east = min(x for x, _ in floor), max(x for x, _ in floor)
        taken = {t for e in plain["entities"] for t in footprint(e)}
        for g in added:
            tiles = footprint(g)
            self.assertTrue(set(tiles) <= floor, g)
            self.assertFalse(set(tiles) & taken, g)
            self.assertIn(min(x for x, _ in tiles) if g["position"]["x"] < 0 else max(x for x, _ in tiles),
                          (west, east))
            taken |= set(tiles)
        self.assertEqual(sum(g["position"]["x"] < 0 for g in added), 4)
        rails = [e for e in sides["entities"] if e["name"] == "railgun-turret"]
        for r in rails:
            for g in added:
                for cx in (-1, 1):
                    for cy in (-1, 1):
                        dx = g["position"]["x"] + cx - r["position"]["x"]
                        ahead = r["position"]["y"] - (g["position"]["y"] + cy)
                        if ahead > 0:
                            self.assertGreater(math.degrees(math.atan2(abs(dx), ahead)), 36, g)
        self.assertEqual(errors({"blueprint": sides}), [])
        with self.assertRaises(ValueError):
            self.build(**params, side_guns=50)  # more than the side ends hold

    def test_flight_takes_a_magazine_for_the_gun_turrets(self):
        # Uranium can't be made in space: a flight can stock piercing rounds instead.
        [flight] = platform.flight_tests(magazine="piercing-rounds-magazine")
        self.assertEqual(flight.ammo, {"railgun-turret": "railgun-ammo", "gun-turret": "piercing-rounds-magazine",
                                       "rocket-turret": "rocket"})
        self.assertEqual(platform.flight_tests()[0].ammo, platform.AMMO)

    def test_rocket_turrets_take_the_big_asteroids_and_railguns_the_huge(self):
        # A rocket takes 10% off a big asteroid's health and kills it in 3 with the flight
        # research, where a railgun shot is a tenth of a hub slot and a rocket a hundredth;
        # huge asteroids shrug rockets off (99%), so railguns keep them (NOTES.md). The
        # rocket turrets stand in a row behind the gun turrets, ahead of the gap above the
        # hub, clear of the chunk belt: out of every railgun's line, on foundation.
        params = {"railguns": 32, "guns": 32, "thrusters": 1, "cargo_bays": 8, "fuel": True, "reserve": True,
                  "power": 2, "promethium": True}
        bp = self.build(**params, rockets=8)
        rockets = [e for e in bp["entities"] if e["name"] == "rocket-turret"]
        guns = [e for e in bp["entities"] if e["name"] == "gun-turret"]
        rails = [e for e in bp["entities"] if e["name"] == "railgun-turret"]
        self.assertEqual(len(rockets), 8)
        self.assertEqual(len(guns), 32)
        for e in rockets:
            self.assertEqual([t["index"] for t in e["priority-list"]], [1, 2, 3, 4])
            self.assertTrue(all(t["name"].startswith("big-") for t in e["priority-list"]), e)
            self.assertIs(e["ignore-unprioritised"], True)
            self.assertGreater(e["position"]["y"], max(g["position"]["y"] for g in guns))
            self.assertLessEqual(e["position"]["y"] + 1.5, -platform.HUB - 2)  # the gap above the hub stays clear
        for r in rails:
            self.assertTrue(all(t["name"].startswith("huge-") for t in r["priority-list"]), r)
        floor = {(t["position"]["x"], t["position"]["y"]) for t in bp["tiles"]}
        taken = Counter(t for e in bp["entities"] for t in footprint(e))
        self.assertEqual([t for t, n in taken.items() if n > 1], [])
        for e in rockets:
            self.assertTrue(set(footprint(e)) <= floor, e)
        belt = {e["position"]["x"] for e in bp["entities"] if e["name"] == "transport-belt"
                and e.get("direction", 0) == 8}  # the chunk belt, flowing south
        for e in rockets:
            self.assertFalse({x + 0.5 for x, _ in footprint(e)} & belt, e)
        self.assertEqual(errors({"blueprint": bp}), [])
        self.assertEqual(self.build(**params, rockets=0), self.build(**params))

    def test_filed_platform_is_what_build_makes(self):
        # New options leave the default as it was: the filed design still builds the same.
        filed = json.loads((LIBRARY / "platforms" / "platform-32-railguns-32-gun-turrets-1-thruster-8-cargo-bays.json")
                           .read_text())["blueprint"]
        bp = self.build(railguns=32, guns=32, thrusters=1, cargo_bays=8)
        self.assertEqual(bp["entities"], filed["entities"])
        self.assertEqual(bp["tiles"], filed["tiles"])
        self.assertEqual(self.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=False), bp)


def tiles_of(e: dict) -> set[tuple[int, int]]:
    from draftsman.data import entities as protos

    (x0, y0), (x1, y1) = protos.raw[e["name"]]["collision_box"]
    if e.get("direction", 0) in (4, 12):
        x0, y0, x1, y1 = -y1, x0, -y0, x1
    x, y = e["position"]["x"], e["position"]["y"]
    return {(tx, ty) for tx in range(math.floor(x + x0), math.ceil(x + x1))
            for ty in range(math.floor(y + y0), math.ceil(y + y1))}


def fluid_ends(e: dict) -> list[tuple[tuple[int, int], str]]:
    """(the tile a connection points into, its box) for chemical plants and thrusters built unrotated."""
    from draftsman.data import entities as protos

    p = protos.raw[e["name"]]
    if e["name"] == "chemical-plant":
        boxes = [(b["production_type"], b) for b in p["fluid_boxes"]]
    elif e["name"] == "thruster":
        boxes = [("fuel", p["fuel_fluid_box"]), ("oxidizer", p["oxidizer_fluid_box"])]
    else:
        return []
    assert e.get("direction", 0) == 0, e
    x, y = e["position"]["x"], e["position"]["y"]
    out = []
    for kind, box in boxes:
        for c in box["pipe_connections"]:
            dx, dy = pipes.VEC[c["direction"]]
            out.append(((math.floor(x + c["position"][0]) + dx, math.floor(y + c["position"][1]) + dy), kind))
    return out


class PlatformFuel(unittest.TestCase):
    """fuel=True: the platform makes its own thruster fuel and oxidizer from asteroid chunks."""

    def setUp(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bp = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True).to_dict()["blueprint"]
        self.ents = self.bp["entities"]

    def named(self, name, **match):
        return [e for e in self.ents if e["name"] == name and all(e.get(k) == v for k, v in match.items())]

    def test_check_clean_and_nothing_overlaps(self):
        self.assertEqual(errors({"blueprint": self.bp}), [])
        seen = {}
        floor = {(t["position"]["x"], t["position"]["y"]) for t in self.bp["tiles"]}
        for e in self.ents:
            for t in tiles_of(e):
                self.assertNotIn(t, seen, (e, seen.get(t)))
                seen[t] = e
                if e["name"] != "asteroid-collector":
                    self.assertIn(t, floor, e)

    def test_collectors_sit_in_the_railguns_row_facing_out(self):
        # Chunks are made in front, where the turrets break asteroids up: collectors
        # elsewhere catch almost none (NOTES.md). Their front row and the 3 ahead are
        # empty space, and they stay out of every railgun's 72-degree arc.
        floor = {(t["position"]["x"], t["position"]["y"]) for t in self.bp["tiles"]}
        top = min(y for _, y in floor)
        rails = self.named("railgun-turret")
        self.assertEqual(len(rails), 32)
        collectors = self.named("asteroid-collector")
        self.assertEqual(len(collectors), 2)
        for c in collectors:
            self.assertEqual(c.get("direction", 0), 0)
            self.assertEqual({f["name"] for f in c["chunk-filter"]},
                             {"metallic-asteroid-chunk", "carbonic-asteroid-chunk", "oxide-asteroid-chunk"})
            x, y = c["position"]["x"], c["position"]["y"]
            self.assertEqual(y, top + 0.5)
            ahead = {(math.floor(x) + dx, top - k) for dx in (-1, 0, 1) for k in range(1, 5)}
            self.assertFalse(ahead & floor)
            for r in rails:
                for cx in (-1.5, 1.5):
                    dx = x + cx - r["position"]["x"]
                    front = r["position"]["y"] - (y - 1.5)
                    if front > 0:
                        self.assertGreater(math.degrees(math.atan2(abs(dx), front)), 36, (c, r))

    def test_one_belt_runs_from_the_collectors_to_a_void(self):
        # Chunks only: every crusher takes its own kind off it and puts back what it
        # returns; what reaches the end goes over the back edge into space.
        belts = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e.get("direction", 0)
                 for e in self.named("transport-belt")}
        floor = {(t["position"]["x"], t["position"]["y"]) for t in self.bp["tiles"]}
        ins = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e
               for e in self.ents if e["name"].endswith("inserter")}

        def follow(tile):
            path = [tile]
            while True:
                dx, dy = pipes.VEC[belts[path[-1]]]
                nxt = (path[-1][0] + dx, path[-1][1] + dy)
                if nxt not in belts:
                    return path
                path.append(nxt)

        ends = set()
        for c in self.named("asteroid-collector"):
            x, y = math.floor(c["position"]["x"]), math.floor(c["position"]["y"])
            self.assertEqual(ins[(x, y + 2)].get("direction", 0), 0)  # takes from the collector north of it
            ends.add(follow((x, y + 3))[-1])
        [(ex, ey)] = ends
        void = ins[(ex, ey + 1)]
        self.assertEqual((void["name"], void.get("direction", 0)), ("fast-inserter", 0))
        self.assertNotIn((ex, ey + 2), floor)  # it drops into space

    def test_each_crusher_feeds_its_plant_and_returns_its_chunk(self):
        expect = {"oxide-asteroid-crushing": "ice-melting", "carbonic-asteroid-crushing": "thruster-fuel",
                  "metallic-asteroid-crushing": "thruster-oxidizer"}
        crushers = self.named("crusher")
        self.assertEqual(sorted(c["recipe"] for c in crushers), sorted(["oxide-asteroid-crushing"] * 2 +
                                                                        ["carbonic-asteroid-crushing",
                                                                         "metallic-asteroid-crushing"]))
        at = {}
        for e in self.ents:
            for t in tiles_of(e):
                at[t] = e
        for c in crushers:
            mine = tiles_of(c)
            near = {at[(x + dx, y)]["entity_number"]: at[(x + dx, y)] for x, y in mine for dx in (-1, 1)
                    if (x + dx, y) in at and at[(x + dx, y)]["name"].endswith("inserter")}
            chunk = c["recipe"].replace("-crushing", "-chunk")
            returns = [i for i in near.values() if i.get("use_filters") and [f["name"] for f in i["filters"]] == [chunk]]
            self.assertEqual(len(returns), 1, c)
            # Some inserter beside the crusher drops into the plant that uses its product.
            plants = []
            for i in near.values():
                dx, dy = pipes.VEC[i.get("direction", 0)]
                x, y = math.floor(i["position"]["x"]), math.floor(i["position"]["y"])
                if (x - dx, y - dy) in at:
                    plants.append(at[(x - dx, y - dy)].get("recipe"))
            self.assertIn(expect[c["recipe"]], plants, c)

    def test_fuel_and_oxidizer_reach_the_thruster_on_their_own_lines(self):
        nets = pipes.networks(self.bp)
        touch = {}
        for e in self.ents:
            for tile, box in fluid_ends(e):
                if tile in nets:
                    kind = box if e["name"] == "thruster" else e["recipe"]
                    touch.setdefault(nets[tile], set()).add((kind, box if e["name"] != "thruster" else "input"))
        lines = sorted(touch.values(), key=sorted)
        self.assertEqual(lines, sorted([{("fuel", "input"), ("thruster-fuel", "output")},
                                        {("oxidizer", "input"), ("thruster-oxidizer", "output")}], key=sorted))
        # Each fuel and oxidizer plant gets its water straight from an ice plant above it.
        plants = {(e["position"]["x"], e["position"]["y"]): e["recipe"] for e in self.named("chemical-plant")}
        for (x, y), recipe in plants.items():
            if recipe in ("thruster-fuel", "thruster-oxidizer"):
                self.assertEqual(plants.get((x, y - 3)), "ice-melting", recipe)

    def test_refuses_what_it_doesnt_do(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with self.assertRaisesRegex(ValueError, "one thruster"):
                platform.build(railguns=32, guns=32, thrusters=2, fuel=True)
            with self.assertRaisesRegex(ValueError, "rows"):
                platform.build(railguns=32, guns=32, thrusters=1, layout="front", fuel=True)

    def test_flight_makes_its_own_fuel(self):
        [flight] = platform.flight_tests(fuel=True, research={"railgun-damage": 2})
        self.assertFalse(flight.fuel)  # no fuel by script
        self.assertEqual(flight.research, {"railgun-damage": 2, "space-platform": 1, "space-platform-thruster": 1})
        # It starts as if it arrived from the last leg: standing still it meets too few asteroids to start.
        self.assertTrue(flight.primed)
        self.assertEqual(flight.start_speed, platform.CRUISE)
        [plain] = platform.flight_tests()
        self.assertEqual((plain.fuel, plain.primed, plain.start_speed), (True, False, 0.0))

    def test_reserve_tanks_sit_on_the_thrusters_west_inputs(self):
        # reserve=True: a storage tank on each of the thruster's free west inputs, fuel on
        # its top row and oxidizer on its middle one, joined straight to it, so the tank
        # shares the line's one fill level (NOTES.md). On foundation, clear of the empty
        # strip behind the thruster.
        self.assertEqual(self.named("storage-tank"), [])  # fuel=True alone is unchanged
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True,
                                reserve=True).to_dict()["blueprint"]
            with self.assertRaisesRegex(ValueError, "reserve"):
                platform.build(railguns=32, guns=32, thrusters=1, reserve=True)
        self.assertEqual(errors({"blueprint": bp}), [])
        [t] = [e for e in bp["entities"] if e["name"] == "thruster"]
        tx, ty = t["position"]["x"], t["position"]["y"]
        tanks = sorted((e for e in bp["entities"] if e["name"] == "storage-tank"), key=lambda e: e["position"]["y"])
        self.assertEqual([(e["position"]["x"], e["position"]["y"], e.get("direction", 0)) for e in tanks],
                         [(tx - 3.5, ty - 3, 0), (tx - 3.5, ty + 1, 4)])
        floor = {(p["position"]["x"], p["position"]["y"]) for p in bp["tiles"]}
        seen = set()
        for e in bp["entities"]:
            for tile in tiles_of(e):
                self.assertNotIn(tile, seen, e)
                seen.add(tile)
        for e in tanks:
            self.assertLessEqual(set(tiles_of(e)), floor)
        self.assertFalse({(x, y) for x in range(-2, 2) for y in range(15, 20)} & floor)  # behind the thruster

    def test_flight_fills_the_reserve(self):
        from draftsman.data import entities as protos

        from validator.flight import PRIME

        tank = protos.raw["storage-tank"]["fluid_box"]["volume"]
        [flight] = platform.flight_tests(fuel=True, reserve=True)
        self.assertFalse(flight.fuel)
        self.assertGreater(flight.primed, PRIME + tank)  # more than the line and its tank hold: the game fills them
        [alone] = platform.flight_tests(fuel=True)
        self.assertEqual(alone.primed, PRIME)
        # A control: the fuelled layout with fuel by script, flown as the plain one is.
        [control] = platform.flight_tests(fuel=True, script_fuel=True)
        self.assertEqual((control.fuel, control.primed, control.start_speed), (True, 0, 0.0))


class PlatformAdvancedFuel(PlatformFuel):
    """fuel="advanced": the module on the advanced thruster recipes, 1,500 fuel or oxidizer from 2
    carbon or iron ore, a calcite and 100 water, against 75 from 2 and 10 (NOTES.md)."""

    def setUp(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bp = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8,
                                     fuel="advanced").to_dict()["blueprint"]
        self.ents = self.bp["entities"]

    def takes_from(self, crusher) -> set:
        """(what each inserter taking from this crusher drops into, its filter)."""
        at = {t: e for e in self.ents for t in tiles_of(e)}
        mine, out = set(tiles_of(crusher)), set()
        for i in self.ents:
            if not i["name"].endswith("inserter"):
                continue
            dx, dy = pipes.VEC[i.get("direction", 0)]  # the side it picks up from
            x, y = math.floor(i["position"]["x"]), math.floor(i["position"]["y"])
            if (x + dx, y + dy) in mine:
                to = at.get((x - dx, y - dy), {})
                out.add((to.get("recipe") or to.get("name"),
                         tuple(f["name"] for f in i["filters"]) if i.get("use_filters") else ()))
        return out

    def test_each_crusher_feeds_its_plant_and_returns_its_chunk(self):
        # An oxide crusher stands across its side's two plants: ice into the ice plant, calcite
        # into the thruster plant, and the calcite they don't take onto the belt, to the void, so
        # it never stops for calcite (it makes 2 a craft with 3 ice; a plant takes 1 a 100 water).
        # The carbonic and metallic crushers hand theirs straight to their thruster plant.
        crushers = self.named("crusher")
        self.assertEqual(sorted(c["recipe"] for c in crushers), sorted(
            ["advanced-oxide-asteroid-crushing"] * 2 + ["carbonic-asteroid-crushing", "metallic-asteroid-crushing"]))
        fed = []
        for c in crushers:
            out = self.takes_from(c)
            chunk = c["recipe"].replace("advanced-", "").replace("-crushing", "-chunk")
            self.assertIn(("transport-belt", (chunk,)), out, c)  # the chunk it returns, back on the belt
            if c["recipe"].startswith("advanced-oxide"):
                self.assertIn(("ice-melting", ()), out, c)
                self.assertIn(("transport-belt", ("calcite",)), out, c)
                [plant] = {to for to, _ in out} & {"advanced-thruster-fuel", "advanced-thruster-oxidizer"}
                fed.append(plant)
            else:
                plant = {"carbonic": "advanced-thruster-fuel", "metallic": "advanced-thruster-oxidizer"}[chunk.split("-")[0]]
                self.assertIn((plant, ()), out, c)
        self.assertEqual(sorted(fed), ["advanced-thruster-fuel", "advanced-thruster-oxidizer"])

    def test_fuel_and_oxidizer_reach_the_thruster_on_their_own_lines(self):
        nets = pipes.networks(self.bp)
        touch = {}
        for e in self.ents:
            for tile, box in fluid_ends(e):
                if tile in nets:
                    kind = box if e["name"] == "thruster" else e["recipe"]
                    touch.setdefault(nets[tile], set()).add((kind, box if e["name"] != "thruster" else "input"))
        lines = sorted(touch.values(), key=sorted)
        self.assertEqual(lines, sorted([{("fuel", "input"), ("advanced-thruster-fuel", "output")},
                                        {("oxidizer", "input"), ("advanced-thruster-oxidizer", "output")}], key=sorted))
        plants = {(e["position"]["x"], e["position"]["y"]): e["recipe"] for e in self.named("chemical-plant")}
        for (x, y), recipe in plants.items():
            if recipe.startswith("advanced-thruster"):
                self.assertEqual(plants.get((x, y - 3)), "ice-melting", recipe)  # its water, straight from above

    def test_flight_makes_its_own_fuel(self):
        [flight] = platform.flight_tests(fuel="advanced", research={"railgun-damage": 2})
        self.assertFalse(flight.fuel)  # no fuel by script
        self.assertEqual(flight.research, {"railgun-damage": 2, "space-platform": 1, "space-platform-thruster": 1,
                                           "advanced-asteroid-processing": 1})
        self.assertTrue(flight.primed)

    def test_refuses_a_fuel_it_doesnt_make(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with self.assertRaisesRegex(ValueError, "fuel"):
                platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel="nuclear")

    def test_the_filed_design_says_what_to_research(self):
        from tools.plan import PROMETHIUM_PLATFORM

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp = platform.build(**{**PROMETHIUM_PLATFORM, "fuel": "advanced"})
        self.assertIn("advanced-asteroid-processing", bp.description)
        self.assertLessEqual(len(bp.description.encode()), 500)  # the game cuts the rest
        self.assertLessEqual(len(bp.label.encode()), 200)

    def test_the_full_design_fits(self):
        # With 24 bays, the reserve, the plants, the hub feed and promethium, nothing overlaps,
        # everything stands on foundation, and the strip behind the thruster stays empty.
        from tools.plan import PROMETHIUM_PLATFORM

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp = platform.build(**{**PROMETHIUM_PLATFORM, "fuel": "advanced"}).to_dict()["blueprint"]
        self.assertEqual(errors({"blueprint": bp}), [])
        floor = {(t["position"]["x"], t["position"]["y"]) for t in bp["tiles"]}
        seen = {}
        for e in bp["entities"]:
            for t in tiles_of(e):
                self.assertNotIn(t, seen, (e, seen.get(t)))
                seen[t] = e
                if e["name"] != "asteroid-collector":
                    self.assertIn(t, floor, e)
        [t] = [e for e in bp["entities"] if e["name"] == "thruster"]
        back = math.floor(t["position"]["y"] + 2.5)
        self.assertFalse({(x, y) for x in range(-2, 2) for y in range(back, back + 5)} & floor)


class PlatformPromethium(unittest.TestCase):
    """promethium=True: the fuel module's collectors take promethium chunks too, and they go to the hub."""

    def setUp(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bp = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, reserve=True,
                                     power=2, promethium=True).to_dict()["blueprint"]
        self.ents = self.bp["entities"]

    def test_check_clean_and_nothing_overlaps(self):
        self.assertEqual(errors({"blueprint": self.bp}), [])
        seen = {}
        floor = {(t["position"]["x"], t["position"]["y"]) for t in self.bp["tiles"]}
        for e in self.ents:
            for t in tiles_of(e):
                self.assertNotIn(t, seen, (e, seen.get(t)))
                seen[t] = e
                if e["name"] != "asteroid-collector":
                    self.assertIn(t, floor, e)

    def test_bays_fit_the_narrow_design_up_to_its_width(self):
        # On 16/16 (74 tiles wide) the bays push the collectors' slot east of them, and
        # the fuel module east of the belt with it: 24 bays still fit on the foundation,
        # 28 would stand its chemical plants off the east edge, so the build refuses.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            full = dict(railguns=16, guns=16, thrusters=1, fuel=True, reserve=True, power=2, promethium=True)
            bp = platform.build(**full, cargo_bays=24).to_dict()["blueprint"]
            plain = platform.build(**full, cargo_bays=8).to_dict()["blueprint"]
            with self.assertRaisesRegex(ValueError, "off the foundation"):
                platform.build(**full, cargo_bays=28)
        floor = {(t["position"]["x"], t["position"]["y"]) for t in bp["tiles"]}
        xs = [x for x, _ in floor]
        self.assertEqual((min(xs), max(xs)), (min(t["position"]["x"] for t in plain["tiles"]),
                                              max(t["position"]["x"] for t in plain["tiles"])))
        self.assertEqual(sum(e["name"] == "cargo-bay" for e in bp["entities"]), 24)
        seen = {}
        for e in bp["entities"]:
            for t in tiles_of(e):
                self.assertNotIn(t, seen, (e, seen.get(t)))
                seen[t] = e
                if e["name"] != "asteroid-collector":
                    self.assertIn(t, floor, e)

    def test_collectors_take_promethium_too(self):
        collectors = [e for e in self.ents if e["name"] == "asteroid-collector"]
        self.assertEqual(len(collectors), 2)
        for c in collectors:
            self.assertEqual({f["name"] for f in c["chunk-filter"]}, set(platform.CHUNKS) | {"promethium-asteroid-chunk"})

    def test_promethium_leaves_the_chunk_belt_for_the_hub(self):
        # A filter inserter takes promethium off the chunk belt onto a belt west along
        # the row above the hub, and an inserter drops it into the hub's north face
        # (tiles -4..3). A chunk stacks to 1, so each takes a hub slot (NOTES.md).
        belts = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e.get("direction", 0)
                 for e in self.ents if e["name"] == "transport-belt"}
        ins = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e
               for e in self.ents if e["name"].endswith("inserter")}
        [f] = [i for i in ins.values()
               if i.get("use_filters") and [x["name"] for x in i["filters"]] == ["promethium-asteroid-chunk"]]
        fx, fy = math.floor(f["position"]["x"]), math.floor(f["position"]["y"])
        dx, dy = pipes.VEC[f.get("direction", 0)]  # an inserter's direction is where it picks up
        self.assertIn((fx + dx, fy + dy), belts)  # the chunk belt
        tile = (fx - dx, fy - dy)
        path = [tile]
        while True:
            vx, vy = pipes.VEC[belts[path[-1]]]
            nxt = (path[-1][0] + vx, path[-1][1] + vy)
            if nxt not in belts:
                break
            path.append(nxt)
        ex, ey = path[-1]
        into = ins[(ex, ey + 1)]
        self.assertEqual(into.get("direction", 0), 0)  # picks from the belt's end, north of it
        self.assertTrue(-platform.HUB <= ex < platform.HUB and ey + 2 == -platform.HUB, (ex, ey))  # drops into the hub

    def test_default_is_unchanged_and_needs_the_fuel_module(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            plain = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, reserve=True, power=2)
            off = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, reserve=True, power=2,
                                 promethium=False)
            self.assertEqual(plain.to_dict(), off.to_dict())
            with self.assertRaisesRegex(ValueError, "promethium"):
                platform.build(railguns=32, guns=32, thrusters=1, promethium=True)

    def test_flight_can_fly_back_and_take_what_it_collects(self):
        # reverse: the same stretch of route, flown back toward the edge; start is still
        # the share from the edge. take: the script empties the hub of these each second
        # and counts them, so a full hub doesn't cap what's measured.
        [back] = platform.flight_tests(start=0.4, reverse=True)
        self.assertEqual((back.origin, back.destination), ("shattered-planet", "solar-system-edge"))
        self.assertAlmostEqual(back.start, 0.6)
        [took] = platform.flight_tests(take=["promethium-asteroid-chunk"])
        self.assertEqual(took.take, ["promethium-asteroid-chunk"])
        [plain] = platform.flight_tests(start=0.4)
        self.assertEqual((plain.origin, plain.start, plain.take), ("solar-system-edge", 0.4, []))
        with self.assertRaisesRegex(ValueError, "back"):
            platform.flight_tests(reverse=True)


class PlatformFeed(unittest.TestCase):
    """ammo_feed=True: the hub feeds every railgun and gun turret by belt and inserter."""

    def setUp(self):
        from tools.studies import BAYS_PLATFORM

        self.params = BAYS_PLATFORM
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bp = platform.build(**BAYS_PLATFORM, ammo_feed=True).to_dict()["blueprint"]
        self.ents = self.bp["entities"]

    def test_check_clean_and_nothing_overlaps(self):
        self.assertEqual(errors({"blueprint": self.bp}), [])
        seen = {}
        floor = {(t["position"]["x"], t["position"]["y"]) for t in self.bp["tiles"]}
        for e in self.ents:
            for t in tiles_of(e):
                self.assertNotIn(t, seen, (e, seen.get(t)))
                seen[t] = e
                if e["name"] != "asteroid-collector":
                    self.assertIn(t, floor, e)

    def test_every_turret_is_fed_from_the_hub(self):
        # Follow the belts from where the hub's inserters drop: every railgun and gun turret
        # has an inserter taking from one of them and dropping into it. A start belt takes a
        # belt pointing in from each side onto a lane each (NOTES.md): one railgun ammo, one
        # magazines, so neither backs up the other.
        hub = {(x, y) for x in range(-platform.HUB, platform.HUB) for y in range(-platform.HUB, platform.HUB)}
        belts = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e for e in self.ents
                 if e["name"] in ("transport-belt", "underground-belt")}
        ins = [e for e in self.ents if e["name"].endswith("inserter")]

        def ends(i):  # (picks from, drops into): an inserter's direction is where it picks up
            x, y = math.floor(i["position"]["x"]), math.floor(i["position"]["y"])
            dx, dy = pipes.VEC[i.get("direction", 0)]
            return (x + dx, y + dy), (x - dx, y - dy)

        def downstream(tile):
            seen = []
            while tile in belts and tile not in seen:
                seen.append(tile)
                b = belts[tile]
                dx, dy = pipes.VEC[b.get("direction", 0)]
                if b["name"] == "underground-belt" and b.get("type", "input") == "input":  # the default is left out
                    tile = next(t for k in range(1, 6) if (t := (tile[0] + dx * k, tile[1] + dy * k)) in belts
                                and belts[t]["name"] == "underground-belt")
                else:
                    tile = (tile[0] + dx, tile[1] + dy)
            return seen

        feeders = [i for i in ins if ends(i)[0] in hub and ends(i)[1] in belts and i.get("use_filters")
                   and i["filters"][0]["name"] in platform.AMMO.values()]  # not the fusion cells' inserter
        ammo = Counter(f["filters"][0]["name"] for f in feeders)
        self.assertEqual(ammo, {platform.AMMO["railgun-turret"]: 2, platform.AMMO["gun-turret"]: 2})
        fed = set()
        for f in feeders:
            fed.update(downstream(ends(f)[1]))
        for t in self.ents:
            if t["name"] in ("railgun-turret", "gun-turret"):
                into = [i for i in ins if ends(i)[1] in tiles_of(t) and ends(i)[0] in fed]
                self.assertEqual(len(into), 1, t)
        for f in feeders:  # each start belt: one ammo from the west, the other from the east
            side = belts[ends(f)[1]]
            start = downstream(ends(f)[1])[1]
            self.assertEqual(pipes.VEC[side["direction"]], (start[0] - ends(f)[1][0], 0))

    def test_default_is_unchanged_and_refuses_what_it_cant_feed(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertEqual(platform.build(**self.params).to_dict(),
                             platform.build(**self.params, ammo_feed=False).to_dict())
            for extra in ({"rockets": 4}, {"side_guns": 2}):
                with self.assertRaisesRegex(ValueError, "ammo_feed"):
                    platform.build(**{**self.params, **extra}, ammo_feed=True)
            with self.assertRaisesRegex(ValueError, "ammo_feed"):
                platform.build(railguns=4, guns=6, layout="front", ammo_feed=True)
            small = platform.build(railguns=4, guns=6, thrusters=1, ammo_feed=True).to_dict()["blueprint"]
        self.assertEqual(errors({"blueprint": small}), [])

    def test_description_says_to_stock_the_hub(self):
        self.assertIn("feeds the turrets", self.bp["description"])
        self.assertTrue(self.bp["label"].endswith(", feeds its turrets"), self.bp["label"])
        self.assertNotIn("Supply railgun ammo", self.bp["description"])
        for tech, level in platform.FEED_RESEARCH.items():  # its hub inserters keep up 0.1 out only with it
            self.assertIn(f"research {tech}-{level}", self.bp["description"])
        self.assertLessEqual(len(self.bp["description"].encode()), 500)
        self.assertLessEqual(len(self.bp["label"].encode()), 200)

    def test_flight_feeds_from_the_hub_only(self):
        # ammo_feed: no script top-up; the turrets are loaded once from the hub, as they
        # would be in orbit, and what leaves the hub counts as used.
        [fed] = platform.flight_tests(ammo_feed=True)
        self.assertEqual(fed.ammo, {})
        self.assertEqual(fed.loaded, {"railgun-turret": "railgun-ammo", "gun-turret": "uranium-rounds-magazine"})
        self.assertEqual(sorted(fed.feed), ["railgun-ammo", "uranium-rounds-magazine"])
        [plain] = platform.flight_tests()
        self.assertEqual((plain.loaded, plain.feed), ({}, []))
        self.assertTrue(plain.ammo)
        with self.assertRaisesRegex(ValueError, "ammo_feed"):
            platform.flight_tests(ammo_feed=True, magazine="piercing-rounds-magazine")


def connections(e: dict, box: str | None, spec: dict | None = None) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """(the tile a connection sits on, the tile it points into) for a fluid box of a prototype
    (by key, or the box itself as spec), turned by the entity's direction."""
    from draftsman.data import entities as protos

    turn = {0: lambda x, y: (x, y), 4: lambda x, y: (-y, x), 8: lambda x, y: (-x, -y), 12: lambda x, y: (y, -x)}
    d = e.get("direction", 0)
    out = []
    for c in (spec or protos.raw[e["name"]][box])["pipe_connections"]:
        px, py = turn[d](*c["position"])
        dx, dy = pipes.VEC[(c["direction"] + d) % 16]
        at = (math.floor(e["position"]["x"] + px), math.floor(e["position"]["y"] + py))
        out.append((at, (at[0] + dx, at[1] + dy)))
    return out


class PlatformPower(unittest.TestCase):
    """power=N: N fusion plants on board, the first fed fusion cells from the hub."""

    def setUp(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.bp = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, reserve=True,
                                     power=2).to_dict()["blueprint"]
        self.ents = self.bp["entities"]

    def named(self, name):
        return [e for e in self.ents if e["name"] == name]

    def test_check_clean_on_foundation_and_clear_of_the_exhaust(self):
        self.assertEqual(errors({"blueprint": self.bp}), [])
        self.assertLessEqual(len(self.bp["description"].encode()), 500)
        floor = {(t["position"]["x"], t["position"]["y"]) for t in self.bp["tiles"]}
        self.assertEqual(len(floor), len(self.bp["tiles"]))  # no tile laid twice
        seen = {}
        for e in self.ents:
            for t in tiles_of(e):
                self.assertNotIn(t, seen, (e, seen.get(t)))
                seen[t] = e
                if e["name"] != "asteroid-collector":
                    self.assertIn(t, floor, e)
        [t] = self.named("thruster")
        tx, ty = math.floor(t["position"]["x"]), math.floor(t["position"]["y"])
        self.assertFalse({(x, y) for x in range(tx - 2, tx + 2) for y in range(ty + 3, ty + 30)} & floor)

    def test_each_reactor_has_two_generators_and_a_cooler(self):
        self.assertEqual(len(self.named("fusion-reactor")), 2)
        self.assertEqual(len(self.named("fusion-generator")), 4)
        self.assertEqual([c["recipe"] for c in self.named("cryogenic-plant")], ["fluoroketone-cooling"] * 2)
        self.assertEqual(len(self.named("accumulator")), 1)  # charged before leaving, it starts the reactors
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            one = platform.build(railguns=32, guns=32, thrusters=1, power=True).to_dict()["blueprint"]
        self.assertEqual(sum(e["name"] == "fusion-reactor" for e in one["entities"]), 1)

    def test_each_generator_takes_plasma_from_a_reactor(self):
        out = [c for r in self.named("fusion-reactor") for c in connections(r, "output_fluid_box")]
        for g in self.named("fusion-generator"):
            joined = [(a, b) for a, b in connections(g, "input_fluid_box") for c, d in out if b == c and d == a]
            self.assertEqual(len(joined), 1, g)

    def test_hot_and_cold_coolant_run_on_separate_lines(self):
        # Generators send hot fluoroketone to the cooler; it sends it back cold to the reactor.
        nets = pipes.networks(self.bp)
        reactors, coolers = self.named("fusion-reactor"), self.named("cryogenic-plant")
        hot = {nets[t] for g in self.named("fusion-generator") for _, t in connections(g, "output_fluid_box") if t in nets}
        hot_in = {nets[t] for c in coolers for _, t in connections_of_boxes(c, "input") if t in nets}
        cold = {nets[t] for c in coolers for _, t in connections_of_boxes(c, "output") if t in nets}
        cold_in = {nets[t] for r in reactors for _, t in connections(r, "input_fluid_box") if t in nets}
        self.assertEqual(len(hot), 2)  # a line each way for each plant
        self.assertEqual(hot, hot_in)
        self.assertEqual(len(cold), 2)
        self.assertEqual(cold, cold_in)
        self.assertFalse(hot & cold)

    def test_cells_go_from_the_hub_to_every_reactor(self):
        # A rebuilt reactor starts with no cells, and the hub fills no fuel slot itself
        # (NOTES.md): every reactor needs the path, the far ones under the near ones.
        from draftsman.data import entities

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            three = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, reserve=True,
                                   power=3).to_dict()["blueprint"]
        self.assertEqual(errors({"blueprint": three}), [])
        for bp in (self.bp, three):
            ents = bp["entities"]
            at = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e for e in ents
                  if e["name"].endswith(("transport-belt", "underground-belt", "inserter"))}

            def ends(i):
                dx, dy = pipes.VEC[at[i].get("direction", 0)]  # an inserter's direction is where it picks up
                return (i[0] + dx, i[1] + dy), (i[0] - dx, i[1] - dy)

            [start] = [i for i, e in at.items() if e.get("use_filters")
                       and [f["name"] for f in e["filters"]] == ["fusion-power-cell"]]
            take, tile = ends(start)
            self.assertTrue(-4 <= take[0] <= 3 and -4 <= take[1] <= 3)  # from the hub
            line = set()
            while tile in at and not at[tile]["name"].endswith("inserter"):
                e = at[tile]
                line.add(tile)
                dx, dy = pipes.VEC[e.get("direction", 0)]
                if e["name"].endswith("underground-belt") and e.get("type", "input") == "input":
                    reach = entities.raw[e["name"]]["max_distance"]
                    [tile] = [(tile[0] + dx * k, tile[1] + dy * k) for k in range(1, reach + 1)
                              if at.get((tile[0] + dx * k, tile[1] + dy * k), {}).get("type") == "output"][:1]
                    line.add(tile)
                tile = (tile[0] + dx, tile[1] + dy)
            for reactor in [e for e in ents if e["name"] == "fusion-reactor"]:
                fed = [i for i in at if at[i]["name"].endswith("inserter") and i != start
                       and ends(i)[0] in line and ends(i)[1] in tiles_of(reactor)]
                self.assertEqual(len(fed), 1, reactor["position"])

    def test_refuses_what_it_doesnt_do(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with self.assertRaisesRegex(ValueError, "one thruster"):
                platform.build(railguns=8, guns=8, thrusters=2, power=True)
            plain = platform.build(railguns=8, guns=8, thrusters=1, power=True).to_dict()["blueprint"]
        self.assertEqual(errors({"blueprint": plain}), [])  # without fuel=True too

    def test_flight_runs_on_its_own_power(self):
        [flight] = platform.flight_tests(power=True, fuel=True, reserve=True, research={"railgun-damage": 2})
        self.assertFalse(flight.power)  # no stand-in source
        self.assertTrue(flight.charged)
        self.assertGreater(flight.coolant, 0)
        self.assertEqual(flight.research["cryogenic-plant"], 1)  # the cooler's recipe
        self.assertGreater(flight.supplies["fusion-power-cell"], 0)
        [kept] = platform.flight_tests(power=True, supplies={"fusion-power-cell": 3})
        self.assertEqual(kept.supplies["fusion-power-cell"], 3)
        [plain] = platform.flight_tests()
        self.assertEqual((plain.power, plain.charged, plain.coolant), (True, False, 0))


def connections_of_boxes(e: dict, kind: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """connections() over every fluid box of a crafting machine of one production type."""
    from draftsman.data import entities as protos

    out = []
    for box in protos.raw[e["name"]]["fluid_boxes"]:
        if box["production_type"] == kind:
            out += connections(e, None, box)
    return out


class Check(unittest.TestCase):
    def test_real_2_1_exports_have_no_errors(self):
        self.assertTrue(GAME_21)
        for path in GAME_21:
            with self.subTest(path.stem):
                self.assertEqual(errors(decode(path.read_text())), [])

    def test_handoff_example_has_no_errors(self):
        self.assertEqual(errors(decode(HANDOFF)), [])

    def test_dangling_wire(self):
        data = generated()
        data["blueprint"]["wires"].append([3, 1, 9, 1])
        self.assertTrue(any("no entity 9" in p for p in errors(data)))

    def test_output_connector_on_chest(self):
        data = generated()
        data["blueprint"]["wires"].append([1, 3, 2, 2])
        self.assertTrue(any("has none" in p for p in errors(data)))

    def test_bad_connector_id(self):
        data = generated()
        data["blueprint"]["wires"].append([3, 7, 2, 1])
        self.assertTrue(any("connector 7" in p for p in errors(data)))

    def test_unknown_names_are_errors(self):
        data = generated()
        data["blueprint"]["entities"][2]["name"] = "constant-combinatorr"
        self.assertTrue(any("UnknownEntityWarning" in p for p in errors(data)))

    def test_misspelled_key_is_only_a_warning(self):
        # Draftsman can't tell a typo from a 2.1 key it doesn't model.
        data = generated()
        cond = data["blueprint"]["entities"][1]["control_behavior"]["decider_conditions"]["conditions"][0]
        cond["first_signal_network"] = cond.pop("first_signal_networks")
        self.assertEqual(errors(data), [])
        self.assertTrue(any("first_signal_network" in p for p in check(data)))

    def test_unknown_signal_is_error(self):
        data = generated()
        data["blueprint"]["entities"][2]["control_behavior"]["sections"]["sections"][0]["filters"][0]["name"] = "iron-plates"
        self.assertTrue(any("iron-plates" in p for p in errors(data)))

    def test_text_the_game_would_cut(self):
        data = generated()
        data["blueprint"]["description"] = "é" * 251  # 502 bytes
        data["blueprint"]["label"] = "x" * 201
        self.assertEqual(sorted(e.split(" is ")[0] for e in errors(data)), ["error: description", "error: label"])

    def test_wire_reach(self):
        data = generated()
        data["blueprint"]["entities"][2]["position"] = {"x": 40.5, "y": 2.5}
        self.assertTrue(any("spans" in p for p in check(data)))


def make_book() -> dict:
    """A nested book with both planner kinds, built through Draftsman."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        inner = BlueprintBook(label="Inner")
        inner.blueprints.append(build(item="copper-plate"))
        outer = BlueprintBook(label="Outer")
        outer.blueprints.append(build())
        outer.blueprints.append(inner)
        outer.blueprints.append(DeconstructionPlanner(label="Trees"))
        outer.blueprints.append(UpgradePlanner(label="Belts"))
        return outer.to_dict()


class GameSettings(unittest.TestCase):
    def machine(self):
        from draftsman.entity import new_entity

        return new_entity("assembling-machine-2", position=(1.5, 1.5))

    def test_registry_is_generated(self):
        reg = registry()
        self.assertTrue(reg["game_version"].startswith("2.1"))
        self.assertIn("read_ingredients", known("assembling-machine"))

    def test_sets_registry_keys_into_draftsman_output(self):
        bp = Blueprint()
        machine = self.machine()
        game_settings(machine, {"read_ingredients": True})
        bp.entities.append(machine)
        data = bp.to_dict()
        self.assertEqual(data["blueprint"]["entities"][0]["control_behavior"], {"read_ingredients": True})
        self.assertEqual(check(data), [])  # a registry key is a known game key

    def test_refuses_typos_and_wrong_kinds(self):
        with self.assertRaisesRegex(ValueError, "Did you mean 'read_ingredients'"):
            game_settings(self.machine(), {"read_ingredient": True})
        with self.assertRaisesRegex(ValueError, "takes a bool"):
            game_settings(self.machine(), {"read_ingredients": 1})

    def test_check_warns_on_unknown_control_behavior_key(self):
        data = generated()
        data["blueprint"]["entities"][0]["control_behavior"]["set_request"] = True
        self.assertTrue(any("set_request is not a known game setting" in p for p in check(data)))


LIBRARY = Path(__file__).parent.parent / "library"


class LibraryTests(unittest.TestCase):
    """Tests files parse and name real entities and connectors (no game needed)."""

    def write(self, folder: Path, tests: str) -> Path:
        save(generated(), folder)
        path = folder / "gated-requester.tests.toml"
        path.write_text(tests)
        return path

    def test_library_tests_parse(self):
        found = {label: tests for label, _, tests in library_tests(LIBRARY)}
        self.assertEqual(len(found["logistics/gated-requester.json"].cases), 5)
        [flow] = found["smelting/smelting-column-48-stone-furnace-iron-plate.json"].flows
        self.assertEqual(flow.expect, {"iron-plate": 15})

    def test_flow_typos_fail_statically(self):
        base = ('[ids]\nchest = 1\n[[flow]]\nname = "x"\n'
                'feed = [{ entity = "chest", lane = "left", item = "iron-ore" }]\n'
                'drain = ["chest"]\nexpect = { iron-plate = 1 }\n')
        with tempfile.TemporaryDirectory() as tmp:
            self.write(Path(tmp), base)
            self.assertEqual(len(library_tests(Path(tmp))[0][2].flows), 1)  # the base itself parses
        bad = {
            "unknown lane": ('lane = "left"', 'lane = "middle"'),
            "unknown item": ('item = "iron-ore"', 'item = "iron-ore-"'),
            "unknown expected item": ("iron-plate = 1", "iron-plates = 1"),
            "unknown drain": ('drain = ["chest"]', 'drain = ["chset"]'),
            "unknown key": ("expect =", "expects ="),
        }
        for what, (old, new) in bad.items():
            with self.subTest(what), tempfile.TemporaryDirectory() as tmp:
                self.write(Path(tmp), base.replace(old, new))
                with self.assertRaises(ValueError):
                    library_tests(Path(tmp))

    def test_typos_fail_statically(self):
        bad = {
            "unknown entity": 'entity = "chset"',
            "unknown connector": 'connector = "combinator_ouput_red"',
            "unknown key": 'requets = {}',
        }
        base = ('[ids]\nchest = 1\n[[case]]\nname = "x"\n'
                'expect = [{ entity = "chest", connector = "circuit_red", requests = {} }]\n')
        for what, typo in bad.items():
            with self.subTest(what), tempfile.TemporaryDirectory() as tmp:
                text = (base.replace('entity = "chest"', typo) if "entity" in typo
                        else base.replace('connector = "circuit_red"', typo) if "connector" in typo
                        else base.replace("requests = {}", typo))
                self.write(Path(tmp), text)
                with self.assertRaises(ValueError):
                    library_tests(Path(tmp))

    def test_orphan_tests_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "nothing.tests.toml").write_text("")
            with self.assertRaisesRegex(ValueError, "no nothing.json next to it"):
                library_tests(Path(tmp))


class Library(unittest.TestCase):
    def test_library_files_check_clean(self):
        for path in sorted(LIBRARY.rglob("*.json")):
            if is_entry(path):
                with self.subTest(str(path.relative_to(LIBRARY))):
                    data = decode(path.read_text())
                    data.pop("index", None)
                    self.assertEqual(errors(data), [])

    def test_book_round_trip(self):
        book = make_book()
        with tempfile.TemporaryDirectory() as tmp:
            folder = save(copy.deepcopy(book), Path(tmp))
            self.assertEqual(
                sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.json")),
                [
                    "00-gated-requester.json",
                    "01-inner/00-gated-requester.json",
                    "01-inner/_book.json",
                    "02-trees.json",
                    "03-belts.json",
                    "_book.json",
                ],
            )
            self.assertEqual(load_book(folder), book)
            self.assertEqual(errors(load_book(folder)), [])

    def test_entries_include_loose_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            save(generated(), lib)              # loose top-level file
            save(generated(), lib / "a-book")   # folder book
            got = {name: root_key(data) for name, data in entries(lib)}
            self.assertEqual(got, {"gated-requester.json": "blueprint", "a-book": "blueprint_book"})

    def test_a_plan_beside_a_blueprint_is_not_an_entry(self):
        # NAME.plan.json is NAME.json's trip plan (tools.plan), not a blueprint of its book.
        book = make_book()
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            folder = save(copy.deepcopy(book), lib)
            plan_path(folder / "02-trees.json").write_text("{}")
            loose = save(generated(), lib)
            plan_path(loose).write_text("{}")
            self.assertEqual(load_book(folder), book)
            self.assertEqual(sorted(name for name, _ in entries(lib)), sorted([folder.name, loose.name]))
            self.assertEqual(sorted(p.name for p in lib.rglob("*") if is_entry(p)),
                             sorted(["00-gated-requester.json", "00-gated-requester.json", "02-trees.json",
                                     "03-belts.json", loose.name]))

    def test_plain_folder_becomes_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "my-book"
            save(generated(), folder)
            save(generated(), folder)
            book = load_book(folder)["blueprint_book"]
            self.assertEqual(book["item"], "blueprint-book")  # the game's item name
            self.assertEqual(book["label"], "my-book")
            self.assertEqual([c["index"] for c in book["blueprints"]], [0, 1])


class FactorioInstall(unittest.TestCase):
    def test_data_dir_follows_the_install_layout(self):
        layouts = {
            "linux": ("factorio/bin/x64/factorio", "factorio/data"),
            "mac": ("factorio.app/Contents/MacOS/factorio", "factorio.app/Contents/data"),
        }
        for name, (exe, data) in layouts.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / data).mkdir(parents=True)
                self.assertEqual(data_dir(Path(tmp) / exe), Path(tmp) / data)


class Trip(unittest.TestCase):
    """tools.trip: where a promethium trip should turn round, from measured per-1,000-km rates."""

    FOUNDATION, CHUNK = "space-platform-foundation", "promethium-asteroid-chunk"

    def flat(self, chunks=10.0):
        # The same everywhere: 100 foundation (a stack) and `chunks` chunks per 1,000 km, at 40 km/s.
        row = {self.FOUNDATION: 100.0, self.CHUNK: chunks, "km/s": 40.0}
        return {0.0: dict(row), 0.4: dict(row)}

    def test_rates_come_from_the_route_studys_outward_flights(self):
        from tools import trip

        rates = {"rebuilt space-platform-foundation/1000 km": 20.0, "rebuilt railgun-turret/1000 km": 0.5,
                 "railgun-ammo/1000 km": 5.0, "uranium-rounds-magazine/1000 km": 30.0,
                 "thruster-fuel/1000 km": 9.0, "railgun-ammo/1000 km lived": 4.0, "fusion cells/1000 km": 0.01,
                 "taken promethium-asteroid-chunk/1000 km": 2.0, "cruise km/s": 40.0, "km_mean": 20000.0,
                 "survived": 0.75}
        result = {"trials": [{"start": 0.0, "mode": "out", "passed": True, "rates": rates},
                             {"start": 0.4, "mode": "back", "passed": True, "rates": rates},
                             {"start": 0.4, "mode": "out", "passed": False, "rates": {}}]}
        # Fluids aren't cargo. Deaths per 1,000 km: a quarter of the flights died, over 20,000 km each.
        self.assertEqual(trip.table(result), {0.0: {
            self.FOUNDATION: 20.0, "railgun-turret": 0.5, "railgun-ammo": 5.0, "uranium-rounds-magazine": 30.0,
            "fusion-power-cell": 0.01, self.CHUNK: 2.0, "km/s": 40.0, "deaths": 0.0125}})
        self.assertEqual(set(trip.table(result, mode="back")), {0.4})

    def test_rates_between_measured_places_are_linear(self):
        from tools import trip

        rates = {0.0: {self.FOUNDATION: 10.0, "km/s": 40.0}, 0.4: {self.FOUNDATION: 50.0, self.CHUNK: 8.0, "km/s": 60.0}}
        self.assertEqual(trip.at(rates, 0.1), {self.FOUNDATION: 20.0, self.CHUNK: 2.0, "km/s": 45.0})
        self.assertEqual(trip.at(rates, 0.4)[self.FOUNDATION], 50.0)

    def test_a_trip_loads_what_it_uses_and_fills_the_hub_with_chunks(self):
        # Out 100,000 km and back: 200 stacks of foundation, so 800 of 1,000 slots are free
        # for 2,000 chunks (one a slot), and the slots the foundation frees as it goes.
        from tools import trip

        t = trip.trip(self.flat(), 0.1, slots=1000, length=1e6)
        self.assertTrue(t["fits"])
        self.assertEqual((t["km"], t["loaded slots"]), (200000.0, 200))
        self.assertAlmostEqual(t["used"][self.FOUNDATION], 20000.0)
        self.assertAlmostEqual(t["hours"], 200000 / 40 / 3600)
        self.assertAlmostEqual(t["chunks"], 1000.0, delta=1)  # the whole hub, at the end
        self.assertAlmostEqual(t["lost"], 1000.0, delta=1)  # no room when they came
        self.assertIsNone(t["ran out"])
        self.assertEqual(t["comes back"], 1.0)  # no deaths measured

    def test_deaths_per_1000_km_set_the_chance_of_coming_back(self):
        from tools import trip

        rates = self.flat()
        for row in rates.values():
            row["deaths"] = 0.005
        t = trip.trip(rates, 0.1, slots=1000, length=1e6)
        self.assertAlmostEqual(t["comes back"], math.exp(-0.005 * 200))

    def test_what_is_made_aboard_isnt_cargo(self):
        # aboard: supplies the platform makes for itself, so it loads none.
        from tools import trip

        rates = self.flat(chunks=0.0)
        for row in rates.values():
            row["railgun-ammo"] = 100.0  # 10 slots per 1,000 km at 10 a stack
        loaded = trip.trip(rates, 0.1, slots=10000, length=1e6)
        made = trip.trip(rates, 0.1, slots=10000, length=1e6, aboard={"railgun-ammo"})
        self.assertEqual((loaded["loaded slots"], made["loaded slots"]), (2200, 200))
        self.assertNotIn("railgun-ammo", made["used"])

    def test_spares_set_a_floor_under_the_load_and_take_their_slots(self):
        # least: a floor under the load, spares for a bad trip the rates don't show. They're loaded
        # and fill their slots the whole way, so they take room from the chunks; they aren't use.
        from tools import trip

        plain = trip.trip(self.flat(), 0.1, slots=1000, length=1e6)
        spared = trip.trip(self.flat(), 0.1, slots=1000, length=1e6, least={"railgun-turret": 20})
        self.assertEqual(spared["loaded"]["railgun-turret"], 20)
        self.assertEqual(spared["loaded slots"], plain["loaded slots"] + 2)
        self.assertAlmostEqual(spared["chunks"], plain["chunks"] - 2, delta=0.5)
        self.assertNotIn("railgun-turret", spared["used"])
        self.assertIsNone(spared["ran out"])
        # A floor under what's used too: the larger of the two is loaded.
        floor = trip.trip(self.flat(), 0.1, slots=1000, length=1e6, least={self.FOUNDATION: 100})
        self.assertEqual(floor["loaded"][self.FOUNDATION], 20000)
        # scale: what's used times a margin, for a trip that uses more than the average.
        scaled = trip.trip(self.flat(), 0.1, slots=1000, length=1e6, scale={self.FOUNDATION: 1.5})
        self.assertEqual(scaled["loaded"][self.FOUNDATION], 30000)
        self.assertAlmostEqual(scaled["used"][self.FOUNDATION], 20000.0)
        self.assertEqual(scaled["loaded slots"], 300)

    def test_legs_to_and_from_the_route_use_stock_before_and_after_it(self):
        # before and after: legs flown at their own rates, in order, before the route and after it
        # (Nauvis to the edge and back). Their use is loaded and counted, and their time and deaths,
        # and nothing comes in on them.
        from tools import trip

        leg = {self.FOUNDATION: 200.0, "km/s": 50.0, "deaths": 0.001}
        plain = trip.trip(self.flat(), 0.1, slots=1000, length=1e6)
        legs = trip.trip(self.flat(), 0.1, slots=1000, length=1e6, before=[(10000, leg)], after=[(10000, leg)])
        self.assertEqual(legs["km"], plain["km"] + 20000)
        self.assertAlmostEqual(legs["used"][self.FOUNDATION], plain["used"][self.FOUNDATION] + 4000)
        self.assertAlmostEqual(legs["hours"], plain["hours"] + 20000 / 50 / 3600)
        self.assertAlmostEqual(legs["comes back"], math.exp(-0.001 * 20))
        self.assertEqual(legs["loaded slots"], plain["loaded slots"] + 40)
        # A load that covers the route alone runs out on it: the leg out took its share first.
        short = trip.trip(self.flat(), 0.1, slots=1000, length=1e6, stock={self.FOUNDATION: 20000},
                          before=[(10000, leg)], after=[(10000, leg)])
        item, km, share = short["ran out"]
        self.assertEqual(item, self.FOUNDATION)
        self.assertAlmostEqual(km, 10000 + 180000, delta=1000)
        self.assertGreater(share, 0)

    def test_legs_come_from_a_legs_result_before_and_after_the_route(self):
        # trip.legs: a platform-legs result's leg rates, the ones out before the route and the ones
        # home after it, as (km, row) in the order flown; the route itself is the model's own.
        from tools import trip

        rates = [{"part": "out", "from": "nauvis", "to": "aquilo", "km": 45000, "row": {"km/s": 50.0}},
                 {"part": "route", "from": "solar-system-edge", "to": "solar-system-edge", "km": 160000, "row": {}},
                 {"part": "home", "from": "solar-system-edge", "to": "aquilo", "km": 100000, "row": {"km/s": 70.0}},
                 {"part": "home", "from": "aquilo", "to": "nauvis", "km": 45000, "row": {"km/s": 60.0}}]
        before, after = trip.legs({"leg rates": rates})
        self.assertEqual(before, [(45000, {"km/s": 50.0})])
        self.assertEqual(after, [(100000, {"km/s": 70.0}), (45000, {"km/s": 60.0})])

    def test_a_trip_that_needs_more_than_the_hub_holds_does_not_fit(self):
        from tools import trip

        t = trip.trip(self.flat(), 0.1, slots=150, length=1e6)
        self.assertFalse(t["fits"])
        self.assertEqual((t["loaded slots"], t["largest"]), (200, self.FOUNDATION))

    def test_an_item_used_only_further_out_is_loaded_for_there(self):
        # A part the hub rebuilt only in some flights (a cryogenic plant at 0.4): nothing
        # of it between starts where none was used.
        from tools import trip

        rates = {0.0: {self.FOUNDATION: 100.0, "km/s": 40.0}, 0.2: {self.FOUNDATION: 100.0, "km/s": 40.0},
                 0.4: {self.FOUNDATION: 100.0, "cryogenic-plant": 1.0, "km/s": 40.0}}
        t = trip.trip(rates, 0.4, slots=10000, length=1e6)
        self.assertAlmostEqual(t["used"]["cryogenic-plant"], 2 * 200 * 0.5, delta=1)  # both ways, rising 0 to 1 from 0.2

    def test_a_given_stock_runs_out_where_it_runs_out(self):
        from tools import trip

        t = trip.trip(self.flat(chunks=0.0), 0.1, slots=1000, stock={self.FOUNDATION: 5000}, length=1e6)
        item, km, share = t["ran out"]
        self.assertEqual((item, km), (self.FOUNDATION, 51000.0))  # 50 stacks last 50,000 km
        self.assertAlmostEqual(share, 0.0505)

    def test_best_turns_round_where_the_most_chunks_come_back_and_fit(self):
        # Chunks only past 0.2, foundation dearer further out: 0.3 brings more than 0.2,
        # and 0.4 needs more slots than the hub has.
        from tools import trip

        rates = {0.0: {self.FOUNDATION: 100.0, self.CHUNK: 0.0, "km/s": 40.0},
                 0.2: {self.FOUNDATION: 100.0, self.CHUNK: 0.0, "km/s": 40.0},
                 0.4: {self.FOUNDATION: 300.0, self.CHUNK: 40.0, "km/s": 40.0}}
        best, rows = trip.best(rates, 1000, [0.1, 0.2, 0.3, 0.4], length=1e6)
        self.assertEqual([r["turn"] for r in rows], [0.1, 0.2, 0.3, 0.4])
        self.assertFalse(rows[-1]["fits"])
        self.assertEqual(best["turn"], 0.3)
        self.assertGreater(best["chunks"], rows[1]["chunks"])

    def test_the_command_reads_a_route_result_and_names_the_best_trip(self):
        import contextlib
        import io

        from tools import trip

        rates = {"rebuilt space-platform-foundation/1000 km": 100.0, "taken promethium-asteroid-chunk/1000 km": 5.0,
                 "cruise km/s": 40.0, "km_mean": 20000.0, "survived": 1.0}
        result = {"trials": [{"start": s, "mode": "out", "passed": True, "rates": rates} for s in (0.0, 0.1)]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route.json"
            path.write_text(json.dumps(result))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(trip.main([str(path), "--bays", "8"]), 0)
        self.assertIn("8 cargo bays (219 slots)", out.getvalue())
        self.assertIn("best: turn", out.getvalue())

    def test_slots_per_1000_km_are_each_item_over_its_stack(self):
        # What the platform fills the hub with per 1,000 km: foundation at 100 a stack, railgun
        # ammo at 10 (dump). Chunks are what it brings in, not what it uses.
        from tools import trip

        row = {self.FOUNDATION: 250.0, "railgun-ammo": 15.0, self.CHUNK: 9.0, "km/s": 40.0, "deaths": 0.01}
        self.assertEqual(trip.slots(row), {self.FOUNDATION: 2.5, "railgun-ammo": 1.5})

    def test_the_command_plans_from_any_studys_mode(self):
        # A study comparing designs (platform-mix) names its rows by design: --mode picks one.
        import contextlib
        import io

        from tools import trip

        rates = {"rebuilt space-platform-foundation/1000 km": 100.0, "taken promethium-asteroid-chunk/1000 km": 5.0,
                 "cruise km/s": 40.0, "km_mean": 20000.0, "survived": 1.0}
        result = {"trials": [{"start": s, "mode": "narrow", "passed": True, "rates": rates} for s in (0.0, 0.1)]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mix.json"
            path.write_text(json.dumps(result))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(trip.main([str(path), "--mode", "narrow"]), 0)
        self.assertIn("best: turn", out.getvalue())

    def whole_trip(self):
        # platform-whole-trip: two flights out to 0.02 and back; one came back with 300 chunks, one
        # never did (stranded, its hub alive) with 100 aboard. 200,000 km and 4,000 s in all.
        flight = {"back": True, "died at": None, "km": 160_000, "seconds": 3_000, "chunks aboard": 300,
                  "left": {self.FOUNDATION: 1_400, "railgun-ammo": 0}}
        died = {"back": False, "died at": None, "km": 40_000, "seconds": 1_000, "chunks aboard": 100,
                "left": {self.FOUNDATION: 1_800, "railgun-ammo": 3_000}}
        return {"turn": 0.02, "supplies": {self.FOUNDATION: 2_000, "railgun-ammo": 4_000}, "flights": [flight, died]}

    def test_a_whole_trip_flown_is_one_more_measured_row(self):
        # Out to turn and back, it flew as many km at each share of the route up to the turn, so
        # its rates stand at turn / 2: what the hub gave per 1,000 km over every flight, the chunks
        # brought in, km over seconds, and trips that didn't come back per 1,000 km.
        from tools import trip

        share, row = trip.measured(self.whole_trip())
        self.assertEqual(share, 0.01)
        self.assertEqual(row, {self.FOUNDATION: 4.0, "railgun-ammo": 25.0, self.CHUNK: 2.0, "km/s": 50.0,
                               "deaths": 0.005})  # (600 + 200), (4,000 + 1,000), 400 chunks, 1 hub per 200,000 km

    def test_the_command_folds_in_a_whole_trip(self):
        import contextlib
        import io

        from tools import trip

        rates = {"rebuilt space-platform-foundation/1000 km": 100.0, "taken promethium-asteroid-chunk/1000 km": 5.0,
                 "cruise km/s": 40.0, "km_mean": 20000.0, "survived": 1.0}
        result = {"trials": [{"start": s, "mode": "out", "passed": True, "rates": rates} for s in (0.0, 0.1)]}
        with tempfile.TemporaryDirectory() as tmp:
            path, whole = Path(tmp) / "route.json", Path(tmp) / "whole.json"
            path.write_text(json.dumps(result))
            whole.write_text(json.dumps(self.whole_trip()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(trip.main([str(path), "--whole-trip", str(whole)]), 0)
        self.assertIn("with a whole trip measured at 0.01", out.getvalue())
        self.assertIn("best: turn", out.getvalue())

    def test_the_command_plans_with_the_legs_to_the_route_and_home(self):
        import contextlib
        import io

        from tools import trip

        rates = {"rebuilt space-platform-foundation/1000 km": 100.0, "taken promethium-asteroid-chunk/1000 km": 5.0,
                 "cruise km/s": 40.0, "km_mean": 20000.0, "survived": 1.0}
        result = {"trials": [{"start": s, "mode": "out", "passed": True, "rates": rates} for s in (0.0, 0.1)]}
        leg = {self.FOUNDATION: 50.0, "km/s": 50.0, "deaths": 0.0}
        legs = {"leg rates": [{"part": "out", "from": "nauvis", "to": "solar-system-edge", "km": 145000, "row": leg},
                              {"part": "home", "from": "solar-system-edge", "to": "nauvis", "km": 145000, "row": leg}]}
        with tempfile.TemporaryDirectory() as tmp:
            path, legs_path = Path(tmp) / "route.json", Path(tmp) / "legs.json"
            path.write_text(json.dumps(result))
            legs_path.write_text(json.dumps(legs))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(trip.main([str(path), "--legs", str(legs_path)]), 0)
        self.assertIn("with 290,000 km of legs to the route and home", out.getvalue())
        self.assertIn("best: turn", out.getvalue())


if __name__ == "__main__":
    unittest.main()
