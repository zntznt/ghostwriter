# Format notes

Facts the tools and skill rely on, with how each was established. "Game"
means a headless server (free download from factorio.com) driven over RCON:
import with `LuaItemStack.import_stack`, re-export with `export_stack`, build
with `build_blueprint` + `revive`, then read the live entities.

## Verified in game (2.0.77 stable and 2.1.22 experimental)

| Fact | Evidence |
|---|---|
| Wire connector IDs: `circuit_red`=1, `circuit_green`=2, `combinator_input_red`=1, `combinator_input_green`=2, `combinator_output_red`=3, `combinator_output_green`=4, `pole_copper`=5, `power_switch_left_copper`=5, `power_switch_right_copper`=6 | `defines.wire_connector_id` printed by both servers. Identical to Draftsman 4.0.1 `WireConnectorID`. The docs list the names but not the numbers. |
| Draftsman's wires for the gated requester match the hand-built example: `[[3,1,2,1],[2,3,1,1]]` | Built in game from both strings. Live wire connectors: constant red 1 ↔ decider input red 1, decider output red 3 ↔ chest red 1. |
| Logistic chest circuit mode: `control_behavior.circuit_mode_of_operation` (0 send contents, 1 set requests, 2 none) works on **both** versions | 2.0.77 builds the chest in `set_requests` mode. 2.1.22 re-exports it as `set_requests: true, read_contents: false`. |
| `read_contents` / `set_requests` on logistic chests are **2.1-only** | 2.0.77 drops them **silently**: `import_stack` returns 0 and the chest is built in `send_contents` mode. 2.0.77 docs list only `circuit_mode_of_operation` ([2.0.77](https://lua-api.factorio.com/2.0.77/concepts/LogisticContainerBlueprintControlBehavior.html)). 2.1.22 docs list only the new keys ([latest](https://lua-api.factorio.com/latest/concepts/LogisticContainerBlueprintControlBehavior.html)). |
| A version stamp newer than the game doesn't block import | Draftsman stamps 2.1.20; 2.0.77 imported it (code 0) with content intact. |
| Books built by `tools.library` import with nesting, deconstruction and upgrade planners intact | Imported and re-exported on both servers. |
| `--dump-data` writes to `<write-data>/script-output/data-raw-dump.json`, and the game logs `Write data path: ...` at startup | `tools/dump_data.py` run against the 2.0.77 headless binary. In a portable install, write-data is the install dir, not `~/.factorio`. |

## Corrections to the planning handoff

- The handoff's "verified" requester-chest fields (`read_contents`, `set_requests`) come from the **latest (2.1)** docs. They are the 2.1 format only. The hand-built example uses them, so on 2.0 its chest never sets requests. It still imports "successfully". The example stays in `tests/fixtures/`, not `library/`. `bp check` warns about these keys.
- `import_stack` returning 0 does not mean the blueprint survived intact. Step 2's validator must diff the game's re-export against the input, not just check the return code.

## Draftsman 4.0.1 behaviour worth knowing

- `to_dict()` omits defaults (`direction: 0`, `copy_count_from_input: true`, network flags that are `true`). The game omits them too on re-export.
- `entities.append(e)` stores a copy. Wire by `id=` or index, not by the local object.
- On import it catches unknown names, misspelt keys, overlaps and bad enums. It does not catch wires to missing entities (it crashes), connectors on the wrong entity kind, out-of-range connector IDs, or wire reach. `tools/check.py` covers those.
- Unknown names are only warnings in Draftsman (mods). `bp check` treats them as errors.

## Open

- Which version the user plays. It decides whether 2.1-only fields are acceptable.
- Decider `Each(G) > Each(R)` semantics with a signal present on only one network: not exercised in game yet (needs ticking the circuit network; step 2).
