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
- A supplied fluid enters at the bottom through a pipe of its own, and reaches the
  rows that take it (fluid rows, at their south end) through pipes and
  pipe-to-ground pairs that touch no other fluid (tools/pipe_route.py), routed
  before the belts, which go round or under them.

Fluid blocks (oil processing, cracking, sulfur, acid, lubricant) and fluids made
here aren't laid out compact yet, and `factory` falls back to the bus layout for them.
"""

import math
import time
from copy import deepcopy
from dataclasses import dataclass, field

from draftsman.blueprintable import Blueprint
from draftsman.data import entities

from generators import factory
from tools import belts, pipes
from tools.belts import Blocked, Conn
from tools.pipe_route import Fluids, route_line, sides
from tools.route import N, S, route_all, tunnels_of
from tools.shelves import GAP, MIN_CHANNEL, far, least_area, localize, place, restore, shelve, width, widths

MAX_EXTRA = 14  # rows added to every channel before a width is given up
# Seconds a search may take by default: every request measured reached its best layout
# within it (the slowest, electronic circuits at 15/s, at 30.7 s on the machine measured;
# docs/layout-optimizer.md), with room for a slower one. A search ends when it's done, so
# small factories don't wait for it.
BUDGET = 40.0
# A negotiation that routes took 0.1 to 1 s on every request measured; the ones that
# never route ran to 15 to 38 s first. Cut there.
NEGOTIATION = 4.0


class CompactError(ValueError):
    """These rows can't be laid out compact (the bus layout still can)."""


@dataclass
class Result:
    bp: Blueprint
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
    fluid_ports: list = field(default_factory=list)  # (tile, fluid): row fluid ports and inputs


def build(p, budget=BUDGET) -> Result:
    """The smallest compact layout of the rows in p (factory.rows_and_lines) that routes
    within budget seconds. For each shelf width, and each way of carrying items used
    far from their rows (routed straight from them, or on the bus): every channel grown
    until it routes, then cut to the rows it used. The best of those then loses a
    channel row at a time while it still routes and gets no bigger. Shelves are tried
    least room first, and a channel height whose rows and gaps alone already take more
    room than the best so far isn't tried. Last, it tries every shelf as far west as the
    bus lets it (_attempt's west), and keeps that if smaller. Out of time, it keeps the
    best so far."""
    _refuse(p)
    deadline = time.monotonic() + budget
    candidates = {tuple(map(tuple, shelve(p, wide))) for wide in widths(p["rows"])}
    best = None
    for shelves in sorted(candidates, key=lambda s: least_area(p, s)):
        for bus_far in (False, True):
            if time.monotonic() > deadline:
                break
            got = _fitted(p, shelves, bus_far, best[0].area if best else math.inf, deadline)
            if got and (best is None or got[0].area < best[0].area):
                best = got + (shelves, bus_far)
    if best is None:
        if time.monotonic() > deadline:
            raise CompactError(f"no layout routed within its budget of {budget:g} s")
        raise CompactError("the rows don't route in any shelf width tried")
    got, heights, shelves, bus_far = best
    got, heights = _trimmed(p, shelves, heights, bus_far, deadline, got)
    if time.monotonic() <= deadline:
        try:
            west = _attempt(p, shelves, heights, bus_far, deadline, west=True)
        except Blocked:
            return got
        west, _ = _trimmed(p, shelves, heights, bus_far, deadline, west, west=True)
        got = min(got, west, key=lambda g: g.area)
    return got


def _trimmed(p, shelves, heights, bus_far, deadline, got, west=False):
    """(layout, heights): each channel a row thinner at a time while it still routes."""
    for c in range(len(heights)):
        while heights[c] > MIN_CHANNEL and time.monotonic() <= deadline:
            trial = heights[:c] + [heights[c] - 1] + heights[c + 1:]
            try:
                smaller = _attempt(p, shelves, trial, bus_far, deadline, west)
            except Blocked:
                break
            heights = trial
            got = min(got, smaller, key=lambda g: g.area)
    return got, heights


def _fitted(p, shelves, bus_far, beat=math.inf, deadline=math.inf):
    """(layout, channel heights): channels grown together until it routes, then each
    cut to the rows it used; None if it never routes in less room than beat, or in time."""
    for extra in range(0, MAX_EXTRA + 1, 3):
        heights = [MIN_CHANNEL + extra] * (len(shelves) + 1)
        if least_area(p, shelves, heights) >= beat or time.monotonic() > deadline:
            return None
        try:
            got = _attempt(p, shelves, heights, bus_far, deadline)
            break
        except Blocked:
            continue
    else:
        return None
    fitted = [max(MIN_CHANNEL, used) for used in got.channels_used]
    if fitted != heights:
        try:
            return _attempt(p, shelves, fitted, bus_far, deadline), fitted
        except Blocked:
            pass
    return got, heights


