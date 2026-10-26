"""Rows on shelves, for the compact factory layout (tools/compact.py): which shelf each
row goes on, where it stands, and which of an item's lines feeds each port.

A shelf is a band of rows side by side, bottoms level, with a channel of free rows
south of it for the belts into it. A row goes on a shelf once every row it takes
from is on a lower one.
"""

from tools.route import BELTS, STEP

GAP = 3  # columns between rows on a shelf: an adapter's side feeds, and a way through
MIN_CHANNEL = 2  # rows in a channel to start from
WIDTHS = 6  # shelf widths tried, from one row per shelf to one shelf


def levels(p) -> list[int]:
    """Per row: 0 if it takes only supplied items, else one more than its makers'."""
    rows = p["rows"]
    makers = {}
    for k, r in enumerate(rows):
        for q in r.ports:
            if q.kind == "out":
                makers.setdefault(q.name, []).append(k)
    level = {}

    def of(k):
        if k not in level:
            needs = {i for q in rows[k].ports if q.kind == "in" for i in q.lanes.values()}
            level[k] = max((1 + of(m) for i in needs for m in makers.get(i, [])), default=0)
        return level[k]

    return [of(k) for k in range(len(rows))]


def width(r) -> int:
    return r.box[2] - r.box[0] + 1


def widths(rows) -> list[int]:
    """Shelf widths to try: from the widest row (one row a shelf) to all rows on one."""
    lo = max(width(r) for r in rows)
    hi = sum(width(r) for r in rows) + GAP * (len(rows) - 1)
    if hi <= lo:
        return [lo]
    return sorted({lo + (hi - lo) * i // (WIDTHS - 1) for i in range(WIDTHS)})


def shelve(p, wide) -> list[list[int]]:
    """Rows in level order, on shelves no wider than wide; a row starts a new shelf
    when it takes from a row on the current one."""
    rows, level = p["rows"], levels(p)
    makers = {q.name: k for k, r in enumerate(rows) for q in r.ports if q.kind == "out"}
    order = sorted(range(len(rows)), key=lambda k: (level[k], k))
    got, used = [[]], 0
    for k in order:
        needs = {i for q in rows[k].ports if q.kind == "in" for i in q.lanes.values()}
        mine = {makers[i] for i in needs if i in makers}
        w = width(rows[k])
        if got[-1] and (used + GAP + w > wide or mine & set(got[-1])):
            got.append([])
            used = 0
        used += (GAP if got[-1] else 0) + w
        got[-1].append(k)
    return got


def least_area(p, shelved, heights=None) -> int:
    """No layout of these shelves is smaller: their rows and gaps, and channels this
    high (every one thinnest if not given)."""
    rows = p["rows"]
    wide = max(sum(width(rows[k]) for k in shelf) + GAP * (len(shelf) - 1) for shelf in shelved)
    tall = sum(max(rows[k].box[3] - rows[k].box[1] + 1 for k in shelf) for shelf in shelved)
    return wide * (tall + sum(heights or [MIN_CHANNEL] * (len(shelved) + 1)))


def place(rows, shelved, heights, x0s, canvas):
    """Rows on their shelves, shelf s from column x0s[s] east, south to north from y = -1, with heights[s]
    channel rows south of shelf s and heights[-1] above the last. Takes the rows'
    tiles in canvas, and keeps empty the tile in front of any row belt that runs off
    the row: the top of each input belt would side-load anything passing there.
    Returns (row -> (dx, dy), (south row y, north row y) per shelf, the top row)."""
    y = -1
    where, shelf_box = {}, []
    for s, shelf in enumerate(shelved):
        y -= heights[s]
        bottom = top = y
        x = x0s[s]
        for k in shelf:
            r = rows[k]
            dx, dy = x - r.box[0], bottom - r.box[3]
            where[k] = (dx, dy)
            r.dx, r.dy = dx, dy
            tiles, ends = _footprint(r)
            for tx, ty in tiles:
                canvas.take(tx + dx, ty + dy, f"row {k}")
            outs = {(q.x + dx, q.y + dy) for q in r.ports if q.kind == "out"}
            for (tx, ty), (sx, sy) in ends:
                t = (tx + dx, ty + dy)
                if (t[0] + sx, t[1] + sy) not in canvas.tiles and t not in outs:
                    canvas.reserve(t[0] + sx, t[1] + sy, f"row {k} end")
            x += width(r) + GAP
            top = min(top, r.box[1] + dy)
        shelf_box.append((bottom, top))
        y = top - 1
    return where, shelf_box, y - heights[-1] + 1


def _footprint(r):
    """(tiles, ends): a row's own tiles, and each belt or underground output's tile and the
    way it runs, from its entities once per blueprint: worked out every layout tried, it
    took most of each (compact.exact tries thousands)."""
    got = getattr(r, "_footprint", None)
    if got is None or got[0] is not r.bp:
        tiles = [(int(e.tile_position[0]) + ex, int(e.tile_position[1]) + ey) for e in r.bp.entities
                 for ex in range(e.tile_width) for ey in range(e.tile_height)]
        ends = [((int(e.tile_position[0]), int(e.tile_position[1])), STEP[int(e.direction)]) for e in r.bp.entities
                if e.name in BELTS or getattr(e, "io_type", None) == "output"]
        got = r._footprint = (r.bp, tiles, ends)
    return got[1], got[2]


def restore(p):
    """Every port's lines, and every line's demand, as factory planned them (a layout
    re-shares them by position, localize)."""
    lines = [ln for named in p["lines"].values() for ln in named]
    if "_planned" not in p:
        p["_planned"] = ([(q, dict(q.feeds)) for r in p["rows"] for q in r.ports if q.kind == "in"],
                         [(ln, ln.demand) for ln in lines])
    feeds, demands = p["_planned"]
    for q, f in feeds:
        q.feeds = dict(f)
    for ln, d in demands:
        ln.demand = d


def localize(rows, lines, bus, at):
    """An item made here on several lines, all into the shelf above: each line takes the
    run of the item's ports nearest its own rows, west to east, in proportion to what
    its rows make. factory shares ports between lines by demand alone, which sends a
    row's output across the whole shelf."""
    by_item = {}
    for ln in lines:
        if not ln.supplied and not ln.top:
            by_item.setdefault(ln.name, []).append(ln)
    for name, lns in by_item.items():
        if len(lns) < 2 or any(ln in bus for ln in lns):
            continue
        supply, where = {}, {}
        for k, r in enumerate(rows):
            outs = [q for q in r.ports if q.kind == "out" and q.name == name]
            for q in outs:
                for ln in q.outs:
                    rate = r.flow.expect[name] / len(outs) / len(q.outs)
                    supply[id(ln)] = supply.get(id(ln), 0) + rate
                    where.setdefault(id(ln), []).append(at(k, q)[0])
        sinks = []
        for k, r in enumerate(rows):
            for q in r.ports:
                if q.kind != "in":
                    continue
                lanes = [lane for lane, ln in q.feeds.items() if ln in lns]
                for lane in lanes:
                    sinks.append((at(k, q)[0] + (-0.5 if lane == "left" else 0.5), q, lane,
                                  q.demand.get(name, 0) / len(lanes)))
        order = sorted(lns, key=lambda ln: sum(where[id(ln)]) / len(where[id(ln)]))
        total = sum(d for *_, d in sinks)
        share = sum(supply[id(ln)] for ln in order)
        bounds, acc = [], 0.0
        for ln in order:
            acc += total * supply[id(ln)] / share
            bounds.append(acc)
        for ln in order:
            ln.demand = 0.0
        done = 0.0
        for x, q, lane, d in sorted(sinks, key=lambda s: s[0]):
            j = next((i for i, b in enumerate(bounds) if done + d / 2 <= b + 1e-9), len(order) - 1)
            q.feeds[lane] = order[j]
            order[j].demand += d
            done += d


def far(ln, rows, shelf_of) -> bool:
    """Is a line made here used on more than one shelf, or on one not right above its rows?"""
    makers = {shelf_of[k] for k, r in enumerate(rows) for q in r.ports if q.kind == "out" and ln in q.outs}
    users = {shelf_of[k] for k, r in enumerate(rows) for q in r.ports if q.kind == "in" and ln in q.feeds.values()}
    return len(users) > 1 or users != {max(makers) + 1}
