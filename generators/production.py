"""Production: a book of tested rows that makes ITEM at RATE per second.

    python -m tools.bp gen production item=electronic-circuit rate=5 --validate

Walks the recipe tree from the target. Each item an assembler makes from
items gets as many assembly rows (generators/assembly_row.py) as its demand
needs, in the fastest allowed machine whose row can be fed. Items made with
one fluid (plastic, batteries, processing units) get fluid rows
(generators/fluid_row.py). With smelt=1, items a furnace makes from one ore
get smelting columns. Everything else is an input to supply: plates by
default, fluids (units/s), items made with two fluids or as a fluid (sulfur,
sulfuric acid), and whatever `raw` lists (comma-separated). The book's
description gives those input rates.

Rows aren't connected to each other. Each row's description says which item
goes on which input lane; the book says what each row makes and how much of
it is needed. `--validate` runs every row's own flow test in game.
"""

import functools
import math
from dataclasses import dataclass

from draftsman.blueprintable import BlueprintBook
from draftsman.data import recipes

from generators import assembly_row, cell, fluid_block, fluid_row, smelting_column
from tools import rates
from tools.check import TEXT_LIMITS

MACHINES = ("assembling-machine-3", "assembling-machine-2", "assembling-machine-1")
GENERATORS = {"assembly_row": assembly_row, "fluid_row": fluid_row, "smelting_column": smelting_column,
              "fluid_block": fluid_block, "cell": cell}
OIL = ("heavy-oil", "light-oil", "petroleum-gas")
BUFFER_SECONDS, BUFFER_MIN = 20, 5  # chain tests: output pooled before a product backs up
# Rows are sized for this much more than the demand: a chain sized exactly runs
# short, since every idle inserter swing falls through to the end (a robot frame
# chain made 91% of its target with cable and circuits sized to the last item).
SLACK = 1.05
CRACKING = ("heavy-oil-cracking", "light-oil-cracking")
# The recipes each oil= value plans with: oil=1 picks the fewest tiles among them,
# oil=advanced is the crude-efficient chain (and the one with cracking to test).
RECIPES = {1: ("advanced-oil-processing", "basic-oil-processing", *CRACKING),
           "advanced": ("advanced-oil-processing", *CRACKING)}


@dataclass
class Step:
    item: str
    demand: float  # items (or fluid units) per second needed
    generator: str
    params: dict  # one row's parameters
    rows: int
    rate: float  # rated per row


def solve(item: str, rate: float, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0, research="") -> tuple[list[Step], dict[str, float]]:
    """Steps in build order (ingredients first), and the inputs to supply (item -> items/s,
    fluid -> units/s). A negative input is a surplus the factory makes but doesn't use.
    research: inserter research the rows are sized for (rates measured at it); it rides in
    the params of every row whose inserters it sizes, and isn't there without it."""
    if machine not in MACHINES:
        raise ValueError(f"machine is one of {MACHINES}")
    if oil not in (0, *RECIPES):
        raise ValueError(f"oil is one of 0, {', '.join(map(str, RECIPES))}, not {oil!r}")
    tiers = MACHINES[MACHINES.index(machine):]
    forced_raw = {i for i in str(raw).split(",") if i}
    how, order = {}, []

    def visit(i, path):
        if i in how:
            return
        if i in path:
            raise ValueError(f"recipe loop: {' -> '.join(path + [i])}")
        how[i] = _how(i, tiers, smelt, furnace, forced_raw, oil)
        if how[i] and how[i][1]:
            for ingredient in [*how[i][1].ingredients, *how[i][1].fluids]:
                visit(ingredient, path + [i])
        order.append(i)  # after its ingredients

    visit(item, [])
    if not how[item]:
        raise ValueError(_supplied_only(item, furnace, forced_raw))
    demand = {i: 0.0 for i in order}
    demand[item] = float(rate)
    for i in reversed(order):  # consumers before their ingredients
        if how[i] and how[i][1] and demand[i]:
            r = how[i][1]
            crafts = demand[i] / r.results[i]
            for ingredient, amount in [*r.ingredients.items(), *r.fluids.items()]:
                demand[ingredient] += crafts * amount

    extra = {"research": rates.research_key(research)} if rates.research_key(research) else {}
    steps = [_size(i, demand[i], how[i], belt, furnace, tiers, extra) for i in order if how[i] and how[i][1]]
    inputs = {i: demand[i] for i in order if not how[i]}
    if any((how.get(f) or (None,))[0] == "oil" and demand[f] for f in OIL):
        oil_steps, oil_inputs = _oil({f: demand.get(f, 0) for f in OIL}, oil)
        steps = oil_steps + steps
        for k, v in oil_inputs.items():
            inputs[k] = inputs.get(k, 0) + v
    return steps, inputs


