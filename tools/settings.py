"""Set game settings Draftsman can't express, from a registry the game wrote.

    from tools.settings import game_settings
    game_settings(machine, {"read_ingredients": True})
    game_settings(decider, {"decider_conditions.else_outputs": [...]})

Paths are control_behavior keys, dotted for nesting. Only paths that
tools/game_settings.json lists for the entity's prototype type, and saw on
that very entity, are allowed, with the same kind of value the game wrote there (bool, number, string,
array, signal, networks). That registry comes from `python -m validator.harvest`,
and tests/test_validator.py proves every entry survives a round trip in the
game, so these are game-verified keys, not hand-written ones. Values merge into
the entity's Draftsman `extra_keys`, which Draftsman writes out alongside the
keys it models.
"""

import difflib
import json
from functools import cache
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent / "game_settings.json"


def kind(value) -> str:
    """The kind of a setting value, as recorded in the registry."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict) and value and set(value) <= {"red", "green"}:
        return "networks"
    if isinstance(value, dict) and "name" in value:
        return "signal"
    return "object"


def flatten(d: dict, prefix: str = ""):
    """(dotted path, value) for each leaf of a control_behavior; recurses only into plain objects."""
    for key, value in d.items():
        path = f"{prefix}{key}"
        if kind(value) == "object":
            yield from flatten(value, path + ".")
        else:
            yield path, value


@cache
def registry() -> dict:
    return json.loads(REGISTRY.read_text())


def known(prototype_type: str) -> dict:
    """{path: {"kind", "observed", "entities"}} for a prototype type; empty if none."""
    return registry()["types"].get(prototype_type, {}).get("settings", {})


def game_settings(entity, settings: dict):
    """Validate settings against the registry, then merge them into entity.extra_keys."""
    allowed = known(entity.type)
    for path, value in settings.items():
        if path not in allowed:
            hint = difflib.get_close_matches(path, allowed, n=1)
            raise ValueError(
                f"{entity.name} ({entity.type}): {path!r} is not in the game-settings registry for this type."
                + (f" Did you mean {hint[0]!r}?" if hint else f" Known: {sorted(allowed) or 'none'}.")
                + " If Draftsman models it, use the Draftsman attribute instead."
            )
        if entity.name not in allowed[path]["entities"]:
            raise ValueError(
                f"{entity.name}: {path!r} was only seen on {', '.join(allowed[path]['entities'])}; "
                "the game drops it elsewhere"
            )
        want = allowed[path]["kind"]
        if kind(value) != want:
            raise ValueError(f"{entity.name}: {path!r} takes a {want}, got {kind(value)} {value!r}")

    extra = dict(entity.extra_keys or {})
    cb = extra.setdefault("control_behavior", {})
    for path, value in settings.items():
        *parents, leaf = path.split(".")
        node = cb
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    entity.extra_keys = extra
