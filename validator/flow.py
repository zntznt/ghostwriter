"""Flow tests: run a blueprint's belts and machines in the game and measure throughput.

A generator declares cases next to build():

    def flow_tests(**params) -> list[Flow]:
        return [Flow("rated output",
                     feed=[Feed("ore_left", "left", "iron-ore"), Feed("ore_left", "right", "coal")],
                     drain=["plates"], expect={"iron-plate": 7.5})]

Several blueprints run as one (a production book) link through a pool: what
drains off belts, and the fluid machines make, is credited to it, and feeds
marked `pooled` only hand out what it holds. `caps` give backpressure: once
the pool holds that much of an item (or fluid), its drains (or sinks) stop,
so the belt (or pipe) backs up and the row making it slows. `at_least` makes
`expect` a target to reach rather than a rate to match.

A FluidFeed attaches an unlimited source of a fluid (an infinity pipe) to a
free tile beside the named entity, usually the end of a pipe, or with
sink=True an unlimited drain. `expect` may name fluids: their rate is what the
machines made (the force's fluid production statistics), in units/s.

Library blueprints keep theirs in a tests file (validator/tests_file.py).
Entities are Draftsman ids (or entity_numbers). A Feed keeps one lane of a
belt full from its back end ("left"/"right" as seen along the belt). A drain
belt is emptied every tick and what left it is counted. After `warmup`
seconds the counts restart, and after `window` more seconds each expected
item's rate must be within `tolerance` of its target.

Unlike circuit tests, power comes only through the blueprint's own poles: a
script-wired pole joins them to an unlimited source. An inserter outside
every supply area, or a pole off the network, starves its machine and the
rate falls short. The force has no research, so inserters move one item
per swing, unless the case sets `research` (as bp takes it, a series and its
level), as a generator sized for research does.
"""

import json
from dataclasses import dataclass, field

from draftsman.data import fluids as fluid_data

from tools.codec import encode
from validator.circuit import _ok, _wait_ticks
from validator.server import Server
from validator.validate import Report

# Game speed during flow tests. The headless server runs as fast as the CPU
# allows up to 60 * SPEED ticks per second.
SPEED = 100


@dataclass
class Feed:
    entity: str | int
    lane: str
    item: str
    pooled: bool = False  # hand out only what drains have put in the pool


@dataclass
class FluidFeed:
    entity: str | int  # a pipe (or anything with a fluid connection) to attach the source to
    fluid: str
    side: str = ""  # north/south/east/west of the entity; default the first free tile
    sink: bool = False  # drain the fluid instead of supplying it
    pooled: bool = False  # insert only what machines have made, straight into the entity
    level: float = 0  # sink: drain down to this share of full, leaving the rest for users on the line


@dataclass
class Flow:
    name: str
    feed: list[Feed]
    drain: list[str | int]
    expect: dict[str, float]  # items per second at the drains
    warmup: float = 60  # seconds of game time
    window: float = 120
    tolerance: float = 0.03
    fluids: list[FluidFeed] = field(default_factory=list)
    at_least: bool = False  # a target to reach, not a rate to match
    caps: dict[str, float] = field(default_factory=dict)  # pool level that stops draining an item or fluid
    research: dict[str, int] = field(default_factory=dict)  # technology series -> levels the rate assumes
    # Windows to measure in all, stopping at the first that passes: a big factory
    # fills its belts slowly, with only its rows' slack to spare.
    settle: int = 1


@dataclass
class Measured:
    """What one flow case measured in game, window by window."""
    name: str
    windows: list[dict[str, float]] = field(default_factory=list)  # per window: item or fluid -> per second
    stalled: dict[str, int] = field(default_factory=dict)  # at the end: "entity: status" -> how many
    passed: int | None = None  # the window (from 1) that met expect, or None
    error: str = ""  # the case couldn't run

    def rate(self, name: str) -> float:
        """Per second in the last window measured: the most settled."""
        return self.windows[-1].get(name, 0.0) if self.windows else 0.0


