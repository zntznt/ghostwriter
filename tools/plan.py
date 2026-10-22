"""Plan a promethium trip for a platform design: measure it in flights, then the trip model.

    python -m tools.bp plan platform [key=value ...] [--from edge|nauvis] [--runs N] [--json] [--verify N]

Any design the generator builds: it is checked and validated in game, flown at a few starts
along the route (and with --from nauvis, by its legs from Nauvis and home) with its hub held at
a stock (validator.flight.Flight.hold), so each flight measures what it uses and brings in per
1,000 km and capacity is left to the trip model (tools.trip), which finds the trip that brings
back the most chunks in the hub's slots: where to turn, what to load, what to do before leaving.
"""

import math

from draftsman.data import items

# What the flights assume a player has researched: platform-research's best row for railguns
# and bullets (NOTES.md), and the same for rockets and lasers: their damage series to 10, as
# physical projectile damage is, and laser shooting speed to its last level. Rockets' shooting
# speed comes with weapon shooting speed 6 (+340%).
HIGH_RESEARCH = {"railgun-shooting-speed": 6, "railgun-damage": 2, "physical-projectile-damage": 10,
                 "weapon-shooting-speed": 6}
MIX_RESEARCH = {**HIGH_RESEARCH, "stronger-explosives": 10, "laser-weapons-damage": 10, "laser-shooting-speed": 7}


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
