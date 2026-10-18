"""Run `factorio --dump-data` and copy the result into data/.

    python -m tools.dump_data [--factorio PATH] [--write-data DIR]

Writes data/data-raw-dump.json and data/factorio-version.txt (used by `bp gen`
to stamp blueprints with the version of the game they target).

The game writes the dump to <write-data>/script-output/data-raw-dump.json.
write-data is relocatable (config.ini `write-data`, config-path.cfg), so it is
read from the game's own startup log line "Write data path: ..." rather than
assumed. --write-data overrides that if the log line ever changes.

Dump flags are mutually exclusive (https://forums.factorio.com/viewtopic.php?p=658340),
so this runs --version and --dump-data as separate invocations. Close the game
first; a running instance holds a lock on write-data.
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tools.factorio import find_factorio

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def run(exe: Path, *args: str) -> str:
    proc = subprocess.run([str(exe), *args], capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        sys.exit(f"{exe} {' '.join(args)} exited {proc.returncode}:\n{out}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--factorio", help="path to the factorio executable")
    ap.add_argument("--write-data", help="the game's write-data dir, if the log doesn't say")
    args = ap.parse_args(argv)

    exe = find_factorio(args.factorio)
    print(f"factorio: {exe}")

    version = re.search(r"Version: (\d+\.\d+\.\d+)", run(exe, "--version"))
    if not version:
        sys.exit("could not parse `factorio --version` output")
    print(f"version: {version[1]}")

    started = time.time()
    log = run(exe, "--dump-data")
    if args.write_data:
        write_data = Path(args.write_data).expanduser()
    else:
        m = re.search(r"Write data path: (.+?)(?: \[.*\])?$", log, re.MULTILINE)
        if not m:
            sys.exit(f"no 'Write data path' in the game log; pass --write-data. Log:\n{log}")
        write_data = Path(m[1].strip())
    dump = write_data / "script-output" / "data-raw-dump.json"
    print(f"dump: {dump}")
    if not dump.is_file() or dump.stat().st_mtime < started - 1:
        sys.exit(f"{dump} missing or not written by this run. Game log:\n{log}")

    DATA.mkdir(exist_ok=True)
    shutil.copyfile(dump, DATA / "data-raw-dump.json")
    (DATA / "factorio-version.txt").write_text(version[1] + "\n")
    print(f"copied to {DATA / 'data-raw-dump.json'}")


if __name__ == "__main__":
    main()
