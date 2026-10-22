"""Plan a promethium trip for a platform design: measure it in flights, then the trip model.

    python -m tools.bp plan platform [key=value ...] [--from edge|nauvis] [--runs N] [--json] [--verify N]

Any design the generator builds: it is checked and validated in game, flown at a few starts
along the route (and with --from nauvis, by its legs from Nauvis and home) with its hub held at
a stock (validator.flight.Flight.hold), so each flight measures what it uses and brings in per
1,000 km and capacity is left to the trip model (tools.trip), which finds the trip that brings
back the most chunks in the hub's slots: where to turn, what to load, what to do before leaving.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

from draftsman.data import entities, items

from tools import trip
from tools.library import PLAN_SUFFIX, is_entry, plan_path

# What the flights assume a player has researched: platform-research's best row for railguns
# and bullets (NOTES.md), and the same for rockets and lasers: their damage series to 10, as
# physical projectile damage is, and laser shooting speed to its last level. Rockets' shooting
# speed comes with weapon shooting speed 6 (+340%).
HIGH_RESEARCH = {"railgun-shooting-speed": 6, "railgun-damage": 2, "physical-projectile-damage": 10,
                 "weapon-shooting-speed": 6}
MIX_RESEARCH = {**HIGH_RESEARCH, "stronger-explosives": 10, "laser-weapons-damage": 10, "laser-shooting-speed": 7}
# The platform designs library/platforms/ holds, as the generator builds them (tests hold the library
# to them; NOTES.md has how each was chosen): the 32-railgun platform of platform-foundation, and the
# promethium platform of platform-bays with the hub feeding its turrets (platform-feed). Here, not in
# generators/, which the optimise cache fingerprints.
FILED_PLATFORM = {"railguns": 32, "guns": 32, "thrusters": 1, "cargo_bays": 8}
PROMETHIUM_PLATFORM = {"railguns": 16, "guns": 16, "thrusters": 1, "cargo_bays": 24, "fuel": True, "reserve": True,
                       "power": 2, "promethium": True, "ammo_feed": True}


def full_stacks(stock: dict) -> dict:
    """Every item rounded up to whole stacks: a hub slot holds a stack, so it costs no slots."""
    return {k: -(-n // items.raw[k]["stack_size"]) * items.raw[k]["stack_size"] for k, n in stock.items()}


def planned_trip(best: dict) -> tuple[float, dict]:
    """A trip model plan as flown: its stay half further out and half back (a platform can't hold
    still on a connection, NOTES.md), and its load in whole items."""
    from tools.trip import LENGTH

    return (best["turn"] + best["stay"] / 2 / LENGTH,
            {k: math.ceil(v - 1e-9) for k, v in best["used"].items() if math.ceil(v - 1e-9) > 0})


def trip_flown(flown, stock: dict, turn: float) -> dict:
    """One round-trip flight (out to `turn` of the route and back) in numbers. A flight that didn't
    come back brings back no chunks."""
    turn_km = turn * 4_000_000
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


def trip_means(rows: list[dict]) -> dict:
    """The means over round-trip flights: chunks over every flight (a lost one brings back none),
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


def legs_home(rates: list[dict]) -> list[dict]:
    """leg_rates with the way home as the way out reversed (mirrored) when no flight came home to
    measure it; measured legs home stand."""
    if any(r["part"] == "home" for r in rates):
        return rates
    return rates + [{**r, "part": "home", "from": r["to"], "to": r["from"], "mirrored": True}
                    for r in reversed(rates) if r["part"] == "out"]


# What a plan measures and searches (NOTES.md): the starts the studies' trip plans came from, 600 s
# flights, and from Nauvis the chain of legs to the solar system edge and home.
STARTS = (0.0, 0.1, 0.2)
SECONDS = 600
HOME, VIA = "nauvis", ("fulgora", "aquilo")
LEGS_TURN = 0.001  # a flight that measures the legs turns at once on the route
LEGS_SECONDS = 18_000  # at most: 452,000 km at 50 km/s take about 9,000
FOUNDATION = "space-platform-foundation"
VERIFY_SECONDS = 1800  # a trip flown whole gets three times its planned time, and at least this


def slots(stock: dict) -> int:
    """Hub slots a stock fills, each item over its stack size."""
    return sum(math.ceil(n / items.raw[k]["stack_size"] - 1e-9) for k, n in stock.items() if n > 0)


def capacity(design: dict) -> int:
    """The hub's slots and its cargo bays'."""
    return trip.HUB_SLOTS + trip.BAY_SLOTS * design.get("cargo_bays", 0)


