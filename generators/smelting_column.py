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

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from tools import layout, rates

LANES = ("left", "right")
MODEL = ("y", 0)  # gen's bound (tools/density.bound): furnaces stand along y, none between


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
    r = rates.recipe(recipe)
    if len(r.ingredients) != 1 or len(r.results) != 1:
        raise ValueError(f"{recipe}: a column belt carries one ore and one product")
    crafts = rates.crafts_per_second(furnace, r)
    ((ore, ore_amount),), ((product, product_amount),) = r.ingredients.items(), r.results.items()
    burner = not rates.electric(furnace)
    lane = rates.lane_rate(belt)
    ore_rate, product_rate = ore_amount * crafts, product_amount * crafts
    ore_lanes = 1 if burner else 2
    limit = math.floor(min(lane / product_rate, ore_lanes * lane / ore_rate) + 1e-9)
    return Plan(furnaces or limit, sides, rates.size(furnace), burner, ore, product,
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
    ins_half = rates.half_width(inserter)
    for side, (ore_x, in_x, furnace_x, out_x) in columns.items():
        west = side == "left"
        for k in range(n):
            y = k * s
            bp.entities.append(furnace, tile_position=(furnace_x, y))
            if not p.burner:
                powered.append((furnace_x + s / 2, y + s / 2, rates.half_width(furnace)))
            # Input takes from the ore belt, output drops toward the plate belt.
            for x, pick_from_west in ((in_x, west), (out_x, west)):
                bp.entities.append(inserter, tile_position=(x, y + ins_offset), direction=layout.facing(pick_from_west))
                powered.append((x + 0.5, y + ins_offset + 0.5, ins_half))

    inserter_columns = sorted(c for cols in columns.values() for c in (cols[1], cols[3]))
    free_rows = [k * s + o for k in range(n) for o in range(s) if o != ins_offset]
    for x, y in layout.poles(pole, powered, {c: free_rows for c in inserter_columns}):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


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
