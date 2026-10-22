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

    def test_flown_budget_counts_ammo_the_hub_fed(self):
        # ammo_feed: the hub's inserters feed the turrets, and what left the hub, less what
        # the belts and turrets still hold, is what was used, as with the script's ammo.
        # A turret with none counts each second it had none.
        from validator.flight import Flown

        flown = Flown("f", seconds=20.0, km=4000.0, fed={"railgun-ammo": 50.0}, held={"railgun-ammo": 10.0}, dry=7)
        budget = flown.budget()
        self.assertEqual(budget["railgun-ammo/s"], 2.0)
        self.assertEqual(budget["railgun-ammo/1000 km"], 10.0)
        self.assertEqual(budget["dry turret s"], 7)
        both = Flown("f", seconds=10.0, supplied={"railgun-ammo": 30.0}, fed={"railgun-ammo": 20.0}).budget()
        self.assertEqual(both["railgun-ammo/s"], 5.0)
        self.assertNotIn("dry turret s", Flown("f", seconds=10.0, supplied={"railgun-ammo": 30.0}).budget())

    def test_flown_budget_counts_cargo_bays_lost_by_distance_from_the_hub(self):
        # A dead bay cuts off the bays joined through it (NOTES.md): bays further out
        # may be hit more, so the flight counts them by |x|, tiles from the hub's centre.
        from validator.flight import Flown

        flown = Flown("f", seconds=20.0, km=4000.0, bays_lost={"6": 2, "26": 8})
        budget = flown.budget()
        self.assertEqual(budget["cargo bays lost at |x| 6/1000 km"], 0.5)
        self.assertEqual(budget["cargo bays lost at |x| 26/1000 km"], 2.0)

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

    def test_the_whole_trip_flies_the_planned_load_and_path(self):
        import math

        from draftsman.data import items

        from tools import studies, trip

        slots = sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in studies.WHOLE_TRIP_STOCK.items())
        self.assertEqual(slots, 530)  # of the 539 that 24 bays give (NOTES.md)
        corrected = sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in studies.CORRECTED_TRIP_STOCK.items())
        self.assertEqual(corrected, 537)  # with the whole trip folded in
        self.assertAlmostEqual(2 * studies.WHOLE_TRIP_TURN * trip.LENGTH, 162_000)  # out, the stay, and back

    def test_a_whole_trip_flight_in_numbers(self):
        from tools import plan, studies
        from validator.flight import Flown

        turn_km = studies.WHOLE_TRIP_TURN * 4_000_000
        back = Flown("trip", odometer=162_000, seconds=3_000, survived=True, turned=1_620, back=3_000, dry=5,
                     cargo={"promethium-asteroid-chunk": 300, "railgun-ammo": 10}, low_fuel=2, brownout=1,
                     ran_out={"space-platform-foundation": 150_000.0, "pipe": 160_000.0},
                     at_turn={"km": turn_km, "cargo": {"promethium-asteroid-chunk": 120, "railgun-ammo": 2005,
                                                       "space-platform-foundation": 1000}})
        died = Flown("trip", odometer=70_000, seconds=1_400, ended="hub destroyed by big asteroid",
                     cargo={"promethium-asteroid-chunk": 200}, position=0.0175)
        stock = {"railgun-ammo": 4305, "space-platform-foundation": 2329}
        rows = [studies.whole_trip_flown(f, stock) for f in (back, died)]
        self.assertEqual([r["chunks back"] for r in rows], [300, 0])  # it died with 200 aboard
        self.assertEqual([r["died at"] for r in rows], [None, 0.0175])
        self.assertEqual(rows[0]["left"], {"railgun-ammo": 10, "space-platform-foundation": 0})
        self.assertEqual(rows[0]["first out"], {"item": "space-platform-foundation", "km": 150_000.0})
        self.assertAlmostEqual(rows[0]["out km/s"], turn_km / 1_620)
        self.assertAlmostEqual(rows[0]["back km/s"], (162_000 - turn_km) / 1_380)
        # The two legs apart: chunks in, and stock used, out and back.
        self.assertEqual((rows[0]["chunks out"], rows[0]["chunks on the way back"]), (120, 180))
        self.assertEqual(rows[0]["used out"], {"railgun-ammo": 2300, "space-platform-foundation": 1329})
        self.assertEqual(rows[0]["used back"], {"railgun-ammo": 1995, "space-platform-foundation": 1000})
        self.assertEqual((rows[1]["chunks out"], rows[1]["used back"]), (None, None))  # it never turned
        means = plan.trip_means(rows)
        self.assertEqual(means["came back"], 0.5)
        self.assertEqual(means["chunks back"], 150)  # over every flight: a lost one brings back none
        self.assertEqual(means["chunks per trip back"], 300)  # over the ones that came back
        self.assertEqual(means["left, came back"], {"railgun-ammo": 10, "space-platform-foundation": 0})
        self.assertEqual(means["first out"], {"space-platform-foundation": 1})
        self.assertEqual((means["km"], means["dry turret s"]), (116_000, 2.5))

    def test_a_flight_whose_seconds_end_between_ticks_still_ends(self):
        # A flight of 444.444 s (a plan's three times its hours) once stepped 0 ticks forever: the
        # last 0.0133 s is less than a tick. It steps in whole ticks, so it ends on its last one.
        from validator.flight import Flight, run_flight
        from validator.validate import Report

        class Game:  # a paused game that advances only by flight_step, as the scenario's does
            tick, steps = 0, []

            def call(self, function, *args):
                if function == "flight_setup":
                    return {"built": 1, "entities": 1}
                if function == "flight_step":
                    if int(args[0]) <= 0:
                        raise AssertionError("a step of no ticks never ends")
                    self.steps.append(int(args[0]))
                    self.tick += int(args[0])
                    return {"ok": True, "tick": self.tick - int(args[0])}
                if function == "tick":
                    return self.tick
                if function == "flight_read":
                    return {"ticks": self.tick, "km": 0, "speed_max": 0, "deaths": {}, "alive": True, "speed": 0,
                            "hub_health": 1}
                return {"ok": True}

        game = Game()
        flown = run_flight(game, {"blueprint": {"entities": []}}, Flight("between ticks", seconds=444.444), Report("2"))
        self.assertTrue(flown.survived)
        self.assertEqual(game.tick, round(444.444 * 60))

    def test_a_flight_from_home_splits_into_legs_at_its_stops(self):
        # Flight.home: each place it reaches is a stop with the km flown so far and what the hub
        # held; a leg is what happened between two stops, and the last runs on to the end if it
        # died between them. Used is what the hub lost (a gain is negative).
        from validator.flight import Flown

        stops = [{"at": "nauvis", "seconds": 0, "km": 0, "cargo": {"railgun-ammo": 100, "pipe": 50}, "deaths": {}},
                 {"at": "fulgora", "seconds": 300, "km": 15000, "cargo": {"railgun-ammo": 90, "pipe": 50},
                  "deaths": {"gun-turret <- medium-metallic-asteroid": 1}},
                 {"at": "aquilo", "seconds": 900, "km": 45000, "cargo": {"railgun-ammo": 60, "pipe": 40},
                  "deaths": {"gun-turret <- medium-metallic-asteroid": 1, "railgun-turret <- big-oxide-asteroid": 2}}]
        died = Flown("legs", seconds=1500, trip_km=60000, stops=stops, cargo={"railgun-ammo": 20, "pipe": 40,
                                                                                "promethium-asteroid-chunk": 3},
                     deaths={"gun-turret <- medium-metallic-asteroid": 1, "railgun-turret <- big-oxide-asteroid": 3},
                     ended="hub destroyed by huge-oxide-asteroid")
        self.assertEqual(died.legs(), [
            {"from": "nauvis", "to": "fulgora", "km": 15000, "seconds": 300, "used": {"railgun-ammo": 10},
             "died": {"gun-turret <- medium-metallic-asteroid": 1}},
            {"from": "fulgora", "to": "aquilo", "km": 30000, "seconds": 600, "used": {"railgun-ammo": 30, "pipe": 10},
             "died": {"railgun-turret <- big-oxide-asteroid": 2}},
            {"from": "aquilo", "to": None, "km": 15000, "seconds": 600,
             "used": {"railgun-ammo": 40, "promethium-asteroid-chunk": -3},
             "died": {"railgun-turret <- big-oxide-asteroid": 1}}])
        self.assertEqual(Flown("plain").legs(), [])

    def test_a_leg_says_what_the_module_made_and_what_its_lines_held(self):
        # Stops that record the fuel made on board so far and the fuel and oxidizer in the thruster
        # and its tanks: a leg then has what was made on it and what was held at its end (the
        # reserve as it got there), the last leg to the end of the flight.
        from validator.flight import Flown

        fluids = ("thruster-fuel", "thruster-oxidizer")
        stops = [{"at": "nauvis", "seconds": 0, "km": 0, "cargo": {}, "deaths": {}, "made": {},
                  "held": dict.fromkeys(fluids, 26000)},
                 {"at": "aquilo", "seconds": 900, "km": 45000, "cargo": {}, "deaths": {},
                  "made": {"thruster-fuel": 30000, "thruster-oxidizer": 9000}, "held": dict.fromkeys(fluids, 800)}]
        flown = Flown("legs", seconds=1500, trip_km=60000, stops=stops, ended="stranded",
                      made={"thruster-fuel": 45000, "thruster-oxidizer": 9000},
                      held={"thruster-fuel": 600, "thruster-oxidizer": 0, "railgun-ammo": 40})
        out, on = flown.legs()
        self.assertEqual((out["made"], out["held"]), ({"thruster-fuel": 30000, "thruster-oxidizer": 9000},
                                                      dict.fromkeys(fluids, 800)))
        self.assertEqual((on["made"], on["held"]), ({"thruster-fuel": 15000}, {"thruster-fuel": 600,
                                                                                "thruster-oxidizer": 0}))

    def test_a_leg_counts_what_was_used_when_the_hub_is_held(self):
        # With the hub held at its stock (Flight.hold) its cargo doesn't move, so stops also record
        # the flight's counters (what the hub rebuilt, the feed drew and the script gave): a leg
        # then has what they counted on it, the last leg to the end of the flight.
        from validator.flight import Flown

        stops = [{"at": "nauvis", "seconds": 0, "km": 0, "cargo": {"pipe": 50}, "deaths": {}, "counted": {}},
                 {"at": "fulgora", "seconds": 300, "km": 15000, "cargo": {"pipe": 50}, "deaths": {},
                  "counted": {"railgun-ammo": 40, "pipe": 2}}]
        flown = Flown("held", seconds=900, trip_km=45000, stops=stops, cargo={"pipe": 50}, ended="stranded",
                      rebuilt={"pipe": 5}, fed={"railgun-ammo": 100}, supplied={"thruster-fuel": 900})
        self.assertEqual(flown.counted(), {"pipe": 5, "railgun-ammo": 100, "thruster-fuel": 900})
        out, on = flown.legs()
        self.assertEqual(out["counted"], {"railgun-ammo": 40, "pipe": 2})
        self.assertEqual(on["counted"], {"railgun-ammo": 60, "pipe": 3, "thruster-fuel": 900})
        self.assertEqual(out["used"], {})  # held: the cargo didn't move

    def test_leg_fuel_says_what_each_leg_made_and_what_was_left_at_its_end(self):
        # Per leg in the order flown: fuel and oxidizer made a second over every flight that got onto
        # it, and what the thruster and its tanks held at its end over the flights that finished it.
        from tools import studies

        def leg(a, b, s, made, held):
            return {"from": a, "to": b, "km": 1000, "seconds": s, "used": {}, "died": {}, "made": made, "held": held}

        f = ("thruster-fuel", "thruster-oxidizer")
        one = {"legs": [leg("nauvis", "aquilo", 100, dict.fromkeys(f, 3000), dict.fromkeys(f, 800)),
                        leg("aquilo", None, 300, {"thruster-fuel": 900}, {"thruster-fuel": 500, "thruster-oxidizer": 0})]}
        two = {"legs": [leg("nauvis", "aquilo", 100, dict.fromkeys(f, 1000), dict.fromkeys(f, 1200)),
                        leg("aquilo", "solar-system-edge", 100, dict.fromkeys(f, 300), dict.fromkeys(f, 100))]}
        out, edge = studies.leg_fuel([one, two])
        self.assertEqual((out["from"], out["to"], out["flights"], out["finished"]), ("nauvis", "aquilo", 2, 2))
        self.assertEqual(out["made/s"], dict.fromkeys(f, 20.0))
        self.assertEqual(out["held at end"], dict.fromkeys(f, 1000.0))
        self.assertEqual((edge["to"], edge["flights"], edge["finished"]), ("solar-system-edge", 2, 1))
        self.assertEqual(edge["made/s"], {"thruster-fuel": 3.0, "thruster-oxidizer": 0.75})
        self.assertEqual(edge["held at end"], dict.fromkeys(f, 100.0))  # only the flight that got there

    def test_leg_rates_come_from_every_flight_that_got_onto_the_leg(self):
        # platform-legs: per leg, in the order flown (out, the route, home), what the hub gave per
        # 1,000 km over all the km flown on it (a leg it died on counts its km too), the chunks it
        # brought in, its km/s, and the trips lost on it per 1,000 km.
        from tools import plan

        def leg(a, b, km, s, used):
            return {"from": a, "to": b, "km": km, "seconds": s, "used": used, "died": {}}

        edge = "solar-system-edge"
        back = {"back": True, "legs": [leg("nauvis", "aquilo", 45000, 900, {"railgun-ammo": 9}),
                                       leg("aquilo", edge, 100000, 2000, {"railgun-ammo": 200}),
                                       leg(edge, edge, 160000, 3200, {"railgun-ammo": 300, "promethium-asteroid-chunk": -480}),
                                       leg(edge, "aquilo", 100000, 1600, {"railgun-ammo": 100})]}
        lost = {"back": False, "legs": [leg("nauvis", "aquilo", 45000, 900, {"railgun-ammo": 9}),
                                        leg("aquilo", None, 50000, 1000, {"railgun-ammo": 250})]}
        rates = plan.leg_rates([back, lost])
        self.assertEqual([(r["part"], r["from"], r["to"]) for r in rates],
                         [("out", "nauvis", "aquilo"), ("out", "aquilo", edge), ("route", edge, edge),
                          ("home", edge, "aquilo")])
        out, edge_leg, route, home = rates
        self.assertEqual(out["km"], 45000)
        self.assertAlmostEqual(out["row"]["railgun-ammo"], 18 / 90000 * 1000)
        self.assertEqual(edge_leg["km"], 100000)  # a whole leg: the one that got there
        self.assertAlmostEqual(edge_leg["row"]["railgun-ammo"], 450 / 150000 * 1000)
        self.assertAlmostEqual(edge_leg["row"]["deaths"], 1 / 150000 * 1000)
        self.assertAlmostEqual(edge_leg["row"]["km/s"], 150000 / 3000)
        self.assertAlmostEqual(route["row"]["promethium-asteroid-chunk"], 480 / 160000 * 1000)
        self.assertNotIn("promethium-asteroid-chunk", out["row"])
        self.assertEqual(home["row"]["deaths"], 0.0)
        # The route starts at the first leg from the edge, whether or not a flight finished it.
        died = {"back": False, "legs": [leg("nauvis", edge, 145000, 3000, {}), leg(edge, None, 100000, 2000, {})]}
        self.assertEqual([r["part"] for r in plan.leg_rates([died])], ["out", "route"])

    def test_the_planned_trip_with_legs_loads_its_plan_and_flies_its_stay_further_out(self):
        # A platform can't hold still on a connection, so a stay is flown as half further out and
        # half back (NOTES.md), and the load is what the plan uses, in whole items.
        from tools import plan

        best = {"turn": 0.004, "stay": 50000.0, "used": {"railgun-ammo": 4550.4, "cargo-bay": 2.7, "pipe": 0.0}}
        turn, stock = plan.planned_trip(best)
        self.assertAlmostEqual(turn, 0.004 + 25000 / 4_000_000)
        self.assertEqual(stock, {"railgun-ammo": 4551, "cargo-bay": 3})

    def test_a_margin_load_tops_up_what_ran_out_and_fills_the_hub_with_railgun_ammo(self):
        # margin_stock: from the planned trip's flights, magazines and foundation at 10% over the most
        # a flight used, in whole stacks; every spare in whole stacks, with the cascade's 20 railguns,
        # 20 bays and 2 reactors (NOTES.md); and railgun ammo, which never ran out, in what's left.
        import math

        from draftsman.data import items

        from tools import studies

        stock = {"uranium-rounds-magazine": 5726, "railgun-ammo": 4551, "space-platform-foundation": 625,
                 "crusher": 3, "railgun-turret": 2}
        flights = [{"left": {"uranium-rounds-magazine": 0, "railgun-ammo": 246, "space-platform-foundation": 0,
                             "crusher": 0, "railgun-turret": 0}},
                   {"left": {"uranium-rounds-magazine": 158, "railgun-ammo": 699, "space-platform-foundation": 483,
                             "crusher": 3, "railgun-turret": 1}}]
        margin = studies.margin_stock({"supplies": stock, "flights": flights}, slots=539)
        self.assertEqual(margin["uranium-rounds-magazine"], 6300)  # 5,726 x 1.1, in stacks of 100
        self.assertEqual(margin["space-platform-foundation"], 700)
        self.assertEqual((margin["crusher"], margin["railgun-turret"], margin["cargo-bay"], margin["fusion-reactor"]),
                         (10, 20, 20, 2))
        self.assertEqual(sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in margin.items()), 539)
        self.assertEqual(margin["railgun-ammo"] % 10, 0)

    def test_with_no_flight_home_the_legs_home_mirror_the_legs_out(self):
        # legs_model: the way home is the way out reversed when no flight came home to measure it.
        from tools import plan

        rates = [{"part": "out", "from": "nauvis", "to": "aquilo", "km": 45000, "row": {"km/s": 50.0}},
                 {"part": "out", "from": "aquilo", "to": "solar-system-edge", "km": 100000, "row": {"km/s": 60.0}},
                 {"part": "route", "from": "solar-system-edge", "to": None, "km": None, "row": {}}]
        legs = plan.legs_home(rates)
        self.assertEqual([(r["part"], r["from"], r["to"], r["km"], r["mirrored"]) for r in legs[-2:]],
                         [("home", "solar-system-edge", "aquilo", 100000, True), ("home", "aquilo", "nauvis", 45000, True)])
        measured = rates + [{"part": "home", "from": "solar-system-edge", "to": "aquilo", "km": 100000, "row": {}}]
        self.assertEqual(plan.legs_home(measured), measured)  # measured ones stand

    def test_lost_trips_count_the_bays_and_railguns_that_died(self):
        # What starts the cascade (NOTES.md): railguns die, huge asteroids get through to the bays.
        from tools import plan, studies
        from validator.flight import Flown

        died = Flown("trip", odometer=70_000, seconds=1_400, ended="hub destroyed by huge asteroid",
                     deaths={"cargo-bay <- huge-oxide-asteroid": 10, "cargo-bay <- big-oxide-asteroid": 2,
                             "railgun-turret <- huge-carbonic-asteroid": 7, "gun-turret <- small-oxide-asteroid": 3})
        back = Flown("trip", odometer=162_000, seconds=3_000, survived=True, back=3_000,
                     deaths={"cargo-bay <- big-carbonic-asteroid": 1})
        means = plan.trip_means([studies.whole_trip_flown(f, {}) for f in (died, back)])
        self.assertEqual((means["cargo bays died"], means["railgun turrets died"]), (6.5, 3.5))
        self.assertEqual(means["killed by huge asteroids"], 8.5)  # 17 of ours, over 2 flights

    def test_spares_round_up_to_whole_stacks_for_free(self):
        # A hub slot holds a whole stack, so a spare short of one costs nothing to fill (NOTES.md).
        import math

        from draftsman.data import items

        from tools import plan, studies

        self.assertEqual(plan.full_stacks({"railgun-turret": 6, "fusion-reactor": 1, "uranium-rounds-magazine": 5450}),
                         {"railgun-turret": 10, "fusion-reactor": 1, "uranium-rounds-magazine": 5500})

        def slots(stock):
            return sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in stock.items())

        self.assertEqual([slots(s) for s in studies.CASCADE_STOCKS.values()], [530, 530, 533, 536])  # of 539

    def test_the_shock_study_opens_the_same_gap_for_each_load(self):
        # 8 railguns at once, just under the 9 to 21 lost trips lost, against 6, 10 and 20 spares.
        from tools import studies

        self.assertEqual(studies.SHOCK, 8)
        self.assertEqual([s["railgun-turret"] for s in studies.SHOCK_STOCKS.values()], [6, 10, 20])

    def test_a_crossing_counts_once_it_reaches_the_edge(self):
        # Aquilo to the edge is 100,000 km: a flight crossed once its log first reaches it; one that
        # stranded short of it, out of time, didn't. Made a second over the whole flight.
        from tools import studies
        from validator.flight import Flown

        crossed = Flown("x", km=100_000, seconds=2000, made={"thruster-fuel": 200_000, "thruster-oxidizer": 100_000},
                        held={"thruster-fuel": 20_000}, low_fuel=3,
                        log=[{"seconds": 10, "km": 500}, {"seconds": 1500, "km": 99_995}, {"seconds": 2000, "km": 100_000}])
        row = studies.crossing_flown(crossed)
        self.assertEqual((row["crossed"], row["seconds to cross"], row["ended"]), (True, 1500, "crossed"))
        self.assertEqual(row["made/s"], {"thruster-fuel": 100.0, "thruster-oxidizer": 50.0})
        self.assertEqual(row["held"], {"thruster-fuel": 20_000, "thruster-oxidizer": 0.0})
        stranded = Flown("y", km=40_000, seconds=4000, log=[{"seconds": 4000, "km": 40_000}])
        self.assertEqual((studies.crossing_flown(stranded)["crossed"], studies.crossing_flown(stranded)["ended"]),
                         (False, "out of time"))

    def test_fingerprint_changes_with_the_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1")
            first = optimise.fingerprint(["*.py"], root)
            (root / "a.py").write_text("x = 2")
            self.assertNotEqual(first, optimise.fingerprint(["*.py"], root))


