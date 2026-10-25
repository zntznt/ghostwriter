"""Inserter research: rates chosen by research, and default builds unchanged by it."""

import hashlib
import json
import unittest
import warnings
from pathlib import Path

from generators import assembly_row, cell, factory, fluid_block, fluid_row, production, smelting_column

FIXTURE = Path(__file__).parent / "fixtures" / "default_builds.json"

# Default builds (no research) of every generator that sizes inserters from measured
# rates, and of the books and factories built from them: recorded before `research=`
# existed, so the option can't change what a request without it builds.
DEFAULT_REQUESTS = [
    ("assembly_row", {"recipe": "electronic-circuit"}),
    ("assembly_row", {"recipe": "iron-gear-wheel", "machine": "assembling-machine-3", "belt": "fast-transport-belt"}),
    ("assembly_row", {"recipe": "advanced-circuit"}),
    ("assembly_row", {"recipe": "logistic-science-pack"}),
    ("fluid_row", {"recipe": "plastic-bar"}),
    ("fluid_row", {"recipe": "battery"}),
    ("fluid_row", {"recipe": "processing-unit"}),
    ("fluid_block", {"recipe": "sulfuric-acid"}),
    ("fluid_block", {"recipe": "lubricant"}),
    ("fluid_block", {"recipe": "advanced-oil-processing"}),
    ("cell", {}),
    ("cell", {"recipe": "inserter", "producer": "iron-gear-wheel"}),
    ("cell", {"recipe": "advanced-circuit", "producer": "copper-cable", "belts": 2}),
    ("smelting_column", {}),
    ("production", {"item": "electronic-circuit", "rate": 5}),
    ("factory", {"item": "electronic-circuit", "rate": 5}),
    ("factory", {"item": "plastic-bar", "rate": 5}),
    ("factory", {"item": "electronic-circuit", "rate": 5, "layout": "bus"}),
]
GENERATORS = {"assembly_row": assembly_row, "fluid_row": fluid_row, "fluid_block": fluid_block, "cell": cell,
              "smelting_column": smelting_column, "production": production, "factory": factory}


def default_build_hashes() -> dict[str, str]:
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name, params in DEFAULT_REQUESTS:
            data = GENERATORS[name].build(**params).to_dict()
            key = f"{name} {json.dumps(params, sort_keys=True)}"
            out[key] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return out


class Rates(unittest.TestCase):
    FAKE = {"rates": {"inserter": {"transport-belt": {"from_belt": 0.9, "to_belt": 0.85}}},
            "machine_to_machine": {"inserter": 0.85},
            "researched": {"inserter-capacity-bonus=7": {
                "rates": {"inserter": {"transport-belt": {"from_belt": 2.4, "to_belt": 2.2}}},
                "machine_to_machine": {"inserter": 2.3}}}}

    def setUp(self):
        from unittest import mock

        from tools import rates
        self.rates = rates
        patch = mock.patch.object(rates, "_inserter_file", lambda: self.FAKE)
        patch.start()
        self.addCleanup(patch.stop)

    def test_research_reads_as_bp_takes_it(self):
        r = self.rates
        self.assertEqual(r.research_levels("inserter-capacity-bonus=7"), {"inserter-capacity-bonus": 7})
        self.assertEqual(r.research_levels(""), {})
        self.assertEqual(r.research_levels({"b": 2, "a": 1}), {"b": 2, "a": 1})
        self.assertEqual(r.research_key("b=2,a=1"), "a=1,b=2")
        self.assertEqual(r.research_key(""), "")

    def test_rates_follow_research(self):
        r = self.rates
        self.assertEqual(r.inserter_rate("inserter", "transport-belt", True), 0.9)
        self.assertEqual(r.inserter_rate("inserter", "transport-belt", True, "inserter-capacity-bonus=7"), 2.4)
        self.assertEqual(r.handover_rate("inserter", "inserter-capacity-bonus=7"), 2.3)
        # 2 items/s at 85% of the rate: 3 inserters without research, 1 with it.
        self.assertEqual(r.fewest_inserters(2, "transport-belt", True, ("inserter",), 0.85), ("inserter", 3))
        self.assertEqual(r.fewest_inserters(2, "transport-belt", True, ("inserter",), 0.85,
                                            "inserter-capacity-bonus=7"), ("inserter", 1))

    def test_unmeasured_research_says_how_to_measure_it(self):
        with self.assertRaisesRegex(ValueError, "validator.measure --research inserter-capacity-bonus=4"):
            self.rates.inserter_rate("inserter", "transport-belt", True, "inserter-capacity-bonus=4")


class DefaultBuilds(unittest.TestCase):
    def test_default_builds_are_unchanged(self):
        self.assertEqual(default_build_hashes(), json.loads(FIXTURE.read_text()))


if __name__ == "__main__":
    unittest.main()
