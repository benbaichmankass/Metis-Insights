# Conflict-policy walk-forward — scoping (S-STRAT-IMPROVE follow-on)

> **Date:** 2026-05-30 · **Status:** scope-only (no code yet) · **Tier:** Tier-1
> for the walk-forward harness; Tier-3 for any subsequent change to
> `src/runtime/intents.py::aggregate_intents`. **Parent docs:**
> [`docs/audits/system-portfolio-backtest-2026-05-30.md`](../audits/system-portfolio-backtest-2026-05-30.md)
> (and its addendum) and
> [`docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md`](DECIDER-SINGLE-ACCOUNT-2026-05-24.md).
> **Backlog driver:** `PERF-20260530-001`.

## Why this exists

The system-portfolio backtest (`scripts/backtest_system.py`) has produced two
findings that together justify an investigation of the live conflict policy:

1. **The flip policy is first-order.** On a 4-member portfolio over 4.2y the
   "hold" policy (don't tear an open winner off its own exit logic on an
   opposite-side vote) beats live "reverse" by +$2169 net and halves maxDD;
   on the original 5.7y full roster it took the book from −$411 to +$127.
   That magnitude is large enough to warrant a Tier-3 design discussion.
2. **At 6-member, "hold" is necessary but not sufficient.** Adding the two
   highest-priority members (turtle_soup, ict_scalp_5m) bleeds the portfolio
   −$6220 over 4.2y under "hold" — because those two members own the shared
   position most of the time and their standalone winning edges do not
   transfer when they monopolise it. The decider's *selection* layer is the
   second lever and is necessary on top.

A walk-forward (train/OOS) on the flip policy alone is the **next prerequisite
research step** — it answers whether the +$ / lower-DD effect is robust across
sub-windows or a single-period artifact. **This document defines what that
walk-forward will look like; it does NOT build it and it does NOT propose any
change to the live aggregator.**

## Scope of THIS document (and what's deliberately NOT in scope)

In scope:

- Define the train / OOS splits that the walk-forward will use.
- Define the metrics it must compute and the pass / fail thresholds.
- Define the rosters and policies it must compare.
- Identify what code changes to `scripts/backtest_system.py` are needed (vs
  what already exists today).
- Define the dependency chain from "walk-forward passes" to "Tier-3 PR
  drafting".

NOT in scope (and explicitly NOT to be done next session without operator
approval):

- Any edit to `src/runtime/intents.py::aggregate_intents` or
  `compute_execution_delta`.
- Any change to the live "reverse" policy.
- Any change to strategy `execution:` gates.
- Any sub-account / capital-allocation work — that's the separate
  `DECIDER-SINGLE-ACCOUNT-2026-05-24.md` v2 selection layer.

## Walk-forward design

### Splits

Two-fold, **anchored** (always start at the data origin) to keep the trainer
window growing:

| fold | train window         | OOS window           | rationale                                     |
|------|----------------------|----------------------|-----------------------------------------------|
| A    | 2020-06 → 2023-12    | 2024-01 → 2026-02    | the original 5.7y split — recent regime as OOS |
| B    | 2022-01 → 2024-06    | 2024-07 → 2026-02    | the 4.2y re-scoped split — match the verified addendum |

If signal-gen budget permits a third fold (operator's call), add:

| fold | train window         | OOS window           |
|------|----------------------|----------------------|
| C    | 2020-06 → 2024-06    | 2024-07 → 2026-02    | longer train, same OOS — robustness check     |

Each fold is run TWICE: once with the **4-member** roster
(`trend_donchian,fade_breakout_4h,squeeze_breakout_4h,fvg_range_15m`) and
once with the **6-member** roster (4-member + `turtle_soup,ict_scalp_5m`).
That gives 2 splits × 2 rosters × 3 policies = **12 runs per fold**, plus 4
more if fold C is added.

### Metrics per cell

Read directly from each result JSON's existing fields (the harness already
records all of them):

- `net_pnl` (USD)
- `return_pct`
- `max_drawdown_usd`, `max_drawdown_pct`
- `return_dd_ratio`
- `total_trades`, `win_rate_pct`, `capital_utilization_pct`
- `by_exit_reason["flip"]` (the actual flip count for the policy)
- `per_strategy_attribution[name].pnl`

