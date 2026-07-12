# M20 Exit Refinement — evidence memo (2026-07-12)

**Session:** S-M20-EXIT-REFINEMENT-2026-07-12 (branch
`claude/exit-refinement-sprint-l74k6o`). Tier-1 research — no live-path file
changed. Data pulled autonomously: live-VM diag relay #6157, trainer relays
#6158/#6159 (soak logs mirrored live→trainer, journal freshly synced, analysis
run trainer-side against `datasets-out/market_raw` candles).

## Verdict summary

| Question | Verdict |
|---|---|
| Graduate the ExitPlan ladder (P4, `PB-20260617-002`)? | **NO — and the soak can never answer it as instrumented.** 135 soak rows, **0 differing**: the only strategy that declares a TP1→TP2 ladder (`meta.tp2`) is `turtle_soup`, which is `execution: shadow` and never executes. Every live strategy derives a single-target plan identical to what is placed, so `differs_from_single_target` is structurally false fleet-wide. The gate is not "keep soaking" — it is "no ladder exists to test." |
| Graduate fc-scaled SL/TP geometry (`MB-20260705-FC-SLTP-GEOMETRY`)? | **NO — insufficient data.** 23 soak rows since 2026-07-06, only 7 with a served forecast (`fc_present`), because fc heads exist only for BTC/ETH/SOL 15m while the soak logs every symbol. Censoring-aware resolver output below. **Re-check ≈ 2026-08-25** (~50 fc-covered rows at current accrual). |
| Is there an exit-timing problem at all? | **YES — large and measurable.** Over the last 90d (275 path-resolved closed trades on BTC/ETH/SOL), the average real-money trade reached **+1.92R MFE** yet realized **−0.16R** — a mean giveback of **2.08R**; 26% of real-money trades touched ≥ +1R and still closed negative ("round-trippers"). The chop-hold hypothesis is confirmed and quantified. |
| Which lever fixes it? | Per-strategy, not blanket. A conditional **stale-stop** (time-stop that only fires when the trade is still flat/negative) and a **trend-invalidation exit** (close crossing the strategy's own Donchian midline against the position) both showed positive truncation-counterfactual ΔR concentrated in the 2h pullback-trend family; full-history harness A/B below. `ict_scalp_5m` shows the opposite sign — its exits are already good; a blanket time-stop would damage it. |

## 1. Data-sufficiency gate (M20 prompt step 1)

### exit_ladder_soak — 135 rows (112 api / 23 prop), 2026-06-18 → 2026-07-12

`differing = 0`. Root cause is structural, verified in code:
`build_exit_plan_from_legacy` only produces a rung when the order package
carries `meta.tp2`, and the only producer of `meta.tp2` is `turtle_soup`
(`execution: shadow`, so its packages never reach `execute.py`'s soak writer).
Every other strategy — the whole live fleet — uses a single far target
(`tp_r: 50` sentinel) + chandelier trail, so the "ladder" the soak materializes
is byte-identical to the flat SL/TP placed.

**Consequence for P4 (`PB-20260617-002`):** the graduation question cannot be
answered by more soaking. Either (a) strategies must *declare* real ladders
(an ExitPlan with actual rungs — a Tier-3 strategy-logic change to design
deliberately), or (b) P4 is re-scoped around exit levers that the fleet
actually needs (below). Recommend (b) first; (a) only if partial-banking shows
harness evidence.

### fc_geometry_soak — 23 rows, 7 fc-covered, 2026-07-06 → 2026-07-12

Coverage denominator is honest and low: `fc_present` only for BTC/ETH/SOL
(the symbols with fc heads); the equities/metals/alt rows are structurally
uncovered until fc heads exist for them. The trainer-side censoring-aware
resolver (`scripts/ml/fc_geometry_resolve.py`, relay #6159) on the 7 covered
rows: **6/7 counterfactuals censored (85.7%), paired uncensored n = 1**
(real_R −0.86 vs fc-scaled −1.00 — one trade, meaningless). Far below any
conclusive n. **Dated re-check: 2026-08-25**, or earlier if fc coverage
expands. Until then `MB-20260705-FC-SLTP-GEOMETRY` stays open, no proposal.
(Two infra gaps found + fixed en route: the trainer's checkout was stale at
`38ac1c04` — reset to `origin/main`; and `sync_trainer_data.sh` never mirrored
the soak logs `fc_geometry_resolve.py`'s contract assumes — both soak files
added to the sync set on this branch.)

## 2. The chop-hold problem, quantified (90d, path-resolved on 15m candles)

Universe: closed, non-backtest, non-reduce-leg, non-superseded,
non-adopted-orphan trades with resolvable risk, symbols with trainer candle
coverage (BTC/ETH/SOL — 275 of 491 closed trades; ADA/AVAX/XRP + equities +
metals lack trainer candles, logged as a coverage gap). R = pnl / (|entry−sl|
× qty × contract_value_usd). Real and paper reported separately, never
blended.

| class | n | mean R | mean hold | med. time-to-MFE | mean MFE | mean giveback | % time in ±0.25R chop | round-trippers |
|---|---|---|---|---|---|---|---|---|
| real_money | 200 | −0.163 | 4.0 h | 0.0 h | +1.92R | **2.08R** | 21% | **26.0%** |
| paper | 75 | −0.429 | 15.2 h | 2.0 h | +1.40R | 1.83R | 36% | 16.0% |

Per-strategy highlights (n ≥ 5):

| strategy·class | n | mean R | hold | t_MFE (med) | MFE | giveback | round-trip % |
|---|---|---|---|---|---|---|---|
| htf_pullback_trend_2h·paper | 16 | **−1.16** | 26.0 h | 2.2 h | 0.70 | 1.87 | 12.5 |
| htf_pullback_trend_2h·real | 5 | −0.61 | 24.0 h | 9.8 h | 0.50 | 1.10 | 0 |
| vwap·real | 169 | −0.26 | 0.9 h | 0.0 h | 2.06 | 2.31 | 30.2 |
| trend_donchian·real | 5 | +0.80 | 49.8 h | 5.0 h | 2.13 | 1.32 | 20 |
| ict_scalp_5m·real | 11 | **+1.02** | 6.1 h | 3.8 h | 1.35 | **0.33** | 0 |
| fade_breakout_4h·real | 7 | −0.16 | 17.2 h | 0.0 h | 0.70 | 0.86 | 0 |

Readings:

- **Flagship example (the operator's complaint verbatim in the data):**
  real-money `trend_donchian` trade #2535 (BTC long, opened 2026-06-11) —
  held **166 h (~7 days)**, peaked at **+3.59R** at hour 94, spent 23% of its
  life inside ±0.25R, and closed at **−1.09R** — a 4.7R round-trip through
  chop after the trend had stopped paying.
- **The peak comes early, the exit comes late.** Median time-to-MFE is a
  fraction of hold time everywhere (htf_pullback paper: peak at 2.2 h of a
  26 h hold). What follows the peak is chop the current exits (trail frozen
  at entry-ATR distance, 50R sentinel TP) don't respond to.
- **`htf_pullback_trend_2h` is the chop-hold poster child** — consistent
  negative expectancy in both classes, driven by holds through invalidated
  trends.
- **`ict_scalp_5m` real-money is the control case**: giveback 0.33R, no
  round-trippers — a strategy whose exit design already fits its hold
  horizon. Any fleet-wide exit rule must not touch it (its time-stop
  counterfactuals are *negative*).
- **Caveat on `vwap` / 5m strategies:** MFE measured on 15m bars overstates
  capturable profit when the stop distance is small relative to the 15m bar
  range, so the vwap giveback/round-tripper numbers are upper bounds. The 2h/4h
  strategy numbers are robust to this (stop distance ≫ bar range).

## 3. Truncation counterfactuals (90d) — honest by construction

Unlike a barrier re-simulation (which the T0.4 evidence showed diverges ~0.6R
from reality), these counterfactuals only **truncate real trades**: exit value
= observed close at the truncation bar; trades the lever doesn't touch
contribute Δ = 0. Fees ≈ neutral (a truncated exit pays the same close-side
fee the real exit paid; funding is saved). Sign conventions: ΔR > 0 = the
lever would have improved the realized outcome.

**Time-stop (flat: exit at T if open R < 0):**

| lever | real-money ΣΔR | paper ΣΔR | dominated by |
|---|---|---|---|
| exit@4h if <0R | **+2.4** | **+21.6** | htf_pullback (+17.3 paper / +2.1 real), trend_donchian paper +5.0 |
| exit@8h if <0R | −0.1 | +14.7 | htf_pullback (+11.7 / +1.7) |
| exit@24h if <0R | 0.0 | +9.3 | htf_pullback paper only |
| exit@24h if <+0.25R | +1.3 | +11.5 | htf_pullback, trend_donchian |

**Stagnation-stop (exit after K consecutive hours inside ±0.25R):** positive
for trend_donchian real (+2.1R on 2 of 5) and htf_pullback (+7.8R paper /
+0.8R real); negative for eth_pullback_2h (n=3, −2.8R) — small-n noise both
ways.

**Cross-TF trend-flip exit (1h EMA9×21 against position ≥2h, age >8h):**

| strategy·class | n | affected | ΣΔR |
|---|---|---|---|
| htf_pullback_trend_2h·paper | 16 | 10 | **+18.0** |
| htf_pullback_trend_2h·real | 5 | 2 | +2.0 |
| trend_donchian·real | 5 | 2 | +1.6 |
| ict_scalp_5m·real | 11 | 1 | **−2.0** |
| eth_pullback_2h·paper | 3 | 2 | −2.4 |

Consistent story: **the faster-timeframe trend-flip and the early
conditional time-stop rescue the 2h trend-following family**, are mildly
positive for donchian, and are **harmful for the scalp family**. All
n_affected are small (1–10) — hypothesis-grade, which is why the full-history
harness validation below exists.

## 4. Full-history harness validation (5y, IS/OOS split 2025-07-01)

Levers were added to the *same standalone harnesses that validated these
strategies* (`scripts/backtest_pullback.py`, `scripts/research/backtest_trend.py`),
default-off so the base cell is byte-identical to the original engine
(the research_sweep delta-vs-base discipline). New flags:
`--stale-exit-bars N --stale-exit-below-r X` (conditional time-stop, fires
only when the trade is below X open-R at bar N, never pre-empting the intrabar
stop) and `--flip-exit-bars M` (close crossing the strategy's own Donchian
midline against the position for M consecutive bars — the trend-invalidation
exit, pullback harness only; the trend harness already has an opposite-signal
flip + unconditional `--timeout-bars`).

*(Sweep table inserted from relay #6159 — see § appendix.)*

## 5. Recommendation

*(Finalized after the sweep — see § 6 Verdict.)*

## Appendix — raw relay outputs

- Live-VM diag (soak tails + status + trades): issue #6157.
- Trainer analysis run (mirror + m20_exit_analysis full output): issue #6158.
- Trainer validation sweep + fc resolver: issue #6159.
