"""Production: a book of tested rows that makes ITEM at RATE per second.

    python -m tools.bp gen production item=electronic-circuit rate=5 --validate

Walks the recipe tree from the target. Each item an assembler makes from
items gets as many assembly rows (generators/assembly_row.py) as its demand
needs, in the fastest allowed machine whose row can be fed. machine= caps the
tiers: electromagnetic-plant adds it before assembling-machine-3, for the
recipes it crafts (circuits, cable, accumulators...); a step it can't feed or
craft falls back to an assembler, and says why on stderr (often a faster belt,
or research=, would let the plant). Its +50% productivity shrinks what the
ingredients' rows make. Items made with
one fluid (plastic, batteries, processing units) get fluid rows
(generators/fluid_row.py). With smelt=1, items a furnace makes from one ore
get smelting columns. With casting=ore (or lava), whatever a foundry casts
from molten metal and fits a row (plates, gears, steel, pipes; cable and sticks on
a belt fast enough) is cast in foundries (fluid rows), and the molten metal is
melted from ore and calcite (or lava and calcite, leaving stone to take away) in
a foundry block; the foundry's +50% shrinks what feeds it. Everything else is an input to supply: plates by
default, fluids (units/s), items made with two fluids or as a fluid (sulfur,
sulfuric acid), and whatever `raw` lists (comma-separated). The book's
description gives those input rates.

Rows aren't connected to each other. Each row's description says which item
goes on which input lane; the book says what each row makes and how much of
it is needed. `--validate` runs every row's own flow test in game.
"""

import functools
import math
import sys
from dataclasses import dataclass

from draftsman.blueprintable import BlueprintBook
from draftsman.data import fluids as fluid_data, recipes

from generators import assembly_row, cell, fluid_block, fluid_row, recycler_row, smelting_column
from tools import rates
from tools.check import TEXT_LIMITS

MACHINES = ("electromagnetic-plant", "assembling-machine-3", "assembling-machine-2", "assembling-machine-1")
GENERATORS = {"assembly_row": assembly_row, "fluid_row": fluid_row, "smelting_column": smelting_column,
              "fluid_block": fluid_block, "cell": cell, "recycler_row": recycler_row}
OIL = ("heavy-oil", "light-oil", "petroleum-gas")
BUFFER_SECONDS, BUFFER_MIN = 20, 5  # chain tests: output pooled before a product backs up
# Rows are sized for this much more than the demand: a chain sized exactly runs
# short, since every idle inserter swing falls through to the end (a robot frame
# chain made 91% of its target with cable and circuits sized to the last item).
SLACK = 1.05
CRACKING = ("heavy-oil-cracking", "light-oil-cracking")
# The recipes each oil= value plans with: oil=1 picks the fewest tiles among them (basic
# only when petroleum gas is all that's needed, see _oil), oil=advanced is the
# crude-efficient chain (and the one with cracking to test).
RECIPES = {1: ("advanced-oil-processing", "basic-oil-processing", *CRACKING),
           "advanced": ("advanced-oil-processing", *CRACKING),
           "coal": ("coal-liquefaction", *CRACKING)}
# Solid fuel's recipes, by the oil each takes. With oil= the oil plan picks one (a fluid row);
# without, solid fuel is made from light oil, the least oil a piece.
SOLID_FUEL = {f: f"solid-fuel-from-{f}" for f in OIL}
# casting=: item -> the foundry recipe that casts it from molten metal, and where molten metal
# comes from. An item is cast where a foundry row fits it (_cast); the rest is made as before.
CASTING = {"iron-plate": "casting-iron", "copper-plate": "casting-copper", "steel-plate": "casting-steel",
           "iron-gear-wheel": "casting-iron-gear-wheel", "iron-stick": "casting-iron-stick",
           "copper-cable": "casting-copper-cable", "pipe": "casting-pipe", "pipe-to-ground": "casting-pipe-to-ground",
           "low-density-structure": "casting-low-density-structure", "concrete": "concrete-from-molten-iron"}
MOLTEN = {"ore": {"molten-iron": "iron-ore-melting", "molten-copper": "copper-ore-melting"},
          "lava": {"molten-iron": "molten-iron-from-lava", "molten-copper": "molten-copper-from-lava"}}


