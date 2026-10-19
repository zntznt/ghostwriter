---
name: blueprint
description: Generate, check, decode and file Factorio 2.x blueprints, blueprint books, deconstruction planners and upgrade planners in this repo. Use whenever the user asks for a Factorio blueprint/book/planner string, wants one decoded, explained, changed or added to the library.
---

# Factorio blueprints

## Core rule

Never hand-write blueprint JSON or strings. Every string comes from code:
a generator in `generators/` (Draftsman), or the codec in `tools/` applied to
a string the game produced. Hand-written JSON drifts: field names change
between game versions, and wire connector IDs are easy to get wrong.

Target game: **Factorio 2.1** (the user's version). Read `NOTES.md` before
relying on any format detail. It records what has been verified in game and
where Draftsman 4.0.1 lags behind 2.1.

## Commands (repo root, venv active: `pip install -r requirements.txt`)

```
python -m tools.bp gen NAME [key=value ...] --validate   # generator -> checked, validated string
python -m tools.bp validate SRC...                      # in-game validation (headless server)
python -m tools.bp check SRC                          # static checks, exit 1 on error
python -m tools.bp decode SRC                         # string -> pretty JSON
python -m tools.bp encode SRC                         # JSON -> string
python -m tools.bp add SRC [--to library/BOOK]        # file into library/
python -m tools.bp book library/BOOK [--json]         # folder -> book string
python -m tools.site                                  # rebuild the library browser (site/dist/index.html)
python -m tools.dump_data [--factorio PATH]           # refresh data/ from the game
python -m validator.fetch 2.1.22                      # headless server for validation (Linux)
python -m validator.harvest                           # refresh tools/game_settings.json from the game
python -m unittest discover tests
```

SRC is a string, a file path, or `-` (stdin).

## Making something new

1. Look for an existing generator in `generators/`. Extend one with
   parameters before writing a new one. Layout is where freeform design
   fails, so prefer small, parametric, tested generator functions.
   - **Any item recipe in assemblers:** use `assembly_row`, e.g. `recipe=electronic-circuit machine=assembling-machine-1`. It sizes machines to the belts, inserters to the measured rates, and spreads ingredients over one or two input belts. When nothing fits, it says why.
   - **Smelting:** use `smelting_column`.
   - **Rates and layout:** take rates from `tools/rates.py` (recipes, machine speeds, belt lanes, measured inserter rates) and columns and poles from `tools/layout.py`. Never hard-code a speed.
2. A new generator is `generators/<name>.py` exposing
   `build(**params) -> Blueprintable` (Draftsman). Docstring: what it does and
   how to hook it up in game. If it has circuit logic, it also exposes
   `circuit_tests(**params) -> list[Case]` (`validator/circuit.py`): cases
   that inject signals and assert outputs or chest requests. Cover the
   boundaries: below, at and above every threshold, plus unrelated signals.
   Make the cases follow the parameters (an "unrelated" item must differ
   from `item`). If it moves items (belts, inserters, machines), it also
   exposes `flow_tests(**params) -> list[Flow]` (`validator/flow.py`): feed
   belt lanes, drain output belts, and assert the rated items/s. Compute the
   rate from the same prototype data the layout uses, and size the window so
   whole crafts can't skew it (`generators/smelting_column.py`).
3. Draftsman gotchas: `entities.append()` copies, so wire by `id=`; use
   `LogisticModeOfOperation` for chest circuit mode (2.1 migrates it); place
   `railgun-turret` by `position`, not `tile_position` (wrong footprint).
   For 2.1 settings Draftsman doesn't model (read ingredients/fuel, splitter
   and pump filters, turret priority flags, per-network selections, decider
   `else_outputs`...), use `tools.settings.game_settings(entity, {path: value})`.
   It only accepts keys the game itself wrote for that entity
   (`tools/game_settings.json`). Never put raw keys into `extra_keys` yourself.
   If a setting isn't in the registry, say what the user must set by hand.
4. `gen` runs `check` and refuses to print a string with errors. Unknown
   names are errors (typos). Unknown keys are only warnings, because
   Draftsman lags 2.1; the validator is what confirms keys.
   Always use `gen --validate`. A string that hasn't passed validation does
   not go to the user as working. If no Factorio binary is available, say
   so and label the string "static checks only".
5. Add a test in `tests/` for anything verified, especially wires.
6. Hand the string to the user with what it needs in game (wires to connect,
   items to set). Say what validation covered: import, settings, wires,
   building on a test surface, and the circuit and flow cases by name.
   Inputs in circuit tests are synthetic (injected signals, script power),
   and flow tests feed belts by script with no research on the force (one
   item per inserter swing, all recipes unlocked for the run), so name what
   the user still has to check in a real factory.
7. If validation fails: read the error, fix the generator, re-run. A key
   the game drops is either a typo or something Draftsman writes in an older
   form. For the latter, verify the game's form in a round trip, then add a
   migration to `validator/validate.py` with a note in `NOTES.md`.

## Filing

- `library/` holds decoded, pretty-printed JSON exactly as the game or the
  generator produced it. Folders are books (`_book.json` holds book fields;
  children keep their `index`). Strings are never stored. Build them on
  demand with `book` / `encode`.
- Only file blueprints the user has confirmed work in game, or that passed
  the validator including circuit and flow tests for any logic or throughput.
- A filed blueprint with circuit logic or a rated throughput gets
  `NAME.tests.toml` next to its `NAME.json` (`[[case]]` and `[[flow]]`
  tables; format: `validator/README.md`). For a generated one, write its
  `circuit_tests()` / `flow_tests()` for the filed parameters, with entity
  numbers from the same build, and check the filed JSON equals `build()`.
  For one the user imports, ask what it should do, then write cases for the
  boundaries. CI runs them before every publish, and the site lists them.
  `add` refuses strings with check errors
  (`--force` for modded exports with names neither Draftsman nor `data/`
  knows). Expect "doesn't model" warnings on 2.1 exports; they are harmless.
- Give every filed blueprint a `label` and a `description` saying what it
  does and how to hook it up. The browser and the in-game tooltip show both.
- After filing, rebuild the browser with `python -m tools.site`.
- `data/` is gitignored. `python -m tools.dump_data` regenerates it.

## Explaining a string

`decode` it, then describe entities, circuit settings and `wires`
(`[src_entity, src_connector, dst_entity, dst_connector]`; connector IDs in
`NOTES.md`). Run `check` and report what it says.
