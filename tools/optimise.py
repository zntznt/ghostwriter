"""Deterministic search over a generator's parameters, scored by the game.

    python -m tools.optimise STUDY      # a study from tools/studies.py: runs, prints a table, saves JSON

The game is the scoring function. A trial builds the generator's blueprint
with some parameters, runs its flow tests in a headless server, and keeps
what they measured (validator.flow.Measured). Factorio's simulation is
deterministic, so a trial's result depends only on its parameters and the
code: trials are cached under .optimise-cache/, keyed by both, and a study
can be stopped and run again without repeating work.

Searches take any `evaluate(params) -> Trial`, so they're tested without the game:

- grid: every combination of the given values.
- bisect: along one ordered parameter, the last value that passes and the
  first that fails, for a yes/no that changes once.
- descend: coordinate descent over ordered values, to the best score among
  trials that pass (with a margin for noisy scores, such as flights).
"""

import argparse
import hashlib
import importlib
import inspect
import itertools
import json
import sys
import warnings
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".optimise-cache"
# What a trial's result depends on besides its parameters: generators and the
# tools they use, and the flow test harness on both sides.
SOURCES = ("generators/*.py", "tools/rates.py", "tools/layout.py", "tools/pipes.py", "tools/inserter_rates.json",
           "validator/flow.py", "validator/scenario/control.lua")


@dataclass
class Trial:
    params: dict
    passed: bool
    rates: dict[str, float] = field(default_factory=dict)  # per second, the last window measured
    windows: int = 0  # how many were measured
    stalled: dict[str, int] = field(default_factory=dict)
    error: str = ""  # the build refused, or the test couldn't run


def fingerprint(patterns=SOURCES, root=ROOT) -> str:
    digest = hashlib.sha256()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


class GameEvaluator:
    """Trials of one generator in one server, cached per parameters and code."""

    def __init__(self, server, generator: str, cache_dir: Path = CACHE):
        self.server = server
        self.generator = generator
        self.module = importlib.import_module(f"generators.{generator}")
        self.path = cache_dir / f"{generator}.json"
        self.code = fingerprint()
        stored = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.cache = stored.get(self.code, {})
        self.ran = 0  # trials run (not from the cache)

    def __call__(self, params: dict) -> Trial:
        key = json.dumps(params, sort_keys=True)
        if key in self.cache:
            return Trial(**self.cache[key])
        trial = self._run(params)
        self.ran += 1
        self.cache[key] = asdict(trial)
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps({self.code: self.cache}, indent=1))
        return trial

    def _run(self, params: dict) -> Trial:
        from validator.flow import run_flow_tests
        from validator.validate import Report

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                bp = self.module.build(**params)
                flows = self.module.flow_tests(**params)
            except ValueError as e:  # the generator refused: a failed trial, not a crash
                return Trial(params, False, error=str(e))
        ids = {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
        report = Report(self.server.version)
        measured = run_flow_tests(self.server, bp.to_dict(), flows, ids, report)
        first = measured[0] if measured else None
        return Trial(params, report.ok, dict(first.windows[-1]) if first and first.windows else {},
                     len(first.windows) if first else 0, first.stalled if first else {},
                     "; ".join(report.errors) if not report.ok else "")


class FlightEvaluator(GameEvaluator):
    """Trials of a platform generator: each flies `runs` flights (validator.flight)
    and scores them: km_mean, km_min, km_max, seconds_mean, survived (the share
    that outlived the flight's time)."""

    def __init__(self, server, generator: str, runs: int = 4, cache_dir: Path = CACHE):
        super().__init__(server, generator, cache_dir)
        self.runs = runs
        self.path = cache_dir / f"{generator}-flights.json"
        stored = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.cache = stored.get(self.code, {})

    def __call__(self, params: dict) -> Trial:
        return super().__call__({**params, "runs": self.runs})

    def _run(self, params: dict) -> Trial:
        from validator.flight import run_flight
        from validator.validate import Report

        params = {k: v for k, v in params.items() if k != "runs"}
        # Flight-only parameters (research) go to flight_tests, not build.
        shape = {k: v for k, v in params.items() if k in inspect.signature(self.module.build).parameters}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                data = self.module.build(**shape).to_dict()
                (flight,) = self.module.flight_tests(**params)
            except ValueError as e:
                return Trial(params, False, error=str(e))
        flights, errors = [], []
        for _ in range(self.runs):
            report = Report(self.server.version)
            flown = run_flight(self.server, data, flight, report)
            errors += report.errors
            if flown.error or flown.built < flown.entities:
                return Trial(params, False, error="; ".join(errors) or f"built {flown.built} of {flown.entities}")
            flights.append(flown)
        km = [f.km for f in flights]
        return Trial(params, True, {"km_mean": sum(km) / len(km), "km_min": min(km), "km_max": max(km),
                                    "seconds_mean": sum(f.seconds for f in flights) / len(flights),
                                    "survived": sum(f.survived for f in flights) / len(flights)},
                     windows=len(flights), stalled=dict(Counter(f.ended or "survived" for f in flights)))


def grid(evaluate, space: dict[str, list], base: dict | None = None) -> list[Trial]:
    """A trial for every combination of the values in space (with base's other parameters)."""
    names = list(space)
    return [evaluate({**(base or {}), **dict(zip(names, combo))})
            for combo in itertools.product(*(space[n] for n in names))]


def bisect(evaluate, name: str, values: list, base: dict | None = None, ok=lambda t: t.passed):
    """(last value that passes, first that fails, trials) along ordered values,
    assuming trials pass up to some value and fail after it. None where there's none."""
    trials = {}

    def judge(i):
        if i not in trials:
            trials[i] = evaluate({**(base or {}), name: values[i]})
        return ok(trials[i])

    lo, hi = -1, len(values)  # values[lo] passes (or lo = -1), values[hi] fails (or hi = len)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if judge(mid):
            lo = mid
        else:
            hi = mid
    return (values[lo] if lo >= 0 else None, values[hi] if hi < len(values) else None,
            [trials[i] for i in sorted(trials)])


def descend(evaluate, space: dict[str, list], start: dict, score, ok=lambda t: t.passed, rounds: int = 10,
            margin: float = 0.0):
    """Coordinate descent: from start, try each parameter's neighbouring values in
    turn and move to any that passes with a higher score, until none does.
    score(trial) is maximised; with margin, a move needs a score above the best's
    by that share of it (for noisy scores). Returns (best trial, all trials)."""
    trials = []

    def run(params):
        trial = evaluate(params)
        trials.append(trial)
        return trial

    best = run(dict(start))
    if not ok(best):
        return None, trials
    for _ in range(rounds):
        moved = False
        for name, values in space.items():
            i = values.index(best.params[name])
            for j in (i - 1, i + 1):
                if 0 <= j < len(values):
                    trial = run({**best.params, name: values[j]})
                    if ok(trial) and score(trial) > score(best) + margin * abs(score(best)):
                        best, moved = trial, True
                        break
        if not moved:
            break
    return best, trials


def main(argv=None):
    from tools import studies

    ap = argparse.ArgumentParser(prog="python -m tools.optimise", description=__doc__.splitlines()[0])
    ap.add_argument("study", choices=sorted(studies.STUDIES))
    ap.add_argument("--factorio", help="headless executable (default: found by validator.fetch)")
    args = ap.parse_args(argv)
    from validator.server import Server

    with Server(args.factorio) as server:
        result = studies.STUDIES[args.study](server)
    out = CACHE / f"{args.study}.result.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    print(f"saved {out.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
