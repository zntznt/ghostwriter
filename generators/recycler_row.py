"""Recycler row: recyclers on both sides of one results belt, fed one item.

  in | ins | recycler | results | recycler | ins | in   (sides=2)

All belts run north: the item enters at the bottom of each side's input
belt on both lanes, the results leave mixed at the top. A recycler is 2x4;
turned to face the results belt it takes two rows and drops what it makes
straight onto the belt, as a mining drill does, with no output inserter: on
the near lane, so each side fills one lane (checked in game).

Inserters feed from the input belt, up to two a recycler (its two rows): the
fewest that keep it crafting, by the measured rates with a 15% margin, as
assembly_row picks them. A fast recipe (item recycling takes 1/32 s) needs
more than two can move; the recycler then crafts what two of the fastest
move flat out, as measured into a recycler off a standing belt (the input
belt stops past the last recycler; rates.recycler_feed_rate). Poles take the inserter-column tiles
left over, else a row's gap after each recycler, which also leaves the
recycler columns free there.

With beacons, each recycler column runs beacon, recycler, beacon, ... beacon,
each beacon at the outer side of the column; every recycler takes the two
beside it, and with two sides the two across the belt too.

`machines` (per side) defaults to the most whichever binds first allows: the
input belt (both lanes), or the results lane for the sum of the mean results
(rates.recipe_any's means). The description names each result's mean rate.
Quality modules go in like any other (`modules=`); their quality outcomes
aren't modelled.
"""

import math
from dataclasses import dataclass

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities, items, recipes

from generators.assembly_row import BEACON, MARGIN, REGULAR
from tools import layout, rates

MACHINE = "recycler"
DEPTH, ROWS = 4, 2  # a recycler facing the results belt: tiles across, rows along
SIGMAS = 4  # a flow test holds each result to this many standard deviations of its count


@dataclass
class Plan:
    recipe: rates.Recipe  # results at their means
    belt: str
    sides: int
    machines: int  # per side
    limit: int  # recyclers per side the belts carry
    crafts: float  # per recycler, per second: its rate, or what its inserters feed
    rated: float  # per recycler, per second, fed all it takes
    feeder: tuple[str, int]  # inserter, count per recycler
    gap: int = 0  # empty rows after each recycler, for poles when the inserter column is full
    modules: str = ""
    beacons: str = ""
    beacons_per_machine: int = 0
    research: str = ""

    @property
    def ingredient(self) -> str:
        (item,) = self.recipe.ingredients
        return item

    @property
    def product(self) -> str:
        """The commonest result (the first, on a tie), as the observer picks a group's."""
        return max(self.recipe.results.items(), key=lambda kv: kv[1])[0]

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
        w += self.sides * (self.machines + 1) * rates.beacon_watts() if self.beacons else 0
        inserter, k = self.feeder
        return w + rates.inserter_watts(inserter, m * k, crafts * self.recipe.ingredients[self.ingredient],
                                        self.research)


def _plan(recipe="scrap-recycling", belt="transport-belt", sides=2, machines=0, research="", modules="",
          beacons="") -> Plan:
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    if recipe not in recipes.raw:
        raise ValueError(f"unknown recipe {recipe!r}")
    r = rates.recipe_any(recipe, means=True)
    if r.fluids or len(r.ingredients) != 1 or set(r.results) - set(items.raw):
        raise ValueError(f"{recipe}: a recycler row takes one item in and gives items back")
    per = 2 * sides if beacons else 0  # build() checks every recycler takes them
    rated = rates.crafts_per_second(MACHINE, r, modules, beacons, per)
    (item, amount), = r.ingredients.items()
    lane = rates.lane_rate(belt)
    feeder = rates.fewest_inserters(rated * amount, belt, True, REGULAR, MARGIN, research)
    crafts = rated
    if feeder[1] > ROWS:  # flat out: two of the fastest (the cheaper on a tie)
        best = max(REGULAR, key=lambda i: (rates.recycler_feed_rate(i, belt, research), -REGULAR.index(i)))
        feeder = (best, ROWS)
        crafts = min(rated, ROWS * rates.recycler_feed_rate(best, belt, research) / amount)
    out = sum(r.results.values())
    limit = math.floor(min(2 * lane / (amount * crafts), lane / (out * crafts)) + 1e-9)
    if limit < 1:
        faster = rates.slowest_belt(out * crafts)
        raise ValueError(f"{recipe}: one recycler's results ({out * crafts:.4g}/s) need more than a {belt} lane "
                         f"carries ({lane:g}/s)" + (f"; try belt={faster}" if faster else ""))
    return Plan(r, belt, sides, machines or limit, limit, crafts, rated, feeder, modules=modules, beacons=beacons,
                beacons_per_machine=per, research=rates.research_key(research))


