# Cross Zero — flip-policy, overtrading throttle, net-of-fee measurement

**Date:** 2026-05-31 · **Status:** implementation (draft PR, Tier-3 — awaiting
operator approval before merge/activation) · **Objective:** take the live book
from net-negative to **net-positive after fees** ("cross zero"), closing the two
loss drivers the project's own audits already diagnosed.

## Why

- The live book is net-negative: `vwap` on bybit_2 was −$35.82 / 7d (gross
  +$11.25, fees −$47.07 = **fees 418% of gross**); the 4-member system backtest
  was −$411 / −4.11% / 5.7y.
- The documented goal is **2–3%/week**, not a 20%/month fantasy. The honest
  first milestone is crossing zero.
- The two root causes are already proven, not hypothetical:
  1. **Flip-policy churn** — `intents.py::compute_execution_delta` close-and-
     reverses the one shared position on every opposite vote. The flip-policy
     sweep (`docs/audits/system-portfolio-backtest-2026-05-30.md`) shows
     `reverse` is the worst of three policies in every window; `hold` zeroes
     the flips, ~halves max-DD, and flips the 4-member book net-positive.
  2. **Overtrading → fee drag** — thin per-trade edge consumed by taker fees.

## What shipped in this PR (all inert / default-off on merge)

### P1 — Flip-policy knob (`src/runtime/intents.py`)
- New `FLIP_POLICIES = {reverse, hold, flat}` + `resolve_flip_policy(settings)`
  mirroring `intent_multiplexer_enabled` (settings key → `FLIP_POLICY` env →
  default `reverse`).
- `compute_execution_delta` (and the `_for_package` bridge) take an optional
  `flip_policy`; the opposite-vote branch now resolves it:
  - `reverse` (default) → unchanged `action="flip"`.
  - `hold` → `action="noop"` (keep the position; the owner's monitor()/SL/TP
    exits it). The coordinator already journals noops → loud & auditable.
  - `flat` → `action="close"` (close, no re-open).
- **Default `reverse` ⇒ merging changes no live behaviour.** Activation is the
  separate `FLIP_POLICY=hold` env flip on the VM (Tier-3, operator-gated).
- Not an auto-flip/kill-switch — it is a per-tick target decision (Prime
  Directive safe).
- Tests: `tests/test_intent_delta_dispatch.py::TestFlipPolicy` (9).

### P2a — Overtrading throttle (`risk_counters.py` + `orders.py`)
- `inject_per_strategy_counters` now also injects `STRATEGY_TRADES_TODAY`
  (open+closed, non-backtest, today) and `STRATEGY_MINUTES_SINCE_LAST_TRADE`
  (computed in SQL; omitted when there is no prior trade so the first trade is
  never blocked).
- `safe_place_order` gains two **optional, default-off** per-strategy guards
  mirroring `MAX_POS_PER_STRATEGY`: `MAX_TRADES_PER_STRATEGY_PER_DAY` and
  `MIN_TRADE_SPACING_MINUTES`. Both fire only when cap **and** counter are
  present (omitting either never strands capability). Refusals are per-trade →
  the existing rejection ping fires; the account stays live.
- Cap **values** are Tier-3 (operator-set, after a per-strategy backtest with
  the cap applied confirms fees drop toward ≤30–40% of gross and net stays
  positive). The guard **code** is additive/inert.
- Tests: `tests/test_per_strategy_risk.py` (+10).

### P3c — Per-strategy net-of-fee measurement primitive (`exchange_fills_store.py`)
- `fifo_pnl_by_strategy(days, strategy_of_order_id, ...)` → per-strategy
  `gross_pnl / total_fees / net_pnl / fee_pct_of_gross / fill_count` (the audit
  headline: vwap's fee_pct_of_gross was 418%). Reuses `_fifo_match`; fills with
  an unmapped order_id bucket under `"unattributed"` so totals reconcile.
- Pure + unit-tested (`tests/test_exchange_fills_store.py`, +5, incl. a 420%
  fee-drag reproduction). The `order_id → strategy` map is **injected** because
  `exchange_fills` stores the exchange order id while the strategy lives in
  `trade_journal.db` — see the deferred follow-up.

## Deferred (Tier-2 follow-up; needs live-schema / VM verification)

- **P3a — pull fills live.** `scripts/pull_exchange_fills.py` exists but no
  systemd timer runs it. Add `deploy/ict-exchange-fills-pull.{service,timer}`,
  activate via `system-actions`. (Could not validate a timer in the sandbox.)
- **P3b — strategy attribution resolver.** Build the `order_id → strategy` map
  from `trade_journal.db` and surface `?group_by=strategy` on
  `/api/bot/pnl/exchange`. Deferred deliberately: the exchange order id is not
  cleanly persisted in a queryable trades column, so the join needs verifying
  against the live schema before it touches money-adjacent tooling.
- **P2b — maker-fee preference** (post-only entry w/ taker fallback): larger
  order-path change; revisit once the book is provably net-positive.

## Activation sequence (operator-gated)

1. Merge this PR (inert).
2. Backtest `--flip-policy hold` on the **real bybit_2 roster** via
   `scripts/backtest_system.py`; walk-forward (train/OOS) to confirm `hold` ≥
   `reverse` on this exact roster, then per-strategy backtests to set the
   throttle cap values.
3. Set `FLIP_POLICY=hold` + the throttle caps on **bybit_1 (demo mirror)
   first**; confirm via the P3c tracker (flips→0, fee% drops, net crosses zero).
4. Only then promote the same config to **bybit_2** (real money) via the
   sanctioned path, post-state verified on the diag relay.

## Risk to flag

The audit's "poison pair" `turtle_soup` + `ict_scalp_5m` is **live on bybit_2**
and loses ~$7,490 between them in-system even under `hold`. P1+P2a attack the
mechanical churn/fee drag, but crossing zero may also require demoting one to
`execution: shadow` — a separate, evidence-backed, operator-approved Tier-3
decision once the P3c tracker has live data. Do **not** flip any
`enabled`/`execution` on inference (the PR #1358 failure mode).
</content>
</invoke>
