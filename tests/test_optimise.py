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

    def test_fingerprint_changes_with_the_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1")
            first = optimise.fingerprint(["*.py"], root)
            (root / "a.py").write_text("x = 2")
            self.assertNotEqual(first, optimise.fingerprint(["*.py"], root))


if __name__ == "__main__":
    unittest.main()
