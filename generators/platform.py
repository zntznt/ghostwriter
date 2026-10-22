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
from draftsman.constants import Direction
from draftsman.data import entities

HUB = 4  # the hub covers tiles -4..3 on both axes
RAILGUN = (3, 5)  # tiles; Draftsman says 3x4 (NOTES.md)
THRUSTER = (4, 5)
GUN = 2
ROCKET = 3  # rocket-turret, square
CARGO_BAY = 4  # square; 20 hub slots while joined to the hub, directly or through other bays (NOTES.md)
POLE = "medium-electric-pole"
POLE_REACH = 9  # its wire reach, tiles
# Railguns hold their slow, heavy shots for these; gun turrets take the rest.
BIG_TARGETS = [f"{size}-{kind}-asteroid" for size in ("huge", "big") for kind in ("metallic", "carbonic", "oxide",
                                                                                    "promethium")]
# The game numbers priority targets from 1; Draftsman's default 0 makes it drop the turret (NOTES.md).
TARGETS = [signatures.TargetID(index=i + 1, name=name) for i, name in enumerate(BIG_TARGETS)]
# rockets=N: rocket turrets take the big asteroids (3 rockets each, 10% explosion resistance)
# and the railguns keep to the huge ones, which take 99% off rockets (NOTES.md).
HUGE_TARGETS = [signatures.TargetID(index=i + 1, name=name) for i, name in enumerate(BIG_TARGETS[:4])]
ROCKET_TARGETS = [signatures.TargetID(index=i + 1, name=name) for i, name in enumerate(BIG_TARGETS[4:])]
AMMO = {"railgun-turret": "railgun-ammo", "gun-turret": "uranium-rounds-magazine", "rocket-turret": "rocket"}
FED = {k: AMMO[k] for k in ("railgun-turret", "gun-turret")}  # ammo_feed: what the hub feeds to which turret
# fuel=True: two asteroid collectors in the railgun row, a tile clear of the railguns
# beside them (their front corners then sit 45 degrees off a railgun's axis) and
# one apart, with the belt leaving from between them.
FUEL_SLOT = 9
CHUNKS = ["metallic-asteroid-chunk", "carbonic-asteroid-chunk", "oxide-asteroid-chunk"]
PROMETHIUM = "promethium-asteroid-chunk"  # promethium=True: the collectors take it too, and it goes to the hub
BELT = "transport-belt"
UNDER = "express-underground-belt"  # power=N: the cell belt under a reactor and its inserter, 8 tiles
# What each crusher hands to which plant: the module's two columns, west then east,
# top band then bottom (the ice plants feed the plants under them).
FUEL_BANDS = [("oxide", "ice-melting", "carbonic", "thruster-fuel"),
              ("oxide", "ice-melting", "metallic", "thruster-oxidizer")]
CRUISE = 0.62  # km a tick the fuel=True platform holds on what it makes (NOTES.md): its flights start there
# power=N: the first fusion reactor's centre, south-west of the hub, so that its generators,
# cooler and coolant lines stay west of the thruster's tanks and off its exhaust; each
# further plant stands PLANT_STEP west of the last.
REACTOR = (-10, HUB + 3)
PLANT_STEP = 11  # a tile clear of the last plant's west coolant line: pipes side by side join
CELLS = 50  # fusion cells into the hub for a flight, unless supplies name some: one hub slot
COOLANT = 10000  # cold fluoroketone into the reactor at the start, more than its loop holds
# ammo_feed: the two belts up from the hub's north face to the turrets, west and east. Each
# takes a belt from either side, so it needs the hub's columns beside it: -4 to 1 for both.
FEEDERS = (-HUB + 1, -HUB + 4)
UNDER_BELT = "underground-belt"  # ammo_feed: the feed belt under the chunk belt
# ammo_feed: its hub inserters move one item a swing, 2.5 a second, too few for the gun
# turrets 0.1 out; inserter capacity research (levels 2 and 7) makes it 3 a swing, 5.7 a
# second (NOTES.md). A base-game technology: its flights fly with it.
FEED_RESEARCH = {"inserter-capacity-bonus": 7}


