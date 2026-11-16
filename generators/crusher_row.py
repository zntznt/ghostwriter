"""Crusher row: crushers on both sides of one results belt, fed asteroid chunks.

  in | ins | crusher | out | results | out | crusher | ins | in   (sides=2)

All belts run north: chunks enter at the bottom of each side's input belt on
both lanes, the results leave mixed at the top. A crusher (2x3) stands north,
its three rows beside the inserter columns: up to three inserters feed it and
three take out its results onto the far lane of the results belt, so each side
fills one lane. A returned chunk is a result like any other: it leaves on the
results belt, at its mean, with the rest.

Results count at their means (rates.recipe_any's means), research productivity
(`productivity=0.3` for +30%, as the game's recipe bonus) and productivity
modules on top, capped at the recipe's maximum. Inserters are the fewest that
keep up, by the measured rates with a 15% margin, as assembly_row picks them; a
chunk stacks to one, so a researched hand still carries one a swing and chunks
move at the rates without research. Where more than three would be needed
(crushing gives 20 ore a craft), three of the fastest take out what they can
and the crusher crafts at that, its results waiting in it; a lane can bind the
same way. `machines` (per side) defaults to what 85% of a lane takes: inserters
spread along it don't fill it, though one crusher's three side by side do (checked
in game) but for a turbo lane, 89% (FILL). Poles take the inserter-column tiles
left over, else a row's gap after each crusher.

With beacons, a beacon column stands outside each input belt: a beacon beside
each crusher's three rows and one past each end, so every crusher takes the
three nearest (`beacons_per_machine`), 480 kW each. Inserters and lanes still
cap what it crafts, so a crusher its output already binds (crushing on yellow)
gains nothing; reprocessing and researched turbo rows do.

Crushers need zero gravity: a space platform (the validator builds on one).
"""

import math
from dataclasses import dataclass

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities, items, recipes

from generators.assembly_row import BEACON, MARGIN, REGULAR
from generators.recycler_row import SIGMAS, _spread
from tools import layout, rates

MACHINE = "crusher"
DEPTH, ROWS = 2, 3  # a crusher standing north: tiles across, rows along
# Share of a lane three inserters side by side fill flat out, measured: a yellow lane 7.49 of 7.5, an
# express one 98%, a turbo one 26.8 of 30 (three bulk inserters at capacity 7, beaconed crushing).
# ponytail: turbo measured with bulk hands of 12 only; measure other hands if a row binds there.
FILL = {"turbo-transport-belt": 0.89}
FASTEST = 0.98  # inserters moving at least this share of the fastest count as fast, the cheapest of them taken


