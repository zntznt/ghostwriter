"""In-game validation tests. Need a Factorio executable (see tools/factorio.py);
skipped without one. One server for the whole class, ~30 s."""

import dataclasses
import json
import os
import unittest
from pathlib import Path

from draftsman.blueprintable import Blueprint
from draftsman.entity import new_entity

from tests.test_tools import GAME_21, HANDOFF, generated, make_book
from generators import (assembly_row, factory, fluid_block, fluid_row, gated_requester, platform, production,
                        smelting_column)
from tools.codec import decode, encode
from tools import rates
from tools.factorio import locate
from tools.library import entries
from tools.settings import game_settings, registry
from validator.circuit import Case, Expect, Inject, run_circuit_tests
from validator.flight import Flight, _step, run_flight
from validator.flow import Flow, run_flow_tests
from validator.tests_file import library_tests
from validator.server import Server
from validator.validate import Report, validate

# Draftsman 4.0.1 derives a 3x4 footprint for the railgun turret from its
# collision box; the 2.1.22 prototype says tile_height = 5 (NOTES.md). This is
# the centre the game uses for tile_position (0, 0).
DRAFTSMAN_FOOTPRINT_BUGS = {"railgun-turret": (1.5, 2.5)}

FIXTURES_21 = GAME_21[0].parent

# Entity numbers of the gated requester, in the generator and the handoff string alike.
GATED_IDS = {"chest": 1, "decider": 2, "requests": 3}


# CI sets GHOSTWRITER_REQUIRE_FACTORIO so a missing server fails instead of skipping.
@unittest.skipUnless(locate() or os.environ.get("GHOSTWRITER_REQUIRE_FACTORIO"),
                     "no Factorio executable (run `python -m validator.fetch`)")
