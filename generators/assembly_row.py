"""Assembly row: assemblers on both sides of one product belt, for any item recipe.

  in2 | in1 | ins | machine | out | product | out | machine | ins | in1 | in2   (sides=2)

All belts run north: ingredients enter at the bottom, the product leaves at
the top. Each side's output inserters drop onto the far lane of the product
belt, so a side fills one lane. Ingredients go onto input belts and lanes so
the most machines fit: one item on a belt takes both lanes, two take one
each. A second input belt (in2) feeds through long-handed inserters reaching
over the first.

Inserters per machine follow from the rates measured in game
(tools/inserter_rates.json, no research): for each job, the tier that needs
the fewest inserters, then the cheapest, keeping a 15% margin. They sit in
the column between belt and machine; poles take the column tiles left over,
or a one-row gap after each machine when there are none.

`machines` (per side) defaults to the most the belts carry. The description
names each belt lane's item. Recipes with fluids are refused.
"""

import math
from dataclasses import dataclass
from itertools import permutations

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from tools import layout, rates

MARGIN = 0.85  # use at most this share of an inserter's measured rate
REGULAR = ("inserter", "fast-inserter")
LONG = "long-handed-inserter"
LANES = ("left", "right")


@dataclass
class Plan:
    recipe: rates.Recipe
    machine: str
    belt: str
    sides: int
    machines: int  # per side
    limit: int  # machines per side the belts carry
    crafts: float  # per machine, per second
    product: str
    lanes: list[dict[str, str]]  # per input belt (inner first): lane -> item
    feeders: list[tuple[str, int]]  # per input belt: inserter, count per machine
    output: tuple[str, int]  # inserter, count per machine
    size: int  # machine footprint
    gap: int = 0  # empty rows after each machine, for poles when the columns are full

    @property
    def per_side(self) -> float:
        """Product/s from one side; past `limit` the belts set the rate."""
        r, lane = self.recipe, rates.lane_rate(self.belt)
        out = r.results[self.product]
        caps = [self.machines * self.crafts * out, lane]
        for lanes in self.lanes:
            for item in set(lanes.values()):
                n = sum(1 for i in lanes.values() if i == item)
                caps.append(n * lane / r.ingredients[item] * out)
        return min(caps)


def plans(recipe="iron-gear-wheel", machine="assembling-machine-2", belt="transport-belt", sides=2,
          machines=0, inserter="", input_belts=0) -> list[Plan]:
    """Input layouts that fit the belts and inserters, best first."""
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    r = rates.recipe(recipe)
    if len(r.results) != 1:
        raise ValueError(f"{recipe}: a row carries one product")
    crafts = rates.crafts_per_second(machine, r)
    (product, out_amount), = r.results.items()
    lane = rates.lane_rate(belt)
    s = rates.size(machine)
    tiers = (inserter,) if inserter else REGULAR

    def pick(demand, from_belt, options):
        return rates.fewest_inserters(demand, belt, from_belt, options, MARGIN)

    candidates, why, fewest = [], set(), None
    groupings = _groupings(list(r.ingredients), input_belts)
    if not groupings:
        why.add(f"{len(r.ingredients)} ingredients don't fit {input_belts or 'one or two'} input belts "
                "at two per belt")
    for groups in groupings:
        lanes = [dict(zip(LANES, g * 2 if len(g) == 1 else g)) for g in groups]
        per_lane = {}
        for ls in lanes:
            for item in ls.values():
                per_lane[item] = per_lane.get(item, 0) + 1
        limit = math.floor(min([lane / (out_amount * crafts)] +
                               [n * lane / (r.ingredients[i] * crafts) for i, n in per_lane.items()]) + 1e-9)
        feeders = []
        for b, g in enumerate(groups):
            demand = sum(r.ingredients[i] for i in g) * crafts
            feeders.append(pick(demand, True, tiers if b == 0 else (LONG,)))
        output = pick(out_amount * crafts, False, tiers)
        if limit < 1:
            why.add(f"one machine needs more than a {belt} lane carries")
            continue
        if sum(k for _, k in feeders) > s or output[1] > s:
            need = max(sum(k for _, k in feeders), output[1])
            fewest = need if fewest is None else min(fewest, need)
            continue
        candidates.append(Plan(r, machine, belt, sides, 0, limit, crafts, product, lanes, feeders, output, s))
    if fewest is not None:
        why.add(f"one machine needs at least {fewest} inserters on a side where {s} fit")
    if not candidates:
        hints = (["a slower machine"] if any("inserters" in w or "lane" in w for w in why) else []) + \
                (["a faster belt"] if any("lane" in w for w in why) else [])
        raise ValueError(f"{recipe} in {machine} on {belt}: no input layout fits ({'; '.join(sorted(why))})"
                         + (f"; try {' or '.join(hints)}" if hints else ""))
    # Most machines first; with a fixed count, the fewest belts that carry it.
    candidates.sort(key=lambda p: (-p.limit, len(p.lanes)))
    if machines:
        fitting = sorted((p for p in candidates if p.limit >= machines), key=lambda p: len(p.lanes))
        candidates = fitting + [p for p in candidates if p not in fitting]
    for p in candidates:
        p.machines = machines or p.limit
    return candidates


