"""Belt pieces the compact factory layout (tools/compact.py) is joined with: adapters
under ports, merges, combs of splitters, taps off the bus and entries onto it. Each lays its fixed belts
and splitters on a factory.Canvas and adds the belts still to route (Conn) between
them, for tools.route.route_all.

Splitter rules these keep (NOTES.md): a splitter with one output left empty passes
both inputs out of the other, so a merge leaves one empty; a splitter's unused
input has nothing pointing into it.
"""

from dataclasses import dataclass, field

from tools.route import E, N, S, W

LEAD = 24  # tiles a split's lead may run north before its comb: past the tallest row (19) and a channel


class Blocked(Exception):
    """This piece doesn't fit here; a wider channel or another shelf width may."""


@dataclass
class Conn:
    """A belt to route: from a handle (the next tile, entered moving heading) to a goal."""
    start: tuple
    heading: int
    goal: tuple
    facing: int
    owner: str


@dataclass
class Taps:
    rows: list = field(default_factory=list)  # branch rows of every tap and entry so far, kept apart


def put_across(canvas, splitter, x, y, owner, d):
    """A splitter facing east or west: one tile wide, two tall (rows y and y + 1)."""
    canvas.take(x, y + 1, owner)
    canvas.put(splitter, x, y, owner, direction=d)


def adapter(canvas, free, xi, yp, south, feeds, belt, claimed, owner, turn=None):
    """Belts under the port at (xi, yp), up to 4 and not south of south, and where its
    lines are to arrive: one line onto both lanes from below; two onto the last belt's
    sides, the left lane's from the west and the right's from the east; one onto one
    lane from that side, with a stub belt pointing in from behind so it's a side-load,
    not a turn (NOTES.md). turn (W or E), for two lines: first try turning in from that
    side, from a belt there fed from both sides, which keeps its lanes through the turn
    (a port beside another taking two lines, whose belt below takes this one's sides).
    Returns [(line, (x, y, facing, owner))]."""
    fed = list({id(ln): ln for ln in feeds.values()}.values())
    one_lane = len(feeds) == 1
    if turn is not None and len(fed) > 1:
        dx = -1 if turn == W else 1
        bend, tee, above, below = (xi, yp + 1), (xi + dx, yp + 1), (xi + dx, yp), (xi + dx, yp + 2)
        if yp + 2 <= south and all(free(*t) for t in (bend, tee, above, below)):
            heading = E if turn == W else W
            canvas.put(belt, *bend, owner, direction=N)
            canvas.put(belt, *tee, owner, direction=heading)
            # Side-loaded onto the lane on its side: heading east, the north one is the left.
            sides = {"left": (above, S), "right": (below, N)} if heading == E else \
                {"left": (below, N), "right": (above, S)}
            got = []
            for lane in ("left", "right"):
                (gx, gy), facing = sides[lane]
                claimed[(gx, gy)] = f"{owner} {lane}"
                got.append((feeds[lane], (gx, gy, facing, f"{owner} {lane}")))
            return got
    for e in range(4):
        ya = yp + 1 + e
        tiles = [(xi, yy) for yy in range(yp + 1, ya + 1)]
        sides = [(xi - 1, ya), (xi + 1, ya)] if len(fed) > 1 else []
        if one_lane:
            sides = [(xi - 1, ya) if "left" in feeds else (xi + 1, ya), (xi, ya + 1)]
        if ya + one_lane <= south and all(free(*t) for t in tiles + sides):
            break
    else:
        raise Blocked(f"{owner}: no room for its adapter")
    for yy in range(yp + 1, ya):
        canvas.put(belt, xi, yy, owner, direction=N)
    if one_lane:
        (lane, line), = feeds.items()
        canvas.put(belt, xi, ya, owner, direction=N)
        canvas.put(belt, xi, ya + 1, owner + " stub", direction=N)
        (gx, gy) = sides[0]
        claimed[(gx, gy)] = owner + " " + lane
        return [(line, (gx, gy, E if lane == "left" else W, owner + " " + lane))]
    if len(fed) == 1:
        claimed[(xi, ya)] = owner
        return [(fed[0], (xi, ya, N, owner))]
    canvas.put(belt, xi, ya, owner, direction=N)
    got = []
    for (gx, gy), lane, facing in zip(sides, ("left", "right"), (E, W)):
        claimed[(gx, gy)] = owner + " " + lane
        got.append((feeds[lane], (gx, gy, facing, owner + " " + lane)))
    return got


