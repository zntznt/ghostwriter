"""Static checks: Draftsman's object model plus wire lint Draftsman lacks.

This is not the real-game validator (validator/, step 2). It catches typos,
unknown names, overlaps and malformed wires before a string reaches the game.

Draftsman 4.0.1 on import, verified by probing:
  catches   unknown entity/signal names, overlaps, bad enums
  misses    wires to missing entities (crashes), connector IDs on the wrong
            entity kind, out-of-range connector IDs, wire reach
  unreliable on 2.1: it rejects many keys real 2.1.22 exports contain and
            crashes on some entities (NOTES.md), so unknown keys and its own
            crashes are warnings here. The game validator is the authority.
"""

import copy
import json
import re
import warnings
from functools import cache
from pathlib import Path

from draftsman.blueprintable import get_blueprintable_from_JSON
from draftsman.constants import WireConnectorID
from draftsman.data import entities as entity_data
from draftsman.data import signals as signal_data
from draftsman.error import DataFormatError
from draftsman.utils import distance

from tools.codec import root_key

_COMBINATOR_OUTPUTS = {WireConnectorID.COMBINATOR_OUTPUT_RED, WireConnectorID.COMBINATOR_OUTPUT_GREEN}
_COPPER = {WireConnectorID.POLE_COPPER, WireConnectorID.POWER_SWITCH_RIGHT_COPPER}
_CIRCUIT = {WireConnectorID.CIRCUIT_RED, WireConnectorID.CIRCUIT_GREEN} | _COMBINATOR_OUTPUTS
_DUMP = Path(__file__).resolve().parent.parent / "data" / "data-raw-dump.json"


def check(data: dict, prefix: str = "") -> list[str]:
    """Return problems as "error: ..." / "warning: ..." lines; empty means clean."""
    kind = root_key(data)
    if kind == "blueprint":
        return _check_blueprint(data, prefix)
    if kind == "blueprint_book":
        out = []
        for child in data["blueprint_book"].get("blueprints", []):
            label = child[root_key(child)].get("label", "")
            out += check(child, f"{prefix}[{child.get('index')}:{label}] ")
        return out
    return [prefix + p for p in _load(data)[0]]


def _check_blueprint(data: dict, prefix: str) -> list[str]:
    problems = []
    bp = data["blueprint"]
    entities = {e["entity_number"]: e for e in bp.get("entities", [])}
    if len(entities) != len(bp.get("entities", [])):
        problems.append("error: duplicate entity_number")

    stripped = copy.deepcopy(data)
    bad_wires = _wire_endpoint_problems(bp, entities)
    numbers = [e["entity_number"] for e in bp.get("entities", [])]
    if numbers != list(range(1, len(numbers) + 1)):
        # Game exports always number 1..n in order; Draftsman resolves wires by
        # list position, so anything else would mis-wire or crash below.
        bad_wires.append("error: entity_number must run 1..n in list order")
    problems += bad_wires
    if bad_wires:
        # Draftsman crashes on dangling wires; check the rest without them.
        stripped["blueprint"]["wires"] = []
    model_problems, model = _load(stripped)
    problems += model_problems
    if model is not None and not bad_wires:
        problems += _wire_model_problems(bp, model)
    return [prefix + p for p in problems]


def _wire_endpoint_problems(bp: dict, entities: dict) -> list[str]:
    valid = {int(c) for c in WireConnectorID}
    out = []
    for i, wire in enumerate(bp.get("wires", [])):
        if len(wire) != 4:
            out.append(f"error: wire {i} {wire}: expected [entity, connector, entity, connector]")
            continue
        for ent, conn in (wire[0:2], wire[2:4]):
            if ent not in entities:
                out.append(f"error: wire {i} {wire}: no entity {ent}")
            if conn not in valid:
                out.append(f"error: wire {i} {wire}: connector {conn} not in {sorted(valid)}")
        if (wire[1] in _COPPER) != (wire[3] in _COPPER):
            out.append(f"error: wire {i} {wire}: joins a copper connector to a circuit connector")
    return out


def _wire_model_problems(bp: dict, model) -> list[str]:
    by_number = {n: e for n, e in enumerate(model.entities, start=1)}
    out = []
    for i, (a, ca, b, cb) in enumerate(bp.get("wires", [])):
        ea, eb = by_number[a], by_number[b]
        if not (hasattr(ea, "circuit_wire_max_distance") and hasattr(eb, "circuit_wire_max_distance")):
            continue  # unknown entity; already reported, and Draftsman has no data for it
        for ent, conn in ((ea, ca), (eb, cb)):
            if conn in _COMBINATOR_OUTPUTS and not ent.dual_circuit_connectable:
                out.append(f"error: wire {i}: connector {conn} is a combinator output; {ent.name} has none")
        if ca in _CIRCUIT:
            reach = min(ea.circuit_wire_max_distance or 0, eb.circuit_wire_max_distance or 0)
        else:
            reach = min(getattr(ea, "maximum_wire_distance", 0) or 0, getattr(eb, "maximum_wire_distance", 0) or 0)
        d = distance(ea.global_position._data, eb.global_position._data)
        if d > reach:
            out.append(f"warning: wire {i}: {ea.name}->{eb.name} spans {d:.2f} tiles (centre to centre), reach {reach}")
    return out


def _load(data: dict):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            model = get_blueprintable_from_JSON(data)
            errors = model.validate().error_list
        except DataFormatError as e:
            return [f"error: draftsman: {' '.join(str(e).split())}"], None
        except Exception as e:
            # A Draftsman bug, not a blueprint problem (seen on 2.1 vehicles).
            return [f"warning: draftsman crashed, model checks skipped: {type(e).__name__}: {e}"], None
    out = [f"error: draftsman: {e}" for e in errors]
    for w in caught:
        if issubclass(w.category, ResourceWarning):
            continue
        message = " ".join(str(w.message).split())
        kind = w.category.__name__
        if kind == "UnknownKeywordWarning":
            out.append(f"warning: Draftsman 4.0.1 doesn't model: {message.split('keys: ', 1)[-1]}")
        elif kind.startswith("Unknown"):
            # A name neither Draftsman nor the game knows is almost always a
            # typo. One the game knows is a Draftsman gap (e.g. 'thruster').
            name = re.search(r"'([^']+)'", message)
            level = "warning" if name and _game_knows(name[1]) else "error"
            out.append(f"{level}: {kind}: {message}")
        else:
            out.append(f"warning: {kind}: {message}")
    return out, model


def _game_knows(name: str) -> bool:
    return name in _known_names()


@cache
def _known_names() -> frozenset:
    """Prototype names from data/ (the user's game) if dumped, else Draftsman's data."""
    if _DUMP.exists():
        dump = json.loads(_DUMP.read_text())
        return frozenset(name for group in dump.values() if isinstance(group, dict) for name in group)
    return frozenset(entity_data.raw) | frozenset(signal_data.raw)

