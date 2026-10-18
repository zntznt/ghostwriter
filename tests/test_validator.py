"""In-game validation tests. Need a Factorio executable (see tools/factorio.py);
skipped without one. One server for the whole class, ~30 s."""

import os
import unittest
from pathlib import Path

from tests.test_tools import GAME_21, HANDOFF, generated, make_book
from generators import gated_requester
from tools.codec import decode
from tools.factorio import locate
from tools.library import load_book
from validator.circuit import Case, Expect, Inject, run_circuit_tests
from validator.server import Server
from validator.validate import Report, validate

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
        library = Path(__file__).parent.parent / "library"
        for folder in sorted(p for p in library.iterdir() if p.is_dir()):
            with self.subTest(folder.name):
                self.assertPasses(load_book(folder))

    def test_space_only_entity_is_a_warning(self):
        report = self.assertPasses(decode((GAME_21[0].parent / "thruster.txt").read_text()))
        self.assertTrue(any("thruster" in w for w in report.warnings))

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
        self.assertFailsWith(data, "was not built")

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