@dataclass
class Plan:
    recipe: rates.Recipe  # as a crusher makes it: results at their means, with productivity
    belt: str
    sides: int
    machines: int  # per side
    limit: int  # crushers per side the belts carry
    crafts: float  # per crusher, per second: its rate, or what its output inserters (or lane) take
    rated: float  # per crusher, per second
    feeder: tuple[str, int]  # inserter, count per crusher
    output: tuple[str, int]
    gap: int = 0  # empty rows after each crusher, for poles when the inserter columns are full
    lead: int = 0  # empty rows before the first, when its top inserters are out of a pole's reach
    modules: str = ""
    beacons: str = ""  # in every beacon's slots; no beacons without
    beacons_per_machine: int = 0
    research: str = ""  # the inserter research the row is sized for
    productivity: float = 0  # the force's research bonus for the recipe

    @property
    def ingredient(self) -> str:
        (item,) = self.recipe.ingredients
        return item

    @property
    def product(self) -> str:
        """The commonest result (the first, on a tie), as the observer picks a group's."""
        return max(self.recipe.results.items(), key=lambda kv: kv[1])[0]

    @property
    def output_bound(self) -> bool:
        """Each crusher crafts below its rate: its results wait in it."""
        return self.crafts < self.rated - 1e-9

    @property
    def side_crafts(self) -> float:
        """Crafts/s of one side; past `limit` the belts set it."""
        lane = rates.lane_rate(self.belt)
        return min(self.machines * self.crafts, 2 * lane / self.recipe.ingredients[self.ingredient],
                   lane / sum(self.recipe.results.values()))

    @property
    def per_side(self) -> float:
        """The product's rate from one side."""
        return self.side_crafts * self.recipe.results[self.product]

    def rates(self) -> dict[str, float]:
        """Each result's mean rate off the whole row."""
        return {k: self.sides * self.side_crafts * v for k, v in self.recipe.results.items()}

    def watts(self, share: float = 1) -> float:
        """Average electric W making `share` of the rated output."""
        m = self.machines * self.sides
        crafts = share * self.sides * self.side_crafts
        w = m * rates.machine_watts(MACHINE, self.recipe, self.modules, self.beacons, self.beacons_per_machine,
                                    crafts / (m * self.rated))
        w += self.sides * (self.machines + 2) * rates.beacon_watts() if self.beacons else 0
        for (inserter, k), moved, from_belt in ((self.feeder, self.recipe.ingredients, True),
                                                (self.output, self.recipe.results, False)):
            w += m * k * rates.inserter_drain(inserter) + sum(
                rates.energy_per_item(inserter, self.belt, _hand(item, self.research), from_belt) * crafts * v
                for item, v in moved.items())
        return w


def _hand(item: str, research: str) -> str:
    """The research an inserter's hand moves `item` at: none for what stacks to one."""
    # ponytail: a stack between 1 and a researched hand still counts as the hand; no such result here.
    return "" if items.raw[item]["stack_size"] == 1 else research


def _plan(recipe="metallic-asteroid-crushing", belt="transport-belt", sides=2, machines=0, research="", modules="",
          beacons="", productivity=0.0) -> Plan:
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    if recipe not in recipes.raw:
        raise ValueError(f"unknown recipe {recipe!r}")
    base = rates.recipe_any(recipe, means=True)
    if base.fluids or len(base.ingredients) != 1 or set(base.results) - set(items.raw):
        raise ValueError(f"{recipe}: a crusher row takes one item in and gives items back")
    per = 3 if beacons else 0  # build() checks every crusher takes them
    rated = rates.crafts_per_second(MACHINE, base, modules, beacons, per)
    r = rates.made(MACHINE, base, modules, float(productivity))
    research = rates.research_key(research)

    def seconds(inserter, moved, from_belt):  # one inserter's time moving a craft's `moved`
        return sum(v / rates.inserter_rate(inserter, belt, from_belt, _hand(i, research)) for i, v in moved.items())

    def fewest(moved, from_belt, crafts):
        return min(((math.ceil(crafts * seconds(i, moved, from_belt) / MARGIN - 1e-9), k), i)
                   for k, i in enumerate(REGULAR))[::-1]

    crafts = min(rated, FILL.get(belt, 1) * rates.lane_rate(belt) / sum(r.results.values()))
    out, (k, _) = fewest(r.results, False, crafts)
    if k > ROWS:  # flat out: three of the fastest
        quickest = min(seconds(i, r.results, False) for i in REGULAR)
        out = next(i for i in REGULAR if quickest / seconds(i, r.results, False) >= FASTEST)
        k, crafts = ROWS, min(crafts, ROWS / seconds(out, r.results, False))
    feeder, (fed, _) = fewest(r.ingredients, True, crafts)
    if fed > ROWS:
        raise ValueError(f"{recipe}: one crusher takes more than {ROWS} inserters can feed it")
    (amount,), lane, total = r.ingredients.values(), rates.lane_rate(belt), sum(r.results.values())
    # Inserters spread along a lane fill only so much of it (7 a side, a fast one each, put 6.8 of a 7.5/s
    # lane's 7.14 on it), so a side's crushers keep MARGIN of it; three side by side fill one alone (7.49).
    limit = max(1, math.floor(min(2 * lane / amount, MARGIN * lane / total) / crafts + 1e-9))
    return Plan(r, belt, sides, machines or limit, limit, crafts, rated, (feeder, fed), (out, k), modules=modules,
                beacons=beacons, beacons_per_machine=per, research=research, productivity=float(productivity))


