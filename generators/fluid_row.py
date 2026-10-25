"""Fluid row: machines on both sides of one pipe, for recipes with one fluid ingredient.

  in2 | in1 | ins | machine | pipe | machine | ins | in1 | in2   (sides=2)

Every machine is turned so its fluid inputs face the shared pipe in the
middle; feed the fluid at either end of it. Items come from the outer side:
in1 runs beside the inserters, in2 beyond it through long-handed inserters.
The product leaves on in1's far lane (output inserters drop there), so in1's
near lane carries at most one ingredient. All belts run north: ingredients
enter at the bottom, the product leaves at the top of each side's in1.

Chemical plants (plastic, batteries, explosives, solid fuel) and assembling
machines 2 and 3 (processing units, electric engines, rocket fuel) both fit.
Recipes with two fluids, or a fluid product, are refused.

Inserters per machine follow the measured rates (tools/inserter_rates.json)
with a 15% margin, and poles take the inserter-column tiles left over, or a
one-row gap after each machine when there are none. `machines` (per side)
defaults to the most the belts carry; the description names each lane's item
and the fluid flow the pipe must supply.
"""

import math
from dataclasses import dataclass
from itertools import combinations

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities

from tools import layout, rates

MARGIN = 0.85
REGULAR = ("inserter", "fast-inserter")
LONG = "long-handed-inserter"
MACHINES = ("chemical-plant", "assembling-machine-2", "assembling-machine-3")
# Basic inserters can't fill a product lane to the top: explosives (2 per craft,
# 0.5/s per plant) reached 87% of a yellow lane but not 93%; fast ones reached
# 100% (NOTES.md). Above this fill, outputs use fast inserters.
BASIC_FILL = 0.85
SIDES = ("left", "right")


@dataclass
class Plan:
    recipe: rates.Recipe
    machine: str
    belt: str
    sides: int
    machines: int  # per side
    limit: int
    crafts: float  # per machine, per second
    product: str
    fluid: str
    near: str | None  # ingredient on in1's near lane
    far: list[str]  # ingredients on in2 (both lanes for one)
    feeders: list[tuple[str, int]]  # (inserter, count) for in1, then in2
    output: tuple[str, int]
    size: int
    gap: int = 0

    @property
    def per_side(self) -> float:
        """Product/s from one side; past `limit` the belts set the rate."""
        r, lane = self.recipe, rates.lane_rate(self.belt)
        out = r.results[self.product]
        caps = [self.machines * self.crafts * out, lane]
        if self.near:
            caps.append(lane / r.ingredients[self.near] * out)
        for item in self.far:
            caps.append((2 if len(self.far) == 1 else 1) * lane / r.ingredients[item] * out)
        return min(caps)

    @property
    def fluid_rate(self) -> float:
        """Units/s of the fluid the whole row draws at its rated output."""
        r = self.recipe
        return self.sides * self.per_side / r.results[self.product] * r.fluids[self.fluid]