def run_flow_tests(server: Server, data: dict, flows: list[Flow], ids: dict[str, int], report: Report) -> list[Measured]:
    """Append failures to report, and return what each case measured. ids maps
    Draftsman entity ids to entity_numbers."""

    def number(ref):
        return ref if isinstance(ref, int) else ids[ref]

    results = []
    for flow in flows:
        result = Measured(flow.name)
        results.append(result)
        setup = server.call("flow_setup", json.dumps({"blueprint": encode(data), "speed": SPEED,
                                                      **({"research": flow.research} if flow.research else {})}))
        if "error" in setup:
            result.error = f"flow: setup failed: {setup['error']}"
            report.errors.append(result.error)
            return results
        if setup.get("unplaced") or setup.get("unplaceable") or setup.get("unrevived"):
            result.error = f"flow: could not build everything for the test: {setup}"
            report.errors.append(result.error)
            return results
        try:
            _ok(server.call("flow_start", json.dumps({
                "feeds": [{"entity": number(f.entity), "lane": f.lane, "item": f.item, "pooled": f.pooled}
                          for f in flow.feed],
                "drains": [number(d) for d in flow.drain],
                "fluids": [{"entity": number(f.entity), "fluid": f.fluid, "side": f.side or None, "sink": f.sink,
                            "pooled": f.pooled, "level": f.level} for f in flow.fluids],
                "caps": flow.caps or None,
            })))
            _wait_ticks(server, int(flow.warmup * 60), timeout=_timeout(flow.warmup))
            for window in range(flow.settle):
                _ok(server.call("flow_count"))
                _wait_ticks(server, int(flow.window * 60), timeout=_timeout(flow.window))
                got = server.call("flow_read")
                _ok(got)
                result.windows.append(_rates(got))
                result.stalled = got.get("stalled") if isinstance(got.get("stalled"), dict) else {}
                errors = _shortfalls(flow, got)
                if not errors:
                    result.passed = window + 1
                    if window:
                        report.warnings.append(f"flow '{flow.name}': reached its rate in window {window + 1} "
                                               f"of {flow.settle}")
                    break
            if flow.settle > 1 and errors:
                errors = [f"{e} (after {flow.settle} windows)" for e in errors]
            report.errors += errors
        finally:
            server.call("flow_teardown")
    return results


def _rates(got: dict) -> dict[str, float]:
    """Per second: items counted at the drains, fluids from what machines made."""
    seconds = got["ticks"] / 60
    counts = got["counts"] if isinstance(got["counts"], dict) else {}
    made = got.get("fluids") if isinstance(got.get("fluids"), dict) else {}
    rates = {name: n / seconds for name, n in counts.items()}
    rates.update({name: n / seconds for name, n in made.items() if name in fluid_data.raw})
    return rates


def _shortfalls(flow: Flow, got: dict) -> list[str]:
    rates = _rates(got)
    errors = []
    for item, rate in flow.expect.items():
        measured = rates.get(item, 0.0)
        short = measured < (1 - flow.tolerance) * rate
        if short or (not flow.at_least and measured > (1 + flow.tolerance) * rate):
            stalled = got.get("stalled") if isinstance(got.get("stalled"), dict) else {}
            pool = got.get("pool") if isinstance(got.get("pool"), dict) else {}
            pool = {k: round(v) for k, v in pool.items() if v}
            errors.append(
                f"flow '{flow.name}': {item} {measured:.3g}/s, expected {'at least ' if flow.at_least else ''}"
                f"{rate:.3g}/s" + (f"; not working: {stalled}" if stalled else "")
                + (f"; pooled: {pool}" if pool else "")
            )
    return errors


def _timeout(game_seconds: float) -> float:
    # Real time if the CPU can't keep up with SPEED at all, plus slack.
    return game_seconds + 30

