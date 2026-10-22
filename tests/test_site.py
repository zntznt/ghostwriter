import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_tools import GAME_21, generated, make_book
from tools import site
from tools.codec import decode
from tools.library import load_book, plan_path, save


class Site(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Path(self.tmp.name)
        save(make_book(), self.lib)
        save(decode((GAME_21[0].parent / "car.txt").read_text()), self.lib / "exports")

    def tearDown(self):
        self.tmp.cleanup()

    def nodes(self, data):
        for n in data["children"]:
            yield n
            yield from self.nodes(n) if "children" in n else ()

    def test_strings_are_the_library(self):
        data = site.build(self.lib)
        book = next(n for n in data["children"] if n["path"] == "outer")
        self.assertEqual(decode(book["string"]), load_book(self.lib / "outer"))
        leaf = next(n for n in self.nodes(data) if n["path"] == "outer/00-gated-requester.json")
        stored = json.loads((self.lib / leaf["path"]).read_text())
        stored.pop("index")
        self.assertEqual(decode(leaf["string"]), stored)

    def test_preview_attaches_wires_to_connector_sides(self):
        data = site.build(self.lib)
        leaf = next(n for n in self.nodes(data) if n["path"] == "outer/00-gated-requester.json")
        wires = {(w["x1"], w["y1"], w["x2"], w["y2"]) for w in leaf["preview"]["wires"]}
        # Decider at (0.5, 1) facing north: input 0.45 south of centre, output 0.45 north.
        self.assertEqual(wires, {(0.5, 2.5, 0.5, 1.45), (0.5, 0.55, 0.5, -0.5)})
        self.assertEqual(leaf["entities"], {"requester-chest": 1, "decider-combinator": 1, "constant-combinator": 1})

    def test_preview_falls_back_when_draftsman_cannot_load(self):
        data = site.build(self.lib)
        car = next(n for n in self.nodes(data) if n["path"].startswith("exports/"))
        self.assertEqual([s["name"] for s in car["preview"]["shapes"]], ["car"])

    def test_tested_entries_list_their_cases(self):
        data = site.build(Path(__file__).parent.parent / "library")
        leaf = next(n for n in self.nodes(data) if n["path"] == "logistics/gated-requester.json")
        self.assertEqual(len(leaf["tests"]), 5)
        self.assertIn("network has surplus: request", leaf["tests"])

    def test_flow_tested_entries_list_their_flows(self):
        data = site.build(Path(__file__).parent.parent / "library")
        leaf = next(n for n in self.nodes(data) if n["path"].startswith("smelting/"))
        self.assertEqual(leaf["flows"], ["48 furnaces: 15 iron-plate/s"])
        self.assertNotIn("tests", leaf)

    def test_an_entry_with_a_plan_carries_it_and_others_dont(self):
        # NAME.plan.json beside NAME.json (bp plan --save): the page shows its trip, load, research,
        # steps before leaving and caveats; an entry without one is as it was.
        path = "outer/00-gated-requester.json"
        leaf = next(n for n in self.nodes(site.build(self.lib)) if n["path"] == path)
        self.assertNotIn("plan", leaf)
        plan = {"from": "edge", "runs": 8, "game": "2.1.23", "research": {"construction-robotics": 1},
                "trip": {"turn": 0.021, "stay": 0, "km": 168000, "hours": 0.9, "chunks": 353, "packs": 141.2,
                         "comes back": 1.0, "loaded slots": 534, "slots": 539},
                "limit": {"by": "hub slots", "largest": "railgun-ammo", "slots": 440},
                "stock": [{"item": "railgun-ammo", "count": 4400, "slots": 440}],
                "readying": ["Load the hub."], "caveats": ["A caveat."]}
        plan_path(self.lib / path).write_text(json.dumps(plan))
        data = site.build(self.lib)
        leaf = next(n for n in self.nodes(data) if n["path"] == path)
        self.assertEqual(leaf["plan"], plan)
        self.assertEqual(sum(1 for _ in self.nodes(data)), sum(1 for _ in self.nodes(site.build(self.lib))))
        self.assertFalse(any("plan" in n for n in self.nodes(data) if n["path"] != path))

    def test_the_promethium_platform_shows_its_plan(self):
        data = site.build(Path(__file__).parent.parent / "library")
        platforms = {n["path"]: n for n in self.nodes(data) if n["path"].startswith("platforms/")}
        promethium = next(n for p, n in platforms.items() if p.endswith("keeps-promethium-feeds-its-turrets.json"))
        self.assertGreater(promethium["plan"]["trip"]["chunks"], 0)
        self.assertTrue(promethium["plan"]["stock"] and promethium["plan"]["readying"])
        # The 32-railgun platform keeps no promethium: bp plan refuses it, so it has no plan.
        self.assertEqual([p for p, n in platforms.items() if "plan" not in n],
                         ["platforms/platform-32-railguns-32-gun-turrets-1-thruster-8-cargo-bays.json"])

    def test_page_embeds_data_safely(self):
        data = generated()
        data["blueprint"]["description"] = "</script><script>alert(1)</script>"
        save(data, self.lib)
        out = self.lib / "page" / "index.html"
        site.main(["--library", str(self.lib), "--out", str(out)])
        page = out.read_text()
        self.assertNotIn("/*LIBRARY_DATA*/", page)
        self.assertEqual(len(re.findall(r"</script>", page)), 1)


if __name__ == "__main__":
    unittest.main()