def cannot_plan(design: dict) -> str | None:
    """Why a design can't make a promethium trip, or None."""
    if not design.get("promethium"):
        return ("it doesn't keep promethium: promethium=True (which needs fuel=True) has its collectors take "
                "the chunks and send them to the hub")
    return None


def research_for(design: dict, override: dict | None = None) -> dict:
    """The research the flights assume, unless given: the studies' (MIX_RESEARCH) with
    construction-robotics, for the hub to rebuild, and the feed's for a hub that feeds its turrets."""
    from generators.platform import FEED_RESEARCH

    if override is not None:
        return dict(override)
    return {**MIX_RESEARCH, "construction-robotics": 1, **(FEED_RESEARCH if design.get("ammo_feed") else {})}


def measure_stock(data: dict, design: dict) -> dict:
    """What a measurement flight holds in the hub (Flight.hold): a stack of every part the blueprint
    has, to rebuild from, and of foundation; with ammo_feed the ammo to load every turret it feeds;
    with power the fusion cells. Held, it never runs out, whatever the design uses."""
    from generators.platform import CELLS, FED

    names = Counter(e["name"] for e in data["blueprint"]["entities"])
    stock = {n: items.raw[n]["stack_size"] for n in names if n in items.raw}
    stock[FOUNDATION] = items.raw[FOUNDATION]["stack_size"]
    if design.get("ammo_feed"):
        for turret, ammo in FED.items():
            if names[turret]:
                stock[ammo] = full_stacks({ammo: names[turret] * entities.raw[turret]["automated_ammo_count"]})[ammo]
    if design.get("power"):
        stock["fusion-power-cell"] = CELLS
    if slots(stock) > capacity(design):
        raise ValueError(f"the hub can't hold a stack of each of its {len(names)} kinds of part to rebuild from "
                         f"({slots(stock)} slots of {capacity(design)}): give it cargo bays")
    return stock


def readying(design: dict) -> list[str]:
    """What a player does before leaving, from the design's options."""
    bays = design.get("cargo_bays", 0)
    steps = ["Research construction-robotics: the hub rebuilds what asteroids destroy from its stock.",
             f"Load the hub{f' and its {bays} cargo bays' if bays else ''} with the stock listed."]
    fuel = design.get("fuel")
    if fuel:
        recipes = "advanced-asteroid-processing" if fuel == "advanced" else "space-platform-thruster"
        steps.append(f"The fuel module makes thruster fuel and oxidizer from chunks (research {recipes}); standing "
                     "still it meets too few to start, so leave with the thruster's line full.")
        if design.get("reserve"):
            steps.append("Fill the reserve tanks with thruster fuel and oxidizer before leaving: in orbit the "
                         "module fills them slowly (NOTES.md).")
    else:
        steps.append("Supply thruster fuel and oxidizer: this design makes none (its flights were fuelled by script).")
    if design.get("power"):
        steps.append("Fill each fusion reactor with fusion power cells and its loop with cold fluoroketone, and "
                     "charge the accumulator and the railguns: a reactor needs power to start (research "
                     "cryogenic-plant).")
    else:
        steps.append("Supply power: this design makes none (its flights were powered by script).")
    if design.get("ammo_feed"):
        steps.append("Ready it in orbit with the hub stocked: its feed fills the belts and turrets from the hub, so "
                     "top the hub back up before leaving.")
    else:
        steps.append("Load the turrets: this design doesn't feed them from the hub (its flights stocked them by "
                     "script, which a player can't do on the way).")
    return steps


def caveats(design: dict, origin: str) -> list[str]:
    """What the plan can't see, from what NOTES records of plans flown whole."""
    out = ["The trip model reads its rates in straight lines between the starts measured; flown whole, a "
           "planned trip brought back more chunks and used less foundation near the edge than planned (NOTES.md).",
           "Flights of 600 s lose little; over a whole trip losses compound, and a load sized to average use did "
           "worse flown whole (NOTES.md). Check it with --verify, and carry spares in whole stacks."]
    if origin == "edge":
        out.append("The trip starts and ends at the solar system edge: getting there from Nauvis and home again "
                   "isn't in it (--from nauvis).")
    by_script = [what for what, key in (("fuel", "fuel"), ("power", "power"), ("ammo", "ammo_feed"))
                 if not design.get(key)]
    if by_script:
        out.append(f"Flown with its {', '.join(by_script)} by script: not a trip a player can make as it is.")
    return out