def merge(canvas, free, hs, belt, splitter, claimed, conns, name):
    """Several rows' outputs into one line: through splitters stacked north of the
    first, each other output routed to a splitter's second input. Returns the
    merged line's handle (heading north)."""
    if len(hs) == 1:
        return hs[0]
    x, y = hs[0]
    for side in (1, -1):  # the second inputs east of the line, else west
        tiles, ok = [], True
        yy = y
        for m in range(1, len(hs)):
            cx = min(x, x + side)
            need = [(x, yy), (x + side, yy), (cx, yy - 1), (cx + 1, yy - 1), (x + side, yy - 2), (x, yy - 2)]
            if m < len(hs) - 1:
                need.append((x, yy - 3))
            if not all(free(*t) for t in need if t not in tiles):
                ok = False
                break
            tiles += need
            yy -= 3
        if ok:
            break
    else:
        raise Blocked(f"{name}: no room to merge its rows")
    yy = y
    for m in range(1, len(hs)):
        owner = f"merge {name} {m}"
        cx = min(x, x + side)
        canvas.put(belt, x, yy, owner, direction=N)
        goal = (x + side, yy)
        claimed[goal] = owner
        conns.append(Conn(hs[m], N, goal, N, owner))
        canvas.put(splitter, cx, yy - 1, owner, width=2, direction=N)
        canvas.reserve(x + side, yy - 2, owner)  # the splitter's other output stays empty
        if m < len(hs) - 1:
            canvas.put(belt, x, yy - 2, owner, direction=N)
        yy -= 3
    return (x, yy + 1)


def entry(canvas, free, taps, rows_south_north, c, Y1, Y2, trunk, claimed, conns, belt, name) -> int:
    """Where a line made here joins the bus: a row of belts west across the bus to its
    column, turning north there (the line's start), and a route from its rows to them.
    Returns the row."""
    owner = f"onto bus {name}"
    for ye in rows_south_north[1:]:
        if any(abs(ye - tr) < 3 for tr in taps.rows):
            continue
        need = [(x, ye) for x in range(c, Y1 + 1)] + [(Y2, ye)]
        if all(free(*tt) for tt in need):
            break
    else:
        raise Blocked(f"{name}: no room to join the bus")
    taps.rows.append(ye)
    canvas.put(belt, c, ye, owner, direction=N)
    for x in range(c + 1, Y1 + 1):
        canvas.put(belt, x, ye, owner, direction=W)
    claimed[(Y2, ye)] = owner
    conns.append(Conn(trunk, N, (Y2, ye), W, owner))
    return ye


def comb(free, start, heading, targets):
    """A trunk along start's row (entered at start moving east or west), with a
    splitter as near under each target but the last as the row allows, each
    peeling a branch north; the trunk's end goes to the last target.

    Returns (placements, reserved tiles, [(handle, heading, target)]) or None if it
    doesn't fit. Placements are (kind, x, y, direction), kind "belt" or "splitter"
    (a splitter at (x, y) covers (x, y - 1) and (x, y), as put_across places it)."""
    s = 1 if heading == E else -1
    x, y = start
    targets = sorted(targets, key=lambda t: s * t[0])
    place, reserve, branches, cur = [], [], [], x
    for m, target in enumerate(targets[:-1]):
        tx = target[0]
        first = cur if m == 0 else cur + s  # after the first, a trunk tile between splitters
        sx = first if s * (tx - s - first) <= 0 else tx - s  # its branch in the target's column, if ahead
        for bx in range(cur, sx, s):
            place.append(("belt", bx, y, heading))
        upper = (sx - s, y - 1)
        if not any(k == "belt" and (px, py) == upper for k, px, py, _ in place):
            reserve.append(upper)  # nothing may feed the splitter's other input
        place += [("splitter", sx, y, heading), ("belt", sx + s, y - 1, N)]
        branches.append(((sx + s, y - 2), N, target))
        cur = sx + s
    branches.append(((cur, y), heading, targets[-1]))
    tiles = [(px, py) for k, px, py, _ in place] + [(px, py - 1) for k, px, py, _ in place if k == "splitter"]
    tiles += reserve + [h for h, _, _ in branches]
    if len(set(tiles)) != len(tiles) or not all(free(*tt) for tt in tiles):
        return None
    return place, reserve, branches


