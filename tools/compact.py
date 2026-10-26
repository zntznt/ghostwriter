"""Compact factory layout: `factory`'s rows packed on shelves, belts routed between them.

    python -m tools.bp gen factory item=electronic-circuit rate=5 layout=compact --validate

The bus layout returns every product to a bus and taps it again for the next
row; this one hands products over directly (docs/layout-optimizer.md):

    ↑ product (merged) leaves at the top
    ║ ║  ┌──────┐   ┌──────┐     shelf 1: consumers, side by side
    ║ ║  └▲─────┘   └▲────▲┘
    ║ ║═══╪══┬═══════╪════╡       channel: a bus tap, split chains, adapters,
    ║ ║    ╰─┴───┐ ┌─╯    │       belts routed by A* (tools/route.py) between
    ║ ║  ┌──────┐ │ │┌──────┐     shelf 0: producers, side by side
    ║ ║  └▲────▲┘   └▲────▲┘
    ║ ║═══╧════╧═════╧════╧═      channel 0: taps from the bus to shelf 0
    ↑ raw inputs enter at the bottom of the west bus

Placement is tools/shelves.py, the merges, combs and taps tools/belts.py.

- Rows keep their own templates (inputs at the south corners, the product out of
  the top). A row goes on a shelf once every row it takes from is on a lower one,
  and a shelf is as wide as the search allows: several widths are tried and the
  smallest box that routes is kept.
- Raw inputs ride a bus on the west, one column per line with a spare column
  for splitters; a splitter taps it in each channel, the last use turns the line.
  The bus dives under the taps' branches.
- Made items go straight from their rows to the rows that use them: merged
  through splitters when several rows make one line, split by a chain of
  splitters when it feeds several ports.
- A port with two items gets an adapter: a belt below it fed from both sides,
  the left item onto the left lane from the west, the right onto the right lane
  from the east (NOTES.md).
- A product belt that also carries an ingredient's leftovers (a fluid row's near
  lane) leaves through a filter splitter that keeps the ingredient in the row.
- Fluids go through pipes and pipe-to-ground pairs that touch no other fluid
  (tools/pipe_route.py), routed before the belts, which go round or under them:
  between the rows that make and take each (fluid rows at their south end, fluid
  blocks at their lines' west ends), a supplied one in at the bottom and a fluid
  product or left-over out at the top, each through a pipe of its own.
- Cracking takes its oil through a gate, as on the bus: a pump wired to a tank on
  the oil's line, running while the tank holds more than factory.GATE_LEVEL.
"""

import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import cached_property

from draftsman.blueprintable import Blueprint
from draftsman.data import entities

from generators import factory
from tools import belts, pipes, rates
from tools.belts import Blocked, Conn
from tools.pipe_route import ALL, Fluids, route_line, sides
from tools.route import E, N, S, W, Work, route_all, tunnels_of
from tools.shelves import GAP, MIN_CHANNEL, far, least_area, localize, place, restore, shelve, width, widths

MAX_EXTRA = 14  # rows added to every channel before a width is given up
# Columns more west of a shelf whose first row has a fluid port on its west side between
# two others (refining's light oil): with the yard's two alone it has no way out. Given to
# every row with west fluid ports, processing units at 0.5/s stopped routing.
FLUID_ROOM = 2
# A* steps (route.Work) the whole search for a factory may take, shared by factory._compact:
# the least that lays out every request measured no larger than when seconds cut it (4
# million left chemical science at 4,356 tiles against 4,268; docs/layout-optimizer.md).
# Steps are the same on any machine, so the same request always gives the same layout.
STEPS = 5_000_000
# Steps a negotiation may take: the most one that routed took on the 34 requests measured
# was 160,041; the ones that never route ran on to 1.5 million, stuck a few tiles short.
NEGOTIATION = 300_000
# Seconds, only as a safety: steps end every search measured long before it (the slowest,
# chemical science from crude oil at research 7, in 62 s on this Mac, CI's runners about 1.8
# times slower). Past it a search keeps the best so far, which then depends on the machine.
BUDGET = 300.0
# The exact pass (exact): steps for every row set factory tries, each layout tried charged
# TRY besides its A* steps, as most of one that doesn't route spends none, only time (about
# 0.3 ms here, an A* step about 4 us); and the most layouts whose least box is under the
# smallest so far it starts on. Batteries at 2/s have 4,956 under the search's 1,044 tiles,
# and it tries 1,293 as the smallest so far falls (to 968); advanced circuits 40,363 of
# 178,044 (1,242 tiles to 1,134), in about 7 s here; electric engines have 504,384.
EXACT = 4_000_000
TRY = 70
TRIES = 200_000


class CompactError(ValueError):
    """These rows can't be laid out compact (the bus layout still can)."""


@dataclass
class Result:
    blueprint: Callable[[], Blueprint]  # the layout's blueprint, built on first use (bp)
    area: int
    inputs: list  # (id, item)
    outputs: list  # (id, item)
    height: int
    channels_used: list  # per channel, the rows anything but the rows used
    ports: list = field(default_factory=list)  # ("in", tile, items it takes) and ("out", tile, item)
    bus: list = field(default_factory=list)  # (item, column, northmost row) per raw line, west to east
    shelves: list = field(default_factory=list)  # (west column, south row, north row) per shelf
    leftovers: list = field(default_factory=list)  # (product port tile, ingredient it also carries)
    fluid_inputs: list = field(default_factory=list)  # (id, fluid)
    fluid_ports: list = field(default_factory=list)  # (tile, fluid): row fluid ports, inputs and outputs
    fluid_outputs: list = field(default_factory=list)  # (id, fluid, "product" or "surplus")
    bound: dict = field(default_factory=dict)  # lower_bound's, the least over the rows factory tried
    optimal: bool = False  # exact tried every layout under it of every row set factory tried

    @cached_property
    def bp(self) -> Blueprint:
        return self.blueprint()


