# M27 Batch-4 — XAUUSD 15m findings (2026-07-21)

**Question:** does `ict_scalp` transfer to XAUUSD at its native 15m timeframe,
re-validated **config-exact** against the current live-mirror harness with
proper k-fold OOS — not the M15 Phase-0 screening harness's single train/OOS
split?

**Answer: yes — a clean, statistically meaningful PASS, and it does not need
a regime gate.** The anchored 4-fold walk-forward OOS baseline (ungated) is
**4/4 folds positive**, 240 OOS trades, **net total_r +44.35R**, expectancy
**+0.185R/trade** at a 2.0 bps round-trip fee assumption. This both confirms
and statistically strengthens the M15 Phase-0 finding (+39.4R train / +10.2R
OOS, single split, no k-fold) using the current config-exact,
live-exit-faithful harness.

## Rig

- **Data:** Dukascopy XAUUSD 15m via `scripts/ops/fetch_dukascopy_ohlcv.py`
  (`INSTRUMENT_FX_METALS_XAU_USD` — correct on first try), 2019-01-01 →
  2026-07-21, **178,466 bars** (trainer relay #7295). Deep, keyless, free —
  no new credentials needed.
- **Harness:** `scripts/backtest_ict_scalp.py --timeframe 15m --stamp-regime
  --sim-breakeven` — config-exact, live-exit-faithful, run at the symbol's
  **native** 15m resolution (not derived from 5m bars like Batch-1/2/3).
  Required adding a `--timeframe` passthrough to
  `scripts/research/m27/run_symbol_p0.py` (PR #7293) so the shared driver
  script isn't hardcoded to a 5m label; ran the two stages
  (`backtest_ict_scalp.py` then `kfold_oos.py`) directly rather than through
  the driver, since the driver's spec15 resample-of-5m-bars step doesn't
  apply when the input is already native 15m.
- **Frozen vol-spec:** tercile edges of `rolling_log_return_vol` (w=20) over
  the earliest 25% prefix of the data (~44,600 bars) — well over the 5,000-bar
  floor and strictly inside fold-1's train territory for a 4-fold anchored
  walk.
- **Fees:** 2.0 bps round-trip — the same assumption the M15 Phase-0 doc used
  for XAUUSD 15m, kept for direct comparability. Not independently re-derived
  from a live OANDA spread measurement this session (a documented estimate,
  same caveat the original M15 doc carried: "OANDA spreads ≠ flat 2 bps").
- **k-fold:** `scripts/research/ict_scalp_phase0/kfold_oos.py --folds 4`
  (anchored, walk-forward). Because the data is already native 15m, the
  script's `vol15` resample-of-5m step is a no-op — `vol15 == vol5` in every
  row, so the "off_cells" 2-axis rule (drop chop/volatile on BOTH axes)
  degenerates to the same 1-axis filter applied twice. Documented, not a bug;
  the calm-only rules are correspondingly thin (n=8 across all 4 folds) and
  not independently informative here.
- **Artifacts:** trainer `/home/ubuntu/m27_out_xau/XAUUSD/` (`volspec_15m.json`,
  `emit.json`, `backtest.json`, `kfold.json`); relay issues #7295 (data),
  #7302+#7303 (backtest launch+check), #7304+#7305 (kfold launch+check).

## Gross backtest (full period, no fees)

284 trades, 167 wins / 117 losses (58.8% win rate), expectancy +0.224R,
gross total +63.53R, max drawdown 3.89R, Sharpe(R) 0.27. Outcome mix: 171
timeout, 54 tp_hit, 44 sl_hit, 15 be_stop — timeout-dominated, consistent
with a scalp exit ladder on a slower (15m) timeframe than the 5m BTC leg.

## k-fold OOS (net of 2bps, anchored 4-fold walk-forward)

| Rule | Folds+ | n (OOS) | Net total R | Net exp R |
|---|---|---|---|---|
| **baseline (ungated)** | **4/4** | **240** | **+44.35** | **+0.1848** |
| conf070_fixed (fixed 0.70 threshold) | 4/4 | 102 | +21.24 | +0.2082 |
| fitted_conf_oos (per-fold optimal threshold) | 4/4 | 208 | +33.68 | +0.1619 |
| off_cells_5m (regime OFF-cells gate) | 4/4 | 76 | +11.76 | +0.1547 |
| off_cells_5m_and_conf070 | 2/4 | 32 | +2.02 | +0.0631 |
| calm_only_5m / calm_only_15m (identical — see note above) | 2/4 | 8 | +1.81 | +0.2263 |

Per-fold baseline detail (all four test windows positive):

| Fold | Test window | n | Net total R | Net exp R | Win % |
|---|---|---|---|---|---|
| 1 | 2020-07-30 → 2022-01-23 | 65 | +9.70 | +0.1492 | 55.4% |
| 2 | 2022-01-23 → 2023-07-19 | 49 | +14.67 | +0.2993 | 59.2% |
| 3 | 2023-07-19 → 2025-01-11 | 47 | +7.52 | +0.1600 | 66.0% |
| 4 | 2025-01-11 → 2026-07-07 | 79 | +12.46 | +0.1577 | 55.7% |

**Verdict: the ungated baseline is the strongest cell, not a gated variant.**
Unlike BTC/ETH/XRP (where the regime OFF-cells gate is load-bearing — pass
only with it, fail without) or ADA (mixed — the BTC-shaped gate doesn't
transfer), XAUUSD's `off_cells_5m` gate actively **underperforms** baseline
on both total R (+11.76 vs +44.35) and expectancy (+0.1547 vs +0.1848) while
cutting trade count 76→24/fold-avg — the same failure-to-transfer pattern as
Batch-1's ADA result, but here the ungated cell is unambiguously the winner
rather than merely "mixed." `conf070_fixed` and `fitted_conf_oos` both trade
fewer, higher-quality-looking trades but leave total R on the table relative
to baseline — not worth the added complexity for a leg this thin an
information edge doesn't need trimming.

**This closes the loop with M15 Phase-0's original recommendation** ("gold
alone gives the 1h-4h trend/pullback family a validated new home... the ICT
scalp logic survives fees there") — now with k-fold discipline instead of a
single split, and against the current config-exact harness instead of the
2026-06-10 screening harness.

## Promotion-path caveat — the OANDA venue is currently shelved

**This research PASS does not translate into an immediately promotable
leg.** `config/strategies.yaml::xauusd_trend_1h` (a different XAUUSD
strategy, 1h trend, the M15 Phase-3 leg) is `execution: live` but
**disabled** because its only routed account, `oanda_practice`, was
**shelved to `mode: dry_run` on 2026-06-12** (`set-account-mode` #3446) —
**OANDA US cannot trade `XAU_USD`** (BL-20260611-007). Gold coverage today
lives on IBKR `mgc_trend_1h` (micro-gold futures) + Alpaca `GLD` instead.

A hypothetical `ict_scalp_xauusd_15m` leg inherits the identical constraint:
there is currently **no live-tradeable OANDA account for XAU_USD** to route
it to. Two paths forward, neither pursued this session (out of scope — this
was the research-verdict step, not the promotion step):

1. **Wire a spot-gold-capable OANDA entity** (non-US, if the operator has
   access) — the sanctioned fix BL-20260611-007 already names.
2. **Re-target the setup to MGC** (IBKR micro-gold futures, already live via
   `mgc_trend_1h`) — but Batch-2 already tested `ict_scalp_5m` on MGC and it
   was **rejected as underpowered + gross-negative** (14 trades/yr, −4.73R).
   A native-15m MGC variant (mirroring this XAUUSD 15m rig, on futures data)
   is untested and would be a distinct follow-up study, not a re-use of this
   result — MGC's IBKR 5m data was itself found majority flat-bar-contaminated
   (`PB-20260721-M27-FUTURES-5M-LOWSIGNAL`), a data-quality question this
   XAUUSD/OANDA rig does not share (Dukascopy's spot-FX/metals feed showed no
   comparable contamination in this run).

## Recommendation (no Tier-3 — proposals only)

- **XAUUSD 15m `ict_scalp` is a validated leg** — ungated, k-fold OOS
  4/4 positive, +44.35R net over 240 OOS trades spanning 2020-2026. Ready for
  a `new-strategy` shadow-soak packet **once a live-tradeable XAU_USD venue
  exists** (see caveat above) — this is the strongest, cleanest passer in the
  M27 P0 batch to date by folds-positive + net R.
  M27 milestone bookkeeping: mark XAUUSD 15m ✅ in
  `docs/research/artifacts/m27/coverage.md`; do not re-run this exact rig
  expecting a different answer.

## Coverage impact

XAUUSD 15m resolves ✅ **PASS (ungated baseline, 4/4 folds, +44.35R net,
exp +0.1848R/trade)** — see `docs/research/artifacts/m27/coverage.md`.
Promotion is venue-blocked (OANDA US / XAU_USD), not evidence-blocked.
