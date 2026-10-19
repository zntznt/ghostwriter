"""Run: python -m unittest discover tests"""

import copy
import json
import math
import tempfile
import unittest
import warnings
from collections import Counter
from pathlib import Path

from draftsman.blueprintable import Blueprint, BlueprintBook, DeconstructionPlanner, UpgradePlanner

from generators import assembly_row, factory, fluid_block, fluid_row, production, smelting_column
from generators.gated_requester import build
from tools import rates
from tools.check import check
from tools.codec import CodecError, decode, encode, root_key
from tools.library import entries, load_book, save
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


VEC = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def pipe_networks(bp: dict) -> dict:
    """Tile -> network id for every pipe and pipe-to-ground, joined the way the game
    joins them: pipes to any neighbour that connects back, pipe-to-ground by its
    one surface side and to the first opposite-facing one underground (10 tiles)."""
    tiles = {}
    for e in bp["entities"]:
        if e["name"] in ("pipe", "pipe-to-ground"):
            tiles[(int(e["position"]["x"] // 1), int(e["position"]["y"] // 1))] = (e["name"], e.get("direction", 0))

    def opens(tile, toward):  # does the thing at tile have a normal connection toward this side?
        name, d = tiles[tile]
        return name == "pipe" or VEC[d] == toward

    group = {t: t for t in tiles}

    def root(t):
        while group[t] != t:
            t = group[t]
        return t

    for (x, y), (name, d) in tiles.items():
        sides = VEC.values() if name == "pipe" else [VEC[d]]
        for dx, dy in sides:
            n = (x + dx, y + dy)
            if n in tiles and opens(n, (-dx, -dy)):
                group[root((x, y))] = root(n)
        if name == "pipe-to-ground":
            dx, dy = VEC[d]
            for k in range(1, 12):
                n = (x - dx * k, y - dy * k)
                if n in tiles and tiles[n] == ("pipe-to-ground", (d + 8) % 16):
                    group[root((x, y))] = root(n)
                    break
    return {t: root(t) for t in tiles}


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

    def test_flow_test_feeds_the_bottom_and_drains_the_top(self):
        for params in FACTORIES[:2]:
            bp, p, _ = factory_built(**params)
            ids = {e.id for e in bp.entities if e.id}
            (flow,) = factory.flow_tests(**params)
            named = {f.entity for f in flow.feed} | set(flow.drain) | {f.entity for f in flow.fluids}
            self.assertLessEqual(named, ids, params)
            self.assertEqual(flow.drain, [f"out {ln.name} {ln.column}" for ln in p["lines"][params["item"]]])
            self.assertEqual({f.item for f in flow.feed}, {ln.name for named in p["lines"].values()
                                                           for ln in named if ln.supplied and not ln.fluid})

    def test_description_fits_the_game(self):
        for params in FACTORIES:
            _, _, bp = factory_built(**params)
            self.assertLessEqual(len(bp["description"].encode()), 500, params)


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
            if path.name != "_book.json":
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

    def test_plain_folder_becomes_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "my-book"
            save(generated(), folder)
            save(generated(), folder)
            book = load_book(folder)["blueprint_book"]
            self.assertEqual(book["item"], "blueprint-book")  # the game's item name
            self.assertEqual(book["label"], "my-book")
            self.assertEqual([c["index"] for c in book["blueprints"]], [0, 1])


if __name__ == "__main__":
    unittest.main()
