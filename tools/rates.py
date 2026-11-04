"""Rates from prototype data: recipes, machines, belts and inserters.

Recipes, machines and belts come from Draftsman's data, which matches the
2.1.22 dump for every crafting recipe and machine (NOTES.md). Inserter rates
come from the game itself: tools/inserter_rates.json, written by
`python -m validator.measure`.

A machine crafts at its crafting_speed and its modules' speed effect (as bp
takes them, "modules=speed-module-3"), plus that of the beacons around it
("beacons=speed-module-3", each beacon's two slots filled); no power. Productivity modules
also make more per craft (made()), only in machines and recipes that allow
productivity; elsewhere they aren't placed. An inserter moves what the
measurement saw. Inserter rates take research (as bp takes it,
"inserter-capacity-bonus=7"): none unless given, else the rates measured at it.
"""

import json
import math
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

from draftsman.data import entities, items, recipes

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


def recipe_any(name: str) -> Recipe:
    """Any recipe with fixed amounts: item ingredients in `ingredients`, fluid ones in
    `fluids`, and item and fluid products together in `results`."""
    if name not in recipes.raw:
        raise ValueError(f"unknown recipe {name!r}")
    r = recipes.raw[name]
    items, fluids, results = {}, {}, {}
    for p in r.get("ingredients", []):
        (fluids if p.get("type") == "fluid" else items)[p["name"]] = p["amount"]
    for p in r.get("results", []):
        if "amount" not in p or {"probability", "shared_probability", "independent_probability"} & set(p):
            raise ValueError(f"{name}: {p['name']} has a variable amount")
        results[p["name"]] = results.get(p["name"], 0) + p["amount"]
    return Recipe(name, r.get("energy_required", DEFAULT_CRAFT_TIME), items, results,
                  tuple(r.get("categories", DEFAULT_CATEGORIES)), fluids)


def check_modules(modules: str = "") -> None:
    """Refuses what isn't a module; "" is none."""
    if not modules:
        return
    if modules not in items.raw:
        raise ValueError(f"unknown item {modules!r}")
    if items.raw[modules].get("type") != "module" or "effect" not in items.raw[modules]:
        raise ValueError(f"{modules} is not a module")


def check_beacon_modules(beacons: str = "") -> None:
    """Refuses what a beacon won't take: only speed and efficiency modules; "" is none."""
    check_modules(beacons)
    if beacons and {"productivity", "quality"} & {k for k, v in items.raw[beacons]["effect"].items() if v > 0}:
        raise ValueError(f"a beacon doesn't take {beacons}")


def beacon_speed(machine: str, beacons: str = "", n: int = 0) -> float:
    """The speed bonus `n` beacons with `beacons` in their slots give a machine."""
    check_beacon_modules(beacons)
    if not beacons or not n or not entities.raw[machine].get("effect_receiver", {}).get("uses_beacon_effects", True):
        return 0
    b = entities.raw["beacon"]
    return n * b["module_slots"] * items.raw[beacons]["effect"].get("speed", 0) * b["distribution_effectivity"] \
        * b["profile"][n - 1]


def module_slots(machine: str, modules: str = "", r: Recipe | None = None) -> int:
    """Slots a machine fills with `modules`: none without modules, slots or (for `r`) speed,
    or productivity where the machine or (for `r`) the recipe doesn't allow it."""
    check_modules(modules)
    if not modules or (r and not recipes.raw.get(r.name, {}).get("allow_speed", True)):
        return 0
    if items.raw[modules]["effect"].get("productivity", 0) > 0 and (
            "productivity" not in entities.raw[machine].get("allowed_effects", ())
            or (r and not recipes.raw.get(r.name, {}).get("allow_productivity"))):
        return 0
    return entities.raw[machine].get("module_slots") or 0


def productivity(machine: str, r: Recipe, modules: str = "") -> float:
    """The extra share of results a machine's modules make per craft of `r`."""
    slots = module_slots(machine, modules, r)
    return slots * items.raw[modules]["effect"].get("productivity", 0) if slots else 0


