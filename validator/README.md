# validator (step 2)

Not built yet. Learned while verifying `NOTES.md` by hand:

- Headless servers: `factorio --create map.zip`, then
  `--start-server map.zip --rcon-port P --rcon-password X`. Two servers need
  distinct `--port` values (UDP 34197 is the default for both).
- The first Lua command over RCON only prints the "achievements" warning and
  must be sent again. `/sc` needs a space or newline before the code.
- `game.create_inventory(1)[1].import_stack(s)` returns 0 even when fields
  are dropped, so diff `export_stack()` against the input.
- `build_blueprint{..., build_mode=defines.build_mode.forced}` returns
  ghosts; `ghost.revive()` gives real entities whose
  `get_wire_connectors(false)` and control behaviour can be read back.
- Target **2.1** (the user's game): `factorio.com/get-download/latest/headless/linux64`.
  2.0 is `stable`. They disagree on some fields, so pin the user's build.
- Highest-value first job: round-trip every generator's output and diff the
  re-export. Draftsman may write keys 2.1 drops (NOTES.md).