def build(p, work=None, beat=math.inf) -> Result:
    """The smallest compact layout of the rows in p (factory.rows_and_lines) that routes
    within work (a route.Work; by default STEPS, and BUDGET seconds as a safety). For
    each shelf width, and each way of carrying items used far from their rows (routed
    straight from them, or on the bus): every channel grown until it routes, then cut to
    the rows it used. The best of those then loses a channel row at a time while it still
    routes and gets no bigger. Shelves are tried least room first, and a channel height
    whose rows and gaps alone already take more room than the best so far isn't tried,
    nor shelves whose rows and thinnest channels take as much as beat (a layout found
    before). Last, it tries every shelf as far west as the bus lets it (_attempt's west),
    and keeps that if smaller. Out of work, it keeps the best so far."""
    _gated(p)
    _refuse(p)
    work = Work(STEPS, seconds=BUDGET) if work is None else work
    candidates = {tuple(map(tuple, shelve(p, wide))) for wide in widths(p["rows"])}
    best = None
    for shelves in sorted(candidates, key=lambda s: least_area(p, s)):
        if least_area(p, shelves) >= beat:  # nor any after: none of them can be smaller
            break
        for bus_far in (False, True):
            if work.over():
                break
            got = _fitted(p, shelves, bus_far, best[0].area if best else math.inf, work)
            if got and (best is None or got[0].area < best[0].area):
                best = got + (shelves, bus_far)
    if best is None:
        if work.over():
            raise CompactError("no layout routed within its budget")
        if beat < math.inf:
            raise CompactError(f"no layout smaller than {beat:,} tiles")
        raise CompactError("the rows don't route in any shelf width tried")
    got, heights, shelves, bus_far = best
    got, heights = _trimmed(p, shelves, heights, bus_far, work, got)
    west = False
    if not work.over():
        try:
            moved = _attempt(p, shelves, heights, bus_far, work, west=True)
        except Blocked:
            moved = None
        if moved is not None:
            moved, moved_heights = _trimmed(p, shelves, heights, bus_far, work, moved, west=True)
            if moved.area < got.area:
                got, heights, west = moved, moved_heights, True
    # Last, the kept layout's channels thinner still with taps closer together (belts.Taps
    # close), from its heights: never larger than without them, whatever they'd do to the
    # search before (docs/layout-optimizer.md, "Thinner channels").
    got, heights = _trimmed(p, shelves, heights, bus_far, work, got, west, close=True)
    # Then the same shelves with two supplied items on one line's lanes, where that takes
    # fewer lines (_shared): kept only if smaller ("Two items on one belt").
    shared = _shared(p)
    if shared and not work.over():
        try:
            lanes = _attempt(shared, shelves, heights, bus_far, work, west, close=True)
            lanes = _trimmed(shared, shelves, heights, bus_far, work, lanes, west, close=True)[0]
            got = lanes if lanes.area < got.area else got
        except Blocked:
            pass
    return got


def _shared(p):
    """p with two supplied items on one line, one a lane, or None where that takes no
    fewer lines: the ports taking one of them on each lane (circuit cells: copper on the
    left, iron on the right), as many to a line as each lane carries, and the rest of each
    item's ports on lines of their own, as factory shares a supplied item's. p's own lines
    are left as they are."""
    restore(p)
    lane = rates.lane_rate(p["belt"])
    cap = 2 * lane
    raw = {id(ln) for named in p["lines"].values() for ln in named if ln.supplied and not ln.fluid}
    ports = [q for r in p["rows"] for q in r.ports if q.kind == "in"]
    groups = {}  # (left item, right item) -> [[port, ...] a line]
    for q in ports:
        a, b = q.lanes.get("left"), q.lanes.get("right")
        if len(q.lanes) != 2 or a == b or not all(id(ln) in raw for ln in q.feeds.values()):
            continue
        if max(q.demand[a], q.demand[b]) > lane:
            continue
        lines = groups.setdefault((a, b), [])
        fits = [g for g in lines if sum(x.demand[a] for x in g) + q.demand[a] <= lane + 1e-9
                and sum(x.demand[b] for x in g) + q.demand[b] <= lane + 1e-9]
        if fits:
            fits[0].append(q)
        else:
            lines.append([q])
    candidates = [((a, b), g) for (a, b), lines in groups.items() for g in lines]

    def units(item, chosen):  # (port, lanes, demand) of the item's ports on lines of their own
        taken = {id(q) for _, g in chosen for q in g}
        return [(q, tuple(s for s, i in q.lanes.items() if i == item), q.demand.get(item, 0)) for q in ports
                if id(q) not in taken and item in q.lanes.values() and id(q.feeds[next(
                    s for s, i in q.lanes.items() if i == item)]) in raw]

    def own_lines(item, chosen):
        got = units(item, chosen)
        return factory._bins(got, [cap] * math.ceil(sum(d for *_, d in got) / cap - 1e-9)) if got else []

    items = sorted({i for pair, _ in candidates for i in pair})
    count = lambda chosen: len(chosen) + sum(len(own_lines(i, chosen)) for i in items)  # noqa: E731
    chosen = []
    for c in candidates:  # each as long as it takes no more lines
        if count(chosen + [c]) <= count(chosen):
            chosen.append(c)
    if not chosen or count(chosen) >= sum(len(p["lines"][i]) for i in items):
        return None
    shared = {**p, "lines": dict(p["lines"])}
    shared.pop("_planned", None)
    for item in items:
        shared["lines"][item] = []
        for group in own_lines(item, chosen):
            ln = factory.Line(item, False, True, demand=sum(d for *_, d in group))
            for q, sides, _ in group:
                for s in sides:
                    q.feeds[s] = ln
            shared["lines"][item].append(ln)
    for (a, b), g in chosen:
        da, db = sum(q.demand[a] for q in g), sum(q.demand[b] for q in g)
        ln = factory.Line(f"{a}+{b}", False, True, demand=da + db, lanes={"left": (a, da), "right": (b, db)})
        for q in g:
            q.feeds = {"left": ln, "right": ln}
        shared["lines"].setdefault(ln.name, []).append(ln)
    restore(shared)  # its ports' lines as planned here
    restore(p)
    return shared


