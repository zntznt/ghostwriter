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

With beacons (electric furnaces only: burner furnaces take no beacon effects),
each furnace column runs beacon, furnace, beacon, ... beacon. The columns are
too far apart for a beacon to reach across, so each furnace takes the two
beside it; inserters and the belt limit follow the beaconed speed.
"""

import math
from dataclasses import dataclass

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities

from generators.fluid_row import _reaching, _spots
from tools import layout, rates

LANES = ("left", "right")
BEACON = "beacon"
INSERTERS = ("inserter", "fast-inserter", "bulk-inserter")
MARGIN = 0.85  # use at most this share of an inserter's measured rate


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
    inserters: tuple[str, str]  # input, output
    furnace: str = "stone-furnace"
    recipe: str = "iron-plate"
    modules: str = ""
    beacons: str = ""  # in every beacon's slots; no beacons without
    beacons_per_furnace: int = 0
    belt: str = "transport-belt"

    @property
    def per_side(self) -> float:
        """Product/s from one side; past `limit` the belts set the rate."""
        ore_lanes = 1 if self.burner else 2
        return min(self.furnaces * self.product_rate, self.lane,
                   ore_lanes * self.lane * self.product_rate / self.ore_rate)

    def watts(self, share: float = 1) -> float:
        """Average electric W making `share` of the rated output: electric furnaces and inserters."""
        n, made = self.furnaces * self.sides, share * self.sides * self.per_side
        w = n * rates.machine_watts(self.furnace, rates.recipe(self.recipe), self.modules, self.beacons,
                                    self.beacons_per_furnace, made / (n * self.product_rate))
        w += self.sides * (self.furnaces + 1) * rates.beacon_watts() if self.beacons else 0
        return w + rates.inserter_watts(self.inserters[0], self.belt, n, made * self.ore_rate / self.product_rate) \
            + rates.inserter_watts(self.inserters[1], self.belt, n, made)


def plan(furnace="stone-furnace", recipe="iron-plate", belt="transport-belt", sides=2, furnaces=0, modules="",
         inserter="", beacons="", **_) -> Plan:
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    if beacons and not rates.takes_beacons(furnace):
        raise ValueError(f"{furnace} takes no beacon effects: beacons need an electric furnace")
    r = rates.recipe(recipe)
    if len(r.ingredients) != 1 or len(r.results) != 1:
        raise ValueError(f"{recipe}: a column belt carries one ore and one product")
    per = beacons_per_furnace(furnace, sides) if beacons else 0
    crafts = rates.crafts_per_second(furnace, r, modules, beacons, per)
    r = rates.made(furnace, r, modules)
    ((ore, ore_amount),), ((product, product_amount),) = r.ingredients.items(), r.results.items()
    burner = not rates.electric(furnace)
    lane = rates.lane_rate(belt)
    ore_rate, product_rate = ore_amount * crafts, product_amount * crafts
    ore_lanes = 1 if burner else 2
    limit = math.floor(min(lane / product_rate, ore_lanes * lane / ore_rate) + 1e-9)
    # Out-of-step furnaces (productivity) leave gaps on the plate lane too short for a
    # basic inserter to drop into: measured 5 to 10% short in game, fast inserters keep up.
    outs = INSERTERS[1:] if rates.productivity(furnace, r, modules) else INSERTERS
    inserters = []
    for demand, from_belt, options in ((ore_rate, True, INSERTERS), (product_rate, False, outs)):
        picked, count = rates.fewest_inserters(demand, belt, from_belt, (inserter,) if inserter else options, MARGIN)
        if count > 1 and not inserter:
            raise ValueError(f"one {furnace} moves {demand:g}/s, more than one {picked} does on {belt}")
        inserters.append(picked)
    return Plan(furnaces or limit, sides, rates.size(furnace), burner, ore, product,
                ore_rate, product_rate, lane, limit, 1 / crafts, tuple(inserters), furnace, recipe, modules,
                beacons, per, belt)


def beacons_per_furnace(furnace: str, sides: int) -> int:
    """Beacons reaching each furnace of a beaconed column: the fewest any furnace gets (all
    do alike)."""
    s, bs = rates.size(furnace), rates.size(BEACON)
    furnaces, beacons = _spots(s, bs, 0, 3, [2, s + 5][:sides])
    return min(_reaching(furnace, f, beacons) for f in furnaces)


def build(furnace="stone-furnace", recipe="iron-plate", belt="transport-belt", inserter="",
          pole="medium-electric-pole", sides=2, furnaces=0, modules="", beacons="") -> Blueprint:
    """inserter: both inserters of every furnace; by default the slowest that keeps up.
    beacons: a module in every slot of beacons between the furnaces, the column sized for
    their speed."""
    p = plan(furnace, recipe, belt, sides, furnaces, modules, inserter, beacons)
    s, n = p.size, p.furnaces
    bs = rates.size(BEACON) if beacons else 0  # beacon rows before each furnace and after the last
    length = n * (s + bs) + bs
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
        f"{p.product} leaves at the top. "
        + (f"{p.beacons_per_furnace} beacons of {beacons} reach each furnace. " if beacons else "")
        + f"Draws about {rates.megawatts(p.watts())}. Connect any pole to your grid."
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
    for side, (ore_x, in_x, furnace_x, out_x) in columns.items():
        west = side == "left"
        spots, beacons_at = _spots(s, bs, 0, n, [furnace_x])
        for x, y in beacons_at:
            bp.entities.append(BEACON, tile_position=(x, y))
            bp.entities[-1].request_modules(beacons, range(entities.raw[BEACON]["module_slots"]))
            powered.append((x + bs / 2, y + bs / 2, rates.half_width(BEACON)))
        for _, y in spots:
            bp.entities.append(furnace, tile_position=(furnace_x, y))
            rates.request_modules(bp.entities[-1], furnace, rates.recipe(recipe), modules)
            if not p.burner:
                powered.append((furnace_x + s / 2, y + s / 2, rates.half_width(furnace)))
            # Input takes from the ore belt, output drops toward the plate belt.
            for x, ins in zip((in_x, out_x), p.inserters):
                bp.entities.append(ins, tile_position=(x, y + ins_offset), direction=layout.facing(west))
                powered.append((x + 0.5, y + ins_offset + 0.5, rates.half_width(ins)))

    inserter_columns = sorted(c for cols in columns.values() for c in (cols[1], cols[3]))
    taken = {y + ins_offset for _, y in _spots(s, bs, 0, n, [0])[0]}
    free_rows = [y for y in range(length) if y not in taken]
    for x, y in layout.poles(pole, powered, {c: free_rows for c in inserter_columns},
                             layout.open_tiles(bp)):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def flow_tests(furnace="stone-furnace", recipe="iron-plate", belt="transport-belt", sides=2, furnaces=0, modules="",
               beacons="", **_):
    from validator.flow import Feed, Flow

    p = plan(furnace, recipe, belt, sides, furnaces, modules, beacons=beacons)
    feeds = []
    for side in LANES[:sides]:
        fuel = "coal" if p.burner else p.ore
        feeds += [Feed(f"ore_{side}", "left", p.ore), Feed(f"ore_{side}", "right", fuel)]
    total = p.sides * p.per_side
    # A window counts each furnace's crafts to within one, so give it 50:
    # under 2% error against the 3% tolerance.
    return [Flow(f"{p.furnaces * sides} furnaces: {total:g} {p.product}/s", feeds, ["plates"], {p.product: total},
                 window=max(120, 50 * p.craft))]
