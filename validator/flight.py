"""Flight tests: fly a space platform blueprint and see how far it gets.

    from validator.flight import Flight, run_flight
    flown = run_flight(server, blueprint_data, Flight("toward the shattered planet", seconds=600))

The blueprint is built around a fresh platform's hub: its origin is the
hub's centre, and the hub (8x8) is already there. Foundation comes from the
blueprint's tiles, or, if it has none, from under each entity. Supplies go
into the hub. The platform then leaves `origin` for `destination` (by default
the solar system edge toward the shattered planet, a 4,000,000 km connection)
on its own thrusters, at game speed SPEED, until its hub dies or `seconds` of
game time pass. It can start partway (`start`, a share of the route), where
the route is busier: huge promethium asteroids are rare before a fifth of the
way (NOTES.md). Until a design makes its own, a script can keep thrusters
full (fuel), power its poles (power) and stock its turrets (ammo). The test
force has no research unless the flight sets some (research), so weapon
damage and shooting speed are the game's base values.

Asteroids spawn by the route's own rules, and flights differ run to run,
even each first flight in a fresh server (NOTES.md): judge a design over
several flights. The game is paused and advanced by exact tick counts, so a
flight lasts exactly as long as asked.
"""

import json
import time
from dataclasses import dataclass, field

from draftsman.data import entities

from tools.codec import encode
from validator.circuit import _ok
from validator.server import Server, ServerError
from validator.validate import Report

SPEED = 100
POLL = 10  # game seconds between reads
PRIME = entities.raw["thruster"]["fuel_fluid_box"]["volume"]  # a primed thruster's worth of each fluid
FUSION_CELL_J = 40e9  # a fusion power cell's fuel_value (dump)


@dataclass
class Flight:
    name: str
    origin: str = "solar-system-edge"
    destination: str = "shattered-planet"
    seconds: float = 600  # game time to fly at most
    start: float = 0.0  # where on the route it starts, as a share of its length from origin
    supplies: dict[str, int] = field(default_factory=dict)  # into the hub at the start
    fuel: bool = False  # keep thrusters full by script
    power: bool = False  # an unlimited energy source on the platform's poles, by script
    ammo: dict[str, str] = field(default_factory=dict)  # turret name -> ammo kept stocked by script
    # Technology series -> levels researched, e.g. {"railgun-damage": 4} for railgun-damage 1 to 4.
    # Set on the test force for the flight and undone after it; the test force has none otherwise.
    research: dict[str, int] = field(default_factory=dict)
    # As if it arrived from the last leg: that much of each fluid into each thruster once
    # at the start (it spreads into the line and any tank on it), and moving (km a tick).
    # A platform that makes its own fuel needs both: standing still it meets too few
    # asteroids to make any (NOTES.md).
    primed: float = 0
    start_speed: float = 0.0
    # A plant that makes its own power, readied as before leaving: every accumulator and
    # railgun charged and each fusion reactor's fuel slot full of cells (the accumulator
    # starts them), and cold fluoroketone into each fusion reactor, which spreads into
    # its coolant line.
    charged: bool = False
    coolant: float = 0
    # Items the script empties from the hub each second and counts (Flown.taken): what the
    # platform collects, measured whether or not the hub has room for it.
    take: list[str] = field(default_factory=list)
    # A platform whose hub feeds its turrets: each turret loaded once from the hub at the start
    # (turret name -> ammo), as the feed would in orbit, and these items counted as used as they
    # leave the hub (Flown.fed). With no ammo kept stocked by script, the hub is all they get.
    loaded: dict[str, str] = field(default_factory=dict)
    feed: list[str] = field(default_factory=list)
    # A round trip: at `turn` (a share of the route from origin) the schedule goes back to the
    # origin, and the flight ends when the platform is back there (Flown.back).
    turn: float = 0.0
    # Readied in orbit before leaving: in Nauvis orbit (chunks, no asteroids) the hub's feed
    # fills its belts and turrets while the script holds the hub's feed items at their stock,
    # as a player would top it up; then it leaves with the hub holding exactly that stock.
    fill: bool = False


