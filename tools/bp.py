"""Blueprint CLI. Run from the repo root: python -m tools.bp <command> ...

  decode SRC            string -> pretty JSON
  encode SRC            JSON (file or -) -> string
  check SRC             static checks (string or JSON); exit 1 on errors
  density SRC           how a blueprint spends its tiles: box, covered and empty, by role
      [--rate R] [--json]   (--rate: its output in items/s, for tiles per item/s)
  add SRC [--to DIR]    decode into library/ (books become folders)
  book DIR [--json]     library folder -> blueprint book string
  gen NAME [k=v ...]    run generators/NAME.py build(), check, print string
      [--validate]        ...and validate in the game (plus the generator's
                          circuit_tests() and flow_tests(), and for a book
                          generator with parts(), each row's own tests and,
                          with chain_tests(), the whole book run as one) before printing
  validate SRC...       import, round-trip and build in a headless server; runs
                        circuit and flow tests from a sibling NAME.tests.toml
  fly SRC [--runs N]    build a space platform blueprint around a fresh hub and fly
      [--seconds S]       it from the solar system edge toward the shattered planet;
      [--fuel]            report each flight: how far, what killed it, chunks in the
      [--supply k=n,...]  hub (--fuel keeps thrusters full; --supply loads the hub;
      [--research k=n,...]  --research sets levels researched, e.g. railgun-damage=4;
      [--start F]           --start begins F of the way along the route, e.g. 0.4;
      [--turn F]            --turn turns back home at F of the way: a round trip)
  plan NAME [k=v ...]   a promethium trip for a platform generator's design (tools.plan):
      [--from edge|nauvis]  check and validate it, measure it in flights along the route (and
      [--runs N]            by its legs from Nauvis), and print where to turn, what to load
      [--verify N]          and what to do before leaving; --verify flies the plan whole N
      [--shock N]           times against it (--shock kills N railguns as each turns);
      [--json]              research=k=n,... sets what the flights assume; --save files it
      [--save]              beside the design's blueprint in library/ (NAME.plan.json)

SRC is a blueprint string, a path to a file holding one (or JSON), or - for
stdin; `validate` also takes library folders (validated as books).
validate/gen --validate take --factorio PATH (else $FACTORIO, PATH, common
install paths, or .factorio-headless/ from `python -m validator.fetch`).
"""

import argparse
import importlib
import sys
import warnings
from pathlib import Path

from tools.check import check
from tools.codec import decode, dump_json, encode, read_input
from tools.library import load_book, save
from validator.circuit import run_circuit_tests
from validator.flow import run_flow_tests
from validator.tests_file import Tests, library_tests, load_tests, tests_path
from validator.server import Server
from validator.validate import validate

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "library"


def _report(problems: list[str]) -> bool:
    """Print problems to stderr; True if any is an error."""
    for p in problems:
        print(p, file=sys.stderr)
    return any(p.split("] ")[-1].startswith("error") for p in problems)


def cmd_decode(args):
    sys.stdout.write(dump_json(decode(read_input(args.src))))


def cmd_encode(args):
    print(encode(decode(read_input(args.src))))


def cmd_check(args):
    if _report(check(decode(read_input(args.src)))):
        sys.exit(1)
    print("ok", file=sys.stderr)


def cmd_density(args):
    import json

    from tools.density import density, report

    measures = density(decode(read_input(args.src)), rate=args.rate)
    print(json.dumps(measures, indent=1) if args.json else report(measures))


def cmd_add(args):
    data = decode(read_input(args.src))
    failed = _report(check(data))
    if failed and not args.force:
        sys.exit("not added: fix errors or pass --force")
    print(save(data, Path(args.to)))


def cmd_book(args):
    data = load_book(Path(args.dir))
    if _report(check(data)):
        sys.exit(1)
    sys.stdout.write(dump_json(data) if args.json else encode(data) + "\n")


