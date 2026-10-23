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

Item factories only: rows with fluids (fluid rows and blocks, oil) aren't laid out
compact yet, and `factory` falls back to the bus layout for them.
"""

import math
from copy import deepcopy
from dataclasses import dataclass, field

from draftsman.blueprintable import Blueprint
from draftsman.data import entities

from generators import factory
from tools import belts
from tools.belts import Blocked, Conn
from tools.route import N, route_all, tunnels_of
from tools.shelves import MIN_CHANNEL, far, least_area, localize, place, restore, shelve, widths

MAX_EXTRA = 14  # rows added to every channel before a width is given up


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


def build(p) -> Result:
    """The smallest compact layout of the rows in p (factory.rows_and_lines) that routes.
    For each shelf width, and each way of carrying items used far from their rows
    (routed straight from them, or on the bus): every channel grown until it routes,
    then cut to the rows it used. The best of those then loses a channel row at a time
    while it still routes and gets no bigger. Shelves are tried least room first, and a
    channel height whose rows and gaps alone already take more room than the best so
    far isn't tried."""
    _refuse(p)
    candidates = {tuple(map(tuple, shelve(p, wide))) for wide in widths(p["rows"])}
    best = None
    for shelves in sorted(candidates, key=lambda s: least_area(p, s)):
        for bus_far in (False, True):
            got = _fitted(p, shelves, bus_far, best[0].area if best else math.inf)
            if got and (best is None or got[0].area < best[0].area):
                best = got + (shelves, bus_far)
    if best is None:
        raise CompactError("the rows don't route in any shelf width tried")
    got, heights, shelves, bus_far = best
    for c in range(len(heights)):
        while heights[c] > MIN_CHANNEL:
            trial = heights[:c] + [heights[c] - 1] + heights[c + 1:]
            try:
                smaller = _attempt(p, shelves, trial, bus_far)
            except Blocked:
                break
            heights = trial
            got = min(got, smaller, key=lambda g: g.area)
    return got


def _fitted(p, shelves, bus_far, beat=math.inf):
    """(layout, channel heights): channels grown together until it routes, then each
    cut to the rows it used; None if it never routes in less room than beat."""
    for extra in range(0, MAX_EXTRA + 1, 3):
        heights = [MIN_CHANNEL + extra] * (len(shelves) + 1)
        if least_area(p, shelves, heights) >= beat:
            return None
        try:
            got = _attempt(p, shelves, heights, bus_far)
            break
        except Blocked:
            continue
    else:
        return None
    fitted = [max(MIN_CHANNEL, used) for used in got.channels_used]
    if fitted != heights:
        try:
            return _attempt(p, shelves, fitted, bus_far), fitted
        except Blocked:
            pass
    return got, heights


def _refuse(p):
    fluid = sorted({r.step.item for r in p["rows"] for q in r.ports if q.kind.startswith("fluid")})
    if fluid:
        raise CompactError(f"rows with fluids ({', '.join(fluid)}) aren't laid out compact yet")
    for r in p["rows"]:
        x0, y0, x1, y1 = r.box
        for q in r.ports:
            if (q.kind == "in" and q.y != y1) or (q.kind == "out" and q.y != y0):
                raise CompactError(f"{r.step.item}: a port off the row's south or north edge")