def _inserter_for(demand: float) -> str:
    """The cheapest inserter moving `demand` items/s between two machines: one item a
    turn without research, so its turns a second (rotation_speed is turns a tick)."""
    for name in ("inserter", "fast-inserter"):
        if 60 * entities.raw[name]["rotation_speed"] >= demand:
            return name
    raise ValueError(f"no inserter moves {demand:g} items/s")


def _fuel_module(bp: Blueprint, xb: int, r: int, bottom: int):
    """Crushers, plants and pipes beside the chunk belt (column xb, flowing south) from
    row r, and the thruster's lines (one thruster, centred, its top row r + 6).

    Each crusher takes its chunk off the belt, puts back the chunk it returns
    (a chunk stacks to 1, so one left in its output stops it) and hands its
    product straight to its plant; the belt carries chunks only. An ice plant's
    outputs feed the plant under it directly. A thruster takes fuel on its east
    side at its middle row and oxidizer at its top row (NOTES.md)."""
    from tools import rates

    for side, (chunk1, plant1, chunk2, plant2) in zip((-1, 1), FUEL_BANDS):
        for band, (chunk, recipe) in enumerate(((chunk1, plant1), (chunk2, plant2))):
            y = r + 3 * band
            crusher_x = xb - 3 if side < 0 else xb + 2  # 2 wide, next to the belt's inserters
            bp.entities.append("crusher", tile_position=(crusher_x, y), recipe=f"{chunk}-asteroid-crushing")
            toward = Direction.EAST if side < 0 else Direction.WEST  # an inserter's direction is where it picks up
            bp.entities.append("inserter", tile_position=(xb + side, y), direction=toward)
            bp.entities.append("inserter", tile_position=(xb + side, y + 2), direction=toward.opposite(),
                               use_filters=True)
            bp.entities[-1].set_item_filter(0, f"{chunk}-asteroid-chunk")
            r_ = rates.recipe_any(recipe)
            item = next(iter(r_.ingredients))
            demand = r_.ingredients[item] * rates.crafts_per_second("chemical-plant", r_)
            bp.entities.append(_inserter_for(demand), tile_position=(xb + 4 * side, y + 1), direction=toward)
            bp.entities.append("chemical-plant", tile_position=(xb - 7 if side < 0 else xb + 5, y), recipe=recipe)
    # Oxidizer, from the east column's lower plant, along row r + 6: under the belt, then
    # under the fuel plant's outputs (two pipe-to-ground pairs, 10 apart at most).
    yo, yf = r + 6, r + 8
    bp.entities.append("pipe", tile_position=(xb + 5, yo))
    for x, d in ((xb + 4, Direction.EAST), (xb - 1, Direction.WEST), (xb - 2, Direction.EAST),
                 (xb - 9, Direction.WEST)):
        bp.entities.append("pipe-to-ground", tile_position=(x, yo), direction=d)
    for x in range(2, xb - 9):
        bp.entities.append("pipe", tile_position=(x, yo))
    # Fuel, from the west column's lower plant, down to row r + 8.
    for y in (yo, yo + 1):
        bp.entities.append("pipe", tile_position=(xb - 5, y))
    for x in range(2, xb - 4):
        bp.entities.append("pipe", tile_position=(x, yf))
    # What the belt brings and no crusher takes goes off the back edge into space.
    inflow = 2 * rates.inserter_rate("inserter", BELT, from_belt=False)
    void, n = rates.fewest_inserters(inflow, BELT, True, ("inserter", "fast-inserter"), margin=1.0)
    if n != 1:
        raise ValueError(f"the belt's end takes {n} {void}s; the layout has room for one")
    bp.entities.append(void, tile_position=(xb, bottom), direction=Direction.NORTH)


def _promethium_path(bp: Blueprint, xb: int, end: int = HUB - 2):
    """Promethium chunks off the chunk belt (column xb, flowing south) into the hub: a
    filter inserter onto a belt west along the gap above the hub to column end, and an
    inserter down into its north face. A chunk stacks to 1, so each takes a hub slot
    (NOTES.md). Fast inserters: a collector catches about 2.7 chunks/s where promethium is."""
    y = -HUB - 2  # the gap between the gun turrets and the hub (front_gap 2)
    bp.entities.append("fast-inserter", tile_position=(xb - 1, y), direction=Direction.EAST, use_filters=True)
    bp.entities[-1].set_item_filter(0, PROMETHIUM)
    for x in range(xb - 2, end - 1, -1):
        bp.entities.append(BELT, tile_position=(x, y), direction=Direction.WEST)
    bp.entities.append("fast-inserter", tile_position=(end, y + 1), direction=Direction.NORTH)


