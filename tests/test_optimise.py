"""The optimiser's searches and cache, with a fake game."""

import json
import tempfile
import unittest
from pathlib import Path

from tools import optimise
from tools.optimise import Trial, bisect, descend, grid


def fake(threshold=0.9, peak=(3, 2)):
    """Passes while share <= threshold; scores highest at (a, b) = peak."""
    calls = []

    def evaluate(params):
        calls.append(dict(params))
        a, b = params.get("a", 0), params.get("b", 0)
        score = -((a - peak[0]) ** 2 + (b - peak[1]) ** 2)
        return Trial(params, params.get("share", 0) <= threshold, {"score": score})

    evaluate.calls = calls
    return evaluate


class Searches(unittest.TestCase):
    def test_grid_tries_every_combination(self):
        ev = fake()
        trials = grid(ev, {"a": [1, 2], "b": [5, 6, 7]}, {"item": "x"})
        self.assertEqual(len(trials), 6)
        self.assertEqual(ev.calls[0], {"item": "x", "a": 1, "b": 5})

    def test_bisect_finds_the_threshold_in_few_trials(self):
        ev = fake(threshold=0.9)
        values = [0.8, 0.825, 0.85, 0.875, 0.9, 0.925, 0.95, 0.975, 1.0]
        last, first_bad, trials = bisect(ev, "share", values)
        self.assertEqual((last, first_bad), (0.9, 0.925))
        self.assertLessEqual(len(ev.calls), 4)  # log2(9) rounded up
        self.assertEqual(bisect(fake(threshold=2), "share", values)[:2], (1.0, None))
        self.assertEqual(bisect(fake(threshold=0), "share", values)[:2], (None, 0.8))

    def test_descend_climbs_to_the_best_passing_score(self):
        ev = fake(peak=(3, 2))
        best, trials = descend(ev, {"a": [0, 1, 2, 3, 4, 5], "b": [0, 1, 2, 3]}, {"a": 0, "b": 0},
                               score=lambda t: t.rates["score"])
        self.assertEqual((best.params["a"], best.params["b"]), (3, 2))

    def test_descend_needs_the_margin_to_move(self):
        def evaluate(params):  # each step right scores 3% better: inside a 5% margin
            return Trial(params, True, {"score": 100 * 1.03 ** params["a"]})

        best, _ = descend(evaluate, {"a": [0, 1, 2, 3]}, {"a": 0}, score=lambda t: t.rates["score"], margin=0.05)
        self.assertEqual(best.params["a"], 0)
        best, _ = descend(evaluate, {"a": [0, 1, 2, 3]}, {"a": 0}, score=lambda t: t.rates["score"])
        self.assertEqual(best.params["a"], 3)

    def test_descend_never_moves_to_a_failing_trial(self):
        def evaluate(params):  # better scores further right, but a > 2 fails
            return Trial(params, params["a"] <= 2, {"score": params["a"]})

        best, _ = descend(evaluate, {"a": [0, 1, 2, 3, 4]}, {"a": 0}, score=lambda t: t.rates["score"])
        self.assertEqual(best.params["a"], 2)


