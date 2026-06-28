# P1 — Book-level volatility-targeting overlay: result (2026-06-26)

**Verdict: KEEP (validated).** A constant-vol-budget overlay on the book robustly
improves risk-adjusted return — Sharpe +0.10 to +0.20 net of turnover, across every
target level and re-target cadence tested. Recommended to graduate to a **live shadow
overlay** (Tier-3, operator-gated). This is the opposite outcome from P5 (shelved).

## What was tested
- **Tooling (this session, PR #4672, merged):** `scripts/portfolio_combine.py`
  (per-strategy `--emit-trades` JSONL → one daily book-return series, 1 unit risk/trade)
  + `scripts/backtest_vol_target.py` (scale book gross to a constant annualized
  realized-vol budget, 20d/60d-blended vol estimate, cap 0.5×–1.5×, re-target
  daily/weekly/monthly, net of |Δmultiplier| turnover cost, **no look-ahead** — the
  day-t multiplier uses returns through t-1 only, proven by perturbation tests).
- **Book:** 9 live-roster sleeves, **2,818 trades, 2021-06-21 → 2026-06-25 (~5 yr)**,
  emitted on the trainer (relay #4674) from the per-strategy harnesses on real candles:
  trend (BTC/ETH/MES 2h), fade (BTC 4h), squeeze (BTC 4h), pullback (BTC/ETH/MES 2h),
  fvg_range (BTC 15m). BTC/ETH on a deep ~5-yr 1h history; MES cells thin (28/23 trades).
- Combine + overlay run on the trainer (relay #4675).

## Results

| Config (weekly unless noted) | Sharpe(ann) | Δ Sharpe | Ann vol (R) | MaxDD (R) | Total R |
|---|---|---|---|---|---|
| Baseline (no overlay) | 0.271 | — | 39.0 | 75.1 | +60.1 |
| Vol-target 0.8× base | 0.439 | +0.167 | 37.1 | 68.6 | +92.3 |
| Vol-target 1.0× base | 0.448 | +0.177 | 45.9 | 85.7 | +116.9 |
| Vol-target 1.2× base | 0.412 | +0.141 | 52.2 | 100.1 | +122.3 |
| 1.0× base, monthly | 0.467 | +0.196 | — | 78.1 | — |
| 1.0× base, daily | 0.370 | +0.098 | — | 81.4 | — |
| BTC-only sub-book, 1.0× wk | 0.185 → 0.289 | +0.104 | — | — | — |

(`--target-vol` is in the book's own R units; set to the book's baseline annualized vol
× the listed factor so the multiplier modulates around 1.0 instead of pinning at a cap.)

## Read
1. **Genuine risk-timing, not leverage.** At **0.8× base**, realized vol stays ≈ baseline
   (37.1 vs 39.0 R) yet Sharpe rises 0.27→0.44 **and** max-drawdown falls 75→69 R. Same
   (slightly lower) risk, better return and lower drawdown ⇒ the overlay is redistributing
   exposure across time (up in calm/trending stretches, down in turbulence), not just
   levering up. The 1.0×/1.2× configs add Sharpe too but by raising average exposure
   (mean multiplier 1.12 / 1.29) → more vol + deeper drawdown; that's a different risk
   posture, not a free lunch.
2. **Robust + cheap.** ΔSharpe is +0.10…+0.20 across all targets and cadences; turnover
   cost is negligible (~0.003–0.006 R total). **Monthly re-targeting is best** (Sharpe
   +0.196, least churn) — vol is persistent enough that monthly captures most of the
   benefit; daily over-trades the multiplier (+0.098, most turnover). This validates the
   20/60 blend's turnover-damping intent.
3. **Benefit is broad-based.** BTC-only sub-book also improves (0.185→0.289); the full
   BTC/ETH/MES book has a higher baseline (0.271) and a larger absolute lift.

## Caveats
- **The underlying book is mediocre** (Sharpe 0.27 over 5 yr; 2021/2022/2026 negative
  years; `fvg_range_BTCUSDT_15m` bleeds −41 R and is the single worst sleeve). Vol-targeting
  is an **overlay** — it improves the risk-adjusted profile of *whatever* book it wraps; it
  does not fix a weak book or manufacture return. Its value is real but bounded.
- **MES cells are thin** (28/23 trades; MES 5m v002 is the short dataset). The result is
  BTC/ETH-dominated — consistent with the memo's expectation that vol-target benefit
  concentrates on the liquid trend-bearing symbols.
- Per-trade fees are already in each sleeve's `net_r`; the overlay adds only the
  re-balance turnover cost (modeled at 2 bps on |Δmultiplier|).

## Recommendation
**Graduate to a live SHADOW overlay** (Tier-3 — operator-gated; sizing change). Proposed
shape: a book-level multiplier on per-account qty, **0.8× base-vol target, monthly
re-target, capped 0.5×–1.5×, 20/60 blended vol estimate** — logged shadow-only first
(would-be multiplier vs actual) to soak before it influences live sizing, mirroring the
advisory/conviction soak pattern. Separately, the `fvg_range_BTCUSDT_15m` −41 R drag is a
sleeve-level finding worth its own review (not part of this overlay result).