def _groupings(items, input_belts):
    """Ways to put ingredients on input belts (inner first), at most two per belt."""
    seen = []
    for belts in ([input_belts] if input_belts else (1, 2)):
        if belts == 1 and len(items) <= 2:
            seen.append([items])
        if belts == 2 and 2 <= len(items) <= 4:
            for order in permutations(items):
                for cut in range(1, len(order)):
                    inner, outer = sorted(order[:cut]), sorted(order[cut:])
                    if len(inner) <= 2 and len(outer) <= 2 and [inner, outer] not in seen:
                        seen.append([inner, outer])
    return seen


def plan(pole="medium-electric-pole", **params) -> Plan:
    """The best of plans() that also leaves room for poles."""
    errors = []
    for gap in (0, 1):
        for p in plans(**params):
            p.gap = gap
            try:
                _build(p, pole)
                return p
            except ValueError as e:
                errors.append(str(e))
    raise ValueError(f"{p.recipe.name}: no input layout leaves room to power it ({'; '.join(errors)})")


def build(recipe="iron-gear-wheel", machine="assembling-machine-2", belt="transport-belt", sides=2,
          machines=0, inserter="", input_belts=0, pole="medium-electric-pole") -> Blueprint:
    p = plan(pole, recipe=recipe, machine=machine, belt=belt, sides=sides, machines=machines,
             inserter=inserter, input_belts=input_belts)
    return _build(p, pole)


def _build(p: Plan, pole: str) -> Blueprint:
    s, n, nb = p.size, p.machines, len(p.lanes)
    pitch = s + p.gap
    length = n * pitch
    o = nb - 1
    # Columns from the west: [in2,] in1, inserters, machine, out, product (then mirrored).
    product_x = o + 3 + s
    columns = {"left": {"belts": [o - b for b in range(nb)], "ins": o + 1, "machine": o + 2, "out": o + 2 + s}}
    if p.sides == 2:
        columns["right"] = {"belts": [o + 6 + 2 * s + b for b in range(nb)], "ins": o + 5 + 2 * s,
                            "machine": o + 5 + s, "out": o + 4 + s}

    bp = Blueprint()
    total = n * p.sides
    bp.label = f"Assembly row: {total} {p.machine} ({p.recipe.name})"
    belts_text = "; ".join(
        f"in{b + 1}: " + (f"{ls['left']} on both lanes" if ls["left"] == ls["right"]
                          else f"{ls['left']} left lane, {ls['right']} right lane")
        for b, ls in enumerate(p.lanes))
    bp.description = (
        f"{n} {p.machine} per side x{p.sides}: {p.sides * p.per_side:g} {p.product}/s "
        f"(belt-limited at {p.limit} per side). Ingredients enter at the bottom of each side's "
        f"input belts, {belts_text}. {p.product} leaves at the top. Connect any pole to your grid."
    )
    bp.icons = [p.machine, p.product]

    for y in range(length):
        top, bottom = y == 0, y == length - 1
        bp.entities.append(p.belt, tile_position=(product_x, y), direction=Direction.NORTH,
                           **({"id": "product"} if top else {}))
        for side, c in columns.items():
            for b, x in enumerate(c["belts"]):
                bp.entities.append(p.belt, tile_position=(x, y), direction=Direction.NORTH,
                                   **({"id": f"in{b + 1}_{side}"} if bottom else {}))

    powered = []  # (centre x, centre y, half width)
    used_in = sum(k for _, k in p.feeders)
    used_out = p.output[1]
    for side, c in columns.items():
        west = side == "left"
        for m in range(n):
            y0 = m * pitch
            bp.entities.append(p.machine, tile_position=(c["machine"], y0), recipe=p.recipe.name)
            powered.append((c["machine"] + s / 2, y0 + s / 2, rates.half_width(p.machine)))
            row = 0
            for inserter, k in p.feeders:
                for _ in range(k):
                    bp.entities.append(inserter, tile_position=(c["ins"], y0 + row), direction=layout.facing(west))
                    powered.append((c["ins"] + 0.5, y0 + row + 0.5, rates.half_width(inserter)))
                    row += 1
            inserter, k = p.output
            for row in range(k):
                bp.entities.append(inserter, tile_position=(c["out"], y0 + row), direction=layout.facing(west))
                powered.append((c["out"] + 0.5, y0 + row + 0.5, rates.half_width(inserter)))

    free = {}
    for c in columns.values():
        free[c["ins"]] = [m * pitch + r for m in range(n) for r in range(used_in, pitch)]
        free[c["out"]] = [m * pitch + r for m in range(n) for r in range(used_out, pitch)]
    for x, y in layout.poles(pole, powered, free):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def flow_tests(**params):
    from validator.flow import Feed, Flow

    p = plan(**params)
    feeds = [Feed(f"in{b + 1}_{side}", lane, item)
             for side in LANES[:p.sides] for b, ls in enumerate(p.lanes) for lane, item in ls.items()]
    total = p.sides * p.per_side
    # A window counts each machine's crafts to within one, so give it 50.
    return [Flow(f"{p.machines * p.sides} machines: {total:g} {p.product}/s", feeds, ["product"],
                 {p.product: total}, warmup=rates.warmup_seconds(p.belt, p.machines * (p.size + p.gap), 1 / p.crafts),
                 window=max(120, 50 / p.crafts))]