def _ammo_feed(bp: Blueprint, rails: list[int], guns: list[int], xb: int | None, y: int):
    """ammo_feed: railgun ammo and magazines from the hub to every turret along row y,
    between the railguns' pole row (y - 1) and the gun turrets' inserters (y + 1).

    At each of FEEDERS two filtered inserters on the hub's north face drop railgun ammo
    and magazines onto a belt pointing into a start belt, one from each side: the start
    belt takes each onto a lane of its own (NOTES.md), so a backed-up one doesn't stop
    the other. It runs north through a gap in the gun turrets and turns along row y, west
    from the west one and east from the east one, under the chunk belt (column xb) by an
    underground pair. Under each railgun (x, its west tile: its west or east column, off
    its pole) an inserter takes railgun ammo from the belt below; over each gun turret
    (gx) one takes magazines from the belt above: an inserter fills a turret to 10
    (automated_ammo_count, dump) and takes only what it can use."""
    row = -HUB - 2  # the gap row above the hub
    for t in FEEDERS:
        for side, d, ammo in ((-1, Direction.EAST, AMMO["railgun-turret"]), (1, Direction.WEST, AMMO["gun-turret"])):
            bp.entities.append("fast-inserter", tile_position=(t + side, row + 1), direction=Direction.SOUTH,
                               use_filters=True)
            bp.entities[-1].set_item_filter(0, ammo)
            bp.entities.append(BELT, tile_position=(t + side, row), direction=d)
        for yy in range(row, y, -1):
            bp.entities.append(BELT, tile_position=(t, yy), direction=Direction.NORTH)
    under = {xb - 1, xb, xb + 1} if xb is not None else set()

    def column(*options):  # the first under the row belt
        return next(c for c in options if (c <= FEEDERS[0] or c >= FEEDERS[1]) and c not in under)

    picks = ([(column(x, x + 2), y - 1, Direction.SOUTH) for x in rails]
             + [(column(gx, gx + 1), y + 1, Direction.NORTH) for gx in guns])
    cols = [c for c, _, _ in picks]
    bp.entities.append(BELT, tile_position=(FEEDERS[0], y), direction=Direction.WEST)
    for x in range(FEEDERS[0] - 1, min(cols) - 1, -1):
        bp.entities.append(BELT, tile_position=(x, y), direction=Direction.WEST)
    for x in range(FEEDERS[1], max(cols) + 1):
        if x in under:
            if x != xb:
                bp.entities.append(UNDER_BELT, tile_position=(x, y), direction=Direction.EAST,
                                   io_type="input" if x < xb else "output")
        else:
            bp.entities.append(BELT, tile_position=(x, y), direction=Direction.EAST)
    for c, yy, d in picks:
        bp.entities.append("inserter", tile_position=(c, yy), direction=d)


def _cell_path(bp: Blueprint, plants: int):
    """Fusion cells off the hub's south edge onto a belt west along row HUB + 1, and
    into each reactor's east side. The hub rebuilds a reactor with an empty fuel slot
    and fills none itself (NOTES.md), so every reactor has its inserter; past one,
    the belt runs under it and its inserter (an express underground spans 8 tiles)."""
    y = HUB + 1
    bp.entities.append("inserter", tile_position=(-HUB, HUB), direction=Direction.NORTH, use_filters=True)
    bp.entities[-1].set_item_filter(0, "fusion-power-cell")
    x = -HUB
    for k in range(plants):
        rx = REACTOR[0] - PLANT_STEP * k
        pick = rx + 4  # where this reactor's inserter takes cells from
        for bx in range(x, pick, -1):
            bp.entities.append(BELT, tile_position=(bx, y), direction=Direction.WEST)
        if k < plants - 1:
            bp.entities.append(UNDER, tile_position=(pick, y), direction=Direction.WEST, io_type="input")
            bp.entities.append(UNDER, tile_position=(rx - 4, y), direction=Direction.WEST, io_type="output")
            x = rx - 5
        else:
            bp.entities.append(BELT, tile_position=(pick, y), direction=Direction.WEST)
        bp.entities.append("inserter", tile_position=(rx + 3, y), direction=Direction.EAST)


