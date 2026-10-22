"""Studies for tools.optimise: questions about the generators, answered in game.

Each study takes a running server and returns a JSON-able result, printing a
table as it goes. Its books and values are fixed in code before it runs.
"""

import sys
from collections import Counter

from generators.platform import FEED_RESEARCH
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
# The same for rockets and lasers: their damage series to 10, as physical projectile damage
# is, and laser shooting speed to its last level. Rockets' shooting speed comes with weapon
# shooting speed 6 (+340%).
MIX_RESEARCH = {**HIGH_RESEARCH, "stronger-explosives": 10, "laser-weapons-damage": 10, "laser-shooting-speed": 7}
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


SIDE_MODES = {"no side guns": {}, "5 side guns a side": {"side_guns": 5}}  # 5: as many as the ends hold


def platform_sides(server) -> dict:
    """Do gun turrets at the side ends, where asteroids eat foundation first, save what
    the hub rebuilds? FULL_PLATFORM with each of SIDE_MODES flies 8 times from BELT_START
    for up to 600 s with FULL_STOCK and platform-budget's research: what it lives and
    flies, and what it used and rebuilt per 1,000 km flown."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for mode, extra in SIDE_MODES.items():
        t = evaluate({**FULL_PLATFORM, **extra, "start": BELT_START, "seconds": 600,
                      "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": FULL_STOCK})
        r = t.rates
        rebuilt = {k[len("rebuilt "):-len("/1000 km")]: round(v, 2) for k, v in r.items()
                   if k.startswith("rebuilt ") and k.endswith("/1000 km")}
        print(f"{mode}: " + (
            f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean "
            f"({r['km_min']:,.0f} to {r['km_max']:,.0f}); per 1,000 km: {per_1000_km(r)}; rebuilt per 1,000 km "
            f"{rebuilt}" if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"mode": mode, "passed": t.passed, "rates": r, "counts": t.counts, "ended": t.stalled,
                     "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": FULL_PLATFORM, "start": BELT_START, "modes": SIDE_MODES,
            "research": {**HIGH_RESEARCH, "construction-robotics": 1}, "supplies": FULL_STOCK, "trials": rows}


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


def platform_route(server) -> dict:
    """What does the whole platform use, and collect, along the route? TRIP_PLATFORM flies
    8 times from each of ROUTE_STARTS (and each other run of ROUTE_RUNS) for up to 600 s
    with TRIP_STOCK and platform-budget's research: what it lives and flies, its cruise,
    and per 1,000 km what it used and rebuilt and the promethium chunks it brought in."""
    from tools.optimise import FlightEvaluator

    from generators.platform import PROMETHIUM

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for start, mode, extra in ROUTE_RUNS:
        keeps = extra.get("promethium", True)
        t = evaluate({**TRIP_PLATFORM, **extra, "start": start, "seconds": 600, "supplies": TRIP_STOCK,
                      "research": {**HIGH_RESEARCH, "construction-robotics": 1}, **({"take": [PROMETHIUM]} if keeps else {})})
        r = t.rates
        print(f"from {start:.0%}, {mode}: " + (
            f"{r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, {r['km_mean']:,.0f} km mean, cruise "
            f"{r.get('cruise km/s', 0):.1f} km/s; per 1,000 km: {per_1000_km(r)}, fusion cells "
            f"{r.get('fusion cells/1000 km', 0):.3f}, promethium {r.get(f'taken {PROMETHIUM}/1000 km', 0):.2f}; made "
            f"{r.get('made thruster-fuel/s', 0):.1f} fuel/s" if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
        rows.append({"start": start, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                     "ended": t.stalled, "error": t.error})
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": TRIP_PLATFORM, "research": {**HIGH_RESEARCH, "construction-robotics": 1},
            "supplies": TRIP_STOCK, "trials": rows}


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
    """Which turret mix and shape fill the fewest hub slots per 1,000 km? TRIP_PLATFORM with
    each of MIX_MODES flies 8 times from each of MIX_STARTS for up to 600 s with MIX_STOCK and
    MIX_RESEARCH (with construction-robotics): what it lives and flies, its cruise and power,
    and per 1,000 km what it used, rebuilt and brought in, scored in hub slots (tools.trip).
    Rows are named by mode, so `python -m tools.trip RESULT --mode M` plans a trip from one."""
    from tools import trip
    from tools.optimise import FlightEvaluator

    from generators.platform import PROMETHIUM

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for mode, extra in MIX_MODES.items():
        for start in MIX_STARTS:
            t = evaluate({**TRIP_PLATFORM, **extra, "start": start, "seconds": 600, "supplies": MIX_STOCK,
                          "research": {**MIX_RESEARCH, "construction-robotics": 1}, "take": [PROMETHIUM]})
            r = t.rates
            row = {"start": start, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                   "ended": t.stalled, "error": t.error}
            if t.passed:
                used = trip.slots(trip.table({"trials": [row]}, mode)[start])
                top = ", ".join(f"{k} {v:.2f}" for k, v in sorted(used.items(), key=lambda kv: -kv[1])[:5])
                print(f"{mode}, from {start:.0%}: {r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, "
                      f"{r['km_mean']:,.0f} km mean, cruise {r.get('cruise km/s', 0):.1f} km/s, "
                      f"{r.get('generated MW', 0):.1f} MW; hub slots per 1,000 km {sum(used.values()):.2f} ({top}); "
                      f"promethium {r.get(f'taken {PROMETHIUM}/1000 km', 0):.2f}", file=sys.stderr, flush=True)
            else:
                print(f"{mode}, from {start:.0%}: FAIL {t.error}", file=sys.stderr, flush=True)
            rows.append(row)
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": TRIP_PLATFORM, "modes": MIX_MODES, "research": {**MIX_RESEARCH, "construction-robotics": 1},
            "supplies": MIX_STOCK, "trials": rows}


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
    import math

    from draftsman.data import items

    def slots(stock):
        return sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in stock.items())

    extra = 200 * (bays - 8)  # the hub refuses an insert of none
    stock = {**MIX_STOCK, **({"railgun-ammo": MIX_STOCK.get("railgun-ammo", 0) + extra} if extra > 0 else {})}
    if module_spares:
        before = slots(stock)
        stock.update(BAY_MODULE_SPARES)
        stock["space-platform-foundation"] -= 100 * (slots(stock) - before)
    return stock


def platform_bays(server) -> dict:
    """How many cargo bays should the 16-railgun platform carry? NARROW with each of BAY_COUNTS
    flies 8 times from each of BAY_STARTS for up to 600 s with bay_stock and MIX_RESEARCH (with
    construction-robotics): what it lives and flies, and per 1,000 km what it used, rebuilt and
    brought in, the bays it lost by distance from the hub, and its hub slots (tools.trip). Rows
    are named "N bays", so `python -m tools.trip RESULT --mode "N bays" --bays N` plans its trip."""
    from tools import trip
    from tools.optimise import FlightEvaluator

    from generators.platform import PROMETHIUM

    evaluate = FlightEvaluator(server, "platform", runs=8)
    rows = []
    for bays, spares in ((b, s) for b in BAY_COUNTS for s in (True, False)):
        mode = f"{bays} bays" + ("" if spares else ", no module spares")
        for start in BAY_STARTS:
            t = evaluate({**NARROW, "cargo_bays": bays, "start": start, "seconds": 600, "supplies": bay_stock(bays, spares),
                          "research": {**MIX_RESEARCH, "construction-robotics": 1}, "take": [PROMETHIUM]})
            r = t.rates
            row = {"start": start, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                   "ended": t.stalled, "error": t.error}
            if t.passed:
                used = trip.slots(trip.table({"trials": [row]}, mode)[start])
                top = ", ".join(f"{k} {v:.2f}" for k, v in sorted(used.items(), key=lambda kv: -kv[1])[:5])
                lost = ", ".join(f"|x| {k.split('|x| ')[1].split('/')[0]}: {v:.2f}" for k, v in sorted(r.items())
                                 if k.startswith("cargo bays lost at") and k.endswith("/1000 km"))
                print(f"{mode}, from {start:.0%}: {r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, "
                      f"{r['km_mean']:,.0f} km mean, cruise {r.get('cruise km/s', 0):.1f} km/s; hub slots per "
                      f"1,000 km {sum(used.values()):.2f} ({top}); bays lost per 1,000 km {lost or 'none'}; "
                      f"promethium {r.get(f'taken {PROMETHIUM}/1000 km', 0):.2f}", file=sys.stderr, flush=True)
            else:
                print(f"{mode}, from {start:.0%}: FAIL {t.error}", file=sys.stderr, flush=True)
            rows.append(row)
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": NARROW, "bays": BAY_COUNTS, "research": {**MIX_RESEARCH, "construction-robotics": 1},
            "supplies": {b: bay_stock(b) for b in BAY_COUNTS}, "module spares": BAY_MODULE_SPARES, "trials": rows}


# The promethium platform's hub feeds its own turrets (ammo_feed), with no ammo by script,
# against the same platform on script ammo, from where the trip goes (NOTES.md). Both fly
# the same stock: bay_stock(24) with magazines for 600 s 0.1 out (about 5.7 a second) and
# spares for the feed's underground belts, in place of as much foundation.
FEED_STARTS = [0.0, 0.1]
FEED_MODES = {"script ammo": {}, "hub feeds": {"ammo_feed": True}}
PROMETHIUM_PLATFORM = {**BAYS_PLATFORM, "ammo_feed": True}  # library/platforms/: as flown, the hub feeding (NOTES.md)


def feed_stock() -> dict:
    import math

    from draftsman.data import items

    def slots(stock):
        return sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in stock.items())

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


def platform_feed(server, modes=FEED_MODES, starts=FEED_STARTS, stock=None) -> dict:
    """Does the hub keep the turrets fed? BAYS_PLATFORM in each of modes flies 8 times
    from each of starts for up to 600 s with stock (feed_stock) and MIX_RESEARCH (with
    construction-robotics, and a mode's own "research"): what it lives and flies, the
    turret-seconds with no ammo, and per 1,000 km what it used (from the hub, or by script),
    rebuilt and brought in, and its hub slots (tools.trip). Rows are named by mode, so
    `python -m tools.trip RESULT --mode M --bays 24` plans its trip."""
    from tools import trip
    from tools.optimise import FlightEvaluator

    from generators.platform import PROMETHIUM

    evaluate = FlightEvaluator(server, "platform", runs=8)
    stock = stock or feed_stock()
    rows, researched = [], {}
    for mode, extra in modes.items():
        extra = dict(extra)
        researched[mode] = {**MIX_RESEARCH, "construction-robotics": 1, **extra.pop("research", {})}
        for start in starts:
            t = evaluate({**BAYS_PLATFORM, **extra, "start": start, "seconds": 600, "supplies": stock,
                          "research": researched[mode], "take": [PROMETHIUM]})
            r = t.rates
            row = {"start": start, "mode": mode, "passed": t.passed, "rates": r, "counts": t.counts,
                   "ended": t.stalled, "error": t.error}
            if t.passed:
                used = trip.slots(trip.table({"trials": [row]}, mode)[start])
                top = ", ".join(f"{k} {v:.2f}" for k, v in sorted(used.items(), key=lambda kv: -kv[1])[:5])
                feed = {k[:-2]: round(v, 2) for k, v in r.items()
                        if k in ("railgun-ammo/s", "uranium-rounds-magazine/s")}
                print(f"{mode}, from {start:.0%}: {r['seconds_mean']:.0f}s mean, survived {r['survived']:.0%}, "
                      f"{r['km_mean']:,.0f} km mean, cruise {r.get('cruise km/s', 0):.1f} km/s; ammo used a second "
                      f"{feed}; turrets dry {r.get('dry turret s', 0):.0f} turret-s a flight; hub slots per 1,000 km "
                      f"{sum(used.values()):.2f} ({top}); promethium {r.get(f'taken {PROMETHIUM}/1000 km', 0):.2f}",
                      file=sys.stderr, flush=True)
            else:
                print(f"{mode}, from {start:.0%}: FAIL {t.error}", file=sys.stderr, flush=True)
            rows.append(row)
    print(f"trials run: {evaluate.ran} (the rest from the cache)", file=sys.stderr)
    return {"design": BAYS_PLATFORM, "modes": modes, "research": researched, "supplies": stock, "trials": rows}


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


def full_stacks(stock: dict) -> dict:
    """Every item rounded up to whole stacks: a hub slot holds a stack, so it costs no slots."""
    from draftsman.data import items

    return {k: -(-n // items.raw[k]["stack_size"]) * items.raw[k]["stack_size"] for k, n in stock.items()}


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


def whole_trip_flown(flown, stock: dict) -> dict:
    """One whole-trip flight in numbers. A flight that didn't come back brings back no chunks."""
    turn_km = WHOLE_TRIP_TURN * 4_000_000
    first = min(flown.ran_out.items(), key=lambda kv: kv[1]) if flown.ran_out else None
    chunks = flown.cargo.get("promethium-asteroid-chunk", 0)
    # The two legs apart, from the hub's cargo as it turned (None if it never did, or didn't come back).
    turned = flown.at_turn.get("cargo") if flown.at_turn else None
    legs = {"chunks out": None, "chunks on the way back": None, "used out": None, "used back": None}
    if turned is not None:
        legs["chunks out"] = turned.get("promethium-asteroid-chunk", 0)
        legs["used out"] = {k: stock[k] - turned.get(k, 0) for k in stock if stock[k] != turned.get(k, 0)}
        if flown.back:
            legs["chunks on the way back"] = chunks - legs["chunks out"]
            legs["used back"] = {k: turned.get(k, 0) - flown.cargo.get(k, 0) for k in stock
                                 if turned.get(k, 0) != flown.cargo.get(k, 0)}
    return {**legs, "back": bool(flown.back), "ended": flown.ended or ("back" if flown.back else "out of time"),
            "died at": None if flown.back or not flown.ended else flown.position, "km": flown.odometer,
            "seconds": flown.seconds, "chunks back": chunks if flown.back else 0, "chunks aboard": chunks,
            "left": {k: flown.cargo.get(k, 0) for k in stock}, "first out": first and {"item": first[0], "km": first[1]},
            "ran out": flown.ran_out, "filled": flown.filled,
            "dry turret s": flown.dry, "low fuel s": flown.low_fuel, "brownout s": flown.brownout,
            "out km/s": turn_km / flown.turned if flown.turned else None,
            "back km/s": (flown.odometer - turn_km) / (flown.back - flown.turned) if flown.back else None,
            "deaths": flown.deaths, "rebuilt": flown.rebuilt}


def whole_trip_means(rows: list[dict]) -> dict:
    """The means over whole-trip flights: chunks over every flight (a lost one brings back none),
    what was left over the ones that came back, and which item ran out first how often."""
    from collections import Counter

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    def died(row, kind=None, cause=""):
        # Ours, not asteroids' own deaths: "cargo-bay <- huge-oxide-asteroid".
        return sum(n for k, n in row["deaths"].items() if not k.split(" <- ")[0].endswith("asteroid")
                   and (kind is None or k.split(" <- ")[0] == kind) and k.split(" <- ")[1].startswith(cause))

    back = [r for r in rows if r["back"]]
    return {"flights": len(rows), "came back": len(back) / len(rows), "chunks back": mean(r["chunks back"] for r in rows),
            "chunks per trip back": mean(r["chunks back"] for r in back),
            "cargo bays died": mean(died(r, "cargo-bay") for r in rows),
            "railgun turrets died": mean(died(r, "railgun-turret") for r in rows),
            "killed by huge asteroids": mean(died(r, cause="huge-") for r in rows),
            "km": mean(r["km"] for r in rows), "seconds": mean(r["seconds"] for r in rows),
            "out km/s": mean(r["out km/s"] for r in rows), "back km/s": mean(r["back km/s"] for r in rows),
            "left, came back": {k: mean(r["left"][k] for r in back) for k in (back[0]["left"] if back else {})},
            "first out": dict(Counter(r["first out"]["item"] for r in rows if r["first out"])),
            "dry turret s": mean(r["dry turret s"] for r in rows), "low fuel s": mean(r["low fuel s"] for r in rows),
            "brownout s": mean(r["brownout s"] for r in rows)}


def platform_whole_trip(server, runs: int = 8, stock=None, fold: str = "", shock: int = 0) -> dict:
    """Does the planned trip hold when flown in one go? PROMETHIUM_PLATFORM, readied in Nauvis
    orbit (Flight.fill) with stock (WHOLE_TRIP_STOCK), flies from the solar system edge out to
    WHOLE_TRIP_TURN and back home, `runs` times, with nothing topped up and every chunk kept in
    the hub: per flight whether it came back, the km and time, the chunks it brought, the stock
    left, the first item to run out, turrets dry and thrusters and reactors short. Then the
    trip model's prediction for the same trip, from the rates it was planned on, with the whole
    trip of `fold` (a result file, tools.trip.measured) folded in if given."""
    import inspect
    import json
    import warnings

    from generators import platform
    from tools import trip
    from tools.optimise import CACHE
    from validator.flight import run_flight
    from validator.validate import Report

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data = platform.build(**{k: v for k, v in PROMETHIUM_PLATFORM.items()
                                 if k in inspect.signature(platform.build).parameters}).to_dict()
        [flight] = platform.flight_tests(**PROMETHIUM_PLATFORM, start=0.0, turn=WHOLE_TRIP_TURN, fill=True,
                                         seconds=WHOLE_TRIP_SECONDS, supplies=stock or WHOLE_TRIP_STOCK,
                                         research=WHOLE_TRIP_RESEARCH, shock=shock)
    stock = stock or WHOLE_TRIP_STOCK
    rows = []
    for run in range(runs):
        report = Report(server.version)
        flown = run_flight(server, data, flight, report)
        if flown.error:
            raise RuntimeError(flown.error)
        row = whole_trip_flown(flown, stock)
        rows.append(row)
        out = row["first out"]
        print(f"flight {run + 1}: {row['ended']} after {row['seconds']:,.0f} s and {row['km']:,.0f} km"
              + (f" (died at {row['died at']:.4f} of the route)" if row["died at"] is not None else "")
              + f"; {row['chunks aboard']} chunks aboard ({row['chunks out']} out, {row['chunks on the way back']} back)"
              f"; first out "
              + (f"{out['item']} at {out['km']:,.0f} km" if out else "nothing")
              + f"; dry {row['dry turret s']} turret-s, low fuel {row['low fuel s']} s, brownout {row['brownout s']} s; "
              f"out {row['out km/s'] or 0:.1f} km/s, back {row['back km/s'] or 0:.1f} km/s", file=sys.stderr, flush=True)
    means = whole_trip_means(rows)
    print(f"means: {json.dumps(means)}", file=sys.stderr)
    model = None
    rates_file = CACHE / WHOLE_TRIP_RATES[0]
    if rates_file.exists():
        rates = trip.table(json.loads(rates_file.read_text()), WHOLE_TRIP_RATES[1])
        if fold and (CACHE / fold).exists():
            share, row = trip.measured(json.loads((CACHE / fold).read_text()))
            rates[share] = row
        slots = trip.HUB_SLOTS + trip.BAY_SLOTS * PROMETHIUM_PLATFORM["cargo_bays"]
        planned = trip.trip(rates, 0.019, slots, stock=stock, stay=10_000)
        same = trip.trip(rates, WHOLE_TRIP_TURN, slots, stock=stock)
        model = {name: {"chunks": p["chunks"], "comes back": p["comes back"], "km": p["km"], "hours": p["hours"],
                        "ran out": p["ran out"],
                        "left": {k: stock[k] - p["used"].get(k, 0) for k in stock}}
                 for name, p in (("planned", planned), ("same path", same))}
        print(f"model: {json.dumps(model)}", file=sys.stderr)
    return {"design": PROMETHIUM_PLATFORM, "turn": WHOLE_TRIP_TURN, "supplies": stock,
            "research": WHOLE_TRIP_RESEARCH, "flights": rows, "means": means, "model": model}


def platform_whole_trip_cascade(server, runs: int = 8, stocks=None, shock: int = 0) -> dict:
    """Do more spares stop the cascade that loses whole trips? Each of `stocks` (CASCADE_STOCKS)
    flown as platform-whole-trip, `runs` times in the same run, with `shock` railguns killed as
    it turns: how many came back, the chunks per trip that came back and per trip counting
    losses, and the bays and railguns that died."""
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


# Getting there (NOTES.md): Nauvis to the solar system edge is 145,000 km by Fulgora and Aquilo,
# and the chunks go home to Nauvis orbit, where promethium science is made. platform-legs flies
# the filed platform from Nauvis out to WHOLE_TRIP_TURN and home in one flight.
LEGS_HOME, LEGS_VIA = "nauvis", ["fulgora", "aquilo"]
LEGS_STOCK = CASCADE_STOCKS["full stacks, 20 railguns, 20 bays, 2 reactors"]  # the filed platform's (NOTES.md)
LEGS_SECONDS = 18_000  # at most: 452,000 km at the whole trip's 50 km/s take about 9,000


def leg_rates(rows: list[dict]) -> list[dict]:
    """Per leg, in the order flown (out, the route edge to edge, home), over every flight that got
    onto it: the km of a whole leg (over the flights that finished it), what the hub gave per
    1,000 km over all the km flown on it (a gain is negative; promethium chunks are what came in),
    its km/s, and the trips lost on it per 1,000 km."""
    from tools.trip import CHUNK, DEATHS, SPEED

    edge = "solar-system-edge"
    # The route is the first leg from the edge, whether or not a flight finished it.
    starts = [next((i for i, leg in enumerate(r["legs"]) if leg["from"] == edge), None) for r in rows]
    route = min((i for i in starts if i is not None), default=None)
    out = []
    for i in range(max((len(r["legs"]) for r in rows), default=0)):
        on = [r["legs"][i] for r in rows if len(r["legs"]) > i]
        whole = [leg for leg in on if leg["to"] is not None]
        km = sum(leg["km"] for leg in on)
        used = {}
        for leg in on:
            for k, n in leg["used"].items():
                used[k] = used.get(k, 0) + n
        row = {k: n / km * 1000 for k, n in used.items() if k != CHUNK} if km else {}
        if used.get(CHUNK):
            row[CHUNK] = -used[CHUNK] / km * 1000
        row[SPEED] = km / sum(leg["seconds"] for leg in on) if km else 0.0
        row[DEATHS] = sum(leg["to"] is None for leg in on) / km * 1000 if km else 0.0
        name = whole[0] if whole else on[0]
        out.append({"part": "out" if route is None or i < route else "route" if i == route else "home",
                    "from": name["from"], "to": name["to"], "flights": len(on), "finished": len(whole),
                    "km": sum(leg["km"] for leg in whole) / len(whole) if whole else None, "row": row,
                    "died": {k: sum(leg["died"].get(k, 0) for leg in on) for k in {k for leg in on for k in leg["died"]}}})
    return out


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


def legs_home(rates: list[dict]) -> list[dict]:
    """leg_rates with the way home as the way out reversed (mirrored) when no flight came home to
    measure it; measured legs home stand."""
    if any(r["part"] == "home" for r in rates):
        return rates
    return rates + [{**r, "part": "home", "from": r["to"], "to": r["from"], "mirrored": True}
                    for r in reversed(rates) if r["part"] == "out"]


def legs_model(rates_by_leg: list[dict], stock: dict) -> dict | None:
    """The trip model with the legs before and after the route (the way home mirrored if unmeasured):
    the planned trip (0.019 and a 10,000 km stay) on `stock`, and the best trip that fits with the
    legs, from the rates the plan came from with the whole trip folded in. None if a leg out was
    never finished or the rates aren't cached."""
    import json

    from tools import trip
    from tools.optimise import CACHE

    rates_file = CACHE / WHOLE_TRIP_RATES[0]
    legs = legs_home(rates_by_leg)
    if not rates_file.exists() or any(r["km"] is None for r in legs if r["part"] != "route"):
        return None
    rates = trip.table(json.loads(rates_file.read_text()), WHOLE_TRIP_RATES[1])
    if (CACHE / "platform-whole-trip.result.json").exists():  # the route near the edge, as flown (NOTES.md)
        share, measured_row = trip.measured(json.loads((CACHE / "platform-whole-trip.result.json").read_text()))
        rates[share] = measured_row
    before, after = trip.legs({"leg rates": legs})
    slots = trip.HUB_SLOTS + trip.BAY_SLOTS * PROMETHIUM_PLATFORM["cargo_bays"]
    same = trip.trip(rates, 0.019, slots, stock=stock, stay=10_000, before=before, after=after)
    top, _ = trip.best(rates, slots, [t / 1000 for t in range(1, 51)], stays=(0.0, 10_000.0, 50_000.0),
                       before=before, after=after)
    keys = ("turn", "stay", "chunks", "comes back", "km", "hours", "ran out", "fits", "loaded slots", "used", "largest")
    return {"mirrored home": any(r.get("mirrored") for r in legs), "same trip": {k: same.get(k) for k in keys},
            "best": top and {k: top.get(k) for k in keys}}


def planned_trip(best: dict) -> tuple[float, dict]:
    """A trip model plan as flown: its stay half further out and half back (a platform can't hold
    still on a connection, NOTES.md), and its load in whole items."""
    import math

    from tools.trip import LENGTH

    return (best["turn"] + best["stay"] / 2 / LENGTH,
            {k: math.ceil(v - 1e-9) for k, v in best["used"].items() if math.ceil(v - 1e-9) > 0})


def platform_legs_planned(server, runs: int = 8) -> dict:
    """Does the best trip with the legs hold? The trip model's best from platform-legs' flights with
    fuel by script (legs_model), flown from Nauvis as planned (planned_trip), `runs` times, fuel by
    script: the module can't keep up from Aquilo to the edge (NOTES.md)."""
    import json

    from tools.optimise import CACHE

    legs = json.loads((CACHE / "platform-legs.result.json").read_text())["fuel by script"]
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
    import math

    from draftsman.data import items

    stock = planned["supplies"]

    def stacks(k, n):
        return math.ceil(n / items.raw[k]["stack_size"] - 1e-9)

    most = {k: max(n - f["left"].get(k, 0) for f in planned["flights"]) for k, n in stock.items()}
    out = {**full_stacks(stock), "railgun-turret": 20, "cargo-bay": 20, "fusion-reactor": 2}
    for k in ("uranium-rounds-magazine", "space-platform-foundation"):
        out[k] = stacks(k, most[k] * MARGIN) * items.raw[k]["stack_size"]
    out.pop("railgun-ammo", None)
    room = slots - sum(stacks(k, n) for k, n in out.items())
    return {**out, "railgun-ammo": room * items.raw["railgun-ammo"]["stack_size"]}


def platform_legs_margin(server, runs: int = 8) -> dict:
    """The planned trip with the legs on a load with a margin (margin_stock), fuel by script."""
    import json

    from tools import trip
    from tools.optimise import CACHE

    planned = json.loads((CACHE / "platform-legs-planned.result.json").read_text())
    stock = margin_stock(planned, trip.HUB_SLOTS + trip.BAY_SLOTS * PROMETHIUM_PLATFORM["cargo_bays"])
    return {**platform_legs(server, runs, stock=stock, script_fuel=True, turn=planned["turn"]), "plan": planned["plan"]}


def platform_legs(server, runs: int = 8, stock=None, script_fuel: bool = False, turn: float = WHOLE_TRIP_TURN,
                 design=None) -> dict:
    """What does getting there cost? PROMETHIUM_PLATFORM, readied in Nauvis orbit with stock
    (LEGS_STOCK), flies by Fulgora and Aquilo to the solar system edge, out to WHOLE_TRIP_TURN and
    back, and home to Nauvis the same way, `runs` times, with nothing topped up and every chunk
    kept: per flight as platform-whole-trip, with its legs; per leg (leg_rates); and the trip model
    for the same trip with the measured legs before and after the route, and its best trip with them.
    With script_fuel its thrusters are kept full by script, the module still aboard: what the legs
    cost in stock when fuel can't strand it. design: another platform flown the same way
    (PROMETHIUM_PLATFORM by default); per leg also what its fuel module made (leg_fuel)."""
    import inspect
    import json
    import warnings

    from generators import platform
    from validator.flight import run_flight
    from validator.validate import Report

    stock, design = stock or LEGS_STOCK, design or PROMETHIUM_PLATFORM
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data = platform.build(**{k: v for k, v in design.items()
                                 if k in inspect.signature(platform.build).parameters}).to_dict()
        [flight] = platform.flight_tests(**design, start=0.0, turn=turn, fill=True,
                                         seconds=LEGS_SECONDS, supplies=stock, research=WHOLE_TRIP_RESEARCH,
                                         home=LEGS_HOME, via=LEGS_VIA, script_fuel=script_fuel)
    rows = []
    for run in range(runs):
        flown = run_flight(server, data, flight, Report(server.version))
        if flown.error:
            raise RuntimeError(flown.error)
        row = {**whole_trip_flown(flown, stock), "legs": flown.legs(), "trip km": flown.trip_km,
               "out km/s": None, "back km/s": None}  # the route's own are in its leg
        row["died on"] = None if flown.back or not row["legs"] else (row["legs"][-1]["from"], row["legs"][-1]["to"])
        rows.append(row)
        out = row["first out"]
        print(f"flight {run + 1}: {row['ended']} after {row['seconds']:,.0f} s and {row['trip km']:,.0f} km"
              + (f" (died between {row['died on'][0]} and the next stop)" if row["died on"] else "")
              + f"; {row['chunks aboard']} chunks aboard; first out "
              + (f"{out['item']} at {out['km']:,.0f} km" if out else "nothing")
              + f"; dry {row['dry turret s']} turret-s, low fuel {row['low fuel s']} s; legs "
              + ", ".join(f"{leg['from']}-{leg['to']} {leg['km']:,.0f} km in {leg['seconds']:,.0f} s"
                          for leg in row["legs"]), file=sys.stderr, flush=True)
    means = whole_trip_means(rows)
    rates_by_leg = leg_rates(rows)
    print(f"means: {json.dumps(means)}", file=sys.stderr)
    for r in rates_by_leg:
        print(f"{r['part']} {r['from']}-{r['to']}: {r['finished']}/{r['flights']} finished, "
              f"{json.dumps({k: round(v, 3) for k, v in r['row'].items()})}", file=sys.stderr)
    fuel_by_leg = leg_fuel(rows)
    for r in fuel_by_leg:
        print(f"{r['from']}-{r['to']}: made {json.dumps({k: round(v, 1) for k, v in r['made/s'].items()})} a second, "
              f"held at its end {json.dumps({k: round(v) for k, v in r['held at end'].items()})}", file=sys.stderr)
    model = legs_model(rates_by_leg, stock)
    print(f"model: {json.dumps(model)}", file=sys.stderr)
    return {"design": design, "home": LEGS_HOME, "via": LEGS_VIA, "script fuel": script_fuel,
            "turn": turn,
            "supplies": stock, "research": WHOLE_TRIP_RESEARCH, "flights": rows, "means": means,
            "leg rates": rates_by_leg, "leg fuel": fuel_by_leg, "model": model}


# fuel="advanced" (NOTES.md): the filed platform on the advanced thruster recipes, which need a
# twentieth of the metallic and carbonic chunks; flown from Nauvis on its own fuel as platform-legs
# flew the plain one ("on board"), with the plain one re-flown as the control.
ADVANCED_PLATFORM = {**PROMETHIUM_PLATFORM, "fuel": "advanced"}


def platform_advanced_fuel_margin(server, runs: int = 8) -> dict:
    """The trip platform-legs-margin flew on fuel by script (its load and turn), flown by ADVANCED_PLATFORM
    on its own fuel: whether the advanced recipes take the place of the script's fuel."""
    import json

    from tools import trip
    from tools.optimise import CACHE

    margin = json.loads((CACHE / "platform-legs-margin.result.json").read_text())
    return {**platform_legs(server, runs, stock=margin["supplies"], turn=margin["turn"], design=ADVANCED_PLATFORM),
            "plan": margin["plan"]}


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
           "platform-advanced-fuel-margin": platform_advanced_fuel_margin}
