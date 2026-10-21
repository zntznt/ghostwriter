"""Platform: a space platform for flights toward the shattered planet.

    python -m tools.bp gen platform railguns=4 thrusters=2 --validate

    north (front)   [R][R][R][R]      railgun turrets, poles behind them
                    [g][g][g][g][g]   gun turrets (railgun shots hit whatever is in front)
                       [ hub ]        the hub, at the blueprint's origin (not in it)
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
POLE = "medium-electric-pole"
# Railguns hold their slow, heavy shots for these; gun turrets take the rest.
BIG_TARGETS = [f"{size}-{kind}-asteroid" for size in ("huge", "big") for kind in ("metallic", "carbonic", "oxide",
                                                                                    "promethium")]
# The game numbers priority targets from 1; Draftsman's default 0 makes it drop the turret (NOTES.md).
TARGETS = [signatures.TargetID(index=i + 1, name=name) for i, name in enumerate(BIG_TARGETS)]
AMMO = {"railgun-turret": "railgun-ammo", "gun-turret": "uranium-rounds-magazine"}


def build(railguns=4, guns=6, thrusters=2, front_gap=2, back_gap=2, spacing=1) -> Blueprint:
    if railguns < 1 or thrusters < 1:
        raise ValueError("a platform needs at least one railgun and one thruster")
    rw, rh = RAILGUN
    tw, th = THRUSTER
    gw = GUN
    widths = [railguns * rw + (railguns - 1) * spacing, thrusters * tw + (thrusters - 1) * spacing,
              guns * gw + max(guns - 1, 0) * spacing, 2 * HUB + 2]
    half = max(widths) // 2 + 1
    # North to south: railguns, a row of poles, gun turrets, the gap, the hub. Railgun
    # shots hit everything along their line, own turrets included, so nothing stands in front.
    gun_top = -HUB - front_gap - (gw if guns else 0)
    rail_top = gun_top - 1 - rh
    top = rail_top
    bottom = HUB - 1 + back_gap + th  # last row of the thrusters: the foundation ends here

    bp = Blueprint()
    bp.label = f"Platform: {railguns} railguns, {guns} gun turrets, {thrusters} thrusters"
    bp.description = ("Railguns across the front for big and huge asteroids only, gun turrets behind them for the "
                      "rest, thrusters at the back, the hub in the middle (the blueprint's origin is the hub's "
                      "centre). Supply power, railgun ammo, magazines and thruster fuel and oxidizer.")
    bp.icons = ["railgun-turret", "thruster"]
    for x in range(-half, half):
        for y in range(top, bottom + 1):
            bp.tiles.append("space-platform-foundation", position=(x, y))

    left = -(widths[2] // 2)
    for k in range(guns):
        bp.entities.append("gun-turret", tile_position=(left + k * (gw + spacing), gun_top))
    left = -(widths[0] // 2)
    for k in range(railguns):
        cx = left + k * (rw + spacing) + rw / 2
        bp.entities.append("railgun-turret", position=(cx, rail_top + rh / 2), priority_list=TARGETS)
        bp.entities[-1].extra_keys = {"ignore-unprioritised": True}  # the game's key; Draftsman's is dropped
        # A pole behind each: inside its supply area, a turret's width from the next.
        bp.entities.append(POLE, tile_position=(int(cx), rail_top + rh))

    left = -(widths[1] // 2)
    for k in range(thrusters):
        x0 = left + k * (tw + spacing)
        bp.entities.append("thruster", position=(x0 + tw / 2, bottom - th + 1 + th / 2))
    bp.generate_power_connections()
    return bp


def flight_tests(seconds=300, research=None, start=0.0, **params):
    """research: technology series -> levels researched; start: share of the route
    to begin at (validator.flight.Flight)."""
    from validator.flight import Flight

    shape = ", ".join(f"{params.get(k, d)} {k}" for k, d in (("railguns", 4), ("guns", 6), ("thrusters", 2)))
    return [Flight(shape, seconds=seconds, start=start, fuel=True, power=True, ammo=AMMO, research=research or {})]