def _power_plant(bp: Blueprint, rx: int, first: bool):
    """A fusion reactor (centred at rx), the two generators under it and a cryogenic
    plant cooling their coolant, with the hot and cold lines kept apart. The first
    plant also gets the accumulator: a reactor runs on its own output once going,
    and an accumulator charged before leaving starts it (NOTES.md). A platform's
    surface is one electric network, so nothing needs wiring."""
    ry = REACTOR[1]
    top = ry - 3  # its first row
    bp.entities.append("fusion-reactor", position=(rx, ry))
    # Turned south, each takes plasma from one of the reactor's south outputs.
    for gx in (rx - 2.5, rx + 2.5):
        bp.entities.append("fusion-generator", position=(gx, top + 8.5), direction=Direction.SOUTH)
    bp.entities.append("cryogenic-plant", position=(rx - 0.5, top + 14.5), direction=Direction.SOUTH,
                       recipe="fluoroketone-cooling")
    # Hot from the generators' backs into the cooler's inputs; cold from its outputs
    # round the west side into the reactor's west input.
    for x in range(rx - 4, rx + 4):
        bp.entities.append("pipe", tile_position=(x, top + 11))
    for x in range(rx - 6, rx + 3):
        bp.entities.append("pipe", tile_position=(x, top + 17))
    for y in range(top + 5, top + 17):
        bp.entities.append("pipe", tile_position=(rx - 6, y))
    for x in range(rx - 6, rx - 3):
        bp.entities.append("pipe", tile_position=(x, top + 4))
    if first:
        bp.entities.append("accumulator", position=(rx + 5, HUB + 3))


