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
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter

from draftsman.data import entities, items

from tools import rates
from validator.rcon import Rcon

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
         "mismatch": [], "unmodelled": None}
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
    surfaces = Counter(g["surface"] for g in groups)
    diagnoses = Counter(g["diagnosis"].split(":")[0] for g in groups)
    out.append(f"summary: {len(groups)} groups ({', '.join(f'{s} {n}' for s, n in surfaces.most_common())})")
    out.append(f"  diagnoses: {', '.join(f'{d} {n}' for d, n in diagnoses.most_common())}")
    out.append(f"  {sum(bool(g['mismatch']) for g in groups)} with a model mismatch, "
               f"{sum(bool(g['unmodelled']) for g in groups)} unmodelled; "
               + ("every group listed" if every else f"{len(shown)} problems listed (--all lists every group)"))
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
    args = ap.parse_args(argv)
    if not args.password:
        ap.error("set GHOSTWRITER_RCON_PASSWORD (or --password)")
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
    finally:
        rcon.close()
    print(json.dumps(groups, indent=1) if args.json else "\n".join(lines(groups, args.all)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
