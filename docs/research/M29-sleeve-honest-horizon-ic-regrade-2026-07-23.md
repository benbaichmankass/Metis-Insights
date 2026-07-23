# M29 — Honest (non-overlapping) horizon-IC re-grade of the three valuation-schema sleeves

**Date:** 2026-07-23 · **Tier-1 research (observe-only)** · scan: `scripts/macro/horizon_ic_scan.py`

## Why this re-grade

The three sleeves that emit the valuation-snapshot schema — the **value** deep-dive,
the **CFTC-COT** positioning sleeve, and the **crypto** funding/OI/basis sleeve — were
each first graded with a horizon-IC scan whose forward windows **overlapped** (horizon
longer than the rebalance spacing). Overlapping windows share observations, so the IC
t-statistic is optimistically inflated: the COT sleeve's headline **90d IC ≈ 0.052 at
t ≈ 3.2** looked significant, but the 7-day rebalance meant ~13 overlapping windows per
independent one.

PR #7500 added two corrections to the scan, and PR #7501 baked them into the three
backfill workflows:

- **`--non-overlapping`** — each horizon uses its own rebalance spacing (≥ the horizon),
  so forward windows don't overlap and the t-stat is honest.
- **`conviction_spread`** — the market-neutral, monetizable reading of a positive IC:
  mean net return of the highest-conviction populated bin minus the lowest. It cancels
  the all-long bull-market drift that unfairly sinks the raw `edge_vs_baseline`, so a
  real conviction→return relationship shows up as a positive long-short spread even in a
  rising market.

This note records the honest re-grade of all three sleeves (value + COT on GitHub
runners; crypto on the trainer VM because Bybit US-geo-blocks GitHub runners).

## Results (all non-overlapping; `t_flag = 2.0`)

### Value sleeve — `no_monetizable_horizon`

| H (days) | n | IC | IC_t | conv_spread |
|---|---|---|---|---|
| 7 | 1104 | +0.032 | 1.06 | −0.0014 |
| 14 | 1104 | +0.015 | 0.50 | −0.0010 |
| 30 | 1104 | −0.004 | −0.12 | −0.0038 |
| 60 | 553 | −0.019 | −0.45 | +0.0010 |
| 90 | 360 | +0.006 | 0.12 | +0.0172 |
| 180 | 182 | −0.030 | −0.41 | +0.0215 |

No horizon clears |t| ≥ 2; the strongest IC (0.032 @ 7d) is t = 1.06. No monetizable edge.

### CFTC-COT positioning sleeve — `no_monetizable_horizon`

| H (days) | n | IC | IC_t | conv_spread |
|---|---|---|---|---|
| 7 | 3746 | +0.004 | 0.24 | +0.0020 |
| 14 | 1869 | +0.002 | 0.09 | +0.0018 |
| 30 | 894 | +0.011 | 0.32 | +0.0045 |
| 60 | 447 | +0.029 | 0.61 | +0.0135 |
| **90** | 290 | +0.068 | **1.16** | **−0.0223** |
| 180 | 143 | +0.012 | 0.15 | −0.0358 |

**The headline 90d signal was overlap inflation.** Overlapping, the 90d IC read t ≈ 3.2;
honest (windows spaced at ~90d → 156 independent windows), the same horizon is
**IC 0.068 at t = 1.16** — not significant — and its conviction spread is *negative*
(−0.022). No monetizable edge survives.

### Crypto funding/OI/basis sleeve — `monetizable_horizon_found` (nominal only)

| H (days) | n | IC | IC_t | conv_spread |
|---|---|---|---|---|
| **1** | 895 | +0.070 | **2.10** | **+0.0002** |
| 3 | 895 | +0.020 | 0.61 | −0.0192 |
| 7 | 384 | +0.047 | 0.93 | +0.0336 |
| 14 | 193 | +0.094 | 1.31 | +0.0400 |
| 30 | 90 | +0.026 | 0.25 | −0.0398 |

The 1d horizon (rebalance 3 ≥ 1, so always non-overlapping — this row was already honest)
is the only flagged-significant one: **IC 0.070, t = 2.10**, verdict
`monetizable_horizon_found`. But its conviction spread is **negligible** — +0.0002
(≈ +2 bps/day gross, net-negative after fees), and win rate 0.494 < 0.5. The
economically meaningful spreads (7d +3.4%, 14d +4.0%) are **not** statistically
significant (t = 0.93, 1.31).

## Conclusion

Under honest (non-overlapping) grading with the conviction-spread metric, **none of the
three sleeves shows a clean, statistically-significant AND economically-meaningful
monetizable edge on its current signal construction:**

- **Value** and **COT** are flat — no flagged horizon; the COT "90d edge" was purely
  overlap inflation.
- **Crypto** has a real-but-tiny 1d statistical signal whose conviction spread is too
  small to trade net of fees; its larger spreads (7–14d) are not significant.

**Recommendation:** do **not** graduate any of the three to an order-affecting sleeve on
the current signal construction. They remain observe-only. Next levers worth a look
(each would need its own honest re-grade before anything ships): a longer crypto history
to power up the 7–14d spread test; conditioning the crypto signal (funding extreme × OI
direction) rather than raw percentile; and a COT construction that is not a raw
large-spec percentile (e.g. a change/impulse form). All Tier-1 research.

## Artifacts

- `comms/macro/horizon_ic_scorecard.json` (value, honest) — landed via the value backfill workflow.
- `comms/macro/cot_horizon_ic_scorecard.json` (COT, honest) — landed via the COT backfill workflow.
- `comms/macro/crypto_horizon_ic_scorecard.json` (crypto, honest) — produced on the trainer VM (Bybit reachable) and committed here directly, since a VM run can't push to protected `main`.
