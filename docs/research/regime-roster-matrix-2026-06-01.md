# Roster regime × direction matrix — 2026-06-01

> **Initiative:** regime-aware routing (`PERF-20260601-002`). Continues the
> 2026-06-01 handoff (`docs/research/session-handoff-2026-06-01.md`). This is the
> evidence foundation: which strategy earns in which ADX regime, split by
> direction, so a router can lean the roster toward the strategies that fit the
> detected regime.

## Method

Each strategy's standalone harness was driven with its **EXACT live params from
`config/strategies.yaml`** (the `min_confidence` reconciliation lesson — wrong
params give a misleading matrix), emitting per-trade JSONL via `--emit-trades`,
then tagged by the ADX regime **at each trade's entry bar** with
`scripts/research/regime_tag_emitted.py` (engine-agnostic; reuses the
ADX/regime primitives from `regime_matrix.py`). `trend_donchian`'s engine has no
emit hook, so `regime_matrix.py` drove it in-process.

- **Regime cut-points (ADX-14, same primitive the live strategies gate on):**
  chop < 20, transitional 20–25, trending ≥ 25.
- **Net R** is fee-adjusted (7.5 bps round-trip), R-normalised.
- **Data (trainer VM):** `data/btc_1h_multiyear.csv` (47,459 1h bars,
  2021-01 → 2026-06) for trend/fade/squeeze; `data/backtest_BTCUSDT_5m.csv`
  (647,585 5m bars, 2020-03 → 2026-05) resampled for fvg (15m) and vwap (5m).