@dataclass
class Step:
    item: str
    demand: float  # items (or fluid units) per second needed
    generator: str
    params: dict  # one row's parameters
    rows: int
    rate: float  # rated per row
    tradeoff: tuple[int, float] | None = None  # where beacons were weighed: the tiles and W they add
    note: str = ""  # why a faster machine that crafts the item wasn't used


def solve(item: str, rate: float, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0, research="", modules="",
          beacons="", casting="") -> tuple[list[Step], dict[str, float]]:
    """Steps in build order (ingredients first), and the inputs to supply (item -> items/s,
    fluid -> units/s). A negative input is a surplus the factory makes but doesn't use.
    research: inserter research the rows are sized for (rates measured at it); it rides in
    the params of every row whose inserters it sizes, and isn't there without it. modules: a
    module in every machine's slots (productivity: ingredients sized for what each
    machine makes); it rides in every row's params the same way. beacons: a module in
    beacons between an assembly or fluid row's machines or an electric furnace column's furnaces
    (only there), where they make it no bigger. casting: "ore" or "lava", molten metal's source
    for what foundries cast (CASTING); a by-product (lava's stone) is a negative input."""
    if machine not in MACHINES:
        raise ValueError(f"machine is one of {MACHINES}")
    rates.check_modules(modules)
    rates.check_beacon_modules(beacons)
    oil = oil or 0  # the CLI's oil= arrives as ""
    if oil not in (0, *RECIPES):
        raise ValueError(f"oil is one of 0, {', '.join(map(str, RECIPES))}, not {oil!r}")
    if casting not in ("", *MOLTEN):
        raise ValueError(f"casting is one of '', {', '.join(MOLTEN)}, not {casting!r}")
    tiers = MACHINES[MACHINES.index(machine):]
    forced_raw = {i for i in str(raw).split(",") if i}
    how, order = {}, []

    def visit(i, path):
        if i in how:
            return
        if i in path:
            raise ValueError(f"recipe loop: {' -> '.join(path + [i])}")
        how[i] = _how(i, tiers, smelt, furnace, forced_raw, oil, casting, cast)
        if how[i] and how[i][1]:
            for ingredient in [*how[i][1].ingredients, *how[i][1].fluids]:
                visit(ingredient, path + [i])
        order.append(i)  # after its ingredients

    extra = {"research": rates.research_key(research)} if rates.research_key(research) else {}
    if modules:
        extra["modules"] = modules
    if beacons:
        extra["beacons"] = beacons
    cast = functools.partial(_cast, belt=belt, research=extra.get("research", "")) if casting else None
    visit(item, [])
    if not how[item]:
        raise ValueError(_supplied_only(item, furnace, forced_raw))
    demand = {i: 0.0 for i in order}
    demand[item] = float(rate)
    sized, left = {}, {}  # left: by-products made (lava's stone)
    for i in reversed(order):  # consumers before their ingredients
        if how[i] and how[i][1]:
            sized[i] = _size(i, demand[i], how[i], belt, furnace, tiers, extra)
            if demand[i]:
                # What the rows take per item depends on the machine (and modules) they got.
                r = _made(sized[i], how[i][1])
                crafts = demand[i] / r.results[i]
                for ingredient, amount in [*r.ingredients.items(), *r.fluids.items()]:
                    demand[ingredient] += crafts * amount
                for k, amount in r.results.items():
                    if k != i:
                        left[k] = left.get(k, 0) + crafts * amount
    steps = [sized[i] for i in order if i in sized]
    inputs = {i: demand[i] for i in order if not how[i]}
    for k, v in left.items():
        if k in demand:  # ponytail: a by-product feeding the factory needs its line both made and supplied
            raise ValueError(f"{k}: a by-product here is also an ingredient; pass raw={k} and take it away")
        inputs[k] = -v
    if any((how.get(f) or (None,))[0] == "oil" and demand[f] for f in (*OIL, "solid-fuel")):
        oil_steps, oil_inputs = _oil({f: demand.get(f, 0) for f in (*OIL, "solid-fuel")}, oil, modules)
        oil_steps = [_size("solid-fuel", s.demand, ("fluid_row", rates.recipe(s.item, fluids=True)), belt,
                           furnace, tiers, extra) if s.item in SOLID_FUEL.values() else s for s in oil_steps]
        steps = oil_steps + steps
        for k, v in oil_inputs.items():
            inputs[k] = inputs.get(k, 0) + v
    return steps, inputs