@dataclass
class Flown:
    name: str
    km: float = 0.0  # furthest along the connection, from where it started
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
    # What the script gave (fuel, oxidizer, ammo), what of it was still held in
    # the thrusters and turrets at the last read, what the hub rebuilt from its
    # stock, and the power used (J): a budget for making them on board.
    supplied: dict[str, float] = field(default_factory=dict)
    held: dict[str, float] = field(default_factory=dict)
    rebuilt: dict[str, int] = field(default_factory=dict)
    power_j: float = 0.0
    made: dict[str, float] = field(default_factory=dict)  # thruster fuel and oxidizer made on board
    # Power made on board (J): what the fusion generators gave, what the plant itself
    # used (reactors and coolers), and the seconds any fusion reactor was short of power.
    generated_j: float = 0.0
    plant_j: float = 0.0
    brownout: int = 0
    taken: dict[str, int] = field(default_factory=dict)  # Flight.take: what the script emptied from the hub
    bays_lost: dict[str, int] = field(default_factory=dict)  # cargo bays that died, by |x| from the hub's centre
    fed: dict[str, float] = field(default_factory=dict)  # Flight.feed: what left the hub, the turrets' loading included
    dry: int = 0  # turret-seconds with no ammo
    odometer: float = 0.0  # km flown, out and back
    position: float = 0.0  # where it is, as a share of the route from origin
    turned: float = 0.0  # Flight.turn: seconds in when it turned round (0: it didn't)
    back: float = 0.0  # and when it was back at the origin
    # item -> km flown since when the hub has held none: a dead cargo bay's spill that comes back isn't it
    ran_out: dict[str, float] = field(default_factory=dict)
    low_fuel: int = 0  # seconds a thruster held under a tenth of its box of either fluid
    filled: dict[str, int] = field(default_factory=dict)  # Flight.fill: what readying drew from the hub
    at_turn: dict = field(default_factory=dict)  # Flight.turn: km flown and the hub's cargo as it turned

    def chunks(self, kind: str = "promethium") -> int:
        return self.cargo.get(f"{kind}-asteroid-chunk", 0)

    def budget(self) -> dict[str, float]:
        """What the flight used a second: each supply (given less held), power, and each thing rebuilt;
        and of what it made on board, what was still held at the end (in the thrusters and any reserve).
        Supplies and rebuilds also per 1,000 km flown: a slower platform lives longer partly because
        it's slower, and what it uses per distance compares designs at different speeds.
        What the script took from the hub (Flight.take), the same ways; and the speed over
        the flight's second half, once a reserve filled before leaving has drained toward
        where use meets what's made: what a long trip flies at."""
        if not self.seconds:
            return {}
        used = {k: self.supplied.get(k, 0) + self.fed.get(k, 0) - self.held.get(k, 0)
                for k in {**self.supplied, **self.fed}}
        out = {f"{k}/s": n / self.seconds for k, n in used.items()}
        out.update({f"taken {k}/s": n / self.seconds for k, n in self.taken.items()})
        if self.km:
            out.update({f"{k}/1000 km": n / self.km * 1000 for k, n in used.items()})
            out.update({f"rebuilt {k}/1000 km": n / self.km * 1000 for k, n in self.rebuilt.items()})
            out.update({f"taken {k}/1000 km": n / self.km * 1000 for k, n in self.taken.items()})
            out.update({f"cargo bays lost at |x| {k}/1000 km": n / self.km * 1000 for k, n in self.bays_lost.items()})
            if self.generated_j:
                out["fusion cells/1000 km"] = self.generated_j / FUSION_CELL_J / self.km * 1000
        late = [r for r in self.log if r["seconds"] >= self.log[-1]["seconds"] / 2] if self.log else []
        if len(late) > 1 and late[-1]["seconds"] > late[0]["seconds"]:
            out["cruise km/s"] = (late[-1]["km"] - late[0]["km"]) / (late[-1]["seconds"] - late[0]["seconds"])
        if self.power_j:
            out["power MW"] = self.power_j / self.seconds / 1e6
        if self.generated_j:
            # A fusion cell is 40 GJ, and once running the generators give out all of it (NOTES.md).
            out["power MW"] = (self.generated_j - self.plant_j) / self.seconds / 1e6
            out["generated MW"] = self.generated_j / self.seconds / 1e6
            out["fusion cells/h"] = self.generated_j / FUSION_CELL_J / self.seconds * 3600
            out["brownout s"] = self.brownout
        out.update({f"rebuilt {k}/s": n / self.seconds for k, n in self.rebuilt.items()})
        out.update({f"made {k}/s": n / self.seconds for k, n in self.made.items()})
        out.update({f"held {k}": self.held.get(k, 0.0) for k in self.made})
        if self.fed:
            out["dry turret s"] = self.dry
        return out


