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

from generators import assembly_row, fluid_row, smelting_column
from tools import rates
from tools.check import TEXT_LIMITS

MACHINES = ("assembling-machine-3", "assembling-machine-2", "assembling-machine-1")
GENERATORS = {"assembly_row": assembly_row, "fluid_row": fluid_row, "smelting_column": smelting_column}


@dataclass
class Step:
    item: str
    demand: float  # items/s needed
    generator: str
    params: dict  # one row's parameters
    rows: int
    rate: float  # rated items/s per row


def solve(item: str, rate: float, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="") -> tuple[list[Step], dict[str, float]]:
    """Steps in build order (ingredients first), and the inputs to supply (item -> items/s)."""
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
        how[i] = _how(i, tiers, smelt, furnace, forced_raw)
        if how[i]:
            for ingredient in [*how[i][1].ingredients, *how[i][1].fluids]:
                visit(ingredient, path + [i])
        order.append(i)  # after its ingredients

    visit(item, [])
    demand = {i: 0.0 for i in order}
    demand[item] = float(rate)
    for i in reversed(order):  # consumers before their ingredients
        if how[i] and demand[i]:
            r = how[i][1]
            crafts = demand[i] / r.results[i]
            for ingredient, amount in [*r.ingredients.items(), *r.fluids.items()]:
                demand[ingredient] += crafts * amount

    steps = [_size(i, demand[i], how[i], belt, furnace, tiers) for i in order if how[i]]
    inputs = {i: demand[i] for i in order if not how[i]}
    return steps, inputs


def _how(item, tiers, smelt, furnace, forced_raw):
    """("assembly_row" | "smelting_column", recipe), or None for an input to supply."""
    if item in forced_raw or item not in recipes.raw:
        return None
    try:
        r = rates.recipe(item, fluids=True)
    except ValueError:  # a fluid product, or variable results
        return None
    if list(r.results) != [item]:
        return None
    if r.fluids:
        ok = len(r.fluids) == 1 and any(_crafts(m, r) for m in _fluid_machines(tiers))
        return ("fluid_row", r) if ok else None
    if any(_crafts(m, r) for m in tiers):
        return "assembly_row", r
    if smelt and len(r.ingredients) == 1 and _crafts(furnace, r):
        return "smelting_column", r
    return None


def _size(item, demand, how, belt, furnace, tiers) -> Step:
    """Rows for one item, in the fastest tier whose row can be fed: the fewest
    full-size rows, then just enough machines in each."""
    kind, r = how
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
          furnace="steel-furnace", raw="") -> list[tuple[str, dict]]:
    """(generator, params) of each row, in book order."""
    steps, _ = solve(item, rate, machine, belt, smelt, furnace, raw)
    return [(s.generator, s.params) for s in steps for _ in range(s.rows)]


def build(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="") -> BlueprintBook:
    steps, inputs = solve(item, rate, machine, belt, smelt, furnace, raw)
    book = BlueprintBook()
    book.label = f"Production: {rate:g} {item}/s"
    supply = ", ".join(f"{v:g} {k}/s" for k, v in inputs.items()) or "nothing"
    plan = "; ".join(f"{s.item}: {s.rows} row{'s' if s.rows > 1 else ''} of {s.rate:g}/s "
                     f"for {s.demand:g}/s" for s in steps)
    tail = "The rows aren't connected: belt each row's product to the rows that use it."
    book.description = f"Supply {supply}. Rows, ingredients first: {plan}. {tail}"
    if len(book.description.encode()) > TEXT_LIMITS["description"]:  # the game would cut it
        book.description = f"Supply {supply}. Each row's description gives its share. {tail}"
    book.icons = [item]
    for s in steps:
        for k in range(s.rows):
            bp = GENERATORS[s.generator].build(**s.params)
            bp.description = (f"{s.item} row {k + 1} of {s.rows} ({s.demand / s.rows:g} of {s.demand:g}/s "
                              f"needed). " + bp.description)
            book.blueprints.append(bp)
    return book