def lower_bound(p) -> dict:
    """Tiles no layout of p's rows can be smaller than (docs/layout-optimizer.md, "How close
    to optimal"), leaving p's lines as they are:
    - rows: any layout of the rows at all. Their entities can't overlap, so the box holds at
      least their own tiles, and the widest and the tallest row (rectangle packing's area
      and size bounds).
    - shelves (wide by tall): any layout this search can make of them (build). For each way
      it shelves them, at least as wide as each shelf's rows and gaps east of the bus lines
      running beside them and their yard, and as tall as the input row, each shelf's tallest
      row and every channel at MIN_CHANNEL rows; the least of those. rows_wide and rows_tall
      are the rows' share of each: the rest is the bus and yard, and the channels."""
    _gated(p)
    rows = p["rows"]
    feeds = [(q, dict(q.feeds)) for r in rows for q in r.ports if q.kind == "in"]
    demands = [(ln, ln.demand) for named in p["lines"].values() for ln in named]
    restore(p)
    own = sum(len({(int(e.tile_position[0]) + dx, int(e.tile_position[1]) + dy) for e in r.bp.entities
                   for dx in range(e.tile_width) for dy in range(e.tile_height)}) for r in rows)
    tallest = [r.box[3] - r.box[1] + 1 for r in rows]
    supplied = any(ln.supplied for ln, _ in demands)
    best = None
    # With two supplied items on one line's lanes (build's last pass, _shared) the bus may be
    # narrower: the least of both.
    for lines in filter(None, (p, _shared(p))):
        restore(lines)
        for shelves in sorted({tuple(map(tuple, shelve(p, wide))) for wide in widths(rows)}):
            rows_wide = [sum(width(rows[k]) for k in shelf) + GAP * (len(shelf) - 1) for shelf in shelves]
            rows_tall = sum(max(tallest[k] for k in shelf) for shelf in shelves)
            # The input row and channel 0 hold the supplied lines' first tiles; above the last
            # shelf, the product leaves from the top channel's north row.
            tall = rows_tall + MIN_CHANNEL * len(shelves) + (1 + MIN_CHANNEL if supplied else 0)
            for bus_far in (False, True):
                raw, running, *_ = _bus(lines, shelves, bus_far)
                for west in (False, True):
                    _, x0s, _ = _columns(lines, shelves, running, len(raw), west)
                    wide = max(x + w for x, w in zip(x0s, rows_wide)) - (0 if raw else min(x0s))
                    if best is None or wide * tall < best["shelves"]:
                        best = {"shelves": wide * tall, "wide": wide, "tall": tall, "rows_wide": max(rows_wide),
                                "rows_tall": rows_tall}
    for q, f in feeds:
        q.feeds = f
    for ln, d in demands:
        ln.demand = d
    return {"rows": max(own, max(map(width, rows)) * max(tallest)), **best}


def exact(p, beat, work) -> tuple:
    """(layout or None, done): the smallest layout of p's rows under beat tiles of all build
    can make of them: every shelving it tries, either bus choice and shelf placement, every
    channel height, and its pieces (taps three rows apart or closer together, and closer
    with two supplied items on one line's lanes, _shared); None if none is under beat.
    Best first by the least box each can be (lower_bound's, with its heights), so it stops
    at the first whose least isn't under the smallest so far. done: every layout under the
    result was tried, so none of these rows on shelves is smaller, as far as the router
    routes (a layout it doesn't route within NEGOTIATION counts as none). Not done: out of
    work, more than TRIES layouts under beat, or no supplied item (channel 0's height then
    sets no least); it keeps the best found."""
    import heapq
    from itertools import combinations

    try:
        _gated(p)
        _refuse(p)
    except CompactError:
        return None, True  # no layout of these rows on shelves at all
    starts = _space(p)
    if starts is None or _count(starts, beat) > TRIES:
        return None, False

    def heights(n, extra):  # every way of n channels taking extra rows past MIN_CHANNEL, in order
        for bars in combinations(range(extra + n - 1), n - 1):
            edges = (-1, *bars, extra + n - 1)
            yield [MIN_CHANNEL + b - a - 1 for a, b in zip(edges, edges[1:])]

    best, least = None, beat
    queue = [(wide * tall, i, 0) for i, (wide, tall, *_) in enumerate(starts)]
    heapq.heapify(queue)
    while queue[0][0] < least:
        box, i, extra = heapq.heappop(queue)
        wide, tall, lines, close, shelves, bus_far, west = starts[i]
        for h in heights(len(shelves) + 1, extra):
            if box >= least:
                break
            work.spend(TRY)
            if work.over():
                restore(p)
                return best, False
            try:
                got = _attempt(lines, shelves, h, bus_far, work, west, close)
            except (Blocked, CompactError):
                continue
            if got.area < least:
                best, least = got, got.area
        heapq.heappush(queue, (wide * (tall + extra + 1), i, extra + 1))
    restore(p)
    return best, True


def _space(p):
    """exact's starts, one per way to lay out the rows but their channel heights: (wide,
    tall with every channel at MIN_CHANNEL, lines, close, shelves, bus_far, west), the least
    box first; one only once, though two choices lay out the same (bus_far with no line
    used far off, west with no shelf moving). None with no supplied item."""
    rows = p["rows"]
    tallest = [r.box[3] - r.box[1] + 1 for r in rows]
    shelvings = sorted({tuple(map(tuple, shelve(p, wide))) for wide in widths(rows)})
    starts, seen = [], set()
    for lines, close in [(p, False), (p, True)] + [(q, True) for q in filter(None, [_shared(p)])]:
        restore(lines)
        if not any(ln.supplied and not ln.fluid for named in lines["lines"].values() for ln in named):
            restore(p)
            return None
        for shelves in shelvings:
            rows_wide = [sum(width(rows[k]) for k in shelf) + GAP * (len(shelf) - 1) for shelf in shelves]
            tall = 1 + sum(max(tallest[k] for k in shelf) for shelf in shelves) + MIN_CHANNEL * (len(shelves) + 1)
            for bus_far in (False, True):
                raw, running, *_ = _bus(lines, shelves, bus_far)
                for west in (False, True):
                    _, x0s, _ = _columns(lines, shelves, running, len(raw), west)
                    same = (id(lines), close, shelves, tuple(map(id, raw)), tuple(x0s))
                    if same not in seen:
                        seen.add(same)
                        wide = max(x + w for x, w in zip(x0s, rows_wide)) - (0 if raw else min(x0s))
                        starts.append((wide, tall, lines, close, [list(s) for s in shelves], bus_far, west))
    restore(p)
    return sorted(starts, key=lambda s: s[0] * s[1])


def _count(starts, beat) -> int:
    """Layouts of starts whose least box is under beat (past TRIES, a little more)."""
    tries = 0
    for wide, tall, _, _, shelves, *_ in starts:
        n, extra = len(shelves) + 1, 0
        while wide * (tall + extra) < beat and tries <= TRIES:
            tries += math.comb(extra + n - 1, n - 1)
            extra += 1
    return tries


