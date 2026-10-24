"""Cell: a producer's machines hand their product straight to a consumer's, by inserter.

  [in2 |] in1 | ins | chain | out | product | out | chain | ins | in1 [| in2]   (sides=2)

Each side's chain runs north to south: producer (P) and consumer (C) machines
with a row between each two, where inserters hand the producer's product over
(copper cable into electronic circuits, gears into inserters), no belt between
them. Every machine takes its own ingredients from the side's input belt: the
producer's on one lane and the consumer's others on the other (an inserter
takes only what its machine's recipe uses), or one item on both lanes. When
that's more than two items, the producer's stay on in1 and the consumer's others
go on a second belt beyond it (in2), which consumers reach by long-handed
inserters, as an assembly row's second belt is (copper into cable, circuits and
plastic into advanced circuits).
Consumers drop their product on the far lane of the middle belt, as in an
assembly row, so the two sides fill both lanes.

The chain is the shortest of a few patterns (a producer at each end and
between every two consumers, one between every pair, ...) in which every
consumer gets what it needs from the producers beside it. Handovers are sized
from inserters measured machine to machine (tools/inserter_rates.json, no
research) with a 15% margin. `consumers` (per side) defaults to the most the
belts carry. Each input belt carries at most two items, and nothing is a fluid.
`belts=2` allows the second belt; the compact factory search doesn't use it, since
such cells came out larger there (docs/layout-optimizer.md).
"""

import math
from collections import Counter
from dataclasses import dataclass, field

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from tools import layout, rates

MARGIN = 0.85  # use at most this share of an inserter's measured rate
REGULAR = ("inserter", "fast-inserter")
LONG = "long-handed-inserter"
LANES = ("left", "right")
MOST = 12  # consumers per side, at most


@dataclass
class Plan:
    producer: rates.Recipe
    consumer: rates.Recipe
    machine: str
    belt: str
    sides: int
    chain: str  # per side, north to south: P a producer, C a consumer
    handover: list[tuple[str, int]]  # per row between neighbours: inserter, count (0: nothing handed)
    lanes: dict[str, str]  # input belt lane -> item
    feeders: dict[str, tuple[str, int]]  # "P" or "C": inserter, count from the input belt
    output: tuple[str, int]  # consumer to the product belt
    p_crafts: float  # per machine, per second
    c_crafts: float
    size: int
    item: str  # handed over
    product: str
    far: dict[str, str] = field(default_factory=dict)  # a second input belt's lanes: the consumer's others

    @property
    def per_side(self) -> float:
        return self.chain.count("C") * self.c_crafts * self.consumer.results[self.product]


def plan(recipe="electronic-circuit", producer="copper-cable", machine="assembling-machine-2",
         belt="transport-belt", sides=2, consumers=0, belts=1) -> Plan:
    if sides not in (1, 2):
        raise ValueError("sides is 1 or 2")
    if belts not in (1, 2):
        raise ValueError("belts is 1 or 2")
    pr, cr = rates.recipe(producer), rates.recipe(recipe)
    if len(pr.results) != 1 or len(cr.results) != 1:
        raise ValueError(f"{producer} into {recipe}: a cell's machines make one product each")
    (item, made), = pr.results.items()
    (product, out), = cr.results.items()
    if item not in cr.ingredients:
        raise ValueError(f"{recipe} doesn't use {item}")
    near, others = list(pr.ingredients), [i for i in cr.ingredients if i != item]
    on_belt = list(dict.fromkeys([*near, *others]))
    far = {}
    if len(on_belt) <= 2:
        lanes = _lanes(on_belt)
    elif belts == 1:
        raise ValueError(f"{producer} into {recipe} needs {len(on_belt)} items on one input belt "
                         f"({', '.join(on_belt)}) where 2 fit; belts=2 adds a second")
    else:
        # More than one belt carries: the producer's ingredients on in1, the consumer's others on in2.
        for who, got in ((producer, near), (recipe, others)):
            if len(got) > 2:
                raise ValueError(f"{producer} into {recipe}: {who} takes {len(got)} items ({', '.join(got)}) "
                                 f"where an input belt fits 2")
        if set(near) & set(others):
            raise ValueError(f"{producer} into {recipe}: {', '.join(sorted(set(near) & set(others)))} would "
                             f"be needed on both input belts")
        lanes, far = _lanes(near), _lanes(others)
    p_crafts, c_crafts = rates.crafts_per_second(machine, pr), rates.crafts_per_second(machine, cr)
    s = rates.size(machine)

    def from_belt(demand, who, options=REGULAR):
        if not demand:
            return REGULAR[0], 0
        got = rates.fewest_inserters(demand, belt, True, options, MARGIN)
        if got[1] > s:
            raise ValueError(f"a {who} machine needs {got[1]} inserters from the belt where {s} fit")
        return got

    feeders = {"P": from_belt(p_crafts * sum(pr.ingredients.values()), producer),
               "C": from_belt(c_crafts * sum(a for i, a in cr.ingredients.items() if i != item), recipe,
                              (LONG,) if far else REGULAR)}
    output = rates.fewest_inserters(c_crafts * out, belt, False, REGULAR, MARGIN)
    if output[1] > s:
        raise ValueError(f"a {recipe} machine needs {output[1]} inserters to the belt where {s} fit")
    give, need, lane = p_crafts * made, c_crafts * cr.ingredients[item], rates.lane_rate(belt)
    width = Counter(lanes.values()) + Counter(far.values())

    def carried(chain):
        # Producers make only what the consumers take, so the belt carries that.
        c = chain.count("C")
        use = Counter({i: c * need * a / made for i, a in pr.ingredients.items()})
        use.update({i: c * c_crafts * a for i, a in cr.ingredients.items() if i != item})
        return c * c_crafts * out <= lane + 1e-9 and all(u <= width[i] * lane + 1e-9 for i, u in use.items())

    def fit(c):
        for chain in _chains(c):
            flows = _handed(chain, give, need)
            if flows is None or not carried(chain):
                continue
            handover = [_handover(f) if f > 1e-9 else (REGULAR[0], 0) for f in flows]
            if all(k <= s for _, k in handover):
                return chain, handover
        return None

    if consumers:
        best = fit(consumers)
        if best is None:
            raise ValueError(f"{producer} into {recipe}: no chain of {consumers} {machine} consumers a side is fed "
                             f"and fits its belts and inserters")
    else:
        best = None
        for c in range(1, MOST + 1):
            got = fit(c)
            if got is None:
                break
            best = got
        if best is None:
            raise ValueError(f"{producer} into {recipe}: no chain is fed and fits its belts and inserters in {machine}")
    chain, handover = best
    return Plan(pr, cr, machine, belt, sides, chain, handover, lanes, feeders, output, p_crafts, c_crafts, s, item,
                product, far)