def assemble(design: dict, origin: str, research: dict, runs: int, rows: list[dict], legs: list[dict] | None = None):
    """A plan from measured flights: rows at starts along the route (tools.trip.table's trials) and,
    from Nauvis, the legs' rates (leg_rates). The trip that brings back the most chunks in the hub's
    slots (tools.trip.best), what to load in whole stacks, what binds it, and what to do before
    leaving; or why there is none (error)."""
    rates = trip.table({"trials": rows}, "plan")
    measured = {start: {"lived": next(r["rates"]["survived"] for r in rows if r["start"] == start),
                        "cruise km/s": row[trip.SPEED],
                        "hub slots per 1,000 km": sum(trip.slots(row).values()),
                        "chunks per 1,000 km": row.get(trip.CHUNK, 0.0)} for start, row in rates.items()}
    plan = {"design": design, "from": origin, "research": research, "runs": runs, "measured": measured,
            "legs": legs and [{k: leg[k] for k in ("part", "from", "to", "km", "finished", "flights")} for leg in legs],
            "trip": None, "limit": None, "stock": [], "readying": readying(design), "caveats": caveats(design, origin),
            "error": None}
    failed = [r for r in rows if not r["passed"]]
    if failed or not rates:
        plan["error"] = f"its flights failed: {failed[0]['error'] if failed else 'nothing measured'}"
        return plan
    before, after = (), ()
    if legs is not None:
        stuck = [leg for leg in legs if leg["part"] == "out" and leg["km"] is None]
        if stuck:
            plan["error"] = (f"it can't get there: no flight finished the leg from {stuck[0]['from']} "
                             f"({stuck[0]['flights']} got onto it)")
            return plan
        before, after = trip.legs({"leg rates": legs_home(legs)})
    room = capacity(design)
    top, tried = trip.best(rates, room, trip.turns(max(rates)), stays=trip.STAYS, before=before, after=after)
    if top is None:
        shortest = min(tried, key=lambda r: (r["turn"], r["stay"]))
        plan["error"] = (f"no trip fits in the hub's {room} slots: the shortest loads {shortest['loaded slots']:,} "
                         f"({shortest['largest']} the most)")
        return plan
    turn, load = planned_trip(top)
    stock = full_stacks(load)
    plan["stock"] = sorted(({"item": k, "count": n, "slots": slots({k: n})} for k, n in stock.items()),
                           key=lambda s: (-s["slots"], s["item"]))
    plan["trip"] = {"turn": top["turn"], "stay": top["stay"], "flown turn": turn, "km": top["km"], "hours": top["hours"],
                    "chunks": top["chunks"], "packs": top["chunks"] * trip.PACKS_PER_CHUNK,
                    "comes back": top["comes back"], "lost for room": top["lost"], "loaded slots": slots(stock),
                    "slots": room}
    longer = [r for r in tried if r["fits"] and r["turn"] > top["turn"]]
    plan["limit"] = {"by": "the chance of coming back" if longer else "hub slots",
                     "largest": plan["stock"][0]["item"], "slots": plan["stock"][0]["slots"]}
    return plan


def text(plan: dict) -> str:
    """A plan a player can act on."""
    d = plan["design"]
    lines = [f"Plan for platform {' '.join(f'{k}={v}' for k, v in d.items())}, from "
             f"{'Nauvis' if plan['from'] == 'nauvis' else 'the solar system edge'} ({plan['runs']} flights a start at "
             f"{', '.join(f'{s:g}' for s in plan['measured'])} of the route)"]
    for s, m in plan["measured"].items():
        lines.append(f"  measured at {s:g}: lived {m['lived']:.0%}, {m['cruise km/s']:.1f} km/s, "
                     f"{m['hub slots per 1,000 km']:.2f} hub slots and {m['chunks per 1,000 km']:.2f} chunks per 1,000 km")
    for leg in plan["legs"] or []:
        lines.append(f"  leg {leg['from']} to {leg['to'] or '(not reached)'}: finished {leg['finished']} of "
                     f"{leg['flights']}" + (f", {leg['km']:,.0f} km" if leg["km"] else ""))
    t = plan["trip"]
    if t is None:
        lines.append(f"No trip: {plan['error']}.")
    else:
        lines += [f"Best trip: turn at {t['turn']:g} of the route, stay {t['stay']:,.0f} km: {t['km']:,.0f} km, "
                  f"{t['hours']:.1f} h; {t['chunks']:,.0f} chunks back ({t['packs']:,.0f} packs), comes back "
                  f"{t['comes back']:.0%}",
                  f"Limit: {plan['limit']['by']} ({t['loaded slots']} of {t['slots']} loaded); "
                  f"{plan['limit']['largest']} fills the most ({plan['limit']['slots']} slots)",
                  "Load the hub:"]
        lines += [f"  {s['item']:<32} {s['count']:>7,}  {s['slots']:>3} slots" for s in plan["stock"]]
    lines.append("Research: " + ", ".join(f"{k} {v}" for k, v in plan["research"].items()))
    lines.append("Before leaving:")
    lines += [f"  - {s}" for s in plan["readying"]]
    lines.append("Caveats:")
    lines += [f"  - {c}" for c in plan["caveats"]]
    return "\n".join(lines)


