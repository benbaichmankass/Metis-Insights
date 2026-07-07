# alpaca_live SPLG + IAUM promotion sizing (2026-07-07)

**Author:** Claude. **Status:** research finding + a **Tier-3 proposal** (real-money
`alpaca_live` promotion + risk sizing). **No config touched** — the sweep patched
`config/accounts.yaml` per iteration and **restored it** (verified: `restored
accounts.yaml` in the run output). Promotion is operator-gated.

## Question

Size a candidate `alpaca_live` promotion of two **cheap, liquid ETF proxies** —
**SPLG** (SPDR Portfolio S&P 500, an S&P proxy) and **IAUM** (iShares Gold Trust
Micro, a gold proxy). They're chosen so **one share is affordable** under a
~$150 per-trade risk budget on a small real-money account. Find the `risk_pct`
that (a) affords ≥ 1 share and (b) clears the mandatory `account_compat_matrix`
survival gate (survival ≥ 0.90 **and** P(breach) ≤ 0.10).

## Data / method

Trainer-VM relay run `actions/runs/28895603207` (issue #5911). yfinance daily
bars; `scripts/backtest_{trend,pullback}.py` for the edge, then
`scripts/prop/account_compat_matrix.py` (the repo's mandatory per-account
compat check) swept over `risk_pct × {dd/daily-loss ruleset}` with
`accounts.yaml::alpaca_live.risk` patched-and-restored each iteration.

## 1. Affordability floor (min `risk_pct` to size 1 share at ~$150 risk)

| Symbol | Last close | ATR14 | Stop dist | Min `risk_pct` for 1 share |
|---|---|---|---|---|
| SPLG | $87.67 | $1.156 | 2.5×ATR = $2.89 | **1.93%** |
| IAUM | $41.29 | $0.775 | 2.0×ATR = $1.55 | **1.03%** |

So `risk_pct` must be **≥ 1.93% (SPLG) / ≥ 1.03% (IAUM)** just to place 1 share.

## 2. Survival ceiling (`account_compat_matrix`, ROUTE = survival ≥0.90 ∧ P(breach) ≤0.10)

| Ruleset dd / daily-loss | SPLG max routable `risk_pct` | IAUM max routable `risk_pct` |
|---|---|---|
| **10%** | **3.0%** (survival 1.00, P(breach) 0.043); 4.0% → skip (0.104) | **5.0%** (0.962 / 0.092); 6.0% → skip |
| **5%** (tighter) | **2.0%** (1.00 / 0.093); 3.0% → skip (0.236) | **2.0%** (1.00 / 0.035); 3.0% → skip |

## 3. Edge quality (net-of-fee @1bp round-trip)

| Symbol | Strategy | Window | Trades | Win% | Net R | Exp R | MaxDD R | Note |
|---|---|---|---|---|---|---|---|---|
| **SPLG** | trend_donchian 1d (dc30/atr2.5/trail4.0, long-only) | 2010→2026 (16y) | 55 | 49.1% | **+33.6** | **+0.61** | 4.7 | durable; positive-dominant across years |
| **IAUM** | htf_pullback 1d (tl40/pb15/0.618/atr2.0/trail4.0) | 2021→2026 (5y) | 27 | 40.7% | **+21.1** | **+0.78** | 4.9 | **+15.4R of +21.1R is 2025 alone** — concentration risk |

SPLG is the stronger case: 16 years, 55 trades, healthy +0.61R expectancy,
positive in most calendar years. IAUM is positive with a higher expectancy but a
**short (5y) and 2025-concentrated** record (IAUM is a newer fund), so it
warrants more caution.

## Recommendation (Tier-3 — proposal only, operator-gated)

The affordability **floor** and the survival **ceiling** define the routable band:

- **Conservative (dd = 5% ruleset):** both cap at **2.0%**, which is also just
  above SPLG's 1.93% affordability floor → **`risk_pct = 2.0%` for both** is the
  clean, survivable, affordable choice. This is the recommended promotion sizing.
- **If the account runs a 10% dd ruleset:** headroom to 3.0% (SPLG) / 5.0%
  (IAUM), but I'd **hold IAUM well below its 5.0% ceiling** given the
  2025-concentration, and keep SPLG ≤ 3.0%.

**Gate before live money:**
1. **`alpaca_paper` soak first.** These are yfinance-ETF backtests, not native
   Alpaca fills; a paper soak validates real fills/slippage before `alpaca_live`.
2. IAUM's short/concentrated record means its live promotion should lag SPLG's,
   or start at the low end of the band.
3. The promotion itself (adding SPLG/IAUM to `alpaca_live`, setting `risk_pct`)
   is a **Tier-3** `config/accounts.yaml` + `config/strategies.yaml` change —
   operator-approved PR, not self-enacted.

## Provenance

Relay run `actions/runs/28895603207` (issue #5911, owner-opened). Sweep +
backtests reproduced from the run output; `account_compat_matrix.py` verdicts
are the tool's own ROUTE/skip labels. `accounts.yaml` was patched-and-restored
per iteration (no residual config change).
