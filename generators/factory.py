"""Factory: a production book laid out on a main bus, every row connected.

    python -m tools.bp gen factory item=electronic-circuit rate=5 --validate

Same parameters as `production`, and the same rows (generators/production.py),
stacked north beside a bus:

    ↑  product, and any fluid left over, leaves at the top
    ║ ║ ┃      ┌────────┐
    ║ ║ ┃ ═══> │ row 2  │   corridor: row 1's products return west onto the
    ║ ║ ┃ <=== └────────┘   bus, then the bus feeds row 2's input belts
    ║ ║ ┃      ┌────────┐
    ║ ║ ┃ ═══> │ row 1  │
    ║ ║ ┃      └────────┘
    ↑  inputs enter at the bottom: a belt per item, a pipe per fluid
    items  fluids

Bus lines run north: a belt per item in the west columns, a pipe per fluid
east of them. Where a corridor crosses a line, the line dips underground
(underground belts, or pipe-to-ground, which also keeps fluids apart); two
empty rows between corridor jobs let lines come back up. A row takes an
ingredient through a splitter, or turns the whole line at its last user. Two
ingredients for one belt go onto their lanes by side-loading. Products join
their line through a splitter, fluids at a junction. Fluid blocks reach the
bus straight from the west end of their lines.

Each item has one line, or as many as its users need: an input past a belt's
worth, or anything made here past 95% of one (measured: fuller lines
meet their target, but only just), each line feeding some of the users. Fluid inputs and outlets each get
their own row, so pipes brought up to them don't join. Fluid rows whose
product belt also carries an ingredient's leftovers pass it through a filter
splitter that keeps the ingredient in the row. Cracking takes its oil through
a pump that runs only while a storage tank on the oil line holds some, so the
oil's other users come first. A long fluid line gets a pump every 200 rows: a
pipe network over 320 tiles stops. Poles join every row into one network.

`flow_tests` feeds the inputs at the bottom, drains the top and expects the
target rate: the whole factory, as a player would build it.
"""

import inspect
import math
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction
from draftsman.data import entities, fluids as fluid_data, items

from generators import production
from tools import pipes, rates
from tools.check import TEXT_LIMITS
from tools.settings import game_settings

KITS = {"transport-belt": ("underground-belt", "splitter"),
        "fast-transport-belt": ("fast-underground-belt", "fast-splitter"),
        "express-transport-belt": ("express-underground-belt", "express-splitter"),
        "turbo-transport-belt": ("turbo-underground-belt", "turbo-splitter")}
BAND = 2  # empty rows before each corridor job, where crossed lines come back up
PIPE_REACH = 10  # pipe-to-ground pairs up to this many tiles apart (NOTES.md)
POLE = "medium-electric-pole"
# A line the factory makes is split once its users need more than this share
# of a belt. Measured (python -m tools.optimise belt-fill, NOTES.md): lines up
# to 95% of a yellow belt met their target in the first window; full ones made
# 99-100% but some needed a second.
MADE_FILL = 0.95
SURPLUS_LEVEL = 0.5  # flow tests: a left-over fluid's outlet keeps its line half full
# Flow tests measure up to this many windows: belts fill only as fast as each
# row's 5% slack allows, and upstream users take more until theirs are full.
SETTLE = 4
GATE_HEIGHT = 6  # rows a cracking gate takes north of its block
GATE_LEVEL = 1000  # units in the gate's tank above which cracking runs
# Rows between pumps on a fluid line. A pipe segment over 320 tiles stops (NOTES.md);
# this leaves room for the rows' own pipes beyond the line's ends.
PUMP_SPAN = 200
PIPELINE_EXTENT = 320  # from the 2.1 dump's default_pipeline_extent
N, E, S, W = Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST


@dataclass(eq=False)  # compared by identity: rows built alike have equal fields
class Port:
    kind: str  # "in" and "out" (item belts), "fluid_in" and "fluid_out"
    x: int  # tile, relative to its row until the row is placed
    y: int
    lanes: dict[str, str] = field(default_factory=dict)  # "in": lane -> item
    name: str = ""  # out and fluid ports
    side: str = "south"  # fluid ports: the side the bus connects to
    demand: dict[str, float] = field(default_factory=dict)  # "in" and fluid_in: per second
    filter: bool = False  # "out": the belt also carries an ingredient's leftovers
    lines: dict = field(default_factory=dict)  # name -> Line (outputs and fluids)
    feeds: dict = field(default_factory=dict)  # "in": lane -> the Line feeding it
    outs: list = field(default_factory=list)  # "out": the Line it joins, or two (split by a splitter)

    def all_lines(self) -> list:
        return list({id(ln): ln for ln in [*self.feeds.values(), *self.lines.values(), *self.outs]}.values())


@dataclass(eq=False)
class Row:
    step: production.Step
    bp: Blueprint
    flow: object
    ports: list[Port]
    box: tuple[int, int, int, int]  # min x, min y, max x, max y of its tiles
    dx: int = 0
    dy: int = 0


@dataclass(eq=False)
class Line:
    name: str
    fluid: bool
    supplied: bool  # fed at the bottom of the bus
    top: str = ""  # "product" or "surplus": runs to the top of the bus
    demand: float = 0
    column: int = -1
    start: int = 0  # southmost row its jobs use (its own tiles may start further north)
    end: int = 0  # northmost
    events: set[int] = field(default_factory=set)  # rows of its own fixed tiles
    jobs: list = field(default_factory=list)  # events in order along the bus


