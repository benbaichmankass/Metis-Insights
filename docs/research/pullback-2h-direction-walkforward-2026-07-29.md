# Pullback-2h directional walk-forward — long-drag REFUTED out-of-sample (2026-07-29)

**Tracking:** closes `PB-20260729-CRYPTO-PULLBACK-2H-LONG-DRAG`
(performance-review-backlog).
**Predecessor:** `docs/research/regime-debt-matrix-results-2026-07-29.md` §3 — the
rec #5 matrix surfaced a consistent long-negative / short-positive asymmetry in
the pullback-2h family over a **single ~300-day in-sample window** and flagged it
for walk-forward before any Tier-3 action. This is that walk-forward.
**Tier:** analysis only — **no live-config change; no Tier-3 proposal** (the
hypothesis did not survive OOS).

## TL;DR — the 300d asymmetry was regime-of-sample

The pullback-2h family (`eth_pullback_2h`, `sol_pullback_2h`, `xrp_pullback_2h`)
**does not have a durable directional edge** to gate on. Over the **full 2 years**
(2024-07-29 → 2026-07-28, exact live params, net-of-fee) the "long side is a
drag" pattern **inverts**:

| Window | pooled long_r | pooled short_r | long_folds_neg | verdict |
|---|--:|--:|:--:|---|
| 300d (2025-10→2026-07) | **−27.6** (n57) | +40.4 (n60) | **4/4** | stable_drag=True (in-sample) |
| **2yr (2024-07→2026-07)** | **+5.77** (n137) | +39.1 (n132) | **5/8** | **stable_drag=False** |

The long side is **net positive** over two years and negative in only a bare
5/8 folds — not the durable drag the short window suggested. **No Tier-3
short-only / long-gate change is warranted.**

## Method

- Data: Binance-vision UM 2h candles, 8,760 bars/symbol, 2024-07-29 → 2026-07-28
  (`scripts/ops/fetch_backtest_candles.py --interval 120 --days 730`).
- Backtest: `scripts/backtest_pullback.py` with **exact live params** from
  `config/strategies.yaml` (trend-lookback 40, pullback-lookback 10, frac 0.5,
  atr 14 / stop 2.5 / trail 5.0, min-conf 0.0; adx-min 25, SOL 30), fee 7.5bps
  roundtrip, `--emit-trades`.
- Walk-forward: `scripts/research/direction_walkforward.py` — pools the three
  symbols' emitted trades, partitions into **8 contiguous equal-count time-folds**
  by entry time, reports per-fold per-direction net-R. Because these strategies
  are **rule-based with fixed live params (no in-sample fitting)**, a contiguous
  time-fold split *is* the out-of-sample test — each fold is genuinely OOS
  relative to the others; there is no leakage to guard.

## Per-symbol, 2 years

| Strategy | trades | net-R | long-R (n) | short-R (n) |
|---|--:|--:|--:|--:|
| `eth_pullback_2h` | 96 | +18.43 | **+8.86** (46) | +9.58 (50) |
| `sol_pullback_2h` | 74 | −5.43 | −12.39 (40) | +6.96 (34) |
| `xrp_pullback_2h` | 99 | +31.82 | **+9.30** (51) | +22.52 (48) |

Two of the three symbols have **net-positive longs** over 2yr. Only SOL keeps a
negative long side — a single-symbol effect, not a family property.

## Pooled walk-forward — 8 folds

| Fold | window | long-R (n) | short-R (n) | net-R |
|--:|---|--:|--:|--:|
| 1 | 2024-08 → 11 | −0.10 (14) | +6.24 (20) | +6.14 |
| 2 | 2024-11 → 2025-02 | **+8.48** (18) | −3.05 (16) | +5.43 |
| 3 | 2025-02 → 05 | −1.79 (17) | −5.88 (16) | −7.66 |
| 4 | 2025-06 → 08 | **+19.99** (24) | −2.73 (10) | +17.26 |
| 5 | 2025-08 → 11 | +2.60 (12) | +27.38 (22) | +29.97 |
| 6 | 2025-11 → 2026-02 | −6.94 (16) | +14.51 (17) | +7.57 |
| 7 | 2026-02 → 05 | −6.33 (19) | −6.38 (15) | −12.71 |
| 8 | 2026-05 → 07 | −10.14 (17) | +8.96 (16) | −1.18 |
| **pooled** | 2yr | **+5.77** (137) | **+39.05** (132) | +44.82 |

**Read:**

- **The long side is not a stable drag.** Negative in 5/8 folds, but two folds
  (#2 +8.5, #4 +20.0) are strongly positive and the pooled sum is **+5.77**.
  `stable_drag=False`.
- **The 300d window = the recent bearish-long regime.** Folds 6–8 (2025-11 →
  2026-07) reproduce the −27.6R long-drag; the earlier year (folds 1–5) does not.
  The rec #5 matrix simply sampled that recent stretch.
- **Even the short "edge" isn't gate-worthy.** Short-R is the larger contributor
  (pooled +39.1) but is positive in only 4/8 folds (negative in folds 2,3,4,7).
  `stable_edge=False`.
- The family's genuine net edge (+44.8R pooled over 2yr) comes from **both sides
  in aggregate**, regime-dependently — not from a durable directional tilt a gate
  could harvest.

## Disposition

- **Close `PB-20260729-CRYPTO-PULLBACK-2H-LONG-DRAG` as refuted** (regime-of-sample).
  No short-only variant, no long-side gate. The three pullback-2h strategies stay
  exactly as they are.
- This **strengthens the rec #5 conclusion**: the crypto-plain regime-coverage
  debt is a classification/bookkeeping matter, not a hidden-edge one — neither a
  regime cell (all entries already `adx_min`-gated to trending) nor a direction
  gate is justified for this family.
- Reusable tool added: `scripts/research/direction_walkforward.py` (composes with
  `regime_tag_emitted.py` — the next directional/temporal-stability question runs
  in one command).

**Why this mattered:** the single-window signal was strong enough (long −27.6R /
57 trades, negative in every quarter) that acting on it looked reasonable. The
2-year walk-forward is the difference between a curve-fit Tier-3 change and a
correctly-declined one.
