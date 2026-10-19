"""Build the library browser: one self-contained HTML file.

    python -m tools.site [--out site/dist/index.html] [--library library/]

Reads library/, embeds everything (data, strings, layout previews) into
site/template.html, so the page opens straight from disk with no server.
Strings are encoded here from the library JSON, as everywhere else.
"""

import argparse
import json
import warnings
from collections import Counter
from pathlib import Path

from draftsman.blueprintable import get_blueprintable_from_JSON

from tools.codec import encode, root_key
from tools.library import BOOK_META, load_book
from validator.tests_file import load_tests, tests_path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "library"
TEMPLATE = ROOT / "site" / "template.html"
OUT = ROOT / "site" / "dist" / "index.html"

# Preview colours by prototype type; anything else is "other".
GROUPS = {
    "combinator": ("arithmetic-combinator", "decider-combinator", "constant-combinator",
                   "selector-combinator", "power-switch", "programmable-speaker", "display-panel", "lamp"),
    "storage": ("container", "logistic-container", "storage-tank", "linked-container", "proxy-container"),
    "belt": ("transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "lane-splitter",
             "linked-belt"),
    "inserter": ("inserter",),
    "machine": ("assembling-machine", "furnace", "mining-drill", "lab", "rocket-silo", "beacon", "reactor",
                "boiler", "generator", "agricultural-tower"),
    "power": ("electric-pole", "solar-panel", "accumulator"),
    "rail": ("straight-rail", "curved-rail-a", "curved-rail-b", "half-diagonal-rail", "rail-ramp",
             "elevated-straight-rail", "elevated-curved-rail-a", "elevated-curved-rail-b",
             "elevated-half-diagonal-rail", "rail-support", "train-stop", "rail-signal", "rail-chain-signal"),
}
GROUP_OF = {t: g for g, types in GROUPS.items() for t in types}
WIRE_COLOURS = {1: "red", 2: "green", 3: "red", 4: "green"}  # connector -> colour (5/6 copper skipped)
# Entities with separate input (connectors 1/2) and output (3/4) sides. The
# output faces `direction` (16-way, 0 = north): verified by building the
# gated requester, whose north-facing decider outputs to the chest above it.
DUAL = {"arithmetic-combinator", "decider-combinator", "selector-combinator"}
FACING = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def build(library: Path = LIBRARY) -> dict:
    """The page's data: a tree of folders/books and blueprintables."""
    return {"children": [_node(p, library) for p in _entries(library)]}


def _entries(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_dir() or (p.suffix == ".json" and p.name != BOOK_META))


def _node(path: Path, library: Path) -> dict:
    rel = path.relative_to(library).as_posix()
    if path.is_dir():
        data = load_book(path)
        book = data["blueprint_book"]
        return {
            "kind": "blueprint_book", "path": rel, "label": book.get("label") or path.name,
            "description": book.get("description", ""), "string": encode(data),
            "children": [_node(p, library) for p in _entries(path)],
        }
    data = json.loads(path.read_text())
    data.pop("index", None)
    kind = root_key(data)
    body = data[kind]
    node = {
        "kind": kind, "path": rel, "label": body.get("label") or path.stem,
        "description": body.get("description", ""), "string": encode(data),
    }
    if kind == "blueprint":
        entities = body.get("entities", [])
        node["entities"] = dict(Counter(e["name"] for e in entities).most_common())
        node["wires"] = len(body.get("wires", []))
        node["preview"] = _preview(data)
        tests = tests_path(path)
        if tests.is_file():
            # CI runs these in the game before publishing, so a published page
            # means they passed.
            loaded = load_tests(tests, data)
            if loaded.cases:
                node["tests"] = [case.name for case in loaded.cases]
            if loaded.flows:
                node["flows"] = [flow.name for flow in loaded.flows]
    return node


def _preview(data: dict) -> dict:
    """Footprints (collision boxes from Draftsman, 1x1 fallback) and circuit wires."""
    body = data["blueprint"]
    raw = body.get("entities", [])
    boxes = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = get_blueprintable_from_JSON(data)
        for e, m in zip(raw, model.entities):
            box = m.get_world_bounding_box() if hasattr(m, "get_world_bounding_box") else None
            if box is not None:
                (x1, y1), (x2, y2) = box.world_top_left, box.world_bot_right
                boxes[e["entity_number"]] = (x1, y1, x2, y2, GROUP_OF.get(getattr(m, "type", ""), "other"))
    except Exception:
        pass  # Draftsman can't load some 2.1 exports (NOTES.md); fall back to 1x1
    shapes = []
    ends = {}  # (entity_number, connector) -> where its wires attach
    for e in raw:
        x, y = e["position"]["x"], e["position"]["y"]
        for connector in (1, 2, 3, 4):
            ends[e["entity_number"], connector] = (x, y)
        if e["name"] in DUAL and e.get("direction", 0) in FACING:
            dx, dy = FACING[e.get("direction", 0)]
            for connector, sign in ((1, -1), (2, -1), (3, 1), (4, 1)):
                ends[e["entity_number"], connector] = (x + sign * 0.45 * dx, y + sign * 0.45 * dy)
        x1, y1, x2, y2, group = boxes.get(e["entity_number"], (x - 0.4, y - 0.4, x + 0.4, y + 0.4, "other"))
        shapes.append({"name": e["name"], "group": group,
                       "x": round(x1, 3), "y": round(y1, 3), "w": round(x2 - x1, 3), "h": round(y2 - y1, 3)})
    wires = []
    for a, ca, b, cb in body.get("wires", []):
        colour = WIRE_COLOURS.get(ca)
        if colour and (a, ca) in ends and (b, cb) in ends:
            (xa, ya), (xb, yb) = ends[a, ca], ends[b, cb]
            wires.append({"colour": colour, "x1": xa, "y1": ya, "x2": xb, "y2": yb})
    return {"shapes": shapes, "wires": wires}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--library", default=str(LIBRARY))
    args = ap.parse_args(argv)
    data = build(Path(args.library))
    page = TEMPLATE.read_text().replace(
        "/*LIBRARY_DATA*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(out)


if __name__ == "__main__":
    main()