@dataclass(eq=False)
class Job:
    kind: str  # "return", "tap", "fluid_tap", "side_in", "side_out", "gate"
    row: int  # index of the row it serves
    port: Port
    height: int = 0
    rows: list[int] = field(default_factory=list)  # allocated, south to north
    part: int = 0  # a return from a split port: 0 west half, 1 east half
    first: set = field(default_factory=set)  # ids of the lines it starts
    last: set = field(default_factory=set)  # ids of the lines it is the last user of


def rows_and_lines(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
                   furnace="steel-furnace", raw="", oil=0, fill=MADE_FILL):
    """Rows and their lines (which line feeds each port), before they're laid out."""
    if belt not in KITS:
        raise ValueError(f"belt is one of {list(KITS)}")
    steps, inputs = production.solve(item, rate, machine, belt, smelt, furnace, raw, oil)
    rows = [_row(s) for s in steps for _ in range(s.rows)]
    lines = _lines(rows, inputs, item, rate, belt, fill)
    return {"steps": steps, "inputs": inputs, "rows": rows, "lines": lines, "belt": belt, "item": item, "rate": rate}


def plan(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
         furnace="steel-furnace", raw="", oil=0, fill=MADE_FILL):
    """Rows, lines and corridor jobs, with every row and line placed."""
    p = rows_and_lines(item, rate, machine, belt, smelt, furnace, raw, oil, fill)
    jobs = _jobs(p["rows"], p["lines"])
    layout = _allocate(p["rows"], jobs, p["lines"])
    _columns(p["lines"])
    return {**p, "jobs": jobs, **layout}


def _row(step: production.Step) -> Row:
    gen = production.GENERATORS[step.generator]
    bp = gen.build(**step.params)
    (flow,) = gen.flow_tests(**step.params)
    by_id = {e.id: e for e in bp.entities if e.id}
    tiles = [(e.tile_position[0] + dx, e.tile_position[1] + dy)
             for e in bp.entities for dx in range(e.tile_width) for dy in range(e.tile_height)]
    box = (min(x for x, _ in tiles), min(y for _, y in tiles), max(x for x, _ in tiles), max(y for _, y in tiles))

    def at(ref):
        p = by_id[ref].tile_position
        return int(p[0]), int(p[1])

    recipe = rates.recipe_any(step.params["recipe"])
    # Oil steps count crafts; the rest count their product.
    crafts = (step.demand / recipe.results[step.item] if step.item in recipe.results else step.demand) / step.rows
    ports, lane_count = {}, {}
    for f in flow.feed:
        port = ports.setdefault(f.entity, Port("in", *at(f.entity)))
        port.lanes[f.lane] = f.item
        lane_count[f.item] = lane_count.get(f.item, 0) + 1
    for port in ports.values():
        for lane, name in port.lanes.items():
            per_craft = recipe.ingredients.get(name) or _fuel(step, recipe, name)
            port.demand[name] = port.demand.get(name, 0) + crafts * per_craft / lane_count[name]
    out = [p for p in flow.expect if p not in fluid_data.raw]
    # A fluid row's near-lane ingredient rides on out with the product.
    leftovers = step.generator == "fluid_row" and any(len(p.lanes) == 1 for p in ports.values())
    result = list(ports.values())
    for d in flow.drain:
        result.append(Port("out", *at(d), name=out[0], filter=leftovers))
    for f in flow.fluids:
        kind = "fluid_out" if f.sink else "fluid_in"
        port = Port(kind, *at(f.entity), name=f.fluid, side=f.side or "south")
        if not f.sink:
            port.demand[f.fluid] = crafts * recipe.fluids[f.fluid]
        result.append(port)
    return Row(step, bp, flow, result, box)


def _fuel(step, recipe, fuel) -> float:
    """Fuel per craft for a burner furnace: its power for one craft's time over the fuel's value."""
    furnace = step.params["furnace"]
    proto = entities.raw[furnace]
    seconds = recipe.time / proto["crafting_speed"]
    return _joules(proto["energy_usage"]) * seconds / _joules(items.raw[fuel]["fuel_value"])


def _joules(text: str) -> float:
    """'90kW' or '4MJ' as a number (watts or joules)."""
    number = text.rstrip("WJ")
    scale = {"k": 1e3, "M": 1e6, "G": 1e9}.get(number[-1], 1)
    return float(number.rstrip("kMG")) * scale


def _lines(rows: list[Row], inputs: dict[str, float], item: str, rate: float, belt: str, fill: float
           ) -> dict[str, list[Line]]:
    """Bus lines per item or fluid, with each port's line set."""
    cap = 2 * rates.lane_rate(belt)
    users, makers, order = {}, {}, list(inputs)
    for r, row in enumerate(rows):
        for p in row.ports:
            for name in dict.fromkeys(p.lanes.values() if p.kind == "in" else [p.name]):
                (makers if p.kind in ("out", "fluid_out") else users).setdefault(name, []).append((r, p))
                if name not in order:
                    order.append(name)
    lines = {}
    for name in order:
        fluid = name in fluid_data.raw
        # Fuel for burner furnaces isn't in the solver's inputs; it is supplied too.
        supplied = inputs.get(name, 0) > 0 or (name not in makers and name in users)
        if name not in users and name != item and inputs.get(name, 0) >= 0:
            continue
        # A unit is what one tap delivers: a port's lanes of this item, or (split) one lane.
        units = [(p, tuple(lane for lane, i in p.lanes.items() if i == name), p.demand.get(name, 0))
                 for _, p in users.get(name, [])]
        top = "product" if name == item else "surplus" if inputs.get(name, 0) < 0 else ""
        if top == "product" and not fluid:  # the outlets at the top use it, a belt's share each
            count = max(1, math.ceil(rate / (fill * cap) - 1e-9))
            units = [(Port("outlet", 0, 0, name=name), (), rate / count) for _ in range(count)]
        total = sum(d for _, _, d in units)
        if fluid:  # one pipe carries any amount here
            groups = [(units, [p for _, p in makers.get(name, [])])]
        elif supplied:  # as many belts as the users need, each feeding some of them
            groups = [(group, []) for group in _bins(units, [cap] * math.ceil(total / cap - 1e-9))]
        else:  # users packed into belts a little under full, then the rows' output shared out
            fewest = max(1, math.ceil(total / (fill * cap) - 1e-9))
            # Whole ports first; failing that, a port's two lanes may come from two lines.
            split = [(p, (lane,), d / len(lanes)) for p, lanes, d in units for lane in lanes] + \
                [u for u in units if not u[1]]
            uses = None
            for count in range(fewest, len(units) + 1):
                size = fill * cap if count > 1 else cap
                for tried in (units, split):
                    try:
                        uses = _bins(tried, [size] * count, grow=False)
                        break
                    except ValueError:
                        continue
                if uses:
                    break
            groups = list(zip(uses, _supply(name, uses, makers.get(name, []), rows)))
        lines[name] = []
        for use, make in groups:
            line = Line(name, fluid, supplied, top, demand=sum(d for _, _, d in use))
            for p, lanes, _ in use:
                for lane in lanes:
                    p.feeds[lane] = line
                if not lanes:
                    p.lines[name] = line
            for p in make:
                if p.kind == "out":
                    p.outs.append(line)
                else:
                    p.lines[name] = line
            lines[name].append(line)
    return lines


