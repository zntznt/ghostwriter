# ghostwriter

Generates Factorio 2.x blueprints, books, deconstruction and upgrade planners
through code ([Draftsman](https://github.com/redruin1/factorio-draftsman)), checks
them, and keeps a browsable library. Planned flow: generate → validate in the
real game → fix → only passing strings reach the user.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m validator.fetch 2.1.23      # headless server for validation (Linux; elsewhere the installed game is found)
.venv/bin/python -m tools.bp gen gated_requester item=iron-plate count=100 --validate
.venv/bin/python -m tools.bp gen factory item=electronic-circuit rate=5 --validate      # rows on shelves, all connected, products handed straight over (budget=SECONDS, default 40)
.venv/bin/python -m tools.bp gen factory item=electronic-circuit rate=5 layout=bus --validate   # the same rows on a main bus
.venv/bin/python -m tools.bp gen production item=electronic-circuit rate=5 --validate   # the same rows as a book
.venv/bin/python -m tools.bp gen cell recipe=electronic-circuit producer=copper-cable --validate   # cable machines handing cable straight to circuit machines
.venv/bin/python -m tools.bp gen factory item=electronic-circuit rate=5 | .venv/bin/python -m tools.bp density - --rate 5   # how it spends its tiles
.venv/bin/python -m tools.bp plan platform railguns=16 guns=16 thrusters=1 cargo_bays=24 fuel=1 reserve=1 power=2 promethium=1 ammo_feed=1   # a platform's promethium trip: where to turn, what to load
.venv/bin/python -m tools.bp plan platform ... --verify 8 --shock 8   # fly the plan whole 8 times, 8 railguns killed as each turns
.venv/bin/python -m tools.bp plan platform ... --save   # file that plan beside the platform's blueprint in library/ (the site shows it)
.venv/bin/python -m tools.site                  # library browser -> site/dist/index.html (open it from disk)
.venv/bin/python -m unittest discover tests
```

| Path | |
|---|---|
| `.claude/skills/blueprint/` | Claude Code skill: how to generate, check, file |
| `tools/` | codec, checks, library/book builder, CLI (`python -m tools.bp`), data dump; `rates.py` (recipes, machines, belts, measured inserters) and `layout.py` (inserter facing, pole placement) for generators; `compact.py` (the compact factory layout) with `shelves.py`, `belts.py` and `route.py` (a grid belt router) |
| `generators/` | parametric designs, `build(**params)`: `factory` (ITEM at RATE/s, every row connected: packed on shelves, or `layout=bus` on a main bus; with each producer only one row uses, then each shared one, tried as cells with the rows that take them, `cells=0` to skip), `production` (the same rows as a book), `assembly_row` (item recipes in assemblers), `cell` (producers handing their product straight to consumers by inserter), `fluid_row` (recipes with one fluid: chemical plants, fluid assemblers), `fluid_block` (several fluids: oil processing, cracking, sulfur, acid, lubricant), `smelting_column`, `gated_requester` |
| `library/` | decoded JSON, one file per blueprint; folders are books; a platform's `NAME.plan.json` is its trip plan |
| `validator/` | in-game validation: headless server, RCON, Lua harness, diff (`bp validate`) |
| `site/` | library browser: `template.html`, built by `python -m tools.site` into `site/dist/` (gitignored) |
| `data/` | `data-raw-dump.json` from `python -m tools.dump_data` (gitignored) |
| `NOTES.md` | format facts and how each was verified |
| `docs/` | design notes: `layout-optimizer.md` (where the generators spend their tiles, and how to optimise it) |

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
