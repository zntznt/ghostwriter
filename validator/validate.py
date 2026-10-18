"""Validate blueprintables in the real game and explain the differences.

Two comparisons per blueprintable:
  parse  input  vs the game's re-export after import_stack. A key the game
         dropped or changed means it didn't understand it: a typo, a key from
         another game version, or a value it rejects. import_stack returns 0
         even then (NOTES.md), which is why this diff exists.
  build  re-export vs a blueprint of what build_blueprint + revive actually
         built on a concrete test surface: entities that couldn't be placed,
         wires that didn't form, settings that didn't stick.

The game omits defaults on export. A dropped key is:
  silent   if it holds a default the game was seen omitting (DEFAULTS),
  warning  if it merely looks like one (false, 0, "", null, empty),
  error    otherwise.
Keys the game adds are ignored. Keys the game renames on import are mapped
first (MIGRATIONS), so a migrated key is not reported as dropped.
"""

from collections import Counter
from dataclasses import dataclass, field

from tools.codec import decode, encode, root_key
from validator.server import Server

IMPORT_RESULTS = {0: "ok", -1: "imported with errors", 1: "rejected"}

# Defaults the 2.1.22 export omits, each observed in a round trip.
DEFAULTS = {
    "direction": 0,
    "type": "item",  # SignalID.type
    "quality": "normal",
    "copy_count_from_input": True,
}


def _logistic_mode(cb: dict) -> dict:
    # 2.1 reads Draftsman's circuit_mode_of_operation and re-exports it as
    # read_contents/set_requests (NOTES.md). Unknown modes stay as they are,
    # so the game dropping them is reported.
    mode = cb.get("circuit_mode_of_operation")
    new = {0: {}, 1: {"set_requests": True, "read_contents": False}, 2: {"read_contents": False}}.get(mode)
    if new is None:
        return cb
    cb = {k: v for k, v in cb.items() if k != "circuit_mode_of_operation"}
    return {**cb, **new}


# control_behavior rewrites the game applies on import: f(cb) -> cb as 2.1 writes it.
MIGRATIONS = [_logistic_mode]


@dataclass
class Report:
    game_version: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        return [f"error: {e}" for e in self.errors] + [f"warning: {w}" for w in self.warnings]


def validate(server: Server, data: dict) -> Report:
    report = Report(server.version)
    result = server.call("validate", encode(data))
    rc = result["import"]
    if rc != 0:
        report.errors.append(f"game import: {IMPORT_RESULTS.get(rc, rc)} (import_stack returned {rc})")
    if "export" not in result:
        return report
    exported = decode(result["export"])
    _compare_blueprintable(data, exported, "", report)

    for path, build in (result.get("builds") or {}).items():
        where = f"[{path}] " if path else ""
        body = _at_path(exported, path)["blueprint"]
        for name in build.get("unplaced") or []:
            report.errors.append(f"{where}build: could not place {name}: it collides with the rest of the blueprint")
        for name in (build.get("unplaceable") or []) + (build.get("unrevived") or []):
            report.warnings.append(
                f"{where}build: {name} can't be built on the concrete test surface "
                "(surface conditions, water or rails); its build was not checked"
            )
        if build.get("unplaceable") or build.get("unrevived"):
            continue
        if build.get("rebuilt") and not build.get("unplaced"):
            rebuilt = decode(build["rebuilt"])["blueprint"]
            _compare_entities(body, rebuilt, f"{where}build", report, shifted=True)
        elif not build.get("rebuilt") and build.get("entities"):
            report.errors.append(f"{where}build: nothing was built")
    return report


def _at_path(data: dict, path: str) -> dict:
    for part in filter(None, path.split("/")):
        data = next(c for c in data["blueprint_book"]["blueprints"] if c["index"] == int(part))
    return data


def _compare_blueprintable(expected: dict, actual: dict, path: str, report: Report):
    """path: book indices down to this blueprintable, e.g. "1/0"; "" at the top."""
    prefix = f"[{path}] parse" if path else "parse"
    kind = root_key(expected)
    if root_key(actual) != kind:
        report.errors.append(f"{prefix}: game turned a {kind} into a {root_key(actual)}")
        return
    exp, act = expected[kind], actual[kind]
    if kind == "blueprint":
        rest = {k: v for k, v in exp.items() if k not in ("version", "entities", "wires")}
        _diff(rest, act, prefix, report)
        _compare_entities(exp, act, prefix, report, shifted=False)
    elif kind == "blueprint_book":
        rest = {k: v for k, v in exp.items() if k not in ("version", "blueprints")}
        _diff(rest, act, prefix, report)
        children = {c["index"]: c for c in act.get("blueprints", [])}
        for child in exp.get("blueprints", []):
            sub = f"{path}/{child['index']}" if path else str(child["index"])
            if child["index"] not in children:
                report.errors.append(f"[{sub}] parse: missing from the game's book")
            else:
                _compare_blueprintable(child, children[child["index"]], sub, report)
    else:
        _diff({k: v for k, v in exp.items() if k != "version"}, act, prefix, report)


