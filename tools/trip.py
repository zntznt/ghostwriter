"""A promethium trip from measured rates: where the platform should turn round.

    python -m tools.trip [RESULT] [--bays N ...]   # RESULT: .optimise-cache/platform-route.result.json

The platform flies from the solar system edge out to `turn` (a share of the route)
and back, using foundation, ammo, fusion cells and spares at the rates the
`platform-route` study measured per 1,000 km at each start (linear between them),
and bringing in promethium chunks. Everything it uses is cargo, in the hub's slots
by stack size, and so is every chunk: a chunk stacks to 1. What it uses frees slots
for chunks, and a chunk that comes in with no slot free is lost (the chunk belt
carries it into space). A trip loads exactly what it will use, unless given a stock.
"""

import argparse
import json
import math
import sys
from pathlib import Path

from draftsman.data import items

ROOT = Path(__file__).resolve().parent.parent
RESULT = ROOT / ".optimise-cache" / "platform-route.result.json"
LENGTH = 4_000_000  # km, the solar system edge to the shattered planet (dump)
HUB_SLOTS, BAY_SLOTS = 59, 20  # the hub's inventory_size, and each cargo bay's inventory_size_bonus (dump)
CHUNK = "promethium-asteroid-chunk"
SPEED = "km/s"
DEATHS = "deaths"  # hubs lost per 1,000 km flown: the chance of coming back
PACKS_PER_CHUNK = 10 / 25  # promethium science: 25 chunks, a quantum processor and 10 biter eggs make 10 packs


def table(result: dict, mode: str = "out") -> dict[float, dict[str, float]]:
    """{start: {item: per 1,000 km, ..., CHUNK: brought in per 1,000 km, SPEED: cruise, DEATHS: hubs
    lost per 1,000 km}} from a platform-route result's flights of `mode`. What the hub rebuilt and
    what the script supplied (ammo) are both cargo; fluids aren't, so they're left out."""
    out = {}
    for t in result["trials"]:
        if t["mode"] != mode or not t["passed"]:
            continue
        row = {}
        for key, v in t["rates"].items():
            if not key.endswith("/1000 km"):
                continue
            name = key[: -len("/1000 km")]
            if name.startswith("rebuilt "):
                name = name[len("rebuilt "):]
                row[name] = row.get(name, 0.0) + v
            elif name.startswith("taken "):
                row[name[len("taken "):]] = v
            elif name == "fusion cells":
                row["fusion-power-cell"] = v
            elif name in items.raw:
                row[name] = row.get(name, 0.0) + v
        row[SPEED] = t["rates"]["cruise km/s"]
        # The flights that died, over all the km flown: (1 - survived) * runs / (km_mean * runs).
        row[DEATHS] = (1 - t["rates"]["survived"]) / t["rates"]["km_mean"] * 1000
        out[t["start"]] = row
    return out


def at(rates: dict[float, dict[str, float]], share: float) -> dict[str, float]:
    """The rates at a share of the route, linear between the measured starts."""
    starts = sorted(rates)
    if not starts[0] <= share <= starts[-1]:
        raise ValueError(f"{share} is outside the measured route ({starts[0]} to {starts[-1]})")
    hi = next(s for s in starts if s >= share)
    lo = max(s for s in starts if s <= share)
    w = (share - lo) / (hi - lo) if hi > lo else 0.0
    keys = set(rates[lo]) | set(rates[hi])
    return {k: rates[lo].get(k, 0.0) * (1 - w) + rates[hi].get(k, 0.0) * w for k in keys}


