"""Run: python -m unittest discover tests"""

import copy
import tempfile
import unittest
import warnings
from pathlib import Path

from draftsman.blueprintable import Blueprint, BlueprintBook, DeconstructionPlanner, UpgradePlanner

from generators.gated_requester import build
from tools.check import check
from tools.codec import CodecError, decode, encode, root_key
from tools.library import entries, load_book, save
from tools.settings import game_settings, known, registry

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
