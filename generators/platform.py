"""Platform: a space platform for flights toward the shattered planet.

    python -m tools.bp gen platform railguns=4 thrusters=2 --validate

    north (front)   [R][R][R][R]      railgun turrets, poles behind them
                    [g][g][g][g][g]   gun turrets (railgun shots hit whatever is in front)
                    [B][ hub ][B]     the hub, at the blueprint's origin (not in it); cargo bays beside it
                    . . . . . .
    south (back)      [T]  [T]        thrusters, nothing behind them

Huge asteroids, which fill the route past the solar system edge, take a flat
3000 off physical hits and 99% of explosive, laser, fire and electric damage
(2.1 data, NOTES.md). Railgun ammo hits for 10000 physical, so railgun turrets
are the defence: one hit kills a huge asteroid, two a huge promethium one.

Small and medium asteroids have little or no resistance, so gun turrets take
them; railguns are set to target only big and huge ones, so they don't spend
a 170-tick reload on a small rock. Version 1 is the shape only: the flight
test supplies power (an energy source on the poles), ammo and thruster fuel
by script. Making them on board
comes later. Foundation covers the rectangle from the turrets to the
thrusters' back edge, so the thrusters' exhaust lane stays empty.

flight_tests flies it from the solar system edge toward the shattered planet.
"""

from draftsman import signatures
from draftsman.blueprintable import Blueprint

HUB = 4  # the hub covers tiles -4..3 on both axes
RAILGUN = (3, 5)  # tiles; Draftsman says 3x4 (NOTES.md)
THRUSTER = (4, 5)
GUN = 2
CARGO_BAY = 4  # square; 20 hub slots while joined to the hub, directly or through other bays (NOTES.md)
POLE = "medium-electric-pole"
POLE_REACH = 9  # its wire reach, tiles
# Railguns hold their slow, heavy shots for these; gun turrets take the rest.
BIG_TARGETS = [f"{size}-{kind}-asteroid" for size in ("huge", "big") for kind in ("metallic", "carbonic", "oxide",
                                                                                    "promethium")]
# The game numbers priority targets from 1; Draftsman's default 0 makes it drop the turret (NOTES.md).
TARGETS = [signatures.TargetID(index=i + 1, name=name) for i, name in enumerate(BIG_TARGETS)]
AMMO = {"railgun-turret": "railgun-ammo", "gun-turret": "uranium-rounds-magazine"}