def _refuse(p):
    blocks = sorted({r.step.item for r in p["rows"]
                     if any(q.kind == "fluid_out" or (q.kind == "fluid_in" and q.side != "south") for q in r.ports)})
    if blocks:
        raise CompactError(f"fluid blocks ({', '.join(blocks)}) aren't laid out compact yet")
    made = sorted({ln.name for named in p["lines"].values() for ln in named if ln.fluid and not ln.supplied})
    if made:
        raise CompactError(f"fluids made here ({', '.join(made)}) aren't laid out compact yet")
    for r in p["rows"]:
        x0, y0, x1, y1 = r.box
        for q in r.ports:
            if (q.kind in ("in", "fluid_in") and q.y != y1) or (q.kind == "out" and q.y != y0):
                raise CompactError(f"{r.step.item}: a port off the row's south or north edge")


def _attempt(p, shelves, heights, bus_far=True, deadline=math.inf, west=False) -> Result:
    """One layout of these shelves and channel heights, or Blocked. west: every shelf as
    far west as the lines running beside it let it, not only as far as the box needs."""
    rows, belt = p["rows"], p["belt"]
    restore(p)
    underground, splitter = factory.KITS[belt]
    reach = entities.raw[underground]["max_distance"]
    lines = [ln for named in p["lines"].values() for ln in named]
    shelf_of = {k: s for s, shelf in enumerate(shelves) for k in shelf}
    raw = [ln for ln in lines if ln.supplied and not ln.fluid]
    raw += [ln for ln in lines if bus_far and not ln.supplied and not ln.top and far(ln, rows, shelf_of)]
    canvas = factory.Canvas()
    claimed = {}  # goal tiles: (x, y) -> the connection allowed onto it
    conns = []
    taps = belts.Taps()

    def free(x, y):
        return canvas.free(x, y) and (x, y) not in claimed

    # Rows on shelves, south to north, each shelf's channel below it (its ports'
    # adapters, the taps into it, the chains out of the shelf below).
    sinks_in = [set() for _ in shelves]
    for s, shelf in enumerate(shelves):
        for k in shelf:
            for q in rows[k].ports:
                if q.kind == "in":
                    for ln in q.feeds.values():
                        sinks_in[s].add(id(ln))

    def source(ln):  # the shelf of a made line's rows
        return max(shelf_of[k] for k, r in enumerate(rows) for q in r.ports if q.kind == "out" and ln in q.outs)

    def tapped(ln):  # the shelves a raw line taps into
        got = [s for s in range(len(shelves)) if id(ln) in sinks_in[s]]
        return got if ln.supplied else [s for s in got if s > source(ln)]

    # A ragged bus: each line ends at its last tap, and lines reaching further north sit
    # further west, so a shelf above a line's last tap starts in its columns. Each
    # channel's yard is the two columns east of the lines still running in it.
    last_channel = {id(ln): max(tapped(ln), default=0) for ln in raw}
    raw.sort(key=lambda ln: -last_channel[id(ln)])
    running = [sum(1 for ln in raw if last_channel[id(ln)] >= c) for c in range(len(shelves) + 1)]
    yard = [(2 * n, 2 * n + 1) for n in running]
    # A shelf moves west only as far as the box needs: moved further, it just stands
    # off from the shelves it takes from.
    span = [sum(width(rows[k]) for k in shelf) + GAP * (len(shelf) - 1) for shelf in shelves]
    edge = max(yard[s][1] + 1 + span[s] for s in range(len(shelves)))
    x0s = [max(yard[s][1] + 1, min(yard[s][1] + 1 if west else 2 * len(raw) + 2, edge - span[s]))
           for s in range(len(shelves))]
    where, shelf_box, y_out = place(rows, shelves, heights, x0s, canvas)
    x_max = max(x for x, _ in canvas.tiles) + 2

    def at(k, q):
        dx, dy = where[k]
        return q.x + dx, q.y + dy

    localize(rows, lines, raw, at)
    # The tile under each fluid port is its pipe's, kept from the belts.
    for k, r in enumerate(rows):
        for q in r.ports:
            if q.kind == "fluid_in":
                px, py = at(k, q)
                if not canvas.free(px, py + 1):
                    raise Blocked(f"{q.name}: no room under its port")
                claimed[(px, py + 1)] = f"fluid {q.name}"

    def channel_rows(s):
        """Rows of the channel below shelf s, south to north (the top channel for s = len)."""
        south = -1 if s == 0 else shelf_box[s - 1][1] - 1
        north = shelf_box[s][0] + 1 if s < len(shelves) else y_out
        return list(range(south, north - 1, -1))

    # Adapters: under each port, a belt fed from below (one line) or from both sides (two).
    goals = {}  # id(line) -> [(x, y, facing, owner)]
    for s, shelf in enumerate(shelves):
        ports = [(k, q) for k in shelf for q in rows[k].ports if q.kind == "in"]
        ports.sort(key=lambda kq: (len(set(map(id, kq[1].feeds.values()))) > 1, at(*kq)[0]))
        south = channel_rows(s)[0]
        for k, q in ports:
            owner = f"row {k} port {q.x}"
            for ln, goal in belts.adapter(canvas, free, *at(k, q), south, q.feeds, belt, claimed, owner):
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
        return lambda x, y: free(x, y) and held.get((x, y), id(ln)) == id(ln)

    outlets = []
    bus_entry = {}  # id(line) -> its merged rows' handle, for lines made here that ride the bus
    for ln in lines:
        if ln.supplied:
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
        if ln.supplied:
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
            mine = [g for g in goals.get(id(ln), []) if channel_rows(s)[0] >= g[1] >= channel_rows(s)[-1]]
            yb = belts.tap(canvas, free, taps, channel_rows(s), c, *yard[s], last, mine, belt, splitter, conns, ln.name)
            events.add(yb if last else yb + 1)
        bus.append(_BusLine(ln.name, c, events))
    tunnels = []
    for cn in conns:  # a route's first tile is its own, as its last is
        if claimed.setdefault(cn.start, cn.owner) != cn.owner:
            raise Blocked(f"{cn.owner}: starts on {claimed[cn.start]}'s tile")
    # Routes keep east of the lines still running at each row: a band is a channel and the shelf above it.
    band = {0: 0}
    for s in range(len(shelves) + 1):
        band.update({y: s for y in channel_rows(s)})
        if s < len(shelves):
            band.update({y: s for y in range(shelf_box[s][1], shelf_box[s][0] + 1)})
    bounds = (min(x for x, _ in yard), y_out, x_max, -1)
    fluid_inputs, fluid_ports = _pipe_fluids(p, rows, lines, where, at, canvas, claimed, yard, band, bounds)
    spec = [(cn.start, cn.heading, cn.goal, cn.facing, lambda x, y, cn=cn: claimed.get((x, y), cn.owner) == cn.owner)
            for cn in conns]
    paths = route_all(spec, lambda x, y: canvas.free(x, y) and x >= yard[band.get(y, 0)][0], reach, bounds,
                      deadline=min(deadline, time.monotonic() + NEGOTIATION))
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

    bp = Blueprint()
    bp.label = f"Factory: {p['rate']:g} {p['item']}/s, compact"
    bp.description = _description(p, raw + [ln for ln in lines if ln.fluid], len(outlets))
    bp.icons = [p["item"]]
    for k, r in enumerate(rows):
        dx, dy = where[k]
        for e in r.bp.entities:
            copy = deepcopy(e)
            copy.tile_position = (int(e.tile_position[0]) + dx, int(e.tile_position[1]) + dy)
            copy.id = f"row {k} {e.id}" if e.id else None
            bp.entities.append(copy, copy=False)
    for name, tile, kw in canvas.items:
        bp.entities.append(name, tile_position=tile, **kw)
    for tile in poles:
        bp.entities.append(factory.POLE, tile_position=tile)
    bp.generate_power_connections()
    if fluid_ports:
        _check_fluids(bp.to_dict()["blueprint"], fluid_ports)
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
    return Result(bp, area, inputs, [(ident, name) for _, ident, name in outlets], max(ys) - min(ys) + 1, used,
                  ports, [(line.name, line.column, min(line.events)) for line in bus],
                  [(x0s[s], *shelf_box[s]) for s in range(len(shelves))], leftovers, fluid_inputs, fluid_ports)


