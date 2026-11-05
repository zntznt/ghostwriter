"""Watch a running game over RCON: how each group of crafting machines really does
against ghostwriter's model.

    GHOSTWRITER_RCON_PASSWORD=... python -m tools.observe --port 27015 --seconds 30

Read-only: it sends fixed Lua that only reads, never caller-supplied Lua. It
reads every assembling machine and furnace of the player force, waits `seconds`
of game time (polling game.tick; it never changes game speed) sampling their
statuses about once a game second, then reads them again. Machines group by
(surface, machine, quality, recipe, modules). The plain report lists only the
problems, most output lost first (--all lists every group), then a summary.

Verified in game (2.1.23): products_finished counts crafts, productivity's bonus
crafts included (two machines with 40% productivity finished 173 crafts as 174
circuits left); crafting_speed and productivity_bonus include modules and
beacons; get_beacons() lists the beacons affecting a machine; an idle furnace
has no get_recipe() but previous_recipe = {name = LuaRecipePrototype, quality}.
productivity_bonus leaves out the force's recipe bonus from research
(force.recipes[r].productivity_bonus), which still makes bonus crafts, the two
capped together at the recipe's maximum_productivity. An unpowered beacon passes
on nothing. A full output is mostly a base asking for no more: it is reported as
demand-limited, not a problem; so is a recycler waiting for space in its result
slots, its status for the same.

--suggest N says, for each of the top N problems a layout can fix (an ingredient
shortage, or below rated with no stall seen; not power, fuel, a recipe or a circuit)
that makes something, what ghostwriter would build to make what the group makes now
(--target rated: what it's rated for, so a group making nothing too): the generator for
its machine (assembly_row; fluid_row, else fluid_block, with a fluid; smelting_column
for a furnace; recycler_row for a recycler, sized by its mean results), in the same
machine, with its modules and beacons where every machine holds one normal module
filling its slots alike, on --belt, inserters sized for --research (none: the rates
any game gets). One row, or a production book when a row can't carry it; else the
generators' reason none fits. Normal-quality machines and no research productivity:
a group of better ones gets more machines than it needs, never fewer.
"""

import argparse
import functools
import json
import math
import os
import sys
import time
import warnings
from collections import Counter

from draftsman.data import entities, items

from generators import production, recycler_row
from tools import rates
from tools.density import density
from validator.rcon import Rcon

RESEARCH = r"""/sc local n = 0
for i = 1, 99 do
  local t = game.forces.player.technologies["inserter-capacity-bonus-" .. i]
  if not t then break end
  if t.researched then n = i end
end
rcon.print(n)"""


def measured_research(level: int) -> str:
    """The most inserter-capacity research with measured rates at or below `level`, "" for none."""
    fits = [k for k in rates._inserter_file().get("researched", {})
            if set(rates.research_levels(k)) == {"inserter-capacity-bonus"}
            and rates.research_levels(k)["inserter-capacity-bonus"] <= level]
    return max(fits, key=lambda k: rates.research_levels(k)["inserter-capacity-bonus"], default="")


SNAPSHOT = r"""/sc local out = {tick = game.tick, machines = {}}
local names = {}
for k, v in pairs(defines.entity_status) do names[v] = k end
for _, s in pairs(game.surfaces) do
  for _, e in pairs(s.find_entities_filtered{type = {"assembling-machine", "furnace"}, force = "player"}) do
    local r = e.get_recipe()
    if not r and e.type == "furnace" and e.previous_recipe then r = e.previous_recipe.name end
    local inv = e.get_module_inventory()
    local beacons = {}
    for _, b in pairs(e.get_beacons() or {}) do
      local bi = b.get_module_inventory()
      beacons[#beacons + 1] = {name = b.name, quality = b.quality.name, status = names[b.status],
        modules = bi and bi.get_contents() or {}}
    end
    local researched = r and e.force.recipes[r.name]
    out.machines[#out.machines + 1] = {unit = e.unit_number, surface = s.name, name = e.name, quality = e.quality.name,
      recipe = r and r.name, energy = r and r.energy, x = e.position.x, y = e.position.y,
      speed = e.crafting_speed, productivity = e.productivity_bonus, finished = e.products_finished,
      research = researched and researched.productivity_bonus or 0,
      modules = inv and inv.get_contents() or {}, beacons = beacons}
  end
end
rcon.print(helpers.table_to_json(out))"""

