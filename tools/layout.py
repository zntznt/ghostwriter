"""Layout helpers shared by generators: inserter facing, beacon reach and pole placement."""

import math
from collections import deque
from itertools import combinations

from draftsman.constants import Direction
from draftsman.data import entities


def facing(pick_from_west: bool) -> Direction:
    # An inserter's direction points at the side it picks up from (prototype
    # pickup_position is north of an unrotated inserter); checked in game.
    return Direction.WEST if pick_from_west else Direction.EAST


def beacons_reaching(centre: tuple[float, float], half, beacons: list[tuple[float, float]]) -> int:
    """Beacons (by centre) whose supply area overlaps the collision box (half width `half`, or
    (x, y) halves for a long entity) of an entity at `centre`; a beacon's supply area reaches
    from its tile edge."""
    b = entities.raw["beacon"]
    reach = b["supply_area_distance"] + math.ceil(2 * b["collision_box"][1][0]) / 2
    halves = half if isinstance(half, tuple) else (half, half)
    return sum(all(abs(c - bc) < reach + h for c, bc, h in zip(centre, at, halves)) for at in beacons)


def poles(pole: str, powered: list[tuple[float, float, float]], free: dict[int, list[int]],
          bridges=()) -> list[tuple[int, int]]:
    """Top-left tiles for poles in a column layout.

    powered: (centre x, centre y, half width) of everything that needs power.
    free: tile column -> the tile rows free for a pole in it (a wider pole takes the
    columns and rows after its tile too).
    bridges: other free tiles, used only to join the poles into one network.

    Picks the smallest set of columns whose supply areas reach every powered entity
    across, gives each entity to the nearest of them that reaches it, and covers each
    column's entities with the fewest of its own rows; then joins the networks.
    """
    w, reach, supply = _pole(pole)
    tiles = {(x, y) for x, rows in free.items() for y in rows}
    anchors = _anchors(tiles, w)

    def covers(t, centre, half):  # supply area overlaps the collision box, along one axis
        return abs(centre - (t + w / 2)) < supply + half

    if not powered:
        return []
    rows = {}
    for x, y in anchors:
        rows.setdefault(x, []).append(y)
    reaching = [{c for c, ys in rows.items() if covers(c, x, h) and any(covers(r, y, h) for r in ys)}
                for x, y, h in powered]
    spots, error = _anchors(tiles | set(bridges), w), f"{pole} can't power everything from the free tiles"
    for n in range(1, len(rows) + 1):
        for subset in combinations(sorted(rows), n):
            if not all(r & set(subset) for r in reaching):
                continue
            mine = {c: [] for c in subset}
            for e, r in zip(powered, reaching):
                mine[min(r & set(subset), key=lambda c: abs(e[0] - (c + w / 2)))].append(e)
            placed = sorted(((c, y) for c, es in mine.items() for y in _rows(es, sorted(rows[c]), covers, reach)),
                            key=lambda t: (t[1], t[0]))
            if any(_overlap(a, b, w) for a, b in combinations(placed, 2)):
                continue
            try:
                return _join(pole, placed, spots)
            except ValueError as e:  # more columns may join where these can't
                error = str(e)
    raise ValueError(error)


def _rows(powered, rows, covers, reach):
    # Interval point cover: take entities by the last row that still reaches
    # them, and put each new row as far down as it can go, within wire reach of
    # the last where a row is (else _join bridges the gap).
    placed = []
    for _, y, h in sorted(powered, key=lambda e: e[1] + e[2]):
        if not any(covers(r, y, h) for r in placed):
            options = [r for r in rows if covers(r, y, h)]
            near = [r for r in options if placed and r - placed[-1] <= reach]
            placed.append(max(near or options))
    return placed


def poles_anywhere(pole: str, powered: list[tuple[float, float, float]], free: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Top-left tiles for poles chosen from any free tiles: greedily the spot that powers
    the most still-unpowered entities, then extra poles until all are on one network.

    powered: (centre x, centre y, half width) of everything that needs power.
    """
    w, _, supply = _pole(pole)

    def covers(tile, e):
        x, y, h = e
        return abs(x - (tile[0] + w / 2)) < supply + h and abs(y - (tile[1] + w / 2)) < supply + h

    todo, chosen, free = set(range(len(powered))), [], _anchors(set(free), w)
    while todo:
        best = max(sorted(free), key=lambda t: sum(covers(t, powered[i]) for i in todo), default=None)
        if best is None or not any(covers(best, powered[i]) for i in todo):
            raise ValueError(f"{pole} can't power everything from the free tiles")
        chosen.append(best)
        free = {t for t in free if not _overlap(t, best, w)}
        todo -= {i for i in todo if covers(best, powered[i])}
    return _join(pole, chosen, free)


def _pole(pole):
    """(tiles wide, wire reach, supply area distance)."""
    p = entities.raw[pole]
    return math.ceil(2 * p["collision_box"][1][0]), p["maximum_wire_distance"], p["supply_area_distance"]


def _anchors(tiles, w):
    """Top-left tiles where a w x w pole stands on free tiles only."""
    return {(x, y) for x, y in tiles if all((x + i, y + j) in tiles for i in range(w) for j in range(w))}


def _overlap(a, b, w):
    return abs(a[0] - b[0]) < w and abs(a[1] - b[1]) < w


def _join(pole, chosen, free):
    """Chosen poles plus bridges from `free` (top-left tiles) until all are on one network:
    each time, the fewest bridges that join the first network to another."""
    w, reach, _ = _pole(pole)
    r = math.floor(reach)
    steps = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if 0 < math.hypot(dx, dy) <= reach]
    chosen = list(chosen)
    while True:
        groups = _groups(chosen, reach, math.dist)
        if len(groups) == 1:
            return chosen
        others = {q for g in groups[1:] for q in g}
        free = {t for t in free if not any(_overlap(t, c, w) for c in chosen)}
        came = {p: None for p in groups[0]}
        queue, end = deque(sorted(groups[0])), None
        while queue and end is None:  # breadth first: fewest bridges
            u = queue.popleft()
            for dx, dy in steps:
                v = (u[0] + dx, u[1] + dy)
                if v in others:
                    end = u
                    break
                if v in free and v not in came:
                    came[v] = u
                    queue.append(v)
        if end is None:
            raise ValueError(f"{pole}: no free tiles to join the poles within wire reach")
        while came[end] is not None:
            chosen.append(end)
            end = came[end]


def _groups(points, reach, dist):
    groups = []
    for p in points:
        joined = [g for g in groups if any(dist(p, q) <= reach for q in g)]
        merged = [p] + [q for g in joined for q in g]
        groups = [g for g in groups if g not in joined] + [merged]
    return groups


def open_tiles(bp) -> set[tuple[int, int]]:
    """Tiles inside a blueprint's bounding box that no entity stands on: room for bridge poles."""
    taken = {(e.tile_position[0] + i, e.tile_position[1] + j)
             for e in bp.entities for i in range(e.tile_width) for j in range(e.tile_height)}
    xs, ys = [x for x, _ in taken], [y for _, y in taken]
    return {(x, y) for x in range(min(xs), max(xs) + 1) for y in range(min(ys), max(ys) + 1)} - taken