def compare(plan: dict, means: dict) -> dict:
    """The plan against its trip flown whole (trip_means)."""
    t = plan["trip"]
    return {"planned": {"chunks": t["chunks"], "comes back": t["comes back"]},
            "flown": {"flights": means["flights"], "came back": means["came back"], "chunks a trip": means["chunks back"],
                      "chunks a trip back": means["chunks per trip back"], "first out": means["first out"]}}


def compare_text(check: dict) -> str:
    p, f = check["planned"], check["flown"]
    first = ", ".join(f"{k} in {n}" for k, n in f["first out"].items()) or "nothing"
    return (f"Planned: {p['chunks']:,.0f} chunks a trip, comes back {p['comes back']:.0%}. Flown whole "
            f"({f['flights']} flights): came back {f['came back']:.0%}, {f['chunks a trip'] or 0:,.0f} chunks a trip "
            f"counting losses ({f['chunks a trip back'] or 0:,.0f} a trip back); first to run out: {first}.")


def measure(server, generator: str, design: dict, research: dict, stock: dict, runs: int, starts=STARTS,
            seconds: float = SECONDS) -> list[dict]:
    """The design flown `runs` times from each of starts for `seconds`, its hub held at stock and the
    chunks it keeps taken from it as they come (Flight.hold, Flight.take), through the optimise cache:
    rows for tools.trip.table, what it used and brought in per 1,000 km."""
    from tools.optimise import FlightEvaluator

    evaluate = FlightEvaluator(server, generator, runs=runs)
    rows = []
    for start in starts:
        t = evaluate({**design, "start": start, "seconds": seconds, "supplies": stock, "research": research,
                      "take": [trip.CHUNK], "hold": sorted(stock)})
        rows.append({"start": start, "mode": "plan", "passed": t.passed, "rates": t.rates, "counts": t.counts,
                     "error": t.error})
        print(f"measured at {start:g}: " + (f"lived {t.rates['survived']:.0%}, {t.rates.get('cruise km/s', 0):.1f} km/s"
                                            if t.passed else f"FAIL {t.error}"), file=sys.stderr, flush=True)
    return rows


def _trips(server, generator: str, runs: int):
    """Round trips of a generator's design, `runs` flights per parameters, cached as trials are
    (tools.optimise.GameEvaluator): each flight as trip_flown has it, with its legs."""
    import inspect
    import json
    import warnings

    from tools.optimise import CACHE, GameEvaluator, Trial
    from validator.flight import run_flight
    from validator.validate import Report

    class Trips(GameEvaluator):
        def __init__(self):
            super().__init__(server, generator)
            self.path = CACHE / f"{generator}-trips.json"
            stored = json.loads(self.path.read_text()) if self.path.exists() else {}
            self.cache = stored.get(self.code, {})

        def __call__(self, params):
            return super().__call__({**params, "runs": runs})

        def _run(self, params):
            params = {k: v for k, v in params.items() if k != "runs"}
            shape = {k: v for k, v in params.items() if k in inspect.signature(self.module.build).parameters}
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = self.module.build(**shape).to_dict()
                [flight] = self.module.flight_tests(**params)
            flights = []
            for _ in range(runs):
                flown = run_flight(server, data, flight, Report(server.version))
                if flown.error:
                    return Trial(params, False, error=flown.error)
                flights.append({**trip_flown(flown, params.get("supplies") or {}, params["turn"]), "legs": flown.legs()})
                print(f"trip {len(flights)}: {flights[-1]['ended']} after {flown.seconds:,.0f} s", file=sys.stderr,
                      flush=True)
            return Trial(params, True, {"flights": flights})

    return Trips()


