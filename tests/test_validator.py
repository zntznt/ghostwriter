"""In-game validation tests. Need a Factorio executable (see tools/factorio.py);
skipped without one. One server for the whole class, ~30 s."""

import os
import unittest
from pathlib import Path

from draftsman.blueprintable import Blueprint
from draftsman.entity import new_entity

from tests.test_tools import GAME_21, HANDOFF, generated, make_book
from generators import gated_requester
from tools.codec import decode
from tools.factorio import locate
from tools.library import entries
from tools.settings import game_settings, registry
from validator.circuit import Case, Expect, Inject, library_tests, run_circuit_tests
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

    def test_library_circuit_tests_pass(self):
        # Logic of every library blueprint with a .tests.toml, before it is published.
        for label, data, cases, ids in library_tests(Path(__file__).parent.parent / "library"):
            with self.subTest(label):
                report = Report(self.server.version)
                run_circuit_tests(self.server, data, cases, ids, report)
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


if __name__ == "__main__":
    unittest.main()
