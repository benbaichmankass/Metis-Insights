# Strategy Edge Research — open-source synthesis (S-STRAT-IMPROVE-S6, 2026-05-23)

> **Tier-1 research.** Grounds the North-Star new-strategy R&D and the
> variation-testing methodology in published/community evidence. Pairs
> with the empirical findings in `strategy-inherent-edge-2026-05-23.md`
> (ict_scalp = durable edge; turtle_soup/vwap = not). Operator emphasis
> 2026-05-23: deep open-source research + test *many variations* per
> strategy (not one config over years), and quantify whether each
> variation seriously moves the edge.

## 1. What works / what fails (the big picture)

- **Momentum and mean-reversion both exist intraday in crypto, but
  regime-dependent.** Momentum/trend worked pre-2021, decayed as
  structure matured; it gets whipsawed by false breakouts in low-volume
  regimes. Mean-reversion (especially BTC-neutral *residual /
  idiosyncratic* MR) excelled post-2021 and in consolidation. Neither
  dominates across all regimes.
- **Complementarity is the proven win.** A 50/50 blend of momentum +
  mean-reversion reported **Sharpe ~1.71 / ~56% ann.** — far smoother
  than either alone. This is direct external support for the operator's
  3–5 complementary-strategy North Star: edges that fire in *different*
  regimes smooth the equity curve. Build the roster around
  *non-correlated* edges, not 5 variants of one idea.
- **Overtrading is the dominant retail/algo killer** — emotional churn +
  fees. Matches our live finding exactly (vwap fees = 418% of gross).
  Fee-discipline (selectivity + fee-efficient stops) is a first-class
  design constraint, not an afterthought.

## 2. ICT / Smart-Money Concepts — evidence (validates ict_scalp)

- A 2,600-trade backtest (Jan 2024→Mar 2026, 10 assets incl. BTC/ETH)
  found SMC concepts work *when traded with institutional flow*.
- **Fair Value Gaps fill ~70% of the time** — a genuinely reliable
  pattern. **Liquidity sweeps** (stop-hunts of obvious highs/lows before
  the real move) are real and tradeable.
- **Caveat — crowding:** as more traders mark the same OBs/FVGs/sweeps,
  naked single-signal setups decay. The edge survives via **confluence +
  multi-timeframe + patience**, not lone signals.
- **Implication:** our `ict_scalp` (sweep + displacement + FVG
  wick-rejection + HTF bias = a *confluence* stack) is exactly the
  resilient form — consistent with its **durable gross edge across
  2023/24/25** in our tests. Lean into confluence/MTF; avoid
  single-signal naked entries.

## 3. Candidate edges for NEW complementary strategies

Ranked by (edge evidence × data we already have × architecture fit).
Each would be a `StrategyInterface` producing `SignalPackage`s into the
existing intent-multiplexer/decider.

| Candidate | Edge basis (evidence) | Data we have? | Complements |
|---|---|---|---|
| **Session / time-of-day volatility gate** | Liquidity+vol cluster at specific hours; spreads narrow in active hours; our S2 audit showed strong hour-of-day PnL skew | ✅ (timestamps) | All — a *filter/overlay* more than a standalone |
| **Trend/momentum (HTF-aligned breakout)** | Momentum real in trending regimes; complements MR | ✅ (OHLCV) | MR strategies (different regime) |
| **Residual / idiosyncratic mean-reversion** | Post-2021 BTC-neutral residual MR outperformed | ✅ (OHLCV; cross-asset later) | Momentum/trend |
| **Funding-rate / basis timing (perps)** | Funding anchors perp→spot; timing around settlement enhances entries | ⚠️ (Bybit funding fetch needed) | Price-only strategies |
| **Order-flow / VPIN toxicity** | Order flow predicts 1-day returns; VPIN predicts jumps | ❌ (need trade/orderbook flow data) | Everything; harder data |
| **Volatility-regime switch (ATR/vol bucket)** | Strategy efficacy is regime-dependent; our regime models already bucket vol | ✅ (+ existing regime models) | Acts as the decider's regime input |

