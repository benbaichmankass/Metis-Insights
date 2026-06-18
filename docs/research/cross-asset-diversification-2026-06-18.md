# Cross-asset diversification — Direction-1 robustness pass (2026-06-18)

Continuation of the strategy-expansion initiative banked in
[`regime-map-step1-results-2026-06-18.md`](regime-map-step1-results-2026-06-18.md)
(the 10-cell crypto alt book, +409.8R / Sharpe 4.03). **Direction 1:** put the
already-wired **non-crypto paper books** through the *same* gate
(`scripts/ops/portfolio_robustness.py`) and measure whether **cross-asset**
diversification lifts portfolio Sharpe / lowers drawdown versus crypto-only.

- Run on the trainer VM via `vm-driver` (`automation/results/cross-asset-robust.txt` — full raw log).
- Per-cell emits generated with the **live cell params** (not harness defaults), via
  `scripts/backtest_trend.py` / `scripts/backtest_pullback.py --emit-trades`.
- Books validated: **futures** (MES/MGC/MHG), **equity+gold** (SPY/QQQ/GLD),
  **non-crypto** (all 6), **crypto** (10, baseline re-run), **combined** (16).

## Data + instrument mapping

| Cell | Live acct | Backtest data | Span | Note |
|---|---|---|---|---|
| mes_trend_long_1d | ib_paper | `ES_F.csv` (daily) | 2000–2026 | E-mini proxy for the **micro** MES — price-identical, multiplier cancels in R-space |
| mgc_pullback_1d | ib_paper | `GC_F.csv` (daily) | 2000–2026 | gold-future proxy for micro MGC |
| mhg_pullback_1d | ib_paper | `HG_F.csv` (daily) | 2000–2026 | copper-future proxy for micro MHG |
| spy_trend_long_1d | alpaca_paper | `SPY_1d.csv` | 2017–2026 | the **actual** traded ETF |
| qqq_trend_long_1d | alpaca_paper | `QQQ_1d.csv` | 2017–2026 | actual ETF |
| gld_pullback_1d | alpaca_paper | `GLD_1d.csv` | 2017–2026 | actual ETF |

**Not validated here — `mgc_trend_1h`:** no clean ≤1h gold history on the trainer
(`GC_F.csv` is daily). Deferred (only `XAUUSD_15m` spot exists as a possible proxy).
`oanda_practice` (XAUUSD) is shelved (`mode: dry_run`, empty routing — OANDA US can't
trade XAU_USD), so it is not a live book to validate.

## Per-cell standalone (full history, 7.5 bps)

| Cell | n | win% | net_r | exp/trade |
|---|---|---|---|---|
| mes_trend_long_1d | 63 | 44% | +30.4 | +0.48 |
| mgc_pullback_1d | 189 | 34% | +56.4 | +0.30 |
| mhg_pullback_1d | 145 | 35% | +85.1 | +0.59 |
| spy_trend_long_1d | 26 | 58% | +23.6 | +0.91 |
| qqq_trend_long_1d | 27 | 52% | +26.5 | +0.98 |
| gld_pullback_1d | 52 | 38% | +24.4 | +0.47 |

All six are standalone net-positive. Daily cadence → **low trade counts** (the
equity-trend cells are ~3 trades/yr — thin; the pullback cells are healthier).

## Book-level robustness

| Book | cells | trades | span | net_r | Sharpe | maxDD | boot P(+) | boot p5 | holdouts |
|---|---|---|---|---|---|---|---|---|---|
| Futures (MES+MGC+MHG) | 3 | 397 | 2000–2026 | +172.0 | 3.67 | 12.0R | 1.000 | +92.9 | 5/5 + |
| Equity+gold (SPY+QQQ+GLD) | 3 | 105 | 2017–2026 | +74.4 | 3.35 | 8.7R | 0.999 | +31.5 | 5/5 + |
| Non-crypto (6) | 6 | 502 | 2000–2026 | +246.3 | 4.75 | 19.5R | 1.000 | +144.2 | 5/5 + |
| Crypto (10, baseline) | 10 | 3147 | 2021–2026 | +409.8 | 4.03 | 96.2R | 0.984 | +98.1 | 5/5 + |
| **Combined (16)** | 16 | 3649 | 2000–2026 | **+656.1** | **5.74** | 89.8R | 0.999 | **+319.8** | 5/5 + |

