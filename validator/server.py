"""A throwaway headless Factorio server running the validator scenario.

    with Server() as server:
        server.call("validate", blueprint_string)

Everything lives in a temp write-data dir (passed via --config), so a real
install's saves, mods and lock file are never touched. The game and DLC data
come from the executable's own read-data dir; mods are not loaded.
"""

import json
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from tools.factorio import data_dir, find_factorio
from validator.rcon import Rcon

SCENARIO = Path(__file__).resolve().parent / "scenario"
PASSWORD = "ghostwriter"


class ServerError(RuntimeError):
    pass


class Server:
    def __init__(self, factorio: str | None = None, startup_timeout: float = 180):
        self.exe = find_factorio(factorio)
        self.startup_timeout = startup_timeout

    def __enter__(self) -> "Server":
        self.dir = Path(tempfile.mkdtemp(prefix="ghostwriter-validator-"))
        shutil.copytree(SCENARIO, self.dir / "scenarios" / "ghostwriter")
        (self.dir / "config.ini").write_text(
            f"[path]\nread-data={data_dir(self.exe)}\nwrite-data={self.dir}\n"
        )
        # With no players connected a server pauses by default; circuit tests
        # need it to tick. The server wants every key, so start from the
        # example it ships with. Keep it off the LAN/public lists.
        settings = json.loads((data_dir(self.exe) / "server-settings.example.json").read_text())
        settings.update(auto_pause=False, visibility={"public": False, "lan": False})
        (self.dir / "server-settings.json").write_text(json.dumps(settings))
        rcon_port = _free_port(socket.SOCK_STREAM)
        self.log_path = self.dir / "server.log"
        self.proc = subprocess.Popen(
            [
                str(self.exe), "--config", str(self.dir / "config.ini"),
                "--start-server-load-scenario", "ghostwriter",
                "--server-settings", str(self.dir / "server-settings.json"),
                "--port", str(_free_port(socket.SOCK_DGRAM)),
                "--rcon-port", str(rcon_port), "--rcon-password", PASSWORD,
            ],
            stdin=subprocess.DEVNULL, stdout=self.log_path.open("w"), stderr=subprocess.STDOUT,
        )
        try:
            self._wait_for("Starting RCON interface")
            self.rcon = Rcon("127.0.0.1", rcon_port, PASSWORD)
            self.version = self.command('/sc remote.call("ghostwriter", "version")').strip()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        if getattr(self, "rcon", None):
            try:
                self.rcon.command("/quit")
            except Exception:
                pass
            self.rcon.close()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)

    def command(self, text: str) -> str:
        return self.rcon.command(text)

    def call(self, function: str, *args) -> dict:
        """remote.call into the scenario; it answers with JSON via rcon.print."""
        lua_args = "".join(", " + _lua_string(a) for a in args)
        reply = self.command(f'/sc remote.call("ghostwriter", "{function}"{lua_args})')
        try:
            return json.loads(reply)
        except json.JSONDecodeError:
            raise ServerError(f"{function}: unexpected reply: {reply[:500]}") from None

    def _wait_for(self, marker: str):
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            log = self.log_path.read_text(errors="replace")
            if marker in log:
                return
            if self.proc.poll() is not None:
                raise ServerError(f"server exited {self.proc.returncode}:\n{log[-3000:]}")
            time.sleep(0.25)
        raise ServerError(f"server not ready after {self.startup_timeout}s:\n{log[-3000:]}")


def _free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lua_string(text: str) -> str:
    # Blueprint strings are base64 or JSON; a long bracket avoids all escaping.
    level = 0
    while f"]{'=' * level}]" in text:
        level += 1
    eq = "=" * level
    return f"[{eq}[{text}]{eq}]"