def _oil(need: dict[str, float], oil=1, modules="") -> tuple[list[Step], dict[str, float]]:
    """Oil processing and cracking for heavy oil, light oil and petroleum gas per second, and
    solid fuel (need["solid-fuel"], optional) from one of SOLID_FUEL: from RECIPES[oil], the
    fewest measured tiles (a mixed-integer program over crafts, machines and which blocks
    exist), ties to less crude oil. Heavy oil, light oil and solid fuel are made exactly;
    petroleum gas left over is reported as a negative input. A solid fuel step's demand is
    solid fuel per second, for the caller to size as a fluid row. modules: in every machine."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    if oil == 1 and (need["heavy-oil"] or need["light-oil"]):
        # ponytail: basic only for petroleum gas alone. Block tiles leave out the factory row and
        # lines each extra block costs: basic mixed into advanced built 20% larger with 75% more
        # crude (utility packs). A per-block factory cost in the objective would lift this.
        oil = "advanced"
    fuel = need.get("solid-fuel", 0)
    names = (*RECIPES[oil], *(SOLID_FUEL.values() if fuel else ()))
    n = len(names)
    with_modules = {"modules": modules} if modules else {}
    plans = [(fluid_row if r in SOLID_FUEL.values() else fluid_block).plan(recipe=r, **with_modules) for r in names]
    per_machine = np.array([p.crafts for p in plans])
    rs = [rates.made(p.machine, rates.recipe_any(r), modules) for p, r in zip(plans, names)]
    per, fixed = zip(*map(_tiles, names))
    feed = [r.fluids.get("crude-oil", 0) for r in rs]
    made = (*OIL, "solid-fuel") if fuel else OIL
    net = np.array([[r.results.get(f, 0) - r.fluids.get(f, 0) for r in rs] for f in made])
    # big: more machines than any block gets. Every block here takes well under one machine per
    # unit/s of oil asked for (a basic refinery makes 9 petroleum gas/s, the others move more);
    # a unit/s of solid fuel takes up to 20 oil/s, about 2.3 basic refineries.
    eye, zero = np.eye(n), np.zeros((n, n))
    big = 10 + math.ceil(sum(need.get(f, 0) for f in OIL) + 3 * fuel)
    one_fuel = [[0] * 2 * n + [r in SOLID_FUEL.values() for r in names]]  # one solid fuel recipe: one row
    # x = crafts/s, machines, block exists; rows: net oil (and solid fuel), machines cover crafts,
    # no machines without a block, at most one solid fuel recipe.
    a = np.block([[net, np.zeros((len(made), 2 * n))],
                  [-np.diag(SLACK / per_machine), eye, zero],
                  [zero, eye, -big * eye]])
    a = np.vstack([a, one_fuel])
    lo = [need[f] for f in OIL] + [fuel] * (len(made) - len(OIL)) + [0] * n + [-np.inf] * n + [0]
    hi = ([need["heavy-oil"], need["light-oil"], np.inf] + [fuel] * (len(made) - len(OIL)) + [np.inf] * n
          + [0] * n + [1])
    # Petroleum gas left over stops the refineries unless something else takes it, so the least of it
    # comes first (1,000 tiles a unit/s), then the fewest tiles, then the least crude (1e-6 a unit:
    # never worth a tile). Solid fuel from petroleum gas is what burns it.
    surplus = 1_000 * net[OIL.index("petroleum-gas")]
    cost = [*(1e-6 * np.array(feed) + surplus), *per, *fixed]
    res = milp(cost, constraints=LinearConstraint(a, lo, hi), integrality=[0] * n + [1] * 2 * n,
               bounds=Bounds(0, [np.inf] * 2 * n + [1] * n))
    if res.status != 0:
        raise ValueError(f"no oil plan for {need} with oil={oil}: {res.message}")
    crafts, machines = res.x[:n], np.round(res.x[n:2 * n]).astype(int)
    steps, inputs = [], {}
    for name, r, c, m, p in zip(names, rs, crafts, machines, per_machine):
        if m == 0:
            continue
        if name in SOLID_FUEL.values():
            steps.append(Step(name, float(c) * r.results["solid-fuel"], "fluid_row", {"recipe": name}, 1, 0.0))
        else:
            steps.append(Step(name, float(c), "fluid_block", {"recipe": name, "machines": int(m), **with_modules},
                              1, float(m * p)))
        for k, amount in [*r.fluids.items(), *r.ingredients.items()]:
            if k not in OIL:
                inputs[k] = inputs.get(k, 0) + float(c) * amount
    surplus = float(net[OIL.index("petroleum-gas")] @ crafts) - need["petroleum-gas"]
    if surplus > 1e-6:
        inputs["petroleum-gas"] = -surplus
    return steps, inputs


def oil_line(item, rate, oil=0, **kw) -> str:
    """The oil=1 plan in one line: refinery recipes, machines and crude oil per second, and
    the crude oil=advanced would use. Empty without crude oil refining."""
    if oil != 1:
        return ""
    steps, inputs = solve(item, rate, oil=1, **kw)
    if not inputs.get("crude-oil"):
        return ""
    line = "oil: " + ", ".join(f"{s.item} x{s.params['machines']}" for s in steps if s.item in RECIPES[1])
    line += f"; crude oil {inputs['crude-oil']:.3g}/s"
    advanced = solve(item, rate, oil="advanced", **kw)[1]["crude-oil"]
    if abs(advanced - inputs["crude-oil"]) > 1e-6:
        line += f" (advanced with cracking would use {advanced:.3g}/s)"
    return line


@functools.cache
def _tiles(recipe: str) -> tuple[float, float]:
    """(tiles per machine, fixed tiles) of a fluid_block of RECIPE, measured as bounding-box
    area at 1 and 3 machines. Over a pair because coal liquefaction alternates (a coal belt
    gap every second machine)."""
    from tools.density import density

    if recipe in SOLID_FUEL.values():  # a two-sided fluid row: 1 and 3 machines a side
        one, three = (density(fluid_row.build(recipe=recipe, machines=n).to_dict())[0]["area"] for n in (1, 3))
        per = (three - one) / 4
        return per, one - 2 * per
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
        return f"{item} is smelted in a furnace: pass smelt=1{' or casting=ore' if item in CASTING else ''} to make it"
    return f"{item} has no recipe a factory here can make: it's an input to supply"


def _how(item, tiers, smelt, furnace, forced_raw, oil=0, casting="", cast=None):
    """(generator, recipe), ("oil", None) for oil products when oil=1, or None
    for an input to supply. With casting, molten metal is a foundry block and an item
    cast(recipe) fits a row for is cast."""
    if item in forced_raw:
        return None
    if casting and item in MOLTEN[casting]:
        return "fluid_block", rates.recipe_any(MOLTEN[casting][item])
    if casting and item in CASTING and (how := cast(CASTING[item])):
        return how
    if item in OIL:
        return ("oil", None) if oil else None
    if item == "solid-fuel":
        return ("oil", None) if oil else ("fluid_row", rates.recipe(SOLID_FUEL["light-oil"], fluids=True))
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
    a smelting column takes (research; it takes modules and beacons); beacons only assembly and
    fluid rows and electric furnaces' columns take."""
    kind, r = how
    extra = extra or {}
    if kind == "smelting_column":
        extra = {k: v for k, v in extra.items() if k != "research" and (k != "beacons" or rates.takes_beacons(furnace))}
    beaconed, extra = extra, {k: v for k, v in extra.items() if k != "beacons"}
    need = demand * SLACK
    if kind == "fluid_block":  # one block, as many machines as the demand needs
        p = fluid_block.plan(recipe=r.name, belt=belt, **extra)
        machines = math.ceil(need / (p.crafts * p.outputs[item]) - 1e-9)
        p = fluid_block.plan(recipe=r.name, machines=machines, belt=belt, **extra)
        return Step(item, demand, kind, {"recipe": r.name, "machines": machines, "belt": belt, **extra}, 1,
                    p.rate(item))
    errors = []
    casts = ("foundry",) if r.name in CASTING.values() else ()
    machines = {"assembly_row": tiers, "fluid_row": _fluid_machines(tiers) + casts}.get(kind, ())
    # Modules and beacons speed a machine past what its inserters feed: then the tier runs
    # without beacons, then without modules either.
    plain = {k: v for k, v in extra.items() if k != "modules"}
    tries = [e for k, e in enumerate([beaconed, extra, plain]) if e not in [beaconed, extra, plain][:k]]
    options = ([{"recipe": r.name, "machine": m, "belt": belt, **e} for m in machines if _crafts(m, r) for e in tries]
               if kind != "smelting_column" else [{"recipe": r.name, "furnace": furnace, "belt": belt, **e}
                                                  for e in tries])
    count_key = "furnaces" if kind == "smelting_column" else "machines"
    beaconed_step = None
    for base in options:
        try:
            for sides in (2, 1):  # a machine too wide to power two sides of still powers one
                try:
                    full = _rated(kind, {**base, "sides": sides})
                    break
                except ValueError:
                    if sides == 1:
                        raise
            rows = math.ceil(need / full - 1e-9)
            share = need / rows
            machine = base.get("machine") or base["furnace"]
            modules = base.get("modules", "")
            made = rates.made(machine, r, modules).results[item]
            per_machine = {sides: made * rates.crafts_per_second(machine, r, modules, base.get("beacons", ""),
                                                                 _beacons_per_machine(kind, machine, sides))
                           for sides in (1, 2)}
            if share <= per_machine[1] + 1e-9:
                params = {**base, "sides": 1, count_key: 1}
            elif sides == 1:
                params = {**base, "sides": 1, count_key: math.ceil(share / per_machine[1] - 1e-9)}
            else:
                params = {**base, "sides": 2, count_key: math.ceil(share / 2 / per_machine[2] - 1e-9)}
            step = Step(item, demand, kind, params, rows, _rated(kind, params))
        except ValueError as e:
            errors.append(str(e))
            continue
        if errors and base.get("machine") != options[0].get("machine"):
            step.note = f"{options[0]['machine']} not used: {errors[0]}"
        if beaconed_step and beaconed_step.params.get("machine") == base.get("machine"):
            # A beaconed row spaces its machines out: kept only where it saves tiles, or
            # saves power on as many.
            tiles, w = _row_area(beaconed_step) - _row_area(step), watts(beaconed_step) - watts(step)
            kept = beaconed_step if (tiles, w) < (0, 0) else step
            kept.tradeoff = (tiles, w)
            return kept
        if beaconed_step:
            return beaconed_step
        if "beacons" not in params:
            return step
        beaconed_step = step
    if beaconed_step:
        return beaconed_step
    # The slowest tier tried says the most: a faster one only needs more.
    raise ValueError(f"{item}: no row fits ({errors[-1]}); pass raw={item} to supply it instead")


