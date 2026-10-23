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