STATUSES = r"""/sc local out = {tick = game.tick, statuses = {}}
local names = {}
for k, v in pairs(defines.entity_status) do names[v] = k end
for _, s in pairs(game.surfaces) do
  for _, e in pairs(s.find_entities_filtered{type = {"assembling-machine", "furnace"}, force = "player"}) do
    out.statuses[tostring(e.unit_number)] = names[e.status] or tostring(e.status)
  end
end
rcon.print(helpers.table_to_json(out))"""

TICK = "/sc rcon.print(game.tick)"

WORKING = {"working", "normal"}
DEMAND_LIMITED = "demand-limited (output full)"
FLUID_PER_BARREL = 50
AT_RATE = 0.9  # a share at least this is "at rate"
LAYOUT = ("ingredient shortage", "below rated")  # the diagnoses a layout can fix
DIAGNOSES = {
    "item_ingredient_shortage": "starved", "fluid_ingredient_shortage": "starved", "no_ingredients": "starved",
    "full_output": DEMAND_LIMITED, "waiting_for_space_in_destination": DEMAND_LIMITED,
    "low_power": "short of power", "no_power": "no power", "no_fuel": "out of fuel",
    "no_recipe": "no recipe set", "no_minable_resources": "no minable resources",
}


def observe(rcon, seconds: float = 30, area=None, stuck: float = 10) -> list[dict]:
    """Two snapshots `seconds` of game time apart, statuses sampled between, as report()."""
    first = _query(rcon, SNAPSHOT)
    end, samples, next_sample = first["tick"] + round(seconds * 60), [], first["tick"]
    last, since = first["tick"], time.monotonic()
    while (tick := int(rcon.command(TICK))) < end:
        if tick >= next_sample:
            samples.append(_query(rcon, STATUSES))
            next_sample = tick + 60
        if tick != last:
            last, since = tick, time.monotonic()
        elif time.monotonic() - since > stuck:
            raise RuntimeError(f"the game hasn't ticked in {stuck:g}s (paused, or no players with auto_pause?)")
        time.sleep(0.02)
    return report(first, _query(rcon, SNAPSHOT), samples, area)


def _query(rcon, lua: str) -> dict:
    reply = rcon.command(lua.replace("\n", " "))
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        raise RuntimeError(f"unexpected reply: {reply[:500]}") from None


def _list(value) -> list:
    return value if isinstance(value, list) else []  # table_to_json writes an empty table as {}


def _label(modules: list[dict]) -> str:
    return ", ".join(f"{m['count']} {m['name']}" + _quality(m) for m in sorted(_list(modules), key=lambda m: m["name"]))


def _quality(x: dict) -> str:
    return "" if x.get("quality", "normal") == "normal" else f" ({x['quality']})"


def _held(modules) -> list[tuple[str, str]]:
    return [(x["name"], x.get("quality", "normal")) for x in _list(modules) for _ in range(x["count"])]


def _model(m: dict, r: rates.Recipe) -> tuple[float, float]:
    """(crafting_speed, productivity_bonus) the model gives this machine; ValueError where it can't say."""
    beacons = _list(m["beacons"])
    if {b["name"] for b in beacons} - {"beacon"}:
        raise ValueError("beacons of another kind")
    things = [m, *_list(m["modules"]), *beacons, *(x for b in beacons for x in _list(b["modules"]))]
    if {x.get("quality", "normal") for x in things} - set(rates.QUALITY):
        raise ValueError("an unknown quality")
    received = [(b.get("quality", "normal"), [] if b.get("status") == "no_power" else _held(b["modules"]))
                for b in beacons]
    held = _held(m["modules"])
    return (rates.crafts_per_second(m["name"], r, held, received, quality=m.get("quality", "normal")) * r.time,
            rates.productivity(m["name"], r, held))


