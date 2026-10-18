"""Run: python -m unittest discover tests"""

import copy
import tempfile
import unittest
import warnings
from pathlib import Path

from draftsman.blueprintable import BlueprintBook, DeconstructionPlanner, UpgradePlanner

from generators.gated_requester import build
from tools.check import check
from tools.codec import CodecError, decode, encode
from tools.library import load_book, save

FIXTURES = Path(__file__).parent / "fixtures"
# The hand-built string from the planning handoff, kept as a fixture: it is
# NOT in library/ because its requester chest uses 2.1-only keys (see NOTES.md).
HANDOFF = (FIXTURES / "gated_requester_handoff.txt").read_text()


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

    def test_requester_uses_mode_field_that_works_on_2_0_and_2_1(self):
        chest = generated()["blueprint"]["entities"][0]
        self.assertEqual(chest["control_behavior"], {"circuit_mode_of_operation": 1})

    def test_generated_is_clean(self):
        self.assertEqual(check(generated()), [])


class Check(unittest.TestCase):
    def test_flags_2_1_only_logistic_keys(self):
        problems = check(decode(HANDOFF))
        self.assertEqual(len(problems), 1)
        self.assertIn("2.1-only", problems[0])

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

    def test_misspelled_key_is_error(self):
        data = generated()
        cond = data["blueprint"]["entities"][1]["control_behavior"]["decider_conditions"]["conditions"][0]
        cond["first_signal_network"] = cond.pop("first_signal_networks")
        self.assertTrue(any("first_signal_network" in p for p in errors(data)))

    def test_wire_reach(self):
        data = generated()
        data["blueprint"]["entities"][2]["position"] = {"x": 40.5, "y": 2.5}
        self.assertTrue(any("spans" in p for p in check(data)))


class Library(unittest.TestCase):
    def make_book(self) -> dict:
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

    def test_book_round_trip(self):
        book = self.make_book()
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

    def test_plain_folder_becomes_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "my-book"
            save(generated(), folder)
            save(generated(), folder)
            book = load_book(folder)["blueprint_book"]
            self.assertEqual(book["label"], "my-book")
            self.assertEqual([c["index"] for c in book["blueprints"]], [0, 1])


if __name__ == "__main__":
    unittest.main()