def trip(rates: dict, turn: float, slots: int, stock: dict | None = None, stay: float = 0.0, step: float = 1000.0,
         length: float = LENGTH, aboard=()) -> dict:
    """Out to `turn`, `stay` km flown there, and back, in steps of `step` km. Returns what it
    flew, used and loaded (nothing of what's made `aboard`); whether the load fits the hub's
    `slots`; the chunks it brings back and lost for want of room; the chance it comes back at
    the measured deaths per 1,000 km; and, with a given stock, the first item to run out
    (item, km into the trip, share of the route) or None."""
    out = [(k + 0.5) * step / length for k in range(round(turn * length / step))]
    path = out + [turn] * round(stay / step) + out[::-1]
    use = [at(rates, x) for x in path]
    need = {}
    for r in use:
        for k, v in r.items():
            if k not in (CHUNK, SPEED, DEATHS) and k not in aboard:
                need[k] = need.get(k, 0.0) + v * step / 1000
    loaded = dict(stock) if stock is not None else {k: math.ceil(v - 1e-9) for k, v in need.items()}
    stacks = {k: items.raw[k]["stack_size"] for k in set(need) | set(loaded)}

    def slots_for(left):
        return sum(math.ceil(n / stacks[k] - 1e-9) for k, n in left.items() if n > 0)

    plan = {"turn": turn, "stay": stay, "km": len(path) * step, "hours": sum(step / r[SPEED] for r in use) / 3600,
            "used": need, "slots": slots, "loaded slots": slots_for(loaded),
            "comes back": math.exp(-sum(r.get(DEATHS, 0.0) for r in use) * step / 1000)}
    if plan["loaded slots"] > slots:
        return {**plan, "fits": False, "largest": max(loaded, key=lambda k: math.ceil(loaded[k] / stacks[k]))}
    left, chunks, lost, ran_out = dict(loaded), 0.0, 0.0, None
    for i, (x, r) in enumerate(zip(path, use)):
        for k in need:
            left[k] = left.get(k, 0.0) - r.get(k, 0.0) * step / 1000
            if left[k] < -1e-9 and ran_out is None:
                ran_out = (k, (i + 1) * step, x)
        came = r.get(CHUNK, 0.0) * step / 1000
        kept = max(0.0, min(came, slots - slots_for(left) - chunks))
        chunks, lost = chunks + kept, lost + came - kept
    return {**plan, "fits": True, "chunks": chunks, "lost": lost, "ran out": ran_out}


def best(rates: dict, slots: int, turns, stays=(0.0,), **kw) -> tuple[dict | None, list[dict]]:
    """The trip whose load fits that brings back the most chunks, weighed by the chance it comes
    back, and every one tried."""
    rows = [trip(rates, turn, slots, stay=stay, **kw) for turn in turns for stay in stays]
    fitting = [r for r in rows if r["fits"]]
    return (max(fitting, key=lambda r: r["chunks"] * r["comes back"]) if fitting else None), rows


SHOW = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.4)  # the turns printed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.trip", description=__doc__.splitlines()[0])
    ap.add_argument("result", nargs="?", default=str(RESULT))
    ap.add_argument("--bays", type=int, nargs="+", default=[8], help="cargo bays (20 hub slots each)")
    ap.add_argument("--aboard", default="", help="items made on board, not loaded: a,b")
    args = ap.parse_args(argv)
    rates = table(json.loads(Path(args.result).read_text()))
    aboard = {a for a in args.aboard.split(",") if a}
    # Fine near the edge, where short trips are all that fit; coarser out to the furthest start measured.
    turns = [t / 1000 for t in range(1, 51)] + [t / 100 for t in range(6, round(max(rates) * 100) + 1)]
    for bays in args.bays:
        slots = HUB_SLOTS + BAY_SLOTS * bays
        top, rows = best(rates, slots, turns, stays=(0.0, 10_000.0, 50_000.0), aboard=aboard)
        print(f"{bays} cargo bays ({slots} slots){', made aboard: ' + ', '.join(sorted(aboard)) if aboard else ''}:")
        for r in rows:
            if r["stay"] == 0.0 and r["turn"] in SHOW:
                print(f"  turn {r['turn']:g}: {r['km']:,.0f} km, {r['hours']:.1f} h, loads {r['loaded slots']:,} slots"
                      + (f", {r['chunks']:,.0f} chunks back, comes back {r['comes back']:.0%}" if r["fits"] else
                         f": doesn't fit ({r['largest']} the most)"))
        if top:
            used = ", ".join(f"{k} {v:,.0f}" for k, v in sorted(top["used"].items(), key=lambda kv: -kv[1])[:6])
            print(f"  best: turn {top['turn']:g}, stay {top['stay']:,.0f} km: {top['km']:,.0f} km, {top['hours']:.1f} h, "
                  f"{top['chunks']:,.0f} chunks back ({top['chunks'] * PACKS_PER_CHUNK:,.0f} packs), {top['lost']:,.0f} "
                  f"lost for room, comes back {top['comes back']:.0%}; loads {top['loaded slots']:,} of {slots:,} "
                  f"slots, most {used}")
        else:
            print("  best: none fits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