def _step(server: Server, ticks: int, timeout: float):
    """Run exactly `ticks` ticks of the paused game and wait for them."""
    start = server.call("flight_step", str(ticks))["tick"]
    deadline = time.monotonic() + timeout
    while server.call("tick") < start + ticks:
        if time.monotonic() > deadline:
            raise ServerError(f"game did not advance {ticks} ticks in {timeout}s")
        time.sleep(0.02)


FILL_SECONDS = 300  # readying in orbit (Flight.fill) stops once nothing is drawn for 3 s, or by then
LOW_FUEL = 0.1  # of a thruster's box: least thrust (NOTES.md)


def start_flight(server: Server, data: dict, flight: Flight) -> tuple[dict, dict[str, int]]:
    """Set the flight up, ready it in orbit if it fills (Flight.fill), and leave. Returns the
    setup's reply and what readying drew from the hub (and the script put back)."""
    setup = server.call("flight_setup", json.dumps({
        "blueprint": encode(data), "from": flight.origin, "to": flight.destination, "start": flight.start,
        "supplies": flight.supplies or None, "fuel": flight.fuel, "power": flight.power,
        "ammo": flight.ammo or None, "research": flight.research or None, "speed": SPEED,
        "primed": flight.primed or None, "start_speed": flight.start_speed or None,
        "charged": flight.charged, "coolant": flight.coolant or None, "take": flight.take or None,
        "loaded": flight.loaded or None, "feed": flight.feed or None, "turn": flight.turn or None,
        "fill": flight.fill, "low_fuel": LOW_FUEL * PRIME}))
    if "error" in setup or not flight.fill:
        return setup, {}
    idle, got = 0, {}
    for _ in range(FILL_SECONDS):
        _step(server, 60, timeout=60)
        got = server.call("flight_read")
        _ok(got)
        idle = idle + 1 if got["ticks"] >= 300 and not got["drawn"] else 0
        if idle >= 3:
            break
    _ok(server.call("flight_leave"))
    return setup, got["filled"] if isinstance(got.get("filled"), dict) else {}


def run_flight(server: Server, data: dict, flight: Flight, report: Report) -> Flown:
    """Fly it; append an error to report if it couldn't be built or flown."""
    flown = Flown(flight.name)
    try:
        setup, flown.filled = start_flight(server, data, flight)
        if "error" in setup:
            flown.error = f"flight: {setup['error']}"
            report.errors.append(flown.error)
            return flown
        flown.built, flown.entities = setup["built"], setup["entities"]
        if flown.built < flown.entities:
            report.warnings.append(f"flight '{flight.name}': built {flown.built} of {flown.entities} entities")
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
            # An empty Lua table comes back as a JSON list.
            flown.supplied, flown.held, flown.rebuilt, flown.made, flown.taken, flown.bays_lost, flown.fed = (
                got.get(k) if isinstance(got.get(k), dict) else {}
                for k in ("supplied", "held", "rebuilt", "made", "taken", "bays_lost", "fed"))
            flown.dry = got.get("dry") or 0
            flown.power_j = got.get("power_j") or 0.0
            flown.generated_j, flown.plant_j = got.get("generated_j") or 0.0, got.get("plant_j") or 0.0
            flown.brownout = got.get("brownout") or 0
            flown.odometer, flown.position = got.get("odometer") or 0.0, got.get("position") or 0.0
            flown.turned, flown.back = got.get("turned") or 0.0, got.get("back") or 0.0
            flown.ran_out = got["ran_out"] if isinstance(got.get("ran_out"), dict) else {}
            flown.low_fuel = got.get("low_fuel") or 0
            if isinstance(got.get("at_turn"), dict):
                flown.at_turn = {"km": got["at_turn"]["km"], "cargo": {
                    c["name"]: c["count"] for c in got["at_turn"].get("cargo") or [] if isinstance(c, dict)}}
            flown.log.append({"seconds": round(elapsed, 1), "km": round(got["km"]), "speed": got["speed"],
                              "hub": got["hub_health"], "position": flown.position, "odometer": round(flown.odometer)})
            if not got["alive"]:
                flown.ended = (got.get("ended") or {}).get("reason", "platform gone")
                flown.seconds = got.get("ended_after") or elapsed
                return flown
            if flown.back:
                flown.seconds, flown.survived = flown.back, True
                return flown
        flown.seconds, flown.survived = elapsed, True
        return flown
    finally:
        server.call("flight_teardown")
