"""Blueprint CLI. Run from the repo root: python -m tools.bp <command> ...

  decode SRC            string -> pretty JSON
  encode SRC            JSON (file or -) -> string
  check SRC             static checks (string or JSON); exit 1 on errors
  add SRC [--to DIR]    decode into library/ (books become folders)
  book DIR [--json]     library folder -> blueprint book string
  gen NAME [k=v ...]    run generators/NAME.py build(), check, print string
      [--validate]        ...and validate in the game (plus the generator's
                          circuit_tests() and flow_tests(), if any) before printing
  validate SRC...       import, round-trip and build in a headless server; runs
                        circuit and flow tests from a sibling NAME.tests.toml

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
        blueprint = module.build(**params)
        data = blueprint.to_dict()
    if _report(check(data)):
        sys.exit(1)
    circuit = flow = None
    if "blueprint" in data:
        ids = {e.id: n for n, e in enumerate(blueprint.entities, start=1) if e.id}
        if hasattr(module, "circuit_tests"):
            circuit = (module.circuit_tests(**params), ids)
        if hasattr(module, "flow_tests"):
            flow = (module.flow_tests(**params), ids)
    if args.validate and not _validate([("", data, circuit, flow)], args.factorio):
        sys.exit(1)
    sys.stdout.write(dump_json(data) if args.json else encode(data) + "\n")


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
                    run(server, data, cases, ids, report)
                    if report.ok:
                        names = "; ".join(c.name for c in cases)
                        print(f"{label + ': ' if label else ''}{kind}: {len(cases)} cases pass ({names})", file=sys.stderr)
            prefix = f"{label}: " if label else ""
            for line in report.lines():
                print(prefix + line, file=sys.stderr)
            print(prefix + ("pass" if report.ok else "FAIL"), file=sys.stderr)
            all_ok &= report.ok
    return all_ok


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
    p.add_argument("params", nargs="*", metavar="key=value")
    p.add_argument("--json", action="store_true")
    p.add_argument("--validate", action="store_true", help="validate in the game before printing")
    p.add_argument("--factorio", help="Factorio executable for --validate")
    p.set_defaults(fn=cmd_gen)

    p = sub.add_parser("validate")
    p.add_argument("src", nargs="+")
    p.add_argument("--factorio", help="Factorio executable (headless server or game)")
    p.set_defaults(fn=cmd_validate)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
