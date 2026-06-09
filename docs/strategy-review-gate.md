# Strategy Review Gate (M7)

> **Status:** Canonical for M7. Adopted in sprint **S-M7-STRATEGY-REVIEW-GATE** (2026-06-09).
> **Scope:** the *mechanical* decision gate on top of `/performance-review`.
> The gate produces one of `{promote, hold, tune, demote_shadow, kill}` per
> strategy per review window, using fixed thresholds. The gate **proposes**;
> the operator **decides** — every action in `{tune, demote_shadow, kill}` is
> Tier-3.

This document defines the **review packet**, the **decision matrix**, the
**threshold table**, and the **Tier-3 proposal SLA**. It does **not**
replace `/performance-review` — `/performance-review` produces evidence;
M7 is the gate that consumes the evidence and emits a recommendation with
a paper trail.

If you came here to **score individual decisions A-F** (the per-order-
package rubric), that lives in
[`.claude/skills/performance-review/SKILL.md`](.claude/skills/performance-review/SKILL.md).
This doc is one level up — it asks "given the per-strategy aggregate,
the regime-cell PnL slice, and the execution diagnostics, what should
happen to the strategy?"

## Why the gate exists

Strategy lifecycle decisions used to be made ad-hoc, one performance
review at a time, with the catastrophic-failure threshold implicit. The
PR #1358 incident showed why an implicit threshold is dangerous: a
session demoted a live strategy on a stale comment, not on evidence. The
fix is a written gate that says, for a given `n` and a given metric set,
exactly what proposal a packet must emit.

The other forcing function was the **VWAP sanity-check** (this sprint):
external review converged on "5% win rate over 20 trades is a defect,
not variance" — but the bot's own pipeline did not name an explicit
threshold for that judgement. M7 names it.

## The review packet

For a given strategy and a given window, the packet is a single JSON
document with these top-level keys:

```
{
  "schema_version": 1,
  "strategy": "vwap",
  "window_start": "ISO-8601",
  "window_end":   "ISO-8601",
  "execution":    "live | shadow",          // current config/strategies.yaml value
  "enabled":      true,                     // current config/strategies.yaml value
  "headline":     { ...aggregate stats... },
  "regime_cells": [ ...trend × vol grid... ],
  "execution_diagnostics": { ...slippage / fill rate / latency... },
  "backtest_anchor": { ...if a recent sweep exists... },
  "proposed_action": "promote | hold | tune | demote_shadow | kill",
  "reasons":      [ "<= 240 chars each — what triggered the action" ],
  "alternative":  "<= 200 chars — what to consider instead, or 'none'",
  "tier":         3,                        // every action except `hold` is Tier-3
  "sla_due_by":   "ISO-8601",               // demote_shadow/kill must ship within 7d
  "generated_at": "ISO-8601",
  "generated_by": "scripts/ml/strategy_review_packet.py vX.Y"
}
```

### `headline` — per-strategy aggregates (window)

| Field | Type | Source | Note |
|---|---|---|---|
| `n_decisions` | int | `order_packages` rows in window with `strategy_name = X` | Includes shadow + live. |
| `n_filled` | int | `order_packages` with non-null `linked_trade_id` | The packet bot acts on. |
| `n_closed` | int | `trades` filtered to `status ∈ {closed_*}` joined via `order_package_id` | Closes are the only rows with realized PnL. |
| `n_wins` | int | `pnl > 0` over `n_closed` | |
| `win_rate` | float | `n_wins / n_closed` | NULL if `n_closed = 0`. |
| `pnl_total` | float | sum(pnl) over closed | |
| `expectancy` | float | `pnl_total / n_closed` | NULL if `n_closed = 0`. |
| `max_drawdown` | float | running peak-to-trough on cumulative PnL across `n_closed` (close-time order) | |
| `avg_hold_seconds` | int | `closed_at − opened_at` over closed | |
| `fill_rate` | float | `n_filled / n_decisions` | Sanity check: a shadow strategy must have `fill_rate = 0`. |
| `rejection_cluster` | str / null | top reason in `order_packages.close_reason` where status starts with `failed_*` | |

### `regime_cells[]` — per-(trend, vol) PnL slice

Anchored on the same axes as
[`config/regime_policy.yaml`](../config/regime_policy.yaml):

- **Trend** axis: `chop | transitional | trending`. Source: the `regime`
  field stamped on every audit row by `src/runtime/regime/detector.py`
  (read via the `signals.meta` JSON in the `trade_journal.db::signals`
  dual-write — the same source the `/api/diag/audit_query` endpoint
  serves).
- **Vol** axis: `calm | volatile`. Source: the `vol_regime` field
  stamped alongside `regime` since S-MLOPT-S15b (observe-only, frozen
  per-`(symbol, timeframe)` thresholds).

For each `(trend, vol)` cell present in the window:

```
{
  "cell":         { "trend": "trending", "vol": "calm" },
  "n_decisions":  37,
  "n_closed":     12,
  "n_wins":       1,
  "win_rate":     0.083,
  "pnl_total":    -642.10,
  "expectancy":   -53.51,
  "regime_policy_cell": "off" | "on" | "unknown"
}
```

