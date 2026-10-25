"""Fluid block: a row of chemical plants or refineries with a pipe line per fluid.

For recipes with several fluids: oil processing, cracking, lubricant, sulfur,
sulfuric acid.

    north lines     ═══ fluid 2 ═══          (main lines, further out per fluid)
                    ═══ fluid 1 ═══
                    ┴ ┬ ┴   ┴ ┬ ┴            risers; other fluids pass by underground
    machines       [ M ] g [ M ][ M ] g [ M ]  g = inserter | belt | inserter
    south lines     ═══ fluid 3 ═══

Machines stand unrotated in one row. Each side's connections (a chemical
plant's inputs north and outputs south, a refinery's the other way round)
are joined per fluid: one fluid on a side runs as a single pipe along the
connections; with more, each fluid gets its own main line further out, and
its connections reach it through pipe-to-ground pairs under the lines nearer
in, so fluids never touch. The game assigns a recipe's fluids to a machine's
input boxes in recipe order, and the same for outputs (NOTES.md).

Items (sulfur and iron for acid, sulfur out of a sulfur plant) ride belts
running north through a gap after every second machine: inserters on each
side of the belt serve the machine beside them, and the fluid lines cross
under the belt. A belt carries ingredients or the product, not both.

Feed each fluid at the west end of its line (the description names them);
outputs leave there too. Items enter belts at the bottom and leave at the top.
"""

import math
from dataclasses import dataclass, field

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities, recipes

from tools import layout, rates

MARGIN = 0.85
REGULAR = ("inserter", "fast-inserter")
MACHINES = ("chemical-plant", "oil-refinery")
FACING = {"north": Direction.NORTH, "south": Direction.SOUTH}


@dataclass
class Plan:
    recipe: str
    machine: str
    machines: int
    belt: str
    size: int
    crafts: float  # per machine, per second
    inputs: dict[str, float]  # per craft: fluids and items
    outputs: dict[str, float]
    sides: dict[str, list[tuple[int, str]]]  # side -> (column offset in the machine, fluid)
    items_in: list[str] = field(default_factory=list)
    item_out: str | None = None
    feeders: tuple[str, int] = ("inserter", 0)
    output: tuple[str, int] = ("inserter", 0)
    per_belt: int = 2  # machines a belt serves: one on each side, or one when two need too much

    def fluids(self, side: str) -> list[str]:
        """Fluids on a side, nearest line first."""
        return list(dict.fromkeys(f for _, f in self.sides[side]))

    def rate(self, name: str) -> float:
        """Units (fluid) or items per second the whole block makes or uses."""
        per_craft = self.outputs.get(name) or self.inputs.get(name, 0)
        return self.machines * self.crafts * per_craft


