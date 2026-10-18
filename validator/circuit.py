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
from dataclasses import dataclass, field

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
