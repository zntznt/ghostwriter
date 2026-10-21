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


STUDIES = {"belt-fill": belt_fill, "platform-shape": platform_shape, "platform-research": platform_research,
           "platform-promethium": platform_promethium, "platform-belt": platform_belt, "platform-front": platform_front,
           "platform-wide": lambda server: platform_belt(server, WIDE_SPACE, WIDE_START, seconds=600)}
