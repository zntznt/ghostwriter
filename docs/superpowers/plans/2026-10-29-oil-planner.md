# Oil Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `production._oil` chooses refinery and cracking recipes with a mixed-integer program over measured block tiles, adding `oil=advanced` (today's plan) and `oil=coal`.

**Architecture:** One `scipy.optimize.milp` per request inside `generators/production.py`, replacing the closed form and `_root`. Per-recipe tile costs are measured once from the real `fluid_block` generator and cached. `RECIPES[oil]` is the only thing that differs between `oil=1`, `oil=advanced` and `oil=coal`.

**Tech Stack:** Python 3, Draftsman 4.0.1, scipy (brings numpy), unittest, the headless-game validator.

**Spec:** `docs/oil-planner.md`

## Global Constraints

- Output shape of `production.solve` is unchanged: `(list[Step], dict[str, float])`, petroleum gas surplus as a negative input.
- `oil=` accepts `0`, `1`, `"advanced"`, `"coal"`; anything else raises `ValueError` naming the allowed values.
- Heavy oil and light oil are produced exactly to demand; petroleum gas at least to demand.
- Machines come from the integer variables directly (no `ceil`); every block still covers its crafts at `SLACK = 1.05`.
- scipy is pinned in `requirements.txt`; only `production` imports it, lazily, inside `_oil`.
- No `tests/fixtures/default_builds.json` entry changes. If one does, stop and report it.
- Conventional commits, no AI attribution lines.

## Review Focus

1. Light-oil-only demand (e.g. `solid-fuel-from-light-oil`): basic can't make it; the plan must be advanced + heavy cracking with petroleum surplus. Pinned in Task 2's balance cases.
2. Solver float noise: a machine count returned as 5.9999999 must become 6 and still cover the crafts. Pinned by Task 2's balance invariant (`rate >= demand * SLACK`).
3. Misspelled `oil=` values (`"Advanced"`, `"crude"`) must raise, not silently plan crude. Pinned in Task 2.
4. `oil=1` on a request with no oil at all must print nothing. Pinned in Task 4.
5. A coal liquefaction block whose only heavy oil is its own output may never start in game. Pinned by Task 3's in-game step, with the fallback of dropping `oil=coal`.

---

### Task 1: scipy and measured block tiles