def _lanes(items: list[str]) -> dict[str, str]:
    """One item on both lanes of a belt, two on one each."""
    return dict(zip(LANES, items * 2 if len(items) == 1 else items))


def _chains(c: int) -> list[str]:
    """Patterns of c consumers, fewest machines first: groups with a producer at each end
    and between every two consumers (as even as they split); producers alternating; one
    producer between every pair of consumers."""
    options = ["PC" * c, "CP" * c, "CPC" * (c // 2) + ("CP" if c % 2 else "")]
    if c > 1:
        options.append("C" + "PC" * (c - 1))
    for groups in range(1, c + 1):
        sizes = [c // groups + (g < c % groups) for g in range(groups)]
        options.append("".join("P" + "CP" * j for j in sizes))
    return sorted(dict.fromkeys(options), key=lambda ch: (len(ch), ch.count("P")))


def _handed(chain: str, give: float, need: float) -> list[float] | None:
    """Items/s handed over each row between neighbours, or None if a consumer goes short.
    Along a line each consumer takes first what the producer before it has to spare,
    which nothing after can use, then the rest from the producer after it."""
    flows, spare, short = [0.0] * (len(chain) - 1), 0.0, 0.0
    for k, m in enumerate(chain):
        if m == "P":
            spare = give
            if k and chain[k - 1] == "C":
                f = min(short, spare)
                flows[k - 1], short, spare = f, short - f, spare - f
            if short > 1e-9:
                return None
            short = 0.0
        else:
            if k and chain[k - 1] == "C" and short > 1e-9:
                return None
            want = need
            if k and chain[k - 1] == "P":
                f = min(want, spare)
                flows[k - 1], want = f, want - f
            short, spare = want, 0.0
    return None if short > 1e-9 else flows


def _handover(flow: float) -> tuple[str, int]:
    """The fewest inserters, then the cheapest, handing `flow` items/s machine to machine."""
    best = min(((math.ceil(flow / (MARGIN * rates.handover_rate(i)) - 1e-9), k), i) for k, i in enumerate(REGULAR))
    return best[1], best[0][0]


def ingredients(recipe="electronic-circuit", producer="copper-cable", **_) -> dict[str, float]:
    """What a cell takes from its input belt per consumer craft: the consumer's other
    ingredients, and the producer's for the item handed over."""
    pr, cr = rates.recipe(producer), rates.recipe(recipe)
    (item, made), = pr.results.items()
    got = Counter({i: a for i, a in cr.ingredients.items() if i != item})
    got.update({i: a * cr.ingredients[item] / made for i, a in pr.ingredients.items()})
    return dict(got)


def build(recipe="electronic-circuit", producer="copper-cable", machine="assembling-machine-2",
          belt="transport-belt", sides=2, consumers=0, belts=1, pole="medium-electric-pole") -> Blueprint:
    p = plan(recipe, producer, machine, belt, sides, consumers, belts)
    s, n = p.size, len(p.chain)
    pitch = s + 1
    length = n * pitch - 1
    belts = [p.lanes, p.far] if p.far else [p.lanes]
    o = len(belts) - 1
    # Columns from the west: [in2,] in1, inserters, machine, out, product (then mirrored).
    product_x = o + 3 + s
    columns = {"left": {"belts": [o - b for b in range(len(belts))], "ins": o + 1, "machine": o + 2,
                        "out": o + 2 + s}}
    if p.sides == 2:
        columns["right"] = {"belts": [o + 6 + 2 * s + b for b in range(len(belts))], "ins": o + 5 + 2 * s,
                            "machine": o + 5 + s, "out": o + 4 + s}

    bp = Blueprint()
    consumers_all = p.sides * p.chain.count("C")
    bp.label = f"Cell: {consumers_all} {p.machine} ({p.producer.name} into {p.consumer.name})"
    belt_text = "; ".join(
        (f"in{b + 1}: " if len(belts) > 1 else "")
        + (f"{ls['left']} on both lanes" if ls["left"] == ls["right"] else f"{ls['left']} left lane, {ls['right']} right lane")
        for b, ls in enumerate(belts))
    bp.description = (
        f"{p.chain} a side, north to south: {p.item} handed straight from {p.producer.name} (P) to "
        f"{p.consumer.name} (C) machines by inserter. {p.sides * p.per_side:g} {p.product}/s. Ingredients enter "
        f"at the bottom of each side's input belt{'s' if len(belts) > 1 else ''}, {belt_text}. {p.product} leaves "
        f"at the top. Connect any pole to your grid.")
    bp.icons = [p.machine, p.product]

    for y in range(length):
        bp.entities.append(p.belt, tile_position=(product_x, y), direction=Direction.NORTH,
                           **({"id": "product"} if y == 0 else {}))
        for side, c in columns.items():
            for b, x in enumerate(c["belts"]):
                bp.entities.append(p.belt, tile_position=(x, y), direction=Direction.NORTH,
                                   **({"id": f"in{b + 1}_{side}"} if y == length - 1 else {}))

    powered, free = [], {}
    for side, c in columns.items():
        west = side == "left"
        for col in (c["ins"], c["out"], *range(c["machine"], c["machine"] + s)):
            free.setdefault(col, [])
        for k, m in enumerate(p.chain):
            y0 = k * pitch
            recipe_name = p.producer.name if m == "P" else p.consumer.name
            bp.entities.append(p.machine, tile_position=(c["machine"], y0), recipe=recipe_name)
            powered.append((c["machine"] + s / 2, y0 + s / 2, rates.half_width(p.machine)))
            inserter, count = p.feeders[m]
            for r in range(count):
                bp.entities.append(inserter, tile_position=(c["ins"], y0 + r), direction=layout.facing(west))
                powered.append((c["ins"] + 0.5, y0 + r + 0.5, rates.half_width(inserter)))
            free[c["ins"]] += [y0 + r for r in range(count, s)]
            out_count = p.output[1] if m == "C" else 0
            for r in range(out_count):
                bp.entities.append(p.output[0], tile_position=(c["out"], y0 + r), direction=layout.facing(west))
                powered.append((c["out"] + 0.5, y0 + r + 0.5, rates.half_width(p.output[0])))
            free[c["out"]] += [y0 + r for r in range(out_count, s)]
            if k == n - 1:
                continue
            gap = y0 + s  # the row to the next machine: hand over from P to C
            free[c["ins"]].append(gap)
            free[c["out"]].append(gap)
            inserter, count = p.handover[k]
            # An inserter's direction is the side it picks from: the producer's.
            facing = Direction.NORTH if m == "P" else Direction.SOUTH
            used = [c["machine"] + j for j in (1, 0, 2)[:count]]
            for x in used:
                bp.entities.append(inserter, tile_position=(x, gap), direction=facing)
                powered.append((x + 0.5, gap + 0.5, rates.half_width(inserter)))
            for x in range(c["machine"], c["machine"] + s):
                if x not in used:
                    free[x].append(gap)
    for x, y in layout.poles(pole, powered, free):
        bp.entities.append(pole, tile_position=(x, y))
    bp.generate_power_connections()
    return bp


def flow_tests(**params):
    from validator.flow import Feed, Flow

    p = plan(**{k: v for k, v in params.items() if k != "pole"})
    belts = [p.lanes, p.far] if p.far else [p.lanes]
    feeds = [Feed(f"in{b + 1}_{side}", lane, item) for side in LANES[:p.sides] for b, ls in enumerate(belts)
             for lane, item in ls.items()]
    total = p.sides * p.per_side
    machines = len(p.chain)
    return [Flow(f"{p.sides * p.chain.count('C')} {p.consumer.name} machines fed {p.item} by hand-over: "
                 f"{total:g} {p.product}/s", feeds, ["product"], {p.product: total},
                 warmup=rates.warmup_seconds(p.belt, machines * (p.size + 1), 1 / p.c_crafts + 1 / p.p_crafts),
                 window=max(120, 50 / p.c_crafts))]
