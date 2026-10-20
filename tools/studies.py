"""Studies for tools.optimise: questions about the generators, answered in game.

Each study takes a running server and returns a JSON-able result, printing a
table as it goes. Its books and values are fixed in code before it runs.
"""

import sys

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


STUDIES = {"belt-fill": belt_fill}
