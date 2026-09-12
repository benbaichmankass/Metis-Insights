# M20 · U3a — the scalp target was UNSWEEPABLE, not unswept

> **Doc status:** `live` · category `research` · 2026-09-12 · MI-278 unit U3a ·
> object `WO-20260912-M20-ACTIVE-TRADE-MANAGEMENT-HOLD-WINNERS-LONGER` ·
> cycle `CY-20260906-TRADING-TRUTH`
>
> **Tier-1 research tooling.** Touches `scripts/backtest_ict_scalp.py` and its
> test. **No unit file, no `config/`, no `src/`, no order path.** The live
> `tp_at_r: 1.5` is **unchanged on every leg** — this builds the runner that
> makes a proposal *possible*, and the proposal itself is Tier-3 and is not
> made here.

## 1. What this unit found before it built anything

U3's assignment was the counterfactual for the mechanism U2 implicates. It could
not start, and **the reason is the finding**.

The coverage matrix (`docs/research/exit-refinement-coverage.json`) carries 8
`ict_scalp_*` rows whose `bracket_geometry` cell reads **`pending`** (7) or
`blocked:no_free_lane_candle_feed` (1), with the reason:

> *"Not swept in the 2026-08-20 run (scope was the trend/pullback/squeeze fleet).
> Crypto — the free `data.binance.vision` lane DOES cover these, so this is
> genuinely pending, not blocked."*

**The feed half of that is correct and is not disturbed here.** What is
understated is the word `pending`, which a reader takes to mean *schedulable,
nobody scheduled it*. MEASURED 2026-09-12, **no runner in the fleet could
produce those cells**, and the obstacle was three deep:

| # | producer | why it cannot sweep the scalp target | basis |
|---|---|---|---|
| 1 | `scripts/research/e35_bracket_geometry_sweep.py` | refuses the family **by design** — gates on `fam in (donchian, pullback, squeeze)`, emits `out_of_scope_family` | **dispatched run `34677990760`** planned `0 job(s); 7 not scheduled (out_of_scope_family=7)` — observed, not read off the source |
| 2 | `scripts/research/m20_fleet_exit_sweep.py` | passes **no target flag** on its `scalp` branch (its own comment: *"tp_at_r / timeout come from YAML / harness defaults"*). `--tp-r` appears only on the `fvg` branch and in the live-parity cap block, and in **both** it is a config-exact passthrough of a single `cfg` value — **never a grid** | read at `scripts/research/m20_fleet_exit_sweep.py:511,585` |
| 3 | **the root** — `scripts/backtest_ict_scalp.py` | exposed **no target CLI at all**: **30** `add_argument` calls, **none** a target; `tp_at_r` came from YAML only | grep with the count stated so the negative carries a denominator |

So even a caller willing to pass a grid had nothing to pass it to. **The scalp
target was not unswept. It was unsweepable.**

⚠️ **This re-grades no cell.** `pending` remains the honest status. What changes
is what a reader should expect it to cost to clear — which was the whole point
of adjudicating the row into the matrix's `conditions_verdicts`.

## 2. A second finding that SIMPLIFIES the work: the scalp target is unclamped

Before designing a grid I had to know whether a swept target is reachable in
production, because a grid above a venue clamp measures a book production cannot
run — the *"six arms shipping inert"* class `m20_fleet_exit_sweep` documents at
its own `--tp-cap-pct` default.

**MEASURED, with a positive control:**

| probe | `ict_scalp.py` | `trend_donchian.py` (control) |
|---|--:|--:|
| clamp references (`tp_venue_cap` / `TP_VENUE_CAP` / `_TP_SENTINEL_CAP_PCT` / `0.099`) | **0** | **6** |

`CLAMPING_FAMILIES` = `{donchian, pullback, fade, squeeze}` and
`CLAMPING_UNIT_MODULES` = `{trend_donchian, htf_pullback_trend_2h,
fade_breakout_4h, squeeze_breakout_4h}` — **`scalp` is in neither**, and a
whole-tree grep found **no other hardcoded `0.099`** and **nothing on the order
path** (`src/units/accounts/`, `src/core/`, `src/runtime/orders.py`).

**So a scalp target grid is reachable in production at any value**, and the
`--tp-cap-pct` plumbing every other family needs is **not** needed here. That is
a simplification, and it is stated with its control because a *negative* result
about a clamp is exactly the kind that must not be taken on silence.

## 3. What was built

### `--tp-at-r` (default `None` = byte-for-byte the old behaviour)

The override is written into **`cfg_overrides`**, so the **LIVE**
`order_package()` still computes the bracket — the harness never derives a
target of its own and the run stays config-exact in every other respect. That is
the whole design: `run_backtest` already does
`cfg = {"symbol": ..., "timeframe": ..., **cfg_overrides}` and reads
`pkg["tp"]` back from the live unit.

