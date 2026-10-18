"""Circuit tests: run a blueprint's logic in the game and check what it does.

A generator declares cases next to build():

    def circuit_tests(**params) -> list[Case]:
        return [Case("surplus: request",
                     inject=[Inject("decider", "combinator_input_green", {"iron-plate": 150})],
                     expect=[Expect("chest", requests={"iron-plate": 100})])]

Entities are Draftsman ids (or entity_numbers). Connectors are
defines.wire_connector_id names: circuit_red, circuit_green,
combinator_input_red/green, combinator_output_red/green.

Per case: every injector is set to the case's signals (injectors the case
doesn't mention go quiet), the game runs `ticks` ticks, then each Expect is
read: `signals` is the exact set of non-zero signals on that network,
`requests` the exact logistic requests of a requester/buffer chest.

The harness powers everything through a global electric network and wires
injectors by script with no reach limit, so it tests logic, not power or
layout (validate() covers layout). Inputs are synthetic: a roboport's
"logistic network contents" becomes an injected signal set.
"""

import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from draftsman.constants import WireConnectorID

from tools.codec import encode
from validator.server import Server, ServerError
from validator.validate import Report


@dataclass
class Inject:
    entity: str | int
    connector: str
    signals: dict[str, int]


@dataclass
class Expect:
    entity: str | int
    connector: str | None = None
    signals: dict[str, int] | None = None
    requests: dict[str, int] | None = None


@dataclass
class Case:
    name: str
    inject: list[Inject] = field(default_factory=list)
    expect: list[Expect] = field(default_factory=list)
    ticks: int = 10


# Library blueprints carry their cases in a sibling file:
#   library/logistics/gated-requester.json
#   library/logistics/gated-requester.tests.toml
#
#   [ids]                       # optional names for entity_numbers
#   chest = 1
#   decider = 2
#
#   [[case]]
#   name = "surplus: request"
#   ticks = 10                  # optional
#   inject = [{ entity = "decider", connector = "combinator_input_green", signals = { iron-plate = 150 } }]
#   expect = [{ entity = "chest", requests = { iron-plate = 100 } }]
TESTS_SUFFIX = ".tests.toml"
CONNECTORS = {name.lower() for name in WireConnectorID.__members__ if "COPPER" not in name}  # aliases included


def tests_path(blueprint_file: Path) -> Path:
    return blueprint_file.with_name(blueprint_file.stem + TESTS_SUFFIX)


def load_cases(path: Path, data: dict | None = None) -> tuple[list[Case], dict[str, int]]:
    """Parse a tests file. With the blueprint's data, also check every entity
    and connector it names exists, so a typo fails without a game."""
    where = path.name
    spec = tomllib.loads(path.read_text())
    _keys(spec, {"ids", "case"}, where)
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

    cases = []
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
        cases.append(Case(c["name"], inject, expect, c.get("ticks", 10)))
    if not cases:
        raise ValueError(f"{where}: no [[case]]")
    return cases, ids


def library_tests(library: Path) -> list[tuple[str, dict, list[Case], dict[str, int]]]:
    """(label, blueprint data, cases, ids) for every library blueprint with a tests file."""
    out = []
    for path in sorted(library.rglob("*" + TESTS_SUFFIX)):
        blueprint_file = path.with_name(path.name[: -len(TESTS_SUFFIX)] + ".json")
        label = str(blueprint_file.relative_to(library))
        if not blueprint_file.is_file():
            raise ValueError(f"{path.relative_to(library)}: no {blueprint_file.name} next to it")
        data = json.loads(blueprint_file.read_text())
        data.pop("index", None)
        if "blueprint" not in data:
            raise ValueError(f"{label}: circuit tests need a blueprint, not a {next(iter(data))}")
        cases, ids = load_cases(path, data)
        out.append((label, data, cases, ids))
    return out


def _keys(d: dict, allowed: set, ctx: str):
    unknown = set(d) - allowed
    if unknown:
        raise ValueError(f"{ctx}: unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")


def run_circuit_tests(server: Server, data: dict, cases: list[Case], ids: dict[str, int], report: Report):
    """Append failures to report. ids maps Draftsman entity ids to entity_numbers."""
    names = {e["entity_number"]: e["name"] for e in data["blueprint"]["entities"]}

    def number(ref):
        return ref if isinstance(ref, int) else ids[ref]

    def label(ref):
        n = number(ref)
        return f"{ref} ({names[n]})" if isinstance(ref, str) else f"{n} ({names[n]})"

    setup = server.call("circuit_setup", encode(data))
    if "error" in setup:
        report.errors.append(f"circuit: setup failed: {setup['error']}")
        return
    if setup.get("unplaced") or setup.get("unplaceable") or setup.get("unrevived"):
        report.errors.append(f"circuit: could not build everything for the test: {setup}")
        return
    try:
        injectors = {(number(i.entity), i.connector) for c in cases for i in c.inject}
        for case in cases:
            given = {(number(i.entity), i.connector): i.signals for i in case.inject}
            for entity, connector in injectors:
                _ok(server.call("circuit_inject", json.dumps(
                    {"entity": entity, "connector": connector, "signals": given.get((entity, connector), {})}
                )))
            _wait_ticks(server, case.ticks)
            for exp in case.expect:
                got = server.call("circuit_read", json.dumps({"entity": number(exp.entity), "connector": exp.connector}))
                _ok(got)
                where = f"circuit '{case.name}': {label(exp.entity)}"
                if exp.signals is not None and _as_dict(got.get("signals")) != exp.signals:
                    report.errors.append(
                        f"{where} {exp.connector}: signals {_as_dict(got.get('signals'))}, expected {exp.signals}"
                    )
                if exp.requests is not None and _as_dict(got.get("requests")) != exp.requests:
                    report.errors.append(
                        f"{where}: requests {_as_dict(got.get('requests'))}, expected {exp.requests}"
                    )
    finally:
        server.call("circuit_teardown")


def _as_dict(value) -> dict:
    # Lua's table_to_json writes an empty table as [].
    return value if isinstance(value, dict) else {}


def _ok(reply: dict):
    if "error" in reply:
        raise ServerError(reply["error"])


def _wait_ticks(server: Server, ticks: int, timeout: float = 30):
    target = server.call("tick") + ticks
    deadline = time.monotonic() + timeout
    while server.call("tick") < target:
        if time.monotonic() > deadline:
            raise ServerError(f"game did not advance {ticks} ticks in {timeout}s (paused?)")
        time.sleep(0.02)