def _trimmed(p, shelves, heights, bus_far, work, got, west=False, close=False):
    """(layout, heights): each channel a row thinner at a time while it still routes."""
    for c in range(len(heights)):
        while heights[c] > MIN_CHANNEL and not work.over():
            trial = heights[:c] + [heights[c] - 1] + heights[c + 1:]
            try:
                smaller = _attempt(p, shelves, trial, bus_far, work, west, close)
            except Blocked:
                break
            heights = trial
            got = min(got, smaller, key=lambda g: g.area)
    return got, heights


def _fitted(p, shelves, bus_far, beat=math.inf, work=None):
    """(layout, channel heights): channels grown together until it routes, then each
    cut to the rows it used; None if it never routes in less room than beat, or in work."""
    for extra in range(0, MAX_EXTRA + 1, 3):
        heights = [MIN_CHANNEL + extra] * (len(shelves) + 1)
        if least_area(p, shelves, heights) >= beat or (work and work.over()):
            return None
        try:
            got = _attempt(p, shelves, heights, bus_far, work)
            break
        except Blocked:
            continue
    else:
        return None
    fitted = [max(MIN_CHANNEL, used) for used in got.channels_used]
    if fitted != heights:
        try:
            return _attempt(p, shelves, fitted, bus_far, work), fitted
        except Blocked:
            pass
    return got, heights


def _gated(p):
    """Each cracking block's oil through a gate, as on the bus: a storage tank on the oil
    line and a pump below it into the block's outermost line (the oil's), wired to run
    while the tank holds more than factory.GATE_LEVEL, so the oil's other users come
    first. The gate stands north of the block, in its row; the row's oil port is the
    tank's north-west corner, fed from the west.

        t-5   T T T       T: tank (3x3), NW corner fed from the west,
        t-4   T T T          SE corner down into the pump
        t-3   T T T
        t-2       P o     P: pump facing south; o: a pole by it
        t-1       P
        t     ═══════     the block's outermost line (its port at the west end)
    """
    from generators.production import CRACKING
    from tools.settings import game_settings

    if p.get("_gated"):
        return
    p["_gated"] = True
    for k, r in enumerate(p["rows"]):
        if r.step.item not in CRACKING:
            continue
        oil = next((q for q in r.ports if q.kind == "fluid_in" and q.name != "water"), None)
        if oil is None or oil.y != r.box[1] or oil.x != r.box[0]:
            raise CompactError(f"{r.step.item}: the cracked oil's line isn't the block's outermost")
        x, t = oil.x, oil.y
        bp = deepcopy(r.bp)
        bp.entities.append("storage-tank", tile_position=(x, t - 5), id="gate tank")
        bp.entities.append("pump", tile_position=(x + 2, t - 2), direction=S, id="gate pump")
        pump = bp.entities["gate pump"]
        pump.set_circuit_condition(oil.name, ">", factory.GATE_LEVEL)
        game_settings(pump, {"circuit_enabled": True})
        bp.entities.append(factory.POLE, tile_position=(x + 3, t - 2))
        ports = [replace(q, x=x, y=t - 5) if q is oil else q for q in r.ports]
        p["rows"][k] = factory.Row(r.step, bp, r.flow, ports, (r.box[0], t - 5, max(r.box[2], x + 3), r.box[3]),
                                   gate=oil.name, wires=[("red", "gate tank", "gate pump")])


def _refuse(p):
    for r in p["rows"]:
        x0, y0, x1, y1 = r.box
        for q in r.ports:
            if q.kind.startswith("fluid"):
                if not ((q.side == "south" and q.y == y1) or (q.side == "west" and q.x == x0)):
                    raise CompactError(f"{r.step.item}: a fluid port off the row's south or west edge")
            elif (q.kind == "in" and q.y != y1) or (q.kind == "out" and q.y != y0):
                raise CompactError(f"{r.step.item}: a port off the row's south or north edge")


