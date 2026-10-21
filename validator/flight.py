"""Flight tests: fly a space platform blueprint and see how far it gets.

    from validator.flight import Flight, run_flight
    flown = run_flight(server, blueprint_data, Flight("toward the shattered planet", seconds=600))

The blueprint is built around a fresh platform's hub: its origin is the
hub's centre, and the hub (8x8) is already there. Foundation comes from the
blueprint's tiles, or, if it has none, from under each entity. Supplies go
into the hub. The platform then leaves `origin` for `destination` (by default
the solar system edge toward the shattered planet, a 4,000,000 km connection)
on its own thrusters, at game speed SPEED, until its hub dies or `seconds` of
game time pass. Until a design makes its own, a script can keep thrusters
full (fuel), power its poles (power) and stock its turrets (ammo).

Asteroids spawn by the route's own rules, and flights differ run to run,
even each first flight in a fresh server (NOTES.md): judge a design over
several flights. The game is paused and advanced by exact tick counts, so a
flight lasts exactly as long as asked.
"""

import json
import time
from dataclasses import dataclass, field

from tools.codec import encode
from validator.circuit import _ok
from validator.server import Server, ServerError
from validator.validate import Report

SPEED = 100
POLL = 10  # game seconds between reads


@dataclass
class Flight:
    name: str
    origin: str = "solar-system-edge"
    destination: str = "shattered-planet"
    seconds: float = 600  # game time to fly at most
    supplies: dict[str, int] = field(default_factory=dict)  # into the hub at the start
    fuel: bool = False  # keep thrusters full by script
    power: bool = False  # an unlimited energy source on the platform's poles, by script
    ammo: dict[str, str] = field(default_factory=dict)  # turret name -> ammo kept stocked by script


@dataclass
class Flown:
    name: str
    km: float = 0.0  # furthest along the connection
    seconds: float = 0.0  # game time flown
    survived: bool = False  # the hub was alive when time ran out
    ended: str = ""  # what ended it, if the hub died
    deaths: dict[str, int] = field(default_factory=dict)  # "entity <- cause" -> how many
    cargo: dict[str, int] = field(default_factory=dict)  # in the hub at the last read
    speed_max: float = 0.0
    log: list[dict] = field(default_factory=list)  # each read: seconds, km, speed, hub health
    built: int = 0
    entities: int = 0
    error: str = ""

    def chunks(self, kind: str = "promethium") -> int:
        return self.cargo.get(f"{kind}-asteroid-chunk", 0)


def _step(server: Server, ticks: int, timeout: float):
    """Run exactly `ticks` ticks of the paused game and wait for them."""
    start = server.call("flight_step", str(ticks))["tick"]
    deadline = time.monotonic() + timeout
    while server.call("tick") < start + ticks:
        if time.monotonic() > deadline:
            raise ServerError(f"game did not advance {ticks} ticks in {timeout}s")
        time.sleep(0.02)


def run_flight(server: Server, data: dict, flight: Flight, report: Report) -> Flown:
    """Fly it; append an error to report if it couldn't be built or flown."""
    flown = Flown(flight.name)
    setup = server.call("flight_setup", json.dumps({
        "blueprint": encode(data), "from": flight.origin, "to": flight.destination,
        "supplies": flight.supplies or None, "fuel": flight.fuel, "power": flight.power,
        "ammo": flight.ammo or None, "speed": SPEED}))
    if "error" in setup:
        flown.error = f"flight: {setup['error']}"
        report.errors.append(flown.error)
        return flown
    flown.built, flown.entities = setup["built"], setup["entities"]
    if flown.built < flown.entities:
        report.warnings.append(f"flight '{flight.name}': built {flown.built} of {flown.entities} entities")
    try:
        elapsed = 0.0
        while elapsed < flight.seconds:
            step = min(POLL, flight.seconds - elapsed)
            _step(server, int(step * 60), timeout=step + 60)
            got = server.call("flight_read")
            _ok(got)
            elapsed = got["ticks"] / 60
            flown.km, flown.speed_max = got["km"], got["speed_max"]
            flown.deaths = got["deaths"] if isinstance(got["deaths"], dict) else {}
            flown.cargo = {c["name"]: c["count"] for c in got.get("cargo") or [] if isinstance(c, dict)}
            flown.log.append({"seconds": round(elapsed, 1), "km": round(got["km"]), "speed": got["speed"],
                              "hub": got["hub_health"]})
            if not got["alive"]:
                flown.ended = (got.get("ended") or {}).get("reason", "platform gone")
                flown.seconds = got.get("ended_after") or elapsed
                return flown
        flown.seconds, flown.survived = elapsed, True
        return flown
    finally:
        server.call("flight_teardown")
