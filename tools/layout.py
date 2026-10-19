"""Layout helpers shared by generators: inserter facing and pole placement."""

from itertools import combinations

from draftsman.constants import Direction
from draftsman.data import entities


def facing(pick_from_west: bool) -> Direction:
    # An inserter's direction points at the side it picks up from (prototype
    # pickup_position is north of an unrotated inserter); checked in game.
    return Direction.WEST if pick_from_west else Direction.EAST


def poles(pole: str, powered: list[tuple[float, float, float]], free: dict[int, list[int]]) -> list[tuple[int, int]]:
    """Tiles for poles in a column layout.

    powered: (centre x, centre y, half width) of everything that needs power.
    free: tile column -> the tile rows free for a pole in it.

    Picks the smallest set of columns whose supply areas reach every powered
    entity across, then the fewest rows (free in all those columns) that reach
    them all along, each within wire reach of the last.
    """
    reach = entities.raw[pole]["maximum_wire_distance"]
    supply = entities.raw[pole]["supply_area_distance"]

    def covers(c, centre, half):  # supply area overlaps the collision box
        return abs(centre - (c + 0.5)) < supply + half

    if not powered:
        return []
    columns = sorted(c for c, rows in free.items() if rows)
    for n in range(1, len(columns) + 1):
        for subset in combinations(columns, n):
            if not all(any(covers(c, x, h) for c in subset) for x, _, h in powered):
                continue
            if any(b - a > reach for a, b in zip(subset, subset[1:])):
                continue
            rows = sorted(set.intersection(*(set(free[c]) for c in subset)))
            placed = _rows(powered, rows, covers, reach)
            if placed is not None:
                return [(x, y) for y in placed for x in subset]
    raise ValueError(f"{pole} can't power everything from the free tiles")


def _rows(powered, rows, covers, reach):
    # Interval point cover: take entities by the last row that still reaches
    # them, and put each new row as far down as it can go.
    placed = []
    for _, y, h in sorted(powered, key=lambda e: e[1] + e[2]):
        if any(covers(r, y, h) for r in placed):
            continue
        options = [r for r in rows if covers(r, y, h) and (not placed or r - placed[-1] <= reach)]
        if not options:
            return None
        placed.append(max(options))
    return placed


def poles_anywhere(pole: str, powered: list[tuple[float, float, float]], free: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Tiles for poles chosen from any free tiles: greedily the tile that powers the
    most still-unpowered entities, then extra poles until all are on one network
    (each within wire reach of another).

    powered: (centre x, centre y, half width) of everything that needs power.
    """
    reach = entities.raw[pole]["maximum_wire_distance"]
    supply = entities.raw[pole]["supply_area_distance"]

    def covers(tile, e):
        x, y, h = e
        return abs(x - (tile[0] + 0.5)) < supply + h and abs(y - (tile[1] + 0.5)) < supply + h

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    todo, chosen, free = set(range(len(powered))), [], set(free)
    while todo:
        best = max(sorted(free), key=lambda t: sum(covers(t, powered[i]) for i in todo), default=None)
        if best is None or not any(covers(best, powered[i]) for i in todo):
            raise ValueError(f"{pole} can't power everything from the free tiles")
        chosen.append(best)
        free.discard(best)
        todo -= {i for i in todo if covers(best, powered[i])}
    # Join the networks: bridge the closest pair of groups through the free tile
    # that brings them nearest, until one group remains.
    while True:
        groups = _groups(chosen, reach, dist)
        if len(groups) == 1:
            return chosen
        a, b = min(((p, q) for p in groups[0] for g in groups[1:] for q in g), key=lambda pq: dist(*pq))
        step = min((t for t in free if dist(t, a) <= reach), key=lambda t: dist(t, b), default=None)
        if step is None or dist(step, b) >= dist(a, b):
            raise ValueError(f"{pole}: no free tiles to join the poles within wire reach")
        chosen.append(step)
        free.discard(step)


def _groups(points, reach, dist):
    groups = []
    for p in points:
        joined = [g for g in groups if any(dist(p, q) <= reach for q in g)]
        merged = [p] + [q for g in joined for q in g]
        groups = [g for g in groups if g not in joined] + [merged]
    return groups