def _attempt(p, shelves, heights, bus_far=True, work=None, west=False, close=False) -> Result:
    """One layout of these shelves and channel heights, or Blocked. west: every shelf as
    far west as the lines running beside it let it, not only as far as the box needs.
    close: taps as close together as the bus lets them (belts.Taps)."""
    rows, belt = p["rows"], p["belt"]
    restore(p)
    underground, splitter = factory.KITS[belt]
    reach = entities.raw[underground]["max_distance"]
    lines = [ln for named in p["lines"].values() for ln in named]
    raw, running, tapped, source = _bus(p, shelves, bus_far)
    yard, x0s, _ = _columns(p, shelves, running, len(raw), west)
    canvas = factory.Canvas()
    claimed = {}  # goal tiles: (x, y) -> the connection allowed onto it
    conns = []
    taps = belts.Taps(close=close)

    def free(x, y):
        return canvas.free(x, y) and (x, y) not in claimed

    # Rows on shelves, south to north, each shelf's channel below it (its ports'
    # adapters, the taps into it, the chains out of the shelf below).
    where, shelf_box, y_out = place(rows, shelves, heights, x0s, canvas)
    x_max = max(x for x, _ in canvas.tiles) + 2

    def at(k, q):
        dx, dy = where[k]
        return q.x + dx, q.y + dy

    localize(rows, lines, raw, at)
    # The tile beside each fluid port (south of it, or west) is its pipe's, kept from the belts.
    for k, r in enumerate(rows):
        for q in r.ports:
            if q.kind.startswith("fluid"):
                (ax, ay), _ = _approach(at(k, q), q.side)
                if not canvas.free(ax, ay) or claimed.get((ax, ay), f"fluid {q.name}") != f"fluid {q.name}":
                    raise Blocked(f"{q.name}: no room beside its port")
                claimed[(ax, ay)] = f"fluid {q.name}"

    def channel_rows(s):
        """Rows of the channel below shelf s, south to north (the top channel for s = len)."""
        south = -1 if s == 0 else shelf_box[s - 1][1] - 1
        north = shelf_box[s][0] + 1 if s < len(shelves) else y_out
        return list(range(south, north - 1, -1))

    # Routes, merges and splits keep east of the lines still running at each row: a band is a
    # channel and the shelf above it.
    band = {0: 0}
    for s in range(len(shelves) + 1):
        band.update({y: s for y in channel_rows(s)})
        if s < len(shelves):
            band.update({y: s for y in range(shelf_box[s][1], shelf_box[s][0] + 1)})

    # Adapters: under each port, a belt fed from below (one line) or from both sides (two).
    # Two ports side by side taking two lines each (a row on both sides of its machines)
    # can't both: the outer one turns in from beside the row, placed first.
    goals = {}  # id(line) -> [(x, y, facing, owner)]
    for s, shelf in enumerate(shelves):
        ports = [(k, q) for k in shelf for q in rows[k].ports if q.kind == "in"]
        turns = {id(q): _turn(rows[k], q) for k, q in ports}
        ports.sort(key=lambda kq: (turns[id(kq[1])] is None, _lines(kq[1]) > 1, at(*kq)[0]))
        south = channel_rows(s)[0]
        for k, q in ports:
            owner = f"row {k} port {q.x}"
            for ln, goal in belts.adapter(canvas, free, *at(k, q), south, q.feeds, belt, claimed, owner,
                                          turn=turns[id(q)]):
                goals.setdefault(id(ln), []).append(goal)

    # Made items: out of each row's top, merged into their lines, split to their users.
    handles = {}  # id(line) -> [(x, y)] heading north
    leftovers = []
    for k, r in enumerate(rows):
        for q in r.ports:
            if q.kind != "out":
                continue
            xo, yo = at(k, q)
            if q.filter:  # the belt also carries an ingredient's leftovers: kept in the row
                if not all(free(*t) for t in ((xo, yo - 1), (xo + 1, yo - 1), (xo + 1, yo), (xo + 1, yo - 2))):
                    raise Blocked("no room for a filter splitter on a row's output")
                canvas.put(splitter, xo, yo - 1, f"row {k} filter", width=2, direction=N, filter=q.name,
                           output_priority="left")
                canvas.reserve(xo + 1, yo, f"row {k} filter")  # nothing into its other input,
                canvas.reserve(xo + 1, yo - 2, f"row {k} filter")  # and nothing out of its other output
                leftovers += [((xo, yo), i) for p in r.ports if p.kind == "in" and len(p.lanes) == 1
                              for i in p.lanes.values()]
                handles.setdefault(id(q.outs[0]), []).append((xo, yo - 2))
            elif len(q.outs) == 2:  # one row feeding two lines: split as it leaves
                if not (free(xo, yo - 1) and free(xo + 1, yo - 1)):
                    raise Blocked("no room to split a row's output")
                canvas.put(splitter, xo, yo - 1, f"row {k} split", width=2, direction=N)
                handles.setdefault(id(q.outs[0]), []).append((xo, yo - 2))
                handles.setdefault(id(q.outs[1]), []).append((xo + 1, yo - 2))
            else:
                handles.setdefault(id(q.outs[0]), []).append((xo, yo - 1))
    # A row's lead-out, and the tile north of it (its way out), are its line's.
    held = {(x, y - dy): ln_id for ln_id, hs in handles.items() for x, y in hs for dy in (0, 1)}

    def free_for(ln):
        return lambda x, y: free(x, y) and held.get((x, y), id(ln)) == id(ln) and x >= yard[band.get(y, 0)][0]

    outlets = []
    bus_entry = {}  # id(line) -> its merged rows' handle, for lines made here that ride the bus
    for ln in lines:
        if ln.supplied or ln.fluid:
            continue
        # Merged at the northmost: the others flow north into it, on toward the users and the top.
        hs = sorted(handles.get(id(ln), []), key=lambda h: (h[1], h[0]))
        if not hs:
            raise CompactError(f"{ln.name}: a line with no rows making it")
        for m, (hx, hy) in enumerate(hs[1:], 1):  # the others' lead-outs, and their ways out, are their routes'
            for tt in ((hx, hy), (hx, hy - 1)):
                if canvas.free(*tt):
                    claimed[tt] = f"merge {ln.name} {m}"
        trunk = belts.merge(canvas, free_for(ln), hs, belt, splitter, claimed, conns, ln.name)
        if ln in raw:  # used far off: onto the bus, in the channel above its rows
            bus_entry[id(ln)] = trunk
            continue
        if ln.top == "product":
            column = next((x for d in range(x_max) for x in (trunk[0] + d, trunk[0] - d)
                           if yard[-1][0] <= x <= x_max and free(x, y_out)), None)
            if column is None:
                raise Blocked("no room for an outlet")
            goal = (column, y_out)
            ident = f"out {ln.name} {len(outlets)}"
            claimed[goal] = ident
            outlets.append((goal, ident, ln.name))
            conns.append(Conn(trunk, N, goal, N, ident))
            continue
        belts.split(canvas, free_for(ln), trunk, goals.get(id(ln), []), belt, splitter, conns, ln.name)

    for cn in conns:  # routes' first tiles, kept from the taps below
        claimed.setdefault(cn.start, cn.owner)
    names = {id(ln): ln.name for ln in lines}
    for ln_id, trunk in bus_entry.items():  # where a line made here sets off for the bus
        claimed.setdefault(trunk, f"onto bus {names[ln_id]}")
    for h in held:
        if canvas.free(*h):
            claimed.setdefault(h, f"lead-out {h}")

    # Raw items: a bus on the west, tapped in each channel whose shelf uses them.
    inputs = []
    bus = []
    for i, ln in enumerate(raw):
        c = 2 * i
        shelves_used = tapped(ln)
        if ln.lanes:  # its first belt fed from both sides, the left item from the west (NOTES.md);
            y0 = 0 if canvas.free(c - 1, 0) else 1  # a row further south beside another such line's
            events = {y0}
            canvas.put(belt, c, y0, "input", direction=N)
            for dx, side in ((-1, "left"), (1, "right")):
                item, ident = ln.lanes[side][0], f"in {ln.lanes[side][0]} {i}"
                canvas.put(belt, c + dx, y0, "input", direction=E if dx < 0 else W, id=ident)
                inputs.append((ident, item))
        elif ln.supplied:
            events = {0}
            ident = f"in {ln.name} {i}"
            canvas.put(belt, c, 0, "input", direction=N, id=ident)
            inputs.append((ident, ln.name))
        else:  # made here: from its rows west onto the bus, in the channel above them
            src = source(ln)
            ye = belts.entry(canvas, free, taps, channel_rows(src + 1), c, *yard[src + 1], bus_entry[id(ln)], claimed,
                             conns, belt, ln.name)
            events = {ye}
        for n, s in enumerate(shelves_used):
            last = n == len(shelves_used) - 1
            # The channel's goals, and those beside its shelf's ports (a turned adapter's).
            mine = [g for g in goals.get(id(ln), []) if channel_rows(s)[0] >= g[1] >= channel_rows(s)[-1] - 1]
            yb = belts.tap(canvas, free, taps, channel_rows(s), c, *yard[s], last, mine, belt, splitter, conns, ln.name)
            events.add(yb if last else yb + 1)
            for cn in conns if close else ():  # its comb's branches' first tiles, kept from the taps after it
                claimed.setdefault(cn.start, cn.owner)
        bus.append(_BusLine(ln.name, c, events))
    tunnels = []
    for cn in conns:  # a route's first tile is its own, as its last is
        if claimed.setdefault(cn.start, cn.owner) != cn.owner:
            raise Blocked(f"{cn.owner}: starts on {claimed[cn.start]}'s tile")
    bounds = (min(x for x, _ in yard), y_out, x_max, -1)
    fluid_inputs, fluid_outputs, fluid_ports = _pipe_fluids(p, rows, lines, where, at, canvas, claimed, yard, band,
                                                            bounds)
    spec = [(cn.start, cn.heading, cn.goal, cn.facing, lambda x, y, cn=cn: claimed.get((x, y), cn.owner) == cn.owner)
            for cn in conns]
    paths = route_all(spec, lambda x, y: canvas.free(x, y) and x >= yard[band.get(y, 0)][0], reach, bounds,
                      work=(work or Work()).part(NEGOTIATION))
    if paths is None:
        raise Blocked("the belts don't route without sharing a tile")
    for cn, path in zip(conns, paths):
        for kind, x, y, d in path:
            if kind == "belt":
                canvas.put(belt, x, y, cn.owner, direction=d)
            else:
                canvas.put(underground, x, y, cn.owner, direction=d, io_type="input" if kind == "in" else "output")
        tunnels += tunnels_of(path)
    for line in bus:
        try:
            factory._fill_belt(canvas, line, belt, underground)
        except ValueError as e:
            raise Blocked(str(e)) from None
    for (gx, gy), ident, name in outlets:
        for n, (item, tile, kw) in enumerate(canvas.items):
            if tile == (gx, gy):
                canvas.items[n] = (item, tile, {**kw, "id": ident})
    try:
        poles = factory._join_poles(canvas, rows)
    except ValueError as e:
        raise Blocked(str(e)) from None

    label, description = f"Factory: {p['rate']:g} {p['item']}/s, compact", _description(p, raw, len(outlets))

    def blueprint():  # built only for the layout kept: most of an attempt's time went to it
        bp = Blueprint()
        bp.label, bp.description, bp.icons = label, description, [p["item"]]
        for k, r in enumerate(rows):
            dx, dy = where[k]
            for e in r.bp.entities:
                copy = deepcopy(e)
                copy.tile_position = (int(e.tile_position[0]) + dx, int(e.tile_position[1]) + dy)
                copy.id = f"row {k} {e.id}" if e.id else None
                bp.entities.append(copy, copy=False)
        for name, tile, kw in canvas.items:
            bp.entities.append(name, tile_position=tile, **kw)
        for k, r in enumerate(rows):
            for color, a, b in r.wires:
                bp.add_circuit_connection(color, f"row {k} {a}", f"row {k} {b}")
        for tile in poles:
            bp.entities.append(factory.POLE, tile_position=tile)
        bp.generate_power_connections()
        return bp

    if fluid_ports:
        _check_fluids(_pipes(p, rows, where, canvas.items), fluid_ports)
    tiles = list(canvas.tiles) + list(poles)
    xs, ys = [x for x, _ in tiles], [y for _, y in tiles]
    area = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
    used = []
    for s in range(len(shelves) + 1):
        span = channel_rows(s)
        taken = {y for (x, y), owner in canvas.tiles.items()
                 if y in span and x >= yard[s][0] and not str(owner).startswith("row")}
        # Rows from the channel's north end down to the southmost one used: what's north of it can't go.
        used.append(span[0] - min(span) + 1 if not taken else max(taken) - min(span) + 1 if s < len(shelves) else
                    span[0] - min(taken) + 1)
    ports = [("in", at(k, q), set(q.lanes.values())) if q.kind == "in" else ("out", at(k, q), q.name)
             for k, r in enumerate(rows) for q in r.ports if q.kind in ("in", "out")]
    return Result(blueprint, area, inputs, [(ident, name) for _, ident, name in outlets], max(ys) - min(ys) + 1, used,
                  ports, [(line.name, line.column, min(line.events)) for line in bus],
                  [(x0s[s], *shelf_box[s]) for s in range(len(shelves))], leftovers, fluid_inputs, fluid_ports,
                  fluid_outputs)