def _attempt(p, shelves, heights, bus_far=True) -> Result:
    rows, belt = p["rows"], p["belt"]
    restore(p)
    underground, splitter = factory.KITS[belt]
    reach = entities.raw[underground]["max_distance"]
    lines = [ln for named in p["lines"].values() for ln in named]
    shelf_of = {k: s for s, shelf in enumerate(shelves) for k in shelf}
    raw = [ln for ln in lines if ln.supplied]
    raw += [ln for ln in lines if bus_far and not ln.supplied and not ln.top and far(ln, rows, shelf_of)]
    Y1, Y2 = 2 * len(raw), 2 * len(raw) + 1  # yard columns between the bus and the rows
    X0 = Y2 + 1
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
    where, shelf_box, y_out = place(rows, shelves, heights, X0, canvas)
    x_max = max(x for x, _ in canvas.tiles) + 2

    def at(k, q):
        dx, dy = where[k]
        return q.x + dx, q.y + dy

    localize(rows, lines, raw, at)

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
    for k, r in enumerate(rows):
        for q in r.ports:
            if q.kind != "out":
                continue
            xo, yo = at(k, q)
            if len(q.outs) == 2:  # one row feeding two lines: split as it leaves
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
                           if Y1 <= x <= x_max and free(x, y_out)), None)
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
        shelves_used = [s for s in range(len(shelves)) if id(ln) in sinks_in[s]]
        if ln.supplied:
            events = {0}
            ident = f"in {ln.name} {i}"
            canvas.put(belt, c, 0, "input", direction=N, id=ident)
            inputs.append((ident, ln.name))
        else:  # made here: from its rows west onto the bus, in the channel above them
            src = max(shelf_of[k] for k, r in enumerate(rows) for q in r.ports if q.kind == "out" and ln in q.outs)
            ye = belts.entry(canvas, free, taps, channel_rows(src + 1), c, Y1, Y2, bus_entry[id(ln)], claimed,
                             conns, belt, ln.name)
            events = {ye}
            shelves_used = [s for s in shelves_used if s > src]
        for n, s in enumerate(shelves_used):
            last = n == len(shelves_used) - 1
            mine = [g for g in goals.get(id(ln), []) if channel_rows(s)[0] >= g[1] >= channel_rows(s)[-1]]
            yb = belts.tap(canvas, free, taps, channel_rows(s), c, Y1, Y2, last, mine, belt, splitter, conns, ln.name)
            events.add(yb if last else yb + 1)
        bus.append(_BusLine(ln.name, c, events))
    tunnels = []
    for cn in conns:  # a route's first tile is its own, as its last is
        if claimed.setdefault(cn.start, cn.owner) != cn.owner:
            raise Blocked(f"{cn.owner}: starts on {claimed[cn.start]}'s tile")
    bounds = (Y1, y_out, x_max, -1)
    spec = [(cn.start, cn.heading, cn.goal, cn.facing, lambda x, y, cn=cn: claimed.get((x, y), cn.owner) == cn.owner)
            for cn in conns]
    paths = route_all(spec, canvas.free, reach, bounds)
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
    bp.description = _description(p, raw, len(outlets))
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
    tiles = list(canvas.tiles) + list(poles)
    xs, ys = [x for x, _ in tiles], [y for _, y in tiles]
    area = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
    used = []
    for s in range(len(shelves) + 1):
        span = channel_rows(s)
        taken = {y for (x, y), owner in canvas.tiles.items()
                 if y in span and x >= Y1 and not str(owner).startswith("row")}
        # Rows from the channel's north end down to the southmost one used: what's north of it can't go.
        used.append(span[0] - min(span) + 1 if not taken else max(taken) - min(span) + 1 if s < len(shelves) else
                    span[0] - min(taken) + 1)
    ports = [("in", at(k, q), set(q.lanes.values())) if q.kind == "in" else ("out", at(k, q), q.name)
             for k, r in enumerate(rows) for q in r.ports if q.kind in ("in", "out")]
    return Result(bp, area, inputs, [(ident, name) for _, ident, name in outlets], max(ys) - min(ys) + 1, used,
                  ports)


@dataclass
class _BusLine:
    """A raw line on the bus, as factory._fill_belt fills it."""
    name: str
    column: int
    events: set


def _description(p, raw, outlets) -> str:
    supplied = [f"{ln.name} {ln.demand:.3g}/s" for ln in raw]
    text = (f"{len(p['rows'])} rows on shelves, products handed over directly. Inputs enter at the bottom of the "
            f"west bus, west to east: {', '.join(supplied)}. The product leaves at the top"
            f"{' on ' + str(outlets) + ' belts' if outlets > 1 else ''}. Connect any pole to your grid.")
    if len(text.encode()) > 500:
        text = (f"{len(p['rows'])} rows on shelves. Inputs ({len(raw)} belts) enter at the bottom of the west bus, "
                f"the product leaves at the top. Connect any pole to your grid.")
    return text


def flow_tests(p, result):
    """Feed the bus inputs at the bottom, drain the top: the whole factory at its rate."""
    from validator.flow import Feed, Flow

    from tools import rates

    feeds = [Feed(ident, lane, item) for ident, item in result.inputs for lane in ("left", "right")]
    drains = [ident for ident, item in result.outputs if item == p["item"]]
    warmup = sum(max(r.flow.warmup for r in p["rows"] if r.step is s) for s in p["steps"])
    warmup += rates.travel_seconds(p["belt"], result.height)
    window = max(r.flow.window for r in p["rows"])
    return [Flow(f"compact factory of {len(p['rows'])} rows: {p['rate']:g} {p['item']}/s", feeds, drains,
                 {p["item"]: float(p["rate"])}, warmup=warmup, window=window, at_least=True,
                 settle=factory.SETTLE)]