def plan(pole="medium-electric-pole", **params) -> Plan:
    """The plan, with the fewest gap rows that leave room for poles."""
    p, errors = _plan(**params), []
    for p.gap, p.lead in ((0, 0), (1, 0), (1, 1)):
        try:
            _build(p, pole)
            return p
        except ValueError as e:
            errors.append(str(e))
    raise ValueError(f"{p.recipe.name}: no layout leaves room to power it ({'; '.join(errors)})")


def build(recipe="metallic-asteroid-crushing", belt="transport-belt", sides=2, machines=0,
          pole="medium-electric-pole", research="", modules="", beacons="", productivity=0.0) -> Blueprint:
    """research: inserter research the row is sized for (e.g. inserter-capacity-bonus=7). modules: a
    module in every crusher's slots. beacons: a module in every slot of beacons outside the input
    belts, the row sized for their speed. productivity: the force's research bonus for the recipe (asteroid
    productivity: 0.1 a level), the results sized for it."""
    return _build(plan(pole, recipe=recipe, belt=belt, sides=sides, machines=machines, research=research,
                       modules=modules, beacons=beacons, productivity=productivity), pole)


def _length(p: Plan) -> int:
    return p.lead + p.machines * (ROWS + p.gap)


def _build(p: Plan, pole: str) -> Blueprint:
    n, length, pitch = p.machines, _length(p), ROWS + p.gap
    # Columns from the west: in, inserters, crusher, out, results (then mirrored).
    product_x, bs = 3 + DEPTH, rates.size(BEACON) if p.beacons else 0
    columns = {"left": {"belt": 0, "ins": 1, "machine": 2, "out": 2 + DEPTH, "beacon": -bs}}
    if p.sides == 2:
        columns["right"] = {"belt": product_x + DEPTH + 3, "ins": product_x + DEPTH + 2, "machine": product_x + 2,
                            "out": product_x + 1, "beacon": product_x + DEPTH + 4}

    bp = Blueprint()
    made = p.rates()
    bp.label = f"Crusher row: {n * p.sides} crushers ({p.recipe.name})"
    bp.description = (
        f"{n} crushers per side x{p.sides}, {p.sides * p.side_crafts:.3g} {p.ingredient}/s in (the belts carry "
        f"{p.limit} per side). {p.ingredient} enters at the bottom of each side's input belt, both lanes. Results, "
        "returned chunks included, leave mixed at the top, at their means: "
        + ", ".join(f"{k} {v:.3g}/s" for k, v in made.items())
        + (f"; each crusher crafts {p.crafts:.3g} of its {p.rated:.3g}/s, all its output takes" if p.output_bound
           else "")
        + (f". {p.beacons_per_machine} beacons of {p.beacons} reach each crusher" if p.beacons else "")
        + f". Draws about {rates.megawatts(p.watts())}. Needs zero gravity (a space platform). "
        "Connect any pole to your grid.")
    bp.icons = [MACHINE, p.product]

    # One tile past the top crusher's drops, so a drained tile never makes room a real belt wouldn't.
    bp.entities.append(p.belt, tile_position=(product_x, -1), direction=Direction.NORTH, id="product")
    for y in range(length):
        bp.entities.append(p.belt, tile_position=(product_x, y), direction=Direction.NORTH)
        for side, c in columns.items():
            bp.entities.append(p.belt, tile_position=(c["belt"], y), direction=Direction.NORTH,
                               **({"id": f"in_{side}"} if y == length - 1 else {}))

    powered, crushers, beacons = [], [], []  # powered: (centre x, centre y, half width)
    for side, c in columns.items():
        west = side == "left"
        for y in [p.lead - bs] + [p.lead + m * pitch for m in range(n)] + [p.lead + (n - 1) * pitch + bs] if bs else ():
            bp.entities.append(BEACON, tile_position=(c["beacon"], y))
            bp.entities[-1].request_modules(p.beacons, range(entities.raw[BEACON]["module_slots"]))
            beacons.append((c["beacon"] + bs / 2, y + bs / 2))
            powered.append((*beacons[-1], rates.half_width(BEACON)))
        for m in range(n):
            y0 = p.lead + m * pitch
            bp.entities.append(MACHINE, tile_position=(c["machine"], y0), direction=Direction.NORTH,
                               recipe=p.recipe.name)
            rates.request_modules(bp.entities[-1], MACHINE, p.recipe, p.modules)
            # ponytail: the narrow half (0.7) for both axes; a pole's reach along the row is understated.
            crushers.append((c["machine"] + DEPTH / 2, y0 + ROWS / 2))
            powered.append((*crushers[-1], rates.half_width(MACHINE)))
            for x, (inserter, k) in ((c["ins"], p.feeder), (c["out"], p.output)):
                for row in range(k):
                    bp.entities.append(inserter, tile_position=(x, y0 + row), direction=layout.facing(west))
                    powered.append((x + 0.5, y0 + row + 0.5, rates.half_width(inserter)))

    if bs:
        (x1, y1), (x2, y2) = entities.raw[MACHINE]["collision_box"]
        if any(layout.beacons_reaching(c, ((x2 - x1) / 2, (y2 - y1) / 2), beacons) != p.beacons_per_machine
               for c in crushers):
            raise ValueError(f"not every crusher takes {p.beacons_per_machine} beacons")
    free = {}
    for c in columns.values():
        for x, (_, used) in ((c["ins"], p.feeder), (c["out"], p.output)):
            taken = {p.lead + m * pitch + r for m in range(n) for r in range(used)}
            free[x] = [y for y in range(length) if y not in taken]
    for x, y in layout.poles(pole, powered, free, layout.open_tiles(bp)):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def flow_tests(**params):
    """Each result at its mean rate, within SIGMAS standard deviations of its count over the window
    (never under 3%), as recycler_row's: the window runs until every result is within 5%, up to 10
    game minutes; a result whose band would pass half its rate is left out. Research productivity
    is set on the force for the run."""
    from validator.flow import Feed, Flow

    p = plan(**params)
    crafts = p.sides * p.side_crafts
    bonus = 1 + rates.productivity(MACHINE, rates.recipe_any(p.recipe.name, means=True), p.modules, p.productivity)
    # ponytail: a bonus craft's results vary as a craft's, so the spread grows with the bonus.
    spread = {k: v * bonus for k, v in _spread(p.recipe.name).items()}
    need = max((SIGMAS / 0.05) ** 2 * spread[k] / v ** 2 for k, v in p.recipe.results.items())
    window = min(600, max(120, 50 / p.crafts, need / crafts))
    made = p.rates()
    tolerances = {k: max(0.03, SIGMAS * math.sqrt(spread[k] / (crafts * window)) / v)
                  for k, v in p.recipe.results.items()}
    expect = {k: v for k, v in made.items() if tolerances[k] <= 0.5}
    lanes = ("left", "right")
    feeds = [Feed(f"in_{side}", lane, p.ingredient) for side in lanes[:p.sides] for lane in lanes]
    return [Flow(f"{p.machines * p.sides} crushers: {crafts:.3g} {p.ingredient}/s", feeds, ["product"], expect,
                 warmup=rates.warmup_seconds(p.belt, _length(p), 1 / p.crafts), window=window,
                 tolerances={k: tolerances[k] for k in expect},
                 research=rates.research_levels(params.get("research", "")),
                 productivity={p.recipe.name: p.productivity} if p.productivity else {})]
