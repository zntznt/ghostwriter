"""Studies for tools.optimise: questions about the generators, answered in game.

Each study takes a running server and returns a JSON-able result, printing a
table as it goes. Its books and values are fixed in code before it runs.
"""

import sys
from collections import Counter

from tools.optimise import GameEvaluator, grid

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
        trials = grid(evaluate, {"rate": [round(per_share * f, 4) for f in SHARES]}, {"item": item, "fill": 1.0})
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


PLATFORM_SPACE = {"railguns": [2, 4, 6, 8, 10, 12], "guns": [0, 3, 6, 9, 12], "thrusters": [1, 2, 3, 4]}
PLATFORM_START = {"railguns": 4, "guns": 6, "thrusters": 2}


def platform_shape(server) -> dict:
    """How many railguns, gun turrets and thrusters fly a platform furthest toward
    the shattered planet in 300 s (power, ammo and fuel by script)? Coordinate
    descent from 4/6/2, each design flown 4 times, moving only on a 5% gain in
    mean km (flights differ run to run)."""
    from tools.optimise import FlightEvaluator, descend

    evaluate = FlightEvaluator(server, "platform", runs=4)

    def logged(params):
        t = evaluate(params)
        r = t.rates
        print(f"{params}: " + (f"mean {r['km_mean']:,.0f} km (min {r['km_min']:,.0f}, max {r['km_max']:,.0f}), "
                               f"{r['seconds_mean']:.0f}s, survived {r['survived']:.0%}" if t.passed else f"FAIL {t.error}"),
              file=sys.stderr, flush=True)
        return t

    best, trials = descend(logged, PLATFORM_SPACE, PLATFORM_START, score=lambda t: t.rates["km_mean"], margin=0.05)
    print(f"best: {best.params if best else None}; trials run: {evaluate.ran}", file=sys.stderr)
    return {"best": best.params if best else None, "best_rates": best.rates if best else None,
            "trials": [{"params": t.params, "passed": t.passed, "rates": t.rates, "error": t.error} for t in trials]}


PLATFORM_BEST = {"railguns": 6, "guns": 9, "thrusters": 1}  # platform-shape's best (NOTES.md)
# Each axis is one kind of research, at none and at levels a finished game may
# have. Railgun damage 2 is where one shot kills a huge promethium asteroid:
# (10000 x 1.8 - 3000) x 0.9 = 13500 >= 10000. Bullets are the gun turrets'.
RESEARCH_AXES = {
    "railgun shooting speed": [{}, {"railgun-shooting-speed": 3}, {"railgun-shooting-speed": 6}],
    "railgun damage": [{}, {"railgun-damage": 2}],
    "bullets": [{}, {"physical-projectile-damage": 10, "weapon-shooting-speed": 6}],
}