def watts(s: Step) -> float:
    """Average electric W of a step's rows making its demand."""
    p = GENERATORS[s.generator].plan(**s.params)
    return s.rows * p.watts(min(1.0, s.demand / (s.rows * s.rate))) if s.rate else 0.0


def power_lines(steps: list[Step]) -> list[str]:
    """Each step's draw, with what beacons add where they were weighed, then the total."""
    lines = []
    for s in steps:
        line = f"{s.item} {rates.megawatts(watts(s))}"
        if s.tradeoff:
            tiles, w = s.tradeoff
            line += f" (beacons {'kept' if 'beacons' in s.params else 'dropped'}: {tiles:+d} tiles, {w / 1e6:+.2g} MW)"
        lines.append(line)
    return lines + [f"{rates.megawatts(sum(map(watts, steps)))} in all"]


def _beacons_per_machine(kind, machine, sides) -> int:
    if kind == "fluid_row":
        return fluid_row.beacons_per_machine(machine, sides)
    if kind == "smelting_column":
        return smelting_column.beacons_per_furnace(machine, sides)
    return assembly_row.beacons_per_machine(machine, sides)


def _row_area(s: Step) -> int:
    """Bounding-box tiles of a step's rows."""
    from tools.density import density

    return s.rows * density(GENERATORS[s.generator].build(**s.params).to_dict())[0]["area"]