def lay_comb(canvas, got, belt, splitter, conns, owner):
    place, reserve, branches = got
    for kind, x, y, d in place:
        if kind == "belt":
            canvas.put(belt, x, y, owner, direction=d)
        else:
            put_across(canvas, splitter, x, y - 1, owner, d)
    for tt in reserve:
        canvas.reserve(*tt, owner)
    for handle, heading, (gx, gy, facing, gowner) in branches:
        conns.append(Conn(handle, heading, (gx, gy), facing, gowner))


def split(canvas, free, trunk, targets, belt, splitter, conns, name):
    """One line, heading north from trunk, to its ports: straight to a single one,
    else up a few tiles and along a comb toward them (east or west). A short row's comb
    leads up past the taller rows beside it on its shelf (utility science's plastic,
    beside 19-tile circuit cells), as far as LEAD."""
    if not targets:
        return
    if len(targets) == 1:
        gx, gy, facing, owner = targets[0]
        conns.append(Conn(trunk, N, (gx, gy), facing, owner))
        return
    x, y = trunk
    mean = sum(t[0] for t in targets) / len(targets)
    for lead in range(LEAD):
        r = y - lead
        lead_in = [(x, yy) for yy in range(y, r - 1, -1)]
        if not all(free(*tt) for tt in lead_in):
            break
        for heading in ((E, W) if mean >= x else (W, E)):
            s = 1 if heading == E else -1
            got = comb(lambda a, b: free(a, b) and (a, b) not in lead_in, (x + s, r), heading, targets)
            if got:
                owner = f"split {name} {x} {y}"
                for yy in range(y, r, -1):
                    canvas.put(belt, x, yy, owner, direction=N)
                canvas.put(belt, x, r, owner, direction=heading)
                lay_comb(canvas, got, belt, splitter, conns, owner)
                return
    raise Blocked(f"{name}: no room to split it")


def tap(canvas, free, taps, rows_south_north, c, Y1, Y2, last, targets, belt, splitter, conns, name) -> int:
    """A raw line's tap into a channel: a splitter on the bus (or the line turning, at
    its last use), a branch east across the bus to the yard, and a comb from there
    under the channel's ports. Tried from the channel's north end (by the ports)
    south. Returns the branch's row."""
    owner = f"tap {name} {c}"
    for yb in reversed(rows_south_north[1:]):
        if any(abs(yb - tr) < 3 for tr in taps.rows):
            continue
        need = [(x, yb) for x in range(c + 1, Y1 + 1)] + ([(c, yb)] if last else [(c, yb + 1), (c + 1, yb + 1)])
        if not all(free(*tt) for tt in need):
            continue
        rest = lambda a, b, need=set(need): free(a, b) and (a, b) not in need  # noqa: E731
        got = comb(rest, (Y2, yb), E, targets) if len(targets) > 1 else ([], [], [((Y2, yb), E, targets[0])])
        if got is None or (len(targets) == 1 and not rest(Y2, yb)):
            continue
        break
    else:
        raise Blocked(f"{name}: no room for its tap")
    taps.rows.append(yb)
    if last:
        canvas.put(belt, c, yb, owner, direction=E)
    else:
        canvas.put(splitter, c, yb + 1, owner, width=2, direction=N)
        canvas.nofeed.add((c + 1, yb + 2))  # nothing may feed its other input
    for x in range(c + 1, Y1 + 1):
        canvas.put(belt, x, yb, owner, direction=E)
    lay_comb(canvas, got, belt, splitter, conns, owner)
    return yb
