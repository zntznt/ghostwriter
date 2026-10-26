"""Density: how a blueprint spends its tiles.

    python -m tools.bp density SRC [--rate R] [--json]

Plain geometry, no interpretation: the bounding box of the entities' footprints,
the tiles they cover, the empty rest, and what the covered tiles are spent on
by role. An empty tile may still be doing work (inside a pole's supply area or
an inserter's reach); it's counted as empty all the same.

Footprints come from the game's prototypes (data/, else Draftsman's data):
tile_width and tile_height where a prototype sets them, else its collision box
rounded up to whole tiles, turned with the entity's direction.
"""

import json
import math
from functools import cache
from pathlib import Path

from draftsman.data import entities as entity_data

DUMP = Path(__file__).resolve().parent.parent / "data" / "data-raw-dump.json"
CRAFTING = {"assembling-machine", "furnace", "rocket-silo"}
ROLES = {
    "machines": CRAFTING | {"mining-drill", "lab"},
    "belts": {"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "linked-belt", "lane-splitter"},
    "inserters": {"inserter"},
    "poles": {"electric-pole"},
    "pipes": {"pipe", "pipe-to-ground", "pump", "infinity-pipe"},
}
EAST, WEST = 4, 12  # blueprint directions that turn a footprint a quarter


@cache
def _prototypes() -> dict:
    """Entity name -> prototype, from the game's dump if there is one, else Draftsman's data."""
    if DUMP.exists():
        dump = json.loads(DUMP.read_text())
        return {name: proto for group in dump.values() if isinstance(group, dict)
                for name, proto in group.items() if isinstance(proto, dict) and "collision_box" in proto}
    return dict(entity_data.raw)


def footprint(entity: dict) -> set[tuple[int, int]]:
    """The tiles an entity in a blueprint covers."""
    proto = _prototypes()[entity["name"]]
    (x0, y0), (x1, y1) = proto["collision_box"]
    w = proto.get("tile_width") or math.ceil(round(x1 - x0, 6))
    h = proto.get("tile_height") or math.ceil(round(y1 - y0, 6))
    if entity.get("direction", 0) in (EAST, WEST):
        w, h = h, w
    left = round(entity["position"]["x"] - w / 2)
    top = round(entity["position"]["y"] - h / 2)
    return {(x, y) for x in range(left, left + w) for y in range(top, top + h)}


def role(name: str) -> str:
    kind = _prototypes()[name].get("type")
    return next((r for r, kinds in ROLES.items() if kind in kinds), "other")


def _blueprints(data: dict):
    if "blueprint" in data:
        yield data["blueprint"]
    for child in data.get("blueprint_book", {}).get("blueprints", []):
        yield from _blueprints(child)


def density(data: dict, rate: float | None = None) -> list[dict]:
    """One measure per blueprint (a book gives one for each, in order). rate: the
    blueprint's output in items a second, if known, for tiles per item/s."""
    out = []
    for bp in _blueprints(data):
        covered, roles, counts, machines = set(), dict.fromkeys([*ROLES, "other"], 0), dict.fromkeys([*ROLES, "other"], 0), 0
        for e in bp.get("entities", []):
            tiles = footprint(e) - covered
            r = role(e["name"])
            covered |= tiles
            roles[r] += len(tiles)
            counts[r] += 1
            machines += _prototypes()[e["name"]].get("type") in CRAFTING
        xs, ys = [x for x, _ in covered], [y for _, y in covered]
        width, height = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if covered else (0, 0)
        area = width * height
        out.append({
            "label": bp.get("label", ""), "entities": len(bp.get("entities", [])),
            "width": width, "height": height, "area": area,
            "covered": len(covered), "empty": area - len(covered),
            "empty_share": (area - len(covered)) / area if area else 0.0,
            "roles": roles, "counts": counts, "crafting_machines": machines,
            "area_per_machine": area / machines if machines else None,
            "area_per_rate": area / rate if rate else None,
            "placed_tiles": len(bp.get("tiles", [])),
        })
    return out


TRANSPORT = ("belts", "pipes", "poles")  # what a layout lays as it needs; every other entity is a part


def bound(bp: dict, run: str | None = None, between: int = 0) -> dict:
    """Tiles no layout can fit one blueprint in (docs/layout-optimizer.md, "Gaps across the app"):
    - parts: any layout of the same parts, every entity but belts, pipes and poles (machines,
      inserters, chests, combinators). They can't overlap, so the box holds their own tiles,
      however the belts, pipes and poles are laid; roles: those tiles by role.
    - model, with run (the axis a generator's machines stand along, "x" or "y"): any layout
      in its own template, as wide across the run as built, whose machines can't overlap
      along it: across times the machines of the fullest line end to end (along), with
      between rows between each two where the template needs them. None without run."""
    tiles, roles, lines = set(), {}, {}
    for e in bp.get("entities", []):
        t = footprint(e)
        tiles |= t
        r = role(e["name"])
        if r not in TRANSPORT:
            roles[r] = roles.get(r, 0) + len(t)
        if r == "machines":
            xs, ys = sorted({x for x, _ in t}), sorted({y for _, y in t})
            across, length = ((xs[0], xs[-1]), len(ys)) if run == "y" else ((ys[0], ys[-1]), len(xs))
            lines.setdefault(across, []).append(length)
    xs, ys = {x for x, _ in tiles}, {y for _, y in tiles}
    across = (max(xs) - min(xs) + 1 if run == "y" else max(ys) - min(ys) + 1) if tiles else 0
    along = max((sum(n) + between * (len(n) - 1) for n in lines.values()), default=0)
    roles = {r: roles[r] for r in [*ROLES, "other"] if r in roles}
    return {"parts": sum(roles.values()), "roles": roles, "across": across, "along": along,
            "model": across * along if run and lines else None}


def report(measures: list[dict]) -> str:
    lines = []
    for d in measures:
        share = d["covered"] / d["area"] if d["area"] else 0.0
        lines.append(f"{d['label'] or 'blueprint'}: {d['entities']} entities")
        lines.append(f"  bounding box {d['width']} x {d['height']} = {d['area']} tiles; covered {d['covered']} "
                     f"({share:.0%}), empty {d['empty']} ({d['empty_share']:.0%})")
        lines.append("  covered by role: " + ", ".join(f"{r} {n}" for r, n in d["roles"].items()))
        per = [f"{d['crafting_machines']} crafting machines"]
        if d["area_per_machine"] is not None:
            per.append(f"{d['area_per_machine']:.1f} tiles per machine")
        if d["area_per_rate"] is not None:
            per.append(f"{d['area_per_rate']:.1f} tiles per item/s")
        lines.append("  " + ", ".join(per))
        if d["placed_tiles"]:
            lines.append(f"  {d['placed_tiles']} placed tiles (floor)")
    return "\n".join(lines)