class Cache(unittest.TestCase):
    def test_trials_are_cached_per_parameters_and_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = optimise.GameEvaluator.__new__(optimise.GameEvaluator)
            ev.path, ev.code, ev.cache, ev.ran = Path(tmp) / "g.json", "abc", {}, 0
            runs = []
            ev._run = lambda params: runs.append(params) or Trial(params, True, {"x": 1.0})
            self.assertTrue(ev({"rate": 1}).passed)
            ev({"rate": 1})
            self.assertEqual(len(runs), 1)  # the second came from the cache
            self.assertEqual(ev.ran, 1)
            # Stored under the code's fingerprint, so changed code starts afresh.
            stored = json.loads(ev.path.read_text())
            self.assertEqual(list(stored), ["abc"])
            self.assertIn('{"rate": 1}', stored["abc"])

    def test_flight_trials_give_research_to_the_flight_only(self):
        from types import SimpleNamespace
        from unittest import mock

        from validator.flight import Flight, Flown

        got = {}

        def build(railguns=4):
            got["build"] = railguns
            return SimpleNamespace(to_dict=dict)

        def flight_tests(research=None, **params):
            got["flight"] = research
            return [Flight("f")]

        flights = iter([Flown("f", km=100.0, seconds=10.0, ended="hub destroyed",
                              deaths={"huge-oxide-asteroid <- railgun-turret": 2,
                                      "huge-promethium-asteroid <- railgun-turret": 1,
                                      "space-platform-hub <- big-oxide-asteroid": 1}),
                        Flown("f", km=300.0, seconds=30.0, ended="hub destroyed",
                              deaths={"huge-carbonic-asteroid <- railgun-turret": 4}),
                        Flown("f", km=500.0, seconds=60.0, survived=True)])
        ev = optimise.FlightEvaluator.__new__(optimise.FlightEvaluator)
        ev.module = SimpleNamespace(build=build, flight_tests=flight_tests)
        ev.runs, ev.server = 3, SimpleNamespace(version="2.1.22")
        with mock.patch("validator.flight.run_flight", lambda *a: next(flights)):
            trial = ev._run({"railguns": 6, "research": {"railgun-damage": 2}, "runs": 3})
        self.assertEqual(got, {"build": 6, "flight": {"railgun-damage": 2}})
        self.assertEqual(trial.rates["km_mean"], 300.0)
        self.assertAlmostEqual(trial.rates["survived"], 1 / 3)
        self.assertEqual(trial.stalled, {"hub destroyed": 2, "survived": 1})
        # Deaths summed over the flights, asteroids by size.
        self.assertEqual(trial.counts, {"huge asteroid <- railgun-turret": 7,
                                        "space-platform-hub <- big asteroid": 1})

    def test_flown_budget_is_what_was_used_a_second(self):
        from validator.flight import Flown

        flown = Flown("f", seconds=20.0, supplied={"thruster-fuel": 2600.0, "railgun-ammo": 30.0},
                      held={"thruster-fuel": 200.0, "railgun-ammo": 10.0},
                      rebuilt={"space-platform-foundation": 400, "gun-turret": 2}, power_j=2e9)
        self.assertEqual(flown.budget(), {"thruster-fuel/s": 120.0, "railgun-ammo/s": 1.0, "power MW": 100.0,
                                          "rebuilt space-platform-foundation/s": 20.0, "rebuilt gun-turret/s": 0.1})
        self.assertEqual(Flown("f").budget(), {})

    def test_flown_budget_counts_what_was_used_per_1000_km(self):
        # A slower platform lives longer partly because it's slower: what it uses per
        # 1,000 km flown compares designs at different speeds.
        from validator.flight import Flown

        flown = Flown("f", seconds=20.0, km=4000.0, supplied={"railgun-ammo": 30.0}, held={"railgun-ammo": 10.0},
                      rebuilt={"space-platform-foundation": 400})
        budget = flown.budget()
        self.assertEqual(budget["railgun-ammo/1000 km"], 5.0)
        self.assertEqual(budget["rebuilt space-platform-foundation/1000 km"], 100.0)
        self.assertEqual(budget["railgun-ammo/s"], 1.0)  # per second as before
        self.assertNotIn("railgun-ammo/1000 km", Flown("f", seconds=20.0, supplied={"railgun-ammo": 30.0}).budget())

    def test_flown_budget_keeps_what_a_fuel_maker_held_at_the_end(self):
        # On fuel made on board, what was left in the thrusters and any reserve tank at
        # the end: whether a reserve ran out.
        from validator.flight import Flown

        flown = Flown("f", seconds=10.0, supplied={"thruster-fuel": 26000.0},
                      held={"thruster-fuel": 12000.0, "railgun-ammo": 5.0}, made={"thruster-fuel": 300.0})
        self.assertEqual(flown.budget()["held thruster-fuel"], 12000.0)
        self.assertNotIn("held railgun-ammo", flown.budget())

    def test_flown_budget_counts_what_was_taken_and_the_cruise(self):
        # Promethium chunks the script took from the hub (so a full hub doesn't cap the
        # count), a second and per 1,000 km; fusion cells per 1,000 km; and the speed over
        # the flight's second half, once a reserve filled before leaving has drained
        # toward where use meets what's made (NOTES.md).
        from validator.flight import Flown

        log = [{"seconds": s, "km": km} for s, km in ((10, 900), (20, 1600), (30, 2200), (40, 2800))]
        flown = Flown("f", seconds=40.0, km=2800.0, taken={"promethium-asteroid-chunk": 56}, generated_j=4e12, log=log)
        budget = flown.budget()
        self.assertEqual(budget["taken promethium-asteroid-chunk/1000 km"], 20.0)
        self.assertEqual(budget["taken promethium-asteroid-chunk/s"], 1.4)
        self.assertAlmostEqual(budget["fusion cells/1000 km"], 4e12 / 40e9 / 2800 * 1000)
        self.assertEqual(budget["cruise km/s"], 60.0)  # (2800 - 1600) / (40 - 20)

    def test_flight_trials_average_the_budget_and_the_survivors_apart(self):
        from types import SimpleNamespace
        from unittest import mock

        from validator.flight import Flight, Flown

        flights = iter([Flown("f", seconds=10.0, supplied={"railgun-ammo": 30.0}, power_j=1e9),
                        Flown("f", seconds=20.0, survived=True, supplied={"railgun-ammo": 20.0}, power_j=1e9)])
        ev = optimise.FlightEvaluator.__new__(optimise.FlightEvaluator)
        ev.module = SimpleNamespace(build=lambda: SimpleNamespace(to_dict=dict), flight_tests=lambda: [Flight("f")])
        ev.runs, ev.server = 2, SimpleNamespace(version="2.1.23")
        with mock.patch("validator.flight.run_flight", lambda *a: next(flights)):
            trial = ev._run({"runs": 2})
        self.assertEqual(trial.rates["railgun-ammo/s"], 2.0)  # (3 + 1) / 2
        self.assertEqual(trial.rates["power MW"], 75.0)  # (100 + 50) / 2
        self.assertEqual(trial.rates["railgun-ammo/s lived"], 1.0)
        self.assertEqual(trial.rates["power MW lived"], 50.0)

    def test_fingerprint_changes_with_the_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1")
            first = optimise.fingerprint(["*.py"], root)
            (root / "a.py").write_text("x = 2")
            self.assertNotEqual(first, optimise.fingerprint(["*.py"], root))


if __name__ == "__main__":
    unittest.main()