def platform_research(server) -> dict:
    """How much of a platform's survival is research? The best design from
    platform-shape (6 railguns, 9 gun turrets, 1 thruster) flown 4 times at
    every combination of the RESEARCH_AXES levels, 300 s each (power, ammo and
    fuel by script)."""
    import itertools

    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=4)
    rows = []
    for combo in itertools.product(*RESEARCH_AXES.values()):
        research = {k: v for levels in combo for k, v in levels.items()}
        t = evaluate({**PLATFORM_BEST, "research": research})
        r = t.rates
        label = ", ".join(f"{k} {v}" for k, v in research.items()) or "no research"
        print(f"{label}: " + (f"mean {r['km_mean']:,.0f} km (min {r['km_min']:,.0f}, max {r['km_max']:,.0f}), "
                              f"{r['seconds_mean']:.0f}s, survived {r['survived']:.0%}; ended {t.stalled}"
                              if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"research": research, "passed": t.passed, "rates": r, "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": PLATFORM_BEST, "trials": rows}


HIGH_RESEARCH = {"railgun-shooting-speed": 6, "railgun-damage": 2, "physical-projectile-damage": 10,
                 "weapon-shooting-speed": 6}  # platform-research's best row
PROMETHIUM_STARTS = [0.0, 0.2, 0.3, 0.4]  # huge promethium asteroids are rare before 0.2 (NOTES.md)


def platform_promethium(server) -> dict:
    """Does the best platform so far live where promethium is? The platform-shape
    best with HIGH_RESEARCH, flown 4 times for 300 s from each of PROMETHIUM_STARTS
    (shares of the route; power, ammo and fuel by script)."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=4)
    rows = []
    for start in PROMETHIUM_STARTS:
        t = evaluate({**PLATFORM_BEST, "research": HIGH_RESEARCH, "start": start})
        r = t.rates
        print(f"from {start:.0%} of the route: " + (
            f"mean {r['km_mean']:,.0f} km flown (min {r['km_min']:,.0f}, max {r['km_max']:,.0f}), "
            f"{r['seconds_mean']:.0f}s, survived {r['survived']:.0%}; ended {t.stalled}"
            if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"start": start, "passed": t.passed, "rates": r, "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": PLATFORM_BEST, "research": HIGH_RESEARCH, "trials": rows}


BELT_START = 0.4  # where huge promethium asteroids are common (NOTES.md)
BELT_SPACE = {"railguns": [2, 4, 6, 8, 10, 12, 16, 20], "guns": [0, 3, 6, 9, 12, 16, 20, 24], "thrusters": [1, 2, 3]}
# platform-belt's best (20, 24, 1) was its largest shape: platform-wide goes on from there. Its
# flights are 600 s: at 300 s, 28/28 and 32/28 survived and the capped mean couldn't tell them apart.
WIDE_SPACE = {"railguns": [16, 20, 24, 28, 32, 40, 48], "guns": [20, 24, 28, 32, 40, 48], "thrusters": [1, 2]}
WIDE_START = {"railguns": 20, "guns": 24, "thrusters": 1}


def platform_belt(server, space=BELT_SPACE, start=PLATFORM_BEST, seconds=300) -> dict:
    """Which railgun, gun turret and thruster counts live longest where promethium
    is? Coordinate descent over space from start (the platform-shape best), each
    design flown 4 times from BELT_START with HIGH_RESEARCH for up to seconds,
    scored by mean seconds lived, moving only on a 5% gain (flights differ run to
    run). Each trial also counts what killed what, asteroids by size."""
    from tools.optimise import FlightEvaluator, descend

    evaluate = FlightEvaluator(server, "platform", runs=4)
    fixed = {"research": HIGH_RESEARCH, "start": BELT_START, "seconds": seconds}

    def logged(params):
        t = evaluate({**params, **fixed})
        t.params = {k: v for k, v in t.params.items() if k not in fixed}  # descend moves on the shape alone
        if t.passed:
            # Ours are the deaths an asteroid caused; asteroids' own deaths start with their size.
            lost = Counter()
            for k, n in t.counts.items():
                if k.endswith(" asteroid"):
                    lost[k.split(" <- ")[0]] += n
            hub = {k.split(" <- ")[1]: n for k, n in t.counts.items() if k.startswith("space-platform-hub <- ")}
            print(f"{t.params}: {t.rates['seconds_mean']:.0f}s mean, survived {t.rates['survived']:.0%}; "
                  f"lost {dict(lost)}; hub killed by {hub or 'nothing'}", file=sys.stderr, flush=True)
        else:
            print(f"{t.params}: FAIL {t.error}", file=sys.stderr, flush=True)
        return t

    best, trials = descend(logged, space, start, score=lambda t: t.rates["seconds_mean"], margin=0.05)
    print(f"best: {best.params if best else None}; trials run: {evaluate.ran}", file=sys.stderr)
    return {"start": BELT_START, "research": HIGH_RESEARCH, "best": best.params if best else None,
            "best_rates": best.rates if best else None,
            "trials": [{"params": t.params, "passed": t.passed, "rates": t.rates, "counts": t.counts,
                        "ended": t.stalled, "error": t.error} for t in trials]}


FRONT_SHAPES = [(8, 12), (12, 16), (16, 20), (20, 24)]  # railguns, gun turrets: platform-belt's path


def platform_front(server) -> dict:
    """Do gun turrets do better in the railguns' row than behind it? Each of
    FRONT_SHAPES (1 thruster) in both layouts, flown 4 times from BELT_START
    with HIGH_RESEARCH for up to 300 s. Counts show friendly fire: a gun turret
    killed by a railgun stood in its line."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=4)
    rows = []
    for railguns, guns in FRONT_SHAPES:
        for layout in ("rows", "front"):
            t = evaluate({"railguns": railguns, "guns": guns, "thrusters": 1, "layout": layout,
                          "research": HIGH_RESEARCH, "start": BELT_START})
            friendly = {k: n for k, n in t.counts.items() if k.endswith("<- railgun-turret")
                        and not k.split(" <- ")[0].endswith("asteroid")}
            print(f"{railguns} railguns, {guns} gun turrets, {layout}: " + (
                f"{t.rates['seconds_mean']:.0f}s mean, survived {t.rates['survived']:.0%}; "
                f"railguns lost {sum(n for k, n in t.counts.items() if k.startswith('railgun-turret <-'))}, "
                f"gun turrets lost {sum(n for k, n in t.counts.items() if k.startswith('gun-turret <-'))}; "
                f"friendly fire {friendly or 'none'}" if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
            rows.append({"railguns": railguns, "guns": guns, "layout": layout, "passed": t.passed, "rates": t.rates,
                         "counts": t.counts, "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"start": BELT_START, "research": HIGH_RESEARCH, "trials": rows}


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
    """Does keeping the platform up change how long it lives where promethium is?
    Each of REPAIR_SHAPES flown 4 times from BELT_START with HIGH_RESEARCH for up
    to 600 s, with nothing, with repair packs in the hub (it repairs with them, no
    research needed), with spares and construction-robotics (destroyed entities
    and tiles leave ghosts the hub rebuilds), and with both."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=4)
    rows = []
    for shape in REPAIR_SHAPES:
        for mode, (research, supplies) in REPAIR_MODES.items():
            t = evaluate({**shape, "research": {**HIGH_RESEARCH, **research}, "start": BELT_START, "seconds": 600,
                          "supplies": supplies})
            lost = sum(n for k, n in t.counts.items() if not k.split(" <- ")[0].endswith("asteroid"))
            print(f"{shape['railguns']} railguns, {shape['guns']} gun turrets, {mode}: " + (
                f"{t.rates['seconds_mean']:.0f}s mean, {t.rates['km_mean']:,.0f} km, survived "
                f"{t.rates['survived']:.0%}; entities lost {lost}" if t.passed else f"FAIL {t.error}"),
                file=sys.stderr, flush=True)
            rows.append({**shape, "mode": mode, "passed": t.passed, "rates": t.rates, "counts": t.counts,
                         "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"start": BELT_START, "research": HIGH_RESEARCH, "modes": {k: {"research": r, "supplies": s}
                                                                     for k, (r, s) in REPAIR_MODES.items()},
            "trials": rows}


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
    """Does more foundation help a platform that rebuilds? Each of REPAIR_SHAPES
    flown 8 times from BELT_START with HIGH_RESEARCH and construction-robotics for
    up to 600 s, rebuilding from the platform-repair spares, from 8 cargo bays
    full of foundation, with a margin of 16 spare columns each side, and with both.
    8 flights, not 4: at 4, the cargo bays' 4 of 4 survivors stood against 2 of the
    same flights in a probe, both lost."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for shape in REPAIR_SHAPES:
        for mode, (extra, supplies) in FOUNDATION_MODES.items():
            t = evaluate({**shape, **extra, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
                          "start": BELT_START, "seconds": 600, "supplies": supplies})
            r = t.rates
            print(f"{shape['railguns']} railguns, {shape['guns']} gun turrets, {mode}: " + (
                f"{r['seconds_mean']:.0f}s mean, {r['km_mean']:,.0f} km ({r['km_mean'] / r['seconds_mean']:.0f} km/s), "
                f"survived {r['survived']:.0%}" if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
            rows.append({**shape, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                         "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"start": BELT_START, "research": HIGH_RESEARCH, "modes": {k: {"shape": e, "supplies": s}
                                                                     for k, (e, s) in FOUNDATION_MODES.items()},
            "trials": rows}


FILED_PLATFORM = {"railguns": 32, "guns": 32, "thrusters": 1, "cargo_bays": 8}  # library/platforms/
BUDGET_STARTS = [0.0, BELT_START]  # where the route begins, and where promethium is


def platform_budget(server) -> dict:
    """What does the filed platform use a second, to make on board instead? It flies
    8 times from each of BUDGET_STARTS for up to 600 s with platform-foundation's
    winning stock and research, and the flight counts what the script supplies
    (power, thruster fuel and oxidizer, ammo) and what the hub rebuilds."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for start in BUDGET_STARTS:
        t = evaluate({**FILED_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "start": start,
                      "seconds": 600, "supplies": BAY_SPARES})
        r = t.rates
        use = {k: round(v, 3) for k, v in r.items() if k.endswith("/s") or k.endswith("MW")
               or k.endswith(" lived")}
        print(f"from {start:.0%} of the route: " + (
            f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}; a second: {use}" if t.passed
            else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"start": start, "passed": t.passed, "rates": r, "counts": t.counts, "ended": t.stalled,
                     "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": FILED_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
            "supplies": BAY_SPARES, "trials": rows}


def platform_fuel(server) -> dict:
    """Does the filed platform fly on fuel it makes itself (fuel=True), and what does
    that cost it? Both designs fly 8 times from each of BUDGET_STARTS for up to 600 s
    with platform-budget's stock and research: the filed one with fuel by script, the
    fuelled one with none after its start (thrusters primed, moving at CRUISE)."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for start in BUDGET_STARTS:
        for fuel in (False, True):
            t = evaluate({**FILED_PLATFORM, **({"fuel": True} if fuel else {}), "start": start, "seconds": 600,
                          "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": BAY_SPARES})
            r = t.rates
            module = {k: n for k, n in t.counts.items()
                      if k.split(" <- ")[0] in ("asteroid-collector", "crusher", "chemical-plant", "transport-belt")}
            print(f"from {start:.0%}, {'fuel made on board' if fuel else 'fuel by script'}: " + (
                f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean; "
                f"made {r.get('made thruster-fuel/s', 0):.1f} fuel/s, {r.get('made thruster-oxidizer/s', 0):.1f} "
                f"oxidizer/s; module lost {module or 'nothing'}" if t.passed else f"FAIL {t.error}"),
                file=sys.stderr, flush=True)
            rows.append({"start": start, "fuel": fuel, "passed": t.passed, "rates": r, "counts": t.counts,
                         "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": FILED_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
            "supplies": BAY_SPARES, "trials": rows}


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
    """Does a fuel reserve filled before leaving (reserve=True) get the fuelled platform
    going from the solar system edge, and do the module's spares keep it alive? Each of
    RESERVE_MODES flies 8 times from each of BUDGET_STARTS, and each of MODULE_CONTROLS
    8 times from BELT_START, for up to 600 s with platform-budget's research."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    runs = [(start, mode, how) for start in BUDGET_STARTS for mode, how in RESERVE_MODES.items()]
    runs += [(BELT_START, mode, how) for mode, how in MODULE_CONTROLS.items()]
    rows = []
    for start, mode, (extra, supplies) in runs:
        t = evaluate({**FILED_PLATFORM, **extra, "start": start, "seconds": 600,
                      "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": supplies})
        r = t.rates
        module = {k: n for k, n in t.counts.items()
                  if k.split(" <- ")[0] in ("asteroid-collector", "crusher", "chemical-plant", "transport-belt",
                                            "storage-tank")}
        print(f"from {start:.0%}, {mode}: " + (
            f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean "
            f"({r['km_min']:,.0f} to {r['km_max']:,.0f}); made {r.get('made thruster-fuel/s', 0):.1f} fuel/s and "
            f"{r.get('made thruster-oxidizer/s', 0):.1f} oxidizer/s, held at the end "
            f"{r.get('held thruster-fuel', 0):,.0f} and {r.get('held thruster-oxidizer', 0):,.0f}; "
            f"module lost {module or 'nothing'}" if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"start": start, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                     "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": FILED_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
            "modes": {k: {"extra": e, "supplies": s} for k, (e, s) in {**RESERVE_MODES, **MODULE_CONTROLS}.items()},
            "trials": rows}


POWER_MODES = {  # mode -> build and flight parameters; the hub's stock is BAY_SPARES (and cells with power)
    "stand-in power": {"fuel": True, "reserve": True},
    "1 fusion reactor": {"fuel": True, "reserve": True, "power": 1},
    "2 fusion reactors": {"fuel": True, "reserve": True, "power": 2},
}
PLANT = ("fusion-reactor", "fusion-generator", "cryogenic-plant", "accumulator")


def platform_power(server) -> dict:
    """Does the fuelled platform with its reserve run on its own fusion plants (power=N)
    as it does on the stand-in source? Each of POWER_MODES flies 8 times from each of
    BUDGET_STARTS, for up to 600 s with platform-budget's research: what it lives and
    flies, the power it uses and makes, fusion cells an hour, seconds any reactor was
    short of power, and what of the plant asteroids destroyed."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for start in BUDGET_STARTS:
        for mode, extra in POWER_MODES.items():
            t = evaluate({**FILED_PLATFORM, **extra, "start": start, "seconds": 600,
                          "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": BAY_SPARES})
            r = t.rates
            plant = {k: n for k, n in t.counts.items() if k.split(" <- ")[0] in PLANT}
            print(f"from {start:.0%}, {mode}: " + (
                f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean "
                f"({r['km_min']:,.0f} to {r['km_max']:,.0f}); uses {r.get('power MW', 0):.1f} MW"
                + (f", makes {r['generated MW']:.1f} MW gross on {r['fusion cells/h']:.2f} cells/h, reactors short "
                   f"of power {r['brownout s']:.1f} s a flight; plant lost {plant or 'nothing'}"
                   if "generated MW" in r else "") if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
            rows.append({"start": start, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                         "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": FILED_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
            "supplies": BAY_SPARES, "modes": POWER_MODES, "trials": rows}


# What the hub rebuilds a fusion plant from: 9 of its 219 slots with 8 bays (a reactor
# stacks to 1, a generator to 5), which the stock is full without, so 1,000 foundation
# make room. The cell belt passes under a reactor on an express underground belt.
PLANT_SPARES = {"fusion-reactor": 2, "fusion-generator": 4, "cryogenic-plant": 2, "accumulator": 1, "pipe": 50,
                "inserter": 10, "transport-belt": 20, "express-underground-belt": 4}
PLANT_MODES = {  # mode -> the hub's stock, for the fuelled platform with its reserve and power=2
    "no plant spares": BAY_SPARES,
    "plant spares": {**BAY_SPARES, **PLANT_SPARES, "space-platform-foundation": 19800},
}


def platform_plant(server) -> dict:
    """Does the hub keep the fusion plants going where promethium is when it holds their
    parts? The fuelled platform with its reserve and power=2 flies 8 times from BELT_START
    for up to 600 s with each of PLANT_MODES' stock and platform-budget's research: what
    it lives and flies, seconds a reactor was short of power, fusion cells an hour, and
    what of the plant asteroids destroyed and the hub rebuilt."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for mode, supplies in PLANT_MODES.items():
        t = evaluate({**FILED_PLATFORM, "fuel": True, "reserve": True, "power": 2, "start": BELT_START,
                      "seconds": 600, "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": supplies})
        r = t.rates
        lost = {k: n for k, n in t.counts.items() if k.split(" <- ")[0] in PLANT}
        rebuilt = {k[len("rebuilt "):-2]: round(v * r["seconds_mean"], 2) for k, v in r.items()
                   if k.startswith("rebuilt ") and k[len("rebuilt "):-2] in PLANT}
        print(f"{mode}: " + (
            f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean "
            f"({r['km_min']:,.0f} to {r['km_max']:,.0f}); makes {r['generated MW']:.1f} MW gross on "
            f"{r['fusion cells/h']:.2f} cells/h, reactors short of power {r['brownout s']:.1f} s a flight; plant lost "
            f"{lost or 'nothing'} (8 flights), rebuilt {rebuilt or 'nothing'} a flight"
            if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"mode": mode, "passed": t.passed, "rates": r, "counts": t.counts, "ended": t.stalled,
                     "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": {**FILED_PLATFORM, "fuel": True, "reserve": True, "power": 2}, "start": BELT_START,
            "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "modes": PLANT_MODES, "trials": rows}


# The whole platform so far: its own fuel (with the reserve) and its own power, and the
# hub's stock with plant spares. Only ammo is still supplied by script.
FULL_PLATFORM = {**FILED_PLATFORM, "fuel": True, "reserve": True, "power": 2}
FULL_STOCK = PLANT_MODES["plant spares"]
# Uranium rounds can't be made in space (no uranium); piercing and firearm rounds can.
MAGAZINES = ["uranium-rounds-magazine", "piercing-rounds-magazine", "firearm-magazine"]


def per_1000_km(rates: dict) -> str:
    """The supplies and rebuilds a flight used per 1,000 km flown, as one line."""
    names = {"rebuilt space-platform-foundation/1000 km": "foundation",
             "railgun-ammo/1000 km": "railgun ammo"} | {f"{m}/1000 km": "magazines" for m in MAGAZINES}
    return ", ".join(f"{label} {rates[k]:,.1f}" for k, label in names.items() if k in rates)


def platform_ammo(server) -> dict:
    """What can the gun turrets fire that the platform could make? FULL_PLATFORM with
    FULL_STOCK flies 8 times from each of BUDGET_STARTS for up to 600 s with
    platform-budget's research, its gun turrets stocked with each of MAGAZINES: what it
    lives and flies, and what it uses a second and per 1,000 km flown."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for start in BUDGET_STARTS:
        for magazine in MAGAZINES:
            t = evaluate({**FULL_PLATFORM, "start": start, "seconds": 600, "magazine": magazine,
                          "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": FULL_STOCK})
            r = t.rates
            print(f"from {start:.0%}, {magazine}: " + (
                f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean "
                f"({r['km_min']:,.0f} to {r['km_max']:,.0f}); {r.get(magazine + '/s', 0):.2f} magazines/s; "
                f"per 1,000 km: {per_1000_km(r)}" if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
            rows.append({"start": start, "magazine": magazine, "passed": t.passed, "rates": r, "counts": t.counts,
                         "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": FULL_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
            "supplies": FULL_STOCK, "trials": rows}


STUDIES = {"belt-fill": belt_fill, "platform-shape": platform_shape, "platform-research": platform_research,
           "platform-promethium": platform_promethium, "platform-belt": platform_belt, "platform-front": platform_front,
           "platform-wide": lambda server: platform_belt(server, WIDE_SPACE, WIDE_START, seconds=600),
           "platform-repair": platform_repair, "platform-foundation": platform_foundation,
           "platform-budget": platform_budget, "platform-fuel": platform_fuel, "platform-reserve": platform_reserve,
           "platform-power": platform_power, "platform-plant": platform_plant, "platform-ammo": platform_ammo}
