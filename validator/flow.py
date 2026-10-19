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
per swing.
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


def run_flow_tests(server: Server, data: dict, flows: list[Flow], ids: dict[str, int], report: Report):
    """Append failures to report. ids maps Draftsman entity ids to entity_numbers."""

    def number(ref):
        return ref if isinstance(ref, int) else ids[ref]

    for flow in flows:
        setup = server.call("flow_setup", json.dumps({"blueprint": encode(data), "speed": SPEED}))
        if "error" in setup:
            report.errors.append(f"flow: setup failed: {setup['error']}")
            return
        if setup.get("unplaced") or setup.get("unplaceable") or setup.get("unrevived"):
            report.errors.append(f"flow: could not build everything for the test: {setup}")
            return
        try:
            _ok(server.call("flow_start", json.dumps({
                "feeds": [{"entity": number(f.entity), "lane": f.lane, "item": f.item, "pooled": f.pooled}
                          for f in flow.feed],
                "drains": [number(d) for d in flow.drain],
                "fluids": [{"entity": number(f.entity), "fluid": f.fluid, "side": f.side or None, "sink": f.sink,
                            "pooled": f.pooled} for f in flow.fluids],
                "caps": flow.caps or None,
            })))
            _wait_ticks(server, int(flow.warmup * 60), timeout=_timeout(flow.warmup))
            _ok(server.call("flow_count"))
            _wait_ticks(server, int(flow.window * 60), timeout=_timeout(flow.window))
            got = server.call("flow_read")
            _ok(got)
            seconds = got["ticks"] / 60
            counts = got["counts"] if isinstance(got["counts"], dict) else {}
            made = got.get("fluids") if isinstance(got.get("fluids"), dict) else {}
            for item, rate in flow.expect.items():
                # Items are counted at the drains; fluids from what machines made.
                measured = (made.get(item, 0) if item in fluid_data.raw else counts.get(item, 0)) / seconds
                short = measured < (1 - flow.tolerance) * rate
                if short or (not flow.at_least and measured > (1 + flow.tolerance) * rate):
                    stalled = got.get("stalled") if isinstance(got.get("stalled"), dict) else {}
                    pool = got.get("pool") if isinstance(got.get("pool"), dict) else {}
                    pool = {k: round(v) for k, v in pool.items() if v}
                    report.errors.append(
                        f"flow '{flow.name}': {item} {measured:.3g}/s, expected {'at least ' if flow.at_least else ''}"
                        f"{rate:.3g}/s" + (f"; not working: {stalled}" if stalled else "")
                        + (f"; pooled: {pool}" if pool else "")
                    )
        finally:
            server.call("flow_teardown")


def _timeout(game_seconds: float) -> float:
    # Real time if the CPU can't keep up with SPEED at all, plus slack.
    return game_seconds + 30

