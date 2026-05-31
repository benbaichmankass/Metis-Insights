# Consolidation & Strategy Roadmap (session 2026-05-31)

**Status:** planning record (Tier-1 doc). Captures the consolidation decision and
the strategy findings from the 2026-05-31 multi-thread research session so two
parallel sessions stop diverging. Code work proceeds from here.

## 0. Why this exists

Two sessions ran in parallel and produced overlapping backtest harnesses; a
merge collision (PR #2441 cross-zero vs PR #2451 FLIP-hold-default, same lines of
`src/runtime/intents.py`) confirmed the drift risk. This doc is the single source
of truth for what's canonical and what's queued. Both PRs are now on `main` and
coexist (`resolve_flip_policy` + `fifo_pnl_by_strategy` from #2441; `FLIP_POLICY`
default `hold` from #2451).

## 1. Backtest harness consolidation (CANONICAL DECISION)

Two harnesses on `main` overlap:
- `scripts/backtest_system.py` — better **account model** ($ equity, finite shared
  netted BTCUSDT position, daily-loss cap, per-trade risk sizing, capital
  utilization, per-strategy $ attribution). Reimplements its own winner logic.
- `sim/` (engine/models/attrition/sweep/fills/ledger + 49 tests) — better
  **evaluation layer**: models-in-the-loop (reuses live `advisory_influence`),
  regime-gate policy, decision-attrition (funnel_scored vs holdout eval_n),
  multi-variant sweep. Faithfully reuses live `aggregate_intents`. R-multiple
  based — **no account realism**.

**Decision: `sim/` is the canonical base; fold `backtest_system.py`'s account
model in as an optional Phase-5 account layer.** Rationale: sim/ already reuses
live code (no forked trading logic), ships tests, and carries the ML layer that
is the whole point of "test MLs + strategies together." The only gap is the
capital model, which is additive.

**Plan:**
1. Add an optional account layer to `sim/` (initial_balance, risk_pct,
   daily_loss_pct → $ equity curve, $ drawdown, capital utilization) — port the
   logic from `backtest_system.py:258-465`. When no account config is passed,
   sim/ stays R-based (back-compat).
2. Add `--flip-policy {reverse,hold,flat}` parity to sim/ (sim currently relies on
   at-most-one-open; backtest_system has the explicit knob). Must read the live
   `resolve_flip_policy` default.
3. Migrate `backtest_system.py`'s `ROSTER` + signal-cache convenience into sim/'s
   loader so the $-based system runs survive.
4. **Do NOT delete `scripts/backtest_system.py`** until the operator signs off
   that sim/ Phase-5 reproduces its numbers. Until then, keep both; mark
   backtest_system as "legacy account-only" in its docstring.
5. Tier-1 throughout — never touches the live order path or config.

**Hardware constraint (from the SIM session):** the trainer VM is single-core;
model-in-loop sweeps over 175k+ bars take hours. The new prem-tier VM cores are
the fix — heavy sweeps run detached on the VM, Claude collects results. Short of
that: vectorize the model-scoring path or use shorter windows.

## 2. Strategy selection gate (M7) — verdict on current roster

Mechanical gate mirroring `ml/promotion/{gates.py,stage_guard.py}` (computes
PASS/FAIL, PROPOSES, operator flips the YAML). Criteria: net-positive after fees,
OOS decay ≤50%, |corr to live book| ≤0.5, return/DD ≥1.0, **positive in-system
contribution under `FLIP_POLICY=hold`**. Applied to the audit evidence:

| Strategy | Verdict | Action |
|---|---|---|
| trend_donchian | PASS | keep live (only consistent in-system earner) |
| fvg_range_15m | HOLD | stay shadow until demo data (best candidate) |
| squeeze_breakout_4h | WATCH | borderline; one more window |
| fade_breakout_4h | FAIL | propose demote → shadow (−$673 in-system) |
| turtle_soup | FAIL (severe) | demote → shadow (−$3,032; book hog) |
| ict_scalp_5m | FAIL (severe) | demote → shadow (−$4,458) |
| vwap | already shadow | keep shadow (gate agrees) |

Implementation: new `scripts/strategy_gate.py` consuming `fifo_pnl_by_strategy`
+ the consolidated harness's in-system attribution; folds into `/performance-review`.
Depends on the P3b `order_id→strategy` resolver (else net-of-fee criteria are
`DEGRADED (journal-pnl proxy)`). Demotions are Tier-3 (operator-approved YAML flips).

## 3. MES / SPX cross-asset (highest risk-adjusted lever)

MES is **live in paper since 2026-05-22** (plumbing built, gateway auto-heals),
but runs the **crypto-tuned** strategies, and the validated SPX-trend edge
(+29.6R, corr 0.009) is **not routed to it** and its `SPX500_1m.parquet` isn't in
the repo. Path (mostly autonomous): re-source SPX data → re-tune Donchian on it →
add a per-symbol param mechanism (doesn't exist yet) → route `trend_donchian` to
`ib_paper` → soak on paper → measure live correlation. Only real-money `ib_live`
needs the broker 2FA hand-off. IBKR paper confirmed working by operator.

## 4. New complementary strategies (ranked, for research → shadow)

| Rank | Candidate | Axis | New data? | Notes |
|---|---|---|---|---|
| 1 | session_breakout_trend | time-of-session | No | empty roster axis; clone backtest_trend |
| 2 | htf_pullback_trend | trend pullback | No | structurally flip-safe vs trend (same-side) |
| 3 | funding_carry_2h | derivatives carry | Yes (absent) | naive funding-fade already falsified |
| 4 | equity_leadlag_filter | cross-asset | Yes (absent) | cousin failed; equity-hours mismatch |

All ship `execution: shadow`, priority ≤3, bybit_1 demo, reusing the shared
Chandelier `monitor()`. Gate = net-positive after 7.5bps (full + walk-forward) +
low corr + positive in-system under `hold`. Ranks 1–2 are zero-new-data, test first.

## 5. BUG-049 verdict — BENIGN

`_sweep_unlinked_packages` (`order_monitor.py:2085`) is a janitor that orphans
unlinked open packages to unblock the strategy gate. For `execution: shadow`
strategies (vwap) it *always* fires — shadow logs packages but never trades, so
"never executed" is expected, not a bug. Original BUG-049 (live vwap silencing
itself on spot-margin) was structurally fixed in S-047. No action; one health-
review note: confirm live strategies' packages link to trades (trades table is
healthy to #1868, so the live path works).

## Sequencing

1. **Harness consolidation** (this doc §1) — prerequisite; everything builds on it.
2. **Selection gate + demotions PR** (§2) — built on the consolidated harness.
3. **MES/SPX re-tune** (§3) — uses consolidated harness + prem-tier cores.
4. **New strategies** (§4) — research → shadow, tested via consolidated harness.

All backtest tooling is Tier-1. Every strategy/account/risk flip is Tier-3
(operator-approved). No auto-flips (Prime Directive).