Every book: all 5 holdout cutoffs positive, leave-one-cell-out all positive, bootstrap
P(net>0) ≈ 1.0. The `portfolio_robustness` composite reads **NOT fully robust** for all
of them — driven **only** by `all_years_positive=False`, i.e. thin small-sample early
years (futures 2000 n=5, 2002/2004/2007/2012/2019 mildly negative on n≈11–19) and crypto's
flat 2026-YTD. Same blemish class as the banked crypto book — not a losing-book signal.

## The diversification result (the honest comparison)

The **full-history combined Sharpe (5.74 > crypto 4.03) is span-confounded** — the
non-crypto book has 21 pre-crypto years where it trades alone at high Sharpe. The clean,
apples-to-apples evidence is the **concurrent holdout cutoffs** (2023+, where crypto AND
non-crypto both trade):

| Concurrent holdout | Crypto-only | Combined | Δ Sharpe | Δ maxDD |
|---|---|---|---|---|
| ≥ 2023-07-01 | +341.7R, Sharpe 4.00, DD 96.2R | +431.7R, Sharpe **4.89**, DD **89.8R** | +0.89 | −6.4R |
| ≥ 2024-01-01 | +196.0R, Sharpe 2.72, DD 96.2R | +270.3R, Sharpe **3.62**, DD **89.8R** | +0.90 | −6.4R |
| ≥ 2025-01-01 | +140.0R, Sharpe 2.33, DD 96.2R | +198.3R, Sharpe **3.17**, DD **89.8R** | +0.84 | −6.4R |

**At every concurrent cutoff the combined book beats crypto-only on BOTH Sharpe and
drawdown** — more return, higher risk-adjusted return, lower peak drawdown. In the
2021–2026 overlap the non-crypto book adds **+86.8R** on top of crypto's +409.8R (+21%)
with low correlation. **Cross-asset diversification works** — exactly the thesis, and a
stronger lift than adding more (correlated) alts would give.

## Caveats (carry into any real-money step)

1. **Fee model is bps, futures/equities are per-contract commission + spread.** Emits
   used the 7.5 bps harness default. The R-multiple framing abstracts notional, and the
   added-cost headroom is large (futures book absorbs **+0.43R/trade** extra cost before
   flat; equity +0.71R/trade) — but a **per-contract** cost validation via
   `scripts/prop/account_compat_matrix.py` is mandatory before any real-money promotion.
2. **Internal equity correlation.** MES, SPY, QQQ are all long US-equity beta — the "6-cell
   non-crypto book" is really three clusters: equity-index (MES/SPY/QQQ), metals
   (MGC/MHG/GLD), crypto. The genuine diversification axes are those three, not six
   independent cells.
3. **Thin equity samples.** SPY/QQQ ≈ 3 trades/yr. Book-level bootstrap P(+)=0.999 is
   reassuring, but per-cell these are low-n; the pullback cells (mgc/mhg/gld) are healthier.
4. **Proxy instruments** for the micros (ES_F→MES etc.) are price-identical, so R-space is
   valid; SPY/QQQ/GLD are the actual traded ETFs.

## Where this lands

- **Bank:** both non-crypto paper books (futures + equity/gold) are robust on the same
  gate the crypto book passed. They are **already wired enabled+live on paper**
  (`ib_paper`, `alpaca_paper`) — this validation confirms the existing paper book is sound;
  **no new wiring is needed** for Direction 1's paper expansion.
- **Portfolio thesis confirmed:** the combined cross-asset book is the realistic
  overall-P&L-positive portfolio — higher concurrent Sharpe + lower drawdown than
  crypto-only. Track it live alongside the crypto book.
- **Follow-ups:** (a) `mgc_trend_1h` needs ≤1h gold data to validate; (b) real-money
  promotion of any non-crypto cell stays **Tier-3** — operator-approved +
  `account_compat_matrix` (per-contract cost/survival) gated; (c) Direction 2
  (recombination sweep) is the next pass.
