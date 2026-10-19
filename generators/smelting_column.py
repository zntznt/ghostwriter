"""Smelting column: two rows of furnaces sharing one plate belt.

  ore | in | furnace | out | plates | out | furnace | in | ore     (sides=2)
  ore | in | furnace | out | plates                               (sides=1)

All belts run north: ore enters at the bottom, plates leave at the top.
Output inserters drop onto the far lane of the plate belt, so each side
fills one lane. Burner furnaces take fuel from their ore belt: ore on one
lane, fuel on the other. Electric furnaces take ore on both lanes.

`furnaces` (per side) defaults to the most the belts carry: a side's output
fills one plate lane, and its ore comes from one lane (burner) or the whole
ore belt (electric). Poles stand in the inserter columns' free tiles: the
fewest columns, and the fewest rows, that power every inserter (and electric
furnace) within wire reach. Hook the column up by feeding the ore belts from
the bottom, taking plates off the top, and connecting any pole to your grid.
"""

import math
from dataclasses import dataclass
from itertools import combinations

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities, recipes

LANES = ("left", "right")


@dataclass
class Plan:
    furnaces: int  # per side
    sides: int
    size: int  # furnace footprint, tiles
    burner: bool
    ore: str
    product: str
    ore_rate: float  # items/s per furnace
    product_rate: float
    lane: float  # items/s per belt lane
    limit: int  # furnaces per side the belts can carry
    craft: float  # seconds per craft

    @property
    def per_side(self) -> float:
        """Product/s from one side; past `limit` the belts set the rate."""
        ore_lanes = 1 if self.burner else 2
        return min(self.furnaces * self.product_rate, self.lane,
                   ore_lanes * self.lane * self.product_rate / self.ore_rate)


def plan(furnace="stone-furnace", recipe="iron-plate", belt="transport-belt", sides=2, furnaces=0, **_) -> Plan:
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    proto = entities.raw[furnace]
    r = recipes.raw[recipe]
    if len(r["ingredients"]) != 1 or len(r["results"]) != 1:
        raise ValueError(f"{recipe}: a column belt carries one ore and one product")
    crafts = proto["crafting_speed"] / r.get("energy_required", 0.5)
    (ingredient,), (result,) = r["ingredients"], r["results"]
    burner = proto["energy_source"]["type"] == "burner"
    # A belt lane holds 4 items per tile; speed is tiles per tick.
    lane = entities.raw[belt]["speed"] * 60 * 4
    ore_rate, product_rate = ingredient["amount"] * crafts, result["amount"] * crafts
    ore_lanes = 1 if burner else 2
    limit = math.floor(min(lane / product_rate, ore_lanes * lane / ore_rate) + 1e-9)
    (x1, _), (x2, _) = proto["collision_box"]
    return Plan(furnaces or limit, sides, math.ceil(x2 - x1), burner, ingredient["name"], result["name"],
                ore_rate, product_rate, lane, limit, 1 / crafts)