def report(first: dict, second: dict, samples: list[dict], area=None) -> list[dict]:
    """One dict per group of machines in both snapshots (and in `area`, x1,y1,x2,y2, if given)."""
    before = {m["unit"]: m for m in _list(first["machines"])}
    seconds = (second["tick"] - first["tick"]) / 60
    groups = {}
    for m in _list(second["machines"]):
        if m["unit"] not in before:
            continue
        if area:
            x1, y1, x2, y2 = area
            if not (min(x1, x2) <= m["x"] <= max(x1, x2) and min(y1, y2) <= m["y"] <= max(y1, y2)):
                continue
        key = (m["surface"], m["name"], m.get("quality", "normal"), m.get("recipe") or "", _label(m["modules"]))
        groups.setdefault(key, []).append(m)
    return [_group(key, ms, before, seconds, samples) for key, ms in sorted(groups.items())]


def _group(key, ms: list[dict], before: dict, seconds: float, samples: list[dict]) -> dict:
    surface, machine, quality, recipe, modules = key
    crafts = sum(m["finished"] - before[m["unit"]]["finished"] for m in ms) / seconds
    # products_finished counts productivity's bonus crafts, research's included, so the rate it can reach does.
    cap = rates.max_productivity(recipe)
    rated = sum(m["speed"] * (1 + min(m["productivity"] + m.get("research", 0), cap)) / m["energy"]
                for m in ms if m.get("energy"))
    units = {str(m["unit"]) for m in ms}
    seen = Counter(s for sample in samples for u, s in _statuses(sample).items() if u in units)
    total = sum(seen.values())
    g = {"surface": surface, "machine": machine, "quality": quality, "recipe": recipe, "modules": modules,
         "count": len(ms), "crafts_per_second": crafts, "rated_crafts_per_second": rated, "share": crafts / rated if rated else None,
         "statuses": {s: n / total for s, n in seen.most_common()}, "products": {},
         "crafting_speed": sum(m["speed"] for m in ms) / len(ms), "model_crafting_speed": None,
         "mismatch": [], "unmodelled": None,
         "uniform_modules": _filled([m["modules"] for m in ms], entities.raw.get(machine, {}).get("module_slots", 0)),
         "uniform_beacons": _beaconed(ms)}
    try:
        r = rates.recipe_any(recipe, means=True) if recipe else None
        if r and machine not in entities.raw:
            raise ValueError(f"unknown machine {machine}")
    except ValueError as e:
        r, g["unmodelled"] = None, str(e)
    if r:
        g["products"] = {p: {"actual": crafts * n, "rated": rated * n} for p, n in r.results.items()}
        _check(g, ms, r)
    g["diagnosis"] = _diagnose(g, seen)
    # Output lost in items/s; a fluid by the barrel. Without products, in crafts/s.
    g["lost"] = sum((v["rated"] - v["actual"]) / (1 if p in items.raw else FLUID_PER_BARREL)
                    for p, v in g["products"].items()) if g["products"] else g["rated_crafts_per_second"] - crafts
    return g


def _filled(holders: list, slots: int) -> str:
    """The module filling all `slots` slots of every holder alike, normal quality; "" if none does."""
    kinds = {tuple(sorted((x["name"], x.get("quality", "normal"), x["count"]) for x in _list(h))) for h in holders}
    if len(kinds) != 1:
        return ""
    (k,) = kinds
    return k[0][0] if len(k) == 1 and k[0][1:] == ("normal", slots) else ""


def _beaconed(ms: list[dict]) -> str:
    """The module every powered beacon of every machine holds alike (_filled); "" if any has none."""
    beacons = [_list(m["beacons"]) for m in ms]
    if not all(beacons) or any(b["name"] != "beacon" or b.get("status") == "no_power" for bs in beacons for b in bs):
        return ""
    return _filled([b["modules"] for bs in beacons for b in bs], entities.raw["beacon"]["module_slots"])


def _statuses(sample: dict) -> dict:
    s = sample.get("statuses")
    return s if isinstance(s, dict) else {}


def _check(g: dict, ms: list[dict], r: rates.Recipe):
    """Fills the group's model_crafting_speed and the ways the game differs from the model."""
    speeds, diffs, unmodelled = [], Counter(), Counter()
    for m in ms:
        try:
            speed, productivity = _model(m, r)
        except ValueError as e:
            unmodelled[str(e)] += 1
            continue
        speeds.append(speed)
        for what, game, model in (("crafting_speed", m["speed"], speed),
                                  ("productivity_bonus", m["productivity"], productivity),
                                  ("recipe time", m["energy"], r.time)):
            if not math.isclose(game, model, rel_tol=0.01, abs_tol=1e-6):
                diffs[f"{what} {game:.4g} in game, model {model:.4g}"] += 1
    if speeds:
        g["model_crafting_speed"] = sum(speeds) / len(speeds)
    g["mismatch"] = [f"{text} ({n} machines)" for text, n in diffs.items()]
    if unmodelled:
        g["unmodelled"] = "; ".join(f"{text} ({n} machines)" for text, n in unmodelled.items())