def _supply(name, uses, makers, rows) -> list[list]:
    """The rows' output ports for each line: shared out in proportion to each line's
    demand, in order up the bus, so a port feeds one line or, at a boundary, two
    (through a splitter as it leaves its row). A port that also carries leftovers
    (behind a filter splitter) isn't split: it goes to the line it mostly feeds."""
    ports_per_row = Counter(r for r, _ in makers)
    outs = [(p, rows[r].flow.expect[name] / ports_per_row[r]) for r, p in makers]
    demand = [sum(d for _, _, d in use) for use in uses]
    total, supply = sum(demand), sum(rate for _, rate in outs)
    room = [supply * d / total for d in demand]
    shares = [[] for _ in uses]
    j = 0
    for p, rate in outs:
        fed = []
        while rate > 1e-9:
            take = min(rate, room[j]) if j < len(room) - 1 else rate
            fed.append((j, take))
            rate -= take
            room[j] -= take
            if room[j] <= 1e-9 and j < len(room) - 1:
                j += 1
        if len(fed) > 2 or (p.filter and len(fed) > 1):
            fed = [max(fed, key=lambda jt: jt[1])]
        for k, _ in fed:
            if p not in shares[k]:
                shares[k].append(p)
    if any(not share for share in shares):
        raise ValueError(f"{name}: its rows can't be shared between {len(uses)} belts; use a faster belt")
    return shares


def _bins(units, sizes, grow=True):
    """Units (port, lanes, demand) grouped into bins of the given sizes (per second),
    worst fit: the largest demand first, into the bin with the most room. With grow,
    a bin is added (the size of the first) when they don't fit."""
    sizes = list(sizes) or [0]
    while True:
        room = list(sizes)
        bins = [[] for _ in sizes]
        for unit in sorted(units, key=lambda u: -u[2]):
            b = max(range(len(room)), key=lambda b: room[b])
            if unit[2] > room[b] * 1.001:
                break
            room[b] -= unit[2]
            bins[b].append(unit)
        else:
            return [b for b in bins if b] or [[]]
        if not grow:
            raise ValueError("doesn't fit")
        sizes.append(sizes[0])


def _jobs(rows: list[Row], lines) -> list[Job]:
    """Corridor jobs and side ports in order up the bus, with each line's first and last."""
    jobs = []
    for k, row in enumerate(rows + [None]):
        if k:  # the previous row's products return to the bus, westmost first (lowest)
            outs = [p for p in rows[k - 1].ports if p.kind == "out"]
            # A port feeding two lines splits as it leaves: its west half returns first.
            jobs += [Job("return", k - 1, p, part=i) for p in sorted(outs, key=lambda p: p.x) for i in range(len(p.outs))]
        if row is None:
            break
        taps = [p for p in row.ports if p.kind == "in" or (p.kind == "fluid_in" and p.side == "south")]
        # Eastmost first, so each tap's climb to its row stays east of the later (higher) taps.
        jobs += [Job("fluid_tap" if p.kind == "fluid_in" else "tap", k, p) for p in sorted(taps, key=lambda p: -p.x)]
        sides = [p for p in row.ports if p.kind in ("fluid_in", "fluid_out") and p.side == "west"]
        for p in sides:
            # Cracking takes only what the oil's other users leave: its oil comes through a gate.
            gated = p.kind == "fluid_in" and row.step.item in production.CRACKING and p.name != "water"
            jobs.append(Job("gate" if gated else "side_in" if p.kind == "fluid_in" else "side_out", k, p))
    for job in jobs:
        for line in ([job.port.outs[job.part]] if job.kind == "return" else job.port.all_lines()):
            if job.kind in ("return", "side_out") and not any(j.kind in ("return", "side_out") for j in line.jobs):
                job.first.add(id(line))
            line.jobs.append(job)
    for named in lines.values():
        for line in named:
            users = [j for j in line.jobs if j.kind not in ("return", "side_out")]
            if users and not line.top:
                users[-1].last.add(id(line))
    for job in jobs:
        p = job.port
        if job.kind == "return":
            job.height = 1 if job.first else 3
        elif job.kind == "tap":
            if len(p.lanes) == 2:  # one line on both lanes turns in; two side-load
                job.height = 2 if len(p.all_lines()) == 1 else 4
            else:  # one lane: from above onto the left, from below onto the right
                job.height = 2 if "left" in p.lanes else 3
        elif job.kind == "fluid_tap":
            job.height = 1
    return jobs


