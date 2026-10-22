"""Rows on shelves for the compact layout (tools/shelves.py)."""

import unittest

from generators import factory
from tools.shelves import MIN_CHANNEL, place, shelve, widths


class Shelves(unittest.TestCase):
    def test_a_row_goes_above_every_row_it_takes_from(self):
        p = factory.rows_and_lines(item="logistic-science-pack", rate=1)
        makers = {q.name: k for k, r in enumerate(p["rows"]) for q in r.ports if q.kind == "out"}
        for wide in widths(p["rows"]):
            shelf_of = {k: s for s, shelf in enumerate(shelve(p, wide)) for k in shelf}
            for k, r in enumerate(p["rows"]):
                for q in r.ports:
                    for item in (q.lanes.values() if q.kind == "in" else []):
                        if item in makers:
                            self.assertLess(shelf_of[makers[item]], shelf_of[k], (wide, item))

    def test_nothing_may_pass_in_front_of_an_input_belts_top(self):
        # A belt there would take whatever the row's input belt carries past the row.
        p = factory.rows_and_lines(item="electronic-circuit", rate=5)
        shelved = shelve(p, max(widths(p["rows"])))
        canvas = factory.Canvas()
        where, _, _ = place(p["rows"], shelved, [MIN_CHANNEL] * (len(shelved) + 1), 6, canvas)
        for k, r in enumerate(p["rows"]):
            dx, dy = where[k]
            for q in r.ports:
                if q.kind == "in":
                    self.assertEqual(canvas.tiles.get((q.x + dx, r.box[1] + dy - 1)), f"row {k} end", (k, q.x))

    def test_rows_on_a_shelf_stand_on_one_line_apart(self):
        p = factory.rows_and_lines(item="electronic-circuit", rate=15)
        shelved = shelve(p, max(widths(p["rows"])))
        where, shelf_box, top = place(p["rows"], shelved, [MIN_CHANNEL] * (len(shelved) + 1), 6, factory.Canvas())
        for s, shelf in enumerate(shelved):
            bottoms = {p["rows"][k].box[3] + where[k][1] for k in shelf}
            self.assertEqual(bottoms, {shelf_box[s][0]})
        self.assertLess(top, shelf_box[-1][1])


if __name__ == "__main__":
    unittest.main()