class Plan(unittest.TestCase):
    # tools.plan: a promethium trip for any platform design, from its flights (faked here).
    DESIGN = {"railguns": 8, "guns": 4, "thrusters": 1, "fuel": True, "promethium": True}
    FULL = {"railguns": 16, "guns": 16, "thrusters": 1, "cargo_bays": 24, "fuel": True, "reserve": True, "power": 2,
            "promethium": True, "ammo_feed": True}

    @staticmethod
    def rows(magazines=(30.0, 170.0), chunks=(2.0, 8.0)):
        # Two starts measured: per 1,000 km what the hub gave and the chunks it took in, the
        # cruise, and how many flights lived.
        def rates(m, c):
            return {"uranium-rounds-magazine/1000 km": m, "rebuilt space-platform-foundation/1000 km": 10.0,
                    "taken promethium-asteroid-chunk/1000 km": c, "cruise km/s": 50.0, "survived": 1.0,
                    "km_mean": 30000.0}
        return [{"start": s, "mode": "plan", "passed": True, "rates": rates(m, c), "counts": {}}
                for s, m, c in zip((0.0, 0.1), magazines, chunks)]

    def test_the_hub_holds_its_own_slots_and_its_cargo_bays(self):
        from tools import plan

        self.assertEqual(plan.capacity(self.DESIGN), 59)
        self.assertEqual(plan.capacity(self.FULL), 59 + 24 * 20)

    def test_a_measurement_holds_a_stack_of_every_part_and_the_ammo_to_load_the_turrets(self):
        import warnings

        from draftsman.data import items

        from generators import platform
        from tools import plan

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = platform.build(**self.DESIGN).to_dict()
            fed = platform.build(**{k: v for k, v in self.FULL.items()}).to_dict()
        stock = plan.measure_stock(data, self.DESIGN)
        for name in {e["name"] for e in data["blueprint"]["entities"]}:
            self.assertEqual(stock[name], items.raw[name]["stack_size"], name)
        self.assertEqual(stock["space-platform-foundation"], 100)
        self.assertNotIn("railgun-ammo", stock)  # not fed: the script stocks the turrets
        stock = plan.measure_stock(fed, self.FULL)
        self.assertGreaterEqual(stock["railgun-ammo"], 16 * 10)
        self.assertGreaterEqual(stock["uranium-rounds-magazine"], 16 * 10)
        self.assertIn("fusion-power-cell", stock)
        self.assertLessEqual(plan.slots(stock), plan.capacity(self.FULL))

    def test_a_design_that_keeps_no_promethium_cant_plan_a_trip(self):
        from tools import plan

        self.assertIn("promethium", plan.cannot_plan({"railguns": 8, "guns": 4, "thrusters": 1, "fuel": True}))
        self.assertIsNone(plan.cannot_plan(self.DESIGN))

    def test_research_is_the_flights_own_unless_given(self):
        from generators.platform import FEED_RESEARCH
        from tools import plan

        self.assertEqual(plan.research_for(self.DESIGN), {**plan.MIX_RESEARCH, "construction-robotics": 1})
        self.assertEqual(plan.research_for(self.FULL), {**plan.MIX_RESEARCH, "construction-robotics": 1, **FEED_RESEARCH})
        self.assertEqual(plan.research_for(self.DESIGN, {"railgun-damage": 4}), {"railgun-damage": 4})

    def test_readying_follows_the_designs_options(self):
        from tools import plan

        full = " ".join(plan.readying(self.FULL)).lower()
        for word in ("reserve", "fusion power cells", "fluoroketone", "accumulator", "feed", "24 cargo bays"):
            self.assertIn(word, full)
        plain = " ".join(plan.readying({"railguns": 8, "guns": 4, "thrusters": 1})).lower()
        for word in ("thruster fuel", "power", "load the turrets"):
            self.assertIn(word, plain)

    def test_a_plan_from_measured_rates(self):
        from tools import plan

        p = plan.assemble(self.DESIGN, "edge", plan.research_for(self.DESIGN), runs=8, rows=self.rows())
        trip = p["trip"]
        self.assertGreater(trip["chunks"], 0)
        self.assertAlmostEqual(trip["packs"], trip["chunks"] * 10 / 25)
        self.assertLessEqual(trip["loaded slots"], 59)
        self.assertEqual(sum(s["slots"] for s in p["stock"]), trip["loaded slots"])
        for s in p["stock"]:  # whole stacks: they cost no more slots
            from draftsman.data import items
            self.assertEqual(s["count"] % items.raw[s["item"]]["stack_size"], 0, s)
        self.assertIn(p["limit"]["by"], ("hub slots", "the chance of coming back"))
        self.assertEqual(sorted(p["measured"]), [0.0, 0.1])
        self.assertTrue(p["readying"] and p["caveats"])
        self.assertIsNone(p["error"])
        json.dumps(p)  # for --json and the site
        text = plan.text(p)
        for word in ("turn", "uranium-rounds-magazine", "Before leaving"):
            self.assertIn(word, text)

    def test_a_plan_says_when_no_trip_fits(self):
        from tools import plan

        p = plan.assemble(self.DESIGN, "edge", {}, runs=8, rows=self.rows(magazines=(100_000.0, 200_000.0)))
        self.assertIsNone(p["trip"])
        self.assertIn("fits", p["error"])
        self.assertIn("fits", plan.text(p))

    def test_spares_are_the_platforms_own_count_of_each_part_in_whole_stacks(self):
        # The margin (NOTES.md: lost trips ran out of spare railguns first, then huge asteroids took
        # the bays; trips that kept about one platform's worth of railguns in spares all came back):
        # at least as many of each part as the platform has, enough to rebuild it once.
        import warnings

        from draftsman.data import items

        from generators import platform
        from tools import plan

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            data = platform.build(**self.FULL).to_dict()
        counts = {}
        for e in data["blueprint"]["entities"]:
            counts[e["name"]] = counts.get(e["name"], 0) + 1
        spares = plan.spares(data)
        self.assertEqual(set(spares), {n for n in counts if n in items.raw})
        for name, n in spares.items():
            self.assertGreaterEqual(n, counts[name], name)
            self.assertEqual(n % items.raw[name]["stack_size"], 0, name)
        self.assertEqual((spares["railgun-turret"], spares["cargo-bay"], spares["fusion-reactor"]), (20, 30, 2))

    def test_the_margin_scales_each_kind_of_use_by_the_spread_whole_trips_showed(self):
        # NOTES.md, whole trips: ammo ran out in the last 3 to 10% of most trips at the mean; a bad
        # trip used about twice the mean of foundation and two to three times of the parts the hub
        # rebuilt. Ammo a tenth over, foundation twice, parts three times; anything else as used.
        from tools import plan

        scale = plan.margin({"railgun-ammo", "uranium-rounds-magazine", "space-platform-foundation", "crusher",
                             "fusion-power-cell"}, parts={"crusher"})
        self.assertEqual(scale, {"railgun-ammo": 1.1, "uranium-rounds-magazine": 1.1,
                                 "space-platform-foundation": 2.0, "crusher": 3.0})

    def test_a_plan_carries_its_spares_and_says_what_rule_sized_them(self):
        from tools import plan

        parts = {"railgun-turret": 10, "gun-turret": 50}
        bare = plan.assemble(self.DESIGN, "edge", {}, runs=8, rows=self.rows())
        p = plan.assemble(self.DESIGN, "edge", {}, runs=8, rows=self.rows(), spares=parts)
        stock = {s["item"]: s["count"] for s in p["stock"]}
        self.assertEqual((stock["railgun-turret"], stock["gun-turret"]), (10, 50))
        self.assertEqual(sum(s["slots"] for s in p["stock"]), p["trip"]["loaded slots"])
        self.assertLess(p["trip"]["chunks"], bare["trip"]["chunks"])  # spares take room from chunks
        self.assertEqual(p["margin"]["spares"], parts)
        self.assertIn("rebuild", p["margin"]["rule"])
        self.assertIn("rebuild", plan.text(p))

    @staticmethod
    def leg(a, b, km, held=None):
        out = {"from": a, "to": b, "km": km, "seconds": km / 50, "used": {}, "died": {}}
        if held is not None:
            out["held"] = held
        return out

    def flights_to_the_edge(self, finished, stranded, destroyed):
        # Flights from Nauvis: each gets to Aquilo; then finishes the leg to the edge, strands on it
        # short of oxidizer (out of time, its lines nearly empty of it), or is destroyed on it.
        edge, out = "solar-system-edge", self.leg("nauvis", "aquilo", 45000.0, {"thruster-fuel": 9000.0,
                                                                                "thruster-oxidizer": 6000.0})
        done = [{"back": True, "ended": "back", "legs": [out, self.leg("aquilo", edge, 100000.0),
                                                         self.leg(edge, edge, 160000.0)]}] * finished
        stuck = [{"back": False, "ended": "out of time",
                  "legs": [out, self.leg("aquilo", None, 30000.0, {"thruster-fuel": 4000.0,
                                                                  "thruster-oxidizer": 2.0})]}] * stranded
        dead = [{"back": False, "ended": "hub destroyed by huge-oxide-asteroid",
                 "legs": [out, self.leg("aquilo", None, 60000.0, {"thruster-fuel": 9000.0,
                                                                 "thruster-oxidizer": 8000.0})]}] * destroyed
        return done + stuck + dead

    def test_a_leg_counts_the_flights_stranded_on_it_and_what_they_ran_short_of(self):
        from tools import plan

        rates = plan.leg_rates(self.flights_to_the_edge(finished=2, stranded=5, destroyed=1))
        crossing = next(r for r in rates if r["from"] == "aquilo")
        self.assertEqual((crossing["flights"], crossing["finished"], crossing["stranded"], crossing["destroyed"]),
                         (8, 2, 5, 1))
        self.assertEqual(crossing["short of"], "thruster-oxidizer")
        first = rates[0]
        self.assertEqual((first["stranded"], first["destroyed"], first["short of"]), (0, 0, None))

    def test_a_plan_from_nauvis_says_plainly_when_its_flights_strand_on_a_leg(self):
        # More flights stranded on a leg than finished it: no trip, and the plan says which leg and
        # what ran short, not a trip it can't make. Fewer: a trip, with the strandings in its caveats.
        from tools import plan

        stuck = plan.leg_rates(self.flights_to_the_edge(finished=2, stranded=5, destroyed=1))
        p = plan.assemble(self.DESIGN, "nauvis", {}, runs=8, rows=self.rows(), legs=stuck)
        self.assertIsNone(p["trip"])
        for word in ("aquilo", "stranded", "5 of 8", "thruster-oxidizer"):
            self.assertIn(word, p["error"])
        self.assertIn("stranded", plan.text(p))
        some = plan.leg_rates(self.flights_to_the_edge(finished=7, stranded=1, destroyed=0))
        p = plan.assemble(self.DESIGN, "nauvis", {}, runs=8, rows=self.rows(), legs=some)
        self.assertIsNotNone(p["trip"])
        self.assertTrue(any("stranded" in c and "thruster-oxidizer" in c for c in p["caveats"]))

    def test_a_plan_from_nauvis_says_when_it_cant_get_there(self):
        from tools import plan

        legs = [{"part": "out", "from": "nauvis", "to": "fulgora", "km": 15000.0, "finished": 2, "flights": 2,
                 "row": {"uranium-rounds-magazine": 5.0, "km/s": 76.0, "deaths": 0.0}},
                {"part": "out", "from": "aquilo", "to": None, "km": None, "finished": 0, "flights": 2,
                 "row": {"uranium-rounds-magazine": 14.0, "km/s": 20.0, "deaths": 0.0}}]
        p = plan.assemble(self.DESIGN, "nauvis", {}, runs=2, rows=self.rows(), legs=legs)
        self.assertIsNone(p["trip"])
        self.assertIn("aquilo", p["error"])

    def filed(self, lib):
        # A library with DESIGN's blueprint filed, and its plan as bp plan makes one.
        from tools import plan
        from tools.library import save

        data = plan.build("platform", self.DESIGN)
        path = save(data, lib)
        p = {**plan.assemble(self.DESIGN, "edge", {}, runs=8, rows=self.rows()), "generator": "platform",
             "game": "2.1.23"}
        return data, path, p

    def test_a_plan_is_filed_beside_the_blueprint_its_design_builds(self):
        from tools import plan
        from tools.library import plan_path

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            data, path, p = self.filed(lib)
            self.assertEqual(plan.save(p, data, lib, "2.1.23"), plan_path(path))
            self.assertEqual(json.loads(plan_path(path).read_text()), json.loads(json.dumps(p)))
            self.assertEqual(plan.problems(lib, "2.1.23"), [])

    def test_a_plan_is_refused_without_its_blueprint_filed_on_another_game_or_with_no_trip(self):
        from tools import plan

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            data, path, p = self.filed(lib)
            with self.assertRaisesRegex(ValueError, "file it first"):
                plan.save(p, plan.build("platform", {**self.DESIGN, "railguns": 9}), lib, "2.1.23")
            with self.assertRaisesRegex(ValueError, "2.1.22"):
                plan.save({**p, "game": "2.1.22"}, data, lib, "2.1.23")
            with self.assertRaisesRegex(ValueError, "no trip"):
                plan.save({**p, "trip": None, "error": "its flights failed: x"}, data, lib, "2.1.23")
            self.assertEqual(list(lib.rglob("*.plan.json")), [])

    def test_a_filed_plan_goes_stale_when_its_design_or_game_moves_on(self):
        from tools import plan
        from tools.library import plan_path

        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            data, path, p = self.filed(lib)
            plan.save(p, data, lib, "2.1.23")
            self.assertIn("2.1.24", " ".join(plan.problems(lib, "2.1.24")))  # the library's target moved on
            stale = json.loads(plan_path(path).read_text())
            stale["design"]["railguns"] = 9
            plan_path(path).write_text(json.dumps(stale))
            self.assertIn("no longer builds", " ".join(plan.problems(lib, "2.1.23")))
            path.unlink()
            self.assertIn("beside it", " ".join(plan.problems(lib, "2.1.23")))

    def test_every_filed_plan_holds(self):
        # CI: a plan filed in library/ still matches its blueprint and the game the library targets.
        from tools import plan
        from tools.settings import registry

        self.assertEqual(plan.problems(Path(__file__).parent.parent / "library", registry()["game_version"]), [])

    def test_a_check_against_trips_flown_whole(self):
        from tools import plan

        p = plan.assemble(self.DESIGN, "edge", {}, runs=8, rows=self.rows())
        means = {"flights": 4, "came back": 0.75, "chunks back": 30.0, "chunks per trip back": 40.0, "first out": {}}
        check = plan.compare(p, means)
        self.assertEqual(check["planned"]["comes back"], p["trip"]["comes back"])
        self.assertEqual(check["flown"]["came back"], 0.75)
        self.assertIn("chunks a trip", plan.compare_text(check))
        # Flown under a shock (railguns killed as it turns), the check says so.
        shocked = plan.compare(p, means, shock=8)
        self.assertEqual(shocked["flown"]["shock"], 8)
        self.assertIn("8 railguns killed", plan.compare_text(shocked))


if __name__ == "__main__":
    unittest.main()
