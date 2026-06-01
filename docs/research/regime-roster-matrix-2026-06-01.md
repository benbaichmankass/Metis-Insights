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
| **htf_pullback_trend_2h** (shadow) | 2h | **+26.3** | **+30.1** (+30.1 / −0.05) | +8.4 (+12.7 / −4.3) | **−12.2** (−7.9 / −4.3) | **14.8** | trend-continuation **owns trending**, loses chop; long-led; clean ADX>20 inclusion candidate |
| **vwap** (shadow) | 5m | **−10724** *(live gates threaded — see structural read)* | **−6179** (−2970 / −3209) | −1903 (−910 / −992) | −2642 (−1183 / −1459) | n/a | **net loser in every regime** even with the live gates active; gross is +3,399 R but the 7.5 bps round-trip fee × 40,650 trades = −14,123 R kills it |

(n trades: trend 1104, fade 157, squeeze 110, fvg 60, htf_pullback 348,
vwap **40,650**. fade/fvg fire only at ADX<20 by design; trend/squeeze/htf_pullback
fire across all regimes; vwap fires across all regimes too. vwap window
**2020-03 → 2026-05** — the multiyear archive, same window the unfiltered run
used. htf_pullback added 2026-06-01 via #2573; vwap re-run with the live gates
landed 2026-06-01 via #2575/#2579 once `src/backtest/run_backtest_vwap.py`
shipped four new flags (`--min-r-for-vwap-cross`, `--min-hold-minutes-for-vwap-cross`,
`--be-at-r`, `--be-offset-bps`) and a BE ratchet in `_simulate_trade`.)

> **vwap row update (PERF-20260601-003, 2026-06-01).** The prior row (−3749 R
> over 2.4 years, 10,188 trades, no live gates) was the unfiltered harness. The
> updated row above is the full multiyear window (6+ years, 647,585 5m bars)
> with the four live exit-side gates threaded:
> `--min-r-for-vwap-cross 0.25 --min-hold-minutes-for-vwap-cross 10
> --be-at-r 1.0 --be-offset-bps 15`. Result:
> **40,650 trades, gross +3,399 R, fees −14,123 R, net −10,724 R, win rate
> 49.8%, every regime net-negative at exp ≈ −0.26 R/trade.** The gates DO
> work on the gross side (the live failure mode they target — "VWAP drifted to
> price" sub-fee crosses — was real and they fix it), but the BE ratchet's
> small-win exits at +15 bps stop cycle trades 4× faster, the fee per trade
> stays the same, and the fee:gross ratio of ~4.2× swamps the edge. Per-regime
> the exp_r is uniform (−0.27 trending / −0.26 transitional / −0.26 chop) —
> **no regime where vwap earns net of fees**. Confirms
> `docs/audits/vwap-viability-verdict-2026-05-23.md`'s 2026-05-23 verdict on
> the precise live-gate path over the multiyear archive.
>
> The `recent_context_filter` was intentionally **not** threaded: per
> `config/strategies.yaml::vwap.recent_context_filter` it is "Informational
> only — does not block entries", confirmed by
> `src/runtime/strategy_signal_builders.py` line 631 ("Informational only —
> neither side is blocked"). It tags the meta but does not gate the signal.
>
> The starter-prompt expectation that gating would drop the trade count from
> ~11/day to live cadence was wrong: the BE ratchet exits trades sooner (at
> BE+15 bps), the cooldown shortens, and the trade count rises (~18/day for
> the gated run). The live trader does not have years of vwap fills to
> compare cadence against; the gated-harness output here IS the
> live-equivalent backtest under the exact live params.

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
- **vwap** — net **−10,724** over 6+ years (40,650 trades, 49.8% WR — but the
  win rate is a fee mirage: the BE-stop ratchet locks in many sub-fee small
  wins). Gross is **+3,399** R; round-trip fee at 7.5 bps × the average
  (entry+exit)/risk = **−14,123** R, a 4.2× fee-to-gross ratio. Loser in
  every regime: trending −6,179 (long −2,970 / short −3,209), transitional
  −1,903, chop −2,642. Long and short bleed equally. **Net of fees this
  strategy has no edge in any regime** — gated or unfiltered. The
  router should treat vwap as net-negative everywhere (same shape as fvg
  but ~600× the magnitude).
- **htf_pullback_trend_2h** — net **+26.3** over 5+ years (348 trades, 32.8% WR,
  maxDD 14.8 R). Almost the entire edge is **trending long** (+30.1 R; the short
  side is flat at −0.05 R in trending — BTC's uptrend punishes shorts here just
  like trend_donchian). Transitional adds **+8.4 R** (long-led). **Chop is a
  loser at −12.2 R both sides** — this is a trend-continuation strategy and the
  chop entries are noise: the policy answer is the same shape as fvg (gate it
  out of chop, not the rest). Confirms the PERF-20260531-002 walk-forward
  finding (IS +32.7 / OOS +22.4 at tl=50; +26.3 here at the live tl=40).

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

- ~~**vwap — re-run with live selectivity params (BLOCKING for vwap's row).**~~
  **DONE 2026-06-01** — `src/backtest/run_backtest_vwap.py` now exposes four
  new flags (`--min-r-for-vwap-cross`, `--min-hold-minutes-for-vwap-cross`,
  `--be-at-r`, `--be-offset-bps`) + a BE ratchet in `_simulate_trade` +
  `_vwap_cross_gates_allow` mirroring the live `vwap._vwap_cross_gates_pass`.
  Row above updated. Verdict: vwap is a net loser in every regime even with
  the live gates threaded (the gates work on the gross side but the fee
  structure swamps them); the prior `−3749 R unfiltered` finding holds up at
  the larger ~−10,724 R magnitude on the longer window. `recent_context_filter`
  intentionally not threaded — it's informational-only in the live strategy
  (see structural read above).
- ~~**htf_pullback_trend_2h — no committed standalone harness.**~~ **DONE**
  2026-06-01 — `scripts/backtest_pullback.py` committed; row added above.
- **mes_trend_long_1d** is long-only on a separate MES daily data source
  (native-MES validation already positive, +13.8 R full / +2.9 OOS) — short side
  gated off by design and on different data, so it's tracked separately.
- Single-fee (7.5 bps) R-based accounting; the matrix establishes the **sign and
  regime-shape** of each edge, not a deployable magnitude.

## Reproduction

`scripts/research/regime_matrix.py` (trend, in-process) + `scripts/research/regime_tag_emitted.py`
(every other harness's `--emit-trades` JSONL), driven with the exact live params above.
Relays: #2562 (discovery), #2564 (trend/fade/squeeze), #2565 (fvg, after the pandas-3.0
fixes), #2567 (vwap unfiltered launch) → #2569 (vwap unfiltered tag), #2573
(htf_pullback, after committing `scripts/backtest_pullback.py`), #2575 (vwap
live-gated detached launch) → #2579 (vwap live-gated result, after committing
the four exit-side-gate CLI flags + BE ratchet in `src/backtest/run_backtest_vwap.py`).
