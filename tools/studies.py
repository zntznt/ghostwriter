"""Studies for tools.optimise: questions about the generators, answered in game.

Each study takes a running server and returns a JSON-able result, printing a
table as it goes. Its books and values are fixed in code before it runs. A
platform study is its trials as data on one of three runners: flights() for
trials through the optimise cache, descent() for a coordinate descent over
shapes, one_flights() for whole trips flown one flight at a time. The trip
model's pieces and the filed designs live in tools.plan, where bp plan
and the library use them. tests/test_studies.py
holds every study to the trials it ran when NOTES.md recorded it.
"""

import sys
from collections import Counter

from generators.platform import FEED_RESEARCH, PROMETHIUM
from tools.optimise import GameEvaluator, grid
from tools.plan import (FILED_PLATFORM, HIGH_RESEARCH, MIX_RESEARCH, PROMETHIUM_PLATFORM, full_stacks, leg_rates, legs_home, planned_trip,
                        slots, trip_flown, trip_means as whole_trip_means)

SHARES = [0.8, 0.825, 0.85, 0.875, 0.9, 0.925, 0.95, 0.975, 1.0]
# Each book makes one line run at a chosen share of a yellow belt (15/s), with
# lines never split below a full belt (fill=1): the rate per unit share.
BELT_FILL_BOOKS = {
    "copper cable, two rows merged, into circuits": ("electronic-circuit", "copper-cable", 5.0),  # 3 cable per circuit
    "transport belts, the product line": ("transport-belt", "transport-belt", 15.0),
    "gears, the product line": ("iron-gear-wheel", "iron-gear-wheel", 15.0),
}


def belt_fill(server) -> dict:
    """How full can a line the factory makes run and the factory still meet its
    target? factory.MADE_FILL splits lines past a share of a belt; this finds,
    per book, the highest share at which it and every lower one pass."""
    evaluate = GameEvaluator(server, "factory")
    result = {}
    for label, (item, line, per_share) in BELT_FILL_BOOKS.items():
        trials = grid(evaluate, {"rate": [round(per_share * f, 4) for f in SHARES]}, {"item": item, "fill": 1.0, "layout": "bus"})
        rows = []
        for f, t in zip(SHARES, trials):
            target = t.params["rate"]
            rows.append({"share": f, "rate": target, "passed": t.passed, "windows": t.windows,
                         "made": round(t.rates.get(item, 0.0) / target, 4) if target else 0.0,
                         "error": t.error[:300]})
            print(f"{label}: {line} at {f:.1%} of a belt ({target:g} {item}/s): "
                  f"{'pass' if t.passed else 'FAIL'}, made {rows[-1]['made']:.1%} of target"
                  + (f" in window {t.windows}" if t.passed and t.windows > 1 else ""), file=sys.stderr, flush=True)
        highest = None
        for r in rows:
            if not r["passed"]:
                break
            highest = r["share"]
        result[label] = {"item": item, "line": line, "highest_passing_share": highest, "trials": rows}
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return result


# The research most platform studies fly with: platform-research's best (HIGH_RESEARCH), with the hub
# rebuilding what dies (construction-robotics); the trip studies' (MIX_RESEARCH) the same way.
REBUILD = {**HIGH_RESEARCH, "construction-robotics": 1}
TRIP_RESEARCH = {**MIX_RESEARCH, "construction-robotics": 1}
ROW = ("passed", "rates", "counts", "ended", "error")  # a trial's row, after the fields that name it


def flights(server, runs: int, trials, show=(), keys=ROW) -> list[dict]:
    """Each of trials, (fields, params), flown `runs` times through the optimise cache: a row of the
    fields and the trial's keys each, printed as it comes with show's extras (each row -> text)."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=runs)
    rows = []
    for fields, params in trials:
        t = evaluate(params)
        whole = {"passed": t.passed, "rates": t.rates, "counts": t.counts, "ended": t.stalled, "error": t.error}
        rows.append({**fields, **{k: whole[k] for k in keys}})
        print(", ".join(f"{k} {v}" for k, v in fields.items()) + ": "
              + (summary({**whole, **fields}, show) if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return rows


def summary(row: dict, show=()) -> str:
    """A trial in a line: how long it lived and how far, then each of show's extras."""
    r = row["rates"]
    return "; ".join([f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean "
                      f"({r['km_min']:,.0f} to {r['km_max']:,.0f})"] + [s(row) for s in show])


