"""Rates from prototype data: recipes, machines, belts and inserters.

Recipes, machines and belts come from Draftsman's data, which matches the
2.1.22 dump for every crafting recipe and machine (NOTES.md). Inserter rates
come from the game itself: tools/inserter_rates.json, written by
`python -m validator.measure`.

No modules, beacons or research: a machine crafts at its crafting_speed and
an inserter moves what the measurement saw.
"""

import json
import math
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from draftsman.data import entities, recipes

INSERTER_RATES = Path(__file__).resolve().parent / "inserter_rates.json"
DEFAULT_CRAFT_TIME = 0.5  # a recipe without energy_required
DEFAULT_CATEGORIES = ("crafting",)  # a recipe without categories


@dataclass(frozen=True)
class Recipe:
    name: str
    time: float
    ingredients: dict[str, float]  # item -> amount per craft
    results: dict[str, float]
    categories: tuple[str, ...]
    fluids: dict[str, float] = field(default_factory=dict)  # fluid ingredients, with fluids=True


def recipe(name: str, fluids: bool = False) -> Recipe:
    """An item recipe. Fluid ingredients are refused unless `fluids`, which keeps
    them apart in Recipe.fluids; fluid results and probabilistic results are refused."""
    if name not in recipes.raw:
        raise ValueError(f"unknown recipe {name!r}")
    r = recipes.raw[name]
    parts, fluid_parts = {}, {}
    for key in ("ingredients", "results"):
        parts[key] = {}
        for p in r.get(key, []):
            if p.get("type", "item") == "fluid" and key == "ingredients" and fluids:
                fluid_parts[p["name"]] = fluid_parts.get(p["name"], 0) + p["amount"]
                continue
            if p.get("type", "item") != "item":
                raise ValueError(f"{name}: {p['name']} is a {p['type']}; only item recipes are supported")
            if "amount" not in p or {"probability", "shared_probability", "independent_probability"} & set(p):
                raise ValueError(f"{name}: {p['name']} has a variable amount")
            parts[key][p["name"]] = parts[key].get(p["name"], 0) + p["amount"]
    return Recipe(name, r.get("energy_required", DEFAULT_CRAFT_TIME), parts["ingredients"], parts["results"],
                  tuple(r.get("categories", DEFAULT_CATEGORIES)), fluid_parts)


def crafts_per_second(machine: str, r: Recipe) -> float:
    proto = entities.raw[machine]
    if not set(r.categories) & set(proto.get("crafting_categories", [])):
        raise ValueError(f"{machine} can't craft {r.name} (categories {list(r.categories)})")
    return proto["crafting_speed"] / r.time


def size(entity: str) -> int:
    """Tiles across, from the collision box (square entities)."""
    (x1, _), (x2, _) = entities.raw[entity]["collision_box"]
    return math.ceil(x2 - x1)


def half_width(entity: str) -> float:
    """Half the collision box width: what a pole's supply area must overlap."""
    return entities.raw[entity]["collision_box"][1][0]


def electric(entity: str) -> bool:
    return entities.raw[entity].get("energy_source", {}).get("type") == "electric"


def travel_seconds(belt: str, tiles: float) -> float:
    """Seconds an item takes to ride `tiles` of belt."""
    return tiles / (entities.raw[belt]["speed"] * 60)


def warmup_seconds(belt: str, tiles: float, craft_seconds: float) -> float:
    """Game time before a row reaches steady state: items reach its far end, the
    last machine fills and finishes a couple of crafts, plus slack."""
    return 30 + travel_seconds(belt, tiles) + 2 * craft_seconds


def lane_rate(belt: str) -> float:
    """Items/s one belt lane carries: 4 items per tile per lane; speed is tiles per tick."""
    return entities.raw[belt]["speed"] * 60 * 4


@cache
def _inserter_table() -> dict:
    return json.loads(INSERTER_RATES.read_text())["rates"]


def inserter_rate(inserter: str, belt: str, from_belt: bool) -> float:
    """Items/s one inserter moves off (from_belt) or onto a moving belt, measured in game."""
    table = _inserter_table()
    if inserter not in table or belt not in table[inserter]:
        raise ValueError(f"no measured rate for {inserter} on {belt} (python -m validator.measure)")
    return table[inserter][belt]["from_belt" if from_belt else "to_belt"]


def fewest_inserters(demand: float, belt: str, from_belt: bool, options, margin: float) -> tuple[str, int]:
    """(inserter, count) moving `demand` items/s on or off `belt` at `margin` of the
    measured rate: the fewest inserters, then the cheapest (earliest in options)."""
    best = min(((math.ceil(demand / (margin * inserter_rate(i, belt, from_belt)) - 1e-9), k), i)
               for k, i in enumerate(options))
    return best[1], best[0][0]