def plan(recipe="heavy-oil-cracking", machine="", machines=4, belt="transport-belt", inserter="",
         research="") -> Plan:
    if recipe not in recipes.raw:
        raise ValueError(f"unknown recipe {recipe!r}")
    r = recipes.raw[recipe]
    fluids_in = [(p["name"], p["amount"]) for p in r["ingredients"] if p.get("type") == "fluid"]
    fluids_out = [(p["name"], p["amount"]) for p in r["results"] if p.get("type") == "fluid"]
    items_in = [(p["name"], p["amount"]) for p in r["ingredients"] if p.get("type", "item") == "item"]
    items_out = [(p["name"], p["amount"]) for p in r["results"] if p.get("type", "item") == "item"]
    if any("probability" in p or "amount" not in p for p in r["results"]):
        raise ValueError(f"{recipe}: variable results")
    categories = r.get("categories", rates.DEFAULT_CATEGORIES)
    machine = machine or next((m for m in MACHINES if set(categories) & set(entities.raw[m]["crafting_categories"])), "")
    if not machine:
        raise ValueError(f"{recipe}: no chemical plant or refinery crafts it")
    crafts = entities.raw[machine]["crafting_speed"] / r.get("energy_required", rates.DEFAULT_CRAFT_TIME)
    s = rates.size(machine)

    # Fluid boxes take the recipe's fluids in order: inputs to input boxes, outputs to output boxes.
    sides = {"north": [], "south": []}
    queues = {"input": [f for f, _ in fluids_in], "output": [f for f, _ in fluids_out]}
    for box in entities.raw[machine]["fluid_boxes"]:
        kind = box.get("production_type")
        if kind not in queues or not queues[kind]:
            continue
        fluid = queues[kind].pop(0)
        for c in box["pipe_connections"]:
            side = {0: "north", 8: "south"}.get(c.get("direction", 0))
            if side is None:
                raise ValueError(f"{machine}: connections on east or west faces aren't supported")
            sides[side].append((math.floor(s / 2 + c["position"][0]), fluid))
    if queues["input"] or queues["output"]:
        raise ValueError(f"{recipe}: more fluids than {machine} has boxes")

    p = Plan(recipe, machine, machines, belt, s, crafts, dict(fluids_in + items_in), dict(fluids_out + items_out),
             sides, [i for i, _ in items_in])
    if items_out:
        if len(items_out) > 1 or items_in:
            raise ValueError(f"{recipe}: a block's belts carry ingredients or one product, not both")
        p.item_out = items_out[0][0]
    if len(items_in) > 2:
        raise ValueError(f"{recipe}: a belt carries at most two ingredients")
    tiers = (inserter,) if inserter else REGULAR
    if items_in:
        p.feeders = rates.fewest_inserters(sum(a for _, a in items_in) * crafts, belt, True, tiers, MARGIN, research)
    if p.item_out:
        p.output = rates.fewest_inserters(dict(items_out)[p.item_out] * crafts, belt, False, tiers, MARGIN,
                                          research)
    if p.feeders[1] + p.output[1] > s:
        raise ValueError(f"{recipe} in {machine}: one machine needs {p.feeders[1] + p.output[1]} inserters "
                         f"on its belt side, where {s} fit")
    # A belt serves the machines on both sides of it, or just one when two would
    # need more than its lanes carry.
    lane = rates.lane_rate(belt)

    def fits(machines_per_belt):
        lanes = 2 if len(items_in) == 1 else 1
        return all(machines_per_belt * a * crafts <= lanes * lane for _, a in items_in)

    p.per_belt = 2 if fits(2) else 1
    if not fits(1):
        raise ValueError(f"{recipe}: one machine needs more than a {belt} carries; try a faster belt")
    if p.item_out and dict(items_out)[p.item_out] * crafts > lane:
        raise ValueError(f"{recipe}: one machine makes more than a {belt} lane carries")
    return p


