"""tools.density: how a blueprint spends its tiles, on small blueprints with known answers."""

import unittest
import warnings

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from tools.density import bound, density, report


def blueprint(*entities, label=None) -> dict:
    """A blueprint from (name, tile_position, direction) triples."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bp = Blueprint()
        if label:
            bp.label = label
        for name, tile, direction in entities:
            bp.entities.append(name, tile_position=tile, **({"direction": direction} if direction is not None else {}))
        return bp.to_dict()


N, E = Direction.NORTH, Direction.EAST


class Density(unittest.TestCase):
    def test_one_machine_fills_its_box(self):
        [d] = density(blueprint(("assembling-machine-2", (0, 0), N)))
        self.assertEqual((d["width"], d["height"], d["area"]), (3, 3, 9))
        self.assertEqual((d["covered"], d["empty"], d["empty_share"]), (9, 0, 0.0))
        self.assertEqual(d["roles"]["machines"], 9)
        self.assertEqual((d["crafting_machines"], d["area_per_machine"]), (1, 9.0))

    def test_empty_tiles_are_the_box_less_what_entities_cover(self):
        # Assembler on tiles x 0-2, y 0-2; inserter at (3, 1); belt at (4, 1).
        [d] = density(blueprint(("assembling-machine-2", (0, 0), N), ("inserter", (3, 1), E),
                                ("transport-belt", (4, 1), N)))
        self.assertEqual((d["width"], d["height"], d["area"]), (5, 3, 15))
        self.assertEqual((d["covered"], d["empty"]), (11, 4))
        self.assertAlmostEqual(d["empty_share"], 4 / 15)
        self.assertEqual({k: v for k, v in d["roles"].items() if v},
                         {"machines": 9, "inserters": 1, "belts": 1})
        self.assertEqual(d["counts"]["inserters"], 1)

    def test_a_turned_entity_turns_its_footprint(self):
        # A pump is 1 x 2; turned east it lies 2 x 1.
        [north] = density(blueprint(("pump", (0, 0), N)))
        [east] = density(blueprint(("pump", (0, 0), E)))
        self.assertEqual((north["width"], north["height"]), (1, 2))
        self.assertEqual((east["width"], east["height"]), (2, 1))
        self.assertEqual(east["roles"]["pipes"], 2)

    def test_poles_and_other_entities_have_their_own_roles(self):
        [d] = density(blueprint(("medium-electric-pole", (0, 0), None), ("iron-chest", (1, 0), None)))
        self.assertEqual((d["roles"]["poles"], d["roles"]["other"]), (1, 1))
        self.assertEqual((d["crafting_machines"], d["area_per_machine"]), (0, None))

    def test_a_rate_gives_tiles_per_item_a_second(self):
        [d] = density(blueprint(("assembling-machine-2", (0, 0), N)), rate=1.5)
        self.assertEqual(d["area_per_rate"], 6.0)
        self.assertIsNone(density(blueprint(("assembling-machine-2", (0, 0), N)))[0]["area_per_rate"])

    def test_a_book_reports_each_blueprint(self):
        children = [blueprint(("transport-belt", (0, 0), N), label="belt"),
                    blueprint(("assembling-machine-2", (0, 0), N), label="machine")]
        data = {"blueprint_book": {"item": "blueprint-book",
                                   "blueprints": [{"index": i, **c} for i, c in enumerate(children)]}}
        self.assertEqual([(d["label"], d["area"]) for d in density(data)], [("belt", 1), ("machine", 9)])

    def test_without_the_dump_draftsmans_data_gives_the_same_footprints(self):
        # data/ is gitignored, so CI may measure from Draftsman's data alone.
        from pathlib import Path
        from unittest import mock

        import tools.density as module

        row = blueprint(("assembling-machine-2", (0, 0), N), ("inserter", (3, 1), E), ("pump", (4, 0), N))
        with_dump = density(row)
        module._prototypes.cache_clear()
        try:
            with mock.patch.object(module, "DUMP", Path("/nonexistent/data-raw-dump.json")):
                self.assertEqual(density(row), with_dump)
        finally:
            module._prototypes.cache_clear()

    def test_report_states_the_geometry(self):
        text = report(density(blueprint(("assembling-machine-2", (0, 0), N), ("inserter", (3, 1), E),
                                        ("transport-belt", (4, 1), N), label="row"), rate=0.5))
        self.assertIn("row", text)
        self.assertIn("5 x 3 = 15 tiles", text)
        self.assertIn("covered 11 (73%), empty 4 (27%)", text)
        self.assertIn("30.0 tiles per item/s", text)


class Bound(unittest.TestCase):
    """tools.density.bound: tiles no layout of a blueprint's parts, or of its generator's own
    template, can be smaller than."""

    def test_the_parts_are_every_entity_but_belts_pipes_and_poles(self):
        # Assembler x 0-2, inserter (3, 1), belt (4, 1), chest (5, 1), pole (5, 0): the box is 6 by 3.
        bp = blueprint(("assembling-machine-2", (0, 0), N), ("inserter", (3, 1), E), ("transport-belt", (4, 1), N),
                       ("iron-chest", (5, 1), None), ("medium-electric-pole", (5, 0), None))["blueprint"]
        b = bound(bp)
        self.assertEqual(b["parts"], 11)
        self.assertEqual(b["roles"], {"machines": 9, "inserters": 1, "other": 1})
        self.assertIsNone(b["model"])

    def test_along_its_run_the_machines_of_the_fullest_line_stand_end_to_end(self):
        # Two assemblers in one column with a row between them (y 0-2, 4-6), a third beside
        # the first, and a belt column: 4 wide by 7. End to end the column takes 6 rows.
        bp = blueprint(("assembling-machine-2", (0, 0), N), ("assembling-machine-2", (0, 4), N),
                       ("transport-belt", (3, 0), N), ("transport-belt", (3, 6), N))["blueprint"]
        b = bound(bp, run="y")
        self.assertEqual((b["across"], b["along"], b["model"]), (4, 6, 24))
        # A template that needs a row between each two machines: that row too.
        self.assertEqual(bound(bp, run="y", between=1)["model"], 28)

    def test_a_row_runs_along_x(self):
        # Two assemblers side by side with a column between (x 0-2, 4-6), a belt below: 7 by 4.
        bp = blueprint(("assembling-machine-2", (0, 0), N), ("assembling-machine-2", (4, 0), N),
                       ("transport-belt", (0, 3), E))["blueprint"]
        b = bound(bp, run="x")
        self.assertEqual((b["across"], b["along"], b["model"]), (4, 6, 24))

    def test_neither_bound_is_ever_above_the_area(self):
        import importlib

        from tools.codec import decode, encode

        requests = [("assembly_row", {"recipe": "electronic-circuit"}),
                    ("assembly_row", {"recipe": "iron-gear-wheel", "machine": "assembling-machine-3",
                                      "belt": "fast-transport-belt"}),
                    ("fluid_row", {"recipe": "plastic-bar"}), ("fluid_block", {"recipe": "sulfuric-acid"}),
                    ("fluid_block", {"recipe": "advanced-oil-processing"}), ("smelting_column", {}),
                    ("cell", {}), ("cell", {"recipe": "inserter", "producer": "iron-gear-wheel"}),
                    ("gated_requester", {})]
        for name, params in requests:
            module = importlib.import_module(f"generators.{name}")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = decode(encode(module.build(**params).to_dict()))
            [d] = density(data)
            b = bound(data["blueprint"], *getattr(module, "MODEL", None) or (None,))
            with self.subTest(name, **params):
                self.assertLessEqual(b["parts"], d["area"])
                if b["model"] is not None:
                    self.assertLessEqual(b["model"], d["area"])
                    self.assertGreaterEqual(b["model"], b["roles"]["machines"])


class GenGap(unittest.TestCase):
    """gen says how close each generator's layout is to its bounds, on stderr."""

    def gap(self, *argv) -> str:
        import contextlib
        import io

        from tools.bp import main

        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            main(["gen", *argv])
        return err.getvalue()

    def test_a_row_names_its_columns_and_its_parts(self):
        self.assertIn("assembly_row: 52 tiles; at least 39 in its columns (13 wide by 3: the machines of its "
                      "fullest column end to end), within 33%; the parts' own tiles 26 (machines 18, inserters 8), "
                      "within 100%", self.gap("assembly_row", "recipe=electronic-circuit"))

    def test_a_cell_counts_the_row_between_each_two_machines(self):
        self.assertIn("cell: 403 tiles; at least 403 in its columns (13 wide by 31: the machines of its fullest "
                      "column end to end, a row between each two), within 0%",
                      self.gap("cell"))

    def test_a_fluid_block_runs_along_its_row(self):
        self.assertIn("fluid_block: 175 tiles; at least 84 in its row (12 by 7 tall: the machines end to end), "
                      "within 108%", self.gap("fluid_block", "recipe=sulfuric-acid"))

    def test_a_book_adds_up_its_rows(self):
        self.assertRegex(self.gap("production", "item=electronic-circuit", "rate=5"),
                         r"production: 260 tiles in \d+ rows; at least 234 in their own layouts, within 11%; "
                         r"the parts' own tiles 148 \(machines \d+, inserters \d+\), within 76%")

    def test_without_machines_the_parts_are_the_bound(self):
        self.assertIn("gated_requester: 4 tiles; the parts' own tiles 4 (other 4), within 0%",
                      self.gap("gated_requester"))


class DensityCommand(unittest.TestCase):
    def test_bp_density_reads_a_string_and_prints_json(self):
        import contextlib
        import io
        import json

        from tools.bp import main
        from tools.codec import encode

        string = encode(blueprint(("assembling-machine-2", (0, 0), N), label="m"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["density", string, "--rate", "0.75", "--json"])
        [d] = json.loads(out.getvalue())
        self.assertEqual((d["label"], d["area"], d["area_per_rate"]), ("m", 9, 12.0))


if __name__ == "__main__":
    unittest.main()