`regime_policy_cell` is the current verdict the regime router would
emit for this cell (`config/regime_policy.yaml`). When the slice shows a
cell is **losing money AND the policy already says `off`**, that is a
strong signal the strategy has no remaining edge anywhere — the kill
threshold.

Cells with `n_closed = 0` are emitted as observation-only (no PnL fields
populated). A strategy whose slice has `regime_policy_cell == "off"`
across **every cell with `n_decisions ≥ 1`** is, by definition, a
candidate for `kill`.

### `execution_diagnostics`

| Field | Type | Source | Note |
|---|---|---|---|
| `entry_slippage_bps` | float | (filled trade `entry_price` − package `entry`) / package `entry` × 10000, mean over `n_filled` | Positive = filled worse than signal. |
| `fill_rate` | float | `n_filled / n_decisions` | Repeated from `headline` for diagnostic-only convenience. |
| `dispatch_latency_seconds` | float | mean(`trades.timestamp` − `order_packages.created_at`) over `n_filled` | Distinct from broker fill latency. |
| `confidence_distribution` | object | `{min, max, p50, std}` over `order_packages.confidence` | The PERF-20260601-010 "degenerate confidence" failure mode is `std ≈ 0` and `min = max = 1.0`. The gate flags this. |

### `backtest_anchor` (optional)

If the trainer-VM sweep mirror
(`runtime_logs/trainer_mirror/backtests/<UTC-date>/`) contains a recent
run for this strategy, the most recent `SUMMARY.md` headline number is
echoed for comparison:

```
{
  "date": "2026-06-04",
  "summary_table_present": true,
  "best_variant_net_r": 12.4,
  "current_config_net_r": -3.1,
  "note": "current config underperforms best variant by 5x; consider tune"
}
```

Absent the mirror, this block is omitted (the packet remains valid).

## Threshold table — the gate

The gate decides per-strategy `proposed_action` from `headline`,
`regime_cells`, and `execution_diagnostics`. Anchor metrics are **win
rate vs. its symmetric baseline (50%)**, **expectancy**, **max
drawdown**, and **per-regime-cell behavior**. The matrix below is
**mechanical** — the packet script computes it; the operator decides
whether to merge it.

