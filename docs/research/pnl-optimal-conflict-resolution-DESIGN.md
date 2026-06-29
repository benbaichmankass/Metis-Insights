# Design Proposal — PnL-Optimal Intent Conflict Resolution (`FLIP_POLICY=selective`)

> **Status:** RESEARCH / DESIGN PROPOSAL — read-only analysis. **No code, config, or live
> state changed.** Every option that touches intent/conflict-resolution, sizing, order-path,
> or hedge-mode is **Tier-3 — PROPOSE ONLY**, gated on backtest validation + explicit operator
> approval per `docs/CLAUDE-RULES-CANONICAL.md` § Permission Tiers.
>
> **Origin:** the 2026-06-29 optimization investigation (delegated research). The operator
> refined the target after the first pass: **one account, single net position, capital always
> on the best trade — no hedge mode, no second account, no coexisting opposing legs.** The
> recommended build is therefore a new **opt-in `FLIP_POLICY=selective`** mode (§7), which
> supersedes the original §5 "separate account / hedge" options.

---

## 0. The observed problem

On `bybit_2` (real money) a low-confidence `htf_pullback_trend_2h` LONG on BTCUSDT (confidence
~0.186) was held open, and ~7 higher-confidence `ict_scalp_5m` SHORT signals (confidence
0.74–0.90) were rejected over a day, each journaled `intent_noop:flip_suppressed_hold_policy`.
A weak long blocks strong counter-trend scalps and leaves PnL on the table.

## 1. End-to-end current behaviour (cited)

- Strategies emit typed `StrategyIntent`s (`intent_from_signal`, `src/runtime/intents.py:634`);
  `aggregate_intents` (`intents.py:1139`) collapses them to **one net `DesiredPosition` per
  symbol** — same-direction → reinforcement (`max`, not sum, `intents.py:1248-1294`); conflict
  → deterministic priority winner, loser dropped to `meta["dropped_intents"]`
  (`intents.py:1296-1351`). It never emits both sides.
