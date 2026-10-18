# validator

Checks blueprints, books and planners in a real headless Factorio server.

```
python -m validator.fetch 2.1.22              # once: Linux headless server -> .factorio-headless/
python -m tools.bp validate SRC...            # strings, files or library folders
python -m tools.bp gen NAME k=v --validate    # generate, check, validate, run circuit tests, print
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

## Game-settings harvest

`harvest.py` asks the game which control-behaviour settings Draftsman 4.0.1
can't express. It places every placeable entity and flips every writable
boolean and network selection listed in that version's Lua API docs. Then it
exports each entity and keeps the keys Draftsman parks in `extra_keys`. The
result, `tools/game_settings.json`, is what `tools.settings.game_settings()`
lets generators set. `test_game_settings_registry_round_trips` validates
every entry on every entity it lists, and fails if the registry was
harvested for a different game version than the server's.

## Not covered yet

- Circuit tests for strings that don't come from a generator (`bp validate` only checks structure).
- Item requests (modules, fuel): ignored in the build diff because nothing delivers them.
- Mods: the server loads only base and the DLC.

## Server facts this relies on

- RCON requests and responses each travel as a single packet, tested to 3 MB both ways. No chunking is needed.
- With a scenario loaded, the first `/sc` runs directly. The "achievements disabled, repeat the command" prompt only appears on plain saves.
- factorio.com answers Python's default User-Agent with 403, so `fetch.py` sends its own.
