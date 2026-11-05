"""Rates from prototype data: recipes, machines, belts and inserters.

Recipes, machines and belts come from Draftsman's data, which matches the
2.1.22 dump for every crafting recipe and machine (NOTES.md). Inserter rates
come from the game itself: tools/inserter_rates.json, written by
`python -m validator.measure`.

A machine crafts at its crafting_speed and its modules' speed effect (as bp
takes them, "modules=speed-module-3"), plus that of the beacons around it
("beacons=speed-module-3", each beacon's two slots filled). Productivity modules
also make more per craft (made()), only in machines and recipes that allow
productivity; elsewhere they aren't placed. A foundry, electromagnetic plant and
biochamber make 50% more whatever they hold (their base_effect). For a real
machine (tools.observe) the model also takes what it holds: mixed modules, some
slots empty, qualities, each beacon's quality and modules, the machine's quality
and the force's research productivity for the recipe; generators build with
none of those. Measured in a real game (2.1.24): a quality level makes a machine
craft 30% faster and a module's good effects 30% stronger; productivity, research
included, is capped at the recipe's maximum_productivity (+300%). An inserter moves what the
measurement saw. Inserter rates take research (as bp takes it,
"inserter-capacity-bonus=7"): none unless given, else the rates measured at it.

Power, as measured in game (2.1.23): a crafting machine draws its energy_usage
times 1 + its modules' and beacons' consumption bonus (floored at -80%) while
crafting, plus a drain of energy_usage / 30 always; burner machines none. A
beacon draws 480 kW whatever it holds. An inserter draws its drain plus the
energy it was measured to take per item moved (energy_per_item, with no research or at the
research a row is sized for).
"""

import json
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

from draftsman.data import entities, items, recipes

INSERTER_RATES = Path(__file__).resolve().parent / "inserter_rates.json"
DEFAULT_CRAFT_TIME = 0.5  # a recipe without energy_required
DEFAULT_CATEGORIES = ("crafting",)  # a recipe without categories
MAX_PRODUCTIVITY = 3  # a recipe without maximum_productivity
QUALITY = {"normal": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 5}  # levels, as prototypes.quality has them
QUALITY_STEP = 0.3  # a quality level adds this share to a machine's crafting speed and a module's good effects
LOWER_IS_BETTER = {"consumption", "pollution"}


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


def recipe_any(name: str, means: bool = False) -> Recipe:
    """Any recipe with fixed amounts (with `means`, any: a variable one counts at its mean):
    item ingredients in `ingredients`, fluid ones in `fluids`, and item and fluid products
    together in `results`."""
    if name not in recipes.raw:
        raise ValueError(f"unknown recipe {name!r}")
    r = recipes.raw[name]
    items, fluids, results = {}, {}, {}
    for p in r.get("ingredients", []):
        (fluids if p.get("type") == "fluid" else items)[p["name"]] = p["amount"]
    for p in r.get("results", []):
        if "amount" not in p or {"probability", "shared_probability", "independent_probability",
                                 "extra_count_fraction"} & set(p):
            if not means:
                raise ValueError(f"{name}: {p['name']} has a variable amount")
        results[p["name"]] = results.get(p["name"], 0) + _mean(p)
    return Recipe(name, r.get("energy_required", DEFAULT_CRAFT_TIME), items, results,
                  tuple(r.get("categories", DEFAULT_CATEGORIES)), fluids)


def _mean(p: dict) -> float:
    """A result's mean amount a craft."""
    shared = p.get("shared_probability", {})
    chance = p.get("independent_probability", p.get("probability", shared.get("max", 1) - shared.get("min", 0)))
    amount = p["amount"] if "amount" in p else (p["amount_min"] + p["amount_max"]) / 2
    return amount * chance + p.get("extra_count_fraction", 0)


def check_modules(modules: str = "") -> None:
    """Refuses what isn't a module; "" is none."""
    if not modules:
        return
    if modules not in items.raw:
        raise ValueError(f"unknown item {modules!r}")
    if items.raw[modules].get("type") != "module" or "effect" not in items.raw[modules]:
        raise ValueError(f"{modules} is not a module")