def cmd_gen(args):
    module = importlib.import_module(f"generators.{args.name}")
    params = dict(_param(p) for p in args.params)
    with warnings.catch_warnings():
        # check() below re-detects these from the output and reports them once.
        warnings.simplefilter("ignore")
        try:
            blueprint = module.build(**params)
        except ValueError as e:  # a generator saying these parameters can't work, and why
            sys.exit(f"error: {e}")
        data = blueprint.to_dict()
    if _report(check(data)):
        sys.exit(1)
    gap = _gap(args.name, module, params, data)
    if gap:
        print(gap, file=sys.stderr)
    items = [("", data, *_generator_tests(module, params, blueprint, data))]
    if hasattr(module, "parts"):
        # A book of other generators' blueprints: each child runs its own tests.
        # Identical rows are tested once; validating the book builds them all.
        children = {c["index"]: c for c in data["blueprint_book"]["blueprints"]}
        seen = set()
        for i, (name, part_params) in enumerate(module.parts(**params)):
            key = (name, tuple(sorted(part_params.items())))
            if key in seen:
                continue
            seen.add(key)
            part = importlib.import_module(f"generators.{name}")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                part_bp = part.build(**part_params)
            child = {k: v for k, v in children[i].items() if k != "index"}
            label = f"{i}: {child['blueprint'].get('label', name)}"
            items.append((label, child, *_generator_tests(part, part_params, part_bp, child)))
    if args.validate and hasattr(module, "chain_tests"):
        # The whole book run as one: each row's products feed the rows that use them.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            chain, flows = module.chain_tests(**params)
        items.append(("chain", chain, None, (flows, {})))
    if args.validate and not _validate(items, args.factorio):
        sys.exit(1)
    sys.stdout.write(dump_json(data) if args.json else encode(data) + "\n")


def _gap(name, module, params, data) -> str | None:
    """How close a generator's layout is to its bounds (tools.density.bound), from its MODEL
    (a book's from each row's generator); None for one without (factory says its own)."""
    from tools.density import bound, density

    if "blueprint_book" in data and hasattr(module, "parts"):
        children = {c["index"]: c["blueprint"] for c in data["blueprint_book"]["blueprints"]}
        models = [getattr(importlib.import_module(f"generators.{n}"), "MODEL", None)
                  for n, _ in module.parts(**params)]
        bps = [children[i] for i in range(len(models))]
    elif "blueprint" in data and hasattr(module, "MODEL"):
        models, bps = [module.MODEL], [data["blueprint"]]
    else:
        return None
    area = sum(density({"blueprint": bp})[0]["area"] for bp in bps)
    bounds = [bound(bp, *(m or (None,))) for bp, m in zip(bps, models)]
    parts, roles = sum(b["parts"] for b in bounds), {}
    for b in bounds:
        for r, n in b["roles"].items():
            roles[r] = roles.get(r, 0) + n
    line = f"{name}: {area:,} tiles"
    if len(bps) > 1:
        line += f" in {len(bps)} rows"
        if all(b["model"] for b in bounds):
            model = sum(b["model"] for b in bounds)
            line += f"; at least {model:,} in their own layouts, within {area / model - 1:.0%}"
    elif bounds[0]["model"]:
        b, between = bounds[0], ", a row between each two" if models[0][1] else ""
        where = (f"its columns ({b['across']} wide by {b['along']}: the machines of its fullest column end to end"
                 if models[0][0] == "y" else f"its row ({b['along']} by {b['across']} tall: the machines end to end")
        line += f"; at least {b['model']:,} in {where}{between}), within {area / b['model'] - 1:.0%}"
    if parts:
        line += (f"; the parts' own tiles {parts:,} ({', '.join(f'{r} {n:,}' for r, n in roles.items())}), "
                 f"within {area / parts - 1:.0%}")
    return line


def _generator_tests(module, params, blueprint, data) -> tuple:
    """(circuit, flow) for _validate from a generator's circuit_tests() and flow_tests()."""
    if "blueprint" not in data:
        return None, None
    ids = {e.id: n for n, e in enumerate(blueprint.entities, start=1) if e.id}
    circuit = (module.circuit_tests(**params), ids) if hasattr(module, "circuit_tests") else None
    flow = (module.flow_tests(**params), ids) if hasattr(module, "flow_tests") else None
    return circuit, flow


def cmd_validate(args):
    items = []
    for i, src in enumerate(args.src):
        path = Path(src)
        if path.is_dir():
            items.append((src, load_book(path), None, None))
            # Circuit and flow tests run on single blueprints: each tested child on its own.
            try:
                tested = library_tests(path)
            except ValueError as e:
                sys.exit(f"error: {e}")
            for label, data, tests in tested:
                items.append((f"{src}/{label}", data, *_split(tests)))
        else:
            label = src if path.is_file() else f"#{i + 1}"
            data = decode(read_input(src))
            tests = Tests()
            if path.is_file() and tests_path(path).is_file():
                try:
                    tests = load_tests(tests_path(path), data)
                except ValueError as e:
                    sys.exit(f"error: {e}")
            items.append((label if len(args.src) > 1 else "", data, *_split(tests)))
    if not _validate(items, args.factorio):
        sys.exit(1)