def _allocate(rows, jobs, lines):
    """Rows (y) for every job and row, from the bottom of the bus (y = 0) north.
    Fluid inputs, and fluid outlets at the top, each get their own row, two apart:
    pipes brought up to them side by side would join."""
    all_lines = [ln for named in lines.values() for ln in named]
    fed = [ln for ln in all_lines if ln.supplied and ln.fluid]
    for i, line in enumerate(fed):
        line.start = -2 * i
    cursor = min([0] + [ln.start for ln in fed])
    by_row = {}
    for job in jobs:
        by_row.setdefault((job.kind in ("side_in", "side_out", "gate"), job.row), []).append(job)
    for k, row in enumerate(rows + [None]):
        corridor = [j for j in jobs if j.kind in ("return", "tap", "fluid_tap")
                    and ((j.kind == "return" and j.row == k - 1) or (j.kind != "return" and j.row == k))]
        for job in corridor:
            start = cursor - BAND - 1
            job.rows = [start - i for i in range(job.height)]
            cursor = job.rows[-1]
        if row is None:
            break
        if not corridor:  # rows never touch: a block's pipes would join its neighbour's
            cursor -= BAND
        x0, y0, x1, y1 = row.box
        bottom = cursor - 1
        row.dy = bottom - y1
        cursor = row.dy + y0
        for job in by_row.get((True, k), []):
            job.rows = [row.dy + job.port.y]
            if job.kind == "gate":  # north of the block, above its outermost line (the oil's)
                if job.port.y != min(p.y for p in row.ports if p.kind.startswith("fluid")):
                    raise ValueError(f"{row.step.item}: the cracked oil's line isn't the block's outermost")
                job.rows = [job.rows[0] - i for i in range(1, GATE_HEIGHT + 1)]
                cursor = min(cursor, job.rows[-1])
    top = cursor - BAND - 1
    outlets = [ln for ln in all_lines if ln.top and ln.fluid]
    for line in all_lines:
        rows_used = [r for j in line.jobs for r in j.rows]
        if not line.supplied:
            line.start = max(rows_used)
        elif not line.fluid:
            line.start = 0
        line.end = top - 2 * outlets.index(line) if line in outlets else top if line.top else min(rows_used)
    return {"bottom": 0, "top": min(ln.end for ln in all_lines)}


def _columns(lines):
    """Bus columns: item lines west, fluid lines east of them, reusing a column once
    its line has ended a few rows south of the next one's start."""
    width = 0
    for fluid in (False, True):
        band = sorted((ln for named in lines.values() for ln in named if ln.fluid == fluid), key=lambda ln: -ln.start)
        ends, hosts = [], []  # per column: northmost row used so far, its lines
        for line in band:
            free = [c for c, e in enumerate(ends) if e > line.start + 3]
            c = free[0] if free else len(ends)
            if c == len(ends):
                ends.append(line.end)
                hosts.append([])
            ends[c] = line.end
            hosts[c].append(line)
        # Columns of fluid lines long enough for pumps go east, where a pole can reach them.
        order = sorted(range(len(hosts)), key=lambda c: fluid and any(ln.start - ln.end > PUMP_SPAN for ln in hosts[c]))
        for k, c in enumerate(order):
            for line in hosts[c]:
                line.column = width + k
        width += len(ends)


class Canvas:
    """Tiles taken, and the entities to place."""

    def __init__(self):
        self.tiles = {}  # (x, y) -> owner
        self.items = []  # (name, tile, kwargs)
        self.wires = []  # (color, id, id)
        self.extra_poles = []  # poles placed for single entities, joined to the rest
        self.fluid = {}  # (x, y) -> (fluid, sides it connects on): the bus's pipes, tanks and pumps
        # Tiles below a splitter's unused input: empty, an underground entrance or
        # a belt turning away, but nothing that runs north into it.
        self.nofeed = set()

    def free(self, x, y):
        return (x, y) not in self.tiles

    def take(self, x, y, owner):
        if (x, y) in self.tiles:
            raise ValueError(f"layout: tile ({x}, {y}) taken by {self.tiles[(x, y)]} and {owner}")
        self.tiles[(x, y)] = owner

    def put(self, name, x, y, owner, width=1, fluid=None, **kw):
        for dx in range(width):
            self.take(x + dx, y, owner)
        self.items.append((name, (x, y), kw))
        if fluid:
            self.fluid[(x, y)] = (fluid, _sides(name, kw.get("direction")))

    def reserve(self, x, y, owner):
        """A tile that must stay empty (a splitter side that isn't used)."""
        self.take(x, y, owner)


def build(item="electronic-circuit", rate=5, machine="assembling-machine-2", belt="transport-belt", smelt=0,
          furnace="steel-furnace", raw="", oil=0, fill=MADE_FILL, layout="bus", budget=None) -> Blueprint:
    """fill: the share of a belt a line made here may carry before it's split.
    layout: "bus" (every row on one bus), or "compact" (rows on shelves with their
    products handed over directly, tools/compact.py); compact falls back to the bus,
    saying why, for what it can't lay out yet. budget: seconds the compact search may
    take (default compact.BUDGET); it keeps the best layout found in that time."""
    params = dict(item=item, rate=rate, machine=machine, belt=belt, smelt=smelt, furnace=furnace, raw=raw, oil=oil,
                  fill=fill)
    compact = _compact(layout, params, budget, say=True)
    return compact[1].bp if compact else _build(plan(**params))[0]


_COMPACT = {}  # params -> (parts, compact.Result) or the reason it isn't compact: build and flow_tests share one search