def check_beacon_modules(beacons: str = "") -> None:
    """Refuses what a beacon won't take: a module with a good effect the beacon doesn't
    allow (only speed and efficiency modules); "" is none."""
    check_modules(beacons)
    allowed = set(entities.raw["beacon"]["allowed_effects"])
    if beacons and {k for k, v in items.raw[beacons]["effect"].items() if _good(k, v)} - allowed:
        raise ValueError(f"a beacon doesn't take {beacons}")


def _module_effect(module: str, quality: str = "normal") -> dict[str, float]:
    """A module's effects at `quality`: the good ones (more speed, productivity and quality,
    less consumption and pollution) grow 30% a quality level, the bad ones don't."""
    check_modules(module)
    up = 1 + QUALITY_STEP * QUALITY[quality]
    return {k: v * up if _good(k, v) else v for k, v in items.raw[module]["effect"].items()}


def _good(effect: str, v: float) -> bool:
    return (v < 0) == (effect in LOWER_IS_BETTER)


def takes_beacons(machine: str) -> bool:
    """Whether beacons affect the machine: burner furnaces take none of their effects."""
    return entities.raw[machine].get("effect_receiver", {}).get("uses_beacon_effects", True)


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


def _held(machine: str, r: Recipe, modules) -> list[tuple[str, str]]:
    """(module, quality) per module in the machine's slots: `modules` in every slot it fills, or
    a list of what it really holds."""
    if isinstance(modules, str):
        return [(modules, "normal")] * module_slots(machine, modules, r)
    held = list(modules)
    if len(held) > (entities.raw[machine].get("module_slots") or 0):
        raise ValueError(f"{machine} has fewer slots than {len(held)} modules")
    return held


def _beacons(beacons, n: int) -> list[tuple[str, list[tuple[str, str]]]]:
    """(quality, [(module, quality)]) per beacon: `n` with `beacons` in both slots, or a list of
    what each really holds (an unpowered beacon holds nothing)."""
    slots = entities.raw["beacon"]["module_slots"]
    if isinstance(beacons, str):
        check_beacon_modules(beacons)
        return [("normal", [(beacons, "normal")] * slots)] * n if beacons else []
    out = [(q, list(held)) for q, held in beacons]
    for _, held in out:
        if len(held) > slots:
            raise ValueError(f"a beacon has {slots} slots, not {len(held)}")
        for m, _ in held:
            check_beacon_modules(m)
    return out


def effects(machine: str, r: Recipe, modules="", beacons="", n: int = 0) -> Counter:
    """The machine's bonuses crafting `r`, before the game's limits: its base effect plus what its
    modules and beacons give, as far as the machine and recipe allow them.

    `modules` is one module in every slot it fills, or a list of (module, quality) per module held.
    `beacons` is one module in both slots of each of `n` beacons, or a list of (quality, [(module,
    quality)]) per beacon (n is then ignored). Measured in game: each beacon passes on its modules'
    effects times its distribution_effectivity (+0.2 a quality level) and the profile for how many
    beacons reach the machine, empty and unpowered ones included."""
    proto, rec = entities.raw[machine], recipes.raw.get(r.name, {})
    receiver = proto.get("effect_receiver", {})
    allowed = set(proto.get("allowed_effects", ()))
    if not rec.get("allow_speed", True):
        allowed.discard("speed")
    if not rec.get("allow_productivity"):
        allowed.discard("productivity")
    total = Counter()
    if receiver.get("uses_module_effects", True):
        for m, q in _held(machine, r, modules):
            total.update(_module_effect(m, q))
    bs = _beacons(beacons, n)
    if bs and receiver.get("uses_beacon_effects", True):
        b = entities.raw["beacon"]
        share = b["profile"][min(len(bs), len(b["profile"])) - 1]
        for q, held in bs:
            reach = (b["distribution_effectivity"] + b["distribution_effectivity_bonus_per_quality_level"]
                     * QUALITY[q]) * share
            for m, mq in held:
                total.update({k: v * reach for k, v in _module_effect(m, mq).items()})
    out = Counter({k: v for k, v in total.items() if k in allowed})
    out.update(receiver.get("base_effect", {}))  # even where the recipe allows no productivity, as measured
    return out


def max_productivity(recipe: str) -> float:
    return recipes.raw.get(recipe, {}).get("maximum_productivity", MAX_PRODUCTIVITY)