**Files:**
- Modify: `requirements.txt`
- Modify: `generators/production.py` (imports; new `_tiles` after `_root`'s current spot)
- Test: `tests/test_tools.py` class `Production`

**Interfaces:**
- Produces: `production._tiles(recipe: str) -> tuple[float, float]` = `(tiles per machine, fixed tiles per block)`, cached.

- [ ] **Step 1: Set up the environment**

```bash
cd ~/repos/ghostwriter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt scipy==1.18.1
```

Add to `requirements.txt` (after the Draftsman line):

```
# The oil planner's mixed-integer program (generators/production.py); brings numpy.
scipy==1.18.1
```

Check `.venv` is git-ignored (`git status --short` must not list it); if it isn't, add `.venv/` to `.gitignore`.

- [ ] **Step 2: Write the failing test**

In `tests/test_tools.py`, class `Production`:

```python
    def test_oil_block_tiles_are_measured(self):
        # Bounding-box areas at 1 and 3 machines (2.1.23 / Draftsman 4.0.1): basic 48, 128;
        # advanced 90, 240; cracking 32, 80.
        self.assertEqual(production._tiles("basic-oil-processing"), (40, 8))
        self.assertEqual(production._tiles("advanced-oil-processing"), (75, 15))
        self.assertEqual(production._tiles("heavy-oil-cracking"), (24, 8))
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_tools.Production.test_oil_block_tiles_are_measured -v`
Expected: FAIL with `AttributeError: module 'generators.production' has no attribute '_tiles'`

- [ ] **Step 4: Implement**

In `generators/production.py`, add `import functools` next to `import math`, and add after `_oil`:

```python
@functools.cache
def _tiles(recipe: str) -> tuple[float, float]:
    """(tiles per machine, fixed tiles) of a fluid_block of RECIPE, measured as bounding-box
    area at 1 and 3 machines. Over a pair because coal liquefaction alternates (a coal belt
    gap every second machine)."""
    from tools.density import density

    one, three = (density(fluid_block.build(recipe=recipe, machines=n).to_dict())[0]["area"] for n in (1, 3))
    per = (three - one) / 2
    return per, one - per
```

- [ ] **Step 5: Run it to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_tools.Production.test_oil_block_tiles_are_measured -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt generators/production.py tests/test_tools.py
git commit -m "feat(production): measure oil block tiles from the real generator"
```

---

### Task 2: the milp over crude recipes, `oil=advanced`, and moved cracking tests

This task flips `oil=1` for petroleum-only requests to basic oil processing, so the tests that exist to cover cracking move to `oil="advanced"` in the same commit.

**Files:**
- Modify: `generators/production.py` (`RECIPES` constant, `solve` validation and `_oil` call, `_oil` body; delete `_root`)
- Modify: `tests/test_tools.py` (lines ~629, 657, 671, 690, 980), `tests/test_compact.py:22`, `tests/test_validator.py:347,364`

**Interfaces:**
- Consumes: `_tiles(recipe)` from Task 1.
- Produces: `production.RECIPES: dict` keyed by `1`, `"advanced"` (Task 3 adds `"coal"`); `production._oil(need: dict[str, float], oil=1) -> tuple[list[Step], dict[str, float]]`. Steps are in `RECIPES[oil]` order (refineries before cracking); `Step.demand` is crafts/s, `Step.rate` is `machines * crafts per machine`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tools.py`, class `Production`, replace `test_oil_balance` with:

```python
    def test_oil_balance(self):
        # oil=advanced is the old fixed chain: 10 plastic/s needs 100 petroleum gas/s, refining
        # 100/97.5 crafts/s with all heavy and light oil cracked down; water and crude follow.
        steps, inputs = production.solve("plastic-bar", 10, oil="advanced")
        self.assertAlmostEqual(inputs["crude-oil"], 100 * 100 / 97.5, places=6)
        self.assertAlmostEqual(inputs["water"], 135.897, places=3)
        self.assertEqual([s.item for s in steps[:3]],
                         ["advanced-oil-processing", "heavy-oil-cracking", "light-oil-cracking"])
        self.assertEqual(steps[0].params["machines"], 6)  # 1.03 crafts/s at 0.2 per refinery

    def test_oil_picks_basic_for_petroleum_only(self):
        # Fewer tiles: 12 basic refineries (488 tiles) against advanced with cracking (649).
        steps, inputs = production.solve("plastic-bar", 10, oil=1)
        self.assertEqual([(s.item, s.params["machines"]) for s in steps if s.generator == "fluid_block"
                          and s.item in production.RECIPES[1]], [("basic-oil-processing", 12)])
        self.assertAlmostEqual(inputs["crude-oil"], 100 * 100 / 45, places=3)
        self.assertNotIn("water", inputs)
        steps, _ = production._oil({"heavy-oil": 0, "light-oil": 0, "petroleum-gas": 3})
        self.assertEqual([(s.item, s.params["machines"]) for s in steps], [("basic-oil-processing", 1)])

    def test_oil_picks_advanced_for_mixed_demand(self):
        steps, _ = production._oil({"heavy-oil": 5, "light-oil": 20, "petroleum-gas": 20})
        self.assertEqual([(s.item, s.params["machines"]) for s in steps],
                         [("advanced-oil-processing", 2), ("heavy-oil-cracking", 1)])

    def test_oil_plans_balance(self):
        # Independent of the solver: recompute net oil from the recipes and the planned crafts.
        from tools import rates

        cases = [({"heavy-oil": 0, "light-oil": 0, "petroleum-gas": p}, oil) for p in (3, 100) for oil in (1, "advanced")]
        cases += [({"heavy-oil": 20, "light-oil": 0, "petroleum-gas": 0}, 1),  # lubricant
                  ({"heavy-oil": 0, "light-oil": 30, "petroleum-gas": 0}, 1),  # light oil only: basic can't
                  ({"heavy-oil": 5, "light-oil": 20, "petroleum-gas": 20}, 1)]
        for need, oil in cases:
            steps, inputs = production._oil(need, oil)
            made = dict.fromkeys(production.OIL, 0.0)
            for s in steps:
                self.assertGreaterEqual(s.rate, s.demand * production.SLACK - 1e-6, (need, oil, s))
                r = rates.recipe_any(s.item)
                for f in production.OIL:
                    made[f] += s.demand * (r.results.get(f, 0) - r.fluids.get(f, 0))
            self.assertAlmostEqual(made["heavy-oil"], need["heavy-oil"], places=6, msg=(need, oil))
            self.assertAlmostEqual(made["light-oil"], need["light-oil"], places=6, msg=(need, oil))
            self.assertAlmostEqual(made["petroleum-gas"] - need["petroleum-gas"],
                                   -inputs.get("petroleum-gas", 0), places=6, msg=(need, oil))

    def test_unknown_oil_value_is_refused(self):
        for oil in ("crude", "Advanced", 2):
            with self.assertRaisesRegex(ValueError, "advanced"):
                production.solve("plastic-bar", 1, oil=oil)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_tools.Production -k oil -v`
Expected: FAIL (`oil="advanced"` isn't refused yet, `RECIPES` doesn't exist, basic is never picked).

- [ ] **Step 3: Implement**

In `generators/production.py`, after `CRACKING`:

```python
# The recipes each oil= value plans with: oil=1 picks the fewest tiles among them,
# oil=advanced is the crude-efficient chain (and the one with cracking to test).
RECIPES = {1: ("advanced-oil-processing", "basic-oil-processing", *CRACKING),
           "advanced": ("advanced-oil-processing", *CRACKING)}
```

In `solve`, first lines after the `machine` check:

```python
    if oil not in (0, *RECIPES):
        raise ValueError(f"oil is one of 0, {', '.join(map(str, RECIPES))}, not {oil!r}")
```

and change the `_oil` call to `_oil({f: demand.get(f, 0) for f in OIL}, oil)`.

Replace the whole of `_oil` and delete `_root`:

```python
def _oil(need: dict[str, float], oil=1) -> tuple[list[Step], dict[str, float]]:
    """Oil processing and cracking for heavy oil, light oil and petroleum gas per second,
    from RECIPES[oil]: the fewest measured tiles (a mixed-integer program over crafts,
    machines and which blocks exist), ties to less feedstock. Heavy and light oil are made
    exactly; petroleum gas left over is reported as a negative input."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    names = RECIPES[oil]
    n = len(names)
    rs = [rates.recipe_any(r) for r in names]
    per_machine = np.array([fluid_block.plan(recipe=r).crafts for r in names])
    per, fixed = zip(*map(_tiles, names))
    feed = [r.fluids.get("crude-oil", 0) + r.ingredients.get("coal", 0) for r in rs]
    net = np.array([[r.results.get(f, 0) - r.fluids.get(f, 0) for r in rs] for f in OIL])
    eye, zero, big = np.eye(n), np.zeros((n, n)), 10_000  # big: more machines than any block gets
    # x = crafts/s, machines, block exists; rows: net oil, machines cover crafts, no machines without a block.
    a = np.block([[net, np.zeros((len(OIL), 2 * n))],
                  [-np.diag(SLACK / per_machine), eye, zero],
                  [zero, eye, -big * eye]])
    lo = [need[f] for f in OIL] + [0] * n + [-np.inf] * n
    hi = [need["heavy-oil"], need["light-oil"], np.inf] + [np.inf] * n + [0] * n
    cost = [*(1e-6 * np.array(feed)), *per, *fixed]  # 1e-6 a unit of feedstock: never worth a tile
    res = milp(cost, constraints=LinearConstraint(a, lo, hi), integrality=[0] * n + [1] * 2 * n,
               bounds=Bounds(0, [np.inf] * 2 * n + [1] * n))
    if res.status != 0:
        raise ValueError(f"no oil plan for {need} with oil={oil}: {res.message}")
    crafts, machines = res.x[:n], np.round(res.x[n:2 * n]).astype(int)
    steps, inputs = [], {}
    for name, r, c, m, p in zip(names, rs, crafts, machines, per_machine):
        if m == 0:
            continue
        steps.append(Step(name, float(c), "fluid_block", {"recipe": name, "machines": int(m)}, 1, float(m * p)))
        for k, amount in [*r.fluids.items(), *r.ingredients.items()]:
            if k not in OIL:
                inputs[k] = inputs.get(k, 0) + c * amount
    surplus = float(net[OIL.index("petroleum-gas")] @ crafts) - need["petroleum-gas"]
    if surplus > 1e-6:
        inputs["petroleum-gas"] = -surplus
    return steps, inputs
```

- [ ] **Step 4: Move the cracking tests to `oil="advanced"`**

These tests exist to cover cracking, its gate and its layout; with `oil=1` they'd now plan basic oil processing and test nothing about cracking. Change only the oil value, keep every assertion:

```bash
rg -n '"oil": 1\b|oil=1\)' tests/test_tools.py tests/test_compact.py tests/test_validator.py
```

- `tests/test_tools.py`: `test_chain_packs_every_row` params, `test_chain_pools_intermediates_only` (`oil=1` → `oil="advanced"`), `FACTORIES[1]` (comment stays "refinery, cracking, a fluid row"), `test_a_row_can_feed_two_lines` (`processing-unit ... oil=1`).
- `tests/test_compact.py:22`: `OIL = [{"item": "advanced-circuit", "rate": 1, "oil": "advanced"}]`.
- `tests/test_validator.py:347,364`: `{"item": "advanced-circuit", "rate": 1, "oil": "advanced"}`.

Leave `test_oil_surplus_is_reported` (`lubricant`, `oil=1`): heavy oil demand still picks advanced. Leave the `"oil=1"` hint in the supplied-only test at ~570.

- [ ] **Step 5: Run the oil tests, then the affected classes**

Run: `.venv/bin/python -m unittest tests.test_tools.Production -v`
Expected: all PASS.
Run: `.venv/bin/python -m unittest tests.test_tools tests.test_compact -v`
Expected: all PASS. A failure in a moved test means `oil="advanced"` doesn't reproduce the old plan: compare its `solve` steps with `git stash; ...; git stash pop` before touching any assertion.

- [ ] **Step 6: Commit**

```bash
git add generators/production.py tests/test_tools.py tests/test_compact.py tests/test_validator.py
git commit -m "feat(production): plan oil recipes by measured tiles, add oil=advanced"
```

---

### Task 3: `oil=coal`

Kept in its own commit so it can be reverted alone if it fails in game.

**Files:**
- Modify: `generators/production.py` (`RECIPES`)
- Test: `tests/test_tools.py` class `Production`

**Interfaces:**
- Consumes: `RECIPES`, `_oil` from Task 2 (its input loop already adds item ingredients such as coal, and steam).

- [ ] **Step 1: Write the failing test**

```python
    def test_oil_from_coal_needs_no_crude(self):
        steps, inputs = production._oil({"heavy-oil": 0, "light-oil": 0, "petroleum-gas": 30}, "coal")
        self.assertEqual(steps[0].item, "coal-liquefaction")
        self.assertNotIn("crude-oil", inputs)
        self.assertGreater(inputs["coal"], 0)
        self.assertGreater(inputs["steam"], 0)
```

Also add `({"heavy-oil": 0, "light-oil": 0, "petroleum-gas": 30}, "coal")` to the `cases` list in `test_oil_plans_balance`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_tools.Production -k oil -v`
Expected: FAIL with `ValueError: oil is one of 0, 1, advanced, not 'coal'`

- [ ] **Step 3: Implement**

```python
RECIPES = {1: ("advanced-oil-processing", "basic-oil-processing", *CRACKING),
           "advanced": ("advanced-oil-processing", *CRACKING),
           "coal": ("coal-liquefaction", *CRACKING)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_tools.Production -k oil -v`
Expected: PASS

- [ ] **Step 5: Validate in game**

Run: `.venv/bin/python -m tools.bp gen production item=plastic-bar rate=1.5 oil=coal --validate`
Expected: every row's flow test passes, including the coal liquefaction block.

If the liquefaction block never starts (no heavy oil to begin with), stop: don't commit, revert Step 3 and the test, and report it. The spec moves `oil=coal` to a follow-up.

- [ ] **Step 6: Commit**

```bash
git add generators/production.py tests/test_tools.py
git commit -m "feat(production): oil=coal plans coal liquefaction with cracking"
```

---

### Task 4: the stderr oil line

**Files:**
- Modify: `generators/production.py` (new `oil_line` after `_oil`)
- Modify: `generators/factory.py` (`build`, before the `return`)
- Test: `tests/test_tools.py` classes `Production` and `Factory`

**Interfaces:**
- Consumes: `solve`, `RECIPES`.
- Produces: `production.oil_line(item, rate, oil=0, **kw) -> str` (`kw` = `solve`'s other keyword arguments); `""` unless `oil == 1` and crude oil is refined.

- [ ] **Step 1: Write the failing tests**

In `Production`:

```python
    def test_oil_line_names_the_plan_and_its_crude(self):
        self.assertEqual(production.oil_line("plastic-bar", 10, oil=1),
                         "oil: basic-oil-processing x12; crude oil 222/s (advanced with cracking would use 103/s)")
        self.assertEqual(production.oil_line("electronic-circuit", 5, oil=1), "")  # no oil in it
        self.assertEqual(production.oil_line("plastic-bar", 10, oil="advanced"), "")
```

In `Factory` (add `import contextlib, io` to the test module imports):

```python
    def test_build_prints_the_oil_line(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            factory.build(item="plastic-bar", rate=3, oil=1, layout="bus")
        self.assertIn("oil: basic-oil-processing", err.getvalue())
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_tools -k oil_line -v`
Expected: FAIL with `AttributeError: ... has no attribute 'oil_line'`

- [ ] **Step 3: Implement**

In `generators/production.py`:

```python
def oil_line(item, rate, oil=0, **kw) -> str:
    """The oil=1 plan in one line: refinery recipes, machines and crude oil per second, and
    the crude oil=advanced would use. Empty without crude oil refining."""
    if oil != 1:
        return ""
    steps, inputs = solve(item, rate, oil=1, **kw)
    if not inputs.get("crude-oil"):
        return ""
    line = "oil: " + ", ".join(f"{s.item} x{s.params['machines']}" for s in steps if s.item in RECIPES[1])
    line += f"; crude oil {inputs['crude-oil']:.3g}/s"
    advanced = solve(item, rate, oil="advanced", **kw)[1]["crude-oil"]
    if abs(advanced - inputs["crude-oil"]) > 1e-6:
        line += f" (advanced with cracking would use {advanced:.3g}/s)"
    return line
```

In `generators/factory.py` `build`, before `compact = _compact(...)`:

```python
    if line := production.oil_line(item, rate, machine=machine, belt=belt, smelt=smelt, furnace=furnace,
                                   raw=raw, oil=oil, research=research):
        print(line, file=sys.stderr)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_tools -k oil_line -v` and `-k build_prints_the_oil_line`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add generators/production.py generators/factory.py tests/test_tools.py
git commit -m "feat(factory): report the oil plan's crude oil on stderr"
```

---

### Task 5: the model against reality, docs, full suite

**Files:**
- Test: `tests/test_tools.py` class `Factory`
- Modify: `docs/layout-optimizer.md` (the paragraph under the first benchmark table), `docs/oil-planner.md` (status line)

- [ ] **Step 1: Write the test**

```python
    def test_chosen_oil_plan_is_no_larger_built(self):
        # The milp's tile estimate leaves out the factory's water and gate lines: check the
        # pick against the built factory.
        from tools.density import density

        for rate in (3, 10):
            area = {oil: density({"blueprint": factory_built(item="plastic-bar", rate=rate, oil=oil)[2]})[0]["area"]
                    for oil in (1, "advanced")}
            self.assertLessEqual(area[1], area["advanced"], rate)
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m unittest tests.test_tools.Factory.test_chosen_oil_plan_is_no_larger_built -v`
Expected: PASS. If it fails, stop and report both areas: the spec's objective is wrong at factory level, and the fix is a design decision, not a test change.

- [ ] **Step 3: Validate basic oil processing in game**

Run: `.venv/bin/python -m tools.bp gen production item=plastic-bar rate=0.3 oil=1 --validate`
Expected: every flow test passes (one basic oil processing block).

- [ ] **Step 4: Docs**

In `docs/layout-optimizer.md`, replace `Every factory with oil (\`oil=1\`)` with
`Every factory with oil (\`oil=1\` when this was measured, \`oil=advanced\` since docs/oil-planner.md)`.

In `docs/oil-planner.md`, replace `Status: design, not built.` with `Status: built.` (and drop `oil=coal` from the spec's caller section if Task 3 was reverted, moving it under Later).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m unittest discover tests`
Expected: all PASS, and `git status --short tests/fixtures` empty (no `default_builds.json` change).

- [ ] **Step 6: Commit**

```bash
git add tests/test_tools.py docs/layout-optimizer.md docs/oil-planner.md
git commit -m "test(factory): check the oil pick against the built factory"
```