def _diagnose(g: dict, seen: Counter) -> str:
    if not g["recipe"]:
        return "no recipe set"
    if g["share"] is not None and g["share"] >= AT_RATE:
        return "at rate"
    stalls = [(n, s) for s, n in seen.items() if s not in WORKING]
    if not stalls:
        return "below rated with no stall seen (watch longer?)"
    _, top = max(stalls)
    text = DIAGNOSES.get(top, f"mostly {top}")
    if text == "starved":
        need = math.ceil(g["crafts_per_second"] / (g["rated_crafts_per_second"] / g["count"]) - 1e-9)
        text = f"ingredient shortage: the inputs feed about {need} of {g['count']} machines" if need \
            else "ingredient shortage: no ingredients arrive"
    return text


def problem(g: dict) -> bool:
    """A group falling short of its rate for a reason other than demand, or the model's."""
    return bool(g["mismatch"]) or (g["diagnosis"] not in ("at rate", DEMAND_LIMITED) and g["lost"] > 1e-9)


def suggest(groups: list[dict], target: str = "actual", belt: str = "turbo-transport-belt", research: str = "",
            top: int = 10) -> list[dict]:
    """The groups, each of the `top` problems a layout can fix that make something to aim for
    (most output lost first) with a "suggestion": what ghostwriter would build to make the
    group's `target` output ("actual" or "rated"), or why no generator fits. A recipe with
    variable results gets why, past the `top` too, unless a recycler makes it (recycler_row
    sizes by the mean results): no other generator takes one, so there's nothing to build."""
    ranked = sorted((g for g in groups if fixable(g) and (not g["products"] or any(
        v[target] > 1e-9 for v in g["products"].values()))), key=lambda g: -g["lost"])
    made, tried = {}, 0
    for g in ranked:
        try:
            if g["machine"] != recycler_row.MACHINE:
                rates.recipe_any(g["recipe"])
        except ValueError as e:
            made[id(g)] = {"kind": "no generator fits", "reason": str(e)}
            continue
        if tried < top:
            made[id(g)], tried = _suggestion(g, target, belt, research), tried + 1
    return [{**g, "suggestion": made[id(g)]} if id(g) in made else g for g in groups]


def fixable(g: dict) -> bool:
    """A problem a layout can fix: not power, fuel, a recipe or a circuit."""
    return problem(g) and g["diagnosis"].startswith(LAYOUT)


def _suggestion(g: dict, target: str, belt: str, research: str) -> dict:
    if g["unmodelled"] or not g["products"]:
        return {"kind": "no generator fits", "reason": g["unmodelled"] or "it makes nothing the model knows"}
    item, v = max(g["products"].items(), key=lambda kv: kv[1][target])
    rate = v[target]
    machine, r = g["machine"], rates.recipe_any(g["recipe"], means=True)
    recycler = machine == recycler_row.MACHINE  # a furnace to the game, but a row of its own
    furnace = not recycler and entities.raw[machine]["type"] == "furnace"
    if not furnace and not rates.electric(machine):
        return {"kind": "no generator fits", "reason": f"{machine} burns fuel, and ghostwriter's rows feed it none"}
    base = {"recipe": g["recipe"], **({} if recycler else {"furnace" if furnace else "machine": machine}), "belt": belt}
    base |= {k: g[f"uniform_{k}"] for k in ("modules", "beacons") if g[f"uniform_{k}"]}
    if research and not furnace:  # a furnace's one inserter each way needs none
        base["research"] = research
    if recycler:
        kinds = ("recycler_row",)
    elif furnace:
        kinds = ("smelting_column",)
    elif r.fluids or set(r.results) - set(items.raw):
        kinds = ("fluid_row", "fluid_block")
    else:
        kinds = ("assembly_row",)
    errors = []
    for kind in kinds:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return _sized(kind, base, item, rate, r)
        except ValueError as e:
            errors.append(f"{kind}: {e}")
    return {"kind": "no generator fits", "reason": "; ".join(errors)}


