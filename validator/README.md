# validator

Checks blueprints, books and planners in a real headless Factorio server.

```
python -m validator.fetch 2.1.22              # once: Linux headless server -> .factorio-headless/
python -m tools.bp validate SRC...            # strings, files or library folders
python -m tools.bp gen NAME k=v --validate    # generate, check, validate, run circuit and flow tests, print
python -m validator.harvest                   # refresh tools/game_settings.json (after a game update)
python -m unittest tests.test_validator       # ~30 s; skipped without a Factorio binary
```

On macOS or Windows, point it at the game itself: `--factorio PATH` or
`$FACTORIO`. Match the version you play, because 2.0 and 2.1 disagree on
some fields.

## What it does

`validator/server.py` starts a throwaway server:
- It uses a temp write-data dir via `--config`, so your saves, mods and lock file are never touched.
- It uses free ports and loads the `scenario/` harness.
- It talks to the harness over RCON (`validator/rcon.py`).

`scenario/control.lua` then, per string:

1. `import_stack`. A result of 1 means rejected; -1 means imported with errors (e.g. unknown entity names, which are dropped).
2. `export_stack`. The **parse diff** compares the input with this re-export. A key the game dropped or changed is one it didn't understand: a typo, a key from another version, or a value it rejects. `import_stack` still returns 0 in those cases, so this diff is the real check.
3. For every blueprint, including those inside books:
   - Prepare a test site from the entities' own prototypes:
     - **Surface:** one whose properties meet every entity's `surface_conditions`. Pressure 0 for thrusters, gravity 0 for crushers, gravity ≥ 1 for elevated rails. If entities still can't stand there, try the real planet surfaces; Fulgora's lightning rods only build on Fulgora itself.
     - **Base tile:** concrete or space-platform foundation, whichever fits the most entities.
     - **Per entity:** the tiles its `tile_buildability_rules` need, rotated to its direction (water in front of an offshore pump, empty space behind a thruster), and a resource of the right category under each mining drill or pumpjack.
   - `build_blueprint` with absolute snapping on a 2-tile grid, so every entity lands exactly at its blueprint position.
   - Revive the ghosts, in up to 4 passes. A ghost that didn't build is placed again on the next pass, so rolling stock goes on after its rails exist.
   - Blueprint what was built. The **build diff** compares that with the re-export: entities that weren't built, wires that didn't form, settings that didn't stick.

Reporting rules (`validate.py`):
- **Dropped defaults:** a dropped key holding a default the game is known to omit (`DEFAULTS`) is silent. One that only looks default-ish is a warning.
- **Added keys:** keys the game adds are ignored.
- **Migrations:** keys the game renames on import (`MIGRATIONS`) are mapped first. Each entry must be verified in game.
- **Placement:** collisions within the blueprint are errors. These are warnings, and their build isn't checked:
  - an entity that can't stand even alone on the prepared site
  - a ghost that can't be built because the blueprint lacks something it needs, such as elevated rails without their rail supports
- **Tiles** are compared as a set, because the game re-orders them on export.
- **Train stops:** a stop with an empty name gets a random backer name when built. That isn't reported as a difference.

## Circuit tests

A generator with circuit logic declares `circuit_tests(**params)` next to `build()`. It returns `validator.circuit.Case`s; see `generators/gated_requester.py`. Each case:

- `Inject(entity, connector, signals)` sets a constant combinator wired by script to that connector. There is no reach limit, so it fits anywhere. Injectors a case doesn't mention go quiet.
- The game runs `ticks` ticks (default 10).
- `Expect(entity, connector, signals=...)` checks the exact non-zero signals on that network.
- `Expect(entity, requests=...)` checks the exact logistic requests of a requester or buffer chest. These are the chest's compiled requests, so they include what the circuit sets.

`gen --validate` runs them once structural validation passes. Everything is powered by a global electric network on the test surface, so the tests cover logic, not power or layout. Inputs are synthetic: a roboport's network contents is just a signal set you inject.

