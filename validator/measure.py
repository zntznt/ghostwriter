"""Measure inserter throughput in the game, for generators to size rows with.

    python -m validator.measure [--factorio PATH]

For each inserter and belt tier, with no research (hand size as the
prototype gives it):
- from_belt: items/s an inserter takes off a full, moving belt into a chest.
- to_belt: items/s it moves from a full chest onto a moving belt.
Two inserters per rig, two tiles apart, so neither the belt nor the lane
they drop onto is the limit. Writes tools/inserter_rates.json.

A chest takes whatever comes; a machine only takes what its recipe needs,
so treat these as upper bounds and keep a margin. Flow tests check the
real row.

    python -m validator.measure --handover

measures only machine_to_machine, keeping the belt rates: items/s an inserter
moves from a machine's output straight into another machine's input. The rig
is copper cable in assembling-machine-3 handing over to electronic circuits in
another (each faster than any inserter), so circuits/s x 3 is cable/s moved.
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from tools.codec import dump_json, encode
from validator.circuit import _ok, _wait_ticks
from validator.flow import SPEED
from validator.server import Server

OUT = Path(__file__).resolve().parent.parent / "tools" / "inserter_rates.json"
INSERTERS = ["inserter", "fast-inserter", "long-handed-inserter", "bulk-inserter"]
BELTS = ["transport-belt", "fast-transport-belt", "express-transport-belt", "turbo-transport-belt"]
ITEM = "iron-plate"
RIG_INSERTERS = 2
WARMUP, WINDOW = 10, 120  # seconds of game time


def rig(inserter: str, belt: str, from_belt: bool) -> tuple[dict, dict[str, int]]:
    """A north-running belt with inserters east of it, each with a chest beyond."""
    reach = 2 if inserter.startswith("long") else 1
    length = 4 + 2 * RIG_INSERTERS
    bp = Blueprint()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for y in range(length):
            ref = {"id": "bottom"} if y == length - 1 else {"id": "top"} if y == 0 else {}
            bp.entities.append(belt, tile_position=(0, y), direction=Direction.NORTH, **ref)
        for k in range(RIG_INSERTERS):
            y = 2 + 2 * k
            # An inserter's direction is the side it picks up from (NOTES.md).
            bp.entities.append(inserter, tile_position=(reach, y),
                               direction=Direction.WEST if from_belt else Direction.EAST)
            bp.entities.append("steel-chest", tile_position=(2 * reach, y), id=f"chest{k}")
            bp.entities.append("medium-electric-pole", tile_position=(reach, y + 1))
        bp.generate_power_connections()
    ids = {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}
    return bp.to_dict(), ids


def measure(server: Server, inserter: str, belt: str, from_belt: bool) -> float:
    data, ids = rig(inserter, belt, from_belt)
    chests = [ids[f"chest{k}"] for k in range(RIG_INSERTERS)]
    setup = server.call("flow_setup", json.dumps({"blueprint": encode(data), "speed": SPEED}))
    _ok(setup)
    try:
        if from_belt:
            feeds = [{"entity": ids["bottom"], "lane": lane, "item": ITEM} for lane in ("left", "right")]
        else:
            feeds = []
            _ok(server.call("flow_fill", json.dumps({"entities": chests, "item": ITEM, "count": 4800})))
        # Drain the top so the belt keeps moving, as it does upstream in a row.
        _ok(server.call("flow_start", json.dumps({"feeds": feeds, "drains": [ids["top"]]})))
        _wait_ticks(server, WARMUP * 60, timeout=60)

        def in_chests():
            return server.call("flow_contents", json.dumps({"entities": chests, "item": ITEM}))["count"]

        before = in_chests()
        _ok(server.call("flow_count"))
        _wait_ticks(server, WINDOW * 60, timeout=WINDOW + 60)
        got = server.call("flow_read")
        moved = in_chests() - before if from_belt else (got["counts"] or {}).get(ITEM, 0)
        return moved / (got["ticks"] / 60) / RIG_INSERTERS
    finally:
        server.call("flow_teardown")


HANDOVER_MACHINE = "assembling-machine-3"


def handover_rig(inserter: str) -> tuple[dict, dict[str, int]]:
    """Copper belt -> cable machine -> the inserter -> circuit machine <- iron belt; circuits
    leave on a belt. Two fast inserters for each other job, so only the handover limits."""
    N, E, W = Direction.NORTH, Direction.EAST, Direction.WEST
    bp = Blueprint()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for y in range(5):
            bp.entities.append("transport-belt", tile_position=(0, y), direction=N, **({"id": "cu"} if y == 4 else {}))
            bp.entities.append("transport-belt", tile_position=(10, y), direction=N, **({"id": "fe"} if y == 4 else {}))
        for y in (0, 1):
            bp.entities.append("fast-inserter", tile_position=(1, y), direction=W)
            bp.entities.append("fast-inserter", tile_position=(9, y), direction=E)
        bp.entities.append(HANDOVER_MACHINE, tile_position=(2, 0), recipe="copper-cable")
        bp.entities.append(HANDOVER_MACHINE, tile_position=(6, 0), recipe="electronic-circuit")
        # At x=5 facing west a regular inserter reaches 4 -> 6, a long-handed one 3 -> 7: both machines.
        bp.entities.append(inserter, tile_position=(5, 1), direction=W)
        for x in (6, 7):
            bp.entities.append("fast-inserter", tile_position=(x, 3), direction=N)
        for x in range(6, 9):
            bp.entities.append("transport-belt", tile_position=(x, 4), direction=E, **({"id": "out"} if x == 8 else {}))
        for x, y in ((1, 3), (5, 3), (9, 4)):
            bp.entities.append("medium-electric-pole", tile_position=(x, y))
        bp.generate_power_connections()
    return bp.to_dict(), {e.id: n for n, e in enumerate(bp.entities, start=1) if e.id}


def measure_handover(server: Server, inserter: str) -> float:
    data, ids = handover_rig(inserter)
    _ok(server.call("flow_setup", json.dumps({"blueprint": encode(data), "speed": SPEED})))
    try:
        feeds = [{"entity": ids[belt], "lane": lane, "item": item}
                 for belt, item in (("cu", "copper-plate"), ("fe", "iron-plate")) for lane in ("left", "right")]
        _ok(server.call("flow_start", json.dumps({"feeds": feeds, "drains": [ids["out"]]})))
        _wait_ticks(server, WARMUP * 60, timeout=60)
        _ok(server.call("flow_count"))
        _wait_ticks(server, WINDOW * 60, timeout=WINDOW + 60)
        got = server.call("flow_read")
        return 3 * (got["counts"] or {}).get("electronic-circuit", 0) / (got["ticks"] / 60)
    finally:
        server.call("flow_teardown")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--factorio")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--handover", action="store_true", help="measure only machine-to-machine handovers")
    args = ap.parse_args(argv)
    if args.handover:
        data = json.loads(Path(args.out).read_text())
        with Server(args.factorio) as server:
            if server.version != data["game_version"]:
                sys.exit(f"the belt rates are from {data['game_version']}, the server is {server.version}: "
                         "measure everything first")
            data["machine_to_machine"] = {}
            for inserter in INSERTERS:
                data["machine_to_machine"][inserter] = round(measure_handover(server, inserter), 3)
                print(f"{inserter:22s} {data['machine_to_machine'][inserter]}", file=sys.stderr)
        Path(args.out).write_text(dump_json(data))
        print(f"-> {args.out}", file=sys.stderr)
        return
    rates = {}
    with Server(args.factorio) as server:
        for inserter in INSERTERS:
            for belt in BELTS:
                r = {"from_belt": round(measure(server, inserter, belt, True), 3),
                     "to_belt": round(measure(server, inserter, belt, False), 3)}
                rates.setdefault(inserter, {})[belt] = r
                print(f"{inserter:22s} {belt:24s} {r}", file=sys.stderr)
    Path(args.out).write_text(dump_json({
        "game_version": server.version,
        "note": "Generated by `python -m validator.measure`; do not edit by hand. "
                f"Items/s per inserter, no research, {ITEM}.",
        "rates": rates,
    }))
    print(f"-> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
