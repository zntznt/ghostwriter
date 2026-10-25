"""Pipes on a tile grid: a fluid line joined through pipes and pipe-to-ground pairs,
never touching another fluid's. Used by the compact factory layout (tools/compact.py).

A plain pipe joins every neighbour that connects back; a pipe-to-ground only on its
surface side (its blueprint direction), and underground to the first opposite one
within reach on its line (NOTES.md). So a pipe may stand only where no neighbour of
another fluid connects toward it, and a pipe-to-ground pair may not overlap another
pair on its row or column, of any fluid.

`route_line` joins a line's terminals one at a time to the tree already joined, by
A* over (tile, heading): a pipe, then any way on; or a pipe-to-ground pair straight
on, over anything. Pairs cost less than the pipes they replace, so a line takes few
tiles a belt could use.
"""

import heapq
import math
from collections import deque

from tools.pipes import REACH, VEC
from tools.route import BACK, STEP, E, N, S, W

PIPE = 1.5  # a pipe tile: it blocks a belt's way, where a pair's middle doesn't
PAIR = 2.0  # a pipe-to-ground pair's two ends
PAIR_STEP = 0.1  # per tile a pair spans, so ties go to the shorter
ALL = {STEP[d] for d in (N, E, S, W)}


def sides(name, direction) -> set[tuple[int, int]]:
    """Offsets the thing at a tile connects toward: every side for a pipe, the surface
    side for a pipe-to-ground (its direction)."""
    if name == "pipe-to-ground":
        return {VEC[int(direction or 0)]}
    return set(ALL)


class Fluids:
    """What's on the grid that pipes connect to, and the pipes placed.

    at: tile -> (fluid, sides it connects toward); placed: tile -> (name, direction)
    for the pipes routed or fixed here; spans: pipe-to-ground pairs as (axis, line, low,
    high), axis "x" for a column."""

    def __init__(self, known=None):
        self.at = dict(known or {})
        self.placed = {}
        self.spans = []
        self.kept = {}

    def keep(self, fluid, tile):
        """A tile a line of fluid will take (its way into a port): no other fluid's piece
        may stand on it, or connect toward it."""
        self.kept[tile] = fluid

    def fix(self, fluid, tile, name, direction=None):
        """A pipe or pipe-to-ground placed outright (a terminal, an input)."""
        self.at[tile] = (fluid, sides(name, direction))
        self.placed[tile] = (name, direction)

    def connects(self, tile, toward) -> bool:
        """Does the thing at tile connect to its neighbour toward?"""
        got = self.at.get(tile)
        return got is not None and (toward[0] - tile[0], toward[1] - tile[1]) in got[1]

    def foreign(self, fluid, tile, offsets) -> bool:
        """Would a connector of fluid at tile, connecting toward offsets, join another fluid
        (or a tile another fluid's line will take)?"""
        for dx, dy in offsets:
            n = (tile[0] + dx, tile[1] + dy)
            got = self.at.get(n)
            if (got and got[0] != fluid and self.connects(n, tile)) or self.kept.get(n, fluid) != fluid:
                return True
        return False

    def mixes(self) -> list[tuple]:
        """Pairs of neighbouring tiles of two fluids that connect to each other."""
        got = []
        for tile, (fluid, offsets) in self.at.items():
            for dx, dy in offsets:
                n = (tile[0] + dx, tile[1] + dy)
                if n in self.at and self.at[n][0] != fluid and self.connects(n, tile):
                    got.append((tile, n))
        return got

    def put(self, fluid, path):
        for kind, x, y, d in path:
            if kind == "pipe":
                self.fix(fluid, (x, y), "pipe")
            else:
                self.fix(fluid, (x, y), "pipe-to-ground", d)
        for a, b in _pairs(path):
            self.spans.append(_span(a, b))


def route_line(fluids, fluid, terminals, ok, bounds, limit=60000):
    """Join terminals [(connector tiles, (approach tile, heading))] into one network of
    fluid: the first is the tree, each next (nearest the tree first) routed from its
    approach tile (whose piece connects back to it, entered moving heading) until a piece
    connects into the tree. ok(x, y): may a tile take a pipe or a pipe-to-ground end.
    Places the paths in fluids; ValueError if a terminal can't be joined."""
    tree = set(terminals[0][0])
    todo = list(terminals[1:])
    while todo:
        near = _distances(tree, bounds)
        todo.sort(key=lambda t: near.get(t[1][0], math.inf))
        connectors, (start, heading) = todo.pop(0)
        if tree & connectors:
            continue
        path = route(fluids, fluid, start, heading, tree, ok, bounds, near, limit)
        if path is None:
            raise ValueError(f"{fluid}: no way for its pipes to {start} that keeps clear of other fluids")
        fluids.put(fluid, path)
        tree |= {(x, y) for _, x, y, _ in path} | set(connectors)
    for tile in [t for t, f in fluids.kept.items() if f == fluid]:  # taken now, as its pieces show
        del fluids.kept[tile]


