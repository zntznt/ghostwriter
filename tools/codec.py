"""Blueprint string <-> JSON, lossless.

Format: version byte "0" + base64(zlib level 9(JSON)).
https://wiki.factorio.com/Blueprint_string_format

Encoding/decoding goes through Draftsman's codec, but never through its object
model: the object model normalises (drops defaults, re-orders, rewrites keys it
models differently), and library files must be exactly what the game sees.
"""

import json
import sys
from pathlib import Path

from draftsman.utils import JSON_to_string, string_to_JSON

ROOT_KEYS = ("blueprint", "blueprint_book", "deconstruction_planner", "upgrade_planner")


class CodecError(ValueError):
    pass


def decode(text: str) -> dict:
    """Decode a blueprint string, or parse raw JSON (2.0 imports both)."""
    text = text.strip()
    if text.startswith("{"):
        data = json.loads(text)
    elif text.startswith("0"):
        data = string_to_JSON(text)
    else:
        raise CodecError(f"unknown version byte {text[:1]!r}; expected '0'")
    root_key(data)
    return data


def encode(data: dict) -> str:
    root_key(data)
    return JSON_to_string(data)


def root_key(data: dict) -> str:
    """The blueprintable kind of a decoded dict, e.g. "blueprint"."""
    keys = [k for k in ROOT_KEYS if k in data]
    if len(keys) != 1:
        raise CodecError(f"expected exactly one of {ROOT_KEYS}, got {sorted(data)}")
    return keys[0]


def read_input(arg: str) -> str:
    """`-` reads stdin, an existing path reads the file, anything else is the text."""
    if arg == "-":
        return sys.stdin.read()
    path = Path(arg)
    if len(arg) < 1024 and path.is_file():
        return path.read_text()
    return arg


def dump_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
