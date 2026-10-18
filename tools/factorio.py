"""Locating the Factorio executable, shared by tools/dump_data.py and validator/."""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Where `python -m validator.fetch` unpacks a headless server (gitignored).
HEADLESS = ROOT / ".factorio-headless" / "factorio" / "bin" / "x64" / "factorio"

# Searched only when neither an explicit path, $FACTORIO nor PATH has the binary.
CANDIDATES = [
    HEADLESS,
    "~/.steam/steam/steamapps/common/Factorio/bin/x64/factorio",
    "~/.local/share/Steam/steamapps/common/Factorio/bin/x64/factorio",
    "~/Library/Application Support/Steam/steamapps/common/Factorio/factorio.app/Contents/MacOS/factorio",
    "/Applications/factorio.app/Contents/MacOS/factorio",
    r"C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe",
    r"C:\Program Files\Factorio\bin\x64\factorio.exe",
]


def locate(explicit: str | None = None) -> Path | None:
    for c in (explicit, os.environ.get("FACTORIO"), shutil.which("factorio")):
        if c:
            return Path(c).expanduser()
    for c in CANDIDATES:
        p = Path(c).expanduser()
        if p.is_file():
            return p
    return None


def find_factorio(explicit: str | None = None) -> Path:
    exe = locate(explicit)
    if exe is None:
        sys.exit(
            "factorio executable not found; pass --factorio PATH, set $FACTORIO, "
            "or run `python -m validator.fetch` (Linux headless server)"
        )
    return exe