def build(railguns=4, guns=6, thrusters=2, front_gap=2, back_gap=2, spacing=1, layout="rows", cargo_bays=0,
          margin=0, fuel=False, reserve=False, power=False, side_guns=0, promethium=False, rockets=0,
          ammo_feed=False) -> Blueprint:
    """layout: "rows" puts the gun turrets in a row behind the railguns; "front"
    spreads them through the railguns' row, front edges flush. A railgun fires
    only in a 72-degree arc ahead of it (turn_range 0.2, NOTES.md), so turrets
    beside it are out of its line, and from the front row they reach 18 tiles
    ahead of the railguns instead of 13.

    cargo_bays stand in the hub's rows, east and west, nearest first, in blocks
    of 2x2 that each touch the hub or the block inside: more hub slots for the
    spares it rebuilds from. margin is spare foundation, that many columns on
    each side, where asteroids eat it first (NOTES.md).

    fuel makes the thruster's fuel and oxidizer on board (one thruster): two
    asteroid collectors in the railgun row, where the turrets break asteroids
    into chunks (collectors elsewhere catch almost none, NOTES.md), put
    metallic, carbonic and oxide chunks on a belt to crushers and chemical
    plants east of the hub; the thruster moves back to make room. The
    thruster's thrust follows how full it is kept, so it flies as fast as the
    plants supply it.

    reserve (with fuel) adds a storage tank on each of the thruster's west
    inputs, filled before leaving: where chunks are scarce, as at the solar
    system edge, the plants alone can't get it going (NOTES.md).

    power (a count, True for 1) makes its electricity on board: that many fusion
    reactors in a row south-west of the hub, each with two generators and a
    cryogenic plant cooling its coolant, and fusion cells fed from the hub to the
    first. Before leaving, the reactors are filled with cells and their loops with
    cold fluoroketone, and the accumulator and the railguns are charged: railguns
    take power first, as the reactors do, so one reactor can't keep up once they
    recharge at more than 90 MW (NOTES.md).

    side_guns stands that many gun turrets in a column at each outer edge, from
    the row below the gun turrets down: asteroids eat foundation from the side
    ends inward, past the gun row's reach (NOTES.md).

    promethium (with fuel) has the collectors take promethium chunks too, and a
    filter inserter sends them off the chunk belt along the gap above the hub and
    into it: the hub holds them, one a slot.

    rockets stands that many rocket turrets in a row behind the gun turrets (layout
    rows) for the big asteroids, and the railguns keep to the huge ones: 3 rockets
    kill a big asteroid with the flight research, at a hundredth of a hub slot each
    against a railgun shot's tenth (NOTES.md).

    ammo_feed has the hub feed every turret: railgun ammo and magazines by filtered
    inserters on its north face, two belts up through a gap in the gun turrets and one
    along a row added between the railguns' poles and the gun turrets, with an inserter
    into each turret (_ammo_feed). The railguns stand two rows further forward."""
    if railguns < 1 or thrusters < 1:
        raise ValueError("a platform needs at least one railgun and one thruster")
    if layout not in ("rows", "front"):
        raise ValueError(f"layout is rows or front, not {layout!r}")
    if cargo_bays < 0 or margin < 0 or side_guns < 0:
        raise ValueError("cargo_bays, margin and side_guns can't be negative")
    if fuel and thrusters != 1:
        raise ValueError("fuel=True makes fuel for one thruster")
    if fuel and layout != "rows":
        raise ValueError("fuel=True needs layout rows")
    if reserve and not fuel:
        raise ValueError("reserve=True needs fuel=True: it's the thruster's fuel and oxidizer")
    if power and thrusters != 1:
        raise ValueError("power=True has room beside one thruster")
    if power < 0:
        raise ValueError("power is how many fusion reactors, not a negative number")
    if promethium and not fuel:
        raise ValueError("promethium=True needs fuel=True: its collectors and chunk belt")
    if promethium and front_gap < 2:
        raise ValueError("promethium=True needs front_gap 2 or more: its belt runs in the gap above the hub")
    if rockets < 0 or (rockets and layout != "rows"):
        raise ValueError("rockets is a count of rocket turrets behind the gun turrets (layout rows)")
    if ammo_feed and (layout != "rows" or rockets or side_guns or front_gap != 2):
        raise ValueError("ammo_feed feeds railguns and gun turrets in rows, with front_gap 2: "
                         "not rocket turrets, side guns or layout front")
    rw, rh = RAILGUN
    tw, th = THRUSTER
    gw = GUN
    front = layout == "front"
    row = (railguns * rw + (guns * gw if front else 0)
           + (railguns + (guns if front else 0) - 1) * spacing + ((FUEL_SLOT + spacing) if fuel else 0))
    widths = [row, thrusters * tw + (thrusters - 1) * spacing,
              0 if front else guns * gw + max(guns - 1, 0) * spacing, 2 * HUB + 2 + 2 * CARGO_BAY * -(-cargo_bays // 4)]
    rocket_row = rockets * ROCKET + max(rockets - 1, 0) * spacing
    # The belt may push a gun turret on, and the feeders' gap (ammo_feed) up to FEEDERS' span and one.
    feed_shift = FEEDERS[1] - FEEDERS[0] + gw if ammo_feed else 0
    half = max(*widths[:2], widths[2] + (gw if fuel else 0) + feed_shift, rocket_row + (ROCKET if fuel else 0),
               widths[3]) // 2 + 1  # the belt may push a turret on
    # North to south: railguns, a row of poles, gun turrets (rows), rocket turrets, the
    # gap, the hub. Railgun shots hit everything along their line, own turrets included,
    # so nothing stands in front.
    rocket_top = -HUB - front_gap - (ROCKET if rockets else 0)
    gun_top = rocket_top - (0 if front or not guns else gw)
    rail_top = gun_top - 1 - rh - (2 if ammo_feed else 0)  # ammo_feed: its belt and the gun turrets' inserters
    top = rail_top
    bottom = HUB - 1 + back_gap + th  # last row of the thrusters: the foundation ends here
    full = bottom  # the last row of the full width
    if fuel:
        # The collectors' slot comes after the first `slot` railguns, so that the belt
        # between them runs down east of the hub and its bays, and far enough east
        # that the oxidizer line reaches the thruster under the fuel plant (12).
        east = max(HUB + CARGO_BAY * -(-cargo_bays // 4), 12)
        step = rw + spacing
        slot = min(railguns, max(0, -(-(east - 4 + widths[0] // 2) // step)))
        xb = -(widths[0] // 2) + slot * step + 4
        if xb < east:
            raise ValueError(f"fuel=True needs more railguns: the belt would run down x={xb}, inside the hub's rows")
        bottom = HUB + 10  # the module's two bands, its lines and the thruster (_fuel_module)
        full = min(full, bottom)
        if xb + 8 > half:  # the east chemical plants end at xb + 7; the full-width rows at half - 1
            raise ValueError(f"cargo_bays={cargo_bays}: the fuel module would stand off the foundation's east edge "
                             f"(the belt runs down x={xb}, the foundation ends at x={half - 1})")

    bp = Blueprint()
    def count(n, thing):
        return f"{n} {thing}{'' if n == 1 else 's'}"

    bp.label = (f"Platform: {count(railguns, 'railgun')}, {count(guns, 'gun turret')}{' among them' if front else ''}, "
                f"{count(thrusters, 'thruster')}" + (f", {count(cargo_bays, 'cargo bay')}" if cargo_bays else "")
                + (", makes its fuel" if fuel else "") + (" with a reserve" if reserve else "")
                + (", powers itself" if power else "") + (", keeps promethium" if promethium else "")
                + (f", {count(rockets, 'rocket turret')}" if rockets else "") + (", feeds its turrets" if ammo_feed else ""))
    where = "between the railguns" if front else "behind them"
    bp.description = ("Railguns across the front for big and huge asteroids only, gun turrets " + where +
                      " for the rest, thrusters at the back, the hub in the middle (the blueprint's origin is the "
                      "hub's centre). " + ("The hub feeds the turrets: stock it with railgun ammo and magazines. "
                                            "Supply power" if ammo_feed else "Supply power, railgun ammo, magazines") +
                      ("; it makes its thruster fuel and oxidizer (research space-platform-thruster)" +
                       ("; fill its tanks first." if reserve else ".") if fuel
                       else " and thruster fuel and oxidizer."))
    if cargo_bays:
        bp.description += (" With construction-robotics researched the hub rebuilds what asteroids destroy from its "
                           "stock: fill the bays with spare turrets, poles and foundation, which goes fastest.")
    if power:
        # The game keeps 500 bytes of a description: with power, a shorter one.
        bp.description = (f"Railguns in front for big and huge asteroids, gun turrets {'among them' if front else 'behind'}"
                          ", the hub at the origin. " +
                          ("The hub feeds the turrets (research " +
                           ", ".join(f"{k}-{v}" for k, v in FEED_RESEARCH.items()) + ")" if ammo_feed else
                           "Supply railgun ammo" + (", rockets" if rockets else "") + " and magazines") +
                          ("; it makes thruster fuel (research space-platform-thruster)" +
                           (", fill its tanks first" if reserve else "") if fuel else
                           ("; supply thruster fuel" if ammo_feed else " and thruster fuel")) +
                          ". Fusion power (research cryogenic-plant): before leaving fill the reactors with cells" +
                          ("" if ammo_feed else " (more in the hub)") + " and "
                          "the loops with cold fluoroketone, charge the accumulator and railguns." +
                          (" With construction-robotics the hub rebuilds from stock: fill the bays with " +
                           ("ammo, " if ammo_feed else "") + "spares and foundation." if cargo_bays else ""))
    bp.icons = ["railgun-turret", "thruster"]
    for x in range(-half - margin, half + margin):
        for y in range(top, full + 1):
            bp.tiles.append("space-platform-foundation", position=(x, y))
    for x in range(-tw // 2 - (3 if reserve else 1), xb + 8) if fuel else ():
        for y in range(full + 1, bottom + 1):  # behind, only the thruster's and the module's rows
            bp.tiles.append("space-platform-foundation", position=(x, y))
    if power:
        # Under the plant, wherever the rows above don't reach: from its west coolant
        # line to the hub's west edge, down to its cold line.
        laid = {(t.position.x, t.position.y) for t in bp.tiles}
        for x in range(REACTOR[0] - PLANT_STEP * (power - 1) - 7, -HUB + 1):
            for y in range(HUB, REACTOR[1] + 15):
                if (x, y) not in laid:
                    bp.tiles.append("space-platform-foundation", position=(x, y))

    poles, rail_xs, gun_xs = [], [], []
    x = -(widths[0] // 2)
    if front:
        # Gun turrets spread evenly through the railguns+1 gaps (between and at the ends).
        slots = railguns + 1
        share = [guns * (i + 1) // slots - guns * i // slots for i in range(slots)]
    else:
        share = [0] * (railguns + 1)
        left = -(widths[2] // 2)
        shift = 0
        for k in range(guns):
            gx = left + k * (gw + spacing) + shift
            if ammo_feed and gx <= FEEDERS[1] and gx + gw > FEEDERS[0]:  # leave the feeders' columns clear
                shift += FEEDERS[1] + 1 - gx
                gx = FEEDERS[1] + 1
            if fuel and gx <= xb < gx + gw:  # leave the belt's column clear
                shift += xb + 1 - gx
                gx = xb + 1
            bp.entities.append("gun-turret", tile_position=(gx, gun_top))
            gun_xs.append(gx)
        left, shift = -(rocket_row // 2), 0
        for k in range(rockets):
            rx = left + k * (ROCKET + spacing) + shift
            if fuel and rx <= xb < rx + ROCKET:  # leave the belt's column clear
                shift += xb + 1 - rx
                rx = xb + 1
            bp.entities.append("rocket-turret", tile_position=(rx, rocket_top), priority_list=ROCKET_TARGETS)
            bp.entities[-1].extra_keys = {"ignore-unprioritised": True}  # the game's key; Draftsman's is dropped
    for i in range(railguns + 1):
        for _ in range(share[i]):
            bp.entities.append("gun-turret", tile_position=(x, rail_top))
            x += gw + spacing
        if fuel and i == slot:
            # Two collectors facing out, the front row over empty space, and an inserter
            # behind each onto the belt that leaves from between them.
            for cx in (x + 2, x + 6):
                bp.entities.append("asteroid-collector", position=(cx + 0.5, top + 0.5),
                                   chunk_filter=CHUNKS + ([PROMETHIUM] if promethium else []))
                bp.entities.append("inserter", tile_position=(cx, top + 2), direction=Direction.NORTH)
            for bx, d in ((x + 2, Direction.EAST), (x + 3, Direction.EAST), (x + 6, Direction.WEST),
                          (x + 5, Direction.WEST)):
                bp.entities.append(BELT, tile_position=(bx, top + 3), direction=d)
            for y in range(top + 3, bottom):
                bp.entities.append(BELT, tile_position=(xb, y), direction=Direction.SOUTH)
            poles += [x + 2, x + 6]  # in the pole row, off the belt's column: no pole between lands on it
            x += FUEL_SLOT + spacing
        if i < railguns:
            cx = x + rw / 2
            rail_xs.append(x)
            bp.entities.append("railgun-turret", position=(cx, rail_top + rh / 2),
                               priority_list=HUGE_TARGETS if rockets else TARGETS)
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
    if fuel:
        _fuel_module(bp, xb, HUB, bottom)
    if promethium:
        _promethium_path(bp, xb, HUB - 1 if ammo_feed else HUB - 2)  # ammo_feed: its feeders need column 2
    if ammo_feed:
        _ammo_feed(bp, rail_xs, gun_xs, xb if fuel else None, gun_top - 2)
    for k in range(power):
        _power_plant(bp, REACTOR[0] - PLANT_STEP * k, k == 0)
    if power:
        _cell_path(bp, power)

    left = -(widths[1] // 2)
    for k in range(thrusters):
        x0 = left + k * (tw + spacing)
        bp.entities.append("thruster", position=(x0 + tw / 2, bottom - th + 1 + th / 2))
    if reserve:
        # A tank on each of its west inputs, fuel on its top row and oxidizer (the tank
        # turned east) on its middle one. Joined straight to the thruster, a tank shares
        # its line's one fill level, so thrust follows the reserve as it drains (NOTES.md).
        tx, ty = x0 + tw / 2, bottom - th + 1 + th / 2
        bp.entities.append("storage-tank", position=(tx - 3.5, ty - 3))
        bp.entities.append("storage-tank", position=(tx - 3.5, ty + 1), direction=Direction.EAST)
    for k in range(side_guns):
        y = gun_top + (k + 1) * (gw + spacing)  # behind the railguns' row: out of their arc
        if y + gw - 1 > full:
            raise ValueError(f"side_guns={side_guns}: each side end holds {k}")
        for gx in (-half - margin, half + margin - gw):
            if bp.find_entities_filtered(area=[gx + 0.1, y + 0.1, gx + gw - 0.1, y + gw - 0.1]):
                raise ValueError(f"side_guns={side_guns}: something already stands at ({gx}, {y})")
            bp.entities.append("gun-turret", tile_position=(gx, y))
    bp.generate_power_connections()
    return bp


def flight_tests(seconds=300, research=None, start=0.0, supplies=None, fuel=False, reserve=False, script_fuel=False,
                 power=False, magazine=AMMO["gun-turret"], reverse=False, take=None, ammo_feed=False, turn=0.0,
                 fill=False, **params):
    """research: technology series -> levels researched; start: share of the route
    to begin at; supplies: items into the hub, which rebuilds from them (with
    construction-robotics researched) and repairs with repair packs
    (validator.flight.Flight). A platform with fuel=True gets no fuel by script,
    the research its crushing and thruster recipes need, and starts as if it
    arrived from the last leg: thrusters full and moving at CRUISE. Standing
    still, it meets too few asteroids to make any fuel (NOTES.md). With reserve,
    its tanks start full too: in a game they'd fill while it waits in orbit.
    script_fuel flies a fuel=True layout on fuel by script, as the plain one
    flies: a control for what the module itself costs. A platform with power=True
    gets no stand-in power: its accumulator starts charged and its reactor's loop
    full of cold fluoroketone, as if readied in orbit, with CELLS fusion cells in
    the hub unless supplies name some. magazine: what the script stocks the gun
    turrets with; uranium rounds can't be made in space, piercing rounds can.
    reverse flies the same stretch back toward the solar system edge (start is
    still the share from the edge). take: items the script empties from the hub
    each second and counts, so a full hub doesn't cap what is measured. With ammo_feed the
    script stocks no turret: each is loaded once from the hub at the start, as its feed
    would in orbit, and what leaves the hub counts as used (Flight.feed). turn: a round trip,
    back to where it set out once it reaches that share of the route. fill: a fed platform
    readied in orbit first, its feed's belts and turrets filled while the hub holds its stock."""
    from validator.flight import PRIME, Flight

    if ammo_feed and magazine != AMMO["gun-turret"]:
        raise ValueError(f"ammo_feed's hub inserters take {AMMO['gun-turret']}, not {magazine}")
    if reverse and not 0 < start < 1:
        raise ValueError(f"a flight back starts partway along the route, not at {start} of it")
    if fill and not ammo_feed:
        raise ValueError("fill readies a hub feed in orbit; without ammo_feed there is none")
    if turn and (reverse or not start < turn < 1):
        raise ValueError(f"a round trip turns past where it starts ({start}) and before the end, not at {turn}"
                         + (" (and a flight back has nowhere to turn)" if reverse else ""))
    shape = ", ".join(f"{params.get(k, d)} {k}" for k, d in (("railguns", 4), ("guns", 6), ("thrusters", 2)))
    research = {**(research or {}), **({"space-platform": 1, "space-platform-thruster": 1} if fuel else {}),
                **({"cryogenic-plant": 1} if power else {})}  # the cooler's recipe
    supplies = {**({"fusion-power-cell": CELLS} if power else {}), **(supplies or {})}
    on_board = fuel and not script_fuel
    # With reserve, more than the thruster, its line and its tank hold: the game fills them.
    primed = PRIME + (2 * entities.raw["storage-tank"]["fluid_box"]["volume"] if reserve else 0) if on_board else 0
    route = {"origin": "shattered-planet", "destination": "solar-system-edge", "start": 1 - start} if reverse else {
        "start": start}
    return [Flight(shape, seconds=seconds, **route, supplies=supplies, fuel=not on_board, power=not power,
                   ammo={} if ammo_feed else {**AMMO, "gun-turret": magazine}, research=research, primed=primed, start_speed=CRUISE if on_board else 0.0,
                   charged=power, coolant=COOLANT if power else 0, take=list(take or []),
                   loaded=dict(FED) if ammo_feed else {}, feed=sorted(set(FED.values())) if ammo_feed else [],
                   turn=turn, fill=fill)]