Two decisions were given **one owner** each, because the first draft inlined
both and my own mutation testing showed the tests were asserting a *copy*:

- **`resolve_tp_at_r_override(raw)`** — raises `TpOverrideError` at or below
  zero. At or below zero the target sits at or behind entry, which is not a book
  production can run: **refusing beats measuring it**.
- **`bank_rung_state(bank_frac, bank_at_r, effective_tp_at_r)`** — **four
  states, never collapsed**: `no_ladder` · `provable_no_op` ·
  `rung_below_target` · **`unknown` (*we could not establish the ceiling* —
  never a pass)**.

### Why `bank_rung_state` is part of *this* change and not a separate tidy-up

`ict_scalp` is a fixed-bracket strategy, so a bank rung at or above the leg's
`tp_at_r` is a **provable** no-op — the TP check returns on the same bar and
`bank_frac*tp + (1-bank_frac)*tp == tp`. Such an arm reports a clean *"no
change"* that is **indistinguishable from "tested and lost"**.

The harness already documented that trap in a comment. **`--tp-at-r` is what
makes a comment insufficient:** before it, the ceiling was the constant `1.5`
and an author could check the grid once by hand; now the ceiling is a *swept*
quantity, so the **same** rung is inert in one cell of a grid and live in the
next. The run therefore stamps the condition. `tp_at_r_effective` and
`tp_at_r_source` ship beside it so a reader can tell which ceiling applied.

### Verification

- **27 tests**, `tests/test_backtest_ict_scalp_tp_override.py`.
- **Mutation-tested — 4 of 4 mutants caught.** This is recorded because the
  *first* version of the suite caught only **1 of 2**: it kept a local copy of
  the rung ladder and asserted the copy, so collapsing `unknown` into a pass in
  production left every test green. **A test that restates the logic it tests is
  not a test.** The fix was to give the logic one owner and import it — the same
  discipline this repo applies to `monitor_unit_for` and `provenance.py`.
- The end-to-end case drives **`main()`** with a spy on `order_package` and
  asserts the value arrives, so it covers the whole `argparse → resolve →
  cfg_overrides → unit` hop. An earlier version asserted a *literal line of
  source*, which broke the moment the refusal was extracted — brittle, and not
  behaviour.

## 4. PRE-REGISTERED grid — recorded BEFORE any run

Stated here so the grid cannot be chosen after seeing results.

- **Parameter:** `tp_at_r`, the only target knob the unit reads.
- **Grid:** `0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0` — **brackets the live
  `1.5` on BOTH sides.** A one-sided grid cannot distinguish *"further is
  better"* from *"we only looked further"*, and MI-277's question (winners
  cut short) biases toward looking only upward. `1.5` is included as the
  in-grid baseline.
- **Legs:** the 8 `ict_scalp_*` legs, per leg — never pooled. `tp_at_r` is
  declared per leg and a fleet-pooled verdict would hide a leg that disagrees.
- **Gate:** the `exit-refinement` standard — IS/OOS with a **yearly
  walk-forward**, `MIN_OOS_TRADES = 25`, Path A (`net_R` + `maxDD`) reported
  beside Path B (`net_r_per_capital_day`).
- **Costs:** the harness's own venue-aware model, on. `--tp-cap-pct` is **not**
  applied, per §2 — and that is a *derived* omission, not a default accepted
  silently.
- **Refusals declared in advance:** a cell whose `bank_rung_state` is
  `provable_no_op` or `unknown` is **not** counted as a tested arm. A leg under
  the OOS floor reports `underpowered`, which is **not** a negative result.

⚠️ **No sweep has been run and no verdict is claimed.** This unit built the
runner. U3b runs the grid; only then can U4 make a Tier-3 proposal, and only for
a lever that clears the gate.

## 5. Landing

**This PR declares `landing: hold`.** `check_pr_landing.py::TIER1_SURFACE`
allowlists `scripts/{ci,ops,research,reports}/**` and **not** top-level
`scripts/`, so `scripts/backtest_ict_scalp.py` cannot self-land whatever its
tier. That allowlist polarity is deliberate (*"a path not matched here cannot
self-land"*) and is **not** routed around here — the PR asks for a merge click
instead.

## 6. Filed

The U3 row
`BL-20260912-THE-SCALP-FAMILY-S-TARGET-HAS-NO-SWEEP-E35-EXCLUDES-IT-BY-DESIGN-AND-THE-FLEET-SWEEP-DOES-NOT-SWEEP-A-TARGET`
stays **open**: this PR closes only its **root** clause (a target CLI now
exists). The fleet-sweep wiring — a `tp_at_r` cell that actually schedules the
grid — is **not** built, so the family still has no *sweep*, only a *runner*.
Closing the row on the runner would be the merged-not-observed collapse this
repo keeps paying for.