def _oil(need: dict[str, float], oil=1) -> tuple[list[Step], dict[str, float]]:
    """Oil processing and cracking for heavy oil, light oil and petroleum gas per second,
    from RECIPES[oil]: the fewest measured tiles (a mixed-integer program over crafts,
    machines and which blocks exist), ties to less crude oil. Heavy and light oil are made
    exactly; petroleum gas left over is reported as a negative input."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    names = RECIPES[oil]
    n = len(names)
    rs = [rates.recipe_any(r) for r in names]
    per_machine = np.array([fluid_block.plan(recipe=r).crafts for r in names])
    per, fixed = zip(*map(_tiles, names))
    feed = [r.fluids.get("crude-oil", 0) for r in rs]
    net = np.array([[r.results.get(f, 0) - r.fluids.get(f, 0) for r in rs] for f in OIL])
    eye, zero, big = np.eye(n), np.zeros((n, n)), 10_000  # big: more machines than any block gets
    # x = crafts/s, machines, block exists; rows: net oil, machines cover crafts, no machines without a block.
    a = np.block([[net, np.zeros((len(OIL), 2 * n))],
                  [-np.diag(SLACK / per_machine), eye, zero],
                  [zero, eye, -big * eye]])
    lo = [need[f] for f in OIL] + [0] * n + [-np.inf] * n
    hi = [need["heavy-oil"], need["light-oil"], np.inf] + [np.inf] * n + [0] * n
    cost = [*(1e-6 * np.array(feed)), *per, *fixed]  # 1e-6 a unit of feedstock: never worth a tile
    res = milp(cost, constraints=LinearConstraint(a, lo, hi), integrality=[0] * n + [1] * 2 * n,
               bounds=Bounds(0, [np.inf] * 2 * n + [1] * n))
    if res.status != 0:
        raise ValueError(f"no oil plan for {need} with oil={oil}: {res.message}")
    crafts, machines = res.x[:n], np.round(res.x[n:2 * n]).astype(int)
    steps, inputs = [], {}
    for name, r, c, m, p in zip(names, rs, crafts, machines, per_machine):
        if m == 0:
            continue
        steps.append(Step(name, float(c), "fluid_block", {"recipe": name, "machines": int(m)}, 1, float(m * p)))
        for k, amount in [*r.fluids.items(), *r.ingredients.items()]:
            if k not in OIL:
                inputs[k] = inputs.get(k, 0) + float(c) * amount
    surplus = float(net[OIL.index("petroleum-gas")] @ crafts) - need["petroleum-gas"]
    if surplus > 1e-6:
        inputs["petroleum-gas"] = -surplus
    return steps, inputs


@functools.cache
def _tiles(recipe: str) -> tuple[float, float]:
    """(tiles per machine, fixed tiles) of a fluid_block of RECIPE, measured as bounding-box
    area at 1 and 3 machines. Over a pair because coal liquefaction alternates (a coal belt
    gap every second machine)."""
    from tools.density import density

    one, three = (density(fluid_block.build(recipe=recipe, machines=n).to_dict())[0]["area"] for n in (1, 3))
    per = (three - one) / 2
    return per, one - per


def _supplied_only(item, furnace, forced_raw) -> str:
    """Why the item asked for would only be supplied, and what would make it instead."""
    if item in forced_raw:
        return f"{item} is listed in raw=, so there's nothing to make: drop it from raw="
    if item in OIL:
        return f"{item} comes from oil processing: pass oil=1 to make it"
    try:
        r = rates.recipe(item)
    except ValueError:
        r = None
    if r and len(r.ingredients) == 1 and _crafts(furnace, r):
        return f"{item} is smelted in a furnace: pass smelt=1 to make it"
    return f"{item} has no recipe a factory here can make: it's an input to supply"


def _how(item, tiers, smelt, furnace, forced_raw, oil=0):
    """(generator, recipe), ("oil", None) for oil products when oil=1, or None
    for an input to supply."""
    if item in forced_raw:
        return None
    if item in OIL:
        return ("oil", None) if oil else None
    if item not in recipes.raw:
        return None
    try:
        r = rates.recipe(item, fluids=True)
    except ValueError:  # a fluid product, or variable results
        return _block(item)
    if list(r.results) != [item]:
        return None
    if r.fluids:
        if len(r.fluids) == 1 and any(_crafts(m, r) for m in _fluid_machines(tiers)):
            return "fluid_row", r
        return _block(item)
    if any(_crafts(m, r) for m in tiers):
        return "assembly_row", r
    if smelt and len(r.ingredients) == 1 and _crafts(furnace, r):
        return "smelting_column", r
    return None


def _size(item, demand, how, belt, furnace, tiers, extra=None) -> Step:
    """Rows for one item, in the fastest tier whose row can be fed: the fewest
    full-size rows, then just enough machines in each. extra: params every row but
    a smelting column takes (research)."""
    kind, r = how
    extra = extra or {}
    need = demand * SLACK
    if kind == "fluid_block":  # one block, as many machines as the demand needs
        p = fluid_block.plan(recipe=item, **extra)
        machines = math.ceil(need / (p.crafts * r.results[item]) - 1e-9)
        p = fluid_block.plan(recipe=item, machines=machines, belt=belt, **extra)
        return Step(item, demand, kind, {"recipe": item, "machines": machines, "belt": belt, **extra}, 1,
                    p.rate(item))
    errors = []
    machines = {"assembly_row": tiers, "fluid_row": _fluid_machines(tiers)}.get(kind, ())
    options = ([{"recipe": item, "machine": m, "belt": belt, **extra} for m in machines if _crafts(m, r)]
               if kind != "smelting_column" else [{"recipe": item, "furnace": furnace, "belt": belt}])
    count_key = "furnaces" if kind == "smelting_column" else "machines"
    for base in options:
        try:
            full = _rated(kind, {**base, "sides": 2})
            rows = math.ceil(need / full - 1e-9)
            share = need / rows
            machine = base.get("machine") or base["furnace"]
            per_machine = rates.crafts_per_second(machine, r) * r.results[item]
            if share <= per_machine + 1e-9:
                params = {**base, "sides": 1, count_key: 1}
            else:
                params = {**base, "sides": 2, count_key: math.ceil(share / 2 / per_machine - 1e-9)}
            return Step(item, demand, kind, params, rows, _rated(kind, params))
        except ValueError as e:
            errors.append(str(e))
    # The slowest tier tried says the most: a faster one only needs more.
    raise ValueError(f"{item}: no row fits ({errors[-1]}); pass raw={item} to supply it instead")


def _block(item):
    """A fluid block for the same-name recipe, if one fits (sulfur, acid, lubricant)."""
    try:
        fluid_block.plan(recipe=item)
    except ValueError:
        return None
    r = rates.recipe_any(item)
    return ("fluid_block", r) if list(r.results) == [item] else None


def _fluid_machines(tiers):
    # Chemical plants have one tier; assemblers follow the cap.
    return ("chemical-plant",) + tuple(m for m in tiers if m != "assembling-machine-1")


def _crafts(machine, r) -> bool:
    try:
        rates.crafts_per_second(machine, r)
        return True
    except ValueError:
        return False


def _rated(kind, params) -> float:
    p = GENERATORS[kind].plan(**params)
    return p.sides * p.per_side


def fuse(steps: list[Step], item: str, belt: str, shared=False) -> list[Step]:
    """The steps with each producer row whose product only one consumer row uses
    fused into a cell with it (generators/cell.py): the item handed over by
    inserter, no row or belt between them. Others, and pairs no cell fits, stay.
    shared: a producer whose product several rows use also fuses, into each of them
    that takes a cell, and keeps a row, sized as rows are, for the rest of the demand."""
    users = {}
    for s in steps:
        r = rates.recipe_any(s.params["recipe"])
        for i in [*r.ingredients, *r.fluids]:
            users.setdefault(i, []).append(s)
    if shared:
        return _fuse_shared(steps, item, belt, users)
    gone, cells = set(), {}
    for c in steps:
        if c.generator != "assembly_row":
            continue
        for p in steps:
            if p.generator != "assembly_row" or id(p) in gone or p.item == item or users.get(p.item) != [c]:
                continue
            try:
                cells[id(c)] = _cell_step(p, c, belt)
            except ValueError:
                continue
            gone |= {id(p), id(c)}
            break
    return [cells.get(id(s), s) for s in steps if id(s) not in gone or id(s) in cells]


def _fuse_shared(steps: list[Step], item: str, belt: str, users: dict) -> list[Step]:
    """fuse(shared=True): producers in build order, each into every consumer that takes a
    cell and has none yet; what the cells don't take stays a row, resized to the rest."""
    cells, left = {}, {}
    for p in steps:
        if p.generator != "assembly_row" or p.item == item or id(p) in cells:
            continue
        for c in users.get(p.item, []):
            if c.generator != "assembly_row" or id(c) in cells:
                continue
            try:
                cells[id(c)] = _cell_step(p, c, belt)
            except ValueError:
                continue
            r = rates.recipe_any(c.params["recipe"])
            left[id(p)] = left.get(id(p), p.demand) - c.demand * r.ingredients[p.item] / r.results[c.item]
    out = []
    for s in steps:
        if id(s) in cells:
            out.append(cells[id(s)])
        elif id(s) not in left:
            out.append(s)
        elif left[id(s)] > 1e-9 * max(1.0, s.demand):  # the rest, made the old way
            out.append(_size(s.item, left[id(s)], ("assembly_row", rates.recipe(s.item, fluids=True)),
                             s.params["belt"], None, [s.params["machine"]], _research(s)))
    return out