### Pass criteria (the only ones that justify drafting a Tier-3 PR)

For "hold" to be considered a robust improvement on "reverse" worth a
Tier-3 PR:

1. **Direction holds in BOTH train AND OOS, in BOTH folds, for the 4-member
   roster.** Net PnL(hold) > Net PnL(reverse) AND maxDD%(hold) <
   maxDD%(reverse) in all four cells (2 splits × {train, OOS}).
2. **Direction is at least *not contradicted* in OOS for the 6-member
   roster.** Net PnL(hold) > Net PnL(reverse) in both 6-member OOS cells —
   even if both are net-negative (the addendum's finding). This is the
   weaker test, because the 6-member portfolio bleeds regardless of policy;
   we just need to confirm hold is not worse.
3. **No single fold's OOS shows hold *underperforming* reverse by more than
   the train→OOS noise of a single strategy's backtest** (a rough
   ~0.5R/year equivalent). Catastrophic OOS reversal is a fail even if the
   "average" looks fine.

If any of (1) / (2) fails: the result is period-specific; do NOT propose a
live change; close the backlog item with the OOS evidence.

### Fail-but-promising paths

If the 4-member walk-forward passes but the 6-member does not, the
conclusion is the one the addendum already pre-stages: **the conflict
policy and the selection layer are complementary**. In that case the
Tier-3 PR target shifts from "patch aggregate_intents" to "draft the
decider-v2 selection layer first" (per
`DECIDER-SINGLE-ACCOUNT-2026-05-24.md`), and the conflict-policy change
becomes a follow-on once the selection layer exists.

## What the harness already supports vs what it needs

`scripts/backtest_system.py` already supports:

- `--flip-policy {reverse,hold,flat}` (the knob — verified by the
  flip-churn addendum).
- `--start`, `--end` (the windowing for train vs OOS).
- `--roster <comma-list>` (the 4-member vs 6-member toggle).
- Cached signal streams per `(strategy, start, end, overrides)` keyed by
  SHA-1 — pre-caching one window covers any policy variation within it.
- `--json <path>` dumping every metric named under "Metrics per cell".

What it needs (small additions, Tier-1):

- A driver script (e.g. `scripts/walkforward_flip_policy.py`) that loops
  over the (fold × roster × policy) grid, calls
  `run_system_backtest(...)` directly with the right windows, and emits a
  single combined `walkforward_<run-ts>.json` plus a Markdown summary
  table. This is composition over the existing engine — no engine change.
- Either pre-cache each (strategy, window) signal stream once (separately
  per fold's train and OOS windows — different cache keys), OR run the
  driver with `refresh=False` after the operator has pre-warmed the
  caches. The 6-member signal-gen budget is the binding constraint;
  expect ~30 min per (strategy, window) for the 5m / 15m members at full
  6-yr scope (measured this session).
- Nothing else. No new strategy code, no new exit logic, no aggregator
  change.

## Operator hand-offs (so this doesn't drift into autonomous code change)

This document closes when the operator either:

- approves a Tier-1 PR for the walk-forward driver above (then a subsequent
  session executes the runs and reports back), OR
- de-prioritises the conflict-policy work and routes the next session to
  the decider-v2 selection layer instead (`DECIDER-SINGLE-ACCOUNT-2026-05-24.md`
  v2 step 2/3).

**No code in `src/runtime/intents.py` changes until the walk-forward
results + a Tier-3 design doc + explicit operator approval are all in
hand.**

## Open questions for the operator

1. **Fold C (longer train, same OOS): worth the extra signal-gen cost?**
   Default: no — A + B are sufficient to declare "robust" or "period-
   specific". Add C only if A and B disagree.
2. **Anchor or rolling?** This scope uses anchored splits. A rolling
   3-yr window (2020-2023, 2021-2024, 2022-2025, ...) would be more
   stringent and reveal regime-sensitivity. Defer to operator preference;
   the harness supports either by varying `--start` / `--end`.
3. **Wall-clock budget.** A full execution of this design needs ~3-4 hr of
   trainer-VM time per roster (6 strategies × 2 windows × ~10-30 min/each
   for 5m/15m signal-gen + ~3 min/policy run). Should this run on the
   trainer VM (autonomous Tier-1) or in a Claude-Code session (Tier-1
   under operator visibility)?
