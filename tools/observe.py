"""Watch a running game over RCON: how each group of crafting machines really does
against ghostwriter's model.

    GHOSTWRITER_RCON_PASSWORD=... python -m tools.observe --port 27015 --seconds 30

Read-only: it sends fixed Lua that only reads, never caller-supplied Lua. It
reads every assembling machine and furnace of the player force, waits `seconds`
of game time (polling game.tick; it never changes game speed) sampling their
statuses about once a game second, then reads them again. Machines group by
(surface, machine, recipe, modules).

Verified in game (2.1.23): products_finished counts crafts, productivity's bonus
crafts included (two machines with 40% productivity finished 173 crafts as 174
circuits left); crafting_speed and productivity_bonus include modules and
beacons; get_beacons() lists the beacons affecting a machine; an idle furnace
has no get_recipe() but previous_recipe = {name = LuaRecipePrototype, quality}.
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter

from draftsman.data import entities

from tools import rates
from validator.rcon import Rcon

SNAPSHOT = r"""/sc local out = {tick = game.tick, machines = {}}
for _, s in pairs(game.surfaces) do
  for _, e in pairs(s.find_entities_filtered{type = {"assembling-machine", "furnace"}, force = "player"}) do
    local r = e.get_recipe()
    if not r and e.type == "furnace" and e.previous_recipe then r = e.previous_recipe.name end
    local inv = e.get_module_inventory()
    local beacons = {}
    for _, b in pairs(e.get_beacons() or {}) do
      local bi = b.get_module_inventory()
      beacons[#beacons + 1] = {name = b.name, modules = bi and bi.get_contents() or {}}
    end
    out.machines[#out.machines + 1] = {unit = e.unit_number, surface = s.name, name = e.name,
      recipe = r and r.name, energy = r and r.energy, x = e.position.x, y = e.position.y,
      speed = e.crafting_speed, productivity = e.productivity_bonus, finished = e.products_finished,
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
AT_RATE = 0.9  # a share at least this is "at rate"
DIAGNOSES = {
    "item_ingredient_shortage": "starved", "fluid_ingredient_shortage": "starved", "no_ingredients": "starved",
    "full_output": "output full: the product isn't taken away fast enough",
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
    return ", ".join(f"{m['count']} {m['name']}" + ("" if m.get("quality", "normal") == "normal" else f" ({m['quality']})")
                     for m in sorted(_list(modules), key=lambda m: m["name"]))


def _model(m: dict, r: rates.Recipe) -> tuple[float, float]:
    """(crafting_speed, productivity) the model gives this machine; ValueError where it can't say."""
    mods, beacons = _list(m["modules"]), _list(m["beacons"])
    if len(mods) > 1 or any(x.get("quality", "normal") != "normal" for x in mods):
        raise ValueError(f"modules {_label(mods)}")
    module = mods[0]["name"] if mods else ""
    if mods and mods[0]["count"] != rates.module_slots(m["name"], module, r):
        raise ValueError(f"modules {_label(mods)} not filling the slots")
    held = {_label(b["modules"]) for b in beacons}
    if beacons and ({b["name"] for b in beacons} != {"beacon"} or len(held) != 1):
        raise ValueError("beacons of different kinds")
    bmods = _list(beacons[0]["modules"]) if beacons else []
    if beacons and (len(bmods) != 1 or bmods[0]["count"] != entities.raw["beacon"]["module_slots"]
                    or bmods[0].get("quality", "normal") != "normal"):
        raise ValueError(f"beacons holding {held.pop()}")
    bmod = bmods[0]["name"] if bmods else ""
    return (rates.crafts_per_second(m["name"], r, module, bmod, len(beacons)) * r.time,
            rates.productivity(m["name"], r, module))


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
        key = (m["surface"], m["name"], m.get("recipe") or "", _label(m["modules"]))
        groups.setdefault(key, []).append(m)
    return [_group(key, ms, before, seconds, samples) for key, ms in sorted(groups.items())]


def _group(key, ms: list[dict], before: dict, seconds: float, samples: list[dict]) -> dict:
    surface, machine, recipe, modules = key
    crafts = sum(m["finished"] - before[m["unit"]]["finished"] for m in ms) / seconds
    # products_finished counts productivity's bonus crafts, so the rate it can reach includes them.
    rated = sum(m["speed"] * (1 + m["productivity"]) / m["energy"] for m in ms if m.get("energy"))
    units = {str(m["unit"]) for m in ms}
    seen = Counter(s for sample in samples for u, s in _statuses(sample).items() if u in units)
    total = sum(seen.values())
    g = {"surface": surface, "machine": machine, "recipe": recipe, "modules": modules, "count": len(ms),
         "crafts_per_second": crafts, "rated_crafts_per_second": rated, "share": crafts / rated if rated else None,
         "statuses": {s: n / total for s, n in seen.most_common()}, "products": {},
         "crafting_speed": sum(m["speed"] for m in ms) / len(ms), "model_crafting_speed": None,
         "mismatch": [], "unmodelled": None}
    try:
        r = rates.recipe_any(recipe) if recipe else None
        if r and machine not in entities.raw:
            raise ValueError(f"unknown machine {machine}")
    except ValueError as e:
        r, g["unmodelled"] = None, str(e)
    if r:
        g["products"] = {p: {"actual": crafts * n, "rated": rated * n} for p, n in r.results.items()}
        _check(g, ms, r)
    g["diagnosis"] = _diagnose(g, seen)
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


def lines(groups: list[dict]) -> list[str]:
    """The plain report: a line per group, then its diagnosis and any model mismatch."""
    out = []
    for g in groups:
        share = f"{g['share']:.0%}" if g["share"] is not None else "-"
        made = "; ".join(f"{p} {v['actual']:.2f}/s of {v['rated']:.2f}/s" for p, v in g["products"].items()) \
            or f"{g['crafts_per_second']:.2f} crafts/s of {g['rated_crafts_per_second']:.2f}"
        statuses = ", ".join(f"{s} {v:.0%}" for s, v in list(g["statuses"].items())[:3])
        modules = f" [{g['modules']}]" if g["modules"] else ""
        out.append(f"{g['count']:>4} {g['machine']} {g['recipe'] or '(none)'}{modules} on {g['surface']}: "
                   f"{made} ({share}); {statuses}")
        out.append(f"     -> {g['diagnosis']}")
        out += [f"     model mismatch: {m}" for m in g["mismatch"]]
        if g["unmodelled"]:
            out.append(f"     unmodelled: {g['unmodelled']}")
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
    print(json.dumps(groups, indent=1) if args.json else "\n".join(lines(groups)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