The server runs with `auto_pause: false`. A server with no players otherwise sits paused, which is invisible until you need ticks.

## Flow tests

A generator that moves items declares `flow_tests(**params)` next to
`build()`. It returns `validator.flow.Flow`s; see
`generators/smelting_column.py`. Each case:

- `Feed(entity, lane, item)` keeps one lane of a belt full from its back end. `lane` is `"left"` or `"right"` as seen along the belt.
- `drain` lists belts that are emptied every tick; what leaves them is counted.
- `fluids=[FluidFeed(entity, fluid)]` attaches an unlimited source (a script-placed infinity pipe) on a free tile beside the named entity, usually the end of a pipe. `side="west"` picks the tile, which keeps a source from touching a neighbouring line. With `sink=True` it drains instead.
- `expect` may name fluids. Their rate is what the machines made, from the force's fluid production statistics, in units/s.
- The game runs `warmup` seconds (default 60), the counts restart, then it runs `window` seconds (default 120).
- `expect` maps items to rates in items/s. Each measured rate must be within `tolerance` (default 3%) of its target. On failure, the error lists every built entity that isn't working, by status (`inserter: no_power`, `stone-furnace: no_fuel`, ...).

`gen --validate` runs them once structural validation passes. A generator that
returns a book of other generators' blueprints (`production`) exposes
`parts(**params)`, and each distinct child runs its own generator's tests.
With `chain_tests(**params)` it also runs the whole book as one blueprint:

- Every row is built side by side in one blueprint, and every pole network is powered.
- What the book makes is passed from producers to consumers through a pool. Drains and fluid production add to it; a `Feed(..., pooled=True)` or `FluidFeed(..., pooled=True)` takes from it. Only what the book needs from outside is fed without limit.
- `caps` maps a pooled item or fluid to the most the pool holds. At the cap its drains stop and its outputs back up, as a full belt or pipe would. The chain uses 20 s of output (at least 5).
- Pooled fluid feeds draw in the order listed; cracking comes last, so it gets only surplus.
- `at_least=True` checks only the lower bound, since rows are sized with slack.

`run_flow_tests` also returns a `Measured` per case: every window's rates (items at the drains, fluids made), what wasn't working at the end, and which window passed. `rate(name)` is the last window's, the most settled. `gen --validate` prints the measured rates; with `at_least`, that's what the blueprint can do, not just whether it meets the target.

`settle=N` measures up to N windows and passes at the first that meets `expect`. A factory fills its belts only as fast as its rows' slack allows, and rows near the bottom take more than their share until their own belts are full. `FluidFeed(..., sink=True, level=0.5)` drains a line only down to half full, so users along it still get some: a left-over outlet. They
run at game speed 100, so a 3-minute case takes a few seconds.

Differences from circuit tests:

- **Power** comes only through the blueprint's own poles. A script-wired pole joins them to an unlimited source. An inserter outside every supply area, or a pole off the network, shows up as a shortfall.
- **Research:** the force has none, so inserters carry one item per swing. A design that keeps up here keeps up with any research.
- **Recipes:** all are unlocked for the run, then relocked. A furnace won't take iron plates until Steel processing is researched, and flow tests measure throughput, not research.

A window counts each machine's crafts to within one. Give it enough crafts
that this stays well under the tolerance: the smelting column uses 50 crafts,
under 2%.

## Flight tests

`validator/flight.py` flies a space platform blueprint: `run_flight(server, data, Flight(...))`, or
`python -m tools.bp fly SRC --runs 3 --seconds 600 [--fuel] [--supply item=n,...] [--research series=n,...]`.