def _bus(p, shelves, bus_far):
    """(raw, running, tapped, source): the lines on the west bus, those reaching furthest
    north first (a ragged bus: each line ends at its last tap, and lines reaching further
    north sit further west, so a shelf above a line's last tap starts in its columns); per
    channel, the lines still running in it; the shelves a line taps into; a made line's
    shelf. bus_far: lines made here and used far from their rows ride the bus too."""
    rows = p["rows"]
    lines = [ln for named in p["lines"].values() for ln in named]
    shelf_of = {k: s for s, shelf in enumerate(shelves) for k in shelf}
    raw = [ln for ln in lines if ln.supplied and not ln.fluid]
    raw += [ln for ln in lines if bus_far and not ln.supplied and not ln.fluid and not ln.top
            and far(ln, rows, shelf_of)]
    sinks_in = [{id(ln) for k in shelf for q in rows[k].ports if q.kind == "in" for ln in q.feeds.values()}
                for shelf in shelves]

    def source(ln):
        return max(shelf_of[k] for k, r in enumerate(rows) for q in r.ports if q.kind == "out" and ln in q.outs)

    def tapped(ln):
        got = [s for s in range(len(shelves)) if id(ln) in sinks_in[s]]
        return got if ln.supplied else [s for s in got if s > source(ln)]

    last_channel = {id(ln): max(tapped(ln), default=0) for ln in raw}
    raw.sort(key=lambda ln: -last_channel[id(ln)])
    running = [sum(1 for ln in raw if last_channel[id(ln)] >= c) for c in range(len(shelves) + 1)]
    return raw, running, tapped, source


