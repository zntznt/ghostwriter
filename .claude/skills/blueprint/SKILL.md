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
python -m tools.bp fly SRC --runs 3 [--fuel] [--supply item=n,...] [--research railgun-damage=4,...] [--start 0.4]  # fly a space platform toward the shattered planet
python -m tools.bp check SRC                          # static checks, exit 1 on error
python -m tools.bp decode SRC                         # string -> pretty JSON
python -m tools.bp encode SRC                         # JSON -> string
python -m tools.bp add SRC [--to library/BOOK]        # file into library/
python -m tools.bp book library/BOOK [--json]         # folder -> book string
python -m tools.trip [RESULT] [--bays N ...] [--aboard a,b]  # where a promethium trip should turn round (platform-route)
python -m tools.site                                  # rebuild the library browser (site/dist/index.html)
python -m tools.dump_data [--factorio PATH]           # refresh data/ from the game
python -m validator.fetch 2.1.23                      # headless server for validation (Linux)
python -m validator.harvest                           # refresh tools/game_settings.json from the game
python -m unittest discover tests
```

SRC is a string, a file path, or `-` (stdin).

## Making something new

1. Look for an existing generator in `generators/`. Extend one with
   parameters before writing a new one. Layout is where freeform design
   fails, so prefer small, parametric, tested generator functions.
   - **"Make X per second":** use `factory item=X rate=N` (same options as `production` below). It lays every row on a main bus and connects them: inputs enter at the bottom of the bus (a belt per item, or more past a belt's worth, and a pipe per fluid, each on its own row), the product leaves at the top, and any left-over fluid leaves there too. `--validate` runs the whole factory in game from those inputs. Tell the user the inputs and rates from the description, and that a left-over fluid must be used or stored or the refineries stop. Use `production` when the user wants the rows separately.
   - **The rows alone:** `production item=X rate=N`. It walks the recipe tree and sizes rows for every intermediate, returning a book. Options: `machine=` (fastest assembler allowed), `belt=`, `smelt=1` (ores in, with `furnace=`), and `raw=a,b` (items to supply rather than make). Recipes with one fluid ingredient get fluid rows; sulfur, sulfuric acid and lubricant get fluid blocks. With `oil=1`, petroleum gas, light oil and heavy oil come from refineries plus cracking, balanced so nothing but petroleum gas is left over, and any leftover is named in the book. Without `oil=1` they're supplied. Water, crude oil, and items with no recipe of the same name (solid fuel) are always supplied. Rows are sized for 5% more than the demand. `gen production ... --validate` also runs the chain test: the whole book as one, each row's products feeding the rows that use them, and the target rate must come out. Tell the user what to supply (the book description), that the rows aren't connected to each other, and with cracking, to crack only surplus (a pump switched by tank level).
   - **A space platform toward the shattered planet:** `platform railguns=N guns=N thrusters=N` (railguns at the front for big and huge asteroids only, gun turrets behind, thrusters at the back; the blueprint's origin is the hub's centre). Test it with `python -m tools.bp fly SRC --runs 3`; `python -m tools.optimise platform-shape` searches the counts, and `platform-research` flies the best design at several research levels (flights take `research=`; the test force has none otherwise). Power, ammo and fuel are supplied by script so far: say so. The hub rebuilds what asteroids destroy from its own inventory once `construction-robotics` is researched (`--research construction-robotics=1 --supply railgun-turret=20,space-platform-foundation=4000,...`), and repairs with repair packs in it; `platform-repair` measures both. With the hub's 59 slots, foundation runs out first: `cargo_bays=N` adds 20 hub slots a bay (beside the hub, joined to it), and 8 of them full of foundation kept 32/32/1 alive 600 s 0.4 out in 7 of 8 flights (`platform-foundation`); `margin=N` (spare foundation columns at the side ends) didn't help. Robots can't work on a platform (roboports need pressure). Flights count what the script supplies and what the hub rebuilds (`Flown.budget()`, per second); `platform-budget` measures the filed platform, and NOTES.md sets out what a platform can make on board and what must stay cargo. `fuel=1` (one thruster, layout rows) makes the thruster's fuel and oxidizer on board: two asteroid collectors in the railgun row feed a chunk belt to crushers and chemical plants east of the hub, and thrust settles at what one plant of each makes (about 0.62 km a tick). Its flights get no fuel by script and start primed and moving (`Flight.primed`, `start_speed`): standing still it meets too few asteroids to start. It holds itself where promethium is, but from the solar system edge it often never starts: say so, and that power and ammo are still by script. `reserve=1` (with `fuel=1`) adds a storage tank on each of the thruster's west inputs; a tank shares its line's fill, so it is a reserve with no pump, filled before leaving (its flights start with both full; in a game they fill while it waits in orbit). Flights take `script_fuel=1` to fly a `fuel=1` layout on fuel by script, a control for what the module costs. `power=N` (one thruster) adds N fusion plants in a row south-west of the hub, each a reactor, two generators and a cryogenic plant cooling the coolant, with an accumulator and cells by belt and inserter from the hub to every reactor (a rebuilt reactor starts empty: give the hub spare plant parts, and research construction-robotics). Use `power=2` where promethium is: railguns take power first, as reactors do, and one reactor collapses once they refill at more than 90 MW. Its flights get no stand-in power: they start as readied in orbit (`Flight.charged`: accumulators and railguns full, each reactor's slot full of cells; `coolant`: cold fluoroketone in the loops) with 50 cells in the hub. Tell the user to do the same before leaving (a reactor needs power to start), and that `Flown.budget()` gives `generated MW`, `fusion cells/h` and `brownout s` (seconds a reactor was short of power). `Flown.budget()` also gives what a flight used per 1,000 km flown: compare designs at different speeds on that, not on seconds lived. Flights take `magazine=` for the gun turrets (default uranium rounds, which can't be made in space; piercing rounds can, and do as well at the edge but not where promethium is: `platform-ammo`). `side_guns=N` stands N gun turrets in a column at each outer edge, behind the railguns' row (up to 5 a side on the full platform); they didn't save foundation (`platform-sides`). `promethium=1` (with `fuel=1`) has the collectors take promethium chunks too and sends them into the hub, one a slot (a chunk stacks to 1). Flights take `reverse=1` (the same stretch flown back toward the edge) and `take=` (items the script empties from the hub each second and counts, so room doesn't cap them); `platform-route` measures what the whole platform uses and brings in per 1,000 km along the route. `python -m tools.trip` plans a trip from it: with 8 bays only short trips near the edge fit, and hub slots bind (railgun ammo at 10 a stack, chunks at 1); say so. `rockets=N` (layout rows) stands N rocket turrets in a row behind the gun turrets for the big asteroids and keeps the railguns to the huge ones: 3 rockets kill a big asteroid at stronger explosives 10, at a hundredth of a hub slot each against a railgun shot's tenth (flights stock rockets by script, and take research `MIX_RESEARCH`). Lasers only hurt small asteroids and cost more power than the slots they save (NOTES.md). Rockets don't save slots either (a railgun's line kills small and medium asteroids in passing), but width does: `platform-mix` flies the full platform at 32, 24, 16, 12 and 8 railguns (gun turrets in equal number), and `python -m tools.trip .optimise-cache/platform-mix.result.json --mode M` plans a trip from one design; 16 railguns bring back about three times the baseline's chunks and still live at 0.2: recommend it. Up to 24 `cargo_bays` fit its width (28 would push the fuel module off the foundation, so `build` refuses); each column pushes the module toward the side end, where asteroids eat it, so stock its spares (`BAY_MODULE_SPARES`). Flights count bays lost by distance from the hub. `platform-bays` measures 8, 16 and 24 bays: with the module's spares, the trip model brings back 100, 212 and 306 chunks. `ammo_feed=1` (layout rows, not with rockets or side guns) has the hub feed every turret: filtered inserters on its north face, two belts up through a gap in the gun turrets and one along a row between the railguns' poles and the gun turrets (the railguns stand two rows further forward). Its flights get no ammo by script: each turret is loaded once from the hub (`Flight.loaded`, as in orbit), what leaves the hub counts as used (`Flight.feed`), and `Flown.budget()` gives `dry turret s` (turret-seconds with no ammo). The promethium platform is filed in `library/platforms/` (`PROMETHIUM_PLATFORM`: 16/16/1, 24 bays, `fuel`, `reserve`, `power=2`, `promethium`). Hand it over with: ready it in orbit (tanks full, reactors full of cells, loops of cold fluoroketone, accumulator and railguns charged), research construction-robotics, space-platform-thruster and cryogenic-plant, stock the hub as NOTES.md lists (foundation, uranium magazines and railgun ammo the most, and the module's and plant's spares), turn round before 0.1 of the route (the best trip turns at 0.018), and say that nothing carries ammo from the hub to the turrets yet: flights stock them by script, so the player must feed them.
   - **Several fluids** (oil processing, cracking, sulfur, sulfuric acid, lubricant): use `fluid_block`. Machines stand in a row with one line per fluid on each side, joined by pipe-to-ground so lines never touch. Items ride belts through gaps between machine pairs.
   - **One fluid ingredient** (plastic, batteries, explosives, solid fuel, processing units, electric engines, rocket fuel): use `fluid_row`. Machines face a shared middle pipe, and the product leaves on the inner belt's far lane.
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
4. `gen` runs `check` and refuses to print a string with errors. That includes a label over 200 bytes or a description over 500: the game cuts them silently. Unknown
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
