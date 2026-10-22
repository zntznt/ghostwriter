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
        from tools import studies
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
        means = studies.whole_trip_means(rows)
        self.assertEqual(means["came back"], 0.5)
        self.assertEqual(means["chunks back"], 150)  # over every flight: a lost one brings back none
        self.assertEqual(means["chunks per trip back"], 300)  # over the ones that came back
        self.assertEqual(means["left, came back"], {"railgun-ammo": 10, "space-platform-foundation": 0})
        self.assertEqual(means["first out"], {"space-platform-foundation": 1})
        self.assertEqual((means["km"], means["dry turret s"]), (116_000, 2.5))

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

    def test_leg_rates_come_from_every_flight_that_got_onto_the_leg(self):
        # platform-legs: per leg, in the order flown (out, the route, home), what the hub gave per
        # 1,000 km over all the km flown on it (a leg it died on counts its km too), the chunks it
        # brought in, its km/s, and the trips lost on it per 1,000 km.
        from tools import studies

        def leg(a, b, km, s, used):
            return {"from": a, "to": b, "km": km, "seconds": s, "used": used, "died": {}}

        edge = "solar-system-edge"
        back = {"back": True, "legs": [leg("nauvis", "aquilo", 45000, 900, {"railgun-ammo": 9}),
                                       leg("aquilo", edge, 100000, 2000, {"railgun-ammo": 200}),
                                       leg(edge, edge, 160000, 3200, {"railgun-ammo": 300, "promethium-asteroid-chunk": -480}),
                                       leg(edge, "aquilo", 100000, 1600, {"railgun-ammo": 100})]}
        lost = {"back": False, "legs": [leg("nauvis", "aquilo", 45000, 900, {"railgun-ammo": 9}),
                                        leg("aquilo", None, 50000, 1000, {"railgun-ammo": 250})]}
        rates = studies.leg_rates([back, lost])
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
        self.assertEqual([r["part"] for r in studies.leg_rates([died])], ["out", "route"])

    def test_the_planned_trip_with_legs_loads_its_plan_and_flies_its_stay_further_out(self):
        # A platform can't hold still on a connection, so a stay is flown as half further out and
        # half back (NOTES.md), and the load is what the plan uses, in whole items.
        from tools import studies

        best = {"turn": 0.004, "stay": 50000.0, "used": {"railgun-ammo": 4550.4, "cargo-bay": 2.7, "pipe": 0.0}}
        turn, stock = studies.planned_trip(best)
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
        from tools import studies

        rates = [{"part": "out", "from": "nauvis", "to": "aquilo", "km": 45000, "row": {"km/s": 50.0}},
                 {"part": "out", "from": "aquilo", "to": "solar-system-edge", "km": 100000, "row": {"km/s": 60.0}},
                 {"part": "route", "from": "solar-system-edge", "to": None, "km": None, "row": {}}]
        legs = studies.legs_home(rates)
        self.assertEqual([(r["part"], r["from"], r["to"], r["km"], r["mirrored"]) for r in legs[-2:]],
                         [("home", "solar-system-edge", "aquilo", 100000, True), ("home", "aquilo", "nauvis", 45000, True)])
        measured = rates + [{"part": "home", "from": "solar-system-edge", "to": "aquilo", "km": 100000, "row": {}}]
        self.assertEqual(studies.legs_home(measured), measured)  # measured ones stand

    def test_lost_trips_count_the_bays_and_railguns_that_died(self):
        # What starts the cascade (NOTES.md): railguns die, huge asteroids get through to the bays.
        from tools import studies
        from validator.flight import Flown

        died = Flown("trip", odometer=70_000, seconds=1_400, ended="hub destroyed by huge asteroid",
                     deaths={"cargo-bay <- huge-oxide-asteroid": 10, "cargo-bay <- big-oxide-asteroid": 2,
                             "railgun-turret <- huge-carbonic-asteroid": 7, "gun-turret <- small-oxide-asteroid": 3})
        back = Flown("trip", odometer=162_000, seconds=3_000, survived=True, back=3_000,
                     deaths={"cargo-bay <- big-carbonic-asteroid": 1})
        means = studies.whole_trip_means([studies.whole_trip_flown(f, {}) for f in (died, back)])
        self.assertEqual((means["cargo bays died"], means["railgun turrets died"]), (6.5, 3.5))
        self.assertEqual(means["killed by huge asteroids"], 8.5)  # 17 of ours, over 2 flights

    def test_spares_round_up_to_whole_stacks_for_free(self):
        # A hub slot holds a whole stack, so a spare short of one costs nothing to fill (NOTES.md).
        import math

        from draftsman.data import items

        from tools import studies

        self.assertEqual(studies.full_stacks({"railgun-turret": 6, "fusion-reactor": 1, "uranium-rounds-magazine": 5450}),
                         {"railgun-turret": 10, "fusion-reactor": 1, "uranium-rounds-magazine": 5500})

        def slots(stock):
            return sum(math.ceil(n / items.raw[k]["stack_size"]) for k, n in stock.items())

        self.assertEqual([slots(s) for s in studies.CASCADE_STOCKS.values()], [530, 530, 533, 536])  # of 539

    def test_the_shock_study_opens_the_same_gap_for_each_load(self):
        # 8 railguns at once, just under the 9 to 21 lost trips lost, against 6, 10 and 20 spares.
        from tools import studies

        self.assertEqual(studies.SHOCK, 8)
        self.assertEqual([s["railgun-turret"] for s in studies.SHOCK_STOCKS.values()], [6, 10, 20])

    def test_fingerprint_changes_with_the_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1")
            first = optimise.fingerprint(["*.py"], root)
            (root / "a.py").write_text("x = 2")
            self.assertNotEqual(first, optimise.fingerprint(["*.py"], root))


if __name__ == "__main__":
    unittest.main()