def _columns(p, shelves, running, n_raw, west):
    """(yard, x0s, span): each channel's yard, the two columns east of the lines still running
    in it; each shelf's west column; each shelf's columns (its rows, the gaps between them and
    any room west of its first row). A shelf moves west only as far as the box needs: moved
    further, it just stands off from the shelves it takes from (west: as far as it can)."""
    rows = p["rows"]
    yard = [(2 * n, 2 * n + 1) for n in running]
    room = [FLUID_ROOM if _hemmed(rows[shelf[0]]) else 0 for shelf in shelves]
    span = [room[s] + sum(width(rows[k]) for k in shelf) + GAP * (len(shelf) - 1) for s, shelf in enumerate(shelves)]
    edge = max(yard[s][1] + 1 + span[s] for s in range(len(shelves)))
    x0s = [room[s] + max(yard[s][1] + 1, min(yard[s][1] + 1 if west else 2 * n_raw + 2, edge - span[s]))
           for s in range(len(shelves))]
    return yard, x0s, span


def _lines(q) -> int:
    """The lines feeding an input port."""
    return len(set(map(id, q.feeds.values())))


def _turn(r, q):
    """W or E for a port taking two lines at a corner of its row, beside another that does:
    it turns in from beside the row (belts.adapter), or None."""
    if _lines(q) < 2:
        return None
    beside = {p.x for p in r.ports if p.kind == "in" and p is not q and p.y == q.y and _lines(p) > 1}
    if q.x == r.box[0] and q.x + 1 in beside:
        return W
    if q.x == r.box[2] and q.x - 1 in beside:
        return E
    return None


def _hemmed(r) -> bool:
    """Has the row a west fluid port with others two tiles above and below it?"""
    west = {q.y for q in r.ports if q.kind.startswith("fluid") and q.side == "west"}
    return any(y - 2 in west and y + 2 in west for y in west)


def _approach(tile, side):
    """The tile beside a fluid port its pipe joins it from, and the heading away from it."""
    x, y = tile
    return ((x, y + 1), S) if side == "south" else ((x - 1, y), W)


def _pipe_fluids(p, rows, lines, where, at, canvas, claimed, yard, band, bounds):
    """Each fluid line piped between its rows' ports (south ends of fluid rows, west ends of
    blocks' lines): a supplied fluid in at the bottom, a fluid product or left-over out at
    the top, each through a pipe of its own (two columns from any other, so the players'
    pipes beyond don't join). Returns (inputs as (id, fluid), outputs as (id, fluid, top),
    fluid tiles as (tile, fluid): the inputs, outputs and ports)."""
    fluid_lines = [ln for ln in lines if ln.fluid]
    if not fluid_lines:
        return [], [], []
    fluids = Fluids(_row_fluids(p, rows, where))
    fluids.spans += [span for k in range(len(rows)) for span in _row_spans(p, rows[k], where[k])]
    boxes = {(x + where[k][0], y + where[k][1]) for k, r in enumerate(rows)
             for x in range(r.box[0], r.box[2] + 1) for y in range(r.box[1], r.box[3] + 1)}
    for k, r in enumerate(rows):  # every port's way in, its line's from the start
        for q in r.ports:
            if q.kind.startswith("fluid"):
                fluids.keep(q.name, _approach(at(k, q), q.side)[0])
    x_lo, y_top, x_hi, _ = bounds
    inputs, outputs, ports, columns = [], [], [], {0: [], y_top: []}
    for ln in fluid_lines:
        owner = f"fluid {ln.name}"

        def ok(x, y, owner=owner):
            return (canvas.free(x, y) and claimed.get((x, y), owner) == owner and (x, y) not in boxes
                    and x >= yard[band.get(y, 0)][0])

        terminals = []
        for k, r in enumerate(rows):
            for q in r.ports:
                if q.kind.startswith("fluid") and q.lines.get(q.name) is ln:
                    approach = _approach(at(k, q), q.side)
                    piped = (q.x, q.y) in {t for t, *_ in _row_local(p, r)}
                    # A gate's tank isn't a pipe: nothing else joins the line through it, and
                    # the line is checked at the pipe joining it.
                    terminals.insert(len(terminals) if not piped else 0, ({at(k, q)} if piped else set(), approach))
                    ports.append((at(k, q) if piped else approach[0], ln.name))
        target = sum(t[1][0][0] for t in terminals) / len(terminals)

        def end(y, inward, ident_of):
            """A pipe of the line's own at row y, the player's joining it from beyond."""
            def clear(t):  # no piece there, kept for another line, or joining another fluid
                return (t not in fluids.at and fluids.kept.get(t, ln.name) == ln.name
                        and not fluids.foreign(ln.name, t, ALL))

            column = min((x for x in range(yard[0][1] + 1, x_hi + 1)
                          if all(abs(x - c) > 1 for c in columns[y]) and ok(x, y) and ok(x, y + inward)
                          and clear((x, y)) and clear((x, y + inward))),
                         key=lambda x: abs(x - target), default=None)
            if column is None:
                raise Blocked(f"{ln.name}: no room for its pipe {'in' if y == 0 else 'out'}")
            columns[y].append(column)
            ident = ident_of(column)
            canvas.put("pipe", column, y, owner, id=ident)
            fluids.fix(ln.name, (column, y), "pipe")
            fluids.at[(column, y - inward)] = (ln.name, sides("pipe", None))
            ports.append(((column, y), ln.name))
            return ident, ({(column, y)}, ((column, y + inward), N if inward < 0 else S))

        if ln.supplied:  # in at the bottom, first: the tree the rows join
            ident, terminal = end(0, -1, lambda c: f"in {ln.name} {c}")
            inputs.append((ident, ln.name))
            terminals.insert(0, terminal)
        if ln.top:  # a fluid product or left-over, out at the top
            ident, terminal = end(y_top, 1, lambda c: f"out {ln.name} {c}")
            outputs.append((ident, ln.name, ln.top))
            terminals.append(terminal)
        try:
            route_line(fluids, ln.name, terminals, ok, (x_lo, y_top, x_hi, 0))
        except ValueError as e:
            raise Blocked(str(e)) from None
    for (x, y), (name, d) in fluids.placed.items():
        if canvas.free(x, y):
            canvas.put(name, x, y, f"pipe {x} {y}", **({"direction": d} if d is not None else {}))
    return inputs, outputs, ports