- Run on the trainer via the `trainer-vm-diag` relay (issues #2562–#2566) from a
  detached worktree of the tooling branch. Two pandas-3.0 fixes were needed
  mid-run (`_adx` float dtype; fvg 15m resample) — committed.

## The matrix (BTC, net R, full window unless noted)

| Strategy | TF | Total | trending (L / S) | transitional (L / S) | chop (L / S) | maxDD | structural read |
|---|---|---:|---|---|---|---:|---|
| **trend_donchian** (live) | 1h | **+10.9** | −5.7 (**+22.3** / **−28.0**) | −2.3 (+21.7 / −24.1) | **+18.9** (+3.3 / **+15.6**) | 20.6 | long = the trend edge; short is a *different* strategy that only earns in chop |
| **fade_breakout_4h** (shadow) | 4h | **+19.4** | — *(ADX-gated out)* | +5.2 (−1.0 / +6.2) | **+14.2** (**+14.0** / +0.2) | 30.2 | chop mean-reversion; long-led in chop |
| **squeeze_breakout_4h** (shadow) | 4h | **+17.6** | +5.1 (+7.8 / −2.7) | +1.6 (−0.4 / +2.1) | +10.9 (+7.6 / +3.4) | **7.9** | earns in **every** regime, long-biased, **lowest DD** |
| **fvg_range_15m** (shadow) | 15m | **−16.9** | — *(ADX-gated out)* | — | −16.9 (−11.5 / −5.4) | 13.2 | chop MR; **net loser both sides** — no edge here |
| **vwap** (shadow) | 5m | ⚠️ **−3749** *(unfiltered — see caveat)* | −1972 (−1037 / −936) | −649 (−298 / −351) | −1128 (−459 / −670) | — | **NOT decision-grade** — driven without the live selectivity filters |

(n trades: trend 1104, fade 157, squeeze 110, fvg 60, vwap 10188. fade/fvg fire
only at ADX<20 by design; trend/squeeze/vwap fire across all regimes. vwap window
2024-01-01→2026-05.)

> ⚠️ **vwap is the unfiltered harness, not the live strategy.** 10,188 trades in
> ~2.4 years (~11/day) means it ran on bare `--no-htf` with **none** of the live
> selectivity gates (`recent_context_filter` 1h/24-bar, `threshold: 0.01`,
> `min_r_for_vwap_cross`, `be_at_r`). It is the raw VWAP-touch strategy, which
> bleeds in every regime — the same "wrong params → misleading matrix" trap the
> reconciliation flagged for trend. **Re-run with the live selectivity params
> threaded before trusting vwap's regime profile** (follow-up below). It does not
> affect decisions 1–3, which rest on the exact-live-param runs.

## Per-strategy structural reads

- **trend_donchian** — reproduces the flagship exactly (+10.9 net; long **+47.3**
  / short **−36.5** across regimes). The long side is the trend runner (wins
  trending **+22** and transitional **+22**); the short side is effectively a
  separate, regime-specific strategy: **+15.6 in chop but −52 across
  trending+transitional** (BTC's structural uptrend punishes trend-shorts).
- **fade_breakout_4h** — net **+19.4** over five years, concentrated in **chop
  (+14.2, long-led)** with a smaller transitional contribution. The ADX<20 gate
  keeps it out of trending entirely. Demoted to shadow on a ranging-month live
  read, but the multiyear edge is real and chop-shaped.
- **squeeze_breakout_4h** — the standout: net **+17.6** with the **lowest
  drawdown (7.9 R)** and **net-positive in every regime** (trending +5.1,
  transitional +1.6, chop +10.9), long-biased. Demoted alongside fade, but the
  regime profile is the most router-friendly of the roster (no regime hurts it).
- **fvg_range_15m** — net **−16.9**, every direction negative even in its target
  chop regime. Confirms the standing finding that fine-TF mean-reversion is not
  this market's edge. Shadow status justified; no re-promotion case.

## Decisions this feeds (Tier-3 — operator-gated; proposed, not applied)

### 1. trend_donchian: long-only vs chop-gated-short

| Option | Net R (modelled from the matrix) | Pros | Cons |
|---|---|---|---|
| **A. Long-only** | **+47.3** (drops the −36.5 short drag) | Simplest; removes the whole losing short leg; highest-confidence | Leaves the **+15.6 chop-short** edge on the table |
| **B. Long always + short only in chop** | **≈ +62.9** (+47.3 long + 15.6 chop-short; drops the −52 trending/transitional short) | Captures the theoretically-optimal split | Adds a per-direction regime gate inside the strategy — i.e. it *is* a mini regime-router, better built once (decision 3) than hard-coded here |

**Recommendation:** ship **A (long-only) now** as the clean, robust win (+47 R, removes a −37 R drag), and realise **B as a regime-router cell** (decision 3) rather than special-casing one strategy. B's extra +15.6 R is real but regime-conditional and smaller; it belongs in the router where every strategy's regime cells are handled uniformly.

### 2. Re-promotion criteria for the demoted squeeze / fade

Both were demoted live→shadow on a **single ranging-month read compounded by the re-entry storm** — which the bar-close debounce (#2548) has since fixed. Their multiyear regime profiles are net-positive:

- **squeeze_breakout_4h** — the strongest re-promotion candidate: **+17.6 R, net-positive in every regime, lowest DD (7.9 R)**, long-biased. No regime hurts it.
- **fade_breakout_4h** — **+19.4 R but chop-concentrated and higher DD (30.2 R)**; size conservatively and/or lean on its ADX<20 gate.

**Proposed re-promotion gate (both):**
1. **Debounce verified** — `BL-20260601-001`: the orphan / `intent_noop` / re-entry rate over the post-#2548 window is back to baseline (confirms the demotion cause is gone).
2. **Shadow vs backtest agreement** — accrued shadow net R since demotion does not *contradict* the multiyear sign (squeeze ≥ ~0; fade ≥ ~0 with the chop concentration visible).
3. **Roster correlation** — monthly net-R correlation to the live roster stays low/additive (squeeze's historical corr ≈ 0.30; re-check).
4. **DD-aware sizing** — promote at the current `risk_pct` for squeeze; for fade, given DD 30 R, either trim `risk_pct` or wait for the router to gate it to chop.

squeeze meets 2–4 on the backtest today; the gate is **1 (debounce verification)** + a short shadow-confirmation window. fvg_range_15m (**−16.9 R, loser both sides**) has **no re-promotion case** — keep shadow.

### 3. Regime-router design (initiative step 2)

**Goal:** an intent-layer mechanism that leans the roster toward the strategies/directions that fit the **currently detected regime**, replacing per-strategy ad-hoc ADX gates with one coherent policy derived from this matrix.

**Building blocks (already in the system):** the per-strategy ADX gates (fade/fvg already require ADX<20) + the `regime-classifier-baseline-v0` shadow model.

**Design sketch:**
1. **RegimeDetector** (central) — emits the current regime per `(symbol, timeframe)` from ADX-14 (chop<20 / transitional 20–25 / trending≥25), with `regime-classifier-baseline-v0` as a drop-in/augment once validated. One source of truth instead of each strategy recomputing.
2. **Regime × strategy × direction policy table** — seeded directly from this matrix. Each cell is a gate/weight:
   - **trending:** trend_donchian **long** ON (+22), trend **short** OFF (−28); squeeze ON (+5); fade/fvg already OFF (ADX gate).
   - **transitional:** trend long ON (+22), trend short OFF (−24); squeeze ON (+1.6); fade small (+5).
   - **chop:** trend long small (+3), trend **short ON** (+16 ← this is option-B's chop-short); fade ON (+14); squeeze ON (+11); fvg OFF (−17, loser).
3. **Enforcement point** — the intent layer (`src/runtime/intents.py`), folded into the existing `effective_dry` / confidence path as a **multiplier or hard gate per (strategy, direction, regime)** — *no new order path* (same contract as the `execution:` gate). Start with **hard gates on net-negative cells** (mechanical, auditable), then graduate to **soft confidence weights**.
4. **Observability** — log the detected regime + applied weights per tick so the dashboard/journal can show "why" a strategy was leaned in/out (mirrors how `execution: shadow` is surfaced).

This router *is* the home for trend_donchian's chop-gated-short (decision 1B) and for fade's chop-gating — they become two cells of the same table rather than bespoke per-strategy code.

## Caveats / coverage gaps (follow-ups)

- **vwap — re-run with live selectivity params (BLOCKING for vwap's row).** This
  run used bare `--no-htf` (10,188 trades / −3749 R), i.e. the unfiltered
  VWAP-touch strategy. The live vwap is heavily selectivity-gated
  (`recent_context_filter`, `threshold`, `min_r_for_vwap_cross`, `be_at_r`) —
  thread those into the harness (it exposes `--entry-threshold`, etc.) so the
  trade count drops to the live cadence before the regime read is trustworthy.
- **htf_pullback_trend_2h — no committed standalone harness.** The overnight
  `backtest_pullback.py` was never committed; commit/port it before it joins the
  matrix.
- **mes_trend_long_1d** is long-only on a separate MES daily data source
  (native-MES validation already positive, +13.8 R full / +2.9 OOS) — short side
  gated off by design and on different data, so it's tracked separately.
- Single-fee (7.5 bps) R-based accounting; the matrix establishes the **sign and
  regime-shape** of each edge, not a deployable magnitude.

## Reproduction

`scripts/research/regime_matrix.py` (trend, in-process) + `scripts/research/regime_tag_emitted.py`
(every other harness's `--emit-trades` JSONL), driven with the exact live params above.
Relays: #2562 (discovery), #2564 (trend/fade/squeeze), #2565 (fvg, after the pandas-3.0
fixes), #2567 (vwap launch) → #2569 (vwap tag).