def build(railguns=4, guns=6, thrusters=2, front_gap=2, back_gap=2, spacing=1, layout="rows", cargo_bays=0,
          margin=0) -> Blueprint:
    """layout: "rows" puts the gun turrets in a row behind the railguns; "front"
    spreads them through the railguns' row, front edges flush. A railgun fires
    only in a 72-degree arc ahead of it (turn_range 0.2, NOTES.md), so turrets
    beside it are out of its line, and from the front row they reach 18 tiles
    ahead of the railguns instead of 13.

    cargo_bays stand in the hub's rows, east and west, nearest first, in blocks
    of 2x2 that each touch the hub or the block inside: more hub slots for the
    spares it rebuilds from. margin is spare foundation, that many columns on
    each side, where asteroids eat it first (NOTES.md)."""
    if railguns < 1 or thrusters < 1:
        raise ValueError("a platform needs at least one railgun and one thruster")
    if layout not in ("rows", "front"):
        raise ValueError(f"layout is rows or front, not {layout!r}")
    if cargo_bays < 0 or margin < 0:
        raise ValueError("cargo_bays and margin can't be negative")
    rw, rh = RAILGUN
    tw, th = THRUSTER
    gw = GUN
    front = layout == "front"
    row = (railguns * rw + (guns * gw if front else 0)
           + (railguns + (guns if front else 0) - 1) * spacing)
    widths = [row, thrusters * tw + (thrusters - 1) * spacing,
              0 if front else guns * gw + max(guns - 1, 0) * spacing, 2 * HUB + 2 + 2 * CARGO_BAY * -(-cargo_bays // 4)]
    half = max(widths) // 2 + 1
    # North to south: railguns, a row of poles, gun turrets (rows), the gap, the hub.
    # Railgun shots hit everything along their line, own turrets included, so
    # nothing stands in front.
    gun_top = -HUB - front_gap - (0 if front or not guns else gw)
    rail_top = gun_top - 1 - rh
    top = rail_top
    bottom = HUB - 1 + back_gap + th  # last row of the thrusters: the foundation ends here

    bp = Blueprint()
    def count(n, thing):
        return f"{n} {thing}{'' if n == 1 else 's'}"

    bp.label = (f"Platform: {count(railguns, 'railgun')}, {count(guns, 'gun turret')}{' among them' if front else ''}, "
                f"{count(thrusters, 'thruster')}" + (f", {count(cargo_bays, 'cargo bay')}" if cargo_bays else ""))
    where = "between the railguns" if front else "behind them"
    bp.description = ("Railguns across the front for big and huge asteroids only, gun turrets " + where +
                      " for the rest, thrusters at the back, the hub in the middle (the blueprint's origin is the "
                      "hub's centre). Supply power, railgun ammo, magazines and thruster fuel and oxidizer.")
    if cargo_bays:
        bp.description += (" With construction-robotics researched the hub rebuilds what asteroids destroy from its "
                           "stock: fill the bays with spare turrets, poles and foundation, which goes fastest.")
    bp.icons = ["railgun-turret", "thruster"]
    for x in range(-half - margin, half + margin):
        for y in range(top, bottom + 1):
            bp.tiles.append("space-platform-foundation", position=(x, y))

    poles = []
    x = -(widths[0] // 2)
    if front:
        # Gun turrets spread evenly through the railguns+1 gaps (between and at the ends).
        slots = railguns + 1
        share = [guns * (i + 1) // slots - guns * i // slots for i in range(slots)]
    else:
        share = [0] * (railguns + 1)
        left = -(widths[2] // 2)
        for k in range(guns):
            bp.entities.append("gun-turret", tile_position=(left + k * (gw + spacing), gun_top))
    for i in range(railguns + 1):
        for _ in range(share[i]):
            bp.entities.append("gun-turret", tile_position=(x, rail_top))
            x += gw + spacing
        if i < railguns:
            cx = x + rw / 2
            bp.entities.append("railgun-turret", position=(cx, rail_top + rh / 2), priority_list=TARGETS)
            bp.entities[-1].extra_keys = {"ignore-unprioritised": True}  # the game's key; Draftsman's is dropped
            poles.append(int(cx))  # behind it: inside its supply area
            x += rw + spacing
    # Poles more than a wire's reach apart (gun turrets between) get one between.
    for a, b in zip(poles, poles[1:]):
        for k in range(1, -(-(b - a) // POLE_REACH)):
            poles.append(a + (b - a) * k // -(-(b - a) // POLE_REACH))
    for px in sorted(poles):
        bp.entities.append(POLE, tile_position=(px, rail_top + rh))

    for k in range(cargo_bays):
        column, place = divmod(k, 4)  # east above, west above, east below, west below
        x = (HUB + CARGO_BAY // 2 + CARGO_BAY * column) * (1 if place % 2 == 0 else -1)
        bp.entities.append("cargo-bay", position=(x, -CARGO_BAY // 2 if place < 2 else CARGO_BAY // 2))

    left = -(widths[1] // 2)
    for k in range(thrusters):
        x0 = left + k * (tw + spacing)
        bp.entities.append("thruster", position=(x0 + tw / 2, bottom - th + 1 + th / 2))
    bp.generate_power_connections()
    return bp


def flight_tests(seconds=300, research=None, start=0.0, supplies=None, **params):
    """research: technology series -> levels researched; start: share of the route
    to begin at; supplies: items into the hub, which rebuilds from them (with
    construction-robotics researched) and repairs with repair packs
    (validator.flight.Flight)."""
    from validator.flight import Flight

    shape = ", ".join(f"{params.get(k, d)} {k}" for k, d in (("railguns", 4), ("guns", 6), ("thrusters", 2)))
    return [Flight(shape, seconds=seconds, start=start, supplies=supplies or {}, fuel=True, power=True, ammo=AMMO,
                   research=research or {})]