def _row_local(p, r):
    """A row's pipes in its own coordinates: [(tile, name, direction, fluid)], the fluid
    its port names for the pipes that port joins, None for any others."""
    cache = p.setdefault("_row_pipes", {})
    if id(r) not in cache:
        nets = pipes.networks(r.bp.to_dict()["blueprint"])
        named = {nets[(q.x, q.y)]: q.name for q in r.ports if q.kind.startswith("fluid") and (q.x, q.y) in nets}
        cache[id(r)] = [((x, y), e.name, getattr(e, "direction", None), named.get(nets.get((x, y))))
                        for e in r.bp.entities if e.name in ("pipe", "pipe-to-ground")
                        for x, y in [(int(e.tile_position[0]), int(e.tile_position[1]))]]
    return cache[id(r)]


def _row_fluids(p, rows, where) -> dict:
    """Tile -> (fluid, sides it connects toward) for every row's pipes; pipes no port names
    get a fluid of their own row's, which nothing routed may join (behind a gate's pump,
    the oil the pump lets in). A gate's tank connects at its north-west corner, north and
    west, and at its south-east corner, east and south (as on the bus)."""
    got = {}
    for k, r in enumerate(rows):
        dx, dy = where[k]
        for (x, y), name, d, fluid in _row_local(p, r):
            got[(x + dx, y + dy)] = (fluid or f"row {k}", sides(name, d))
        for e in r.bp.entities:
            if e.name == "storage-tank":
                x, y = int(e.tile_position[0]) + dx, int(e.tile_position[1]) + dy
                got[(x, y)] = (r.gate, {(0, -1), (-1, 0)})
                got[(x + 2, y + 2)] = (r.gate, {(1, 0), (0, 1)})
    return got


def _row_spans(p, r, at) -> list:
    """A row's pipe-to-ground ends as spans on both their lines: no routed pair may
    reach past one, or the game would pair it with it."""
    dx, dy = at
    return [span for (x, y), name, _, _ in _row_local(p, r) if name == "pipe-to-ground"
            for span in (("x", x + dx, y + dy, y + dy), ("y", y + dy, x + dx, x + dx))]


def _pipes(p, rows, where, items) -> dict:
    """The pipes of a layout, rows' and routed, as blueprint data: all tools/pipes reads."""
    got = [((x + where[k][0], y + where[k][1]), name, d) for k, r in enumerate(rows)
           for (x, y), name, d, _ in _row_local(p, r)]
    got += [(tile, name, kw.get("direction")) for name, tile, kw in items if name in ("pipe", "pipe-to-ground")]
    return {"entities": [{"name": name, "position": {"x": x + 0.5, "y": y + 0.5}, "direction": int(d or 0)}
                         for (x, y), name, d in got]}


def _check_fluids(data, ports):
    """Each fluid's tiles one network, no network two fluids', and none longer than the game
    lets a network be (factory.PIPELINE_EXTENT, less the players' pipes at its ends)."""
    nets = pipes.networks(data)
    of_fluid, of_net = {}, {}
    for tile, fluid in ports:
        if tile not in nets:
            raise Blocked(f"{fluid}: a port its pipes don't reach")
        of_fluid.setdefault(fluid, set()).add(nets[tile])
        of_net.setdefault(nets[tile], set()).add(fluid)
    if any(len(v) > 1 for v in of_fluid.values()) or any(len(v) > 1 for v in of_net.values()):
        raise Blocked("a fluid's pipes aren't one network of their own")
    if max(pipes.extents(data).values(), default=0) > factory.PIPELINE_EXTENT - 2:
        raise Blocked(f"a pipe network longer than the game's {factory.PIPELINE_EXTENT} tiles")


@dataclass
class _BusLine:
    """A raw line on the bus, as factory._fill_belt fills it."""
    name: str
    column: int
    events: set


def _description(p, raw, outlets) -> str:
    """raw: the bus's lines, west to east."""
    lines = [ln for named in p["lines"].values() for ln in named]
    supplied = [", ".join(f"{i} {d:.3g}/s" for i, d in ln.lanes.values()) if ln.lanes else
                f"{ln.name} {ln.demand:.3g}/s" for ln in raw]
    piped = [f"{ln.name} {ln.demand:.3g}/s" for ln in lines if ln.fluid and ln.supplied]
    left = [ln.name for ln in lines if ln.fluid and ln.top == "surplus"]
    gates = (" Cracking takes oil only while the tank by its pump holds over %d." % factory.GATE_LEVEL
             if any(r.gate for r in p["rows"]) else "")
    text = (f"{len(p['rows'])} rows on shelves, products handed over directly. Inputs enter at the bottom of the "
            f"west bus, west to east: {', '.join(supplied) or 'none'}"
            f"{'; by pipe: ' + ', '.join(piped) if piped else ''}. The product leaves at the top"
            f"{' on ' + str(outlets) + ' belts' if outlets > 1 else ''}"
            f"{', with ' + ', '.join(left) + ' left over (use or store it, or refining stops)' if left else ''}."
            f"{gates} Connect any pole to your grid.")
    if len(text.encode()) > factory.TEXT_LIMITS["description"]:
        text = (f"{len(p['rows'])} rows on shelves. Inputs ({len(raw) + len(piped)} lines) enter at the bottom, "
                f"the product leaves at the top. Connect any pole to your grid.")
    return text


def flow_tests(p, result):
    """Feed the bus inputs at the bottom, drain the top: the whole factory at its rate."""
    from validator.flow import Feed, Flow, FluidFeed

    from tools import rates

    feeds = [Feed(ident, lane, item) for ident, item in result.inputs for lane in ("left", "right")]
    fluids = [FluidFeed(ident, fluid, side="south") for ident, fluid in result.fluid_inputs]
    fluids += [FluidFeed(ident, fluid, side="north", sink=True, level=factory.SURPLUS_LEVEL if top == "surplus" else 0)
               for ident, fluid, top in result.fluid_outputs]
    drains = [ident for ident, item in result.outputs if item == p["item"]]
    warmup = sum(max(r.flow.warmup for r in p["rows"] if r.step is s) for s in p["steps"])
    warmup += rates.travel_seconds(p["belt"], result.height)
    window = max(r.flow.window for r in p["rows"])
    return [Flow(f"compact factory of {len(p['rows'])} rows: {p['rate']:g} {p['item']}/s", feeds, drains,
                 {p["item"]: float(p["rate"])}, warmup=warmup, window=window, fluids=fluids, at_least=True,
                 settle=factory.SETTLE)]