def plan(pole="medium-electric-pole", **params) -> Plan:
    """The plan, with the fewest gap rows that leave room for poles."""
    p, errors = _plan(**params), []
    for gap in (0, 1):
        p.gap = gap
        try:
            _build(p, pole)
            return p
        except ValueError as e:
            errors.append(str(e))
    raise ValueError(f"{p.recipe.name}: no layout leaves room to power it ({'; '.join(errors)})")


def build(recipe="scrap-recycling", belt="transport-belt", sides=2, machines=0, pole="medium-electric-pole",
          research="", modules="", beacons="") -> Blueprint:
    """research: inserter research the row is sized for (e.g. inserter-capacity-bonus=7). modules: a
    module in every recycler's slots (quality modules too). beacons: a module in every slot of beacons
    between the recyclers, the row sized for their speed."""
    return _build(plan(pole, recipe=recipe, belt=belt, sides=sides, machines=machines, research=research,
                       modules=modules, beacons=beacons), pole)


def _length(p: Plan) -> int:
    bs = rates.size(BEACON) if p.beacons else 0
    return p.machines * (ROWS + p.gap + bs) + bs


def _build(p: Plan, pole: str) -> Blueprint:
    n, length = p.machines, _length(p)
    bs = rates.size(BEACON) if p.beacons else 0
    pitch = ROWS + p.gap + bs
    # Columns from the west: in, inserters, recycler, results (then mirrored).
    product_x = 2 + DEPTH
    columns = {"left": {"belt": 0, "ins": 1, "machine": 2, "beacon": 2, "facing": Direction.EAST}}
    if p.sides == 2:
        columns["right"] = {"belt": product_x + DEPTH + 2, "ins": product_x + DEPTH + 1, "machine": product_x + 1,
                            "beacon": product_x + 1 + DEPTH - bs, "facing": Direction.WEST}

    bp = Blueprint()
    made = p.rates()
    bp.label = f"Recycler row: {n * p.sides} recyclers ({p.recipe.name})"
    bp.description = (
        f"{n} recyclers per side x{p.sides}, {p.sides * p.side_crafts:.3g} {p.ingredient}/s in (the belts carry "
        f"{p.limit} per side). {p.ingredient} enters at the bottom of each side's input belt, both lanes. Results "
        "leave mixed at the top, at their means: " + ", ".join(f"{k} {v:.3g}/s" for k, v in made.items())
        + f". Draws about {rates.megawatts(p.watts())}. Connect any pole to your grid.")
    bp.icons = [MACHINE, p.product]

    taken = set()
    for y in range(length):
        bp.entities.append(p.belt, tile_position=(product_x, y), direction=Direction.NORTH,
                           **({"id": "product"} if y == 0 else {}))
        for side, c in columns.items():
            bp.entities.append(p.belt, tile_position=(c["belt"], y), direction=Direction.NORTH,
                               **({"id": f"in_{side}"} if y == length - 1 else {}))

    powered, machines, beacons = [], [], []  # powered: (centre x, centre y, half width)
    inserter, k = p.feeder
    for side, c in columns.items():
        for b in range(n + 1 if bs else 0):
            x, y = c["beacon"], b * pitch
            bp.entities.append(BEACON, tile_position=(x, y))
            bp.entities[-1].request_modules(p.beacons, range(entities.raw[BEACON]["module_slots"]))
            beacons.append((x + bs / 2, y + bs / 2))
            taken |= {(x + i, y + j) for i in range(bs) for j in range(bs)}
        for m in range(n):
            y0 = m * pitch + bs
            bp.entities.append(MACHINE, tile_position=(c["machine"], y0), direction=c["facing"])
            rates.request_modules(bp.entities[-1], MACHINE, p.recipe, p.modules)
            machines.append((c["machine"] + DEPTH / 2, y0 + ROWS / 2))
            # ponytail: the narrow half (0.7) for both axes; a pole's reach along the row is understated.
            powered.append((*machines[-1], rates.half_width(MACHINE)))
            taken |= {(c["machine"] + i, y0 + j) for i in range(DEPTH) for j in range(ROWS)}
            for row in range(k):
                bp.entities.append(inserter, tile_position=(c["ins"], y0 + row),
                                   direction=layout.facing(side == "left"))
                powered.append((c["ins"] + 0.5, y0 + row + 0.5, rates.half_width(inserter)))
                taken.add((c["ins"], y0 + row))
    powered += [(x, y, rates.half_width(BEACON)) for x, y in beacons]

    if bs:
        (x1, y1), (x2, y2) = entities.raw[MACHINE]["collision_box"]  # facing north: turned, x and y swap
        if any(layout.beacons_reaching(m, ((y2 - y1) / 2, (x2 - x1) / 2), beacons) != p.beacons_per_machine
               for m in machines):
            raise ValueError(f"not every recycler takes {p.beacons_per_machine} beacons")
    belts = {product_x} | {c["belt"] for c in columns.values()}
    width = max(c["belt"] for c in columns.values()) + 1 if p.sides == 2 else product_x + 1
    free = {x: [y for y in range(length) if (x, y) not in taken] for x in range(width) if x not in belts}
    for x, y in layout.poles(pole, powered, free):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def flow_tests(**params):
    """Each result at its mean rate, within SIGMAS standard deviations of its count over the window
    (never under the 3% a deterministic row gets): a 1-in-100 result is held loosely, the commonest
    closely. The window runs until the commonest is within 5%, up to 10 game minutes; results whose
    band would pass half their rate are too rare to say anything about and left out."""
    from validator.flow import Feed, Flow

    p = plan(**params)
    crafts = p.sides * p.side_crafts
    spread = _spread(p.recipe.name)
    main = p.product
    need = (SIGMAS / 0.05) ** 2 * spread[main] / p.recipe.results[main] ** 2  # crafts for 5% on the commonest
    window = min(600, max(120, 50 / p.crafts, need / crafts))
    tolerances = {k: max(0.03, SIGMAS * math.sqrt(spread[k] / (crafts * window)) / v)
                  for k, v in p.recipe.results.items()}
    expect = {k: v for k, v in p.rates().items() if tolerances[k] <= 0.5}
    lanes = ("left", "right")
    feeds = [Feed(f"in_{side}", lane, p.ingredient) for side in lanes[:p.sides] for lane in lanes]
    return [Flow(f"{p.machines * p.sides} recyclers: {crafts:.3g} {p.ingredient}/s", feeds, ["product"], expect,
                 warmup=rates.warmup_seconds(p.belt, _length(p), 1 / p.crafts), window=window,
                 tolerances={k: tolerances[k] for k in expect},
                 research=rates.research_levels(params.get("research", "")))]


def _spread(recipe: str) -> dict[str, float]:
    """Variance of each result's count a craft: its amount if it comes (at its probability, shared or
    independent), plus one more at extra_count_fraction, independent of it."""
    out = {}
    for p in recipes.raw[recipe]["results"]:
        shared = p.get("shared_probability", {})
        chance = p.get("independent_probability", p.get("probability", shared.get("max", 1) - shared.get("min", 0)))
        f = p.get("extra_count_fraction", 0)
        out[p["name"]] = out.get(p["name"], 0) + p.get("amount", 0) ** 2 * chance * (1 - chance) + f * (1 - f)
    return out