def _compact(layout, params, budget=None, say=False):
    """(parts, compact.Result), or None for the bus layout."""
    if layout not in ("bus", "compact"):
        raise ValueError("layout is bus or compact")
    if layout == "bus":
        return None
    from tools import compact

    budget = compact.BUDGET if budget is None else float(budget)
    defaults = {k: v.default for k, v in inspect.signature(rows_and_lines).parameters.items()}
    key = tuple(sorted({**defaults, **params, "budget": budget}.items()))
    if key not in _COMPACT:
        p = rows_and_lines(**params)
        try:
            _COMPACT[key] = (p, compact.build(p, budget))
        except compact.CompactError as e:
            _COMPACT[key] = str(e)
    got = _COMPACT[key]
    if isinstance(got, str):
        if say:
            print(f"factory: laid out on the bus, not compact: {got}", file=sys.stderr)
        return None
    return got


def _build(p):
    rows, lines, jobs, belt = p["rows"], p["lines"], p["jobs"], p["belt"]
    underground, splitter = KITS[belt]
    all_lines = [ln for named in lines.values() for ln in named]
    width = 1 + max(ln.column for ln in all_lines)
    x0 = width + 3  # bus, a spare column (a splitter's other half), two yard columns
    canvas = Canvas()

    for k, row in enumerate(rows):
        row.dx = x0 - row.box[0]
        for e in row.bp.entities:
            for dx in range(e.tile_width):
                for dy in range(e.tile_height):
                    canvas.take(int(e.tile_position[0]) + row.dx + dx, int(e.tile_position[1]) + row.dy + dy, f"row {k}")
        for port in row.ports:
            port.x += row.dx
            port.y += row.dy

    def belt_at(x, y, d, owner, **kw):
        canvas.put(belt, x, y, owner, direction=d, **kw)

    def pipe_at(x, y, owner, fluid, **kw):
        canvas.put("pipe", x, y, owner, fluid=fluid, **kw)

    def feed(line, split_row, tap_row, last, owner):
        """Take a line east at tap_row: through a splitter at split_row, or the whole
        line at its last user. Returns the first column east of the turn."""
        c = line.column
        if last:
            belt_at(c, tap_row, E, owner)
            line.events.add(tap_row)
            return c + 1
        canvas.put(splitter, c, split_row, owner, width=2, direction=N)
        line.events.add(split_row)
        canvas.nofeed.add((c + 1, split_row + 1))  # nothing may feed its other input
        belt_at(c + 1, tap_row, E, owner)
        return c + 2

    for job in jobs:
        port, owner = job.port, f"{job.kind} {job.row}"
        if job.kind == "return":
            line = port.outs[job.part]
            c, r = line.column, job.rows[0]
            y, x_o = port.y - 1, port.x + job.part
            if port.filter:  # keep the ingredient's leftovers in the row; the product goes on
                canvas.put(splitter, port.x, y, owner, width=2, direction=N, filter=port.name, output_priority="left")
                canvas.reserve(port.x + 1, y + 1, owner)
                canvas.reserve(port.x + 1, y - 1, owner)
                y -= 1
            elif len(port.outs) == 2:  # shared by two lines: split as it leaves, each half to one
                if job.part == 0:
                    canvas.put(splitter, port.x, y, owner, width=2, direction=N)
                    canvas.nofeed.add((port.x + 1, y + 1))
                y -= 1
            for yy in range(y, r, -1):
                belt_at(x_o, yy, N, owner)
            belt_at(x_o, r, W, owner)
            if id(line) in job.first:
                for x in range(c + 1, x_o):
                    belt_at(x, r, W, owner)
                belt_at(c, r, N, owner)
                line.events.add(r)
            else:  # merge through a splitter, whose other output stays empty
                for x in range(c + 2, x_o):
                    belt_at(x, r, W, owner)
                belt_at(c + 1, r, N, owner)
                canvas.put(splitter, c, r - 1, owner, width=2, direction=N)
                canvas.reserve(c + 1, r - 2, owner)
                line.events.add(r - 1)
        elif job.kind == "tap":
            x_in = port.x
            feeding = port.all_lines()
            if len(port.lanes) == 2 and len(feeding) == 1:  # one line on both lanes: turn into the belt
                (line,) = feeding
                r0, r1 = job.rows
                x = feed(line, r0, r1, id(line) in job.last, owner)
                for xx in range(x, x_in):
                    belt_at(xx, r1, E, owner)
                belt_at(x_in, r1, N, owner)
                climb = r1 - 1
            else:  # side-load onto lanes: from above onto the left lane, from below onto the right
                if len(feeding) == 2:  # lanes on these belts are interchangeable: the west line goes left
                    left, right = sorted(feeding, key=lambda ln: ln.column)
                else:
                    left, right = (feeding[0], None) if "left" in port.lanes else (None, feeding[0])
                t = job.rows[2] if len(job.rows) == 4 else job.rows[0] if left else job.rows[2]
                x_t = x_in - 1
                if right:
                    x = feed(right, t + 2, t + 1, id(right) in job.last, owner)
                    for xx in range(x, x_t):
                        belt_at(xx, t + 1, E, owner)
                    belt_at(x_t, t + 1, N, owner)
                if left:
                    x = feed(left, t, t - 1, id(left) in job.last, owner)
                    for xx in range(x, x_t):
                        belt_at(xx, t - 1, E, owner)
                    belt_at(x_t, t - 1, S, owner)
                if not (left and right):  # a stub behind, so the side-load isn't taken as a turn
                    belt_at(x_t - 1, t, E, owner)
                belt_at(x_t, t, E, owner)
                belt_at(x_in, t, N, owner)
                climb = t - 1
            for y in range(climb, port.y, -1):
                belt_at(x_in, y, N, owner)
        elif job.kind == "fluid_tap":
            line = port.lines[port.name]
            c, r = line.column, job.rows[0]
            line.events.add(r)
            pipe_at(c, r, owner, line.name)
            for x in range(c + 1, port.x + 1):
                pipe_at(x, r, owner, line.name)
            for y in range(r - 1, port.y, -1):
                pipe_at(port.x, y, owner, line.name)
        elif job.kind == "gate":
            _gate(canvas, job, x0, pipe_at)
        else:  # side ports: straight west to the bus
            line = port.lines[port.name]
            c, r = line.column, job.rows[0]
            line.events.add(r)
            for x in range(c, port.x):
                pipe_at(x, r, owner, line.name)

    _pump_long_lines(canvas, [ln for ln in all_lines if ln.fluid], width)

    # Ends of the bus: inputs at the bottom, the product and left-overs at the top.
    # Fluid ends are pipe-to-ground open only to the outside. The tile beyond is where
    # the player's pipe connects: kept free, and checked like a pipe of that fluid.
    for line in all_lines:
        c = line.column
        if line.fluid:
            for y in ([line.start + 1] if line.supplied else []) + ([line.end - 1] if line.top else []):
                canvas.take(c, y, f"{line.name} connection")
                canvas.fluid[(c, y)] = (line.name, _sides("pipe", None))
            _fill_pipe(canvas, line, {line.start: f"in {line.name} {c}"} if line.supplied else {},
                       {line.end: f"out {line.name} {c}"} if line.top else {})
            continue
        if line.supplied:
            belt_at(c, line.start, N, "input", id=f"in {line.name} {c}")
            line.events.add(line.start)
        if line.top:
            belt_at(c, line.end, N, "output", id=f"out {line.name} {c}")
            line.events.add(line.end)
        _fill_belt(canvas, line, belt, underground)

    _check_fluids(canvas, rows)
    poles = _join_poles(canvas, rows)

    bp = Blueprint()
    bp.label = f"Factory: {p['rate']:g} {p['item']}/s"
    bp.description = _description(p)
    bp.icons = [p["item"]]
    for k, row in enumerate(rows):
        for e in row.bp.entities:
            copy = deepcopy(e)
            copy.tile_position = (int(e.tile_position[0]) + row.dx, int(e.tile_position[1]) + row.dy)
            copy.id = f"row {k} {e.id}" if e.id else None
            bp.entities.append(copy, copy=False)
    for name, tile, kw in canvas.items:
        kw = dict(kw)
        condition = kw.pop("condition", None)
        bp.entities.append(name, tile_position=tile, **kw)
        if condition:  # a pump that runs while the signal holds
            entity = bp.entities[kw["id"]]
            entity.set_circuit_condition(*condition)
            game_settings(entity, {"circuit_enabled": True})
    for color, a, b in canvas.wires:
        bp.add_circuit_connection(color, a, b)
    for tile in poles:
        bp.entities.append(POLE, tile_position=tile)
    bp.generate_power_connections()
    data = bp.to_dict()["blueprint"]
    longest = max(pipes.extents(data).values(), default=0)
    if longest > PIPELINE_EXTENT - 2:  # the player's pipes at the bus ends count too
        raise ValueError(f"layout: a pipe network spans {longest} tiles; the game stops one past {PIPELINE_EXTENT}")
    p["fluid_tiles"] = {tile: canvas.fluid[tile][0] for name, tile, _ in canvas.items
                        if name in ("pipe", "pipe-to-ground")}
    return bp, p