def build(recipe="heavy-oil-cracking", machine="", machines=4, belt="transport-belt", inserter="",
          pole="medium-electric-pole", research="") -> Blueprint:
    p = plan(recipe, machine, machines, belt, inserter, research)
    s, n = p.size, p.machines
    items = bool(p.items_in or p.item_out)

    # Columns: machines side by side; with items, a gap (inserter, belt, inserter)
    # after every second machine (or every machine), so each has one face on a belt.
    x0s, gaps, x = [], [], 0
    for m in range(n):
        x0s.append(x)
        x += s
        if items and m % p.per_belt == 0:
            gaps.append({"left": x, "belt": x + 1, "right": x + 2, "machines": [m]})
            x += 3
        elif items:
            gaps[-1]["machines"].append(m)
    width = x
    rows = {side: (0 if not p.fluids(side) else 1 if len(p.fluids(side)) == 1 else 2 * len(p.fluids(side)))
            for side in ("north", "south")}

    def y(side, j):  # row j (1 = next to the machines) on a side
        return -j if side == "north" else s - 1 + j

    bp = Blueprint()
    bp.label = f"Fluid block: {n} {p.machine} ({p.recipe})"
    taken = set()

    def put(name, tile, **kw):
        bp.entities.append(name, tile_position=tile, **kw)
        taken.add(tile)

    hops = {g["left"]: "west" for g in gaps} | {g["right"]: "east" for g in gaps}
    belt_x = {g["belt"] for g in gaps}

    def main_line(side, j, fluid):
        """A pipe along row j from one tile west of the block, under each belt by pipe-to-ground."""
        for col in range(-1, width):
            if col in belt_x:
                continue
            if col in hops:
                put("pipe-to-ground", (col, y(side, j)), direction=Direction.WEST if hops[col] == "west" else Direction.EAST)
            else:
                put("pipe", (col, y(side, j)), **({"id": f"{side}_{fluid}"} if col == -1 else {}))

    for side in ("north", "south"):
        fluids = p.fluids(side)
        toward, outward = (Direction.SOUTH, Direction.NORTH) if side == "north" else (Direction.NORTH, Direction.SOUTH)
        if len(fluids) == 1:
            main_line(side, 1, fluids[0])
        for i, fluid in enumerate(fluids, start=1):
            if len(fluids) == 1:
                break
            main_line(side, 2 * i, fluid)
            for x0 in x0s:
                for offset, f in p.sides[side]:
                    if f != fluid:
                        continue
                    if i == 1:
                        put("pipe", (x0 + offset, y(side, 1)))
                    else:  # under the nearer lines to its own
                        put("pipe-to-ground", (x0 + offset, y(side, 1)), direction=toward)
                        put("pipe-to-ground", (x0 + offset, y(side, 2 * i - 1)), direction=outward)

    powered = []
    for m, x0 in enumerate(x0s):
        put(p.machine, (x0, 0), recipe=p.recipe)
        taken.update((x0 + dx, dy) for dx in range(s) for dy in range(s))
        powered.append((x0 + s / 2, s / 2, rates.half_width(p.machine)))

    top, bottom = -rows["north"], s - 1 + rows["south"]
    for g, gap in enumerate(gaps):
        for row in range(top, bottom + 1):
            ref = {"id": f"belt{g}_in"} if row == bottom else {"id": f"belt{g}_out"} if row == top else {}
            put(p.belt, (gap["belt"], row), direction=Direction.NORTH, **ref)
        for m in gap["machines"]:
            col = gap["left"] if x0s[m] < gap["belt"] else gap["right"]
            belt_west = col > gap["belt"]  # the belt is west of this inserter column
            jobs = [(p.feeders, belt_west), (p.output, not belt_west)]
            row = 0
            for (inserter, k), pick_from_west in jobs:
                for _ in range(k):
                    put(inserter, (col, row), direction=layout.facing(pick_from_west))
                    powered.append((col + 0.5, row + 0.5, rates.half_width(inserter)))
                    row += 1

    free = {(cx, cy) for cx in range(0, width) for cy in range(top - 1, bottom + 2)} - taken - belt_x_tiles(belt_x, top, bottom)
    for tile in layout.poles_anywhere(pole, powered, free):
        put(pole, tile)
    bp.generate_power_connections()

    lines = "; ".join(f"{side}: " + ", ".join(p.fluids(side)) for side in ("north", "south") if p.fluids(side))
    made = ", ".join(f"{p.rate(o):g} {o}/s" for o in p.outputs)
    used = ", ".join(f"{p.rate(i):g} {i}/s" for i in p.inputs)
    belts = ""
    if gaps:
        belts = (f" {len(gaps)} belt(s) between machine pairs carry " +
                 (" and ".join(p.items_in) + " (enter at the bottom)" if p.items_in else f"{p.item_out} (leaves at the top)") + ".")
        if p.items_in:
            belts = belts.replace("carry ", "carry " + ("both lanes of " if len(p.items_in) == 1 else ""))
    bp.description = (f"{n} {p.machine}: {made} from {used}. Lines, nearest first, fed or emptied at their west "
                      f"end: {lines}.{belts} Connect any pole to your grid.")
    bp.icons = [p.machine]
    return bp


def belt_x_tiles(belt_x, top, bottom):
    return {(bx, by) for bx in belt_x for by in range(top - 1, bottom + 2)}


def flow_tests(**params):
    from validator.flow import Feed, FluidFeed, Flow

    p = plan(**{k: v for k, v in params.items() if k != "pole"})
    n_gaps = -(-p.machines // p.per_belt) if (p.items_in or p.item_out) else 0
    fluids, feeds, drains = [], [], []
    for side in ("north", "south"):
        for fluid in p.fluids(side):
            fluids.append(FluidFeed(f"{side}_{fluid}", fluid, side="west", sink=fluid in p.outputs))
    for g in range(n_gaps):
        for lane, item in zip(("left", "right"), p.items_in * 2 if len(p.items_in) == 1 else p.items_in):
            feeds.append(Feed(f"belt{g}_in", lane, item))
        if p.item_out:
            drains.append(f"belt{g}_out")
    expect = {o: p.rate(o) for o in p.outputs}
    length = rates.size(p.machine) + 8
    return [Flow(f"{p.machines} {p.machine}: " + ", ".join(f"{v:g} {k}/s" for k, v in expect.items()),
                 feeds, drains, expect, fluids=fluids,
                 warmup=rates.warmup_seconds(p.belt, length, 1 / p.crafts), window=max(120, 50 / p.crafts),
                 research=rates.research_levels(params.get("research", "")))]