def made(machine: str, r: Recipe, modules: str = "") -> Recipe:
    """`r` as the machine makes it: results with the productivity bonus, which skips a
    result's ignored_by_productivity part; `r` itself without one."""
    bonus = productivity(machine, r, modules)
    if not bonus:
        return r
    ignored = {}
    for p in recipes.raw[r.name].get("results", []):
        ignored[p["name"]] = ignored.get(p["name"], 0) + p.get("ignored_by_productivity", 0)
    return replace(r, results={k: v + bonus * (v - ignored.get(k, 0)) for k, v in r.results.items()})


def crafts_per_second(machine: str, r: Recipe, modules: str = "", beacons: str = "", n: int = 0) -> float:
    """Crafts/s of one machine, with `modules` in its slots and `n` beacons of `beacons` around it."""
    proto = entities.raw[machine]
    if not set(r.categories) & set(proto.get("crafting_categories", [])):
        raise ValueError(f"{machine} can't craft {r.name} (categories {list(r.categories)})")
    slots = module_slots(machine, modules, r)
    speed = slots * items.raw[modules]["effect"].get("speed", 0) if slots else 0
    if recipes.raw.get(r.name, {}).get("allow_speed", True):
        speed += beacon_speed(machine, beacons, n)
    return proto["crafting_speed"] * max(0.2, 1 + speed) / r.time  # the game keeps 20% speed at least


def request_modules(entity, machine: str, r: Recipe, modules: str = "") -> None:
    """Fills a placed machine's module slots."""
    if slots := module_slots(machine, modules, r):
        entity.request_modules(modules, range(slots))


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
def _inserter_file() -> dict:
    return json.loads(INSERTER_RATES.read_text())


def research_levels(research: str | dict = "") -> dict[str, int]:
    """Technology series -> levels researched, from "a=1,b=2" (as bp takes it) or a dict."""
    if isinstance(research, dict):
        return research
    return {k: int(v) for k, _, v in (kv.partition("=") for kv in str(research).split(",") if kv)}


def research_key(research: str | dict = "") -> str:
    """The research as one sorted string, "" for none: the key measured rates are kept under."""
    return ",".join(f"{k}={v}" for k, v in sorted(research_levels(research).items()))


def _measured(research) -> dict:
    """The rates measured with no research, or at `research`."""
    key = research_key(research)
    if not key:
        return _inserter_file()
    got = _inserter_file().get("researched", {}).get(key)
    if got is None:
        raise ValueError(f"no inserter rates measured at {key} (python -m validator.measure --research {key})")
    return got


def handover_rate(inserter: str, research: str | dict = "") -> float:
    """Items/s one inserter moves from a machine's output straight into another's input, measured in game."""
    table = _measured(research).get("machine_to_machine", {})
    if inserter not in table:
        raise ValueError(f"no measured handover rate for {inserter} (python -m validator.measure --handover)")
    return table[inserter]


def inserter_rate(inserter: str, belt: str, from_belt: bool, research: str | dict = "") -> float:
    """Items/s one inserter moves off (from_belt) or onto a moving belt, measured in game."""
    table = _measured(research)["rates"]
    if inserter not in table or belt not in table[inserter]:
        raise ValueError(f"no measured rate for {inserter} on {belt} (python -m validator.measure)")
    return table[inserter][belt]["from_belt" if from_belt else "to_belt"]


def fewest_inserters(demand: float, belt: str, from_belt: bool, options, margin: float,
                     research: str | dict = "") -> tuple[str, int]:
    """(inserter, count) moving `demand` items/s on or off `belt` at `margin` of the
    measured rate: the fewest inserters, then the cheapest (earliest in options)."""
    best = min(((math.ceil(demand / (margin * inserter_rate(i, belt, from_belt, research)) - 1e-9), k), i)
               for k, i in enumerate(options))
    return best[1], best[0][0]