def _pipe_fluids(p, rows, lines, where, at, canvas, claimed, yard, band, bounds):
    """Each supplied fluid in at the bottom through a pipe of its own (two columns from any
    other, so the players' pipes beyond don't join), piped to the rows that take it.
    Returns (inputs as (id, fluid), fluid tiles as (tile, fluid): the inputs and ports)."""
    fluid_lines = [ln for ln in lines if ln.fluid]
    if not fluid_lines:
        return [], []
    fluids = Fluids(_row_fluids(p, rows, where))
    fluids.spans += [span for k in range(len(rows)) for span in _row_spans(p, rows[k], where[k])]
    boxes = {(x + where[k][0], y + where[k][1]) for k, r in enumerate(rows)
             for x in range(r.box[0], r.box[2] + 1) for y in range(r.box[1], r.box[3] + 1)}
    inputs, ports, columns = [], [], []
    for ln in fluid_lines:
        owner = f"fluid {ln.name}"

        def ok(x, y, owner=owner):
            return (canvas.free(x, y) and claimed.get((x, y), owner) == owner and (x, y) not in boxes
                    and x >= yard[band.get(y, 0)][0])

        users = [(k, q) for k, r in enumerate(rows) for q in r.ports if q.kind == "fluid_in" and q.lines.get(q.name) is ln]
        target = sum(at(k, q)[0] for k, q in users) / len(users)
        column = min((x for x in range(yard[0][1] + 1, bounds[2] + 1)
                      if all(abs(x - c) > 1 for c in columns) and ok(x, 0) and ok(x, -1)),
                     key=lambda x: abs(x - target), default=None)
        if column is None:
            raise Blocked(f"{ln.name}: no room for its input")
        columns.append(column)
        ident = f"in {ln.name} {column}"
        canvas.put("pipe", column, 0, owner, id=ident)
        fluids.fix(ln.name, (column, 0), "pipe")
        fluids.at[(column, 1)] = (ln.name, sides("pipe", None))  # where the player's pipe joins it
        inputs.append((ident, ln.name))
        ports.append(((column, 0), ln.name))
        terminals = [({(column, 0)}, ((column, -1), N))]
        for k, q in users:
            px, py = at(k, q)
            terminals.append(({(px, py)}, ((px, py + 1), S)))
            ports.append(((px, py), ln.name))
        try:
            route_line(fluids, ln.name, terminals, ok, (bounds[0], bounds[1], bounds[2], 0))
        except ValueError as e:
            raise Blocked(str(e)) from None
    for (x, y), (name, d) in fluids.placed.items():
        if canvas.free(x, y):
            canvas.put(name, x, y, f"pipe {x} {y}", **({"direction": d} if d is not None else {}))
    return inputs, ports


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
    get a fluid of their own row's, which nothing routed may join."""
    got = {}
    for k, r in enumerate(rows):
        dx, dy = where[k]
        for (x, y), name, d, fluid in _row_local(p, r):
            got[(x + dx, y + dy)] = (fluid or f"row {k}", sides(name, d))
    return got