def plans(recipe="plastic-bar", machine="", belt="transport-belt", sides=2, machines=0, inserter="",
          research="") -> list[Plan]:
    """Ingredient layouts that fit the belts and inserters, best first."""
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    r = rates.recipe(recipe, fluids=True)
    if len(r.fluids) != 1:
        raise ValueError(f"{recipe}: a fluid row takes exactly one fluid ingredient, not {len(r.fluids)}")
    if len(r.results) != 1:
        raise ValueError(f"{recipe}: a row carries one product")
    machine = machine or next((m for m in MACHINES if _crafts(m, r)), MACHINES[0])
    crafts = rates.crafts_per_second(machine, r)
    _input_face(machine)  # refuses machines whose fluid inputs aren't on one face
    (product, out), = r.results.items()
    (fluid,) = r.fluids
    lane = rates.lane_rate(belt)
    s = rates.size(machine)
    tiers = (inserter,) if inserter else REGULAR

    def pick(demand, from_belt, options):
        return rates.fewest_inserters(demand, belt, from_belt, options, MARGIN, research) if demand else (options[0], 0)

    candidates, why, fewest = [], set(), None
    solids = list(r.ingredients)
    for near in [None] + solids:
        rest = [i for i in solids if i != near]
        if len(rest) > 2:
            continue
        caps = [lane / (out * crafts)]
        if near:
            caps.append(lane / (r.ingredients[near] * crafts))
        for item in rest:
            caps.append((2 if len(rest) == 1 else 1) * lane / (r.ingredients[item] * crafts))
        limit = math.floor(min(caps) + 1e-9)
        feeders = [pick(r.ingredients[near] * crafts if near else 0, True, tiers),
                   pick(sum(r.ingredients[i] for i in rest) * crafts, True, (LONG,))]
        output = pick(out * crafts, False, tiers)
        if limit < 1:
            why.add(f"one machine needs more than a {belt} lane carries")
            continue
        used = sum(k for _, k in feeders) + output[1]
        if used > s:
            fewest = used if fewest is None else min(fewest, used)
            continue
        candidates.append(Plan(r, machine, belt, sides, 0, limit, crafts, product, fluid, near, sorted(rest),
                               feeders, output, s))
    if fewest is not None:
        why.add(f"one machine needs at least {fewest} inserters on a side where {s} fit")
    if not candidates:
        hints = (["a slower machine"] if why else []) + (["a faster belt"] if any("lane" in w for w in why) else [])
        raise ValueError(f"{recipe} in {machine} on {belt}: no layout fits ({'; '.join(sorted(why))})"
                         + (f"; try {' or '.join(hints)}" if hints else ""))
    # Most machines first; then a product belt that carries nothing else, so no
    # leftover ingredient leaves with the product; then without the second belt.
    candidates.sort(key=lambda p: (-p.limit, p.near is not None, len(p.far) > 0))
    if machines:
        fitting = sorted((p for p in candidates if p.limit >= machines), key=lambda p: len(p.far) > 0)
        candidates = fitting + [p for p in candidates if p not in fitting]
    for p in candidates:
        p.machines = machines or p.limit
        fill = min(p.machines * crafts * out, lane) / lane
        if not inserter and p.output[0] == "inserter" and fill > BASIC_FILL:
            p.output = pick(out * crafts, False, ("fast-inserter",))
    return candidates


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
    raise ValueError(f"{p.recipe.name}: no layout leaves room to power it ({'; '.join(errors)})")


def build(recipe="plastic-bar", machine="", belt="transport-belt", sides=2, machines=0, inserter="",
          pole="medium-electric-pole", research="") -> Blueprint:
    p = plan(pole, recipe=recipe, machine=machine, belt=belt, sides=sides, machines=machines, inserter=inserter,
             research=research)
    return _build(p, pole)


def _crafts(machine, r) -> bool:
    try:
        rates.crafts_per_second(machine, r)
        return True
    except ValueError:
        return False


def _input_face(machine) -> int:
    """The direction (0, 4, 8, 12) every fluid input of the unrotated machine faces."""
    faces = {c.get("direction", 0) for fb in entities.raw[machine].get("fluid_boxes", [])
             if fb.get("production_type") == "input" for c in fb.get("pipe_connections", [])}
    if len(faces) != 1:
        raise ValueError(f"{machine}: fluid inputs aren't all on one face")
    return faces.pop()