- The blueprint is built around a fresh platform's hub: its origin is the hub's centre (the 8x8 hub is already there). Foundation comes from its tiles, or from under each entity if it has none. It is built through ghosts, so the game's placement rules hold: a thruster needs empty space behind it.
- Supplies go into the hub. The platform leaves `origin` for `destination` (default: the solar system edge toward the shattered planet) on its own thrusters. `fuel=True` keeps thrusters full by script, to test defence before a design makes its own fuel.
- The game is paused and advanced by exact tick counts until the hub dies or `seconds` pass. A `Flown` holds the furthest km, top speed, every death as "entity <- cause", the hub's cargo (asteroid chunks included), and a log every 10 game seconds.
- The test force has no research unless the flight sets some: `research={"railgun-damage": 4}` researches levels 1 to 4 of the technologies named `railgun-damage-<n>`, so the game applies its own effects. It is undone after the flight. An unknown series, or more levels than a finite one has, is an error.
- Flights differ run to run, even each first flight in a fresh server, so judge a design over several.

## Game-settings harvest

`harvest.py` asks the game which control-behaviour settings Draftsman 4.0.1
can't express. It places every placeable entity and flips every writable
boolean and network selection listed in that version's Lua API docs. Then it
exports each entity and keeps the keys Draftsman parks in `extra_keys`. The
result, `tools/game_settings.json`, is what `tools.settings.game_settings()`
lets generators set. `test_game_settings_registry_round_trips` validates
every entry on every entity it lists, and fails if the registry was
harvested for a different game version than the server's.

## Inserter measurement

`python -m validator.measure` times every inserter tier on every belt tier with
no research. It measures both directions: off a full, moving belt into a
chest, and from a full chest onto a moving belt, with two inserters per rig
so the belt is never the limit. It writes `tools/inserter_rates.json`, which
`tools/rates.py` serves to generators. Rows keep a 15% margin below these
rates, since a chest takes anything and a machine only what it needs, and
flow tests check the result. `test_inserter_rates_were_measured_on_this_version`
fails after a game update until it is re-run.

## Tests for library blueprints

A filed blueprint keeps its tests in a file next to it, since imported ones
have no generator and a generator's defaults can drift from what was filed.
`library/logistics/gated-requester.json` has
`library/logistics/gated-requester.tests.toml`, which holds circuit cases in
TOML:

```toml
[ids]                 # optional names for entity_numbers
chest = 1
decider = 2

[[case]]
name = "surplus: request"
inject = [{ entity = "decider", connector = "combinator_input_green", signals = { iron-plate = 150 } }]
expect = [{ entity = "chest", requests = { iron-plate = 100 } }]
```

Flow cases go in `[[flow]]` tables (`library/smelting/` has one):

```toml
[ids]
ore_left = 143
plates = 1

[[flow]]
name = "48 furnaces: 15 iron-plate/s"
feed = [{ entity = "ore_left", lane = "left", item = "iron-ore" }]
drain = ["plates"]
expect = { iron-plate = 15 }
window = 160          # optional, like warmup and tolerance
fluids = [{ entity = "pipe_end", fluid = "water" }]   # optional
```

A file holds `[[case]]` tables, `[[flow]]` tables or both
(`validator/tests_file.py`).

- `bp validate FILE` runs them. `bp validate library/FOLDER` runs those of every tested child, each on its own blueprint.
- CI runs all of them in game before publishing (`test_library_tests_pass`).
- Without a game, `tests/test_tools.py` checks that every tests file parses and that it only names entities, connectors, lanes and items that exist.
- The site lists each entry's case names: "Circuit logic tested in game" and "Throughput measured in game".

## Not covered yet

- Item requests (modules, fuel): ignored in the build diff because nothing delivers them.
- Mods: the server loads only base and the DLC.

## Server facts this relies on

- RCON requests and responses each travel as a single packet, tested to 3 MB both ways. No chunking is needed.
- With a scenario loaded, the first `/sc` runs directly. The "achievements disabled, repeat the command" prompt only appears on plain saves.
- factorio.com answers Python's default User-Agent with 403, so `fetch.py` sends its own.