def _row_spans(p, r, at) -> list:
    """A row's pipe-to-ground ends as spans on both their lines: no routed pair may
    reach past one, or the game would pair it with it."""
    dx, dy = at
    return [span for (x, y), name, _, _ in _row_local(p, r) if name == "pipe-to-ground"
            for span in (("x", x + dx, y + dy, y + dy), ("y", y + dy, x + dx, x + dx))]


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
    supplied = [f"{ln.name} {ln.demand:.3g}/s{' (pipe)' if ln.fluid else ''}" for ln in raw]
    text = (f"{len(p['rows'])} rows on shelves, products handed over directly. Inputs enter at the bottom of the "
            f"west bus, west to east: {', '.join(supplied)}. The product leaves at the top"
            f"{' on ' + str(outlets) + ' belts' if outlets > 1 else ''}. Connect any pole to your grid.")
    if len(text.encode()) > 500:
        text = (f"{len(p['rows'])} rows on shelves. Inputs ({len(raw)} belts) enter at the bottom of the west bus, "
                f"the product leaves at the top. Connect any pole to your grid.")
    return text


def flow_tests(p, result):
    """Feed the bus inputs at the bottom, drain the top: the whole factory at its rate."""
    from validator.flow import Feed, Flow, FluidFeed

    from tools import rates

    feeds = [Feed(ident, lane, item) for ident, item in result.inputs for lane in ("left", "right")]
    fluids = [FluidFeed(ident, fluid, side="south") for ident, fluid in result.fluid_inputs]
    drains = [ident for ident, item in result.outputs if item == p["item"]]
    warmup = sum(max(r.flow.warmup for r in p["rows"] if r.step is s) for s in p["steps"])
    warmup += rates.travel_seconds(p["belt"], result.height)
    window = max(r.flow.window for r in p["rows"])
    return [Flow(f"compact factory of {len(p['rows'])} rows: {p['rate']:g} {p['item']}/s", feeds, drains,
                 {p["item"]: float(p["rate"])}, warmup=warmup, window=window, fluids=fluids, at_least=True,
                 settle=factory.SETTLE)]
