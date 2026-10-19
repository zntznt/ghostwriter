# ghostwriter

Generates Factorio 2.x blueprints, books, deconstruction and upgrade planners
through code ([Draftsman](https://github.com/redruin1/factorio-draftsman)), checks
them, and keeps a browsable library. Planned flow: generate → validate in the
real game → fix → only passing strings reach the user.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m validator.fetch 2.1.22      # headless server for validation (Linux)
.venv/bin/python -m tools.bp gen gated_requester item=iron-plate count=100 --validate
.venv/bin/python -m tools.bp gen production item=electronic-circuit rate=5 --validate   # book of tested rows
.venv/bin/python -m tools.site                  # library browser -> site/dist/index.html (open it from disk)
.venv/bin/python -m unittest discover tests
```

| Path | |
|---|---|
| `.claude/skills/blueprint/` | Claude Code skill: how to generate, check, file |
| `tools/` | codec, checks, library/book builder, CLI (`python -m tools.bp`), data dump; `rates.py` (recipes, machines, belts, measured inserters) and `layout.py` (inserter facing, pole placement) for generators |
| `generators/` | parametric designs, `build(**params)`: `production` (a book of rows for ITEM at RATE/s), `assembly_row` (any item recipe in assemblers), `smelting_column`, `gated_requester` |
| `library/` | decoded JSON, one file per blueprint; folders are books |
| `validator/` | in-game validation: headless server, RCON, Lua harness, diff (`bp validate`) |
| `site/` | library browser: `template.html`, built by `python -m tools.site` into `site/dist/` (gitignored) |
| `data/` | `data-raw-dump.json` from `python -m tools.dump_data` (gitignored) |
| `NOTES.md` | format facts and how each was verified |

## Library site (GitHub Pages)

`.github/workflows/pages.yml` builds and publishes the library browser to
https://zntznt.com/ghostwriter/ on every push to `main`. The account's Pages
custom domain is `zntznt.com`, so zntznt.github.io/ghostwriter/ redirects there. Pull requests
run the same build without deploying. Each build runs the full test suite,
including in-game validation of `library/` in a Factorio headless server
(cached between runs). A failing blueprint blocks the deploy, so only passing
strings are published.

One-time setup: **Settings → Pages → Build and deployment → Source: GitHub
Actions**. GitHub also recommends a deployment protection rule so that only
`main` can deploy to the `github-pages` environment (Settings → Environments).