def build(furnace="stone-furnace", recipe="iron-plate", belt="transport-belt", inserter="inserter",
          pole="medium-electric-pole", sides=2, furnaces=0) -> Blueprint:
    p = plan(furnace, recipe, belt, sides, furnaces)
    s, n = p.size, p.furnaces
    length = n * s
    ins_offset = (s - 1) // 2  # inserter row within each furnace's span; the rest is free for poles

    # Columns from the west: ore, in, furnace, out, plates (then mirrored).
    plates_x = s + 3
    columns = {"left": (0, 1, 2, s + 2)}  # ore, input inserter, furnace, output inserter
    if sides == 2:
        columns["right"] = (2 * s + 6, 2 * s + 5, s + 5, s + 4)

    bp = Blueprint()
    total = n * sides
    bp.label = f"Smelting column: {total} {furnace} ({p.product})"
    ore_lane = "one lane of each ore belt, fuel on the other" if p.burner else "both lanes of each ore belt"
    bp.description = (
        f"{n} {furnace} per side x{sides}: {sides * p.per_side:g} {p.product}/s "
        f"from {sides * p.per_side * p.ore_rate / p.product_rate:g} {p.ore}/s. {belt} carries {p.lane:g}/s per lane "
        f"(belt-limited at {p.limit} per side). Ore enters at the bottom on {ore_lane}; "
        f"{p.product} leaves at the top. Connect any pole to your grid."
    )
    bp.icons = [furnace, p.product]

    for y in range(length):
        top, bottom = y == 0, y == length - 1
        bp.entities.append(belt, tile_position=(plates_x, y), direction=Direction.NORTH,
                           **({"id": "plates"} if top else {}))
        for side, (ore_x, _, _, _) in columns.items():
            bp.entities.append(belt, tile_position=(ore_x, y), direction=Direction.NORTH,
                               **({"id": f"ore_{side}"} if bottom else {}))

    powered = []  # (centre x, centre y, half width) of everything that needs power
    ins_half = entities.raw[inserter]["collision_box"][1][0]
    for side, (ore_x, in_x, furnace_x, out_x) in columns.items():
        west = side == "left"
        for k in range(n):
            y = k * s
            bp.entities.append(furnace, tile_position=(furnace_x, y))
            if not p.burner:
                powered.append((furnace_x + s / 2, y + s / 2, entities.raw[furnace]["collision_box"][1][0]))
            # Input takes from the ore belt, output drops toward the plate belt.
            for x, pick_from_west in ((in_x, west), (out_x, west)):
                bp.entities.append(inserter, tile_position=(x, y + ins_offset), direction=_facing(pick_from_west))
                powered.append((x + 0.5, y + ins_offset + 0.5, ins_half))

    inserter_columns = sorted(c for cols in columns.values() for c in (cols[1], cols[3]))
    free_rows = [k * s + o for k in range(n) for o in range(s) if o != ins_offset]
    for x, y in _poles(pole, powered, inserter_columns, free_rows):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def _facing(pick_from_west: bool) -> Direction:
    # An inserter's direction points at the side it picks up from (prototype
    # pickup_position is north of an unrotated inserter); checked in game.
    return Direction.WEST if pick_from_west else Direction.EAST


def _poles(pole, powered, columns, rows):
    """Tiles for poles: the smallest set of columns (from `columns`) whose
    supply areas reach every powered entity across, times the fewest rows
    (from `rows`) that reach them all along, each within wire reach."""
    reach = entities.raw[pole]["maximum_wire_distance"]
    supply = entities.raw[pole]["supply_area_distance"]

    def covers(c, centre, half):  # supply area overlaps the collision box
        return abs(centre - (c + 0.5)) < supply + half

    if not powered:
        return []
    chosen = None
    for size in range(1, len(columns) + 1):
        for subset in combinations(columns, size):
            if all(any(covers(c, x, h) for c in subset) for x, _, h in powered) and \
                    all(b - a <= reach for a, b in zip(subset, subset[1:])):
                chosen = subset
                break
        if chosen:
            break
    if not chosen:
        raise ValueError(f"{pole} can't power every inserter from the inserter columns")

    # Interval point cover: take entities by the last row that still reaches
    # them, and put each new row as far down as it can go.
    placed = []
    for _, y, h in sorted(powered, key=lambda e: e[1] + e[2]):
        if any(covers(r, y, h) for r in placed):
            continue
        options = [r for r in rows if covers(r, y, h) and (not placed or r - placed[-1] <= reach)]
        if not options:
            raise ValueError(f"{pole} can't reach row {y - 0.5:g} within wire reach")
        placed.append(max(options))
    return [(x, y) for y in placed for x in chosen]


def flow_tests(furnace="stone-furnace", recipe="iron-plate", belt="transport-belt", sides=2, furnaces=0, **_):
    from validator.flow import Feed, Flow

    p = plan(furnace, recipe, belt, sides, furnaces)
    feeds = []
    for side in LANES[:sides]:
        fuel = "coal" if p.burner else p.ore
        feeds += [Feed(f"ore_{side}", "left", p.ore), Feed(f"ore_{side}", "right", fuel)]
    total = p.sides * p.per_side
    # A window counts each furnace's crafts to within one, so give it 50:
    # under 2% error against the 3% tolerance.
    return [Flow(f"{p.furnaces * sides} furnaces: {total:g} {p.product}/s", feeds, ["plates"], {p.product: total},
                 window=max(120, 50 * p.craft))]