def measure_legs(server, generator: str, design: dict, research: dict, stock: dict, runs: int) -> list[dict]:
    """The legs from Nauvis to the solar system edge and home (VIA), flown `runs` times with the hub
    held at stock and turning at once on the route: leg_rates, each leg's use from the flight's
    counters (a held hub's cargo doesn't move) and the chunks it brought from its cargo."""
    t = _trips(server, generator, runs)({**design, "turn": LEGS_TURN, "home": HOME, "via": list(VIA),
                                         "fill": bool(design.get("ammo_feed")), "seconds": LEGS_SECONDS,
                                         "supplies": stock, "research": research, "hold": sorted(stock)})
    if not t.passed:
        raise ValueError(f"the legs couldn't be flown: {t.error}")
    for f in t.rates["flights"]:
        for leg in f["legs"]:
            chunks = leg["used"].get(trip.CHUNK)
            leg["used"] = {k: n for k, n in leg.get("counted", {}).items() if k in items.raw}
            if chunks:
                leg["used"][trip.CHUNK] = chunks
    return leg_rates(t.rates["flights"])


def verify(server, generator: str, plan: dict, runs: int) -> dict:
    """The planned trip flown whole `runs` times on its stock, nothing held: compare against the plan."""
    design, t = plan["design"], plan["trip"]
    stock = {s["item"]: s["count"] for s in plan["stock"]}
    home = {"home": HOME, "via": list(VIA)} if plan["from"] == "nauvis" else {}
    flights = _trips(server, generator, runs)({**design, **home, "turn": t["flown turn"],
                                               "fill": bool(design.get("ammo_feed")),
                                               "seconds": math.ceil(max(VERIFY_SECONDS, 3 * t["hours"] * 3600)), "supplies": stock,
                                               "research": plan["research"]})
    if not flights.passed:
        raise ValueError(f"the trip couldn't be flown: {flights.error}")
    return compare(plan, trip_means(flights.rates["flights"]))


def build(generator: str, design: dict) -> dict:
    """The design's blueprint, from the generator's build (flight-only keys left out)."""
    import importlib
    import inspect
    import warnings

    module = importlib.import_module(f"generators.{generator}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return module.build(**{k: v for k, v in design.items()
                               if k in inspect.signature(module.build).parameters}).to_dict()


def plan(server, generator: str, design: dict, origin: str = "edge", runs: int = 8, research: dict | None = None,
         starts=None, seconds: float | None = None, data: dict | None = None) -> dict:
    """Measure the design and plan its trip (assemble); data is its blueprint, built if not given.
    starts and seconds default to STARTS and SECONDS. The plan names its generator and the game
    it was measured on, for filing it (save)."""
    reason = cannot_plan(design)
    if reason:
        raise ValueError(f"no promethium trip: {reason}")
    if data is None:
        data = build(generator, design)
    research = research_for(design, research)
    stock = measure_stock(data, design)
    rows = measure(server, generator, design, research, stock, runs, STARTS if starts is None else starts,
                   SECONDS if seconds is None else seconds)
    legs = measure_legs(server, generator, design, research, stock, runs) if origin == "nauvis" else None
    return {**assemble(design, origin, research, runs, rows, legs), "generator": generator, "game": server.version}


def _filed(path: Path) -> dict:
    data = json.loads(path.read_text())
    data.pop("index", None)  # a book's child keeps its place
    return data


def save(plan: dict, data: dict, library: Path, target: str) -> Path:
    """File a plan beside the library entry that is its design's blueprint (data), as NAME.plan.json:
    only a plan with a trip, measured on the game the library targets."""
    if plan.get("error") or not plan.get("trip"):
        raise ValueError(f"no trip to file: {plan.get('error') or 'none planned'}")
    if plan.get("game") != target:
        raise ValueError(f"planned on Factorio {plan.get('game')}, but the library targets {target}")
    for path in sorted(filter(is_entry, library.rglob("*.json"))):
        if _filed(path) == data:
            out = plan_path(path)
            out.write_text(json.dumps(plan, indent=1) + "\n")
            return out
    raise ValueError("no library entry is this design's blueprint: file it first (bp gen ... | bp add -)")


def problems(library: Path, target: str) -> list[str]:
    """Every filed plan that no longer holds: no blueprint beside it, a design that no longer builds
    that blueprint, or measured on another game than the library targets."""
    out = []
    for path in sorted(library.rglob("*" + PLAN_SUFFIX)):
        name = path.relative_to(library).as_posix()
        p = json.loads(path.read_text())
        blueprint = path.with_name(path.name[: -len(PLAN_SUFFIX)] + ".json")
        if not blueprint.is_file():
            out.append(f"{name}: no {blueprint.name} beside it")
            continue
        if p.get("game") != target:
            out.append(f"{name}: measured on Factorio {p.get('game')}, but the library targets {target}: plan it again")
        if build(p["generator"], p["design"]) != _filed(blueprint):
            out.append(f"{name}: its design no longer builds {blueprint.name}: plan it again (bp plan ... --save)")
    return out