def _research(s: Step) -> dict:
    return {"research": s.params["research"]} if "research" in s.params else {}


def _cell_step(p: Step, c: Step, belt: str) -> Step:
    """One cell row type sized for the consumer's demand, as _size sizes rows: the fewest
    full-size cells, then just enough consumers in each, in the consumer's machine."""
    base = {"recipe": c.params["recipe"], "producer": p.params["recipe"], "machine": c.params["machine"], "belt": belt,
            **_research(c)}
    need = c.demand * SLACK
    full = _rated("cell", {**base, "sides": 2})
    rows = math.ceil(need / full - 1e-9)
    share = need / rows
    plan = cell.plan(**base)
    per = plan.c_crafts * plan.consumer.results[plan.product]
    if share <= per + 1e-9:
        params = {**base, "sides": 1, "consumers": 1}
    else:
        params = {**base, "sides": 2, "consumers": math.ceil(share / 2 / per - 1e-9)}
    return Step(c.item, c.demand, "cell", params, rows, _rated("cell", params))


def parts(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0, research="") -> list[tuple[str, dict]]:
    """(generator, params) of each row, in book order."""
    steps, _ = solve(item, rate, machine, belt, smelt, furnace, raw, oil, research)
    return [(s.generator, s.params) for s in steps for _ in range(s.rows)]


