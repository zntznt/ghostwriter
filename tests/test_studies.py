"""The studies (tools.studies) as experiments: every one the docs cite exists, and each runs the
trials it ran when its results were recorded, without the game.

A study is recorded with a fake game: the optimiser's evaluators note each trial's parameters
and hand back a passing trial, run_flight notes each flight's blueprint and settings and hands
back a flown one, and the cache is an empty folder. What a study asked for, in order, is its
experiment. tests/fixtures/study_trials.json holds every study's as recorded from the code the
NOTES.md results came from; `python -m tests.test_studies --write-fixture` rewrites it, which
a change to a study's experiment should be the only reason to do.
"""

import contextlib
import dataclasses
import hashlib
import json
import re
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "study_trials.json"
DOCS = ("NOTES.md", "README.md", ".claude/skills/blueprint/SKILL.md", "validator/README.md")
# Studies that plan from an earlier study's result: with an empty cache they stop at reading it.
CHAINED = ("platform-legs-planned", "platform-legs-margin", "platform-advanced-fuel-margin")


def cited() -> set[str]:
    """Study names the docs cite: `python -m tools.optimise NAME`, and a study's name in backquotes
    (an evidence column naming it, or its result file)."""
    names = set()
    for doc in DOCS:
        text = (ROOT / doc).read_text()
        names |= set(re.findall(r"tools\.optimise ([a-z][a-z0-9-]+)", text))
        names |= set(re.findall(r"`(?:python -m tools\.optimise )?(belt-fill|platform-[a-z][a-z0-9-]*?)"
                                r"(?:\.result\.json)?`", text))
    return names


class Rates(dict):
    """A trial's rates: whatever a study reads that a trial didn't measure is 1."""

    def __missing__(self, key):
        return 1.0


def _trial(params):
    from tools.optimise import Trial

    rates = Rates({"km_mean": 1000.0, "km_min": 900.0, "km_max": 1100.0, "seconds_mean": 600.0, "survived": 1.0,
                   "cruise km/s": 50.0})
    return Trial(dict(params), True, rates, windows=1)


def _plain(value):
    """JSON-able, with dict keys in order, for comparing what was asked."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _shape(value):
    """A result's shape: its keys all the way down, a list by its first item, values by type."""
    if isinstance(value, dict):
        return {str(k): _shape(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


def record(name: str, cache: Path) -> dict:
    """A study run against the fake game, with `cache` as the optimiser's cache: what it asked for,
    in order, and its result's shape (or the exception it stopped on). Its result goes into the
    cache as tools.optimise saves it, for the studies that plan from it."""
    from tools import optimise, studies
    from validator import flight as flight_module
    from validator.flight import Flown

    calls = []

    class Evaluator:
        def __init__(self, server, generator, runs=None, **kw):
            self.generator, self.runs, self.ran = generator, runs, 0

        def __call__(self, params):
            calls.append({"trial": self.generator, "runs": self.runs, "params": _plain(params)})
            return _trial(params)

    def run_flight(server, data, flight, report):
        blueprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]
        calls.append({"flight": _plain(dataclasses.asdict(flight)), "blueprint": blueprint})
        return Flown(flight.name, km=1000.0, seconds=600.0, survived=True, back=600.0, trip_km=1000.0,
                     odometer=1000.0, log=[{"seconds": 600.0, "km": 1000.0, "speed": 1.0, "hub_health": 1.0}])

    server = mock.Mock(version="2.1.23")
    with contextlib.ExitStack() as stack:
        modules = [m for k, m in sys.modules.items() if (k == "tools" or k.startswith("tools.")) and m]
        for module in modules:
            for attr, fake in (("FlightEvaluator", Evaluator), ("GameEvaluator", Evaluator), ("run_flight", run_flight),
                               ("CACHE", cache)):
                if hasattr(module, attr):
                    stack.enter_context(mock.patch.object(module, attr, fake))
        stack.enter_context(mock.patch.object(flight_module, "run_flight", run_flight))
        stack.enter_context(contextlib.redirect_stderr(open("/dev/null", "w")))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                result = studies.STUDIES[name](server)
            except Exception as e:
                return {"calls": _plain(calls), "raised": f"{type(e).__name__}: {e}"}
    (cache / f"{name}.result.json").write_text(json.dumps(result, indent=1))
    return {"calls": _plain(calls), "shape": _shape(_plain(result))}


def record_all() -> dict:
    """Every study recorded in one cache, those that plan from another's result after it."""
    from tools.studies import STUDIES

    order = sorted(n for n in STUDIES if n not in CHAINED) + [n for n in CHAINED if n in STUDIES]
    with tempfile.TemporaryDirectory() as tmp:
        return {name: record(name, Path(tmp)) for name in order}


class Studies(unittest.TestCase):
    def test_every_study_the_docs_cite_exists(self):
        from tools.studies import STUDIES

        missing = sorted(cited() - set(STUDIES))
        self.assertEqual(missing, [], "cited as evidence but not a study: python -m tools.optimise NAME fails")
        self.assertIn("platform-whole-trip-shock", cited())  # the pattern finds them
        self.assertNotIn("platform-16-railguns-16-gun-turrets", cited())  # not a library file's name

    def test_every_study_runs_the_trials_it_ran(self):
        expected = json.loads(FIXTURE.read_text())
        got = record_all()
        self.assertEqual(sorted(got), sorted(expected), "a study was added or removed: rewrite the fixture")
        for name in sorted(expected):
            with self.subTest(name):
                self.assertNotIn("raised", got[name])
                self.assertEqual(len(got[name]["calls"]), len(expected[name]["calls"]), "a different number of trials")
                for i, (g, e) in enumerate(zip(got[name]["calls"], expected[name]["calls"])):
                    self.assertEqual(g, e, f"trial {i + 1} differs")
                self.assertEqual(got[name]["shape"], expected[name]["shape"], "the result's shape differs")

    def test_chained_studies_plan_from_the_studies_before_them(self):
        # They read an earlier study's result: with it cached they fly, without it they can't plan.
        expected = json.loads(FIXTURE.read_text())
        for name in CHAINED:
            self.assertTrue(expected[name]["calls"], name)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("raised", record("platform-legs-planned", Path(tmp)))


def write_fixture():
    out = record_all()
    for name, r in out.items():
        print(f"{name}: {r.get('raised') or str(len(r['calls'])) + ' trials'}", file=sys.stderr, flush=True)
    # A trial a line, so a change shows as the trials it changed.
    lines = []
    for name in sorted(out):
        calls = ",\n".join("  " + json.dumps(c, sort_keys=True) for c in out[name]["calls"])
        rest = "".join(f", {json.dumps(k)}: {json.dumps(v, sort_keys=True)}"
                       for k, v in sorted(out[name].items()) if k != "calls")
        lines.append(f"{json.dumps(name)}: {{\"calls\": [\n{calls}\n ]{rest}}}")
    FIXTURE.write_text("{\n" + ",\n".join(lines) + "\n}\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-fixture"]:
        write_fixture()
    else:
        unittest.main()