def _build(p: Plan, pole: str) -> Blueprint:
    s, n = p.size, p.machines
    pitch = s + p.gap
    length = n * pitch
    belts = 2 if p.far else 1
    o = belts - 1
    pipe_x = o + 2 + s
    columns = {"left": {"in": [o - b for b in range(belts)], "ins": o + 1, "machine": o + 2, "west": True}}
    if p.sides == 2:
        columns["right"] = {"in": [pipe_x + 2 + s + b for b in range(belts)], "ins": pipe_x + 1 + s,
                            "machine": pipe_x + 1, "west": False}
    # Turn each machine so its fluid inputs face the pipe: east on the left, west on the right.
    face = _input_face(p.machine)
    turn = {"left": Direction((4 - face) % 16), "right": Direction((12 - face) % 16)}

    bp = Blueprint()
    total = n * p.sides
    bp.label = f"Fluid row: {total} {p.machine} ({p.recipe.name})"
    lanes = []
    if p.near:
        lanes.append(f"in1 near lane: {p.near}")
    if p.far:
        lanes.append("in2: " + (f"{p.far[0]} on both lanes" if len(p.far) == 1 else
                                f"{p.far[0]} and {p.far[1]}, one lane each"))
    bp.description = (
        f"{n} {p.machine} per side x{p.sides}: {p.sides * p.per_side:g} {p.product}/s "
        f"(belt-limited at {p.limit} per side), drawing {p.fluid_rate:g} {p.fluid}/s from the middle pipe; "
        f"feed it at either end. Items enter at the bottom: {'; '.join(lanes) or 'none'}. "
        f"{p.product} leaves at the top on each side's in1 far lane"
        + (f", with leftover {p.near} on the near lane" if p.near else "") + ". Connect any pole to your grid."
    )
    bp.icons = [p.machine, p.product]

    for y in range(length):
        bp.entities.append("pipe", tile_position=(pipe_x, y), **({"id": "fluid_in"} if y == length - 1 else {}))
        for side, c in columns.items():
            for b, x in enumerate(c["in"]):
                ref = {"id": f"in{b + 1}_{side}"} if y == length - 1 else {}
                bp.entities.append(p.belt, tile_position=(x, y), direction=Direction.NORTH, **ref)
    # in1 runs one tile past the top machine, so the product leaves clear of the
    # top inserters' pickup tile.
    for side, c in columns.items():
        bp.entities.append(p.belt, tile_position=(c["in"][0], -1), direction=Direction.NORTH, id=f"out_{side}")

    powered = []
    used = sum(k for _, k in p.feeders) + p.output[1]
    for side, c in columns.items():
        west = c["west"]
        for m in range(n):
            y0 = m * pitch
            bp.entities.append(p.machine, tile_position=(c["machine"], y0), direction=turn[side],
                               recipe=p.recipe.name)
            powered.append((c["machine"] + s / 2, y0 + s / 2, rates.half_width(p.machine)))
            jobs = [(i, k, west) for i, k in p.feeders] + [(p.output[0], p.output[1], not west)]
            row = 0
            for inserter, k, pick_from_west in jobs:
                for _ in range(k):
                    bp.entities.append(inserter, tile_position=(c["ins"], y0 + row),
                                       direction=layout.facing(pick_from_west))
                    powered.append((c["ins"] + 0.5, y0 + row + 0.5, rates.half_width(inserter)))
                    row += 1

    free = {c["ins"]: [m * pitch + r for m in range(n) for r in range(used, pitch)] for c in columns.values()}
    for x, y in layout.poles(pole, powered, free):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def flow_tests(**params):
    from validator.flow import Feed, FluidFeed, Flow

    p = plan(**params)
    feeds = []
    for side in SIDES[:p.sides]:
        # in1's near lane is the one beside the inserters: east (right) on the left side.
        near_lane = "right" if side == "left" else "left"
        if p.near:
            feeds.append(Feed(f"in1_{side}", near_lane, p.near))
        for lane, item in zip(("left", "right"), p.far * 2 if len(p.far) == 1 else p.far):
            feeds.append(Feed(f"in2_{side}", lane, item))
    total = p.sides * p.per_side
    length = p.machines * (p.size + p.gap)
    return [Flow(f"{p.machines * p.sides} machines: {total:g} {p.product}/s", feeds,
                 [f"out_{side}" for side in SIDES[:p.sides]], {p.product: total},
                 warmup=rates.warmup_seconds(p.belt, length, 1 / p.crafts),
                 window=max(120, 50 / p.crafts), fluids=[FluidFeed("fluid_in", p.fluid)],
                 research=rates.research_levels(params.get("research", "")))]
