# M15 WS-B — SPY/QQQ Intraday K-Fold Walk-Forward Verdict (2026-06-11)

> Workstream B of the M15 soak session (brief:
> [`session-handoff-m15-soak-2026-06-11.md`](session-handoff-m15-soak-2026-06-11.md)).
> Decides whether the SPY (and QQQ) intraday legs earn a shadow slot on
> `alpaca_paper`. Method and gate were fixed by the operator in advance:
> **k-fold anchored walk-forward, net of fee; PASS = positive in EVERY
> OOS fold + ~2× fee headroom.** Driver:
> `scripts/ops/m15_ws_b_walkforward.sh` +
> `scripts/ops/m15_ws_b_fold_report.py` (trainer-vm-diag #3350, #3362,
> #3363); raw outputs on the trainer under
> `/home/ubuntu/m15-phase0/results/m15_ws_b/`.

## Verdict

**All four cells FAIL the promotion gate. No Tier-3 shadow PR is
proposed.** SPY ict_scalp 5m is the only near-miss — 4/5 folds positive
with robust fee headroom — but the gate is "every fold", and it isn't
met. QQQ confirms the Phase-0 read (one-window wonder on ict_scalp;
fvg_range outright negative) and stays research-only.

| Cell | Folds +ve (2 bps) | Total OOS net R @2 bps | @4 bps (2×) | Every-fold | 2× headroom | Verdict |
|---|---|---|---|---|---|---|
| **ict_scalp SPY 5m** | **4/5** | **+11.3** | **+8.6** | ❌ | ✅ | **FAIL (near-miss)** |
| fvg_range SPY 15m | 3/5 | +7.0 | +4.4 | ❌ | ✅ | FAIL |
| ict_scalp QQQ 5m | 3/5 | +19.0 | +14.5 | ❌ | ✅ | FAIL |
| fvg_range QQQ 15m | 1/5 | **−12.9** | −15.4 | ❌ | ❌ | FAIL |

## Method

- **Data**: the pass-2 corrected RTH datasets
  (`data/{SPY,QQQ}_5m_rth.csv`, Dukascopy ETF-CFD 5m since 2019,
  month-based DST session windows — commit `7dccf4d`).
- **Folds**: the M8 `KFold` convention
  (`scripts/ml/strategy_tune_sweep.py`): span 2019-01-01 → 2026-06-11,
  first 40 % as burn-in, the remainder split into **5 equal OOS folds**
  (≈10.6 months each, 2021-12-23 → 2026-06-11).
- **Params fixed** (the Phase-0 screening defaults, `--ignore-yaml` for
  ict_scalp), so each harness ran ONCE over the full series with
  `--emit-trades` and the fold report buckets trades by `entry_time` —
  equivalent to per-fold reruns for a parameterless fit, with no
  indicator warm-up artifacts at fold boundaries.
- **Fees**: 2.0 bps roundtrip base. ict_scalp has no in-harness fee
  model, so its per-trade fee is exact
  (`fee_r = bps/1e4 × entry / |entry−sl|`); fvg_range was rerun at
  4.0 bps for the headroom leg.

## Per-fold detail (net R at 2 bps)

| OOS fold | ict SPY | fvg SPY | ict QQQ | fvg QQQ |
|---|---|---|---|---|
| 2021-12 → 2022-11 | +7.06 (21t) | −7.17 (29t) | +7.65 (29t) | −0.79 (19t) |
| 2022-11 → 2023-10 | **−1.45 (8t)** | +5.81 (13t) | −3.37 (19t) | −4.32 (16t) |
| 2023-10 → 2024-08 | +0.77 (13t) | +1.13 (12t) | −2.41 (22t) | −7.49 (22t) |
| 2024-08 → 2025-07 | +1.64 (18t) | +8.41 (16t) | +8.48 (28t) | −3.31 (17t) |
| 2025-07 → 2026-06 | +3.30 (9t) | **−1.15 (13t)** | +8.67 (16t) | +3.03 (18t) |

## Reading

1. **SPY ict_scalp** is consistent with the Phase-0 screen (train
   +6.9R / OOS +4.6R net) but the fold decomposition exposes a flat-to-
   negative 2022-23 stretch (8 trades, −1.45R). The edge is real-ish
   but thin and regime-dependent; ~14 trades/year is also very low
   accrual for a 5m strategy. Fee headroom is NOT the problem (+8.6R
   at double fee).
2. **QQQ ict_scalp** has the highest total (+19R) and the worst
   consistency — two adjacent negative folds (2022-11→2024-08,
   −5.8R combined). Exactly the "train-flat, OOS-strong, one-window"
   pattern Phase-0 flagged; promotion on the total would be curve-
   chasing.
3. **fvg_range** does not transfer to either symbol at the BTC-tuned
   widths: SPY whipsaws fold-to-fold; QQQ is negative outright.
4. What could change the SPY verdict later (not done here — would be a
   tuning project, against the screening discipline): per-symbol
   re-tune of the ict_scalp HTF/threshold params, or a longer soak of
   the same params on the now-live Alpaca data feed (venue bars, not
   CFD proxies).

## Recommendation

Leave `alpaca_paper` with the three validated daily legs. Revisit the
SPY 5m slot only if the operator wants to waive the single thin-sample
negative fold (2022-23, n=8) — the evidence as specified does not
support it.
