"""Download the Linux headless server into .factorio-headless/ (gitignored).

    python -m validator.fetch [VERSION]     # e.g. 2.1.22; default "latest"

The headless server is a free download and includes the expansion data.
"latest" is the newest experimental release (2.1 at the time of writing);
"stable" is the stable branch. Match the version you play: 2.0 and 2.1
disagree on some blueprint fields (NOTES.md). Linux x64 only; elsewhere point
the validator at your game executable with --factorio or $FACTORIO.
"""

import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from tools.factorio import HEADLESS

URL = "https://factorio.com/get-download/{version}/headless/linux64"
TARGET = HEADLESS.parents[3]  # .factorio-headless/


def fetch(version: str = "latest") -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "headless.tar.xz"
        print(f"downloading {URL.format(version=version)}", file=sys.stderr)
        # factorio.com answers Python's default User-Agent with 403.
        request = urllib.request.Request(URL.format(version=version), headers={"User-Agent": "ghostwriter-validator"})
        with urllib.request.urlopen(request) as resp, archive.open("wb") as out:
            shutil.copyfileobj(resp, out)
        unpacked = Path(tmp) / "unpacked"
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        if not (unpacked / "factorio" / "bin" / "x64" / "factorio").is_file():
            sys.exit("archive layout unexpected: no factorio/bin/x64/factorio")
        if TARGET.exists():
            shutil.rmtree(TARGET)
        TARGET.mkdir(parents=True)
        shutil.move(str(unpacked / "factorio"), TARGET / "factorio")
    HEADLESS.chmod(0o755)
    return HEADLESS


if __name__ == "__main__":
    print(fetch(sys.argv[1] if len(sys.argv) > 1 else "latest"))