def _cast(recipe, belt, research=""):
    """How a foundry casts `recipe` on `belt`: a one-sided fluid row for one fluid, a block for
    two; None if no row fits (cable on a yellow belt: 18/s a foundry), to make it as before."""
    try:
        if len(rates.recipe_any(recipe).fluids) == 1:
            fluid_row.plan(recipe=recipe, machine="foundry", belt=belt, sides=1, research=research)
            return "fluid_row", rates.recipe(recipe, fluids=True)
        fluid_block.plan(recipe=recipe, belt=belt, research=research)
        return "fluid_block", rates.recipe_any(recipe)
    except ValueError:
        return None


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
    that takes a cell, and keeps a row, sized as rows are, for the rest of the demand. Rows
    with beacons stay rows: cells take none."""
    users = {}
    for s in steps:
        r = rates.recipe_any(s.params["recipe"])
        for i in [*r.ingredients, *r.fluids]:
            users.setdefault(i, []).append(s)
    if shared:
        return _fuse_shared(steps, item, belt, users)
    gone, cells = set(), {}
    for c in steps:
        if not _fusable(c):
            continue
        for p in steps:
            if not _fusable(p) or id(p) in gone or p.item == item or users.get(p.item) != [c]:
                continue
            try:
                cells[id(c)] = _cell_step(p, c, belt)
            except ValueError:
                continue
            gone |= {id(p), id(c)}
            break
    return [cells.get(id(s), s) for s in steps if id(s) not in gone or id(s) in cells]


def _fusable(s: Step) -> bool:
    return s.generator == "assembly_row" and "beacons" not in s.params


def _fuse_shared(steps: list[Step], item: str, belt: str, users: dict) -> list[Step]:
    """fuse(shared=True): producers in build order, each into every consumer that takes a
    cell and has none yet; what the cells don't take stays a row, resized to the rest."""
    cells, left = {}, {}
    for p in steps:
        if not _fusable(p) or p.item == item or id(p) in cells:
            continue
        for c in users.get(p.item, []):
            if not _fusable(c) or id(c) in cells:
                continue
            try:
                cells[id(c)] = _cell_step(p, c, belt)
            except ValueError:
                continue
            r = _made(c, rates.recipe_any(c.params["recipe"]))
            left[id(p)] = left.get(id(p), p.demand) - c.demand * r.ingredients[p.item] / r.results[c.item]
    out = []
    for s in steps:
        if id(s) in cells:
            out.append(cells[id(s)])
        elif id(s) not in left:
            out.append(s)
        elif left[id(s)] > 1e-9 * max(1.0, s.demand):  # the rest, made the old way
            out.append(_size(s.item, left[id(s)], ("assembly_row", rates.recipe(s.item, fluids=True)),
                             s.params["belt"], None, [s.params["machine"]], _extra(s)))
    return out


