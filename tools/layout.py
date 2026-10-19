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