def build(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0, research="") -> BlueprintBook:
    steps, inputs = solve(item, rate, machine, belt, smelt, furnace, raw, oil, research)
    book = BlueprintBook()
    book.label = f"Production: {rate:g} {item}/s"
    supply = ", ".join(f"{v:.3g} {k}/s" for k, v in inputs.items() if v > 0) or "nothing"
    surplus = ", ".join(f"{-v:.3g} {k}/s" for k, v in inputs.items() if v < 0)
    if surplus:
        supply += f" (left over: {surplus}; use or store it, or the refineries stop)"
    plan = "; ".join(f"{s.item}: {s.rows} row{'s' if s.rows > 1 else ''} of {s.rate:.3g}/s "
                     f"for {s.demand:.3g}/s" for s in steps)
    tail = "The rows aren't connected: belt each row's product to the rows that use it."
    if any(s.item in CRACKING for s in steps):
        tail += (" Crack only surplus oil: feed cracking through a pump that runs while the oil's tank is "
                 "nearly full, or it starves lubricant and the other oil users.")
    description = f"Supply {supply}. Rows, ingredients first: {plan}. {tail}"
    if len(description.encode()) > TEXT_LIMITS["description"]:  # the game would cut it
        description = f"Supply {supply}. Each row's description gives its share. {tail}"
    book.description = description
    book.icons = [item]
    for s in steps:
        for k in range(s.rows):
            bp = GENERATORS[s.generator].build(**s.params)
            bp.description = (f"{s.item} row {k + 1} of {s.rows} ({s.demand / s.rows:.3g} of {s.demand:.3g}/s "
                              f"needed). " + bp.description)
            book.blueprints.append(bp)
    return book


