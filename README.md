# ghostwriter

Generates Factorio 2.x blueprints, books, deconstruction and upgrade planners
through code ([Draftsman](https://github.com/redruin1/factorio-draftsman)), checks
them, and keeps a browsable library. Planned flow: generate → validate in the
real game → fix → only passing strings reach the user.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m validator.fetch 2.1.22      # headless server for validation (Linux)
.venv/bin/python -m tools.bp gen gated_requester item=iron-plate count=100 --validate
.venv/bin/python -m tools.site                  # library browser -> site/dist/index.html (open it from disk)
.venv/bin/python -m unittest discover tests
```

| Path | |
|---|---|
| `.claude/skills/blueprint/` | Claude Code skill: how to generate, check, file |
| `tools/` | codec, checks, library/book builder, CLI (`python -m tools.bp`), data dump |
| `generators/` | parametric designs, `build(**params)` |
| `library/` | decoded JSON, one file per blueprint; folders are books |
| `validator/` | in-game validation: headless server, RCON, Lua harness, diff (`bp validate`) |
| `site/` | library browser: `template.html`, built by `python -m tools.site` into `site/dist/` (gitignored) |
| `data/` | `data-raw-dump.json` from `python -m tools.dump_data` (gitignored) |
| `NOTES.md` | format facts and how each was verified |
