# P7 — Crypto cross-sectional momentum: 6-coin first pass (2026-06-26)

**Verdict: FAILS the gate on 6 coins (indicative, not final).** Raw cross-sectional
momentum on the available 6-coin universe is essentially dead, BUT the two overlays
behave exactly as the memo predicted (BTC risk-off gate + the P1 vol-target both add
value and stack), and the most-recent holdout is positive. Whether to invest in the
**10–15 coin universe expansion** for a real verdict is a discretionary follow-up
(see Recommendation).

## What was tested
- **Tooling (this session, PR #4680):** `scripts/backtest_xsec_momentum.py` — N-asset
  daily panel (the **T0.2 loader**, leakage-safe), trailing-28d formation as of t-1,
  weekly rebalance, long-top/short-bottom tercile, dollar-neutral; overlays
  `--btc-gate` (risk-off when BTC < 50d SMA) + funding-aware; emits the `date,book_r`
  daily CSV that feeds `backtest_vol_target.py`. No look-ahead (proven by perturbation).
- **Universe:** 6 alt perps, daily, resampled on the trainer (relay #4678): ETH, SOL,
  ADA, AVAX, BNB, LINK (~1700–2070 daily bars each, 2020-10 → 2026-06). BTC kept
  separate as the gate, excluded from the tradeable universe.
- **Run:** relay #4679. P7 4-stage gate (k=5, holdout 0.2) at 1× and 2× fees, with and
  without the BTC gate, plus the vol-target stack.

## Results (full period 2020-10 → 2026-06)

| Variant | Total net | Sharpe(ann) | vol | maxDD | Gate |
|---|---|---|---|---|---|
| No overlay | +0.174 | 0.048 | 0.44 | 1.08 | FAIL — a k-fold net-negative @1×; 2×-Sharpe −0.02; holdout +0.58 |
| + BTC risk-off gate | +0.613 | 0.209 | 0.36 | 0.94 | FAIL — but PASS(2) 2×-Sharpe +0.15 & PASS(3) holdout +0.57; only FAIL(1) |
| + BTC gate + P1 vol-target | — | 0.259 | — | — | overlay stacks (+0.05 Sharpe over the BTC-gated book) |

By-year (no overlay): 2021 −0.19, 2022 +0.28, 2023 +0.69, 2024 −0.40, 2025 −0.05,
2026 −0.15 — mostly negative, 2023 carries it. BTC-gated: 2023 +0.90, 2024 −0.32,
2025 +0.12 (the gate flattened 122/296 weeks, mostly the 2021/2022 bear).

## Read
1. **Raw 6-coin xsec momentum is dead** (Sharpe ~0.05, severe >1.0-return-unit
   drawdown, mostly-negative years). This corroborates the memo's *adversarial* case
   (OOS collapse — Starkiller +69%→−2.35%; 75–94% momentum-crash drawdowns;
   cost-breakeven ~125 bps) far more than the optimistic weekly-LS-Sharpe-1.5 case.
2. **The BTC-50d-SMA risk-off gate is clearly value-additive** — Sharpe 0.05→0.21,
   total +0.17→+0.61, maxDD 1.08→0.94, and it moves the gate from 1→2 of 3 stages.
   This validates the memo's "a BTC-trend overlay cut a practitioner DD 75%→45%"
   direction.
3. **The P1 vol-target overlay stacks cleanly** on the P7 daily series (+0.05 Sharpe),
   confirming the overlay composition works end-to-end (the same `date,book_r` schema
   flows xsec → portfolio_combine → vol_target).
4. **But it still FAILS the full 4-stage gate** — FAIL(1): a k-fold is net-negative at
   1× fees in every variant. And **6 coins is below the memo's 10–15 threshold**: the
   tercile is only 2-long/2-short, too concentrated to express real cross-sectional
   dispersion, and the universe is too small to liquidity-filter or diversify the
   momentum-crash risk.

## Recommendation
**Not promotable on this evidence.** The 6-coin result is indicative only. The decision
on the universe expansion is *discretionary*, with a genuine case on both sides:
- **For expanding (10–15 liquid Bybit perps, daily fetch):** the overlay behavior is
  exactly as theorized (BTC gate + vol-target both help and stack) and the holdout is
  positive — the structure is sound, only the breadth is missing.
- **Against:** the base edge is weak even before the universe constraint, the drawdown
  signature is catastrophic, and the memo's own adversarial review rates P7 high-risk
  (OOS collapse, cost wall). Two other directional sleeves this session (P5) already
  shelved on similar "decayed/regime-specific" grounds.
- **If expanded:** the verdict gate is unchanged (every-fold net-positive @1×, survives
  2×, **out-of-pool holdout** positive, **corr-to-trend-book <0.3** — which this run
  could not test without `--trend-book`; pass the P1 BTC/ETH book as the trend-book on
  the expanded run). Logged `PB-20260626-004`.

The harness + T0.2 loader are validated and in the tree; the expansion is a data-fetch
+ re-run, not a rebuild.