def chain_tests(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
                furnace="steel-furnace", raw="", oil=0, research="") -> tuple[dict, list]:
    """The whole book as one blueprint and one flow test: every row built side by
    side, inputs the book supplies fed without limit, and everything the book
    makes passed from its producers to its consumers through a pool. The target
    rate must come out at the end."""
    from dataclasses import replace

    from validator.flow import Flow

    steps, _ = solve(item, rate, machine, belt, smelt, furnace, raw, oil, research)
    rows = []  # (blueprint data, ids, flow) per row
    for s in steps:
        gen = GENERATORS[s.generator]
        for _ in range(s.rows):
            bp = gen.build(**s.params)
            ids = {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
            (flow,) = gen.flow_tests(**s.params)
            rows.append((bp.to_dict(), ids, flow, s))
    made = {k for _, _, flow, _ in rows for k in flow.expect}
    data, numbers = _pack([d for d, _, _, _ in rows])

    feeds, drains, fluids = [], [], []
    for (d, ids, flow, s), renumber in zip(rows, numbers):
        def at(ref, ids=ids, renumber=renumber):
            return renumber[ids[ref] if isinstance(ref, str) else ref]

        feeds += [replace(f, entity=at(f.entity), pooled=f.item in made) for f in flow.feed]
        drains += [at(x) for x in flow.drain]
        fluids += [replace(f, entity=at(f.entity), pooled=not f.sink and f.fluid in made) for f in flow.fluids]
    # Pooled fluids go to feeds in order: cracking takes only what the rest leave,
    # as the book tells the player to build it (a pump switched by tank level).
    cracking = {at for (d, ids, flow, s), renumber in zip(rows, numbers) if s.item in CRACKING
                for at in [renumber[ids[f.entity]] for f in flow.fluids if isinstance(f.entity, str) and not f.sink]}
    fluids.sort(key=lambda f: f.pooled and f.entity in cracking)
    warmup = sum(max(flow.warmup for _, _, flow, s in rows if s is step) for step in steps)
    window = max(flow.window for _, _, flow, _ in rows)
    # Intermediates back up once a few seconds of output are pooled, as they would
    # on a full belt or pipe; only the final product drains freely. A buffer in
    # items rather than seconds would let slow rows run flat out for minutes and
    # eat ingredients their neighbours need.
    output = {}
    for _, _, flow, _ in rows:
        for k, v in flow.expect.items():
            output[k] = output.get(k, 0) + v
    caps = {k: max(BUFFER_MIN, BUFFER_SECONDS * v) for k, v in output.items() if k != item}
    return data, [Flow(f"chain of {len(rows)} rows: {rate:g} {item}/s", feeds, drains, {item: float(rate)},
                       warmup=warmup, window=window, fluids=fluids, at_least=True, caps=caps,
                       research=rates.research_levels(research))]


def _pack(blueprints: list[dict], gap: int = 4) -> tuple[dict, list[dict[int, int]]]:
    """Blueprints side by side in one, in shelves about as wide as the whole is tall.
    Returns it and, per blueprint, old entity_number -> new."""
    boxes = []
    for d in blueprints:
        xs = [e["position"]["x"] for e in d["blueprint"]["entities"]]
        ys = [e["position"]["y"] for e in d["blueprint"]["entities"]]
        boxes.append((math.floor(min(xs)) - 3, math.floor(min(ys)) - 3, math.ceil(max(xs)) + 3, math.ceil(max(ys)) + 3))
    area = sum((x2 - x1 + gap) * (y2 - y1 + gap) for x1, y1, x2, y2 in boxes)
    width = max(max(x2 - x1 for x1, _, x2, _ in boxes), math.isqrt(area))
    entities, wires, numbers = [], [], []
    x = y = shelf = 0
    for d, (x1, y1, x2, y2) in zip(blueprints, boxes):
        if x and x + (x2 - x1) > width:
            x, y, shelf = 0, y + shelf + gap, 0
        # Even shifts keep everything on the 2-tile grid the validator builds on.
        dx, dy = (x - x1) + (x - x1) % 2, (y - y1) + (y - y1) % 2
        renumber = {}
        for e in d["blueprint"]["entities"]:
            e = {**e, "position": {"x": e["position"]["x"] + dx, "y": e["position"]["y"] + dy}}
            renumber[e["entity_number"]] = e["entity_number"] = len(entities) + 1
            entities.append(e)
        wires += [[renumber[a], ca, renumber[b], cb] for a, ca, b, cb in d["blueprint"].get("wires", [])]
        numbers.append(renumber)
        x += (x2 - x1) + gap + 2
        shelf = max(shelf, y2 - y1 + 2)
    first = blueprints[0]["blueprint"]
    out = {"item": "blueprint", "label": "Production chain test", "entities": entities, "version": first["version"]}
    if wires:
        out["wires"] = wires
    return {"blueprint": out}, numbers