def _split(tests: Tests) -> tuple:
    """(circuit, flow) arguments for _validate."""
    return ((tests.cases, tests.ids) if tests.cases else None,
            (tests.flows, tests.ids) if tests.flows else None)


def _validate(items: list[tuple], factorio: str | None) -> bool:
    """Validate each (label, data, circuit, flow) in one server session, where
    circuit and flow are None or (cases, ids) for run_circuit_tests and
    run_flow_tests; print a report; True if all pass."""
    all_ok = True
    with Server(factorio) as server:
        print(f"validating in Factorio {server.version}", file=sys.stderr)
        for label, data, circuit, flow in items:
            report = validate(server, data)
            for kind, tests, run in (("circuit", circuit, run_circuit_tests), ("flow", flow, run_flow_tests)):
                if tests and report.ok:
                    cases, ids = tests
                    measured = run(server, data, cases, ids, report)
                    if report.ok:
                        names = "; ".join(_measured_text(c, m) for c, m in zip(cases, measured or [None] * len(cases)))
                        print(f"{label + ': ' if label else ''}{kind}: {len(cases)} cases pass ({names})", file=sys.stderr)
            prefix = f"{label}: " if label else ""
            for line in report.lines():
                print(prefix + line, file=sys.stderr)
            print(prefix + ("pass" if report.ok else "FAIL"), file=sys.stderr)
            all_ok &= report.ok
    return all_ok


def _measured_text(case, measured) -> str:
    """A case's name, and for flow cases what its passing window measured."""
    if measured is None or not measured.windows:
        return case.name
    window = measured.windows[(measured.passed or len(measured.windows)) - 1]
    rates = ", ".join(f"{window.get(k, 0):.3g} {k}/s" for k in case.expect)
    return f"{case.name}: measured {rates}" + (f" in window {measured.passed}" if (measured.passed or 1) > 1 else "")


def cmd_fly(args):
    from validator.flight import Flight, run_flight
    from validator.validate import Report

    data = decode(read_input(args.src))
    if "blueprint" not in data:
        sys.exit("fly takes a single blueprint")
    supplies = dict(_param(kv) for kv in args.supply.split(",")) if args.supply else {}
    research = dict(_param(kv) for kv in args.research.split(",")) if args.research else {}
    flight = Flight("flight", origin=args.origin, destination=args.destination, seconds=args.seconds,
                    start=args.start, supplies=supplies, fuel=args.fuel, research=research, turn=args.turn)
    flights = []
    with Server(args.factorio) as server:
        print(f"flying in Factorio {server.version}", file=sys.stderr)
        for run in range(args.runs):
            report = Report(server.version)
            flown = run_flight(server, data, flight, report)
            for line in report.lines():
                print(f"run {run + 1}: {line}", file=sys.stderr)
            if flown.error:
                sys.exit(1)
            end = f"survived {flown.seconds:.0f}s" if flown.survived else f"{flown.ended} after {flown.seconds:.0f}s"
            if flown.back:
                end = f"back after {flown.seconds:.0f}s, {flown.odometer:,.0f} km flown"
            losses = ", ".join(f"{k} x{v}" for k, v in sorted(flown.deaths.items())) or "nothing"
            chunks = ", ".join(f"{v} {k}" for k, v in sorted(flown.cargo.items()) if k.endswith("asteroid-chunk"))
            print(f"run {run + 1}: {flown.km:,.0f} km, {end}; lost: {losses}; chunks in hub: {chunks or 'none'}",
                  file=sys.stderr)
            flights.append(flown)
    km = [f.km for f in flights]
    print(f"{len(km)} flights: {min(km):,.0f} to {max(km):,.0f} km, mean {sum(km) / len(km):,.0f}", file=sys.stderr)