def _compare_entities(exp: dict, act: dict, where: str, report: Report, shifted: bool):
    """Match entities by name and position (allowing one global shift)."""
    exp_entities, act_entities = exp.get("entities", []), act.get("entities", [])
    offset = _offset(exp_entities, act_entities) if shifted else (0.0, 0.0)
    by_place = {}
    for e in act_entities:
        by_place.setdefault(_place(e, (0.0, 0.0)), []).append(e)

    matched = {}  # expected entity_number -> actual entity
    for e in exp_entities:
        candidates = by_place.get(_place(e, offset))
        label = f"{where}: entity {e['entity_number']} ({e['name']})"
        if not candidates:
            why = "was not built (collides with another entity?)" if shifted else "missing in game"
            report.errors.append(f"{label}: {why}")
            continue
        a = candidates.pop(0)
        matched[e["entity_number"]] = a
        fields = {k: v for k, v in e.items() if k not in ("entity_number", "position")}
        if "control_behavior" in fields:
            for migrate in MIGRATIONS:
                fields["control_behavior"] = migrate(fields["control_behavior"])
        _diff(fields, a, label, report, ignore_items=shifted)
    extra = [e["name"] for es in by_place.values() for e in es]
    if extra:
        report.errors.append(f"{where}: unexpected entities in game: {', '.join(extra)}")

    names = {e["entity_number"]: e["name"] for e in exp_entities}

    def wire_set(wires, to_key):
        out = set()
        for a, ca, b, cb in wires:
            ka, kb = to_key(a), to_key(b)
            if ka is not None and kb is not None:
                out.add(frozenset({(ka, ca), (kb, cb)}))
        return out

    # Express both sides in expected entity numbers.
    back = {a["entity_number"]: n for n, a in matched.items()}
    want = wire_set(exp.get("wires", []), lambda n: n if n in matched else None)
    got = wire_set(act.get("wires", []), back.get)
    for w in sorted(want - got, key=sorted):
        report.errors.append(f"{where}: wire missing in game: {_wire_text(w, names)}")
    for w in sorted(got - want, key=sorted):
        report.errors.append(f"{where}: unexpected wire in game: {_wire_text(w, names)}")


def _wire_text(wire, names) -> str:
    return " <-> ".join(f"{n} ({names[n]}) connector {c}" for n, c in sorted(wire))


def _place(e: dict, offset) -> tuple:
    return (e["name"], round(e["position"]["x"] + offset[0], 3), round(e["position"]["y"] + offset[1], 3))


def _offset(exp: list, act: list) -> tuple:
    """The most common position shift between same-named entities."""
    votes = Counter()
    by_name = {}
    for a in act:
        by_name.setdefault(a["name"], []).append(a["position"])
    for e in exp:
        for p in by_name.get(e["name"], []):
            votes[(round(p["x"] - e["position"]["x"], 3), round(p["y"] - e["position"]["y"], 3))] += 1
    return votes.most_common(1)[0][0] if votes else (0.0, 0.0)


def _dropped(path: str, value, report: Report):
    key = path.rsplit(".", 1)[-1]
    if key in DEFAULTS and DEFAULTS[key] == value and type(DEFAULTS[key]) is type(value):
        return
    if key.endswith("networks") and value == {"red": True, "green": True}:
        return  # both networks: the default CircuitNetworkSelection
    parts = path.split(".")
    if len(parts) >= 2 and parts[-2].endswith("networks") and value is True:
        return  # one flag of a selection, true by default
    looks_default = value is None or value is False or value == 0 or value in ("", [], {})
    (report.warnings if looks_default else report.errors).append(f"{path}: dropped by the game (was {value!r})")


def _diff(expected, actual, path: str, report: Report, ignore_items: bool = False):
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, value in expected.items():
            sub = f"{path}.{key}"
            if key not in actual:
                if ignore_items and key == "items":
                    continue  # item requests become proxies; nothing delivers them in the test
                _dropped(sub, value, report)
            else:
                _diff(value, actual[key], sub, report, ignore_items)
    elif isinstance(expected, list) and isinstance(actual, list):
        if all(isinstance(x, dict) and "index" in x for x in expected + actual):
            got = {x["index"]: x for x in actual}
            for x in expected:
                if x["index"] in got:
                    _diff(x, got[x["index"]], f"{path}[index={x['index']}]", report, ignore_items)
                else:
                    report.errors.append(f"{path}[index={x['index']}]: dropped by the game (was {x!r})")
        elif len(expected) != len(actual):
            report.errors.append(f"{path}: game has {actual!r}, expected {expected!r}")
        else:
            for i, (e, a) in enumerate(zip(expected, actual)):
                _diff(e, a, f"{path}[{i}]", report, ignore_items)
    elif expected != actual:
        report.errors.append(f"{path}: game has {actual!r}, expected {expected!r}")