**Near-term picks** (data-available, architecture-fit, complementary to
ict_scalp's sweep-reversal): (a) an **HTF-aligned momentum/breakout**
strategy and (b) a **session/volatility overlay** usable by all
strategies + the decider. Funding-rate and order-flow are higher-effort
(new data) — stage later.

## 4. Validation methodology (operator emphasis: test MANY variations)

The literature is blunt: **in-sample Sharpe has almost no predictive
power for out-of-sample** (R² < 0.025 across 888 Quantopian strategies).
So our process must be variation-first and overfit-hardened:

- **Test variations, not a config.** For each strategy, sweep entry ×
  exit × stop × filter variants *together*, every run — not one config
  over years. The question is "does this lever *seriously* move the
  edge?", which only a grid answers.
- **Plateau, not cliff.** A robust parameter sits in a *plateau* where
  ±10–20% neighbors perform similarly. A lone spiky optimum (cliff) is
  overfit — reject it. Rank by plateau quality, not peak value.
- **Out-of-sample / walk-forward.** Optimize on one window, validate on
  the next; the strategy must re-prove itself across regimes (our
  2023/24/25 split is a coarse version — formalize it).
- **Deflated Sharpe + PBO.** Adjust for multiple testing (we WILL try
  many variations). Deflated Sharpe Ratio (Bailey/López de Prado) and
  Probability of Backtest Overfitting (CSCV) quantify "is this real or
  did we just try enough combos to get lucky?" **CPCV** (Combinatorial
  Purged CV) is the current best anti-overfit protocol.
- **Net-of-fee always.** Already wired into our harnesses (S4). Gross
  edge is the signal; net is the fee-tax that decides viability.
- **Both legs / regimes.** Report long/short and per-year/per-regime so a
  single-regime artifact (turtle_soup 2025) can't masquerade as edge.

## 5. How this maps to our build

1. **Variation-sweep harness** (next build): extend the backtests to run
   an entry × exit × stop × filter grid in one pass, net-of-fee, across
   year-slices, emitting a comparison table + a robustness/plateau read
   (and ideally a Deflated-Sharpe / PBO column). Also speed up the
   per-bar engine so grids × 3yr are feasible on the 1-core trainer.
2. **ict_scalp variation sweep** — quantify which entry/exit/stop levers
   move its durable gross edge into a solid net edge (look for plateaus).
3. **New strategies** — implement (a) HTF-momentum/breakout and (b) a
   session/vol overlay; backtest them with the same variation grid;
   keep only durable, complementary, fee-survivable edges.
4. **Decider** — feed regime/vol bucket + per-strategy confidence/edge
   into the intent multiplexer so it picks the best trade by regime fit.

## Sources

- [Intraday Return Predictability in Crypto: Momentum, Reversal, or Both (SSRN)](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID4135239_code2537556.pdf?abstractid=4080253&mirid=1)
- [Systematic Crypto Strategies: Momentum, Mean Reversion & Vol Filtering (Medium)](https://medium.com/@briplotnik/systematic-crypto-trading-strategies-momentum-mean-reversion-volatility-filtering-8d7da06d60ed)
- [Bitcoin Mean Reversion vs Momentum in Low-Volume Regimes (QuantifiedStrategies)](https://www.quantifiedstrategies.com/bitcoin-mean-reversion-strategies-outperform-momentum-in-low-volume-regimes/)
- [I Backtested 2,600 Trades Using Smart Money Concepts (Medium)](https://medium.com/@space.garaa/i-backtested-2-600-trades-using-smart-money-concepts-heres-what-actually-works-bb3c671098c6)
- [Order flow and cryptocurrency returns (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S1386418126000029)
- [Bitcoin wild moves: order flow toxicity (VPIN) and price jumps (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0275531925004192)
- [Best Time to Trade Crypto Futures — session effects (Mudrex)](https://mudrex.com/learn/best-time-to-trade-crypto-futures/)
- [Rigorous Walk-Forward Validation for Microstructure Signals (arXiv)](https://arxiv.org/html/2512.12924v1)
- [Backtest overfitting: OOS testing methods (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110)
- [Robustness Testing for Algo Strategies (BuildAlpha)](https://www.buildalpha.com/robustness-testing-guide/)