def _sized(kind: str, base: dict, item: str, rate: float, r: rates.Recipe) -> dict:
    """The fewest full rows of `kind` making `rate` of `item`, then just enough machines in each,
    with production's slack, as production sizes a step."""
    need = rate * production.SLACK
    if kind == "fluid_block":  # one block, as many machines as it takes; it takes no beacons
        base = {k: v for k, v in base.items() if k != "beacons"}
        params = {**base, "machines": math.ceil(need / _plan(kind, _key({**base, "machines": 1})).rate(item) - 1e-9)}
        rows, made, machines = 1, _plan(kind, _key(params)).rate(item), params["machines"]
    else:
        count = "furnaces" if kind == "smelting_column" else "machines"
        for sides in (2, 1):  # a machine too wide to power two sides of still powers one
            try:
                full = _rated(kind, {**base, "sides": sides})
                break
            except ValueError:
                if sides == 1:
                    raise
        rows = math.ceil(need / full - 1e-9)
        share = need / rows
        if share <= _rated(kind, {**base, "sides": 1, count: 1}) + 1e-9:
            sides, n = 1, 1
        else:
            n = math.ceil(share / _rated(kind, {**base, "sides": sides, count: 1}) - 1e-9)
        params = {**base, "sides": sides, count: n}
        made, machines = _rated(kind, params), rows * sides * n
    command = f"python -m tools.bp gen {kind} " + " ".join(f"{k}={v}" for k, v in params.items())
    if rows > 1:
        command = _book(kind, base, item, rate, r) or f"{rows} rows of: {command}"
    return {"kind": "build", "generator": kind, "params": params, "rows": rows, "machines": machines,
            "item": item, "target": rate, "rate": rows * made, "tiles": rows * _area(kind, _key(params)),
            "watts": rows * _plan(kind, _key(params)).watts(min(1.0, rate / (rows * made))), "command": command}


def _book(kind: str, base: dict, item: str, rate: float, r: rates.Recipe) -> str:
    """The production book making `rate` of `item` from its ingredients, as `kind` rows of the
    base's machine; "" where production can't pick that machine."""
    machine = base.get("machine")
    if kind == "smelting_column":
        how = {"smelt": 1, "furnace": base["furnace"]}
    elif machine in production.MACHINES:
        how = {"machine": machine}
    elif kind == "fluid_row" and machine == "chemical-plant":  # production tries it first for a fluid row
        how = {}
    else:
        return ""
    params = {"item": item, "rate": f"{rate:.4g}", **how, "belt": base["belt"], "raw": ",".join([*r.ingredients, *r.fluids]),
              **{k: base[k] for k in ("modules", "beacons", "research") if k in base}}
    return "python -m tools.bp gen production " + " ".join(f"{k}={v}" for k, v in params.items())


def _key(params: dict) -> tuple:
    return tuple(sorted(params.items()))


@functools.cache
def _plan(kind: str, params: tuple):
    return production.GENERATORS[kind].plan(**dict(params))


def _rated(kind: str, params: dict) -> float:
    p = _plan(kind, _key(params))
    return p.sides * p.per_side


@functools.cache
def _area(kind: str, params: tuple) -> int:
    """Bounding-box tiles of one row."""
    return density(production.GENERATORS[kind].build(**dict(params)).to_dict())[0]["area"]


def _suggestion_text(g: dict) -> str:
    s = g["suggestion"]
    if s["kind"] != "build":
        return f"{s['kind']}: {s['reason']}"
    return (f"{s['machines']} machine{'s' if s['machines'] != 1 else ''} (now {g['count']}), {s['tiles']} tiles, about {rates.megawatts(s['watts'])} "
            f"for {s['target']:.3g} {s['item']}/s: {s['command']}")