def _gate(canvas, job, x0, pipe_at):
    """Oil for cracking, through a pump that runs only while a storage tank on the
    oil's line holds more than GATE_LEVEL: the line's other users come first. A
    pipe segment shares one fill level, so the tank reads the whole line.

        row t-5  bus ═══════╗ T T T          T: tank (3x3), its NW corner fed from the west,
        ...                   T T T             its SE corner into the pump below
        row t-3               T T T
        row t-2                   P          P: pump, facing south, into the block's
        row t-1                   P             outermost line at row t
        row t      block  ═══════ ═══ ...
    """
    port, line = job.port, job.port.lines[job.port.name]
    t, owner, fluid = port.y, f"gate {job.row}", port.name
    feed = t - 5
    line.events.add(feed)
    for x in range(line.column, x0 - 2):
        pipe_at(x, feed, owner, fluid)
    tank, pump = f"gate {job.row} tank", f"gate {job.row} pump"
    for dx in range(3):
        for dy in range(3):
            canvas.take(x0 - 2 + dx, t - 5 + dy, owner)
    canvas.items.append(("storage-tank", (x0 - 2, t - 5), {"id": tank}))
    canvas.fluid[(x0 - 2, t - 5)] = (fluid, {(0, -1), (-1, 0)})  # its connections: NW corner north and west,
    canvas.fluid[(x0, t - 3)] = (fluid, {(1, 0), (0, 1)})  # SE corner east and south
    canvas.reserve(x0 - 2, t - 6, owner)
    canvas.reserve(x0 + 1, t - 3, owner)
    canvas.take(x0, t - 2, owner)
    canvas.take(x0, t - 1, owner)
    canvas.items.append(("pump", (x0, t - 2), {"id": pump, "direction": S, "condition": (fluid, ">", GATE_LEVEL)}))
    canvas.fluid[(x0, t - 2)] = (fluid, {(0, -1)})
    canvas.fluid[(x0, t - 1)] = (fluid, {(0, 1)})
    canvas.wires.append(("red", tank, pump))
    if not _pole_by_pump(canvas, x0, t - 2, owner, x0 - 3):
        raise ValueError(f"{owner}: no room for a pole by the pump")


