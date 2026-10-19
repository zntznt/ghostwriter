"""Tests for library blueprints, kept in a file next to the blueprint:

  library/logistics/gated-requester.json
  library/logistics/gated-requester.tests.toml

  [ids]                       # optional names for entity_numbers
  chest = 1
  decider = 2

  [[case]]                    # circuit test (validator/circuit.py)
  name = "surplus: request"
  ticks = 10                  # optional
  inject = [{ entity = "decider", connector = "combinator_input_green", signals = { iron-plate = 150 } }]
  expect = [{ entity = "chest", requests = { iron-plate = 100 } }]

  [[flow]]                    # flow test (validator/flow.py)
  name = "rated output"
  feed = [{ entity = "ore", lane = "left", item = "iron-ore" }]
  drain = ["plates"]
  expect = { iron-plate = 7.5 }
  fluids = [{ entity = "pipe_end", fluid = "water" }]   # optional: unlimited sources
  warmup = 60                 # optional, like window and tolerance

Loading checks every entity, connector, lane and item name, so a typo fails
without a game.
"""

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from draftsman.constants import WireConnectorID
from draftsman.data import fluids, items

from validator.circuit import Case, Expect, Inject
from validator.flow import Feed, FluidFeed, Flow

TESTS_SUFFIX = ".tests.toml"
CONNECTORS = {name.lower() for name in WireConnectorID.__members__ if "COPPER" not in name}  # aliases included
LANES = {"left", "right"}


@dataclass
class Tests:
    cases: list[Case] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)


def tests_path(blueprint_file: Path) -> Path:
    return blueprint_file.with_name(blueprint_file.stem + TESTS_SUFFIX)


def load_tests(path: Path, data: dict | None = None) -> Tests:
    """Parse a tests file. With the blueprint's data, also check every entity
    it names exists."""
    where = path.name
    spec = tomllib.loads(path.read_text())
    _keys(spec, {"ids", "case", "flow"}, where)
    ids = spec.get("ids", {})
    if not all(isinstance(k, str) and isinstance(v, int) for k, v in ids.items()):
        raise ValueError(f"{where}: [ids] maps names to entity_numbers")
    numbers = {e["entity_number"] for e in data["blueprint"].get("entities", [])} if data else None

    def entity(ref, ctx):
        n = ids.get(ref) if isinstance(ref, str) else ref
        if not isinstance(n, int) or (numbers is not None and n not in numbers):
            raise ValueError(f"{ctx}: entity {ref!r} is not in the blueprint")
        return ref

    def connector(name, ctx):
        if name is not None and name not in CONNECTORS:
            raise ValueError(f"{ctx}: connector {name!r} is not one of {sorted(CONNECTORS)}")
        return name

    def item(name, ctx):
        if name not in items.raw:
            raise ValueError(f"{ctx}: unknown item {name!r}")
        return name

    tests = Tests(ids=ids)
    for i, c in enumerate(spec.get("case", [])):
        ctx = f"{where} case {i + 1} ({c.get('name', 'unnamed')})"
        _keys(c, {"name", "inject", "expect", "ticks"}, ctx)
        inject, expect = [], []
        for x in c.get("inject", []):
            _keys(x, {"entity", "connector", "signals"}, ctx)
            inject.append(Inject(entity(x["entity"], ctx), connector(x["connector"], ctx), dict(x["signals"])))
        for x in c.get("expect", []):
            _keys(x, {"entity", "connector", "signals", "requests"}, ctx)
            if "signals" in x and "connector" not in x:
                raise ValueError(f"{ctx}: expected signals need a connector")
            expect.append(Expect(entity(x["entity"], ctx), connector(x.get("connector"), ctx),
                                 x.get("signals"), x.get("requests")))
        if not expect:
            raise ValueError(f"{ctx}: a case needs at least one expect")
        tests.cases.append(Case(c["name"], inject, expect, c.get("ticks", 10)))
    for i, f in enumerate(spec.get("flow", [])):
        ctx = f"{where} flow {i + 1} ({f.get('name', 'unnamed')})"
        _keys(f, {"name", "feed", "drain", "expect", "warmup", "window", "tolerance", "fluids"}, ctx)
        feed = []
        for x in f.get("feed", []):
            _keys(x, {"entity", "lane", "item"}, ctx)
            if x["lane"] not in LANES:
                raise ValueError(f"{ctx}: lane {x['lane']!r} is not one of {sorted(LANES)}")
            feed.append(Feed(entity(x["entity"], ctx), x["lane"], item(x["item"], ctx)))
        sources = []
        for x in f.get("fluids", []):
            _keys(x, {"entity", "fluid"}, ctx)
            if x["fluid"] not in fluids.raw:
                raise ValueError(f"{ctx}: unknown fluid {x['fluid']!r}")
            sources.append(FluidFeed(entity(x["entity"], ctx), x["fluid"]))
        drain = [entity(d, ctx) for d in f.get("drain", [])]
        expect = {item(k, ctx): v for k, v in f.get("expect", {}).items()}
        if not (feed and drain and expect):
            raise ValueError(f"{ctx}: a flow needs feed, drain and expect")
        defaults = Flow("", [], [], {})
        tests.flows.append(Flow(f["name"], feed, drain, expect, f.get("warmup", defaults.warmup),
                                f.get("window", defaults.window), f.get("tolerance", defaults.tolerance), sources))
    if not (tests.cases or tests.flows):
        raise ValueError(f"{where}: no [[case]] or [[flow]]")
    return tests


def library_tests(library: Path) -> list[tuple[str, dict, Tests]]:
    """(label, blueprint data, tests) for every library blueprint with a tests file."""
    out = []
    for path in sorted(library.rglob("*" + TESTS_SUFFIX)):
        blueprint_file = path.with_name(path.name[: -len(TESTS_SUFFIX)] + ".json")
        label = str(blueprint_file.relative_to(library))
        if not blueprint_file.is_file():
            raise ValueError(f"{path.relative_to(library)}: no {blueprint_file.name} next to it")
        data = json.loads(blueprint_file.read_text())
        data.pop("index", None)
        if "blueprint" not in data:
            raise ValueError(f"{label}: tests need a blueprint, not a {next(iter(data))}")
        out.append((label, data, load_tests(path, data)))
    return out


def _keys(d: dict, allowed: set, ctx: str):
    unknown = set(d) - allowed
    if unknown:
        raise ValueError(f"{ctx}: unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")