def route(fluids, fluid, start, heading, tree, ok, bounds, near=None, limit=60000):
    """Pipe pieces [(kind, x, y, direction)] from start, entered moving heading, until a
    piece connects into a tile of tree. kind "pipe", or "in" and "out" for a pair's ends,
    direction their surface side. None if there's none."""
    x0, y0, x1, y1 = bounds
    near = near if near is not None else _distances(tree, bounds)

    def inside(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1

    def usable(x, y):
        return inside(x, y) and ok(x, y) and (x, y) not in fluids.at and fluids.kept.get((x, y), fluid) == fluid

    def joins(tile, offsets):
        return any((tile[0] + dx, tile[1] + dy) in tree and fluids.connects((tile[0] + dx, tile[1] + dy), tile)
                   for dx, dy in offsets)

    def h(x, y):
        return 0.25 * near.get((x, y), math.inf)

    on_line = {}  # (axis, line) -> [(low, high)]: the pairs already placed on each column and row
    for axis, line, low, high in fluids.spans:
        on_line.setdefault((axis, line), []).append((low, high))

    def crosses(span, own):  # does a new pair overlap one placed, or one on its own way here?
        axis, line, low, high = span
        return any(low <= b and a <= high for a, b in on_line.get((axis, line), ())) or \
            any(_overlaps(span, s) for s in own)

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
        if not usable(x, y):
            continue
        tiles, spans = _own(came, state)  # the way here: one piece a tile, one pair a stretch
        if (x, y) in tiles:
            continue
        back = BACK[d]
        # A pipe here: it connects every way, so no other fluid may connect to it.
        if not fluids.foreign(fluid, (x, y), ALL):
            step = [("pipe", x, y, None)]
            if joins((x, y), ALL):
                return _path(came, state, step)
            for f in (N, E, S, W):
                if f == back:
                    continue
                dx, dy = STEP[f]
                _push(queue, seen, came, (x + dx, y + dy, f), cost + PIPE, state, step, h)
        # A pair straight on, its entrance facing back the way the line came.
        dx, dy = STEP[d]
        for k in range(2, REACH + 1):
            ex, ey = x + k * dx, y + k * dy
            if not inside(ex, ey):
                break
            span = _span((x, y), (ex, ey))
            if not usable(ex, ey) or (ex, ey) in tiles or crosses(span, spans):
                continue
            step = [("in", x, y, back), ("out", ex, ey, d)]
            onto = (ex + dx, ey + dy)
            if onto in tree and fluids.connects(onto, (ex, ey)):
                return _path(came, state, step)
            _push(queue, seen, came, (onto[0], onto[1], d), cost + PAIR + PAIR_STEP * k, state, step, h)
    return None


def _push(queue, seen, came, nxt, cost, state, step, h):
    if cost < seen.get(nxt, math.inf):
        seen[nxt] = cost
        came[nxt] = (state, step)
        heapq.heappush(queue, (cost + h(nxt[0], nxt[1]), cost, nxt))


def _path(came, state, last):
    path = list(last)
    while state in came:
        state, step = came[state]
        path[:0] = step
    return path


def _own(came, state):
    """The tiles, and the pairs' spans, of the pieces on the way to state."""
    tiles, spans = set(), []
    while state in came:
        state, step = came[state]
        tiles |= {(x, y) for _, x, y, _ in step}
        if len(step) == 2:
            spans.append(_span((step[0][1], step[0][2]), (step[1][1], step[1][2])))
    return tiles, spans


def _pairs(path):
    ins = [(x, y) for k, x, y, _ in path if k == "in"]
    outs = [(x, y) for k, x, y, _ in path if k == "out"]
    return list(zip(ins, outs))


def _span(a, b):
    """A pair as (axis, line, low, high): the column or row it's on, and its extent."""
    if a[0] == b[0]:
        return ("x", a[0], min(a[1], b[1]), max(a[1], b[1]))
    return ("y", a[1], min(a[0], b[0]), max(a[0], b[0]))


def _overlaps(a, b) -> bool:
    return a[:2] == b[:2] and a[2] <= b[3] and b[2] <= a[3]


def _distances(tiles, bounds) -> dict:
    """Steps from the nearest of tiles to every tile in bounds, ignoring what's in the way."""
    x0, y0, x1, y1 = bounds
    got = {t: 0 for t in tiles}
    queue = deque(tiles)
    while queue:
        x, y = queue.popleft()
        for dx, dy in ALL:
            n = (x + dx, y + dy)
            if n not in got and x0 - 1 <= n[0] <= x1 + 1 and y0 - 1 <= n[1] <= y1 + 1:
                got[n] = got[(x, y)] + 1
                queue.append(n)
    return got
