# Tradeable Universe — WS-A0 (2026-06-02)

> Meantime Expansion Program, WS-A0. Scopes the symbol sweep to what we
> can actually trade on the *eventual* venues, per operator directive
> (2026-06-02): futures diversification first; the live futures account
> will likely be **NinjaTrader**, not IBKR.

## Venue catalogs

| Venue | Role | Tradeable | NOT tradeable |
|---|---|---|---|
| **Bybit V5** | live crypto (have) | BTC + liquid alt **linear perps** (ETH, SOL, BNB, XRP, DOGE, ADA, LINK, AVAX, …) + spot | futures-exchange products |
| **NinjaTrader** | *likely* eventual live futures venue | **CME Group (CME/CBOT/NYMEX/COMEX/MGEX) + ICE US + Eurex futures & FX futures**, incl. micro/nano sizes and **CME Micro BTC (MBT) / Micro Ether (MET)** | equities, spot crypto, non-CME/ICE/Eurex products |
| **IBKR** | current paper venue + **WS-A data source** | broad (futures, equities, FX, options) | — (used only to pull futures history for backtests) |

**Key constraint:** validate a futures symbol only if NinjaTrader can
trade it. IBKR is the data pipe; NinjaTrader is the eventual venue.
Their futures sets overlap heavily (CME micros), so this is low-friction.

## Proposed sweep universe (NinjaTrader-tradeable, history via IBKR)

Prioritized for **BTC-uncorrelation** (the diversification goal). Micro
contracts preferred (small size → fits the account).

| Class | Symbols (micro / standard) | Why |
|---|---|---|
| **Equity index** | MES/ES *(have)*, MNQ/NQ, MYM/YM, M2K/RTY | SPX trend already validated net-positive, corr≈0.009 to BTC |
| **Metals** | MGC/GC (gold), SIL/SI (silver), MHG/HG (copper) | Macro-uncorrelated; gold is a classic diversifier |
| **Energy** | MCL/CL (crude), QG/NG (nat gas) | Own regime; trend/vol behavior distinct from crypto |
| **Rates** | ZN (10y note), ZB (bond) | Different driver (macro rates) |
| **Grains (CBOT)** | ZC (corn), ZS (soybeans), ZW (wheat) | Seasonally-driven, uncorrelated |
| **FX futures** | 6E/M6E (euro), 6J (yen) | Macro/FX regime |
| **Crypto futures** | MBT (micro BTC), MET (micro ETH) | Regulated-futures crypto path (corr-aware vs Bybit book) |

**Bybit alts (frequency play, same keys, sweep second):** ETHUSDT,
SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, LINKUSDT, AVAXUSDT.

## Next (WS-A S1)

Run `scripts/backtest_{trend,fade,squeeze,ict_scalp,fvg_range,pullback}.py`
across the futures universe on the trainer VM — net-of-fee, long/short
split, walk-forward — re-tuning per symbol (crypto params don't transfer).
Output: the symbol×strategy generalization matrix, tagged by venue.

> **To verify before live:** the exact NinjaTrader contract specs +
> commissions per symbol (and the broker/data feed: Rithmic / Continuum /
> Tradovate) at the point we wire the futures account. This artifact is
> the research-scope cut, not the execution-integration spec.

## Sources

- [NinjaTrader — Futures Trading](https://ninjatrader.com/)
- [NinjaTrader review — supported markets (StockBrokers.com)](https://www.stockbrokers.com/futures/review/ninjatrader)
- [CME Group — Micro Bitcoin futures on NinjaTrader](https://www.cmegroup.com/trading/micro-bitcoin-futures/ninja-trader.html)
- [NinjaTrader — List of Available Instruments to Trade](https://vendor-support.ninjatrader.com/s/article/List-of-Available-Instruments-to-Trade?language=en_US)