def cmd_plan(args):
    import json

    from tools import plan as planner

    module = importlib.import_module(f"generators.{args.name}")
    if not hasattr(module, "flight_tests"):
        sys.exit(f"error: {args.name} builds nothing that flies (no flight_tests)")
    params = dict(_param(p) for p in args.params)
    research = None
    if "research" in params:
        research = dict(_param(kv) for kv in str(params.pop("research")).split(",") if kv)
    reason = planner.cannot_plan(params)
    if reason:
        sys.exit(f"error: no promethium trip: {reason}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            data = module.build(**params).to_dict()
        except ValueError as e:  # the generator saying these parameters can't work, and why
            sys.exit(f"error: {e}")
    if _report(check(data)):
        sys.exit(1)
    with Server(args.factorio) as server:
        print(f"planning in Factorio {server.version}", file=sys.stderr)
        report = validate(server, data)
        for line in report.lines():
            print(line, file=sys.stderr)
        if not report.ok:
            sys.exit("error: not planned: the blueprint failed validation")
        try:
            p = planner.plan(server, args.name, params, args.origin, args.runs, research, data=data)
            if args.verify and p["trip"]:
                p["verified"] = planner.verify(server, args.name, p, args.verify, args.shock)
        except ValueError as e:
            sys.exit(f"error: {e}")
    if args.json:
        print(json.dumps(p, indent=1))
    else:
        print(planner.text(p) + (f"\n{planner.compare_text(p['verified'])}" if p.get("verified") else ""))
    if args.save:
        from tools.settings import registry

        try:
            filed = planner.save(p, data, LIBRARY, registry()["game_version"])
        except ValueError as e:
            sys.exit(f"error: not filed: {e}")
        print(f"filed {filed}", file=sys.stderr)
    if p["error"]:
        sys.exit(1)


def _param(text: str):
    key, _, value = text.partition("=")
    for cast in (int, float):
        try:
            return key, cast(value)
        except ValueError:
            pass
    return key, value


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.bp", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    for name, fn in (("decode", cmd_decode), ("encode", cmd_encode), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("src")
        p.set_defaults(fn=fn)

    p = sub.add_parser("density")
    p.add_argument("src")
    p.add_argument("--rate", type=float, help="the blueprint's output in items/s, for tiles per item/s")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_density)

    p = sub.add_parser("add")
    p.add_argument("src")
    p.add_argument("--to", default=str(LIBRARY))
    p.add_argument("--force", action="store_true", help="add even if check reports errors")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("book")
    p.add_argument("dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_book)

    p = sub.add_parser("gen")
    p.add_argument("name")
    p.add_argument("params", nargs="*", metavar="key=value",
                   help="the generator's options (its build() arguments), e.g. factory item=X rate=N; "
                        "factory effort=N spends N times the search's steps for a smaller layout")
    p.add_argument("--json", action="store_true")
    p.add_argument("--validate", action="store_true", help="validate in the game before printing")
    p.add_argument("--factorio", help="Factorio executable for --validate")
    p.set_defaults(fn=cmd_gen)

    p = sub.add_parser("validate")
    p.add_argument("src", nargs="+")
    p.add_argument("--factorio", help="Factorio executable (headless server or game)")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("fly")
    p.add_argument("src")
    p.add_argument("--runs", type=int, default=3, help="flights to fly (they differ run to run)")
    p.add_argument("--seconds", type=float, default=600, help="game seconds per flight at most")
    p.add_argument("--fuel", action="store_true", help="keep thrusters full by script")
    p.add_argument("--supply", help="items for the hub, e.g. firearm-magazine=2000,rocket=500")
    p.add_argument("--research", help="technology series and levels researched, e.g. "
                                      "railgun-damage=4,railgun-shooting-speed=3 (default none)")
    p.add_argument("--start", type=float, default=0.0,
                   help="where on the route to start, as a share of it (0.4: 40%% of the way)")
    p.add_argument("--turn", type=float, default=0.0,
                   help="a round trip: turn back toward the origin at this share of the route")
    p.add_argument("--origin", default="solar-system-edge")
    p.add_argument("--destination", default="shattered-planet")
    p.add_argument("--factorio", help="Factorio executable (headless server or game)")
    p.set_defaults(fn=cmd_fly)

    p = sub.add_parser("plan")
    p.add_argument("name")
    p.add_argument("params", nargs="*", metavar="key=value")
    p.add_argument("--from", dest="origin", choices=("edge", "nauvis"), default="edge",
                   help="where the trip sets out: the solar system edge, or Nauvis by its legs")
    p.add_argument("--runs", type=int, default=8, help="flights at each start (they differ run to run)")
    p.add_argument("--verify", type=int, default=0, metavar="N", help="fly the planned trip whole N times")
    p.add_argument("--shock", type=int, default=0, metavar="N",
                   help="with --verify: N railguns killed as each trip turns, a bad trip on purpose")
    p.add_argument("--save", action="store_true",
                   help="file the plan beside the library entry that is this design's blueprint (NAME.plan.json)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--factorio", help="Factorio executable (headless server or game)")
    p.set_defaults(fn=cmd_plan)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
