"""In-game validation tests. Need a Factorio executable (see tools/factorio.py);
skipped without one. One server for the whole class, ~30 s."""

import dataclasses
import json
import math
import os
import unittest
from pathlib import Path

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
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
from validator.flight import Flight, _step, run_flight, start_flight
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
        # It survived once in CI (0 of 40 local flights lived past 42 s): show the flight if it does again.
        self.assertFalse(flown.survived, {"log": flown.log, "deaths": flown.deaths, "ended": flown.ended})
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

    def test_a_flight_turns_round_and_comes_back(self):
        # turn: at that share of the route the schedule goes back to the origin, which turns a
        # platform round within a tick and keeps its speed (NOTES.md); the flight ends when it is
        # back. Nauvis toward Vulcanus, 15,000 km: from 0.3 out to 0.45 and back is 9,000 km.
        data = platform.build(railguns=1, guns=2, thrusters=1).to_dict()
        flight = Flight("round trip", origin="nauvis", destination="vulcanus", start=0.3, turn=0.45, seconds=300,
                        fuel=True, power=True, ammo=platform.AMMO)
        report = Report(self.server.version)
        flown = run_flight(self.server, data, flight, report)
        self.assertEqual(report.errors, [])
        self.assertTrue(flown.survived, flown.ended)
        self.assertGreater(flown.back, flown.turned)
        self.assertGreater(flown.turned, 0)
        self.assertEqual(flown.position, 0.0)  # at the origin
        self.assertAlmostEqual(flown.km, 0.15 * 15000, delta=0.02 * 15000)  # furthest out, as before
        self.assertAlmostEqual(flown.odometer, 0.6 * 15000, delta=0.03 * 15000)
        self.assertEqual(flown.seconds, flown.back)
        self.assertAlmostEqual(flown.at_turn["km"], 0.15 * 15000, delta=0.02 * 15000)  # the two legs apart
        self.assertIsInstance(flown.at_turn["cargo"], dict)

    def test_an_item_runs_out_for_good_not_while_a_bay_has_spilled_it(self):
        # A cargo bay that dies spills its slots' items, and they come back when the hub rebuilds
        # it (NOTES.md): ran_out keeps only an item the hub has held none of since, here one taken
        # out by script and one taken out and put back.
        data = platform.build(railguns=1, guns=2, thrusters=1).to_dict()
        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "start": 0.3, "fuel": True,
                "power": True, "supplies": {"iron-plate": 10, "copper-plate": 10}, "speed": 100}))
            self.assertNotIn("error", setup)
            hub = "storage.flight.platform.hub.get_inventory(defines.inventory.hub_main)"
            _step(self.server, 120, timeout=60)
            self.server.command(f"/sc {hub}.remove{{name = 'iron-plate', count = 10}} "
                                f"{hub}.remove{{name = 'copper-plate', count = 10}}")
            _step(self.server, 120, timeout=60)
            self.server.command(f"/sc {hub}.insert{{name = 'iron-plate', count = 10}}")
            _step(self.server, 120, timeout=60)
            got = self.server.call("flight_read")
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(sorted(got["ran_out"]), ["copper-plate"])

    def test_a_platform_readied_in_orbit_fills_its_feed_first(self):
        # fill: before leaving, in Nauvis orbit (chunks only, no asteroids), the hub feed fills its
        # belts and turrets while the script holds the hub's feed items at their stock, as a player
        # would top it up; the flight then leaves with the hub holding exactly that stock.
        data = platform.build(railguns=4, guns=6, thrusters=1, ammo_feed=True).to_dict()
        supplies = {"railgun-ammo": 500, "uranium-rounds-magazine": 500}
        hub = ("/sc local inv = storage.flight.platform.hub.get_inventory(defines.inventory.hub_main) "
               "rcon.print(helpers.table_to_json{inv.get_item_count('railgun-ammo'), "
               "inv.get_item_count('uranium-rounds-magazine')})")
        flight = Flight("readied", origin="nauvis", destination="vulcanus", start=0.3, seconds=1, fuel=True,
                        power=True, supplies=supplies, loaded=dict(platform.FED), feed=sorted(supplies), fill=True)
        try:
            setup, filled = start_flight(self.server, data, flight)
            self.assertNotIn("error", setup)
            left = json.loads(self.server.command(hub))
            where = self.server.call("flight_read")
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(left, [500, 500])  # topped up to the stock as the feed drew it
        self.assertGreaterEqual(filled["railgun-ammo"], 40)  # 4 railguns' 10, and the belts
        self.assertGreaterEqual(filled["uranium-rounds-magazine"], 60)  # 6 gun turrets' 10, and the belts
        self.assertEqual(where["ticks"], 0)  # the flight's clock starts as it leaves
        self.assertAlmostEqual(where["position"], 0.3)

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

    def test_flight_power_source_stands_behind_and_cannot_be_destroyed(self):
        # On the platform it took a free foundation tile among the turrets, where
        # asteroids destroyed it (in every flight 40% out), and a wide platform had
        # none, so setup failed. It stands on empty space behind instead, past the
        # thrusters' exhaust, and it powers the turrets from there.
        data = platform.build(railguns=10, guns=12, thrusters=1).to_dict()
        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "solar-system-edge", "to": "shattered-planet", "power": True}))
            self.assertNotIn("error", setup)
            _step(self.server, 60, timeout=60)
            got = json.loads(self.server.command(
                "/sc local fl = storage.flight local s = fl.platform.surface local back = 0 "
                "for _, t in pairs(s.find_tiles_filtered{name = 'space-platform-foundation'}) do "
                "back = math.max(back, t.position.y) end local out = {back = back, helpers = {}} "
                "for _, e in pairs(fl.helpers) do out.helpers[#out.helpers + 1] = {y = e.position.y, "
                "destructible = e.destructible, tile = s.get_tile(e.position).name} end "
                "out.energy = s.find_entities_filtered{name = 'railgun-turret', limit = 1}[1].energy "
                "out.global = s.has_global_electric_network "
                "rcon.print(helpers.table_to_json(out))"))
        finally:
            self.server.call("flight_teardown")
        # A platform's surface has a global electric network of its own: the source
        # powers every entity on it, whatever happens to the poles.
        self.assertIs(got["global"], True)
        self.assertEqual(len(got["helpers"]), 2)  # its pole and the source
        for h in got["helpers"]:
            self.assertFalse(h["destructible"])
            self.assertEqual(h["tile"], "empty-space")
            self.assertGreater(h["y"], got["back"] + 90)
        self.assertGreater(got["energy"], 0)

    def small_flight(self, research, supplies):
        data = platform.build(railguns=1, guns=2, thrusters=1).to_dict()
        setup = self.server.call("flight_setup", json.dumps({
            "blueprint": encode(data), "from": "solar-system-edge", "to": "shattered-planet", "fuel": True,
            "ammo": platform.AMMO, "research": research, "supplies": supplies}))
        self.assertNotIn("error", setup)

    def test_flight_counts_cargo_bays_lost_by_distance_from_the_hub(self):
        # Bays further out may be hit more, and a dead bay cuts off the ones beyond it
        # (NOTES.md), so the flight counts each lost bay by |x|, tiles from the hub's
        # centre. Killed by script: nothing waits on an asteroid or a sampling tick.
        data = platform.build(railguns=4, guns=6, thrusters=1, cargo_bays=8).to_dict()
        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "fuel": True,
                "ammo": platform.AMMO}))
            self.assertNotIn("error", setup)
            _step(self.server, 60, timeout=60)
            self.server.command("/sc for _, b in pairs(storage.flight.platform.surface.find_entities_filtered"
                                "{name = 'cargo-bay'}) do if b.position.x == 10 or b.position.x == -6 then b.die() end end")
            got = self.server.call("flight_read")
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(got["bays_lost"], {"6": 2, "10": 2})

    def ghost_on_death(self):
        return self.server.command("/sc rcon.print(tostring(game.forces.player.create_ghost_on_entity_death))").strip()

    def test_flight_rebuilds_from_the_hub_with_construction_robotics(self):
        # construction-robotics is the technology whose effect leaves a ghost when
        # an entity dies; the hub then rebuilds it from its own inventory, in
        # flight. The rebuilt turret is a new entity, and the flight stocks it too.
        gun = ("/sc local s = storage.flight.platform.surface local out = {} for _, g in pairs("
               "s.find_entities_filtered{name = 'gun-turret'}) do out[#out + 1] = {x = g.position.x, "
               "ammo = g.get_inventory(defines.inventory.turret_ammo).get_item_count('uranium-rounds-magazine')} end "
               "rcon.print(helpers.table_to_json(out))")
        try:
            self.small_flight({"construction-robotics": 1}, {"gun-turret": 1})
            self.assertEqual(self.ghost_on_death(), "true")
            _step(self.server, 60, timeout=60)
            before = json.loads(self.server.command(gun))
            self.server.command("/sc storage.flight.platform.surface.find_entities_filtered{name = 'gun-turret'}[1].die()")
            _step(self.server, 600, timeout=60)
            after = json.loads(self.server.command(gun))
            hub = self.server.call("flight_read")["cargo"]
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(sorted(g["x"] for g in after), sorted(g["x"] for g in before))
        self.assertTrue(all(g["ammo"] > 0 for g in after), after)
        self.assertNotIn("gun-turret", [c["name"] for c in hub])  # the spare went into the rebuild
        self.assertEqual(self.ghost_on_death(), "false")  # undone with the research
        try:
            self.small_flight({}, {"gun-turret": 1})
            self.assertEqual(self.ghost_on_death(), "false")
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(self.server.call("flight_setup", json.dumps({
            "blueprint": encode(self.thrusters()), "from": "solar-system-edge", "to": "shattered-planet",
            "research": {"construction-robotics": 2}}))["error"], "construction-robotics has 1 level")

    def test_hub_repairs_with_repair_packs(self):
        # With repair packs in the hub, damaged entities on the platform heal at
        # repair-pack speed 2 times the hub's platform_repair_speed_modifier 0.1:
        # 0.2 a tick, 12 a second, no research needed.
        health = ("/sc rcon.print(storage.flight.platform.surface.find_entities_filtered"
                  "{name = 'railgun-turret'}[1].health)")
        try:
            self.small_flight({}, {"repair-pack": 10})
            self.server.command("/sc storage.flight.platform.surface.find_entities_filtered"
                                "{name = 'railgun-turret'}[1].health = 3000")
            _step(self.server, 600, timeout=60)
            healed = float(self.server.command(health))
            # Construction robots can't help: a roboport needs pressure 10, and a platform has 0.
            roboport = self.server.command("/sc rcon.print(tostring(storage.flight.platform.surface.can_place_entity"
                                           "{name = 'roboport', position = {0, 30}, force = 'player'}))").strip()
        finally:
            self.server.call("flight_teardown")
        self.assertAlmostEqual(healed, 3000 + 10 * 12, delta=12)
        self.assertEqual(roboport, "false")

    def test_cargo_bays_add_hub_slots_while_joined(self):
        # A cargo bay adds 20 hub slots while it is joined to the hub, directly or
        # through other bays. Killing both bays beside the hub on the east cuts off
        # the two beyond them, and what the lost slots held spills onto the platform.
        # With construction-robotics and bays in stock, the hub rebuilds them, and
        # the slots and the spilled foundation come back.
        data = platform.build(railguns=1, guns=2, thrusters=1, cargo_bays=8).to_dict()
        read = ("/sc local p = storage.flight.platform local spilled = 0 for _, e in pairs(p.surface."
                "find_entities_filtered{name = 'item-on-ground'}) do spilled = spilled + e.stack.count end "
                "rcon.print(helpers.table_to_json{slots = #p.hub.get_inventory(defines.inventory.hub_main), "
                "spilled = spilled, foundation = p.hub.get_item_count('space-platform-foundation'), "
                "bays = p.hub.get_item_count('cargo-bay')})")
        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "solar-system-edge", "to": "shattered-planet",
                "research": {"construction-robotics": 1}}))
            self.assertEqual((setup["built"], setup["entities"]), (setup["entities"], len(data["blueprint"]["entities"])))
            # Bays first, so they sit in the first slot and not in the ones that go.
            self.server.command("/sc local hub = storage.flight.platform.hub hub.insert{name = 'cargo-bay', count = 2} "
                                "hub.insert{name = 'space-platform-foundation', count = 100000}")
            full = json.loads(self.server.command(read))
            self.server.command("/sc for _, b in pairs(storage.flight.platform.surface.find_entities_filtered"
                                "{name = 'cargo-bay'}) do if b.position.x == 6 then b.die() end end")
            cut = json.loads(self.server.command(read))
            _step(self.server, 600, timeout=60)
            back = json.loads(self.server.command(read))
        finally:
            self.server.call("flight_teardown")
        self.assertEqual(full, {"slots": 59 + 8 * 20, "spilled": 0, "foundation": 218 * 100, "bays": 2})
        self.assertEqual(cut, {"slots": 59 + 4 * 20, "spilled": 80 * 100, "foundation": 138 * 100, "bays": 2})
        self.assertEqual(back, {"slots": 59 + 8 * 20, "spilled": 0, "foundation": 218 * 100, "bays": 0})

    def test_platform_runs_on_its_own_fusion_power(self):
        # power=True with no stand-in source: readied as in orbit, the charged accumulator
        # starts the reactor, which then runs on its own generators' output, keeps its slot
        # topped up from the hub, and powers the railguns. Nauvis toward Vulcanus: few,
        # small asteroids. The generators' output is all burnt cell energy once running.
        data = platform.build(railguns=4, guns=6, thrusters=1, power=True).to_dict()
        [flight] = platform.flight_tests(power=True, supplies={"fusion-power-cell": 10})
        flight = dataclasses.replace(flight, origin="nauvis", destination="vulcanus", seconds=60)
        # Power is sampled every 60 ticks: start 1 tick before a sample, while the reactor
        # is still starting, the phase CI once hit by chance (a start isn't a shortage).
        self.server.command("/sc game.tick_paused = true")
        tick = int(self.server.command("/sc rcon.print(game.tick)").strip())
        if (59 - tick) % 60:
            _step(self.server, (59 - tick) % 60, timeout=60)
        report = Report(self.server.version)
        flown = run_flight(self.server, data, flight, report)
        self.assertEqual((flown.built, flown.entities), (flown.entities, len(data["blueprint"]["entities"])),
                         report.lines())
        self.assertTrue(flown.survived, flown.ended)
        self.assertGreater(flown.generated_j, 0)
        self.assertGreater(flown.generated_j, flown.plant_j)  # it makes more than it uses itself
        self.assertEqual(flown.brownout, 0)  # readied, the reactor is never short of power
        self.assertLess(flown.cargo.get("fusion-power-cell", 0), 10)  # the inserters took cells from the hub
        budget = flown.budget()
        self.assertAlmostEqual(budget["fusion cells/h"], flown.generated_j / 40e9 / 60 * 3600)

    def plant_flight(self, data, supplies, coolant=platform.COOLANT):
        setup = self.server.call("flight_setup", json.dumps({
            "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "fuel": True, "power": False,
            "ammo": platform.AMMO, "research": {"construction-robotics": 1, "cryogenic-plant": 1},
            "supplies": supplies, "charged": True, "coolant": coolant}))
        self.assertNotIn("error", setup)

    def test_a_rebuilt_reactor_gets_cells_and_restarts(self):
        # The hub rebuilds a reactor that dies with its fuel slot empty, and neither fills
        # it nor has the ghost ask for cells (NOTES.md): the far reactor's cells reach it
        # on the belt, under the near one. It restarts on the other reactor's power.
        data = platform.build(railguns=4, guns=6, thrusters=1, power=2).to_dict()
        far = ("storage.flight.platform.surface.find_entities_filtered{name = 'fusion-reactor', position = "
               f"{{{platform.REACTOR[0] - platform.PLANT_STEP}, {platform.REACTOR[1]}}}, radius = 1}}[1]")
        read = ("/sc local r = " + far + " local status = {} for k, v in pairs(defines.entity_status) do "
                "status[v] = k end rcon.print(helpers.table_to_json(r and {status = status[r.status], cells = "
                "r.get_inventory(defines.inventory.fuel).get_item_count('fusion-power-cell')} or {gone = true}))")
        try:
            self.plant_flight(data, {"fusion-reactor": 1, "fusion-power-cell": 20})
            _step(self.server, 300, timeout=60)
            self.server.command("/sc " + far + ".die()")
            _step(self.server, 900, timeout=60)
            got = json.loads(self.server.command(read).strip())
            hub = self.server.call("flight_read")["cargo"]
        finally:
            self.server.call("flight_teardown")
        self.assertIn(got.get("status"), ("working", "full_output"), got)
        self.assertGreater(got["cells"], 0)
        self.assertNotIn("fusion-reactor", [c["name"] for c in hub])  # the spare went into the rebuild

    def test_gun_turret_bullets_with_the_flight_research(self):
        # A bullet hits for its base damage x (1 + bullet damage) x (1 + gun-turret attack);
        # with the flight research both are +180%, so 7.84 x base. Piercing rounds (8,
        # dump) hit a small asteroid (100 health, no physical resistance) for 62.72, and a
        # medium one (10% physical) for 56.448; uranium rounds (24) kill a small one with
        # one bullet. An asteroid placed by script in front of one turret, the platform
        # standing in Nauvis orbit.
        from tools.studies import HIGH_RESEARCH

        data = platform.build(railguns=1, guns=2, thrusters=1).to_dict()
        place = ("/sc local s = storage.flight.platform.surface local t = s.find_entities_filtered"
                 "{name = 'gun-turret'}[1] local inv = t.get_inventory(defines.inventory.turret_ammo) inv.clear() "
                 "inv.insert{name = '%s', count = 10} storage.probe = s.create_entity{name = '%s', "
                 "position = {t.position.x, t.position.y - 10}} rcon.print(storage.probe.health)")
        health = "/sc rcon.print(storage.probe.valid and storage.probe.health or 0)"

        def first_hit(ammo, rock):
            full = float(self.server.command(place % (ammo, rock)))
            for _ in range(60):
                _step(self.server, 1, timeout=30)
                now = float(self.server.command(health))
                if now < full:
                    return round(full - now, 3)
            self.fail(f"{ammo} never hit {rock}")

        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "power": True,
                "research": HIGH_RESEARCH}))
            self.assertNotIn("error", setup)
            hits = {(ammo, rock): first_hit(ammo, rock) for ammo, rock in (
                ("piercing-rounds-magazine", "small-metallic-asteroid"),
                ("piercing-rounds-magazine", "medium-metallic-asteroid"),
                ("uranium-rounds-magazine", "small-metallic-asteroid"))}
        finally:
            self.server.call("flight_teardown")
        self.assertAlmostEqual(hits["piercing-rounds-magazine", "small-metallic-asteroid"], 62.72, places=2)
        self.assertAlmostEqual(hits["piercing-rounds-magazine", "medium-metallic-asteroid"], 56.448, places=2)
        self.assertEqual(hits["uranium-rounds-magazine", "small-metallic-asteroid"], 100)  # all it had

    def test_rockets_and_lasers_with_the_flight_research(self):
        # Explosion and laser damage against asteroids (dump resistances: explosion 50, 30,
        # 10 and 99% from small to huge, laser 20, 90, 95 and 99%). With stronger
        # explosives 10 a rocket (200, dump) hits for 4.8 x 200 = 960 before resistance:
        # 864 on a big asteroid, so 3 kill one (2,000), and 9.6 on a huge one. With laser
        # weapons damage 10 a laser shot (two beam ticks of 10) hits for 6.1 x 20 = 122:
        # 97.6 on a small asteroid, 12.2 on a medium one. Turrets placed by script on the
        # platform in Nauvis orbit, an asteroid placed in front, its first hit read.
        from tools.studies import MIX_RESEARCH

        data = platform.build(railguns=1, guns=2, thrusters=1).to_dict()
        place = ("/sc local s = storage.flight.platform.surface if storage.probe and storage.probe.valid then "
                 "storage.probe.destroy() end for _, e in pairs(s.find_entities_filtered{name = {'rocket-turret', "
                 "'laser-turret'}}) do e.destroy() end local t = s.create_entity{name = '%s', position = {0, -30}, "
                 "force = 'player'} t.insert{name = 'rocket', count = 10} storage.probe = s.create_entity{name = '%s', "
                 "position = {0, -30 - %d}} rcon.print(storage.probe.health)")
        health = "/sc rcon.print(storage.probe.valid and storage.probe.health or 0)"

        def first_hit(turret, rock, distance):
            full = float(self.server.command(place % (turret, rock, distance)))
            for _ in range(240):
                _step(self.server, 1, timeout=30)
                now = float(self.server.command(health))
                if now < full:
                    return round(full - now, 3)
            self.fail(f"{turret} never hit {rock}")

        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "power": True,
                "research": MIX_RESEARCH}))
            self.assertNotIn("error", setup)
            hits = {(turret, rock): first_hit(turret, rock, distance) for turret, rock, distance in (
                ("rocket-turret", "big-metallic-asteroid", 20),  # past its minimum range, 15
                ("rocket-turret", "huge-metallic-asteroid", 20),
                ("laser-turret", "small-metallic-asteroid", 10),
                ("laser-turret", "medium-metallic-asteroid", 10))}
        finally:
            self.server.call("flight_teardown")
        self.assertAlmostEqual(hits["rocket-turret", "big-metallic-asteroid"], 864, places=2)
        self.assertAlmostEqual(hits["rocket-turret", "huge-metallic-asteroid"], 9.6, places=2)
        self.assertAlmostEqual(hits["laser-turret", "small-metallic-asteroid"], 97.6, places=2)
        self.assertAlmostEqual(hits["laser-turret", "medium-metallic-asteroid"], 12.2, places=2)

    def test_a_fusion_plant_runs_on_little_coolant(self):
        # The cooler turns hot fluoroketone back cold with nothing lost, and a loop in use
        # holds only a few dozen in flight: one plant carries a 75 MW load on 50 cold, and
        # on 20 makes one generator's worth. A loop starts near 3,400 and a reactor that
        # dies takes about 1,000 with it (NOTES.md): rebuilds need no refill for a while.
        data = platform.build(railguns=4, guns=6, thrusters=1, power=1).to_dict()
        load = 75e6 / 60  # J a tick
        made = []
        for coolant in (50, 20):
            try:
                self.plant_flight(data, {"fusion-power-cell": 10}, coolant=coolant)
                self.server.command(
                    "/sc local s = storage.flight.platform.surface local e = s.create_entity{name = "
                    "'electric-energy-interface', position = s.find_non_colliding_position('electric-energy-interface', "
                    f"{{0, -12}}, 40, 1), force = 'player'}} e.power_production = 0 e.electric_buffer_size = {load} "
                    f"e.power_usage = {load} e.destructible = false")
                stats = ("/sc rcon.print(storage.flight.platform.surface.global_electric_network_statistics"
                         ".output_counts['fusion-generator'] or 0)")
                _step(self.server, 600, timeout=60)
                before = float(self.server.command(stats).strip())
                _step(self.server, 600, timeout=60)
                made.append((float(self.server.command(stats).strip()) - before) / 10 / 1e6)
            finally:
                self.server.call("flight_teardown")
        self.assertGreater(made[0], 85)  # the load, the reactor's 10 MW and the cooler's
        self.assertLess(made[1], 60)

    def test_flight_counts_what_it_supplies(self):
        # A budget for making supplies on board: the flight counts what the script
        # gives (fuel and oxidizer, ammo, power) and what the hub rebuilds. Kept
        # full, a thruster runs at max_performance: fluid_usage 2 a tick of each
        # fluid (dump), 120 a second. Flown from Nauvis toward Vulcanus, where
        # asteroids are few and small: at the solar system edge they killed this
        # small platform in some flights before its 20 s were up.
        data = platform.build(railguns=1, guns=2, thrusters=1).to_dict()
        ghost = ("/sc local s = storage.flight.platform.surface local y = 0 "
                 "for _, t in pairs(s.find_tiles_filtered{name = 'space-platform-foundation'}) do "
                 "y = math.min(y, t.position.y) end "
                 "s.create_entity{name = 'tile-ghost', inner_name = 'space-platform-foundation', "
                 "position = {0, y - 1}, force = 'player'}")
        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "fuel": True,
                "power": True, "ammo": platform.AMMO, "research": {"construction-robotics": 1},
                "supplies": {"gun-turret": 1}}))
            self.assertNotIn("error", setup)
            _step(self.server, 600, timeout=60)
            self.server.command("/sc storage.flight.platform.surface.find_entities_filtered{name = 'gun-turret'}[1]"
                                ".die()")
            self.server.command(ghost)
            _step(self.server, 600, timeout=60)
            got = self.server.call("flight_read")
        finally:
            self.server.call("flight_teardown")
        self.assertIs(got["alive"], True, got.get("ended"))
        used = {k: n - got["held"].get(k, 0) for k, n in got["supplied"].items()}
        for fluid in ("thruster-fuel", "thruster-oxidizer"):
            self.assertAlmostEqual(used[fluid] / 20, 120, delta=6)
        # At least the turret and tile made to go; asteroids can take a tile or two as well.
        self.assertGreaterEqual(got["rebuilt"].get("gun-turret", 0), 1, got["rebuilt"])
        self.assertGreaterEqual(got["rebuilt"].get("space-platform-foundation", 0), 1, got["rebuilt"])
        # A turret's load is held, not used, until it fires; the dead turret's went with it.
        self.assertGreaterEqual(used["uranium-rounds-magazine"], 10)
        self.assertGreater(got["power_j"], 10e6)  # the railgun charging its 11 MJ buffer, at least

    def test_platform_makes_its_own_fuel_in_flight(self):
        # fuel=True: after the start (thrusters full, the platform moving, as if it
        # arrived from the last leg) no fuel by script. Its collectors, crushers and
        # chemical plants keep it going on their own: a plant makes 37.5 fuel or
        # oxidizer a second (75 every 2 s), and thrust settles where the thruster
        # uses what they make, about 0.62 km a tick (NOTES.md).
        from tools.studies import HIGH_RESEARCH

        data = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True).to_dict()
        # From 0.4 of the route, where chunks are plenty: from the solar system edge it
        # sometimes never starts (NOTES.md).
        [flight] = platform.flight_tests(fuel=True, seconds=150, start=0.4, research=HIGH_RESEARCH)
        flown = run_flight(self.server, data, flight, Report(self.server.version))
        self.assertEqual((flown.error, flown.built), ("", flown.entities))
        self.assertTrue(flown.survived, flown.ended)
        fluids = ("thruster-fuel", "thruster-oxidizer")
        self.assertEqual({k: flown.supplied.get(k) for k in fluids}, {k: 1000 for k in fluids})  # the start alone
        for k in fluids:
            self.assertGreater(flown.made.get(k, 0), 2000, flown.made)
        self.assertGreater(flown.log[-1]["speed"], 0.4, flown.log)

    def test_a_promethium_chunk_on_the_chunk_belt_reaches_the_hub(self):
        # promethium=True: the filter inserter takes it off the chunk belt, the belt in the
        # gap above the hub carries it west and an inserter drops it in; with take, the
        # script empties it from the hub each second and counts it. Placed by script,
        # from Nauvis toward Vulcanus, where no promethium spawns: no asteroid is needed,
        # and 15 s is more than the 11 tiles of belt take, whatever the tick.
        data = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, promethium=True).to_dict()
        [f] = [e for e in data["blueprint"]["entities"] if e["name"] == "fast-inserter" and e.get("use_filters")
               and [x["name"] for x in e["filters"]] == [platform.PROMETHIUM]]
        x, y = math.floor(f["position"]["x"]) + 1, math.floor(f["position"]["y"]) - 2  # the chunk belt, upstream
        put = ("/sc local b = storage.flight.platform.surface.find_entities_filtered{name = 'transport-belt', "
               f"position = {{{x + 0.5}, {y + 0.5}}}}}[1] rcon.print(tostring(b and b.get_transport_line(1)"
               f".insert_at_back({{name = '{platform.PROMETHIUM}', count = 1}})))")
        for take in ([platform.PROMETHIUM], []):
            try:
                setup = self.server.call("flight_setup", json.dumps({
                    "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "fuel": True, "power": True,
                    "ammo": platform.AMMO, "take": take or None}))
                self.assertNotIn("error", setup)
                self.assertEqual(self.server.command(put).strip(), "true")
                _step(self.server, 900, timeout=60)
                got = self.server.call("flight_read")
            finally:
                self.server.call("flight_teardown")
            cargo = {c["name"]: c["count"] for c in got["cargo"] or [] if isinstance(c, dict)}
            if take:
                self.assertEqual(got["taken"], {platform.PROMETHIUM: 1})
                self.assertNotIn(platform.PROMETHIUM, cargo)
            else:
                self.assertEqual(cargo.get(platform.PROMETHIUM), 1, cargo)  # in the hub: one a slot

    def test_the_hub_feeds_every_turret(self):
        # ammo_feed with no ammo by script: from empty, the hub's filtered inserters, the
        # belts and an inserter at each turret fill every railgun and gun turret. Nauvis
        # toward Vulcanus: few asteroids, and none needed; read after 60 s, whatever the
        # tick. loaded fills each turret from the hub once, to 10 (automated_ammo_count).
        data = platform.build(railguns=4, guns=6, thrusters=1, ammo_feed=True).to_dict()
        supplies = {"railgun-ammo": 500, "uranium-rounds-magazine": 500}  # more than the belts hold, in 56 slots
        ammo = ("/sc local out = {} for _, t in pairs(storage.flight.platform.surface.find_entities_filtered"
                "{type = 'ammo-turret'}) do out[#out + 1] = t.get_inventory(defines.inventory.turret_ammo)"
                ".get_item_count() end rcon.print(helpers.table_to_json(out))")
        hub = ("/sc rcon.print(storage.flight.platform.hub.get_inventory(defines.inventory.hub_main)"
               ".get_item_count('railgun-ammo'))")
        for loaded in (None, platform.FED):
            try:
                setup = self.server.call("flight_setup", json.dumps({
                    "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "fuel": True, "power": True,
                    "supplies": supplies, "feed": sorted(supplies), "loaded": loaded, "speed": 100}))
                self.assertNotIn("error", setup)
                start, stocked = json.loads(self.server.command(ammo)), int(self.server.command(hub))
                _step(self.server, 3600, timeout=120)
                got = self.server.call("flight_read")
                counts = json.loads(self.server.command(ammo))
            finally:
                self.server.call("flight_teardown")
            if loaded:
                self.assertEqual((start, stocked), ([10] * 10, 460))  # 4 railguns' 10 out of the hub
            else:
                self.assertEqual(start, [0] * 10)
            self.assertTrue(all(n > 0 for n in counts), counts)
            self.assertGreaterEqual(sum(counts), 80, counts)
            self.assertGreaterEqual(got["fed"]["railgun-ammo"], 40)
            self.assertGreaterEqual(got["fed"]["uranium-rounds-magazine"], 60)

    def test_feed_research_speeds_the_hubs_inserters(self):
        # ammo_feed's hub inserters bind its feed 0.1 out (NOTES.md): a filtered fast inserter
        # on the hub's north face moves one magazine a swing, 2.5 a second, until inserter
        # capacity research adds to its hand (levels 2 and 7: 3 a swing). It drops onto a belt
        # long enough not to back up; the hub's count is read 5 and 15 s in, whatever the tick.
        bp = Blueprint()
        for x in range(-6, 7):
            for y in range(-126, 6):
                bp.tiles.append("space-platform-foundation", position=(x, y))
        bp.entities.append("fast-inserter", tile_position=(-3, -5), direction=Direction.SOUTH, use_filters=True)
        bp.entities[-1].set_item_filter(0, platform.AMMO["gun-turret"])
        bp.entities.append("medium-electric-pole", tile_position=(-5, -5))
        for y in range(-6, -126, -1):
            bp.entities.append("transport-belt", tile_position=(-3, y), direction=Direction.NORTH)
        hub = ("/sc rcon.print(storage.flight.platform.hub.get_inventory(defines.inventory.hub_main)"
               f".get_item_count('{platform.AMMO['gun-turret']}'))")
        rate = {}
        for name, research in (("none", {}), ("feed", platform.FEED_RESEARCH)):
            try:
                setup = self.server.call("flight_setup", json.dumps({
                    "blueprint": encode(bp.to_dict()), "from": "nauvis", "to": "vulcanus", "power": True,
                    "supplies": {platform.AMMO["gun-turret"]: 500}, "research": research}))
                self.assertNotIn("error", setup)
                _step(self.server, 300, timeout=60)
                before = int(self.server.command(hub))
                _step(self.server, 600, timeout=60)
                rate[name] = (before - int(self.server.command(hub))) / 10
            finally:
                self.server.call("flight_teardown")
        self.assertAlmostEqual(rate["none"], 2.5, delta=0.2)
        self.assertGreaterEqual(rate["feed"], 5.0, rate)

    def test_a_reserve_tank_shares_the_thrusters_fill(self):
        # reserve=True: a storage tank on each thruster input joins its line, which has
        # one fill level, so primed through the thruster the tank fills too, and they
        # drain together: thrust follows the reserve. What the tanks hold counts as held,
        # not used. Flown from Nauvis toward Vulcanus, where asteroids are few.
        data = platform.build(railguns=32, guns=32, thrusters=1, cargo_bays=8, fuel=True, reserve=True).to_dict()
        [flight] = platform.flight_tests(fuel=True, reserve=True)
        fill = ("/sc local s = storage.flight.platform.surface local out = {tank = {}} "
                "for _, t in pairs(s.find_entities_filtered{name = 'storage-tank'}) do "
                "for k, v in pairs(t.get_fluid_contents()) do out.tank[k] = v end end "
                "out.thruster = s.find_entities_filtered{name = 'thruster'}[1].get_fluid_contents() "
                "rcon.print(helpers.table_to_json(out))")
        try:
            setup = self.server.call("flight_setup", json.dumps({
                "blueprint": encode(data), "from": "nauvis", "to": "vulcanus", "power": True,
                "ammo": platform.AMMO, "research": flight.research, "primed": flight.primed,
                "start_speed": flight.start_speed}))
            self.assertNotIn("error", setup)
            primed = json.loads(self.server.command(fill))
            _step(self.server, 1800, timeout=60)
            later = json.loads(self.server.command(fill))
            got = self.server.call("flight_read")
        finally:
            self.server.call("flight_teardown")
        self.assertIs(got["alive"], True, got.get("ended"))
        for k in ("thruster-fuel", "thruster-oxidizer"):
            self.assertAlmostEqual(primed["tank"][k], 25000, delta=1)  # full
            self.assertAlmostEqual(primed["thruster"][k], 1000, delta=1)
            self.assertLess(later["tank"][k], 24000)  # 30 s of thrust came out of it
            self.assertAlmostEqual(later["tank"][k] / 25000, later["thruster"][k] / 1000, delta=0.01)
            self.assertGreaterEqual(got["held"][k], later["tank"][k])

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
