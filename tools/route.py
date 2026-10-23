"""Belts on a tile grid: the cheapest belt path between two tiles, going
underground under whatever is in the way, and where placed belts carry items.

`route` is A* over (tile, heading): a belt goes on straight or turns; an
underground pair jumps up to its reach, over anything. A pair may not overlap
another on the same line, since the game pairs each entrance with the nearest
exit ahead (NOTES.md). Used by the compact factory layout (tools/compact.py).
"""

import heapq
import math

from draftsman.constants import Direction
from draftsman.data import entities

N, E, S, W = Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST
STEP = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}
LEFT = {N: W, W: S, S: E, E: N}
RIGHT = {v: k for k, v in LEFT.items()}
BACK = {N: S, S: N, E: W, W: E}
# Costs: a belt tile, a turn, and an underground pair over and above the belts it
# replaces, so a pair is taken only to pass something (belts on open ground are cheaper).
BELT, TURN, TUNNEL = 1.0, 0.25, 1.0


def route(free, start, heading, goal, facing, reach, bounds, tunnels=(), limit=30000, cost=None):
    """Belt tiles from start, which items enter moving `heading`, to goal, placed
    facing `facing` (and never entered from its front).

    free(x, y): may a tile take a belt or an underground end. bounds: (x0, y0, x1, y1),
    inclusive. reach: an underground pair's farthest distance (the prototype's
    max_distance). tunnels: pairs already placed, (x, y, x2, y2) entrance to exit.
    cost(x, y): what taking a tile costs (default 1), for routing several belts
    that negotiate who gets a tile (route_all).
    Returns [(kind, x, y, direction)], kind "belt", "in" or "out", or None."""
    x0, y0, x1, y1 = bounds
    spans = [_span(t) for t in tunnels]

    def inside(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1

    def h(x, y):
        return abs(x - goal[0]) + abs(y - goal[1])

    tile = cost or (lambda x, y: BELT)

    first = (start[0], start[1], heading)
    seen = {first: 0.0}
    came = {}
    queue = [(h(*start), 0.0, first)]
    expanded = 0
    while queue and expanded < limit:
        _, cost, state = heapq.heappop(queue)
        if cost > seen.get(state, math.inf):
            continue
        expanded += 1
        x, y, d = state
        if (x, y) == goal:
            if d == BACK[facing]:
                continue
            path = [("belt", x, y, facing)]
            while state in came:
                state, step = came[state]
                path[:0] = step
            return path if _own_tunnels_apart(path) else None
        if not (inside(x, y) and free(x, y)):
            continue
        moves = []
        here = tile(x, y)
        for f in (d, LEFT[d], RIGHT[d]):
            dx, dy = STEP[f]
            moves.append(((x + dx, y + dy, f), here + (TURN if f != d else 0), [("belt", x, y, f)]))
        dx, dy = STEP[d]
        for k in range(2, reach + 1):
            ex, ey = x + k * dx, y + k * dy
            if not inside(ex, ey):
                break
            if not free(ex, ey) or any(_overlaps(_span((x, y, ex, ey)), s) for s in spans):
                continue
            # Ties go to the shorter pair: it's in the way of fewer others on its line.
            moves.append(((ex + dx, ey + dy, d), here + tile(ex, ey) + (k - 1) * BELT + TUNNEL + 0.01 * k,
                          [("in", x, y, d), ("out", ex, ey, d)]))
        for nxt, step_cost, step in moves:
            c = cost + step_cost
            if c < seen.get(nxt, math.inf):
                seen[nxt] = c
                came[nxt] = (state, step)
                heapq.heappush(queue, (c + h(nxt[0], nxt[1]), c, nxt))
    return None


def tunnels_of(path) -> list[tuple[int, int, int, int]]:
    """A path's underground pairs, entrance to exit."""
    ins = [(x, y) for k, x, y, _ in path if k == "in"]
    outs = [(x, y) for k, x, y, _ in path if k == "out"]
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(ins, outs)]


def _span(t):
    """A pair as (axis, line, low, high): which column or row it's on, and its extent."""
    ax, ay, bx, by = t
    return ("x", ax, min(ay, by), max(ay, by)) if ax == bx else ("y", ay, min(ax, bx), max(ax, bx))


def _overlaps(a, b) -> bool:
    return a[:2] == b[:2] and a[2] <= b[3] and b[2] <= a[3]


