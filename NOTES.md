# Format notes

**Target: Factorio 2.1** (the user's game). Facts the tools and skill rely
on, with how each was established. "Game" means a headless server (free
download from factorio.com; `latest` is 2.1, `stable` is 2.0) driven over
RCON: import with `LuaItemStack.import_stack`, re-export with `export_stack`,
build with `build_blueprint` + `revive`, then read the live entities.

## Verified in game (2.1.22; 2.0.77 where noted)

| Fact | Evidence |
|---|---|
| Wire connector IDs: `circuit_red`=1, `circuit_green`=2, `combinator_input_red`=1, `combinator_input_green`=2, `combinator_output_red`=3, `combinator_output_green`=4, `pole_copper`=5, `power_switch_left_copper`=5, `power_switch_right_copper`=6 | `defines.wire_connector_id` printed by 2.1.22 and 2.0.77. Identical to Draftsman 4.0.1 `WireConnectorID`. The docs list the names but not the numbers. |
| Draftsman's wires for the gated requester match the hand-built example: `[[3,1,2,1],[2,3,1,1]]` | Built in game from both strings. Live wire connectors: constant red 1 ↔ decider input red 1, decider output red 3 ↔ chest red 1. |
| Logistic chest circuit mode: 2.1 writes `read_contents` / `set_requests` and also reads Draftsman's `circuit_mode_of_operation` (0 send contents, 1 set requests, 2 none) | 2.1.22 re-exports `circuit_mode_of_operation: 1` as `set_requests: true, read_contents: false`. |
| On **2.0** only `circuit_mode_of_operation` works; 2.0 drops `read_contents` / `set_requests` silently | 2.0.77: `import_stack` returns 0 and the chest is built in `send_contents` mode. Docs: [2.0.77](https://lua-api.factorio.com/2.0.77/concepts/LogisticContainerBlueprintControlBehavior.html), [2.1](https://lua-api.factorio.com/latest/concepts/LogisticContainerBlueprintControlBehavior.html). |
| `import_stack` returning 0 does not mean the blueprint survived intact | The 2.0 case above. The validator must diff the game's re-export against the input. |
| The version stamp doesn't block import | Strings stamped 2.0.72, 2.0.77 and 2.1.20 all imported on 2.1.22 (and 2.0.77) with content intact. |
| Books built by `tools.library` import with nesting, deconstruction and upgrade planners intact | Imported and re-exported on both servers. |
| On import the game silently drops: wires beyond reach, wires to a connector the entity lacks (e.g. connector 3 on a chest), keys it doesn't know, and invalid enum values (`circuit_mode_of_operation: 7`). `import_stack` returns 0 for all of these. Unknown entity names make it return -1, and the entity is dropped. | `tests/test_validator.py`, 2.1.22. |
| 2.1 migrates all three `circuit_mode_of_operation` values on logistic chests: 0 → defaults, 1 → `set_requests: true, read_contents: false`, 2 → `read_contents: false` | Validator round trips, 2.1.22. |
| Defaults the 2.1 export omits: `direction: 0`, signal `type: "item"`, `quality: "normal"`, `copy_count_from_input: true`, network flags set to `true` | Validator round trips of the hand-built example and of Draftsman output. |
| `--dump-data` writes to `<write-data>/script-output/data-raw-dump.json`, and the game logs `Write data path: ...` at startup | `tools/dump_data.py` run against the 2.0.77 and 2.1.22 headless binaries. In a portable install, write-data is the install dir, not `~/.factorio`. |

## Draftsman 4.0.1 vs real 2.1.22 exports

124 placeable entities were placed in a 2.1.22 server, with every writable boolean control-behaviour setting flipped and every network selection set. 119 exported; the 5 failures were the 4 rolling-stock types and the space platform hub. Of the 119, Draftsman:

- accepts 64 cleanly.
- doesn't model keys in 51. Examples: `read_ingredients`, `read_fuel` (crafting machines), `set_filter` (splitters, pumps), `set_priority_list` / `set_ignore_unlisted_targets` (turrets), `input_networks` / `output_networks` (most types), `else_outputs` (decider), `circuit_enabled` (power switch, offshore pump), `read_contents` / `read_empty_slots` (all chests), `read_charge`, `read_launched`, `circuit_read_sensor`, `circuit_close_signal`.
- crashes on car, tank, spidertron (`TypeError ... 'mode'`).
- has no entity class for `thruster`, though the name is in its data.

Samples live in `tests/fixtures/game-2.1.22/`. Not separated: which of these keys are new in 2.1 and which Draftsman lacks for 2.0 too.

Consequences for this repo:
- `bp check` reports unknown keys and Draftsman crashes as **warnings**, because it can't tell a typo from a real 2.1 key. Unknown **names** are errors unless the game (the `data/` dump, else Draftsman's data) knows them.
- Generators can only set what Draftsman models, and Draftsman may write older keys that 2.1 migrates (as with the chest) or drops. `bp gen --validate` catches the drops; migrations verified so far: logistic chest mode.

## Draftsman behaviour worth knowing

- `to_dict()` omits defaults (`direction: 0`, `copy_count_from_input: true`, network flags that are `true`). The game omits them too on re-export.
- `entities.append(e)` stores a copy. Wire by `id=` or index, not by the local object.
- It resolves wire endpoints by list position, so `entity_number` must run 1..n (game exports always do). `check` enforces this.
- It doesn't check wires to missing entities (it crashes), connectors on the wrong entity kind, out-of-range connector IDs, or wire reach. `tools/check.py` covers those.

## Open

- Your exact 2.1.x build. `python -m tools.dump_data` on your machine records it in `data/factorio-version.txt`.
- Decider `Each(G) > Each(R)` semantics with a signal present on only one network: not exercised in game. The validator builds but doesn't tick circuits.