def descent(server, space: dict, start: dict, score: str, fixed: dict | None = None, runs: int = 4):
    """Coordinate descent (tools.optimise.descend) over space from start, each shape flown `runs` times
    with fixed's parameters and scored by the rate named score, moving only on a 5% gain (flights
    differ run to run): the best trial and every one tried, their params the shape alone."""
    from tools.optimise import FlightEvaluator, descend

    evaluate, fixed = FlightEvaluator(server, "platform", runs=runs), fixed or {}

    def logged(params):
        t = evaluate({**params, **fixed})
        t.params = {k: v for k, v in t.params.items() if k not in fixed}
        print(f"{t.params}: " + (summary({"rates": t.rates, "counts": t.counts}, [killed]) if t.passed
                                 else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        return t

    best, trials = descend(logged, space, start, score=lambda t: t.rates[score], margin=0.05)
    print(f"best: {best and best.params}; trials run: {evaluate.ran}", file=sys.stderr)
    return best, trials


def descended(best, trials, keys=ROW) -> dict:
    """A descent's result: the best shape and its rates, and every trial."""
    return {"best": best and best.params, "best_rates": best and best.rates,
            "trials": [{"params": t.params, **{k: {"passed": t.passed, "rates": t.rates, "counts": t.counts,
                                                   "ended": t.stalled, "error": t.error}[k] for k in keys}}
                       for t in trials]}


def killed(row: dict) -> str:
    """Ours lost to asteroids, and what killed the hub."""
    lost, hub = Counter(), {}
    for k, n in row["counts"].items():
        if k.endswith(" asteroid"):  # ours are the deaths an asteroid caused; asteroids' own start with their size
            lost[k.split(" <- ")[0]] += n
        if k.startswith("space-platform-hub <- "):
            hub[k.split(" <- ")[1]] = n
    return f"lost {dict(lost)}; hub killed by {hub or 'nothing'}"


def lost(*names):
    """An extra: how many of these entities asteroids destroyed."""
    return lambda row: f"lost {({k: n for k, n in row['counts'].items() if k.split(' <- ')[0] in names}) or 'nothing'}"


def made(row: dict) -> str:
    r = row["rates"]
    return (f"made {r.get('made thruster-fuel/s', 0):.1f} fuel/s and {r.get('made thruster-oxidizer/s', 0):.1f} "
            f"oxidizer/s, held at the end {r.get('held thruster-fuel', 0):,.0f} and {r.get('held thruster-oxidizer', 0):,.0f}")


PLATFORM_SPACE = {"railguns": [2, 4, 6, 8, 10, 12], "guns": [0, 3, 6, 9, 12], "thrusters": [1, 2, 3, 4]}
PLATFORM_START = {"railguns": 4, "guns": 6, "thrusters": 2}


def platform_shape(server) -> dict:
    """How many railguns, gun turrets and thrusters fly a platform furthest toward the shattered planet
    in 300 s (power, ammo and fuel by script)? Descent from 4/6/2 on mean km, 4 flights a shape."""
    return descended(*descent(server, PLATFORM_SPACE, PLATFORM_START, "km_mean"), keys=("passed", "rates", "error"))


PLATFORM_BEST = {"railguns": 6, "guns": 9, "thrusters": 1}  # platform-shape's best (NOTES.md)
# Each axis is one kind of research, at none and at levels a finished game may
# have. Railgun damage 2 is where one shot kills a huge promethium asteroid:
# (10000 x 1.8 - 3000) x 0.9 = 13500 >= 10000. Bullets are the gun turrets'.
RESEARCH_AXES = {
    "railgun shooting speed": [{}, {"railgun-shooting-speed": 3}, {"railgun-shooting-speed": 6}],
    "railgun damage": [{}, {"railgun-damage": 2}],
    "bullets": [{}, {"physical-projectile-damage": 10, "weapon-shooting-speed": 6}],
}
NO_COUNTS = ("passed", "rates", "ended", "error")


def platform_research(server) -> dict:
    """How much of a platform's survival is research? PLATFORM_BEST flown 4 times for 300 s at every
    combination of the RESEARCH_AXES levels (power, ammo and fuel by script)."""
    import itertools

    combos = [{k: v for levels in combo for k, v in levels.items()} for combo in itertools.product(*RESEARCH_AXES.values())]
    rows = flights(server, 4, [({"research": r}, {**PLATFORM_BEST, "research": r}) for r in combos], keys=NO_COUNTS)
    return {"design": PLATFORM_BEST, "trials": rows}


# HIGH_RESEARCH (platform-research's best row) and MIX_RESEARCH (with rockets and lasers): tools.plan.
PROMETHIUM_STARTS = [0.0, 0.2, 0.3, 0.4]  # huge promethium asteroids are rare before 0.2 (NOTES.md)


def platform_promethium(server) -> dict:
    """Does PLATFORM_BEST live where promethium is? With HIGH_RESEARCH, 4 flights of 300 s from each
    of PROMETHIUM_STARTS (shares of the route; power, ammo and fuel by script)."""
    rows = flights(server, 4, [({"start": s}, {**PLATFORM_BEST, "research": HIGH_RESEARCH, "start": s})
                               for s in PROMETHIUM_STARTS], keys=NO_COUNTS)
    return {"design": PLATFORM_BEST, "research": HIGH_RESEARCH, "trials": rows}


BELT_START = 0.4  # where huge promethium asteroids are common (NOTES.md)
BELT_SPACE = {"railguns": [2, 4, 6, 8, 10, 12, 16, 20], "guns": [0, 3, 6, 9, 12, 16, 20, 24], "thrusters": [1, 2, 3]}
# platform-belt's best (20, 24, 1) was its largest shape: platform-wide goes on from there. Its
# flights are 600 s: at 300 s, 28/28 and 32/28 survived and the capped mean couldn't tell them apart.
WIDE_SPACE = {"railguns": [16, 20, 24, 28, 32, 40, 48], "guns": [20, 24, 28, 32, 40, 48], "thrusters": [1, 2]}
WIDE_START = {"railguns": 20, "guns": 24, "thrusters": 1}


def platform_belt(server, space=BELT_SPACE, start=PLATFORM_BEST, seconds=300) -> dict:
    """Which railgun, gun turret and thruster counts live longest where promethium is? Descent over
    space from start on mean seconds lived, 4 flights a shape from BELT_START with HIGH_RESEARCH for up
    to seconds, counting what killed what."""
    found = descent(server, space, start, "seconds_mean", {"research": HIGH_RESEARCH, "start": BELT_START,
                                                            "seconds": seconds})
    return {"start": BELT_START, "research": HIGH_RESEARCH, **descended(*found)}


FRONT_SHAPES = [(8, 12), (12, 16), (16, 20), (20, 24)]  # railguns, gun turrets: platform-belt's path


def friendly(row: dict) -> str:
    """Railguns and gun turrets lost, and gun turrets a railgun killed (they stood in its line)."""
    c = row["counts"]
    fire = {k: n for k, n in c.items() if k.endswith("<- railgun-turret") and not k.split(" <- ")[0].endswith("asteroid")}
    return (f"railguns lost {sum(n for k, n in c.items() if k.startswith('railgun-turret <-'))}, gun turrets lost "
            f"{sum(n for k, n in c.items() if k.startswith('gun-turret <-'))}; friendly fire {fire or 'none'}")


def platform_front(server) -> dict:
    """Do gun turrets do better in the railguns' row than behind it? Each of FRONT_SHAPES (1 thruster)
    in both layouts, 4 flights of up to 300 s from BELT_START with HIGH_RESEARCH."""
    trials = [({"railguns": rg, "guns": g, "layout": layout},
               {"railguns": rg, "guns": g, "thrusters": 1, "layout": layout, "research": HIGH_RESEARCH, "start": BELT_START})
              for rg, g in FRONT_SHAPES for layout in ("rows", "front")]
    return {"start": BELT_START, "research": HIGH_RESEARCH, "trials": flights(server, 4, trials, [friendly])}


REPAIR_SHAPES = [{"railguns": 32, "guns": 32, "thrusters": 1}, {"railguns": 20, "guns": 24, "thrusters": 1}]
REPAIR_PACKS = {"repair-pack": 500}
# Spares for the hub to rebuild from; with REPAIR_PACKS they fill about 55 of its 59 slots.
SPARES = {"railgun-turret": 50, "gun-turret": 100, "medium-electric-pole": 100, "space-platform-foundation": 4000}
# Asteroids destroy foundation too, and the 4000 ran out in every flight while most
# spare turrets and poles were left: these fill 58 slots with what the hub used.
FOUNDATION_FIRST = {"railgun-turret": 20, "medium-electric-pole": 50, "space-platform-foundation": 5400}
REPAIR_MODES = {
    "nothing": ({}, {}),
    "repair": ({}, REPAIR_PACKS),
    "rebuild": ({"construction-robotics": 1}, SPARES),
    "both": ({"construction-robotics": 1}, {**SPARES, **REPAIR_PACKS}),
    "rebuild, foundation first": ({"construction-robotics": 1}, FOUNDATION_FIRST),
}


def platform_repair(server) -> dict:
    """Does keeping the platform up change how long it lives where promethium is? Each of REPAIR_SHAPES,
    4 flights of up to 600 s from BELT_START with HIGH_RESEARCH, in each of REPAIR_MODES: nothing, repair
    packs in the hub (no research needed), spares with construction-robotics (the hub rebuilds), both."""
    trials = [({**shape, "mode": mode}, {**shape, "research": {**HIGH_RESEARCH, **research}, "start": BELT_START,
                                          "seconds": 600, "supplies": supplies})
              for shape in REPAIR_SHAPES for mode, (research, supplies) in REPAIR_MODES.items()]
    return {"start": BELT_START, "research": HIGH_RESEARCH,
            "modes": {k: {"research": r, "supplies": s} for k, (r, s) in REPAIR_MODES.items()},
            "trials": flights(server, 4, trials, [killed])}


# Rebuilding ran out of foundation first in platform-repair. Each cargo bay adds 20
# hub slots: 8 of them hold the same spares, 2 spare bays and 20,800 foundation (the
# hub has 10 of its own). Asteroids eat foundation from the side ends inward, so a
# margin of spare columns there goes first.
BAY_SPARES = {**SPARES, "cargo-bay": 2, "space-platform-foundation": 20800}
FOUNDATION_MODES = {
    "rebuild": ({}, SPARES),
    "cargo bays": ({"cargo_bays": 8}, BAY_SPARES),
    # The bays' foundation slows the platform (weight), and slower platforms live longer:
    # the bays alone, and the bays with ballast the hub never builds with, weighing what
    # the extra foundation does (16,800 x 20,000 = 336 nuclear reactors x 1,000,000).
    "cargo bays, spares only": ({"cargo_bays": 8}, SPARES),
    "cargo bays, ballast": ({"cargo_bays": 8}, {**SPARES, "cargo-bay": 2, "nuclear-reactor": 336}),
    "margin": ({"margin": 16}, SPARES),
    "both": ({"cargo_bays": 8, "margin": 16}, BAY_SPARES),
}


def platform_foundation(server) -> dict:
    """Does more foundation help a platform that rebuilds? Each of REPAIR_SHAPES in each of
    FOUNDATION_MODES, 8 flights of up to 600 s from BELT_START with REBUILD's research. 8 flights, not
    4: at 4, the cargo bays' 4 of 4 survivors stood against 2 of the same flights in a probe, both lost."""
    trials = [({**shape, "mode": mode}, {**shape, **extra, "research": REBUILD, "start": BELT_START, "seconds": 600,
                                          "supplies": supplies})
              for shape in REPAIR_SHAPES for mode, (extra, supplies) in FOUNDATION_MODES.items()]
    return {"start": BELT_START, "research": HIGH_RESEARCH,
            "modes": {k: {"shape": e, "supplies": s} for k, (e, s) in FOUNDATION_MODES.items()},
            "trials": flights(server, 8, trials)}


BUDGET_STARTS = [0.0, BELT_START]  # where the route begins, and where promethium is
MODULE = ("asteroid-collector", "crusher", "chemical-plant", "transport-belt")  # the fuel module's parts


def use(row: dict) -> str:
    """What a flight used a second, as the script supplied it (power, fuel and oxidizer, ammo)."""
    return f"a second: {({k: round(v, 3) for k, v in row['rates'].items() if k.endswith(('/s', 'MW', ' lived'))})}"


def platform_budget(server) -> dict:
    """What does the filed platform use a second, to make on board instead? 8 flights of up to 600 s
    from each of BUDGET_STARTS with platform-foundation's winning stock (BAY_SPARES) and research;
    the flight counts what the script supplies and what the hub rebuilds."""
    trials = [({"start": s}, {**FILED_PLATFORM, "research": REBUILD, "start": s, "seconds": 600, "supplies": BAY_SPARES})
              for s in BUDGET_STARTS]
    return {"design": FILED_PLATFORM, "research": REBUILD, "supplies": BAY_SPARES,
            "trials": flights(server, 8, trials, [use])}


def platform_fuel(server) -> dict:
    """Does the filed platform fly on fuel it makes itself (fuel=True), and what does that cost it?
    The filed one with fuel by script and the fuelled one with none after its start (primed, moving
    at CRUISE), 8 flights of up to 600 s from each of BUDGET_STARTS, as platform-budget flies."""
    trials = [({"start": s, "fuel": fuel}, {**FILED_PLATFORM, **({"fuel": True} if fuel else {}), "start": s,
                                             "seconds": 600, "research": REBUILD, "supplies": BAY_SPARES})
              for s in BUDGET_STARTS for fuel in (False, True)]
    return {"design": FILED_PLATFORM, "research": REBUILD, "supplies": BAY_SPARES,
            "trials": flights(server, 8, trials, [made, lost(*MODULE)])}


# The fuel module's own parts, for the hub to rebuild (platform-fuel lost belts, collectors and
# plants with none in stock), in 9 slots 1,000 foundation gave up: the hub's 219 (59 + 8 bays) stay full.
MODULE_SPARES = {"transport-belt": 50, "inserter": 50, "fast-inserter": 20, "asteroid-collector": 10, "crusher": 10,
                 "chemical-plant": 10, "pipe": 50, "pipe-to-ground": 20, "storage-tank": 4}
FUEL_SPARES = {**BAY_SPARES, **MODULE_SPARES, "space-platform-foundation": 19800}
RESERVE_MODES = {  # mode -> (build and flight parameters, the hub's stock)
    "fuel by script": ({}, BAY_SPARES),
    "made on board": ({"fuel": True}, BAY_SPARES),
    "made on board, reserve": ({"fuel": True, "reserve": True}, BAY_SPARES),
    "made on board, reserve, module spares": ({"fuel": True, "reserve": True}, FUEL_SPARES),
}
# What the fuelled layout costs, on fuel by script: with and without the module's spares, and
# the plain layout as wide (fuel=True adds FUEL_SLOT + spacing = 10 columns, 5 a side).
MODULE_CONTROLS = {
    "fuelled layout, fuel by script": ({"fuel": True, "script_fuel": True}, BAY_SPARES),
    "fuelled layout, fuel by script, module spares": ({"fuel": True, "script_fuel": True}, FUEL_SPARES),
    "plain, as wide (margin 5)": ({"margin": 5}, BAY_SPARES),
}


def platform_reserve(server) -> dict:
    """Does a fuel reserve filled before leaving (reserve=True) get the fuelled platform going from the
    solar system edge, and do the module's spares keep it alive? Each of RESERVE_MODES from each of
    BUDGET_STARTS, and each of MODULE_CONTROLS from BELT_START, 8 flights of up to 600 s."""
    runs = [(s, mode, how) for s in BUDGET_STARTS for mode, how in RESERVE_MODES.items()]
    runs += [(BELT_START, mode, how) for mode, how in MODULE_CONTROLS.items()]
    trials = [({"start": s, "mode": mode}, {**FILED_PLATFORM, **extra, "start": s, "seconds": 600, "research": REBUILD,
                                             "supplies": supplies}) for s, mode, (extra, supplies) in runs]
    return {"design": FILED_PLATFORM, "research": REBUILD,
            "modes": {k: {"extra": e, "supplies": s} for k, (e, s) in {**RESERVE_MODES, **MODULE_CONTROLS}.items()},
            "trials": flights(server, 8, trials, [made, lost(*MODULE, "storage-tank")])}


POWER_MODES = {  # mode -> build and flight parameters; the hub's stock is BAY_SPARES (and cells with power)
    "stand-in power": {"fuel": True, "reserve": True},
    "1 fusion reactor": {"fuel": True, "reserve": True, "power": 1},
    "2 fusion reactors": {"fuel": True, "reserve": True, "power": 2},
}
PLANT = ("fusion-reactor", "fusion-generator", "cryogenic-plant", "accumulator")


def power(row: dict) -> str:
    """The power a flight used and its plants made, fusion cells an hour, and seconds a reactor was short."""
    r = row["rates"]
    return f"uses {r.get('power MW', 0):.1f} MW" + (
        f", makes {r['generated MW']:.1f} MW gross on {r['fusion cells/h']:.2f} cells/h, reactors short of power "
        f"{r['brownout s']:.1f} s a flight" if "generated MW" in r else "")


def platform_power(server) -> dict:
    """Does the fuelled platform with its reserve run on its own fusion plants (power=N) as on the
    stand-in source? Each of POWER_MODES, 8 flights of up to 600 s from each of BUDGET_STARTS."""
    trials = [({"start": s, "mode": mode}, {**FILED_PLATFORM, **extra, "start": s, "seconds": 600, "research": REBUILD,
                                             "supplies": BAY_SPARES})
              for s in BUDGET_STARTS for mode, extra in POWER_MODES.items()]
    return {"design": FILED_PLATFORM, "research": REBUILD, "supplies": BAY_SPARES, "modes": POWER_MODES,
            "trials": flights(server, 8, trials, [power, lost(*PLANT)])}


# What the hub rebuilds a fusion plant from: 9 of its 219 slots with 8 bays (a reactor
# stacks to 1, a generator to 5), which the stock is full without, so 1,000 foundation
# make room. The cell belt passes under a reactor on an express underground belt.
PLANT_SPARES = {"fusion-reactor": 2, "fusion-generator": 4, "cryogenic-plant": 2, "accumulator": 1, "pipe": 50,
                "inserter": 10, "transport-belt": 20, "express-underground-belt": 4}
PLANT_MODES = {  # mode -> the hub's stock, for the fuelled platform with its reserve and power=2
    "no plant spares": BAY_SPARES,
    "plant spares": {**BAY_SPARES, **PLANT_SPARES, "space-platform-foundation": 19800},
}
# The whole platform so far: its own fuel (with the reserve) and its own power, and the
# hub's stock with plant spares. Only ammo is still supplied by script.
FULL_PLATFORM = {**FILED_PLATFORM, "fuel": True, "reserve": True, "power": 2}
FULL_STOCK = PLANT_MODES["plant spares"]


def platform_plant(server) -> dict:
    """Does the hub keep the fusion plants going where promethium is when it holds their parts?
    FULL_PLATFORM with each of PLANT_MODES' stock, 8 flights of up to 600 s from BELT_START."""
    trials = [({"mode": mode}, {**FULL_PLATFORM, "start": BELT_START, "seconds": 600, "research": REBUILD,
                                "supplies": supplies}) for mode, supplies in PLANT_MODES.items()]
    return {"design": FULL_PLATFORM, "start": BELT_START, "research": REBUILD, "modes": PLANT_MODES,
            "trials": flights(server, 8, trials, [power, lost(*PLANT)])}


# Uranium rounds can't be made in space (no uranium); piercing and firearm rounds can.
MAGAZINES = ["uranium-rounds-magazine", "piercing-rounds-magazine", "firearm-magazine"]


def per_1000_km(rates: dict) -> str:
    """The supplies and rebuilds a flight used per 1,000 km flown, as one line."""
    names = {"rebuilt space-platform-foundation/1000 km": "foundation",
             "railgun-ammo/1000 km": "railgun ammo"} | {f"{m}/1000 km": "magazines" for m in MAGAZINES}
    return ", ".join(f"{label} {rates[k]:,.1f}" for k, label in names.items() if k in rates)


def per_km(row: dict) -> str:
    return f"per 1,000 km: {per_1000_km(row['rates'])}"


def platform_ammo(server) -> dict:
    """What can the gun turrets fire that the platform could make? FULL_PLATFORM with FULL_STOCK, its
    gun turrets stocked with each of MAGAZINES, 8 flights of up to 600 s from each of BUDGET_STARTS."""
    trials = [({"start": s, "magazine": m}, {**FULL_PLATFORM, "start": s, "seconds": 600, "magazine": m,
                                             "research": REBUILD, "supplies": FULL_STOCK})
              for s in BUDGET_STARTS for m in MAGAZINES]
    return {"design": FULL_PLATFORM, "research": REBUILD, "supplies": FULL_STOCK,
            "trials": flights(server, 8, trials, [per_km])}


SIDE_MODES = {"no side guns": {}, "5 side guns a side": {"side_guns": 5}}  # 5: as many as the ends hold


def platform_sides(server) -> dict:
    """Do gun turrets at the side ends, where asteroids eat foundation first, save what the hub
    rebuilds? FULL_PLATFORM with each of SIDE_MODES, 8 flights of up to 600 s from BELT_START."""
    trials = [({"mode": mode}, {**FULL_PLATFORM, **extra, "start": BELT_START, "seconds": 600, "research": REBUILD,
                                "supplies": FULL_STOCK}) for mode, extra in SIDE_MODES.items()]
    return {"design": FULL_PLATFORM, "start": BELT_START, "modes": SIDE_MODES, "research": REBUILD,
            "supplies": FULL_STOCK, "trials": flights(server, 8, trials, [per_km])}


# The trip model's inputs (tools.trip): the whole platform, keeping promethium, along the
# route. The script empties the chunks from the hub each second and counts them, so room
# doesn't cap the count; FULL_STOCK fills the hub, so 500 foundation make room for them to
# arrive. Flown back over the stretch where promethium is, and without promethium, for what
# sharing the collectors with it costs the fuel.
ROUTE_STARTS = [0.0, 0.1, 0.2, 0.3, 0.4]
TRIP_PLATFORM = {**FULL_PLATFORM, "promethium": True}
TRIP_STOCK = {**FULL_STOCK, "space-platform-foundation": FULL_STOCK["space-platform-foundation"] - 500}
ROUTE_RUNS = [(start, "out", {}) for start in ROUTE_STARTS] + [
    (BELT_START, "back", {"reverse": True}), (BELT_START, "out, no promethium", {"promethium": False})]


def hub(row: dict) -> str:
    """Hub slots a trial filled per 1,000 km (tools.trip), most first, and the promethium brought in."""
    from tools import trip

    used = trip.slots(trip.table({"trials": [row]}, row["mode"])[row["start"]])
    top = ", ".join(f"{k} {v:.2f}" for k, v in sorted(used.items(), key=lambda kv: -kv[1])[:5])
    return (f"cruise {row['rates'].get('cruise km/s', 0):.1f} km/s; hub slots per 1,000 km {sum(used.values()):.2f} "
            f"({top}); promethium {row['rates'].get(f'taken {PROMETHIUM}/1000 km', 0):.2f}")


def platform_route(server) -> dict:
    """What does the whole platform use, and collect, along the route? TRIP_PLATFORM with TRIP_STOCK,
    8 flights of up to 600 s from each start of ROUTE_RUNS: per 1,000 km what it used and rebuilt and
    the promethium chunks it brought in."""
    trials = [({"start": s, "mode": mode}, {**TRIP_PLATFORM, **extra, "start": s, "seconds": 600, "supplies": TRIP_STOCK,
                                             "research": REBUILD,
                                             **({"take": [PROMETHIUM]} if extra.get("promethium", True) else {})})
              for s, mode, extra in ROUTE_RUNS]
    return {"design": TRIP_PLATFORM, "research": REBUILD, "supplies": TRIP_STOCK,
            "trials": flights(server, 8, trials, [per_km, made])}


# What the platform fills the hub with per 1,000 km comes from how it fights (tools.trip):
# rocket turrets for the big asteroids (railguns for the huge), and narrower shapes, against
# the whole platform, from where the route begins and from where a trip would reach. Lasers
# hurt only small asteroids and cost more power than they save (NOTES.md): not flown.
MIX_STARTS = [0.0, 0.2]
MIX_MODES = {"baseline": {}, "16 rocket turrets": {"rockets": 16},
             "narrow, 24 railguns and 24 gun turrets": {"railguns": 24, "guns": 24},
             "narrow, 16 railguns and 16 gun turrets": {"railguns": 16, "guns": 16},
             "narrow, 12 railguns and 12 gun turrets": {"railguns": 12, "guns": 12},
             "narrow, 8 railguns and 8 gun turrets": {"railguns": 8, "guns": 8}}
MIX_STOCK = {**TRIP_STOCK, "rocket-turret": 10, "space-platform-foundation": TRIP_STOCK["space-platform-foundation"] - 100}


def platform_mix(server) -> dict:
    """Which turret mix and shape fill the fewest hub slots per 1,000 km? TRIP_PLATFORM in each of
    MIX_MODES with MIX_STOCK and TRIP_RESEARCH, 8 flights of up to 600 s from each of MIX_STARTS. Rows
    are named by mode, so `python -m tools.trip RESULT --mode M` plans a trip from one."""
    trials = [({"start": s, "mode": mode}, {**TRIP_PLATFORM, **extra, "start": s, "seconds": 600, "supplies": MIX_STOCK,
                                             "research": TRIP_RESEARCH, "take": [PROMETHIUM]})
              for mode, extra in MIX_MODES.items() for s in MIX_STARTS]
    return {"design": TRIP_PLATFORM, "modes": MIX_MODES, "research": TRIP_RESEARCH, "supplies": MIX_STOCK,
            "trials": flights(server, 8, trials, [hub])}


# platform-mix's 16-railgun platform with more cargo bays: up to 24 fit inside its width
# (NOTES.md). The trip model says the bays' slots go mostly to railgun ammo, which binds
# first, so the extra ones hold it; the turrets still get theirs by script. Measured from
# 0.1 too, where platform-mix interpolated between the edge and 0.2.
NARROW = {**TRIP_PLATFORM, "railguns": 16, "guns": 16}
BAY_COUNTS = [8, 16, 24]
BAY_STARTS = [0.0, 0.1, 0.2]
BAYS_PLATFORM = {**NARROW, "cargo_bays": 24}  # platform-bays' best trip that comes back (NOTES.md)
# Bays past 8 push the fuel module east toward the side end, where asteroids eat inward; with
# none of its parts in the hub it went unrebuilt, and fuel and promethium collapsed (NOTES.md).
# Enough for the worst 600 s measured, in 14 slots, for as much foundation.
BAY_MODULE_SPARES = {"transport-belt": 200, "inserter": 100, "fast-inserter": 50, "asteroid-collector": 20,
                     "crusher": 20, "chemical-plant": 20, "pipe": 100, "pipe-to-ground": 50, "storage-tank": 4}


def bay_stock(bays: int, module_spares: bool = True) -> dict:
    """MIX_STOCK for 8 bays, each bay past 8 full of railgun ammo (20 slots of 10), and the fuel
    module's spares in place of foundation, so every count leaves the same slots free."""
    extra = 200 * (bays - 8)  # the hub refuses an insert of none
    stock = {**MIX_STOCK, **({"railgun-ammo": MIX_STOCK.get("railgun-ammo", 0) + extra} if extra > 0 else {})}
    if module_spares:
        before = slots(stock)
        stock.update(BAY_MODULE_SPARES)
        stock["space-platform-foundation"] -= 100 * (slots(stock) - before)
    return stock


def bays_lost(row: dict) -> str:
    return "bays lost per 1,000 km " + (", ".join(
        f"|x| {k.split('|x| ')[1].split('/')[0]}: {v:.2f}" for k, v in sorted(row["rates"].items())
        if k.startswith("cargo bays lost at") and k.endswith("/1000 km")) or "none")


def platform_bays(server) -> dict:
    """How many cargo bays should the 16-railgun platform carry? NARROW with each of BAY_COUNTS (with
    and without the module's spares), bay_stock and TRIP_RESEARCH, 8 flights of up to 600 s from each of
    BAY_STARTS. Rows are named "N bays", so `python -m tools.trip RESULT --mode "N bays" --bays N` plans."""
    trials = [({"start": s, "mode": f"{bays} bays" + ("" if spares else ", no module spares")},
               {**NARROW, "cargo_bays": bays, "start": s, "seconds": 600, "supplies": bay_stock(bays, spares),
                "research": TRIP_RESEARCH, "take": [PROMETHIUM]})
              for bays in BAY_COUNTS for spares in (True, False) for s in BAY_STARTS]
    return {"design": NARROW, "bays": BAY_COUNTS, "research": TRIP_RESEARCH,
            "supplies": {b: bay_stock(b) for b in BAY_COUNTS}, "module spares": BAY_MODULE_SPARES,
            "trials": flights(server, 8, trials, [hub, bays_lost])}


# The promethium platform's hub feeds its own turrets (ammo_feed), with no ammo by script,
# against the same platform on script ammo, from where the trip goes (NOTES.md). Both fly
# the same stock: bay_stock(24) with magazines for 600 s 0.1 out (about 5.7 a second) and
# spares for the feed's underground belts, in place of as much foundation.
FEED_STARTS = [0.0, 0.1]
FEED_MODES = {"script ammo": {}, "hub feeds": {"ammo_feed": True}}


def feed_stock() -> dict:
    base = bay_stock(24)
    stock = {**base, "uranium-rounds-magazine": 4000, "underground-belt": 10}
    stock["space-platform-foundation"] -= 100 * (slots(stock) - slots(base))
    return stock


def fast_feed_stock() -> dict:
    """feed_stock with magazines for 600 s 0.2 out (about 7.5 a second, and what the belts and
    turrets hold), in place of as much foundation: both stack to 100."""
    stock = feed_stock()
    stock["uranium-rounds-magazine"] += 2500
    stock["space-platform-foundation"] -= 2500
    return stock


def fed(row: dict) -> str:
    """Ammo used a second, and turret-seconds with none."""
    r = row["rates"]
    return (f"ammo used a second {({k[:-2]: round(v, 2) for k, v in r.items() if k in ('railgun-ammo/s', 'uranium-rounds-magazine/s')})}"
            f"; turrets dry {r.get('dry turret s', 0):.0f} turret-s a flight")


def platform_feed(server, modes=FEED_MODES, starts=FEED_STARTS, stock=None) -> dict:
    """Does the hub keep the turrets fed? BAYS_PLATFORM in each of modes (with a mode's own "research"
    besides TRIP_RESEARCH) with stock (feed_stock), 8 flights of up to 600 s from each of starts. Rows are
    named by mode, so `python -m tools.trip RESULT --mode M --bays 24` plans its trip."""
    stock = stock or feed_stock()
    researched = {mode: {**TRIP_RESEARCH, **extra.get("research", {})} for mode, extra in modes.items()}
    trials = [({"start": s, "mode": mode}, {**BAYS_PLATFORM, **{k: v for k, v in extra.items() if k != "research"},
                                             "start": s, "seconds": 600, "supplies": stock,
                                             "research": researched[mode], "take": [PROMETHIUM]})
              for mode, extra in modes.items() for s in starts]
    return {"design": BAYS_PLATFORM, "modes": modes, "research": researched, "supplies": stock,
            "trials": flights(server, 8, trials, [fed, hub])}


# platform-feed's hub inserters bound its feed 0.1 out: inserter capacity research
# (FEED_RESEARCH) speeds them up 2.3 times (NOTES.md). The fed platform with and without
# it, against script ammo with the same research, out to 0.2 with magazines to match.
FAST_FEED_MODES = {"script ammo": {"research": FEED_RESEARCH}, "hub feeds": {"ammo_feed": True},
                   "hub feeds, inserter capacity 7": {"ammo_feed": True, "research": FEED_RESEARCH}}
FAST_FEED_STARTS = [0.0, 0.1, 0.2]


# The best 24-bay trip platform-feed-fast's rates plan (NOTES.md): out to 0.019, a 10,000 km
# stay, and back, 162,000 km. A platform can't hold still on a connection (unthrusted it drifts
# back), so the whole trip flies the stay as 5,000 km further out and as many back: the same
# km, but at 0.019 to 0.02025 of the route rather than at 0.019.
WHOLE_TRIP_TURN = 0.019 + 10_000 / 2 / 4_000_000
# Its load as the trip model plans it, 530 of the hub's 539 slots (NOTES.md); the feed fills
# its belts and turrets from the hub in orbit besides (Flight.fill).
WHOLE_TRIP_STOCK = {"uranium-rounds-magazine": 5450, "railgun-ammo": 4305, "space-platform-foundation": 2329,
                    "transport-belt": 82, "pipe": 71, "inserter": 30, "fast-inserter": 17, "chemical-plant": 16,
                    "crusher": 12, "pipe-to-ground": 11, "medium-electric-pole": 9, "railgun-turret": 6,
                    "asteroid-collector": 5, "cargo-bay": 4, "fusion-generator": 3, "cryogenic-plant": 2,
                    "underground-belt": 2, "gun-turret": 2, "fusion-power-cell": 2, "fusion-reactor": 1,
                    "express-underground-belt": 1}
# The same trip's load as the trip model plans it with platform-whole-trip folded in (tools.trip
# --whole-trip): the whole trip used half the foundation and fewer spares than the model said
# near the edge, and a little more ammo (NOTES.md). The same trip stays best. 537 of 539 slots.
CORRECTED_TRIP_STOCK = {"uranium-rounds-magazine": 5672, "railgun-ammo": 4450, "space-platform-foundation": 1534,
                        "pipe": 53, "transport-belt": 44, "inserter": 17, "chemical-plant": 11, "fast-inserter": 10,
                        "crusher": 7, "pipe-to-ground": 7, "medium-electric-pole": 5, "railgun-turret": 5,
                        "cargo-bay": 3, "asteroid-collector": 3, "fusion-power-cell": 2, "fusion-generator": 2,
                        "cryogenic-plant": 2, "underground-belt": 1, "gun-turret": 1, "fusion-reactor": 1,
                        "express-underground-belt": 1}


# Lost whole trips lost 9 to 21 railguns, against 0 to 6 in the ones that came back, most to huge
# asteroids, and then 14 to 25 cargo bays to the huge asteroids that got through (NOTES.md): the
# 6 spare railguns, 4 bays and 1 fusion reactor ran out. The loads platform-whole-trip-cascade
# tries, cheapest first, with their hub slots (of 539).
CASCADE_STOCKS = {
    "NOTES stock": WHOLE_TRIP_STOCK,  # 530
    "full stacks": full_stacks(WHOLE_TRIP_STOCK),  # 530: 10 railguns, 10 bays, 20 crushers, 5 generators
    "full stacks, 20 railguns, 20 bays, 2 reactors": {**full_stacks(WHOLE_TRIP_STOCK), "railgun-turret": 20,
                                                      "cargo-bay": 20, "fusion-reactor": 2},  # 533
    "full stacks, 30 railguns, 30 bays, 2 reactors, 10 generators": {
        **full_stacks(WHOLE_TRIP_STOCK), "railgun-turret": 30, "cargo-bay": 30, "fusion-reactor": 2,
        "fusion-generator": 10},  # 536
}
# The cascade comes rarely (3 of 24 trips on the NOTES stock), so 8 trips a load can't tell loads
# apart: platform-whole-trip-shock opens the gap itself, as the trip turns where asteroids are
# thickest, SHOCK railguns at once, just under the 9 to 21 lost trips lost.
SHOCK = 8
SHOCK_STOCKS = {k: CASCADE_STOCKS[k] for k in list(CASCADE_STOCKS)[:3]}
WHOLE_TRIP_RESEARCH = {**MIX_RESEARCH, "construction-robotics": 1, **FEED_RESEARCH}
WHOLE_TRIP_SECONDS = 9000  # at most: the plan's 162,000 km at its 50 km/s take 3,240
WHOLE_TRIP_RATES = ("platform-feed-fast.result.json", "hub feeds, inserter capacity 7")  # what the plan came from


def one_flights(server, design: dict, runs: int, row, line, where=None, **flight) -> list[dict]:
    """`design` flown `runs` times, one flight each and uncached (validator.flight.run_flight), set up by
    platform.flight_tests(**design, **flight) and moved to `where` (origin, destination) if given:
    row(flown) each, printed as it comes by line(n, row)."""
    import dataclasses
    import warnings

    from generators import platform
    from tools.plan import build
    from validator.flight import run_flight
    from validator.validate import Report

    data = build("platform", design)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        [setup] = platform.flight_tests(**design, **flight)
    if where:
        setup = dataclasses.replace(setup, origin=where[0], destination=where[1])
    rows = []
    for n in range(runs):
        flown = run_flight(server, data, setup, Report(server.version))
        if flown.error:
            raise RuntimeError(flown.error)
        rows.append(row(flown))
        print(line(n + 1, rows[-1]), file=sys.stderr, flush=True)
    return rows


def whole_trip_flown(flown, stock: dict) -> dict:
    """One whole-trip flight in numbers (tools.plan.trip_flown, turning at WHOLE_TRIP_TURN)."""
    return trip_flown(flown, stock, WHOLE_TRIP_TURN)


def first_out(row: dict) -> str:
    out = row["first out"]
    return f"first out {out['item']} at {out['km']:,.0f} km" if out else "first out nothing"


def trip_line(n: int, row: dict) -> str:
    return (f"flight {n}: {row['ended']} after {row['seconds']:,.0f} s and {row['km']:,.0f} km"
            + (f" (died at {row['died at']:.4f} of the route)" if row["died at"] is not None else "")
            + f"; {row['chunks aboard']} chunks aboard ({row['chunks out']} out, {row['chunks on the way back']} back); "
            f"{first_out(row)}; dry {row['dry turret s']} turret-s, low fuel {row['low fuel s']} s, brownout "
            f"{row['brownout s']} s; out {row['out km/s'] or 0:.1f} km/s, back {row['back km/s'] or 0:.1f} km/s")


def trip_rates(fold: str = "") -> dict | None:
    """The rates the trip plan came from (WHOLE_TRIP_RATES), with the whole trip of `fold` (a result
    file, tools.trip.measured) folded in if it's cached; None if the rates aren't."""
    import json

    from tools import trip
    from tools.optimise import CACHE

    if not (CACHE / WHOLE_TRIP_RATES[0]).exists():
        return None
    rates = trip.table(json.loads((CACHE / WHOLE_TRIP_RATES[0]).read_text()), WHOLE_TRIP_RATES[1])
    if fold and (CACHE / fold).exists():
        share, row = trip.measured(json.loads((CACHE / fold).read_text()))
        rates[share] = row
    return rates


def platform_whole_trip(server, runs: int = 8, stock=None, fold: str = "", shock: int = 0) -> dict:
    """Does the planned trip hold when flown in one go? PROMETHIUM_PLATFORM, readied in Nauvis orbit
    (Flight.fill) with stock (WHOLE_TRIP_STOCK), flies from the solar system edge out to WHOLE_TRIP_TURN
    and back, `runs` times, nothing topped up and every chunk kept, with `shock` railguns killed as it
    turns: per flight as trip_line has it, the means, and the trip model's prediction for the same trip
    from the rates it was planned on (trip_rates, with the whole trip of `fold` folded in if given)."""
    import json

    from tools import trip
    from tools.plan import capacity

    stock = stock or WHOLE_TRIP_STOCK
    rows = one_flights(server, PROMETHIUM_PLATFORM, runs, lambda f: whole_trip_flown(f, stock), trip_line, start=0.0,
                       turn=WHOLE_TRIP_TURN, fill=True, seconds=WHOLE_TRIP_SECONDS, supplies=stock,
                       research=WHOLE_TRIP_RESEARCH, shock=shock)
    means = whole_trip_means(rows)
    print(f"means: {json.dumps(means)}", file=sys.stderr)
    model, rates = None, trip_rates(fold)
    if rates is not None:
        room = capacity(PROMETHIUM_PLATFORM)
        planned = trip.trip(rates, 0.019, room, stock=stock, stay=10_000)
        same = trip.trip(rates, WHOLE_TRIP_TURN, room, stock=stock)
        model = {name: {"chunks": p["chunks"], "comes back": p["comes back"], "km": p["km"], "hours": p["hours"],
                        "ran out": p["ran out"], "left": {k: stock[k] - p["used"].get(k, 0) for k in stock}}
                 for name, p in (("planned", planned), ("same path", same))}
        print(f"model: {json.dumps(model)}", file=sys.stderr)
    return {"design": PROMETHIUM_PLATFORM, "turn": WHOLE_TRIP_TURN, "supplies": stock,
            "research": WHOLE_TRIP_RESEARCH, "flights": rows, "means": means, "model": model}


def platform_whole_trip_cascade(server, runs: int = 8, stocks=None, shock: int = 0) -> dict:
    """Do more spares stop the cascade that loses whole trips? Each of `stocks` (CASCADE_STOCKS)
    flown as platform-whole-trip, `runs` times in the same run, with `shock` railguns killed as it
    turns: how many came back, the chunks per trip that came back and per trip counting losses, and
    the bays and railguns that died."""
    out = {}
    for name, stock in (stocks or CASCADE_STOCKS).items():
        print(f"== {name}", file=sys.stderr, flush=True)
        r = platform_whole_trip(server, runs, stock=stock, shock=shock)
        m = r["means"]
        print(f"{name}: back {sum(f['back'] for f in r['flights'])}/{runs}, {m['chunks per trip back'] or 0:.0f} chunks "
              f"a trip back, {m['chunks back']:.0f} a trip counting losses; {m['cargo bays died']:.1f} bays and "
              f"{m['railgun turrets died']:.1f} railguns died, {m['killed by huge asteroids']:.1f} of ours to huge "
              "asteroids, a flight", file=sys.stderr, flush=True)
        out[name] = r
    return {"stocks": out}


# Getting there (NOTES.md): Nauvis to the solar system edge is 145,000 km by Fulgora and Aquilo
# (tools.plan's HOME and VIA), and the chunks go home to Nauvis orbit, where promethium science is
# made. platform-legs flies the filed platform from Nauvis out to WHOLE_TRIP_TURN and home in one flight.
LEGS_STOCK = CASCADE_STOCKS["full stacks, 20 railguns, 20 bays, 2 reactors"]  # the filed platform's (NOTES.md)


def leg_fuel(rows: list[dict]) -> list[dict]:
    """Per leg, in the order flown: fuel and oxidizer the module made a second over every flight that
    got onto it, and what the thruster and its tanks held at its end (the reserve as it got there)
    over the flights that finished it (Flown.legs)."""
    out = []
    for i in range(max((len(r["legs"]) for r in rows), default=0)):
        on = [r["legs"][i] for r in rows if len(r["legs"]) > i and "made" in r["legs"][i]]
        whole = [leg for leg in on if leg["to"] is not None]
        if not on:
            continue
        seconds = sum(leg["seconds"] for leg in on)
        fluids = sorted({k for leg in on for k in (*leg["made"], *leg["held"])})
        name = whole[0] if whole else on[0]
        out.append({"from": name["from"], "to": name["to"], "flights": len(on), "finished": len(whole),
                    "made/s": {k: sum(leg["made"].get(k, 0) for leg in on) / seconds for k in fluids} if seconds else {},
                    "held at end": {k: sum(leg["held"].get(k, 0) for leg in whole) / len(whole) for k in fluids}
                    if whole else {}})
    return out


def legs_model(rates_by_leg: list[dict], stock: dict) -> dict | None:
    """The trip model with the legs before and after the route (the way home mirrored if unmeasured):
    the planned trip (0.019 and a 10,000 km stay) on `stock`, and the best trip that fits with the
    legs, from the rates the plan came from with the whole trip folded in. None if a leg out was
    never finished or the rates aren't cached."""
    from tools import trip
    from tools.plan import capacity

    legs = legs_home(rates_by_leg)
    rates = trip_rates("platform-whole-trip.result.json")  # the route near the edge, as flown (NOTES.md)
    if rates is None or any(r["km"] is None for r in legs if r["part"] != "route"):
        return None
    before, after = trip.legs({"leg rates": legs})
    room = capacity(PROMETHIUM_PLATFORM)
    same = trip.trip(rates, 0.019, room, stock=stock, stay=10_000, before=before, after=after)
    top, _ = trip.best(rates, room, [t / 1000 for t in range(1, 51)], stays=(0.0, 10_000.0, 50_000.0),
                       before=before, after=after)
    keys = ("turn", "stay", "chunks", "comes back", "km", "hours", "ran out", "fits", "loaded slots", "used", "largest")
    return {"mirrored home": any(r.get("mirrored") for r in legs), "same trip": {k: same.get(k) for k in keys},
            "best": top and {k: top.get(k) for k in keys}}


def cached(name: str) -> dict:
    """An earlier study's result, from the optimise cache."""
    import json

    from tools.optimise import CACHE

    return json.loads((CACHE / f"{name}.result.json").read_text())


def platform_legs_planned(server, runs: int = 8) -> dict:
    """Does the best trip with the legs hold? The trip model's best from platform-legs' flights with
    fuel by script (legs_model), flown from Nauvis as planned (planned_trip), `runs` times, fuel by
    script: the module can't keep up from Aquilo to the edge (NOTES.md)."""
    legs = cached("platform-legs")["fuel by script"]
    best = legs_model(leg_rates(legs["flights"]), legs["supplies"])["best"]
    turn, stock = planned_trip(best)
    print(f"plan: turn {best['turn']:g}, stay {best['stay']:,.0f} km, {best['chunks']:.0f} chunks, comes back "
          f"{best['comes back']:.0%}; flown to {turn:.5f}", file=sys.stderr, flush=True)
    return {**platform_legs(server, runs, stock=stock, script_fuel=True, turn=turn), "plan": best}


MARGIN = 1.1  # over the most a planned flight used


def margin_stock(planned: dict, slots: int) -> dict:
    """A load with a margin, from the planned trip's flights (platform-legs-planned): magazines and
    foundation at MARGIN over the most a flight used, in whole stacks; every spare in whole stacks,
    with the 20 railguns, 20 bays and 2 reactors that closed the cascade (NOTES.md); and railgun
    ammo, which never ran out, in the slots left of `slots`."""
    from draftsman.data import items

    from tools import plan

    stock = planned["supplies"]
    most = {k: max(n - f["left"].get(k, 0) for f in planned["flights"]) for k, n in stock.items()}
    out = {**full_stacks(stock), "railgun-turret": 20, "cargo-bay": 20, "fusion-reactor": 2}
    for k in ("uranium-rounds-magazine", "space-platform-foundation"):
        out[k] = plan.slots({k: most[k] * MARGIN}) * items.raw[k]["stack_size"]
    out.pop("railgun-ammo", None)
    return {**out, "railgun-ammo": (slots - plan.slots(out)) * items.raw["railgun-ammo"]["stack_size"]}


def platform_legs_margin(server, runs: int = 8) -> dict:
    """The planned trip with the legs on a load with a margin (margin_stock), fuel by script."""
    from tools.plan import capacity

    planned = cached("platform-legs-planned")
    stock = margin_stock(planned, capacity(PROMETHIUM_PLATFORM))
    return {**platform_legs(server, runs, stock=stock, script_fuel=True, turn=planned["turn"]), "plan": planned["plan"]}


def legs_flown(flown, stock: dict) -> dict:
    """One flight from home and back in numbers: whole_trip_flown with its legs, and the leg it died on."""
    row = {**whole_trip_flown(flown, stock), "legs": flown.legs(), "trip km": flown.trip_km,
           "out km/s": None, "back km/s": None}  # the route's own are in its leg
    row["died on"] = None if flown.back or not row["legs"] else (row["legs"][-1]["from"], row["legs"][-1]["to"])
    return row


def legs_line(n: int, row: dict) -> str:
    return (f"flight {n}: {row['ended']} after {row['seconds']:,.0f} s and {row['trip km']:,.0f} km"
            + (f" (died between {row['died on'][0]} and the next stop)" if row["died on"] else "")
            + f"; {row['chunks aboard']} chunks aboard; {first_out(row)}; dry {row['dry turret s']} turret-s, low fuel "
            f"{row['low fuel s']} s; legs " + ", ".join(f"{leg['from']}-{leg['to']} {leg['km']:,.0f} km in "
                                                         f"{leg['seconds']:,.0f} s" for leg in row["legs"]))


def platform_legs(server, runs: int = 8, stock=None, script_fuel: bool = False, turn: float = WHOLE_TRIP_TURN,
                  design=None) -> dict:
    """What does getting there cost? design (PROMETHIUM_PLATFORM), readied in Nauvis orbit with stock
    (LEGS_STOCK), flies by Fulgora and Aquilo to the solar system edge, out to `turn` and back, and home
    to Nauvis the same way, `runs` times, nothing topped up and every chunk kept: per flight as
    legs_flown has it, per leg (leg_rates, and what its fuel module made: leg_fuel), and the trip model
    with the measured legs (legs_model). With script_fuel its thrusters are kept full by script, the
    module still aboard: what the legs cost in stock when fuel can't strand it."""
    import json

    from tools.plan import HOME, LEGS_SECONDS, VIA

    stock, design = stock or LEGS_STOCK, design or PROMETHIUM_PLATFORM
    rows = one_flights(server, design, runs, lambda f: legs_flown(f, stock), legs_line, start=0.0, turn=turn, fill=True,
                       seconds=LEGS_SECONDS, supplies=stock, research=WHOLE_TRIP_RESEARCH, home=HOME, via=list(VIA),
                       script_fuel=script_fuel)
    means, rates_by_leg, fuel_by_leg = whole_trip_means(rows), leg_rates(rows), leg_fuel(rows)
    print(f"means: {json.dumps(means)}", file=sys.stderr)
    for r in rates_by_leg:
        print(f"{r['part']} {r['from']}-{r['to']}: {r['finished']}/{r['flights']} finished, "
              f"{json.dumps({k: round(v, 3) for k, v in r['row'].items()})}", file=sys.stderr)
    for r in fuel_by_leg:
        print(f"{r['from']}-{r['to']}: made {json.dumps({k: round(v, 1) for k, v in r['made/s'].items()})} a second, "
              f"held at its end {json.dumps({k: round(v) for k, v in r['held at end'].items()})}", file=sys.stderr)
    model = legs_model(rates_by_leg, stock)
    print(f"model: {json.dumps(model)}", file=sys.stderr)
    return {"design": design, "home": HOME, "via": list(VIA), "script fuel": script_fuel, "turn": turn,
            "supplies": stock, "research": WHOLE_TRIP_RESEARCH, "flights": rows, "means": means,
            "leg rates": rates_by_leg, "leg fuel": fuel_by_leg, "model": model}


# fuel="advanced" (NOTES.md): the filed platform on the advanced thruster recipes, which need a
# twentieth of the metallic and carbonic chunks; flown from Nauvis on its own fuel as platform-legs
# flew the plain one ("on board"), with the plain one re-flown as the control.
ADVANCED_PLATFORM = {**PROMETHIUM_PLATFORM, "fuel": "advanced"}


def platform_advanced_fuel_margin(server, runs: int = 8) -> dict:
    """The trip platform-legs-margin flew on fuel by script (its load and turn), flown by ADVANCED_PLATFORM
    on its own fuel: whether the advanced recipes take the place of the script's fuel."""
    margin = cached("platform-legs-margin")
    return {**platform_legs(server, runs, stock=margin["supplies"], turn=margin["turn"], design=ADVANCED_PLATFORM),
            "plan": margin["plan"]}


# The crossing (NOTES.md): Aquilo to the solar system edge, 100,000 km, where flights from Nauvis
# on their own fuel strand. Flown from Aquilo with the line full, as from the last leg: what one
# tank a fluid does once there, apart from how full it arrives.
CROSSING_SECONDS = 4000  # at most: 100,000 km at 50 km/s take 2,000
CROSSING_KM = 100_000  # aquilo-solar-system-edge (dump)
CROSSING_DESIGNS = {"plain": PROMETHIUM_PLATFORM}


def crossing_flown(flown) -> dict:
    """One crossing in numbers: whether and when it reached the edge, what it made a second and
    held at the end, and its thrusters' seconds short of fuel."""
    from validator.flight import THRUSTER_FLUIDS

    at = next((r["seconds"] for r in flown.log if r["km"] >= CROSSING_KM - 10), None)
    return {"crossed": at is not None, "seconds to cross": at, "km": flown.km,
            "ended": flown.ended or ("crossed" if at is not None else "out of time"), "low fuel s": flown.low_fuel,
            "made/s": {k: flown.made.get(k, 0.0) / flown.seconds for k in THRUSTER_FLUIDS} if flown.seconds else {},
            "held": {k: flown.held.get(k, 0.0) for k in THRUSTER_FLUIDS}, "dry turret s": flown.dry}


def platform_crossing(server, runs: int = 8) -> dict:
    """Does each of CROSSING_DESIGNS cross from Aquilo to the edge on its own fuel? Flown from Aquilo
    as from the last leg (its line full and moving at CRUISE), with LEGS_STOCK and WHOLE_TRIP_RESEARCH,
    `runs` times each: crossed n of runs, the time, fuel made and held, seconds short of fuel."""
    import json

    def line(name):
        return lambda n, row: (
            f"platform-crossing {name} {n}: {row['ended']}"
            + (f" in {row['seconds to cross']:,.0f} s" if row["crossed"] else f", {row['km']:,.0f} km")
            + f"; made {json.dumps({k: round(v, 1) for k, v in row['made/s'].items()})} a second, held "
            f"{json.dumps({k: round(v) for k, v in row['held'].items()})}; short of fuel {row['low fuel s']} s")

    out = {}
    for name, design in CROSSING_DESIGNS.items():
        rows = one_flights(server, design, runs, crossing_flown, line(name), where=("aquilo", "solar-system-edge"),
                           start=0.0, seconds=CROSSING_SECONDS, supplies=LEGS_STOCK, research=WHOLE_TRIP_RESEARCH)
        crossed = [r for r in rows if r["crossed"]]
        out[name] = {"design": design, "flights": rows, "crossed": len(crossed),
                     "seconds to cross": sum(r["seconds to cross"] for r in crossed) / len(crossed) if crossed else None}
        print(f"platform-crossing {name}: crossed {len(crossed)}/{runs}", file=sys.stderr, flush=True)
    return {"from": "aquilo", "to": "solar-system-edge", "supplies": LEGS_STOCK, "research": WHOLE_TRIP_RESEARCH,
            "designs": out}


STUDIES = {"belt-fill": belt_fill, "platform-shape": platform_shape, "platform-research": platform_research,
           "platform-promethium": platform_promethium, "platform-belt": platform_belt, "platform-front": platform_front,
           "platform-wide": lambda server: platform_belt(server, WIDE_SPACE, WIDE_START, seconds=600),
           "platform-repair": platform_repair, "platform-foundation": platform_foundation,
           "platform-budget": platform_budget, "platform-fuel": platform_fuel, "platform-reserve": platform_reserve,
           "platform-power": platform_power, "platform-plant": platform_plant, "platform-ammo": platform_ammo,
           "platform-sides": platform_sides, "platform-route": platform_route,
           "platform-mix": platform_mix, "platform-bays": platform_bays, "platform-feed": platform_feed,
           "platform-feed-fast": lambda server: platform_feed(server, FAST_FEED_MODES, FAST_FEED_STARTS,
                                                              fast_feed_stock()),
           "platform-whole-trip": platform_whole_trip,
           "platform-whole-trip-corrected": lambda server: platform_whole_trip(
               server, stock=CORRECTED_TRIP_STOCK, fold="platform-whole-trip.result.json"),
           "platform-whole-trip-cascade": platform_whole_trip_cascade,
           "platform-whole-trip-shock": lambda server: platform_whole_trip_cascade(server, stocks=SHOCK_STOCKS,
                                                                                    shock=SHOCK),
           "platform-legs": lambda server: {"on board": platform_legs(server),
                                            "fuel by script": platform_legs(server, script_fuel=True)},
           "platform-legs-planned": platform_legs_planned, "platform-legs-margin": platform_legs_margin,
           "platform-advanced-fuel": lambda server: platform_legs(server, design=ADVANCED_PLATFORM),
           "platform-advanced-fuel-control": lambda server: platform_legs(server),
           "platform-advanced-fuel-margin": platform_advanced_fuel_margin, "platform-crossing": platform_crossing}