def _own_tunnels_apart(path) -> bool:
    spans = [_span(t) for t in tunnels_of(path)]
    return not any(_overlaps(a, b) for i, a in enumerate(spans) for b in spans[i + 1:])


BELTS = {"transport-belt", "fast-transport-belt", "express-transport-belt", "turbo-transport-belt"}


def reaches(blueprint: dict, start: tuple[int, int]) -> set[tuple[int, int]]:
    """Tiles items placed on the belt at start can get to: along belts, onto the side
    of a belt, through underground pairs and splitters (both outputs), and the tile
    in front of where they stop. Inserters and machines aren't followed."""
    at = {}  # tile -> (name, direction, kind, tiles of the entity)
    for e in blueprint["entities"]:
        name, d = e["name"], e.get("direction", 0)
        cx, cy = e["position"]["x"], e["position"]["y"]
        if name in BELTS:
            tiles = [(math.floor(cx), math.floor(cy))]
            kind = "belt"
        elif name.endswith("underground-belt"):
            tiles = [(math.floor(cx), math.floor(cy))]
            kind = e.get("type", "input")
        elif name.endswith("splitter"):
            tiles = ([(math.floor(cx - 1), math.floor(cy)), (math.floor(cx), math.floor(cy))] if d in (N, S)
                     else [(math.floor(cx), math.floor(cy - 1)), (math.floor(cx), math.floor(cy))])
            kind = "splitter"
        else:
            continue
        for t in tiles:
            at[t] = (name, d, kind, tiles)

    def accepts(tile, src):
        """Does the entity at tile take items pushed from the tile src?"""
        if tile not in at:
            return False
        name, d, kind, tiles = at[tile]
        dx, dy = STEP[d]
        behind = (tile[0] - dx, tile[1] - dy)
        front = (tile[0] + dx, tile[1] + dy)
        if kind == "splitter":
            return src == behind
        if kind == "output":
            return src not in (behind, front)
        return src != front

    def onward(tile):
        name, d, kind, tiles = at[tile]
        dx, dy = STEP[d]
        if kind == "input":
            reach = entities.raw[name]["max_distance"]
            for k in range(1, reach + 1):
                t = (tile[0] + k * dx, tile[1] + k * dy)
                if t in at and at[t][0] == name and at[t][2] == "output" and at[t][1] == d:
                    return [t]
            return []
        if kind == "splitter":
            return [(t[0] + dx, t[1] + dy) for t in tiles]
        return [(tile[0] + dx, tile[1] + dy)]

    got, todo = set(), [start]
    while todo:
        tile = todo.pop()
        if tile in got:
            continue
        got.add(tile)
        if tile not in at:
            continue
        name, d, kind, tiles = at[tile]
        nexts = onward(tile)
        if kind == "splitter":
            got.update(tiles)
        for n in nexts:
            if kind == "input" or accepts(n, tile) or n not in at:
                todo.append(n)
    return got


def route_all(conns, free, reach, bounds, rounds=30):
    """Several belts that mustn't share a tile, by negotiated congestion (PathFinder,
    as FPGA routers do): each round routes every belt again with the tiles others
    hold costing more, and tiles fought over in earlier rounds costing more still,
    until no tile is shared. conns: (start, heading, goal, facing, free_for) where
    free_for(x, y) adds the belt's own limits to free. Returns their paths, or None."""
    from collections import Counter, defaultdict

    use, history, paths = Counter(), defaultdict(float), [None] * len(conns)
    pressure, best, stale = 0.5, None, 0
    for _ in range(rounds):
        for n, (start, heading, goal, facing, mine) in enumerate(conns):
            if paths[n]:
                use.subtract(_tiles(paths[n]))
            others = [t for m, p in enumerate(paths) if p and m != n for t in tunnels_of(p)]

            def cost(x, y):
                return BELT + history[(x, y)] + pressure * use[(x, y)]

            path = route(lambda x, y: free(x, y) and mine(x, y), start, heading, goal, facing, reach, bounds,
                         others, cost=cost)
            if path is None:
                return None
            paths[n] = path
            use.update(_tiles(path))
        shared = [t for t, k in use.items() if k > 1]
        if not shared:
            return paths
        best = min(best, len(shared)) if best is not None else len(shared)
        stale = 0 if len(shared) <= best else stale + 1
        if stale >= 4:  # not getting better: give up early
            return None
        for t in shared:
            history[t] += 1
        pressure *= 1.6
    return None


def _tiles(path):
    return [(x, y) for _, x, y, _ in path]
