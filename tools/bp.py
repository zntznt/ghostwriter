"""Blueprint CLI. Run from the repo root: python -m tools.bp <command> ...

  decode SRC            string -> pretty JSON
  encode SRC            JSON (file or -) -> string
  check SRC             static checks (string or JSON); exit 1 on errors
  add SRC [--to DIR]    decode into library/ (books become folders)
  book DIR [--json]     library folder -> blueprint book string
  gen NAME [k=v ...]    run generators/NAME.py build(), check, print string

SRC is a blueprint string, a path to a file holding one (or JSON), or - for stdin.
"""

import argparse
import importlib
import sys
import warnings
from pathlib import Path

from tools.check import check
from tools.codec import decode, dump_json, encode, read_input
from tools.library import load_book, save

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
        data = module.build(**params).to_dict()
    if _report(check(data)):
        sys.exit(1)
    sys.stdout.write(dump_json(data) if args.json else encode(data) + "\n")


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
    p.set_defaults(fn=cmd_gen)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