def productivity(machine: str, r: Recipe, modules="", research: float = 0) -> float:
    """The extra share of results a machine makes per craft of `r`: its base effect and modules'
    (as effects() takes them) plus the force's `research` bonus for `r`, capped together at the
    recipe's maximum (+300%, measured)."""
    return min(max(0, effects(machine, r, modules)["productivity"]) + research, max_productivity(r.name))


def made(machine: str, r: Recipe, modules="", research: float = 0) -> Recipe:
    """`r` as the machine makes it: results with the productivity bonus, which skips a
    result's ignored_by_productivity part; `r` itself without one."""
    bonus = productivity(machine, r, modules, research)
    if not bonus:
        return r
    ignored = {}
    for p in recipes.raw[r.name].get("results", []):
        ignored[p["name"]] = ignored.get(p["name"], 0) + p.get("ignored_by_productivity", 0)
    return replace(r, results={k: v + bonus * (v - ignored.get(k, 0)) for k, v in r.results.items()})


def crafts_per_second(machine: str, r: Recipe, modules="", beacons="", n: int = 0, quality: str = "normal") -> float:
    """Crafts/s of one `quality` machine (30% faster a quality level), with `modules` and `beacons`
    as effects() takes them."""
    proto = entities.raw[machine]
    if not set(r.categories) & set(proto.get("crafting_categories", [])):
        raise ValueError(f"{machine} can't craft {r.name} (categories {list(r.categories)})")
    speed = effects(machine, r, modules, beacons, n)["speed"]
    # the game keeps 20% speed at least
    return proto["crafting_speed"] * (1 + QUALITY_STEP * QUALITY[quality]) * max(0.2, 1 + speed) / r.time


def watts(text: str) -> float:
    """'90kW' or '4MJ' as a number (watts or joules)."""
    number = text.rstrip("WJ")
    scale = {"k": 1e3, "M": 1e6, "G": 1e9}.get(number[-1], 1)
    return float(number.rstrip("kMG")) * scale


def machine_watts(machine: str, r: Recipe, modules="", beacons="", n: int = 0, utilisation: float = 1) -> float:
    """Average electric W of one machine crafting `r` for `utilisation` of the time, with
    `modules` and `beacons` as effects() takes them."""
    proto = entities.raw[machine]
    if not electric(machine):
        return 0
    usage = watts(proto["energy_usage"])
    drain = watts(proto["energy_source"]["drain"]) if "drain" in proto["energy_source"] else usage / 30
    bonus = effects(machine, r, modules, beacons, n)["consumption"]
    return drain + utilisation * usage * max(0.2, 1 + bonus)  # the game keeps 20% consumption at least


def beacon_watts() -> float:
    return watts(entities.raw["beacon"]["energy_usage"])


def inserter_watts(inserter: str, n: int, items_per_second: float, research: str | dict = "") -> float:
    """Average electric W of `n` inserters moving `items_per_second` between them, at the energy
    per item measured with no research or at `research`."""
    # ponytail: energy per item is measured off a yellow belt; a faster belt fills a researched
    # hand fuller a swing, so this overestimates it there (measure it per belt if that matters).
    if not n:
        return 0
    per_item = _measured(research).get("energy_per_item", {})
    if inserter not in per_item:
        key = research_key(research)
        raise ValueError(f"no measured energy per item for {inserter}"
                         f"{f' at {key}' if key else ''} (python -m validator.measure --energy"
                         f"{f' --research {key}' if key else ''})")
    return n * watts(entities.raw[inserter]["energy_source"]["drain"]) + per_item[inserter] * items_per_second


def megawatts(w: float) -> str:
    return f"{w / 1e6:.2g} MW"


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


def least_research(fits) -> str:
    """The least research measured (by levels in all) under which `fits(research)` raises no
    ValueError, or "" if none does."""
    for key in sorted(_inserter_file().get("researched", {}), key=lambda k: sum(research_levels(k).values())):
        try:
            fits(key)
            return key
        except ValueError:
            pass
    return ""


def slowest_belt(lane: float) -> str:
    """The slowest belt measured whose lane carries `lane` items/s, or ""."""
    belts = sorted(_inserter_file()["rates"]["inserter"], key=lane_rate)
    return next((b for b in belts if lane_rate(b) >= lane - 1e-9), "")


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
