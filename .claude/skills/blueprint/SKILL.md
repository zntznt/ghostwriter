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
python -m tools.bp gen NAME [key=value ...] [--json]   # generator -> checked string
python -m tools.bp check SRC                          # static checks, exit 1 on error
python -m tools.bp decode SRC                         # string -> pretty JSON
python -m tools.bp encode SRC                         # JSON -> string
python -m tools.bp add SRC [--to library/BOOK]        # file into library/
python -m tools.bp book library/BOOK [--json]         # folder -> book string
python -m tools.dump_data [--factorio PATH]           # refresh data/ from the game
python -m unittest discover tests
```

SRC is a string, a file path, or `-` (stdin).

## Making something new

1. Look for an existing generator in `generators/`. Extend one with
   parameters before writing a new one. Layout is where freeform design
   fails, so prefer small, parametric, tested generator functions.
2. A new generator is `generators/<name>.py` exposing
   `build(**params) -> Blueprintable` (Draftsman). Docstring: what it does and
   how to hook it up in game.
3. Draftsman gotchas: `entities.append()` copies, so wire by `id=`; use
   `LogisticModeOfOperation` for chest circuit mode (2.1 migrates it).
   Draftsman doesn't model many 2.1 settings (`NOTES.md`). Don't fake them
   with raw keys; say what the user must set by hand in game instead.
4. `gen` runs `check` and refuses to print a string with errors. Unknown
   names are errors (typos). Unknown keys are only warnings, because
   Draftsman lags 2.1; only the game validator can confirm keys.
5. Add a test in `tests/` for anything verified, especially wires.
6. Hand the string to the user with what it needs in game (wires to connect,
   items to set). Until the validator exists (step 2), say plainly that the
   string has passed static checks only, not an in-game test.

## Filing

- `library/` holds decoded, pretty-printed JSON exactly as the game or the
  generator produced it. Folders are books (`_book.json` holds book fields;
  children keep their `index`). Strings are never stored. Build them on
  demand with `book` / `encode`.
- Only file blueprints the user has confirmed work in game, or that passed
  the validator once it exists. `add` refuses strings with check errors
  (`--force` for modded exports with names neither Draftsman nor `data/`
  knows). Expect "doesn't model" warnings on 2.1 exports; they are harmless.
- `data/` is gitignored. `python -m tools.dump_data` regenerates it.

## Explaining a string

`decode` it, then describe entities, circuit settings and `wires`
(`[src_entity, src_connector, dst_entity, dst_connector]`; connector IDs in
`NOTES.md`). Run `check` and report what it says.
