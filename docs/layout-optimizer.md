# Layout optimizer: where the space goes, and how to optimise it

The aim: layouts that use space as well as they use throughput, like a perfect
game of Tetris. This note measures where today's generators spend their tiles
(`python -m tools.bp density`), names what's structurally wasteful, and proposes
how to optimise it. The first step, a constructive compact layout, is built
(`factory ... layout=compact`, now the default); what it gained and where the space
still goes is in [The constructive layout](#the-constructive-layout-built), and its
fluids and the sweep that made it the default in
[Fluids, and compact as the default](#fluids-and-compact-as-the-default-built).

All numbers are from `gen NAME ... --validate` on Factorio 2.1.23 (flow tests,
and chain tests for books, all passing) and `bp density` on the result. Empty
means plain geometry: tiles inside the bounding box that no entity covers. An
empty tile inside a pole's supply area or an inserter's reach may still be doing
work, but it's space all the same.

## Measured

| Generator, request | Area (tiles) | Empty | Tiles per machine | Tiles per item/s |
|---|---|---|---|---|
| assembly_row, electronic-circuit, assembling-machine-1 | 78 | 12% | 19.5 | |
| assembly_row, electronic-circuit, assembling-machine-2 | 52 | 21% | 26.0 | |
| assembly_row, iron-gear-wheel, assembling-machine-3, fast belt | 312 | 20% | 26.0 | |
| assembly_row, advanced-circuit, assembling-machine-2 | 1,350 | 10% | 22.5 | |
| assembly_row, automation-science-pack | 1,950 | 17% | 19.5 | |
| assembly_row, logistic-science-pack | 2,340 | 17% | 19.5 | |
| fluid_row, plastic-bar | 130 | 10% | 21.7 | |
| fluid_row, battery | 1,183 | 3% | 19.7 | |
| fluid_row, electric-engine-unit | 1,963 | 3% | 19.6 | |
| fluid_row, processing-unit, assembling-machine-3 | refused | | | |
| fluid_block, sulfuric-acid | 175 | 34% | 43.8 | |
| fluid_block, lubricant | 78 | 18% | 19.5 | |
| fluid_block, advanced-oil-processing | 315 | 24% | 78.8 | |
| fluid_block, heavy-oil-cracking | 104 | 14% | 26.0 | |
| smelting_column, stone furnace, iron | 528 | 16% | 11.0 | |
| smelting_column, steel furnace, steel | 264 | 16% | 11.0 | |
| smelting_column, electric furnace, copper, fast belt and inserters | 936 | 17% | 19.5 | |
| production (book), electronic-circuit 5/s | 260 | 15% | 21.7 | 52 |
| production (book), electronic-circuit 15/s | 624 | 16% | 22.3 | 42 |
| production (book), advanced-circuit 1/s, oil | 628 | 17% | 28.5 | 628 |
| production (book), processing-unit 0.5/s, oil, assembling-machine-3 | 1,257 | 19% | 29.2 | 2,514 |
| **factory, electronic-circuit 5/s** | **1,560** | **60%** | 130.0 | 312 |
| **factory, electronic-circuit 15/s** | **4,462** | **54%** | 159.4 | 297 |
| **factory, advanced-circuit 1/s, oil** | **3,825** | **64%** | 173.9 | 3,825 |
| factory, automation-science-pack 1/s | 738 | 63% | 82.0 | 738 |
| factory, logistic-science-pack 1/s | 2,373 | 65% | 131.8 | 2,373 |
| factory, chemical-science-pack 0.5/s, oil | 6,832 | 62% | 170.8 | 13,664 |

A book's area is the sum of its rows. `production` and `factory` with the same
parameters make the same rows, so the comparison isolates the connections:
**a factory takes 6.0 to 7.2 times its rows' own area** (electronic circuits at
5/s: 260 tiles of rows, 1,560 connected).

## Where the space goes

**Rows are already tight (3 to 21% empty, 34% for sulfuric acid).** Per-column
coverage shows where their empty tiles are:

- **Assembly rows** (`in2 | in1 | ins | machine | out | product | out | machine | ins | in1 | in2`): belts and machines fill their columns (75 to 100%); the waste is in the inserter columns. A 3-tall machine with one output inserter leaves 2 of its 3 column tiles empty: the product-side output columns run 25 to 50% full (12 of 24 for 12 gear assemblers). Poles already take some of the leftover tiles.
- **Smelting columns:** the same shape. Output columns 33 to 50% full, input columns 50 to 62% (with the poles).
- **Fluid rows:** 3 to 10% empty; the shared middle pipe and both item belts run nearly full.
- **Fluid blocks:** 14 to 34% empty, and pipes are the largest single use (42 of 115 covered tiles for sulfuric acid, 137 of 239 for advanced oil processing): a main line per fluid on each side, risers to each machine, and pipe-to-ground pairs under the nearer lines. Belts every second machine add mostly-empty columns (29 to 43% full).

**Factories are not (54 to 65% empty), and that's the connections.** For
electronic circuits at 5/s (20 x 78 tiles):

- The bus columns run 21 to 85% full: lines start at the bottom and end at their last user, so the columns are ragged and the box around them holds the empty rest.
- Every row hands its products back west to the bus through a corridor, with two empty rows between corridor jobs so crossing lines can come back up.
- Rows are stacked north in one column, so the factory is as tall as all its rows plus all its corridors, and as wide as the widest row plus the bus.

## What's structurally wasteful, and what's a cost to keep

| Waste | Where | Size |
|---|---|---|
| Products return to the bus instead of feeding the next row directly | factory | the bulk of 5 to 6 times the rows' area |
| Rows stacked in one column, each as wide as its own template | factory | ragged east edge, bus as tall as the factory |
| Ragged bus columns | factory | 21 to 85% column fill |
| Two empty rows between corridor jobs (for undergrounds to resurface) | factory | 2 rows per crossing |
| One output inserter per 3-tall machine column | rows | 2 empty tiles per machine per side |
| A main line per fluid on both sides of a fluid block | fluid_block | pipes 37 to 57% of covered tiles |
| No inserter research assumed | all rows | more inserters and slower machines than a researched player needs |

The last one also caused the audit's only refusal: processing units in
assembling machine 3 need 4 inserters a side where 3 fit, measured without
research (`tools/inserter_rates.json`). `production` falls back to assembling
machine 2 for that row on its own; `fluid_row` asked directly refuses with "try a
slower machine". With inserter capacity research (#38 measured
`inserter-capacity-bonus` 7 tripling a fast inserter's hand), the faster machine
fits and the row needs fewer of them. A `research=` option on the row generators
would make every row smaller, but it's a capability change with its own flow
tests, not part of the optimiser.

## How to optimise space

Two problems, at two scales.

### 1. The factory: floorplanning plus routing (the big gain)

- **Decision variables.** For each row: which variant (sides 1 or 2, machine count split, mirrored), and its position on the tile grid (x, y integers) and orientation (north or south flow). For each connection between a producer's output and a consumer's input: a belt route on the grid (or a pipe route for fluids), its lane, and where it goes underground.
- **Hard constraints.**
  - Rows don't overlap (each row is a rectangle from its template).
  - Every connection is a continuous belt or pipe path, entering inputs at the template's input ports and leaving at its output ports.
  - Belt lanes as the rows need them: two ingredients on one belt by side-loading, as `factory.py` does today.
  - Pipe networks never touch unless they carry the same fluid, and none is longer than 320 tiles without a pump (NOTES).
  - Underground belts and pipe-to-ground within their reach (4 to 10 tiles by tier), never under something they can't pass.
  - Poles cover every powered entity and every pole is within wire reach of another.
  - Throughput: each connection carries its flow within a belt's or pipe's measured capacity, and the whole layout meets its target rate in `flow_tests` in game. That stays the final judge.
- **Objective,** lexicographic: bounding-box area first, then empty tiles, then entity count (fewer belts and undergrounds).
- **Solver.** A two-level search, each level the tool it suits:
  1. **Floorplan with CP-SAT** (`ortools`). Rows as fixed-size rectangles (one interval variable per axis per row, `AddNoOverlap2D`), with optional variants as alternatives. Minimise box area plus a weighted estimate of connection length (Manhattan distance between ports, times flow). This is exactly what CP-SAT is good at, with tens of rows, and it proves optimality or reports the gap.
  2. **Route with a deterministic grid router** (A* over tiles, with underground moves as edges of the right length, lanes assigned by the rules `factory.py` already encodes). A floorplan the router can't connect is cut, and the floorplan is solved again.
  3. **Validate in game** with the factory's own flow test. Only layouts that meet the target rate count.
- **Is `ortools` worth adding?** Yes for the floorplan. Writing an exact rectangle packer with alternatives and a proof of optimality by hand would reinvent CP-SAT badly. It's one pip install (wheels for macOS arm64 and Linux x64), used only by the optimiser, so the rest of the app doesn't depend on it. Not for routing: a CP-SAT routing model on a grid this size gets large, and a grid router with rip-up and reroute is the standard tool.
- **Start cheaper.** Before any solver, try a constructive layout: rows ordered by the recipe graph, each consumer placed beside its producer with the product belt handed over directly, the bus kept only for raw inputs. Measure it with `bp density` against today's factory. If that captures most of the gain, the solver is for the last stretch; if not, it's needed from the start.

### 2. Rows: templates are already near their own optimum (a small gain)

Rows are a fixed column template repeated per machine, so their search space is
small and enumerable: inserter tier and count per side, pole positions, whether
the second input belt is needed. Exact enumeration over the template (no solver)
can find the smallest row that passes its flow test. The ceiling is the 3 to 21%
empty, and most of that is inserter-column tiles a 3-tall machine can't avoid.

## Recommendation

- **First target: `factory`.** It holds 83 to 86% of a connected factory's area outside its rows, against at most 21% inside a row.
- **First step: the constructive layout above,** measured with `bp density` and validated with the factory's flow test, to size the gain before adding `ortools`.
- **Expected gain, untested.** If the connections took as much area as the rows instead of 5 to 6 times as much, electronic circuits at 5/s would go from 1,560 tiles to about 520: three times smaller. That's an estimate from the measurements above, not a result. The constructive layout reached 880 (below).
- **Alongside: a `research=` option for inserter capacity on the row generators.** It shrinks every row and lifts the processing-unit refusal.

## The constructive layout (built)

`gen factory ... layout=compact` (`tools/compact.py`, with `tools/shelves.py`,
`tools/belts.py` and the router `tools/route.py`). No solver: rows go on shelves,
a row above every row it takes from; raw inputs ride a bus on the west, tapped in
each channel; products go straight from their rows to the rows above that use
them, merged through splitters and split by combs; a negotiated-congestion grid
router (PathFinder, as FPGA routers use) joins the pieces, undergrounds included.
It searches shelf widths, and whether items used far from their rows ride the
bus, growing every channel until it routes and cutting each to what it used.

### Measured

Every request below passed `gen factory ... layout=compact --validate` on
Factorio 2.1.23 (macOS) at its rated rate. Bus is the default layout.

| Request | Bus: area, empty, tiles per item/s | Compact: area, empty, tiles per item/s | Smaller by | Compact in game | Build |
|---|---|---|---|---|---|
| electronic-circuit 5/s | 1,560, 60%, 312 | 880, 44%, 176 | 44% | 6.01/s | 0.4 s |
| electronic-circuit 15/s | 4,462, 54%, 297 | 2,847, 49%, 190 | 36% | 14.9/s (window 2 of 4) | about 100 s |
| automation-science-pack 1/s | 738, 63%, 738 | 500, 57%, 500 | 32% | 1.2/s | 0.3 s |
| logistic-science-pack 1/s | 2,373, 65%, 2,373 | 1,364, 52%, 1,364 | 43% | 1.25/s | about 18 s |
| advanced-circuit 1/s, oil | 3,825 | falls back to the bus | | | |
| chemical-science-pack 0.5/s, oil | 6,832 | falls back to the bus | | | |

Outside the audit, also passing in game: electronic circuits at 5/s from ore
(`smelt=1`, 5.34/s), at 10/s on fast belts (12/s), and inserters at 1/s (1.5/s).

**Rows with fluids aren't laid out compact yet.** Oil processing, cracking,
plastic and sulfur rows have pipe ports, and the compact layout has no pipe
routing or pump and tank rules: those requests get the bus layout and the
message `factory: laid out on the bus, not compact: rows with fluids (...)`.

### Where the space still goes

Compact still takes 2.8 to 4.6 times its rows' area, short of the 2x target:

| Request | Rows | West bus strip | Channels | Beside rows (gaps, ragged shelves) |
|---|---|---|---|---|
| electronic-circuit 5/s (880) | 260 | 264 | 336 | 20 |
| electronic-circuit 15/s (2,847) | 624 | 584 | 1,519 | 120 |
| automation-science-pack 1/s (500) | 177 | 150 | 140 | 33 |
| logistic-science-pack 1/s (1,364) | 367 | 372 | 496 | 129 |

- **Channels** (38 to 53% of the box): rows take inputs at the bottom and send products out of the top, so every shelf needs a channel below it, as wide as the factory, for taps, combs, merges and adapters, plus room for crossing belts to dive under.
- **The west bus** (20 to 30%): two columns per raw line (one spare, for its splitters) and two yard columns, the full height of the factory, though each line is only used up to its last shelf.
- **Beside the rows** (2 to 9%): the 3-column gaps and shelves of unequal width. This is the only part that placement alone controls.

### Would the CP-SAT floorplan help?

Not much on its own. A rectangle floorplanner recovers the "beside rows" column,
20 to 129 tiles here. The rest is routing space. To win there, the floorplan needs
a routing-aware objective: connection length and crossings, and channel capacity,
so rows whose products meet sit close and channels can be thin; otherwise it packs
rows the router can't join. Cheaper steps, in order of the space they target:

1. **Mirrored rows sharing a channel:** a shelf of rows flipped (inputs on top) faces the shelf above it across one channel, so two shelves share it. That's a row variant the design above already lists.
2. **A ragged bus:** stop each raw line at its last tap and let the yard and the rows use the columns above it.
3. **Then the floorplan,** with variants and a wirelength term, once the router gives a per-channel capacity to plan against.

### Negative results

- **Giving up a negotiation earlier.** The router stops when its count of shared tiles hasn't improved in 4 rounds, counting a tie as improvement. Counting only strict improvement (after 4 or 8 rounds) cut the 15/s search from about 100 s to 46 to 56 s, but logistic science grew from 1,364 to 1,890 tiles: its best layout resolves after a long plateau. Kept the tie rule.
- **Every far-used item on the bus.** Sending all items used beyond the next shelf over the bus grew logistic science from 1,632 to 2,574 tiles; both policies are now tried per width.
- **Claiming comb branches before routing** made no difference to any area, and was dropped.
- **A belt end beside a route.** The first version let routes pass in front of the top of a row's input belt; that belt end side-loads onto them, and circuits at 5/s fell to 4.5/s, 15/s to 8/s, logistic science to 0/s. Keeping that tile empty fixed all three (NOTES.md).

## A time budget and a ragged bus (built)

`factory ... layout=compact budget=SECONDS` (default 40). Two changes, and one
idea measured and left out.

- **A budget, and a cut for hopeless negotiations.** At 15 circuits/s, 78 of the
  95 s went to three router negotiations that never routed: one-row shelves without
  far items on the bus, at 15, 25 and 38 s, each running all 30 rounds because a tie
  counts as progress. Every negotiation that routed took 0.1 to 1 s. A negotiation
  is now cut after 4 s (`compact.NEGOTIATION`), and the whole search keeps to
  `budget=` seconds, keeping the best layout found; with nothing found in time it
  falls back to the bus and says so. Same areas on all seven requests; 15 circuits/s
  went from 95 to about 30 s. The default, 40 s, is above the slowest request
  measured (30.7 s on this machine): a smaller factory finishes when it's done, and
  a slower machine or a bigger factory gets the best found in that time.
- **A ragged bus.** Each raw line ends at its last tap, lines reaching further north
  sit further west, each channel's yard is the two columns east of the lines still
  running in it, and routes keep east of those lines at every row. A shelf moves west
  only as far as the box needs; the search then tries every shelf as far west as the
  bus lets it, once, and keeps that if smaller.

### Measured

Every request passed `gen factory ... layout=compact --validate` on Factorio
2.1.23 (macOS) at its rated rate. Build is the search alone, in process.

| Request | Bus | Compact (#61) | Compact now | Change | In game | Build |
|---|---|---|---|---|---|---|
| electronic-circuit 5/s | 1,560 | 880 | 880 | same | 6/s | 0.3 s |
| electronic-circuit 15/s | 4,462 | 2,847 | 2,847 | same | 14.9/s (window 2) | 30.7 s (was about 100 s) |
| automation-science-pack 1/s | 738 | 500 | **450** | -10% | 1.2/s | 0.1 s |
| logistic-science-pack 1/s | 2,373 | 1,364 | **1,239** | -9% | 1.25/s | 12.7 s (was about 18 s) |
| electronic-circuit 5/s, smelt=1 | | 1,755 | **1,564** | -11% | 6/s | 3.2 s (was about 19 s) |
| electronic-circuit 10/s, fast belts | | 1,160 | 1,160 | same | 12/s | 0.6 s |
| inserter 1/s | | 667 | **609** | -9% | 1.5/s | 1.3 s |

A ragged bus pays only where the widest shelf stands above a line's last tap: the
circuit factories' shelves are all one width, so nothing moves.

### Where the space still goes

| Request (box) | Rows | West strip (bus and yard) | Channels | Beside rows | Box / rows |
|---|---|---|---|---|---|
| electronic-circuit 5/s (880) | 260 | 264 (30%) | 336 (38%) | 20 (2%) | 3.4 |
| electronic-circuit 15/s (2,847) | 624 | 584 (21%) | 1,519 (53%) | 120 (4%) | 4.6 |
| automation-science-pack 1/s (450) | 177 | 112 (25%) | 134 (30%) | 27 (6%) | 2.5 |
| logistic-science-pack 1/s (1,239) | 367 | 332 (27%) | 436 (35%) | 104 (8%) | 3.4 |
| electronic-circuit 5/s, smelt=1 (1,564) | 524 | 248 (16%) | 632 (40%) | 160 (10%) | 3.0 |
| electronic-circuit 10/s, fast belts (1,160) | 442 | 348 (30%) | 336 (29%) | 34 (3%) | 2.6 |
| inserter 1/s (609) | 133 | 134 (22%) | 311 (51%) | 31 (5%) | 4.6 |

Each row of the box counts once: a shelf's band (its rows, and beside them) or a
channel's, east of the shelf's west column; the west strip is what lies west of it.
Channels remain the biggest share, 29 to 53%.

### Mirrored rows sharing a channel (measured, not built)

The note above proposed flipping a shelf (inputs on top) so two shelves share one
channel. On this layout it doesn't pay, and the reason is the shape of the problem,
not the router:

- **Every interior channel is already shared.** A row takes inputs at its south edge
  and sends its product out of the top, and a shelf goes above every shelf it takes
  from, so the channel between two shelves already holds the lower one's outputs and
  the upper one's inputs.
- **A stack needs a channel per shelf plus one, whichever way rows face.** Flipping a
  shelf only changes which pair a channel serves. Two shelves of the same level
  could share their input channel (one flipped below it), but the flipped shelf's
  product then leaves at its south edge and has to come back north past both shelves
  to the rows that use it.
- **Estimated on electronic circuits at 5/s:** the two cable shelves sharing their
  input channel saves at most one channel row band, 5 rows by 14 columns (70 tiles),
  and the return route takes a column the factory's full height (44 tiles) with no
  gap between rows to run in, before any routing slack. Not built.

To shrink channels, the levers left are routing-aware: thinner channels where few
lines cross (the trim already does this per channel), fewer crossings by ordering
rows on a shelf and lines in a channel, and the floorplan with a wire-length term.

### Negative results (this step)

- **Every shelf as far west as the bus lets it, always.** The first ragged version
  moved every shelf west: circuits at 5/s and on fast belts grew a column each (880
  to 924, 1,160 to 1,218), the moved shelves standing off from the shelves they take
  from. Moving a shelf only as far as the box needs, and trying all-west once at the
  end, keeps both at their old size and every gain (inserters 609 needs all-west).
- **Trimming every channel a row at once before one at a time.** It saved 5 s at 15
  circuits/s but grew logistic science from 1,364 to 1,408 (a worse local minimum).
  Reverted.

### Should compact be the default?

Not yet. It's smaller on every item request measured and meets the same flow
tests, and the search now keeps to a budget (40 s by default, the slowest request
measured taking 31 s), but (1) every factory with oil falls back to the bus, so the
default would change only some requests, with a fallback message on the rest; (2)
the evidence is seven item requests. Make it the default once fluid rows lay out
compact, after a wider sweep of requests in game. (Both done since: see below.)

## Fluids, and compact as the default (built)

Every factory with a fluid used to fall back to the bus. Now:

- **Pipes** (`tools/pipe_route.py`). A fluid line's terminals are joined one at a time
  to the tree already joined, by A* over (tile, heading): a pipe, which connects every
  way, so it stands only where no other fluid connects toward it; or a pipe-to-ground
  pair straight on, under anything, never overlapping another pair on its row or
  column (the game pairs each with the first opposite one within reach). Pairs cost
  less than the pipes they replace, so lines leave their tiles to belts. Fluids are
  routed before the belts, which go round or under them. Each layout is then checked
  as the game joins it (`tools/pipes.py`): every fluid one network, no network two
  fluids, none longer than the game's 320 tiles less the players' pipes at the ends.
- **Fluid rows and blocks** join their ports: fluid rows at the south end of their
  pipe, blocks (refining, cracking, sulfur, acid, lubricant) at their lines' west
  ends. A supplied fluid enters at the bottom and a fluid product or left-over leaves
  at the top, each through a pipe of its own, two columns from any other so the
  players' pipes beyond don't join. The flow test feeds the inputs and sinks the
  outputs, a left-over only above half full as on the bus, so refining still backs up
  as it would in a game.
- **A near-lane ingredient on the product's belt.** A fluid row's ingredient on the
  near lane (plastic's coal) rides the product belt out; a filter splitter keeps it
  in the row, and its port is side-loaded from that lane's side with a stub behind it,
  so the coal comes in on one lane. Fed from below, coal filled both lanes and plastic
  stopped (0/s); side-loaded, 8/s against the bus's 8/s.
- **Cracking through a gate**, as on the bus: in the cracking block's own row, a
  storage tank on the oil's line and a pump below it into the block's outermost line,
  wired red, running while the tank holds more than 1,000 (`factory.GATE_LEVEL`).
- **The smaller layout kept.** `factory` builds the bus too and keeps it, saying so,
  when compact is no smaller.

### The sweep (34 requests, both layouts, in game)

Every request passed `gen factory ... layout=bus --validate` and
`... layout=compact --validate` on Factorio 2.1.23 (macOS). Areas from `bp density`;
build is `gen` alone, compact including the bus it compares with. A rate above the
request is a row's last machine rounding up.

| Request | Bus | Compact | Change | In game, bus | In game, compact | Build, compact |
|---|---|---|---|---|---|---|
| electronic-circuit 5/s | 1,560 | 880 | -44% | 6.01/s | 6.01/s | 1 s |
| electronic-circuit 15/s | 4,462 | 2,847 | -36% | 15/s (window 3) | 14.9/s (window 2) | 32 s |
| electronic-circuit 5/s, assembling machine 3 | 1,480 | 800 | -46% | 6.01/s | 6/s | 1 s |
| electronic-circuit 2/s, assembling machine 1 | 756 | 475 | -37% | 2.65/s | 2.67/s | 1 s |
| electronic-circuit 10/s, fast belts | 1,840 | 1,160 | -37% | 12/s | 12/s | 2 s |
| electronic-circuit 20/s, express belts | 2,520 | 1,880 | -25% | 24/s | 24/s | 3 s |
| electronic-circuit 5/s, smelt=1 | 2,640 | 1,564 | -41% | 5.68/s | 6/s | 4 s |
| automation-science-pack 1/s | 738 | 450 | -39% | 1.21/s | 1.2/s | 1 s |
| automation-science-pack 1/s, smelt=1 | 1,463 | 900 | -38% | 1.2/s | 1.19/s | 2 s |
| logistic-science-pack 1/s | 2,373 | 1,239 | -48% | 1.25/s | 1.25/s | 14 s |
| logistic-science-pack 0.5/s, fast belts, assembling machine 3 | 1,691 | 704 | -58% | 0.835/s | 0.835/s | 3 s |
| military-science-pack 0.5/s | 2,424 | 1,196 | -51% | 0.599/s | 0.598/s | 6 s |
| inserter 1/s | 1,407 | 609 | -57% | 1.49/s | 1.5/s | 2 s |
| iron-gear-wheel 1/s | 154 | 88 | -43% | 1.49/s | 1.5/s | 1 s |
| engine-unit 1/s | 1,512 | 860 | -43% | 1.05/s | 1.05/s | 3 s |
| battery 2/s | 1,752 | 1,073 | -39% | 2.48/s | 2.45/s | 3 s |
| electric-engine-unit 0.5/s | 3,200 | 1,540 | -52% | 0.6/s | 0.6/s | 12 s |
| sulfur 5/s | 506 | 234 | -54% | 5.99/s | 5.98/s | 1 s |
| plastic-bar 5/s | 527 | 272 | -48% | 8/s | 8/s | 1 s |
| sulfuric-acid 10/s | 406 | 228 | -44% | 20.1/s | 19.8/s | 1 s |
| lubricant 10/s | 144 | 99 | -31% | 20/s | 20/s | 1 s |
| advanced-circuit 1/s | 2,244 | 1,242 | -45% | 1.13/s (window 2) | 1.16/s | 4 s |
| chemical-science-pack 0.5/s | 5,070 | 4,224 | -17% | 0.603/s | 0.6/s | 45 s |
| processing-unit 0.5/s | 8,505 | bus kept (11,505) | | 0.532/s | 0.533/s (the bus) | 46 s |
| plastic-bar 5/s, oil=1 | 1,968 | 1,078 | -45% | 5.85/s | 5.84/s | 2 s |
| sulfuric-acid 10/s, oil=1 | 1,248 | 792 | -37% | 12.9/s | 13.2/s | 2 s |
| lubricant 10/s, oil=1 | 1,127 | 700 | -38% | 15.1/s | 15/s | 1 s |
| battery 1/s, oil=1 | 2,553 | 1,566 | -39% | 1.3/s | 1.3/s | 7 s |
| advanced-circuit 1/s, oil=1 | 3,825 | 2,116 | -45% | 1.17/s (window 2) | 1.16/s | 8 s |
| chemical-science-pack 0.5/s, oil=1 | 6,832 | 6,318 | -8% | 0.601/s | 0.609/s | 45 s |
| electric-engine-unit 0.5/s, oil=1 | 4,401 | 2,461 | -44% | 0.599/s | 0.598/s | 22 s |
| flying-robot-frame 0.2/s, oil=1 | 7,028 | bus (doesn't route) | | 0.225/s | 0.225/s (the bus) | 7 s |
| processing-unit 0.5/s, oil=1 | 10,556 | bus kept (13,689) | | 0.531/s | 0.531/s (the bus) | 46 s |
| production-science-pack 0.2/s | 4,784 | bus (doesn't route) | | 0.21/s | 0.21/s (the bus) | 8 s |

Every factory with oil (`oil=1`) refines crude oil and cracks both heavy and light oil
through gates (lubricant cracks only light oil: its heavy oil is the lubricant's).
Electric engines from crude oil are the case the gate is for (NOTES.md): heavy-oil
cracking and lubricant on one heavy oil line, the lubricant first. The bus builds in 0
to 6 s.

### Should compact be the default? (decided: yes)

Yes, and it is: `gen factory` packs rows on shelves, and `layout=bus` keeps the
main bus. Compact passed everywhere it's offered (30 of 34 laid out compact, every
one at its rate), it's smaller on each of those, by 8 to 58%, and where it can't do
better it gives the bus and says why, so the default is never larger. The cost is
build time: 1 to 14 s for most factories, but about 45 s for the biggest (the 40 s
budget, plus the bus it compares with), against 0 to 6 s for the bus. `budget=`
shortens it; `layout=bus` skips it.

Still on the bus, with the reason on stderr:

- **Processing units** (both): only one row a shelf routes (14 shelves; 39 by 290
  tiles given 60 s), and a channel a shelf plus the 24-column west bus and yard cost
  more than the bus layout's taps. "the bus layout is smaller (8,505 tiles, compact 11,505)".
- **Production science** and **flying robot frames from crude oil**: "the rows don't
  route in any shelf width tried"; production science's belts (stone, circuits, rails)
  find no room for their taps and adapters.

### Negative results (this step)

- **Routing lines one at a time, with nothing reserved.** A line routed past another
  port's way in and sealed it: with cracking gated, advanced circuits from crude oil
  stopped routing at any shelf width. Each port's way in is now its own line's from
  the start (no other fluid's piece on it or beside it).
- **A* over (tile, heading) let a path come back over its own pipe**, as a pair's
  entrance: the game keeps one piece a tile, so the line was cut in two (plastic from
  crude oil, caught by the network check). A path never revisits a tile now.
- **In and out pipes chosen without looking at the pipes already routed.** Crude
  oil's input pipe went beside a water pipe, joining them (lubricant from crude oil).
- **Two more columns for every shelf led by a row with west fluid ports.** Refining's
  light oil needs them (its port sits between two others), but given to every such
  shelf, processing units at 0.5/s stopped routing at all (11,505 tiles without). Only
  a port with others two tiles above and below it gets them; sulfur, acid and
  lubricant blocks lay out smaller without (sulfuric acid 266 to 228 tiles).

## A CP-SAT floorplan (tried, not built)

Section 1's plan: rows anywhere on the grid instead of on shelves, placed by CP-SAT
and joined by the same routers. Prototyped and measured on five item requests, it
doesn't beat shelves, so it isn't in the search, and neither the model nor `ortools`
is in the code.

- **The model works.**
  - Each row is a rectangle with room around it: a column west, two east, two rows north for its lead-out, a south margin for its adapters, and a floor of rows under everything for the inputs.
  - Rows don't overlap (`NoOverlap2D`), and each consumer stands wholly north of the rows it takes from.
  - The objective is the least box area first, then the shortest wires weighted by flow: Manhattan, between output and input ports, to the bottom for a supplied item and to the top for the product.
  - It runs on one worker with a deterministic time limit, so the same rows always get the same answer; every instance solved in under 0.4 s.
  - The version tried was `ortools` 9.15.6755, which has cp313 wheels for macOS arm64 and Linux x86-64; it imports in 0.24 s warm.
- **Routing it doesn't work with compact's pieces.**
  - Those pieces assume a shelf's channel: a comb runs along one channel row and peels branches north to one shelf's ports, and the bus taps each channel.
  - Placed freely, even in the stack the model picks, a line feeding rows at several heights needs new pieces. The prototype added a spine: a splitter per consumer row, each feeding a comb south of that row.

  | Request | Shelves | Floorplan |
  |---|---|---|
  | iron-gear-wheel 1/s | 88 | 91 |
  | automation-science-pack 1/s | 450 | 450 with a comb at the spine's splitter; none with each comb started south of its row |
  | electronic-circuit 5/s | 880 | doesn't route at any margin tried (south 3 to 8, north 2 to 5) |
  | inserter 1/s | 609 | doesn't route |
  | logistic-science-pack 1/s | 1,239 | doesn't route |

- **Why it can't pay with these pieces.**
  - **The box before any routing.** For circuits at 5/s, at the least room it could ever route in (south margin 3, north 2, floor 3), the model's best is a stack 16 wide by 43 tall: 688 tiles. Shelves are 20 by 44.
  - **The heights match.** A row's south margin plus the north margin of the row below it is a channel, the same rows a shelf's channel holds. A channel holds the adapters, combs and merges the rows need: it's routing, not placement waste. The only gain on offer is the west bus strip's columns.
  - **The margins must grow before anything routes.** At south 4 and north 3 the box is already 848 tiles, and at 5 and 3 it's 928, more than shelves.
  - **So the lever is routing, not placement.** A floorplan beats shelves only with pieces that pack a channel tighter than shelves fit it.
- **Cheaper, and still open.** The west strip (bus and yard) is 16 to 30% of the box. Raw lines riding beside the rows, a column each with no yard, would take back part of it without new pieces. The three requests still on the bus (processing units, production science, robot frames from crude oil) weren't tried, since simpler requests didn't route.

## Direct insertion (built)

A producer whose product only one row uses can hand it straight to that row's
machines by inserter: no belt out of the producer's row, no channel crossing, no
merge or comb. `gen cell recipe=CONSUMER producer=PRODUCER` builds that block, and
the compact search tries it.

### Machine to machine, measured (2.1.23)

`python -m validator.measure --handover` hands copper cable from an assembling
machine 3 straight into a circuit machine through one inserter of each tier, no
research, and counts circuits (3 cable each). It writes `machine_to_machine` in
`tools/inserter_rates.json` and leaves the belt rates alone.

| Inserter | Machine to machine | Onto and off belts |
|---|---|---|
| inserter | 0.847/s | 0.86 to 0.94/s |
| fast-inserter | 2.5/s | 2.5 to 2.8/s |
| long-handed-inserter | 1.249/s | 1.18 to 1.25/s |
| bulk-inserter | 2.508/s | the same as fast |

- **One producer feeds two.** Two fast inserters taking from one cable machine (5
  cable/s) moved all of it between them, 5.003/s.
- **A consumer doesn't hoard.** A circuit machine that can't craft takes 12 cable at
  most; while crafting it holds 2 at most.
- **Backpressure is clean.** With the consumer full, the producer stops at
  `full_output` holding 12 cable, the inserter waits for source items, and nothing
  is lost.

### The cell

West to east: an input belt, its inserters, a chain of machines, output inserters,
the product belt, and the same mirrored. Down each chain producers (P) and consumers
(C) alternate, and each gap between neighbours holds the inserters that hand over.
The plan takes the shortest chain that feeds every consumer at full speed from the
handover rates, kept 15% below them as rows are: each consumer takes first from the
producer before it, then the one after it makes up the rest. The input belt's two
lanes carry the producer's ingredients and the consumer's others, and the product
belt only what the consumers make.

| Cell | Chain a side | Rated | Box | Tiles per item/s | In game |
|---|---|---|---|---|---|
| circuits from cable, assembling machine 2 | PCPCPPCP | 9/s | 13 x 31 | 44.8 | 8.99/s |
| the same, assembling machine 3 | PCPCP | 10/s | 13 x 19 | 24.7 | 10/s |
| the same, assembling machine 1 | PCPCPPCPCPPCP | 10/s | 13 x 51 | 66.3 | 9.99/s |
| inserters from gears | PC | 3/s | 13 x 7 | 30.3 | 3/s |
| transport belts from gears | PCPC | 12/s | 13 x 15 | 16.2 | 12/s |
| automation science from gears | CPC six times | 3.6/s | 13 x 71 | 256.4 | 3.62/s |

It refuses, saying why: a consumer that doesn't use the product, a fluid (processing
units from circuits), or more than two items on the input belt (engine units from
gears need steel, pipes and the gears' iron).

A cell is about as dense as the rows it replaces: circuits at 6/s in 247 tiles,
against 260 for the cable and circuit rows. What it saves is the connection
between them.

### In the compact search

- **Fusing** (`production.fuse`): each item row whose product only one other item
  row uses, and isn't the factory's product, joins that row as a cell, sized to its
  demand with the same 5% slack: one cell while one holds it, the demand split evenly
  over more otherwise, one side when one side's enough.
- **Kept only if smaller.** `factory` lays the rows out as solved first, with the
  whole budget, then the fused rows in what's left but at least a quarter of it,
  keeps the smaller, and still compares with the bus. So the default is never
  larger than without cells. `cells=0` skips the second search.
- **Why the quarter.** On a loaded machine the rows as solved took the whole budget
  for circuits at 15/s (3,192 tiles), and the cells, which lay it out in 1,710 in
  about 3 s, were never tried. With the quarter, a run where the rows again took
  the whole budget (42 s) found 1,710.

### The sweep (34 requests, in game)

Areas from `bp density`, each request built with cells (the default), with
`cells=0`, and with `layout=bus`. Every request where cells won passed `gen factory
... --validate` on Factorio 2.1.23 (macOS) at its rate; where they didn't, the layout
is the one without cells, which the #66 sweep validated (budget-bound searches
aside, below). Build is `gen` alone, compact including the bus it
compares with.

| Request | Bus | Without cells | With cells | Change | Empty share: bus, without, with | Tiles per item/s, with | In game, with cells | Build: without, with |
|---|---|---|---|---|---|---|---|---|
| electronic-circuit 5/s | 1,560 | 880 | 600 | **-32%** | 60%, 47%, 58% | 120 | 5.99/s | 1 s, 1 s |
| electronic-circuit 15/s | 4,462 | 2,847 | 1,710 | **-40%** | 54%, 51% (at 2,964), 57% | 114 | 15/s (window 2) | 32 s, 33 s |
| electronic-circuit 5/s, assembling machine 3 | 1,480 | 800 | 600 | **-25%** | 62%, 50%, 58% | 120 | 6.01/s | 1 s, 1 s |
| electronic-circuit 2/s, assembling machine 1 | 756 | 475 | 475 |  | 62%, 53%, 53% | 238 |  | 0 s, 0 s |
| electronic-circuit 10/s, fast belts | 1,840 | 1,160 | 1,000 | **-14%** | 57%, 45%, 56% | 100 | 12/s | 1 s, 2 s |
| electronic-circuit 20/s, express belts | 2,520 | 1,880 | 1,640 | **-13%** | 51%, 44%, 56% | 82 | 21/s | 2 s, 2 s |
| electronic-circuit 5/s, smelt=1 | 2,640 | 1,564 | 1,280 | **-18%** | 59%, 49%, 47% | 256 | 5.83/s | 4 s, 5 s |
| automation-science-pack 1/s | 738 | 450 | 450 |  | 63%, 52%, 52% | 450 |  | 1 s, 1 s |
| automation-science-pack 1/s, smelt=1 | 1,463 | 900 | 900 |  | 63%, 52%, 52% | 900 |  | 2 s, 3 s |
| logistic-science-pack 1/s | 2,373 | 1,239 | 1,239 |  | 65%, 41%, 41% | 1,239 |  | 14 s, 25 s |
| logistic-science-pack 0.5/s, fast belts, assembling machine 3 | 1,691 | 704 | 704 |  | 69%, 51%, 51% | 1,408 |  | 3 s, 5 s |
| military-science-pack 0.5/s | 2,424 | 1,196 | 1,196 |  | 62%, 50%, 50% | 2,392 |  | 6 s, 6 s |
| inserter 1/s | 1,407 | 609 | 576 | **-5%** | 68%, 52%, 62% | 576 | 1.5/s | 2 s, 2 s |
| iron-gear-wheel 1/s | 154 | 88 | 88 |  | 71%, 67%, 67% | 88 |  | 0 s, 0 s |
| engine-unit 1/s | 1,512 | 860 | 860 |  | 59%, 44%, 44% | 860 |  | 3 s, 3 s |
| battery 2/s | 1,752 | 1,073 | 1,073 |  | 64%, 55%, 55% | 536 |  | 2 s, 2 s |
| electric-engine-unit 0.5/s | 3,200 | 1,540 | 1,540 |  | 64%, 46%, 46% | 3,080 |  | 12 s, 21 s |
| sulfur 5/s | 506 | 234 | 234 |  | 72%, 53%, 53% | 47 |  | 0 s, 0 s |
| plastic-bar 5/s | 527 | 272 | 272 |  | 68%, 57%, 57% | 54 |  | 0 s, 0 s |
| sulfuric-acid 10/s | 406 | 228 | 228 |  | 62%, 48%, 48% | 23 |  | 0 s, 0 s |
| lubricant 10/s | 144 | 99 | 99 |  | 68%, 54%, 54% | 10 |  | 0 s, 0 s |
| advanced-circuit 1/s | 2,244 | 1,242 | 1,242 |  | 60%, 47%, 47% | 1,242 |  | 4 s, 4 s |
| chemical-science-pack 0.5/s | 5,070 | 4,268 | 4,268 |  | 59%, 64% (at 4,444), 65% (at 4,488) | 8,536 |  | 45 s, 46 s |
| processing-unit 0.5/s | 8,505 | the bus | the bus |  | 58%, 58%, 58% | 17,010 |  | 43 s, 44 s |
| plastic-bar 5/s, oil=1 | 1,968 | 1,078 | 1,078 |  | 68%, 52%, 52% | 216 |  | 2 s, 2 s |
| sulfuric-acid 10/s, oil=1 | 1,248 | 792 | 792 |  | 64%, 53%, 53% | 79 |  | 1 s, 1 s |
| lubricant 10/s, oil=1 | 1,127 | 700 | 700 |  | 68%, 51%, 51% | 70 |  | 1 s, 1 s |
| battery 1/s, oil=1 | 2,553 | 1,566 | 1,566 |  | 64%, 56%, 56% | 1,566 |  | 6 s, 6 s |
| advanced-circuit 1/s, oil=1 | 3,825 | 2,116 | 2,116 |  | 64%, 51%, 51% | 2,116 |  | 7 s, 8 s |
| chemical-science-pack 0.5/s, oil=1 | 6,832 | 6,318 | 6,318 |  | 62%, 67% (at 6,426), 66% | 12,636 |  | 45 s, 47 s |
| electric-engine-unit 0.5/s, oil=1 | 4,401 | 2,461 | 2,231 | **-9%** | 64%, 51%, 49% | 4,462 | 0.6/s | 22 s, 40 s |
| flying-robot-frame 0.2/s, oil=1 | 7,028 | the bus | the bus |  | 63%, 63%, 63% | 35,140 |  | 5 s, 6 s |
| processing-unit 0.5/s, oil=1 | 10,556 | the bus | the bus |  | 60%, 60%, 60% | 21,112 |  | 44 s, 44 s |
| production-science-pack 0.2/s | 4,784 | the bus | the bus |  | 58%, 58%, 58% | 23,920 |  | 6 s, 7 s |

The three searches that use their whole budget (circuits at 15/s, chemical science)
land on different layouts from run to run when the machine is busy: chemical science
came out between 4,224 (#66) and 4,840 tiles in six runs. Their empty shares are
from the run named.

### Where the space goes now

Counted as before (each row of the box once: a shelf's band or a channel's, east of
the shelf's west column; the west strip is what lies west of it):

| Request (box without, with) | Rows | West strip | Channels | Beside rows |
|---|---|---|---|---|
| electronic-circuit 5/s (880, 600) | 260 to 247 | 264 (30%) to 180 (30%) | 336 (38%) to 154 (26%) | 20 (2%) to 19 (3%) |
| electronic-circuit 15/s (2,847, 1,710) | 624 to 806 | 584 (21%) to 360 (21%) | 1,519 (53%) to 420 (25%) | 120 (4%) to 124 (7%) |
| electronic-circuit 5/s, assembling machine 3 (800, 600) | 208 to 247 | 240 (30%) to 180 (30%) | 336 (42%) to 154 (26%) | 16 (2%) to 19 (3%) |
| electronic-circuit 10/s, fast belts (1,160, 1,000) | 442 to 507 | 348 (30%) to 300 (30%) | 336 (29%) to 154 (15%) | 34 (3%) to 39 (4%) |
| electronic-circuit 20/s, express belts (1,880, 1,640) | 884 to 923 | 564 (30%) to 492 (30%) | 364 (19%) to 154 (9%) | 68 (4%) to 71 (4%) |
| electronic-circuit 5/s, smelt=1 (1,564, 1,280) | 524 to 511 | 248 (16%) to 466 (36%) | 632 (40%) to 260 (20%) | 160 (10%) to 43 (3%) |
| inserter 1/s (609, 576) | 133 to 168 | 134 (22%) to 164 (28%) | 311 (51%) to 182 (32%) | 31 (5%) to 62 (11%) |
| electric-engine-unit 0.5/s, oil=1 (2,461, 2,231) | 699 to 709 | 874 (36%) to 596 (27%) | 570 (23%) to 534 (24%) | 318 (13%) to 392 (18%) |

Channels fall from 19 to 53% of the box to 9 to 32% on the item factories: the cable
line, its merges and the comb into the circuit rows are gone, 129 to 1,099 tiles. The
west strip keeps its share, except from ores (248 to 466 tiles, 16 to 36%) and for
inserters (134 to 164). Electric engines from crude oil save in the west strip
instead (874 to 596 tiles); their channels stay about the same.

### Where it applies

Of the 34 requests, 15 have a producer only one row uses, and 8 lay out smaller
with it fused. All 8 fuse copper cable into circuits, and inserters at 1/s also
gears into inserters: cable feeds nothing else in those factories, three cable
machines feed two circuit machines, and a chain of five producers and three
consumers a side (PCPCPPCP) comes close to that. Where cable also feeds another
row (advanced circuits, processing units, chemical science) nothing fuses. Fused but kept as rows (larger with the cell):
circuits at 2/s on assembling machines 1, automation science (both), logistic science
(both) and electric engines from supplied oil. Robot frames from crude oil fuse
circuits but still don't route compact, so stay on the bus.

### Negative results (this step)

- **A producer that outpaces its consumers.** One gear machine (1.5/s) feeds eight
  automation science machines (0.15/s each), but in a cell a producer reaches only
  its two neighbours, so eight consumers need four producers: 12 machines in 299
  tiles of rows against 9 in 177, and the factory 680 tiles against 450.
- **Rounding multiplied.** A cell feeds every consumer flat out, so rounding consumers
  up rounds producers up too: circuits at 2/s on assembling machines 1 take 4 circuit
  machines either way, but the cell needs 6 cable machines where rows need 4 (sized
  to the 2/s drawn), 247 tiles of rows against 156 and 600 tiles against 475.
- **Emptier boxes.** A cell is itself 17 to 30% empty (the gap rows between machines
  hold only the handover inserters), so a factory with cells is smaller but its box
  emptier: circuits at 5/s 47% empty without, 58% with.