def _made(s: Step, r: rates.Recipe) -> rates.Recipe:
    """`r` as the step's rows make it: with their machines' productivity, modules' and their own."""
    machine = s.params.get("machine") or s.params.get("furnace") or fluid_block.plan(recipe=r.name).machine
    return rates.made(machine, r, s.params.get("modules", ""))


def _extra(s: Step) -> dict:
    """The research and modules a step's rows take, as solve() put them."""
    return {k: s.params[k] for k in ("research", "modules") if k in s.params}


def _cell_step(p: Step, c: Step, belt: str) -> Step:
    """One cell row type sized for the consumer's demand, as _size sizes rows: the fewest
    full-size cells, then just enough consumers in each, in the consumer's machine."""
    base = {"recipe": c.params["recipe"], "producer": p.params["recipe"], "machine": c.params["machine"], "belt": belt,
            **_extra(c)}
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
          furnace="steel-furnace", raw="", oil=0, research="", modules="", beacons="",
          casting="") -> list[tuple[str, dict]]:
    """(generator, params) of each row, in book order."""
    steps, _ = solve(item, rate, machine, belt, smelt, furnace, raw, oil, research, modules, beacons, casting)
    return [(s.generator, s.params) for s in steps for _ in range(s.rows)]


def build(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0, research="", modules="", beacons="",
          casting="") -> BlueprintBook:
    steps, inputs = solve(item, rate, machine, belt, smelt, furnace, raw, oil, research, modules, beacons, casting)
    book = BlueprintBook()
    book.label = f"Production: {rate:g} {item}/s"
    supply = ", ".join(f"{v:.3g} {k}/s" for k, v in inputs.items() if v > 0) or "nothing"
    surplus = ", ".join(f"{-v:.3g} {k}/s" for k, v in inputs.items() if v < 0)
    if surplus:
        supply += f" (left over: {surplus}; use or store it, or what makes it stops)"
    plan = "; ".join(f"{s.item}: {s.rows} row{'s' if s.rows > 1 else ''} of {s.rate:.3g}/s "
                     f"for {s.demand:.3g}/s" for s in steps)
    tail = "The rows aren't connected: belt each row's product to the rows that use it."
    tail += prime_text(steps)
    if any(s.item in CRACKING for s in steps):
        tail += (" Crack only surplus oil: feed cracking through a pump that runs while the oil's tank is "
                 "nearly full, or it starves lubricant and the other oil users.")
    power = f" Draws about {rates.megawatts(sum(map(watts, steps)))}."
    description = f"Supply {supply}. Rows, ingredients first: {plan}.{power} {tail}"
    if len(description.encode()) > TEXT_LIMITS["description"]:  # the game would cut it
        description = f"Supply {supply}. Each row's description gives its share.{power} {tail}"
    book.description = description
    book.icons = [item]
    for s in steps:
        if s.note:
            print(f"production: {s.item} in {s.params['machine']}; {s.note}", file=sys.stderr)
    for line in power_lines(steps):
        print(f"production power: {line}", file=sys.stderr)
    for s in steps:
        for k in range(s.rows):
            bp = GENERATORS[s.generator].build(**s.params)
            bp.description = (f"{s.item} row {k + 1} of {s.rows} ({s.demand / s.rows:.3g} of {s.demand:.3g}/s "
                              f"needed). " + bp.description)
            book.blueprints.append(bp)
    return book


