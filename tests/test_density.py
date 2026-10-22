"""tools.density: how a blueprint spends its tiles, on small blueprints with known answers."""

import unittest
import warnings

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from tools.density import density, report


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