def _pole_by_pump(canvas, x, y, owner, east_of) -> bool:
    """A pole on a free tile east of the bus (column east_of and beyond) powering the
    pump whose top tile is (x, y), joined to the rest later. False if there's no room."""
    supply = entities.raw[POLE]["supply_area_distance"]
    (x1, y1), (x2, y2) = entities.raw["pump"]["collision_box"]  # north-facing: tall and thin
    spot = next(((px, py) for d in range(1, 5) for px in range(max(x - d, east_of), x + d + 1)
                 for py in range(y - d, y + d + 2)
                 if canvas.free(px, py) and abs(px - x) < supply + x2 and abs(py - (y + 0.5)) < supply + y2), None)
    if spot is None:
        return False
    canvas.take(*spot, owner)
    canvas.extra_poles.append(spot)
    return True


def _pump_long_lines(canvas, lines, east_of):
    """A pump every PUMP_SPAN rows up a long fluid line, so no pipe segment passes the
    game's 320-tile limit (NOTES.md). Fluid on the bus always flows north, so the
    pumps face north; each is its own pair of rows in the line, with a pole by it."""
    for line in lines:
        south = line.start if line.supplied else max(line.events)
        north = line.end if line.top else min(line.events)
        c, owner = line.column, f"pump {line.name}"
        y = south
        while y - north > PUMP_SPAN:
            # The northmost spot within reach whose rows (and the ones around) are free.
            for top in range(y - PUMP_SPAN, y - PUMP_SPAN // 2):
                rows = range(top - 1, top + 3)
                if any(not canvas.free(c, r) or r in line.events for r in rows):
                    continue
                if not _pole_by_pump(canvas, c, top, owner, east_of):
                    continue
                canvas.put("pump", c, top, owner, fluid=line.name, direction=N)
                canvas.take(c, top + 1, owner)
                canvas.fluid[(c, top)] = (line.name, {(0, -1)})  # out to the north
                canvas.fluid[(c, top + 1)] = (line.name, {(0, 1)})  # in from the south
                line.events |= {top, top + 1}
                y = top
                break
            else:
                raise ValueError(f"{line.name}: no room for a pump between rows {y - PUMP_SPAN} and {y}")


def _fill_belt(canvas, line, belt, underground):
    """Belts up the line's column between its own tiles, diving under whatever else
    is in the way with underground pairs."""
    reach = entities.raw[underground]["max_distance"]
    c, owner = line.column, f"line {line.name}"
    events = sorted(line.events, reverse=True)  # south first
    for south, north in zip(events, events[1:]):
        span = list(range(south - 1, north, -1))  # south to north
        nofeed = {y for y in span if (c, y) in canvas.nofeed}
        blocked = [y for y in span if not canvas.free(c, y)]
        tunnels = []  # (entrance row, exit row)
        for y in blocked:
            if tunnels and y >= tunnels[-1][1] - 1:  # same run, or one free row apart: one tunnel
                tunnels[-1][1] = y - 1
            else:
                tunnels.append([y + 1, y - 1])
        for entrance, exit_ in tunnels:
            if (entrance not in span or exit_ not in span or not canvas.free(c, entrance) or not canvas.free(c, exit_)
                    or exit_ in nofeed):
                raise ValueError(f"{line.name}: no room to go under the crossing at rows {exit_}..{entrance}")
            if entrance - exit_ > reach:
                raise ValueError(f"{line.name}: crossing at rows {exit_}..{entrance} is longer than {underground} reaches")
        under = {y for a, b in tunnels for y in range(b + 1, a)}
        ends = {a: "input" for a, _ in tunnels} | {b: "output" for _, b in tunnels}
        if nofeed - under - {a for a, _ in tunnels}:
            raise ValueError(f"{line.name}: would feed a splitter's unused input at rows {sorted(nofeed)}")
        for y in span:
            if y in ends:
                canvas.put(underground, c, y, owner, direction=N, io_type=ends[y])
            elif y not in under:
                canvas.put(belt, c, y, owner, direction=N)


def _fill_pipe(canvas, line, start_id, end_id):
    """Pipe-to-ground pairs up the line's column between its own pipes, so the line
    touches nothing beside it; a single tile between two of its pipes is a pipe.
    An input starts, and an outlet ends, on a pipe-to-ground open to the outside
    (start_id and end_id name them)."""
    c, owner = line.column, f"line {line.name}"
    # A bus end is open beyond the line: as if an own pipe stood one tile further out.
    events = sorted(line.events | {y + 1 for y in start_id} | {y - 1 for y in end_id}, reverse=True)
    names = start_id | end_id
    for south, north in zip(events, events[1:]):
        span = list(range(south - 1, north, -1))
        if not span:
            continue
        free = [canvas.free(c, y) for y in span]
        if len(span) == 1:
            if not free[0] or span[0] in names:
                raise ValueError(f"{line.name}: no room for its pipe at row {span[0]}")
            canvas.put("pipe", c, span[0], owner, fluid=line.name)
            continue
        # Pairs: one open to the south at i, its partner open to the north at j, the
        # next pair starting at j + 1. done[i]: a pair starting at i reaches the end.
        last = len(span) - 1
        done = [False] * (last + 2)
        nxt = [None] * (last + 1)
        for i in range(last, -1, -1):
            if free[i]:
                nxt[i] = next((j for j in range(min(i + PIPE_REACH, last), i, -1)
                               if free[j] and (j == last or done[j + 1])), None)
                done[i] = nxt[i] is not None
        if not done[0]:
            raise ValueError(f"{line.name}: no room for pipe-to-ground pairs between rows {span[0]} and {span[-1]}")
        i = 0
        while True:
            j = nxt[i]
            for y, d in ((span[i], S), (span[j], N)):
                canvas.put("pipe-to-ground", c, y, owner, fluid=line.name, direction=d,
                           **({"id": names[y]} if y in names else {}))
            if j == last:
                break
            i = j + 1


def _sides(name, direction) -> set[tuple[int, int]]:
    """Offsets a pipe connects to: every side, or a pipe-to-ground's surface side."""
    if name == "pipe-to-ground":
        return {{N: (0, -1), S: (0, 1), E: (1, 0), W: (-1, 0)}[direction or N]}
    return {(0, -1), (0, 1), (1, 0), (-1, 0)}


def _check_fluids(canvas, rows):
    """No bus pipe connects to a pipe of another fluid, or to a row's pipes other
    than its own port."""
    known = dict(canvas.fluid)
    for k, row in enumerate(rows):
        ports = {(p.x, p.y): p.name for p in row.ports if p.kind in ("fluid_in", "fluid_out")}
        for e in row.bp.entities:
            if e.name in ("pipe", "pipe-to-ground"):
                tile = (int(e.tile_position[0]) + row.dx, int(e.tile_position[1]) + row.dy)
                known.setdefault(tile, (ports.get(tile, f"row {k}"), _sides(e.name, getattr(e, "direction", None))))
    for (x, y), (fluid, sides) in canvas.fluid.items():
        for dx, dy in sides:
            other = known.get((x + dx, y + dy))
            if other and (-dx, -dy) in other[1] and other[0] != fluid:
                raise ValueError(f"layout: {fluid} at ({x}, {y}) connects to {other[0]} at ({x + dx}, {y + dy})")


def _join_poles(canvas, rows) -> list[tuple[int, int]]:
    """Extra poles on free tiles until every row's poles are one network."""
    reach = entities.raw[POLE]["maximum_wire_distance"]
    poles = [(int(e.tile_position[0]) + row.dx, int(e.tile_position[1]) + row.dy)
             for row in rows for e in row.bp.entities if e.type == "electric-pole"] + canvas.extra_poles

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    added = list(canvas.extra_poles)
    groups = _networks(poles, reach)
    r = int(reach)
    while len(groups) > 1:
        # Grow the first group toward the nearest other pole.
        a, b = min(((p, q) for p in groups[0] for g in groups[1:] for q in g), key=lambda pq: dist(*pq))
        step = min(((a[0] + dx, a[1] + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                    if dist((0, 0), (dx, dy)) <= reach and canvas.free(a[0] + dx, a[1] + dy)),
                   key=lambda t: dist(t, b), default=None)
        if step is None or dist(step, b) >= dist(a, b):
            raise ValueError("layout: no free tiles to join the poles")
        canvas.take(*step, "pole")
        added.append(step)
        groups[0].append(step)
        groups = _networks([q for g in groups for q in g], reach)
    return added


def _networks(points, reach):
    groups = []
    for p in points:
        joined = [g for g in groups if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= reach for q in g)]
        groups = [g for g in groups if g not in joined] + [[p] + [q for g in joined for q in g]]
    return groups


def _description(p) -> str:
    lines = [ln for named in p["lines"].values() for ln in named]
    west = sorted(lines, key=lambda ln: ln.column)
    supplied = [f"{ln.name} {ln.demand:.3g}/s" for ln in west if ln.supplied]
    out = [f"{ln.name} ({'product' if ln.top == 'product' else 'left over: use or store it, or refining stops'})"
           for ln in west if ln.top]
    gates = " Cracking takes oil only while the tank by its pump holds over %d." % GATE_LEVEL \
        if any(j.kind == "gate" for j in p["jobs"]) else ""
    text = (f"{len(p['rows'])} rows on a bus. Inputs enter at the bottom of the bus, west to east: "
            f"{', '.join(supplied) or 'none'}. Out at the top: {', '.join(out)}.{gates} Connect any pole to your grid.")
    if len(text.encode()) > TEXT_LIMITS["description"]:
        text = (f"{len(p['rows'])} rows on a bus. Inputs ({len(supplied)} lines) enter at the bottom, "
                f"the product leaves at the top. Connect any pole to your grid.")
    return text


def flow_tests(layout="bus", budget=None, **params):
    from validator.flow import Feed, Flow, FluidFeed

    compact = _compact(layout, params, budget)
    if compact:
        from tools.compact import flow_tests as compact_flow_tests

        return compact_flow_tests(*compact)
    bp, p = _build(plan(**params))
    feeds, drains, fluids = [], [], []
    for line in (ln for named in p["lines"].values() for ln in named):
        if line.supplied:
            ident = f"in {line.name} {line.column}"
            if line.fluid:
                fluids.append(FluidFeed(ident, line.name, side="south"))
            else:
                feeds += [Feed(ident, "left", line.name), Feed(ident, "right", line.name)]
        if line.top:
            ident = f"out {line.name} {line.column}"
            if line.fluid:
                fluids.append(FluidFeed(ident, line.name, side="north", sink=True,
                                        level=SURPLUS_LEVEL if line.top == "surplus" else 0))
            elif line.top == "product":
                drains.append(ident)
    # Time for every row to fill, one after another, and for items to ride the bus.
    warmup = sum(max(r.flow.warmup for r in p["rows"] if r.step is s) for s in p["steps"])
    warmup += rates.travel_seconds(p["belt"], p["bottom"] - p["top"])
    window = max(r.flow.window for r in p["rows"])
    return [Flow(f"factory of {len(p['rows'])} rows: {p['rate']:g} {p['item']}/s", feeds, drains,
                 {p["item"]: float(p["rate"])}, warmup=warmup, window=window, fluids=fluids, at_least=True,
                 settle=SETTLE)]