| n_closed (window) | Win rate | Expectancy | Regime cells | Action |
|---|---|---|---|---|
| **0** | n/a | n/a | n/a | `hold` (no evidence) |
| **1 ≤ n < 30** | win_rate ≤ 10% AND expectancy < 0 | < 0 | any policy-OFF cell present in slice | `kill` (catastrophic at low n — variance can't explain) |
| **1 ≤ n < 30** | win_rate ≤ 25% AND expectancy < 0 | < 0 | every cell with n≥1 is policy-OFF | `kill` (no edge anywhere) |
| **1 ≤ n < 30** | win_rate ≤ 25% AND expectancy < 0 | < 0 | mixed (some policy-ON, some OFF) | `demote_shadow` (catastrophic but might recover in some cells) |
| **1 ≤ n < 30** | otherwise | otherwise | otherwise | `hold` (not enough data to act) |
| **30 ≤ n < 100** | win_rate ≤ 30% AND expectancy < 0 | < 0 | every cell policy-OFF | `kill` |
| **30 ≤ n < 100** | win_rate < 40% AND expectancy < 0 | < 0 | any | `demote_shadow` |
| **30 ≤ n < 100** | 40% ≤ win_rate < 50% OR expectancy ≈ 0 | thin | n/a | `tune` (point at parameter search, M8) |
| **30 ≤ n < 100** | win_rate ≥ 50% AND expectancy > 0 | > 0 | n/a | `hold` (let it season) |
| **n ≥ 100** | win_rate < 40% AND expectancy < 0 | < 0 | every cell policy-OFF | `kill` |
| **n ≥ 100** | win_rate < 40% AND expectancy < 0 | < 0 | any policy-ON | `demote_shadow` |
| **n ≥ 100** | 40% ≤ win_rate ≤ 55% AND expectancy near 0 | thin | n/a | `tune` |
| **n ≥ 100** | win_rate > 55% AND expectancy > 0 AND max_dd within 3× expectancy | > 0 | n/a | `promote` (currently shadow → propose live) |
| **n ≥ 100** | otherwise | otherwise | otherwise | `hold` |

### Overrides

These overrides fire **before** the matrix and short-circuit it:

1. **Execution-mode mismatch.** If `execution: shadow` but `n_filled > 0`,
   the gate emits `hold` with a `reasons[]` entry pointing at the
   pipeline anomaly and links the health-review backlog item — the
   strategy is in an indeterminate state; do not act.
2. **Degenerate confidence.** If `confidence_distribution.std == 0`
   AND `max == min == 1.0`, the gate appends a `reasons[]` note about
   PERF-20260601-010 but does NOT override the matrix — degenerate
   confidence is a **tune** signal regardless of PnL. The matrix's
   `tune` is preferred over `kill` for this case unless win rate is
   also catastrophic.
3. **Already at terminal state.** If the matrix output is
   `demote_shadow` and `execution` is already `shadow`, escalate to
   `kill` if the regime slice supports it; otherwise stay `hold` with
   evidence that the strategy continues to lose in shadow.
4. **Promote requires N cohort weeks.** `promote` is never emitted if
   the shadow soak window is shorter than 14 days, even with strong
   in-window PnL — promotion needs cohort time, not just sample size.

## The five actions

| Action | What it means | Who acts | Tier |
|---|---|---|---|
| `promote` | Currently `shadow`; matrix recommends `live`. | Operator merges a Tier-3 PR flipping `execution: live`. | 3 |
| `hold` | No change. Observation only. | Nobody. Just record the packet. | 1 (read-only) |
| `tune` | A specific parameter looks wrong; recommend a sweep. | Operator runs the M8 parameter-search recipe; result feeds the next packet. | 3 (the recipe is M8) |
| `demote_shadow` | Currently `live`; flip to `execution: shadow`. | Operator merges a Tier-3 PR. | 3 |
| `kill` | Currently `enabled: true`; flip to `enabled: false` (signal builder short-circuits to `side=none`). | Operator merges a Tier-3 PR. | 3 |

`kill` is reversible — flipping `enabled` back to `true` is the same
mechanism. The naming reflects the operational reality (the strategy
stops emitting signals), not finality.

## SLA — bounded operator clock

When the packet's `proposed_action` is `demote_shadow` or `kill`, the
gate's contract is:

- **Generation:** the packet is generated automatically (this script or
  `/performance-review` invoking it) within 24 hours of the window
  closing.
- **Proposal:** the packet ships as a **draft Tier-3 PR** within 7 days
  of generation. The PR body embeds the packet's Markdown summary; the
  reviewer can re-derive every number from the JSON without re-running.
- **Operator decision:** the operator either approves, rejects (with a
  one-line reason recorded in the PR), or asks for more data within 7
  more days. Total wall-clock from window close to decision: ≤ 14 days.

A `demote_shadow` / `kill` proposal that sits past the SLA window
without a decision is escalated by the next `/performance-review`
run — the prior packet is re-attached with a "still pending" tag and
appended to the performance-review backlog. The matrix never overrules
the operator; it only refuses to forget.

`tune` does not have an SLA — the parameter-search recipe (M8) is a
research task, not a remediation. `hold` and `promote` are similarly
clock-free.

## M8 hook — `tune` recipe pointer

The packet's `proposed_action == "tune"` carries an additional
`tune_recipe` field that names the parameter sweep the operator should
run next. Schema:

```
"tune_recipe": {
  "target": "config/strategies.yaml::vwap.threshold",
  "current_value": 0.01,
  "search_space": "log-uniform [0.001, 0.05]",
  "harness": "scripts/backtest_vwap.py",       // M8 will own the canonical entry
  "evidence_window_days": 90,
  "note": "current threshold ties to the long-side overtrade pattern from S-STRAT-IMPROVE-S2."
}
```

M8 ships the canonical sweep harness; this M7 doc only **names** what
the tune action points at. Until M8 lands, `tune_recipe` is advisory.

## Where this lives in the system

```
config/strategies.yaml                           ← the field the gate would write (Tier-3)
config/regime_policy.yaml                        ← the OFF/ON cells the slice reads
trade_journal.db::order_packages                 ← per-decision rows
trade_journal.db::trades                         ← per-fill rows (PnL)
trade_journal.db::signals (.meta JSON)           ← regime/vol stamp source
runtime_logs/trainer_mirror/backtests/<date>/    ← backtest_anchor source

scripts/ml/strategy_review_packet.py             ← THIS SPRINT — packet generator
GET /api/bot/strategies/{name}/review            ← THIS SPRINT — dashboard surface
docs/strategy-review-gate.md                     ← THIS DOC
.claude/skills/performance-review/SKILL.md       ← evidence-gathering skill the gate sits on top of

runtime_logs/strategy_reviews/<UTC-date>/<name>.json   ← per-run packet
runtime_logs/strategy_reviews/<UTC-date>/<name>.md     ← Markdown summary (PR body)
```

## What this doc is not

- **Not a replacement for `/performance-review`.** The skill grades
  decisions; the gate consumes the aggregate. Both run; both ship.
- **Not a strategy-tuning recipe.** That's M8.
- **Not a code-quality review.** That's `/review` / `/security-review`.
- **Not a kill switch in itself.** The script generates a packet and
  may open a draft PR; it does not flip YAML. The operator does.

## Change log

- **2026-06-09** — created in sprint
  [`S-M7-STRATEGY-REVIEW-GATE-2026-06-09`](sprint-logs/S-M7-STRATEGY-REVIEW-GATE-2026-06-09.md);
  initial threshold table seeded against the regime-roster matrix
  (`docs/research/regime-roster-matrix-2026-06-01.md`) and the VWAP
  sanity-check evidence.