def chain_tests(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
                furnace="steel-furnace", raw="", oil=0, research="", modules="", beacons="",
          casting="") -> tuple[dict, list]:
    """The whole book as one blueprint and one flow test: every row built side by
    side, inputs the book supplies fed without limit, and everything the book
    makes passed from its producers to its consumers through a pool. The target
    rate must come out at the end."""
    from dataclasses import replace

    from validator.flow import Flow

    steps, inputs = solve(item, rate, machine, belt, smelt, furnace, raw, oil, research, modules, beacons, casting)
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
        # A fluid a block takes back from its own output (heavy oil in coal liquefaction) comes
        # from a source: the game's statistics, which credit the pool, leave that part out.
        fluids += [replace(f, entity=at(f.entity), pooled=not f.sink and f.fluid in made and f.fluid not in flow.expect)
                   for f in flow.fluids]
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
    # A by-product item (lava's stone) nothing uses drains freely too, as it would off a belt taken away.
    caps = {k: max(BUFFER_MIN, BUFFER_SECONDS * v) for k, v in output.items()
            if k != item and not (inputs.get(k, 0) < 0 and k not in fluid_data.raw)}
    return data, [Flow(f"chain of {len(rows)} rows: {rate:g} {item}/s", feeds, drains, {item: float(rate)},
                       warmup=warmup, window=window, fluids=fluids, at_least=True, caps=caps,
                       research=rates.research_levels(research))]


def primes(steps: list[Step]) -> dict[str, dict[str, float]]:
    """Recipe -> fluid -> units a player puts once into each machine of a block that takes back
    a fluid it makes (heavy oil in coal liquefaction), or it never starts: four crafts' worth."""
    out = {}
    for s in steps:
        if s.generator == "fluid_block":
            r = rates.recipe_any(s.params["recipe"])
            for fluid, amount in r.fluids.items():
                if fluid in r.results:
                    out.setdefault(r.name, {})[fluid] = 4 * amount
    return out


def prime_text(steps: list[Step]) -> str:
    """What a player puts in once, for a description; empty without such a block."""
    return "".join(f" Put {amount:g} {fluid} in each {recipe} once, or it never starts."
                   for recipe, fluids in primes(steps).items() for fluid, amount in fluids.items())


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