class Validator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    def run_validate(self, data):
        return validate(self.server, data)

    def assertPasses(self, data):
        report = self.run_validate(data)
        self.assertTrue(report.ok, report.lines())
        return report

    def assertFailsWith(self, data, text):
        report = self.run_validate(data)
        self.assertFalse(report.ok)
        self.assertTrue(any(text in e for e in report.errors), report.lines())

    # Good inputs pass.

    def test_generator(self):
        self.assertEqual(self.assertPasses(generated()).warnings, [])

    def test_handoff_example(self):
        self.assertPasses(decode(HANDOFF))

    def test_book_with_planners(self):
        self.assertPasses(make_book())

    def test_game_exports_round_trip(self):
        for path in GAME_21:
            with self.subTest(path.stem):
                self.assertPasses(decode(path.read_text()))

    def test_library_validates(self):
        # Everything the site publishes: books and loose top-level files.
        for name, data in entries(Path(__file__).parent.parent / "library"):
            with self.subTest(name):
                self.assertPasses(data)

    def test_library_tests_pass(self):
        # Logic and throughput of every library blueprint with a .tests.toml,
        # before it is published.
        for label, data, tests in library_tests(Path(__file__).parent.parent / "library"):
            with self.subTest(label):
                report = Report(self.server.version)
                if tests.cases:
                    run_circuit_tests(self.server, data, tests.cases, tests.ids, report)
                if tests.flows:
                    run_flow_tests(self.server, data, tests.flows, tests.ids, report)
                self.assertTrue(report.ok, report.lines())

    def test_game_settings_registry_round_trips(self):
        # Every harvested setting, set through game_settings() on its example
        # entity, must survive import, re-export and building in the game.
        reg = registry()
        self.assertEqual(reg["game_version"], self.server.version,
                         "registry is for another game version: run python -m validator.harvest")
        for ptype, entry in reg["types"].items():
            for name in entry["examples"]:
                with self.subTest(name):
                    settings = {path: s["observed"] for path, s in entry["settings"].items()
                                if name in s["entities"]}
                    bp = Blueprint()
                    if name in DRAFTSMAN_FOOTPRINT_BUGS:
                        entity = new_entity(name, position=DRAFTSMAN_FOOTPRINT_BUGS[name])
                    else:
                        entity = new_entity(name, tile_position=(0, 0))
                    game_settings(entity, settings)
                    bp.entities.append(entity)
                    report = validate(self.server, bp.to_dict())
                    self.assertTrue(report.ok, report.lines())
                    # Only an empty else_outputs may be dropped (it is the default).
                    self.assertFalse([w for w in report.warnings if "dropped" in w and "else_outputs" not in w],
                                     report.lines())

    def test_site_is_prepared_for_each_entity(self):
        # Each needs something plain concrete on a default surface lacks; all
        # must now build and match, with no warnings.
        needs = {
            "thruster": "pressure 0, platform foundation, empty space behind",
            "asteroid-collector": "pressure 0, empty space in front",
            "crusher": "gravity 0",
            "offshore-pump": "water in front, ground under it",
            "pumpjack": "crude oil under it",
            "electric-mining-drill": "ore under it",
            "lightning-rod": "the real Fulgora surface",
            "train-on-track": "rails built before the rolling stock",
            "train-stop": "a random backer name is not a difference",
            "construction-robot": "tiles compared as a set, not in order",
        }
        for name, need in needs.items():
            with self.subTest(name, need=need):
                report = self.assertPasses(decode((FIXTURES_21 / f"{name}.txt").read_text()))
                self.assertEqual(report.warnings, [])

    def test_unbuildable_entity_is_a_warning(self):
        # A lone elevated curved rail has no rail supports: not buildable in
        # the real game either, so it is reported, not hidden.
        report = self.assertPasses(decode((FIXTURES_21 / "elevated-curved-rail-a.txt").read_text()))
        self.assertTrue(any("not buildable" in w for w in report.warnings), report.lines())

    def test_all_logistic_modes_migrate(self):
        for mode in (0, 1, 2):
            with self.subTest(mode):
                data = generated()
                data["blueprint"]["entities"][0]["control_behavior"] = {"circuit_mode_of_operation": mode}
                self.assertPasses(data)

    # Broken inputs fail, with the reason.

    def test_misspelled_key(self):
        data = generated()
        cond = data["blueprint"]["entities"][1]["control_behavior"]["decider_conditions"]["conditions"][0]
        cond["first_signal_network"] = cond.pop("first_signal_networks")
        self.assertFailsWith(data, "first_signal_network: dropped")

    def test_invalid_value(self):
        data = generated()
        data["blueprint"]["entities"][0]["control_behavior"] = {"circuit_mode_of_operation": 7}
        self.assertFailsWith(data, "circuit_mode_of_operation: dropped")

    def test_wire_out_of_reach(self):
        data = generated()
        data["blueprint"]["entities"][2]["position"] = {"x": 40.5, "y": 2.5}
        self.assertFailsWith(data, "wire missing in game")

    def test_output_connector_on_chest(self):
        data = generated()
        data["blueprint"]["wires"].append([1, 3, 2, 2])
        self.assertFailsWith(data, "wire missing in game")

    def test_overlap(self):
        data = generated()
        data["blueprint"]["entities"][2]["position"] = {"x": 0.5, "y": 1.5}
        self.assertFailsWith(data, "collides with the rest of the blueprint")

    def test_unknown_entity(self):
        data = generated()
        data["blueprint"]["entities"][2]["name"] = "constant-combinatorr"
        self.assertFailsWith(data, "import_stack returned -1")

    # Circuit behaviour.

    def run_circuit(self, data, cases):
        report = Report(self.server.version)
        run_circuit_tests(self.server, data, cases, GATED_IDS, report)
        return report

    def test_gated_requester_logic(self):
        report = self.run_circuit(generated(), gated_requester.circuit_tests())
        self.assertTrue(report.ok, report.lines())

    def test_handoff_example_logic(self):
        report = self.run_circuit(decode(HANDOFF), gated_requester.circuit_tests())
        self.assertTrue(report.ok, report.lines())

    def test_wrong_expectation_fails(self):
        case = Case("wrong", [Inject("decider", "combinator_input_green", {"iron-plate": 150})],
                    [Expect("chest", requests={"iron-plate": 99})])
        report = self.run_circuit(generated(), [case])
        self.assertTrue(any("expected {'iron-plate': 99}" in e for e in report.errors), report.lines())

    def test_broken_logic_passes_validation_but_fails_circuit(self):
        data = generated()
        data["blueprint"]["entities"][1]["control_behavior"]["decider_conditions"]["conditions"][0]["comparator"] = "<"
        self.assertPasses(data)
        self.assertFalse(self.run_circuit(data, gated_requester.circuit_tests()).ok)


    # Flow: belts and machines running, powered through the blueprint's own poles.

    def run_flow(self, data, flows, ids):
        report = Report(self.server.version)
        run_flow_tests(self.server, data, flows, ids, report)
        return report

    def column(self, **params):
        bp = smelting_column.build(**params)
        return bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}

    def test_smelting_columns_reach_rated_output(self):
        # Burner furnaces fed ore + coal; electric ones ore on both lanes; steel
        # needs its recipe unlocked, which flow tests do for the run only.
        for params in ({}, {"furnace": "electric-furnace", "pole": "small-electric-pole"},
                       {"furnace": "steel-furnace", "recipe": "steel-plate", "furnaces": 4}):
            data, ids = self.column(**params)
            self.assertPasses(data)
            report = self.run_flow(data, smelting_column.flow_tests(**params), ids)
            self.assertTrue(report.ok, (params, report.lines()))
        recipes = self.server.command(
            '/sc local r = game.forces.player.recipes rcon.print(tostring(r["steel-plate"].enabled))')
        self.assertEqual(recipes.strip(), "false")

    def test_assembly_rows_reach_rated_output(self):
        # One per layout feature: two fast inserters, two ingredients on one
        # belt, a second belt through long-handed inserters, poles in gap rows.
        for params in ({}, {"recipe": "electronic-circuit", "machine": "assembling-machine-1"},
                       {"recipe": "inserter", "machine": "assembling-machine-1", "machines": 3},
                       {"recipe": "electronic-circuit"}):
            bp = assembly_row.build(**params)
            data, ids = bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
            self.assertPasses(data)
            report = self.run_flow(data, assembly_row.flow_tests(**params), ids)
            self.assertTrue(report.ok, (params, report.lines()))

    def test_production_book_rows_reach_their_rates(self):
        # Smelting columns and assembly rows from the solver, each distinct row
        # flow-tested on its own, and the book as a whole validated.
        params = {"item": "electronic-circuit", "rate": 5, "smelt": 1}
        self.assertPasses(production.build(**params).to_dict())
        seen = set()
        for name, part_params in production.parts(**params):
            key = (name, tuple(sorted(part_params.items())))
            if key in seen:
                continue
            seen.add(key)
            gen = production.GENERATORS[name]
            bp = gen.build(**part_params)
            ids = {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
            report = self.run_flow(bp.to_dict(), gen.flow_tests(**part_params), ids)
            self.assertTrue(report.ok, (name, part_params, report.lines()))

    def test_production_chain_reaches_its_rate(self):
        # The whole book as one: each row's products feed the rows that use them.
        data, flows = production.chain_tests(item="electronic-circuit", rate=5)
        self.assertPasses(data)
        report = self.run_flow(data, flows, {})
        self.assertTrue(report.ok, report.lines())

    def test_production_chain_missing_a_row_fails(self):
        # One copper-cable row short: the circuits starve below the target.
        from dataclasses import replace
        from unittest import mock

        solve = production.solve

        def short(*args, **kwargs):
            steps, inputs = solve(*args, **kwargs)
            return [replace(s, rows=s.rows - 1) if s.item == "copper-cable" else s for s in steps], inputs

        with mock.patch.object(production, "solve", short):
            data, flows = production.chain_tests(item="electronic-circuit", rate=5)
        report = self.run_flow(data, flows, {})
        self.assertFalse(report.ok)
        self.assertTrue(any("electronic-circuit" in e for e in report.errors), report.lines())

    def test_factory_reaches_its_rate(self):
        # A whole book on a bus: only inputs fed at the bottom, the product drained at the top.
        for params in ({"item": "electronic-circuit", "rate": 5}, {"item": "advanced-circuit", "rate": 1, "oil": 1}):
            bp = factory.build(**params)
            data, ids = bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
            self.assertPasses(data)
            report = self.run_flow(data, factory.flow_tests(**params), ids)
            self.assertTrue(report.ok, (params, report.lines()))

    def test_factory_with_a_cut_line_fails(self):
        # Without the first splitter on the copper line, nothing north of it gets copper.
        bp = factory.build(item="electronic-circuit", rate=5)
        data, ids = bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
        cut = next(e for e in data["blueprint"]["entities"] if e["name"] == "splitter")
        data["blueprint"]["entities"].remove(cut)
        report = self.run_flow(data, factory.flow_tests(item="electronic-circuit", rate=5), ids)
        self.assertTrue(any("electronic-circuit" in e for e in report.errors), report.lines())

    def test_fluid_rows_reach_rated_output(self):
        # Chemical plants and assemblers turned to the middle pipe, fed by an
        # infinity pipe at its end; a long-handed and a near-lane ingredient.
        for params in ({}, {"recipe": "battery", "machines": 3}, {"recipe": "processing-unit", "machines": 2},
                       {"recipe": "explosives", "machines": 4, "sides": 1}):
            bp = fluid_row.build(**params)
            data, ids = bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
            self.assertPasses(data)
            report = self.run_flow(data, fluid_row.flow_tests(**params), ids)
            self.assertTrue(report.ok, (params, report.lines()))

    def test_fluid_blocks_reach_rated_output(self):
        # Fluid made is counted from production statistics, items at the belts.
        for params in ({"machines": 2}, {"recipe": "advanced-oil-processing", "machines": 2},
                       {"recipe": "sulfur", "machines": 3}, {"recipe": "sulfuric-acid", "machines": 2}):
            bp = fluid_block.build(**params)
            data, ids = bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
            self.assertPasses(data)
            report = self.run_flow(data, fluid_block.flow_tests(**params), ids)
            self.assertTrue(report.ok, (params, report.lines()))

    def test_fluids_in_the_wrong_lines_fail(self):
        # Water and petroleum gas swapped: the game fills input boxes in recipe order.
        import dataclasses

        bp = fluid_block.build(recipe="sulfur", machines=2)
        ids = {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
        swap = {"water": "petroleum-gas", "petroleum-gas": "water"}
        flows = [dataclasses.replace(f, fluids=[dataclasses.replace(x, fluid=swap[x.fluid]) for x in f.fluids])
                 for f in fluid_block.flow_tests(recipe="sulfur", machines=2)]
        report = self.run_flow(bp.to_dict(), flows, ids)
        self.assertTrue(any("fluid_ingredient_shortage" in e for e in report.errors), report.lines())

    def test_fluid_row_without_turned_machines_fails(self):
        bp = fluid_row.build(machines=1)
        data, ids = bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
        for e in data["blueprint"]["entities"]:
            if e["name"] == "chemical-plant":
                e.pop("direction", None)  # inputs face north, away from the pipe
        report = self.run_flow(data, fluid_row.flow_tests(machines=1), ids)
        self.assertTrue(any("fluid_ingredient_shortage" in e for e in report.errors), report.lines())

    def test_game_cuts_long_text(self):
        # tools/check.py TEXT_LIMITS: bytes of UTF-8 the game keeps.
        from tools.check import TEXT_LIMITS

        for key, limit in TEXT_LIMITS.items():
            data = generated()
            data["blueprint"][key] = "é" * limit  # twice the limit in bytes
            kept = decode(self.server.call("validate", encode(data))["export"])
            self.assertEqual(len(kept["blueprint"][key].encode()), limit, key)

    def test_inserter_rates_were_measured_on_this_version(self):
        # Rows are sized from tools/inserter_rates.json; re-measure after a game
        # update (python -m validator.measure).
        version = json.loads(rates.INSERTER_RATES.read_text())["game_version"]
        self.assertEqual(version, self.server.version)

    def test_unpowered_column_fails_flow(self):
        # Passes validation (everything builds) but nothing runs without poles.
        data, ids = self.column(furnaces=2)
        data["blueprint"]["entities"] = [e for e in data["blueprint"]["entities"] if "pole" not in e["name"]]
        del data["blueprint"]["wires"]
        self.assertPasses(data)
        report = self.run_flow(data, smelting_column.flow_tests(furnaces=2), ids)
        self.assertTrue(any("no_power" in e for e in report.errors), report.lines())

    def thrusters(self, behind=0):
        """Two thrusters south of the hub on foundation, and `behind` rows of it behind them."""
        bp = Blueprint()
        for x in range(-12, 12):
            for y in range(5, 13 + behind):
                bp.tiles.append("space-platform-foundation", position=(x, y))
        for cx in (-6, 6):
            bp.entities.append("thruster", position=(cx, 10.5))
        return bp.to_dict()

    def test_platform_flies_until_an_asteroid_kills_the_hub(self):
        # Fuelled by script and undefended: it leaves the solar system edge on its
        # own thrust and a huge asteroid soon destroys the hub.
        report = Report(self.server.version)
        flown = run_flight(self.server, self.thrusters(), Flight("undefended", seconds=120, fuel=True), report)
        self.assertEqual((flown.built, flown.entities), (2, 2), report.lines())
        self.assertGreater(flown.km, 500)
        self.assertGreater(flown.speed_max, 1)
        self.assertFalse(flown.survived)
        self.assertRegex(flown.ended, r"hub destroyed by huge-\w+-asteroid")
        self.assertTrue(any(k.startswith("space-platform-hub <- ") for k in flown.deaths), flown.deaths)

    def test_flight_research_is_set_and_undone(self):
        # Levels researched set the game's own modifiers: railgun damage +0.4 a level,
        # physical projectile damage 1-6 then 7-8 (+1.0, +0.2, +0.2) for bullets.
        def modifiers():
            return json.loads(self.server.command("/sc local f = game.forces.player rcon.print(helpers.table_to_json{"
                                                  "f.get_ammo_damage_modifier('railgun'),"
                                                  "f.get_ammo_damage_modifier('bullet')})"))

        def setup(research):
            return self.server.call("flight_setup", json.dumps({
                "blueprint": encode(self.thrusters()), "from": "solar-system-edge", "to": "shattered-planet",
                "research": research}))

        try:
            self.assertNotIn("error", setup({"railgun-damage": 3, "physical-projectile-damage": 8}))
            self.assertEqual([round(m, 6) for m in modifiers()], [1.2, 1.4])
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(modifiers(), [0, 0])
        self.assertEqual(setup({"railgun-damages": 1})["error"], "no technology series railgun-damages")
        self.assertEqual(setup({"weapon-shooting-speed": 7})["error"], "weapon-shooting-speed has 6 levels")
        self.assertEqual(modifiers(), [0, 0])
        # A refused flight leaves the game running for the tests after it.
        self.assertEqual(self.server.command("/sc rcon.print(tostring(game.tick_paused))").strip(), "false")

    def test_flight_can_start_partway(self):
        # 40% along the 4,000,000 km route, moving on toward the shattered planet.
        args = {"blueprint": encode(self.thrusters()), "from": "solar-system-edge", "to": "shattered-planet",
                "fuel": True, "speed": 100}
        try:
            self.assertAlmostEqual(self.server.call("flight_setup", json.dumps({**args, "start": 0.4}))["distance"],
                                   0.4)
            for _ in range(5):
                _step(self.server, 60, timeout=60)
            got = self.server.call("flight_read")
            self.assertGreater(got["furthest"], 0.4)
            self.assertAlmostEqual(got["km"], (got["furthest"] - 0.4) * 4_000_000, delta=1)
        finally:
            self.server.call("flight_teardown")
        self.assertIn("start must be", self.server.call("flight_setup", json.dumps({**args, "start": 1}))["error"])

    def test_flight_power_source_cannot_be_destroyed(self):
        # It stands off the platform; asteroids destroyed it in every flight 40% out,
        # cutting the turrets' power.
        data = platform.build(railguns=1, guns=0, thrusters=1).to_dict()
        try:
            self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "solar-system-edge", "to": "shattered-planet", "power": True}))
            got = json.loads(self.server.command(
                "/sc local s = storage.flight.platform.surface local out = {} "
                "for _, e in pairs(s.find_entities_filtered{name = {'electric-energy-interface', "
                "'medium-electric-pole'}}) do out[#out + 1] = e.destructible end "
                "rcon.print(helpers.table_to_json(out))"))
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(sorted(got), [False, False, True])  # the source and its pole; the design's own pole

    def test_thrusters_need_empty_space_behind(self):
        # A thruster's exhaust needs empty space behind it (about 88 tiles):
        # with foundation there, the game won't build it, and the platform goes nowhere.
        report = Report(self.server.version)
        flown = run_flight(self.server, self.thrusters(behind=4), Flight("blocked", seconds=10, fuel=True), report)
        self.assertEqual(flown.built, 0)
        self.assertTrue(any("built 0 of 2" in w for w in report.warnings), report.lines())
        self.assertEqual(flown.km, 0)

    def test_flow_expectation_is_a_rate(self):
        data, ids = self.column(furnaces=2)
        [flow] = smelting_column.flow_tests(furnaces=2)
        report = self.run_flow(data, [Flow("too much", flow.feed, flow.drain, {"iron-plate": 2.0})], ids)
        self.assertTrue(any("expected 2/s" in e for e in report.errors), report.lines())

    def test_flow_tests_return_what_they_measured(self):
        # Two stone furnaces per side make 4 x 0.3125 = 1.25 plates/s; every window is kept.
        data, ids = self.column(furnaces=2)
        [flow] = smelting_column.flow_tests(furnaces=2)
        report = Report(self.server.version)
        [ok] = run_flow_tests(self.server, data, [flow], ids, report)
        self.assertEqual(ok.passed, 1)
        self.assertAlmostEqual(ok.rate("iron-plate"), 1.25, delta=0.03 * 1.25)
        [short] = run_flow_tests(self.server, data, [dataclasses.replace(flow, expect={"iron-plate": 2.0}, settle=2)],
                                 ids, report)
        self.assertIsNone(short.passed)
        self.assertEqual(len(short.windows), 2)
        self.assertAlmostEqual(short.rate("iron-plate"), 1.25, delta=0.03 * 1.25)


if __name__ == "__main__":
    unittest.main()
