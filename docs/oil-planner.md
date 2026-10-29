# Oil planner: choosing refinery recipes by measured tiles

Status: design, not built. Branch `feat/lp-planner`.

## Problem

`production._oil` balances heavy oil, light oil and petroleum gas with a closed
form over one fixed chain: advanced oil processing, then heavy and light oil
cracking. It never considers basic oil processing or coal liquefaction, and it
can't: with two refinery recipes there is a choice of mix, and the closed form
(`_root`) only works while every quantity is linear in one variable.

The cost of that is largest on small requests. For 3 petroleum gas/s, basic oil
processing is one refinery block; the fixed chain is a refinery plus two cracking
blocks, gates and a water line.

## What changes for a caller

- `oil=1` (crude): the planner chooses among advanced oil processing, basic oil
  processing, heavy oil cracking and light oil cracking.
- `oil=advanced` (new): advanced oil processing with cracking only, exactly today's
  `oil=1` plan. The crude-efficient choice, and what the tests of the cracking gate and
  its layout use, since `oil=1` no longer cracks for petroleum-only requests.
- `oil=coal` (new): coal and steam in; coal liquefaction plus cracking. Crude is
  never mixed in. Any other `oil=` value raises `ValueError` naming the allowed ones.
- Output shape is unchanged: `Step`s for `fluid_block` rows, inputs including water
  and steam, petroleum gas surplus as a negative input. `factory`, `compact`, the
  site and chain tests consume it as before.
- Some builds change (for example petroleum-only requests may use basic oil
  processing and no cracking). See [Accepting changed builds](#accepting-changed-builds).

## Model

One mixed-integer program per request, solved with `scipy.optimize.milp`, inside
`production._oil` (which keeps its signature; `_root` is removed).

For each allowed recipe r:

- `c_r >= 0`, continuous: crafts per second.
- `m_r`, integer `>= 0`: machines, with `m_r >= c_r * SLACK / p_r`, where `p_r` is
  crafts per machine per second from `fluid_block.plan(recipe=r).crafts`.
- `u_r` in {0, 1}: the block exists, with `m_r <= M * u_r` (M = a bound on machines
  from the demand).

Constraints on net production (results minus ingredients, over all recipes):

- heavy oil and light oil: equal to the demand. A surplus would back up and stop
  the refineries; cracking can always absorb an excess, so this is feasible.
- petroleum gas: at least the demand; the excess is reported as a negative input.

Objective, minimised:

    sum_r ( tiles_per_machine_r * m_r + block_tiles_r * u_r ) + eps * crude (or coal) used

- `tiles_per_machine_r` and `block_tiles_r` are measured, not assumed: build the
  block with the real `fluid_block` generator at 1 and 3 machines and take the
  bounding-box area (`bp density`). Half the difference is the per-machine area;
  the rest of the 1-machine area is the block's fixed area. Measured on 2.1.23 /
  Draftsman 4.0.1, areas for 1..6 machines are linear for the crude recipes
  (basic 40, advanced 75, both cracking 24 tiles a machine) but alternate for coal
  liquefaction (75, 120, ...: a coal belt gap every second machine), which is why
  it's measured over a pair; the average is within 23 tiles of the real area. Measured on first use per recipe and cached in
  memory (`functools.cache`), so importing `production` stays cheap.
- `eps` is small enough never to outweigh a tile; it breaks ties toward the plan
  that uses less feedstock, so equal-tile plans don't flip between solver versions.

After solving, machines are `m_r` directly (no `ceil` for oil steps); `Step`s and
non-oil inputs (water, steam, crude, coal) are built from `c_r` as today.

A solver status other than optimal raises `ValueError` naming the request.

### Consequence: space over crude

Minimising tiles puts every request that needs only petroleum gas (plastic,
sulfur, sulfuric acid) on basic oil processing, at every rate: about 4.4 tiles per
petroleum gas/s against about 5.2 for advanced oil processing with cracking (no
water, no cracking blocks). It burns 2.2 times the crude (100 crude oil makes 45
petroleum gas, against 97.5 advanced). Requests needing heavy or light oil still
get advanced oil processing. This is chosen deliberately: tiles are the cost.

To keep the trade visible, `factory.build` prints the oil plan to stderr for `oil=1`
(where it already reports its layout), for example

    oil: basic-oil-processing x4; crude oil 70/s (advanced with cracking would use 33/s)

The comparison is `oil=advanced`'s plan, so the fix for a factory short of crude is
to pass that. A crude weight (`crude_cost=` tiles per crude oil/s) is the follow-up only
if a middle ground is wanted.

What the model does not see: water and gate lines at the factory level (bus and
compact layouts). The density check below is what catches it if that matters.

## Dependency

`scipy` in `requirements.txt`, pinned like Draftsman (it brings numpy). Wheels
exist for macOS arm64 and Linux x86-64. Only `production` imports it.

## Testing

In `tests/test_tools.py`, class `Production`:

1. Small petroleum gas demand (3/s) picks basic oil processing: one block, no
   cracking. The case a continuous LP gets wrong.
2. Mixed heavy, light and petroleum demand picks advanced oil processing with cracking.
3. Balance invariant on every planned case: recompute net production per fluid from
   the recipes and the returned crafts; heavy and light equal the demand, petroleum
   gas meets it. Independent of the solver.
4. `oil=coal` uses coal liquefaction and needs no crude.
4b. `oil=advanced` reproduces today's plan: `test_oil_balance`'s numbers unchanged.
5. An unknown `oil=` value raises `ValueError` listing the allowed values.
6. The stderr oil line names the recipes, machines and crude per second, and the
   crude an advanced-with-cracking plan would use.
7. The estimate agrees with reality: for a few petroleum-only requests, the factory
   built with `oil=1` is no larger (`bp density`) than with `oil=advanced`.

Existing tests that exercise cracking and its gate (`advanced-circuit` and
`processing-unit` with `oil=1` in `test_tools`, `test_compact`, `test_validator`) move
to `oil=advanced` with their assertions unchanged; `test_oil_balance` too, with a new
`oil=1` twin expecting basic oil processing.

In game (`--validate`, flow tests):

- a small petroleum gas request (basic oil processing block);
- an `oil=coal` request (coal liquefaction: heavy oil both in and out of one block).
  A liquefaction block needs heavy oil to start, and its only heavy oil is its own
  output, so it may never start. If this fails in game, `oil=coal` moves to a follow-up
  and this change ships crude only.

Full suite: `python -m unittest discover tests`, and the CI shards on push.

## Accepting changed builds

A changed test expectation or `tests/fixtures/default_builds.json` entry is
accepted only if the new build (a) is no larger in tiles and (b) passes in game.
Anything else is a regression. Every changed fixture is listed with the reason and
the tile difference. Expected: none. No `default_builds.json` request passes `oil=`,
and the moved tests keep their expectations under `oil=advanced`. The `oil=1` rows of
the benchmark tables in `docs/layout-optimizer.md` were measured with the old chain;
they get a note saying so (they are `oil=advanced` now).

## Later

Recipe choice beyond oil, and Space Age by-product chains, reuse the same model
with more recipes; the generators would also need to build multi-output and
probability recipes, which `rates.recipe` and `fluid_block.plan` refuse today.
