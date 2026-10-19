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

import math
from dataclasses import dataclass

from draftsman.blueprintable import BlueprintBook
from draftsman.data import recipes

from generators import assembly_row, fluid_block, fluid_row, smelting_column
from tools import rates
from tools.check import TEXT_LIMITS

MACHINES = ("assembling-machine-3", "assembling-machine-2", "assembling-machine-1")
GENERATORS = {"assembly_row": assembly_row, "fluid_row": fluid_row, "smelting_column": smelting_column,
              "fluid_block": fluid_block}
OIL = ("heavy-oil", "light-oil", "petroleum-gas")


@dataclass
class Step:
    item: str
    demand: float  # items (or fluid units) per second needed
    generator: str
    params: dict  # one row's parameters
    rows: int
    rate: float  # rated per row


def solve(item: str, rate: float, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0) -> tuple[list[Step], dict[str, float]]:
    """Steps in build order (ingredients first), and the inputs to supply (item -> items/s,
    fluid -> units/s). A negative input is a surplus the factory makes but doesn't use."""
    if machine not in MACHINES:
        raise ValueError(f"machine is one of {MACHINES}")
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
    demand = {i: 0.0 for i in order}
    demand[item] = float(rate)
    for i in reversed(order):  # consumers before their ingredients
        if how[i] and how[i][1] and demand[i]:
            r = how[i][1]
            crafts = demand[i] / r.results[i]
            for ingredient, amount in [*r.ingredients.items(), *r.fluids.items()]:
                demand[ingredient] += crafts * amount

    steps = [_size(i, demand[i], how[i], belt, furnace, tiers) for i in order if how[i] and how[i][1]]
    inputs = {i: demand[i] for i in order if not how[i]}
    if any((how.get(f) or (None,))[0] == "oil" and demand[f] for f in OIL):
        oil_steps, oil_inputs = _oil({f: demand.get(f, 0) for f in OIL})
        steps = oil_steps + steps
        for k, v in oil_inputs.items():
            inputs[k] = inputs.get(k, 0) + v
    return steps, inputs


def _oil(need: dict[str, float]) -> tuple[list[Step], dict[str, float]]:
    """Refineries (advanced oil processing) and cracking for heavy oil, light oil and
    petroleum gas per second: the fewest refinery crafts that cover all three with
    heavy and light surplus cracked down. Petroleum gas left over is reported as a
    negative input."""
    adv = rates.recipe_any("advanced-oil-processing")
    heavy_crack, light_crack = rates.recipe_any("heavy-oil-cracking"), rates.recipe_any("light-oil-cracking")
    out = adv.results
    h_in, h_out = heavy_crack.fluids["heavy-oil"], heavy_crack.results["light-oil"]
    l_in, l_out = light_crack.fluids["light-oil"], light_crack.results["petroleum-gas"]
    H, L, P = need["heavy-oil"], need["light-oil"], need["petroleum-gas"]
    # x heavy-cracking and y light-cracking crafts/s for a crafts/s of refining:
    #   heavy:  out_h a - h_in x = H;  light: out_l a + h_out x - l_in y = L;  petroleum: out_p a + l_out y >= P
    def cracks(a):
        x = (out["heavy-oil"] * a - H) / h_in
        y = (out["light-oil"] * a + h_out * x - L) / l_in
        return x, y

    def petroleum(a):
        return out["petroleum-gas"] * a + l_out * cracks(a)[1]

    lo = max(H / out["heavy-oil"], 0)
    lo = max(lo, _root(lambda a: cracks(a)[1], lo))  # enough light oil before cracking it
    a = max(lo, _root(lambda a: petroleum(a) - P, lo))
    x, y = cracks(a)
    steps, inputs = [], {}
    for recipe, crafts in (("advanced-oil-processing", a), ("heavy-oil-cracking", x), ("light-oil-cracking", y)):
        if crafts <= 1e-9:
            continue
        p = fluid_block.plan(recipe=recipe)
        machines = math.ceil(crafts / p.crafts - 1e-9)
        steps.append(Step(recipe, crafts, "fluid_block", {"recipe": recipe, "machines": machines}, 1,
                          machines * p.crafts))
        for fluid, amount in rates.recipe_any(recipe).fluids.items():
            if fluid not in OIL:
                inputs[fluid] = inputs.get(fluid, 0) + crafts * amount
    surplus = petroleum(a) - P
    if surplus > 1e-6:
        inputs["petroleum-gas"] = -surplus
    return steps, inputs


def _root(f, lo):
    """The smallest a >= lo with f(a) >= 0, for f increasing and linear."""
    f0, f1 = f(lo), f(lo + 1)
    if f0 >= 0:
        return lo
    return lo - f0 / (f1 - f0)


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


def _size(item, demand, how, belt, furnace, tiers) -> Step:
    """Rows for one item, in the fastest tier whose row can be fed: the fewest
    full-size rows, then just enough machines in each."""
    kind, r = how
    if kind == "fluid_block":  # one block, as many machines as the demand needs
        p = fluid_block.plan(recipe=item)
        machines = math.ceil(demand / (p.crafts * r.results[item]) - 1e-9)
        p = fluid_block.plan(recipe=item, machines=machines, belt=belt)
        return Step(item, demand, kind, {"recipe": item, "machines": machines, "belt": belt}, 1, p.rate(item))
    errors = []
    machines = {"assembly_row": tiers, "fluid_row": _fluid_machines(tiers)}.get(kind, ())
    options = ([{"recipe": item, "machine": m, "belt": belt} for m in machines if _crafts(m, r)]
               if kind != "smelting_column" else [{"recipe": item, "furnace": furnace, "belt": belt}])
    count_key = "furnaces" if kind == "smelting_column" else "machines"
    for base in options:
        try:
            full = _rated(kind, {**base, "sides": 2})
            rows = math.ceil(demand / full - 1e-9)
            share = demand / rows
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


def parts(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0) -> list[tuple[str, dict]]:
    """(generator, params) of each row, in book order."""
    steps, _ = solve(item, rate, machine, belt, smelt, furnace, raw, oil)
    return [(s.generator, s.params) for s in steps for _ in range(s.rows)]


def build(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0) -> BlueprintBook:
    steps, inputs = solve(item, rate, machine, belt, smelt, furnace, raw, oil)
    book = BlueprintBook()
    book.label = f"Production: {rate:g} {item}/s"
    supply = ", ".join(f"{v:.3g} {k}/s" for k, v in inputs.items() if v > 0) or "nothing"
    surplus = ", ".join(f"{-v:.3g} {k}/s" for k, v in inputs.items() if v < 0)
    if surplus:
        supply += f" (left over: {surplus}; use or store it, or the refineries stop)"
    plan = "; ".join(f"{s.item}: {s.rows} row{'s' if s.rows > 1 else ''} of {s.rate:.3g}/s "
                     f"for {s.demand:.3g}/s" for s in steps)
    tail = "The rows aren't connected: belt each row's product to the rows that use it."
    book.description = f"Supply {supply}. Rows, ingredients first: {plan}. {tail}"
    if len(book.description.encode()) > TEXT_LIMITS["description"]:  # the game would cut it
        book.description = f"Supply {supply}. Each row's description gives its share. {tail}"
    book.icons = [item]
    for s in steps:
        for k in range(s.rows):
            bp = GENERATORS[s.generator].build(**s.params)
            bp.description = (f"{s.item} row {k + 1} of {s.rows} ({s.demand / s.rows:.3g} of {s.demand:.3g}/s "
                              f"needed). " + bp.description)
            book.blueprints.append(bp)
    return book