def lines(groups: list[dict], every: bool = False) -> list[str]:
    """The plain report: the problems, most output lost first (or `every` group, in order), a
    line each with its diagnosis and any model mismatch; then a summary."""
    shown = groups if every else sorted(filter(problem, groups), key=lambda g: -g["lost"])
    out = []
    for g in shown:
        share = f"{g['share']:.0%}" if g["share"] is not None else "-"
        made = "; ".join(f"{p} {v['actual']:.2f}/s of {v['rated']:.2f}/s" for p, v in g["products"].items()) \
            or f"{g['crafts_per_second']:.2f} crafts/s of {g['rated_crafts_per_second']:.2f}"
        statuses = ", ".join(f"{s} {v:.0%}" for s, v in list(g["statuses"].items())[:3])
        modules = f" [{g['modules']}]" if g["modules"] else ""
        out.append(f"{g['count']:>4} {g['machine']}{_quality(g)} {g['recipe'] or '(none)'}{modules} on {g['surface']}: "
                   f"{made} ({share}); {statuses}")
        lost = f" (lost {g['lost']:.2f}/s)" if problem(g) and g["lost"] > 0.005 else ""
        out.append(f"     -> {g['diagnosis']}{lost}")
        out += [f"     model mismatch: {m}" for m in g["mismatch"]]
        if g["unmodelled"]:
            out.append(f"     unmodelled: {g['unmodelled']}")
        if "suggestion" in g:
            out.append(f"     suggest: {_suggestion_text(g)}")
    surfaces = Counter(g["surface"] for g in groups)
    diagnoses = Counter(g["diagnosis"].split(":")[0] for g in groups)
    out.append(f"summary: {len(groups)} groups ({', '.join(f'{s} {n}' for s, n in surfaces.most_common())})")
    out.append(f"  diagnoses: {', '.join(f'{d} {n}' for d, n in diagnoses.most_common())}")
    out.append(f"  {sum(bool(g['mismatch']) for g in groups)} with a model mismatch, "
               f"{sum(bool(g['unmodelled']) for g in groups)} unmodelled; "
               + ("every group listed" if every else f"{len(shown)} problems listed (--all lists every group)"))
    kinds = Counter(g["suggestion"]["kind"] for g in groups if "suggestion" in g)
    if kinds:
        problems = list(filter(problem, groups))
        cant = sum(not fixable(g) for g in problems)
        out.append(f"  suggestions: {kinds['build']} to build, {kinds['no generator fits']} with no generator fitting; "
                   f"{cant} problems a layout can't fix, {len(problems) - cant - kinds.total()} more not tried "
                   "(--suggest N tries N)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.observe", description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=27015)
    ap.add_argument("--password", default=os.environ.get("GHOSTWRITER_RCON_PASSWORD"),
                    help="default $GHOSTWRITER_RCON_PASSWORD (preferred: it stays out of the process list)")
    ap.add_argument("--seconds", type=float, default=30, help="game seconds to watch")
    ap.add_argument("--area", help="x1,y1,x2,y2: only machines in it")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--all", action="store_true", help="list every group, not only the problems")
    ap.add_argument("--suggest", type=int, nargs="?", const=10, default=0, metavar="N",
                    help="what ghostwriter would build instead, for the top N problems (default 10)")
    ap.add_argument("--target", choices=("actual", "rated"), default="actual",
                    help="what a suggestion makes: the group's actual output (default), or its rated output")
    ap.add_argument("--belt", default="turbo-transport-belt", help="the belt suggestions use")
    ap.add_argument("--research", default="auto", help="inserter research suggestions are sized for, e.g. "
                                                        "inserter-capacity-bonus=7, or none (default: the game's)")
    args = ap.parse_args(argv)
    if not args.password:
        ap.error("set GHOSTWRITER_RCON_PASSWORD (or --password)")
    if entities.raw.get(args.belt, {}).get("type") != "transport-belt":
        ap.error(f"--belt: {args.belt} isn't a belt")
    if args.research == "none":
        args.research = ""
    try:
        if args.research != "auto":
            rates.research_levels(args.research)
    except ValueError:
        ap.error("--research takes name=level,...")
    area = None
    if args.area:
        try:
            area = tuple(float(v) for v in args.area.split(","))
        except ValueError:
            area = ()
        if len(area) != 4:
            ap.error("--area takes x1,y1,x2,y2")
    rcon = Rcon(args.host, args.port, args.password)
    try:
        groups = observe(rcon, args.seconds, area)
        if args.research == "auto":
            args.research = measured_research(_query(rcon, RESEARCH))
    finally:
        rcon.close()
    if args.suggest:
        groups = suggest(groups, args.target, args.belt, args.research, args.suggest)
    print(json.dumps(groups, indent=1) if args.json else "\n".join(lines(groups, args.all)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
