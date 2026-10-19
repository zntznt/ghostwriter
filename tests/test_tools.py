"""Run: python -m unittest discover tests"""

import copy
import tempfile
import unittest
import warnings
from collections import Counter
from pathlib import Path

from draftsman.blueprintable import Blueprint, BlueprintBook, DeconstructionPlanner, UpgradePlanner

from generators import smelting_column
from generators.gated_requester import build
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
        # Independent of the generator's own placement: supply areas against
        # collision boxes, and the copper wires joining every pole.
        from draftsman.data import entities as protos

        for params in COLUMN_VARIANTS:
            bp = column(**params)
            ents = bp["entities"]
            poles = {e["entity_number"]: e for e in ents if e["name"].endswith("electric-pole")}
            for e in ents:
                p = protos.raw[e["name"]]
                if p.get("energy_source", {}).get("type") != "electric":
                    continue
                half = p["collision_box"][1][0]
                self.assertTrue(any(
                    all(abs(e["position"][k] - q["position"][k]) < protos.raw[q["name"]]["supply_area_distance"] + half
                        for k in "xy")
                    for q in poles.values()), (params, e))
            group = {n: n for n in poles}

            def root(n):
                while group[n] != n:
                    n = group[n]
                return n

            for a, ca, b, cb in bp.get("wires", []):
                self.assertEqual((ca, cb), (5, 5))  # pole_copper
                group[root(a)] = root(b)
            self.assertEqual(len({root(n) for n in poles}), 1, params)

    def test_flow_tests_name_built_belts(self):
        for params in COLUMN_VARIANTS:
            bp = smelting_column.build(**params)
            ids = {e.id for e in bp.entities if e.id}
            for flow in smelting_column.flow_tests(**params):
                self.assertLessEqual({f.entity for f in flow.feed} | set(flow.drain), ids, params)

    def test_bad_sides(self):
        with self.assertRaises(ValueError):
            smelting_column.build(sides=3)


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