- The actual suppression is one layer down: `Coordinator.multi_account_execute`
  (`src/core/coordinator.py:780`) reads the signed net position from the journal
  (`current_net_position_qty`, `positions.py:137`) and computes a delta
  (`compute_execution_delta`, `intents.py:1359`). When the desired side opposes the held side
  (`intents.py:1490`), `FLIP_POLICY` governs — live default **`hold`** (`intents.py:311`,
  validated by the 24-cell walk-forward, `docs/audits/walkforward-flip-policy-2026-05-30.md`,
  PR #2451) → `action="noop"`, reason `flip_suppressed_hold_policy` (`intents.py:1520-1531`)
  → journaled `intent_noop:flip_suppressed_hold_policy` (`coordinator.py:1708-1728`). **That is
  the exact log line observed.**
- The confidence override exists (`_evaluate_confidence_override`, `intents.py:376-418`) but is
  **dormant** — it only fires when `FLIP_CONFIDENCE_THRESHOLD > 0` (default `0.0`,
  `intents.py:334`). So the strong shorts are dropped purely because the held long exists and
  the override is off. **Not a bug — correct per current validated config — but it is the lost PnL.**

## 2. The hard exchange constraint (decisive)

**Bybit V5 linear perps default to one-way position mode: one net position per account/symbol.
You cannot hold simultaneous long + short on the same symbol unless hedge mode (`positionIdx`)
is enabled.** A full `src/` grep for `positionIdx|hedge|set_position_mode` returns **zero files**
— every order goes to the default one-way slot (`execute.py` order kwargs use only `reduceOnly`
+ SL/TP). IBKR/Alpaca are netting accounts (same constraint). **Therefore "run a short scalp
alongside the long trend on `bybit_2` as configured" is physically impossible without hedge
mode or a second account.**

## 3–5. Options considered (summary)

| Option | Feasibility | Verdict |
|---|---|---|
| (a) EV/confidence-weighted arbitration (extend the existing override) | all venues, one net | **Basis of the recommendation** |
| (b1) Bybit hedge mode `positionIdx` | Bybit only; rewrites the single-net invariant the reconciler depends on | **Rejected** (too invasive for the gain) |
| (b2) Separate account per book | any venue | **Rejected by operator** (wants one account) |
| (c) Net-model "scalp overlay" (reduce/re-add) | all venues | **Rejected** — mathematically = the `reverse` churn the walk-forward already beat |
| (d) Status-quo + tuned confidence override | all venues | **Phase 1** (trivial, env-only) |

## 6. Phase 1 (lowest risk, zero new code)

Validate then enable `FLIP_CONFIDENCE_THRESHOLD` (~0.15) + `FLIP_MIN_POSITION_AGE_HOURS` (~4.0)
on `bybit_2` — a materially stronger, aged opposing signal is then allowed to flip; the 0.186
long would no longer block 0.74–0.90 shorts. Machinery already exists and reads env at call
time (instant rollback). **Tier-3** — must pass the flip-confidence walk-forward sweep first.

---

## 7. RECOMMENDATION — `FLIP_POLICY=selective` (the operator's refined target)

> One account, single net position, capital always on the best trade. A **named, opt-in** flip
> mode beside `hold`/`reverse`/`flat` — **not** a change to the `hold` default, **not** blanket
> `reverse` (which the walk-forward beat), **not** coexisting positions (ruled out by §2). It is:
> *close the held trend only when a counter-signal is strong enough AND profitable enough net of
> the full round-trip cost; let the scalp run to its own TP/SL; then conditionally re-establish
> the trend if it is still valid.*

### 7.1 Flip GATES (all must pass)

Held position **H** (side `s_H`, entry confidence `c_H`, entry `p_H`, age `a_H`h, package `OP_H`);
incoming counter-signal **N** (opposite side, confidence `c_N`, entry `p_N`, stop `sl_N`, target
`tp_N`, sized qty `q_N`). Flip iff (a) AND (b) AND (c):

**(a) Confidence-gap gate** (reuse existing knobs):
```
c_N − c_H ≥ FLIP_CONFIDENCE_THRESHOLD     (>0 to arm; primary operational knob)
a_H       ≥ FLIP_MIN_POSITION_AGE_HOURS    (don't reverse a fresh trend)
```

**(b) Fee-aware EV gate** (the new, decisive inequality — the flip is FOUR fills: close H, open
N, close N, re-open H). With one-way fill cost fraction `f` (from `FEE_BPS_ROUNDTRIP=7.5`,
`backtest_system.py:97`, + slippage), `R_N = |tp_N−p_N|·q_N`, `risk_N = |p_N−sl_N|·q_N`,
`P_win = c_N` (calibrated proxy):
```
EV_flip = P_win·R_N − (1−P_win)·risk_N
          − f·( notional_H + notional_N + notional_N + notional_H )   # close H, open N, close N, re-open H
EV_flip ≥ FLIP_EV_MARGIN_USD     (default 0)
```
i.e. the scalp's expected edge must exceed `f·(2·notional_H + 2·notional_N)`. A high-confidence
scalp into a small TP on a large held trend **fails** this gate (re-entering the big trend twice
costs more than the small scalp earns) — exactly the PnL-optimal answer.

**(c) Age/regime guard:** run the flip gate AFTER `_hard_regime_gate` so a gated scalp can't
trigger a trend exit; fail-permissive on a missing regime tag.

### 7.2 Conditional RE-ENTRY

When N closes, re-open H **iff ALL hold**: (1) the trend strategy is *currently* re-emitting a
same-side actionable signal (within `FLIP_REENTRY_WINDOW_BARS`); (2) price still within
`FLIP_REENTRY_ZONE_FRAC` of `OP_H.entry`; (3) regime unchanged; (4) re-emitted confidence
`≥ FLIP_REENTRY_MIN_CONFIDENCE`; (5) within the time/bar window. **Failure mode: do NOT re-open**
(journal `flip_reentry_skipped:<reason>`). Never resurrect a stale signal.

**State to remember:** a displaced-intent record per `(account, symbol)` — persisted in the
**journal** (projected on `order_packages` via a `# data-wiring:` declaration + the
`new-table-wiring` guard), not in-process, so a restart mid-scalp doesn't abandon trend
restoration. Re-entry is triggered from the monitor close path but evaluated on the next live
tick (so "signal still valid" is a real check, not a replay).

### 7.3 New code (within single-net, one-account)

`selective` branch in `compute_execution_delta`; a pure `compute_flip_ev(...)` (new
`src/runtime/flip_ev.py` or in `intents.py`) + resolvers (`resolve_flip_ev_margin`,
`resolve_flip_reentry_*`); displaced-intent persistence + re-entry trigger in
`multi_account_execute` + `order_monitor.py`. **No `positionIdx`, no second account, no order-path
surgery** — reuses `close_open_position` + normal package dispatch.

### 7.4 Backtest gate

Add a `selective` arm to `scripts/backtest_system.py` (`--flip-policy` + `--flip-confidence-threshold`
/ `--flip-ev-margin` / `--flip-reentry-*`), charge all four fills via the existing fee path, run the
SAME 24-cell walk-forward as a third arm vs `hold` and `reverse`. **Win condition (priority):**
beat BOTH `hold` and `reverse` on net-of-fee PnL, then maxDD%, with OOS lift ≥ in-sample. Report
flip count + re-entry success rate. Only on a clean PASS does it graduate to a live
`FLIP_POLICY=selective` env flip on `bybit_2` (Tier-3, operator-approved, env-deploy, rollback =
`FLIP_POLICY=hold`).

## 8. Tier flags

| Change | Tier | Gate |
|---|---|---|
| Enable `FLIP_CONFIDENCE_THRESHOLD`/`FLIP_MIN_POSITION_AGE_HOURS` (Phase 1) | Tier-3 | walk-forward sweep + operator approval; env-deploy |
| `selective` policy + `compute_flip_ev` + displaced-intent persistence (Phase 2) | Tier-3 | walk-forward PASS vs hold AND reverse; `new-table-wiring` guard; operator approval |
| `FLIP_POLICY=selective` live on `bybit_2` | Tier-3 | operator approval; rollback = `hold` |
