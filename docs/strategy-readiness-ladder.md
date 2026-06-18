# Strategy readiness ladder (2026-06-18)

> **Why this exists.** The backtest/walk-forward gate used to be **binary** —
> a cell either cleared the strict every-fold gate (PASS) or it was FAIL. That
> threw away the cells most worth *refining*: a genuine net-of-fee edge that
> isn't yet fold-robust got bucketed with `vwap`-style fee-bleed garbage, so the
> roster kept shrinking toward a handful of absolute backtest winners. This
> ladder gives a strategy/cell an explicit **readiness tier** with **distinct
> bars for "research/paper-ready" and "live-money-ready"**, and routes
> paper-ready cells into an **active refinement pipeline** instead of the bin.

This is the **onboarding** half of the strategy lifecycle (backtest → demo →
real money). The **ongoing** half — reviewing already-live strategies — is the
**M7 strategy-review gate** (`docs/strategy-review-gate.md`,
`KILL`/`DEMOTE_SHADOW`/`TUNE`/`HOLD`/`PROMOTE`). They meet in the middle: a
paper-ready cell's refinement work feeds the M7 gate's `TUNE` loop. The ladder
deliberately mirrors the **ML model ladder** (`candidate → shadow → advisory`):
same idea — observe/collect before you let it touch money.

## The tiers

| Tier | Bar to clear | What runs / happens |
|---|---|---|
| **`backtest_only`** | a committed harness result exists | nothing live — just a number on disk |
| **`paper_ready`** | net-of-fee **positive overall** (7.5 bps) **AND** survives **2× fees** (15 bps) **AND** no single fold *catastrophically* negative | wire to **demo** (`execution: live`, routed to `bybit_1` / a paper account **only**) to accrue real decisions + ML soak + a live track record, **AND** add a row to the **refinement queue** |
| **`live_money_ready`** | **every** OOS fold positive (strict) **AND** 2× fee headroom **AND** a demo soak window confirming live≈backtest **AND** `account_compat` PASS for the target account | eligible for **real money** (Tier-3, operator-gated) |
| **`reject`** | net-negative at 7.5 bps, **or** fee-bleed (positive gross / negative once 2× fees bite — the `vwap` failure mode), **or** net-positive overall but a single catastrophic fold | **kill** — don't wire anywhere |

**The "moderate" paper bar** (operator decision 2026-06-18): a fold is
*catastrophic* when its net R is worse than `-max(catastrophe_floor_r,
abs(total_net_r))` — i.e. one fold may not lose more than the strategy's whole
net OOS edge, with a small absolute floor (default **3 R**) so a
barely-positive total isn't disqualified by ordinary fold noise. Tunable via
`classify_strategy_tier.py --catastrophe-floor-r`.

## How a tier maps onto the runtime

The tier is **not** a new config field — it maps onto the two execution gates
that already exist (`docs/CLAUDE-RULES-CANONICAL.md` § "The two execution
gates"):

- **`paper_ready`** → `config/strategies.yaml::<name>.execution: live`, routed in
  `config/accounts.yaml` to **demo/paper accounts only** (never a real-money
  account). This is exactly the operator's standing "paper/demo accounts exist
  to test strategies" policy — now with an explicit entry bar.
- **`live_money_ready`** → the strategy is *additionally* routed to a real-money
  account (`bybit_2`, …). This step is **Tier-3, operator-approved**, and is the
  `new-strategy` skill's graduation checklist (`account_compat_matrix` +
  demo-soak evidence).
- **`reject`** → `enabled: false` (M7 `KILL`) or never wired.

So promoting a cell up the ladder is a routing change, audited the same way as
every other Tier-3 roster move. Nothing here bypasses a risk cap or the
account `mode:` gate.

## The classifier

`scripts/ops/classify_strategy_tier.py` turns a k-fold fold-report (the JSON
written by `scripts/ops/m15_ws_b_fold_report.py`) into a tier + reasons:

```bash
python3 scripts/ops/classify_strategy_tier.py results/m15_ws_c_kfold/fold_*.json
python3 scripts/ops/classify_strategy_tier.py --json fold_eth_pullback_2h.json
```

The same `classify_tier(report)` function is importable, so the sweep tooling
stamps a `tier` on every fold-report it writes (future runs are
self-classifying; today's existing reports are tiered by running the CLI over
them).

## The refinement pipeline

Every `paper_ready` cell gets one row in
**`docs/claude/strategy-refinement-queue.json`** carrying:

- the cell (family + symbol + timeframe) and its current tier,
- **why it missed `live_money_ready`** (which fold(s) failed, in which regime),
- a concrete **refinement hypothesis** to test (e.g. "edge degrades in the
  recent chop regime → add an ADX/regime entry gate, re-run the k-fold"),
- `status` ∈ `open | refining | promoted | rejected`.

**Who drains it:** `/performance-review` and the `backtesting` skill. Each run
triages open items: re-test the refinement hypothesis on the harness, re-tier
with the classifier, and either advance the cell (paper→live proposal),
iterate the hypothesis, or reject it with the evidence. This is the "active
pipeline for refinement" — a paper-ready cell is a **work item**, not a
shelved backtest.

## Non-negotiables (unchanged)

- A tier is a *recommendation*; routing a strategy to **real money** is always
  Tier-3, operator-approved (this doc does not auto-promote anything).
- Demo wiring of a `paper_ready` cell carries **no real-money risk** but still
  goes through a normal Tier-3 PR (it edits `config/strategies.yaml` /
  `config/accounts.yaml`).
- Net-of-fee is the only currency. A gross-positive / net-negative cell is
  `reject`, never `paper_ready` (the `vwap` lesson, codified).
