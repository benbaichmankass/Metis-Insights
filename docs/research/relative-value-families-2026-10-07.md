# Relative-value / arbitrage strategy families — feasibility on OUR feeds, order paths and venue rules

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Written 2026-10-07 by lane `ARB-RESEARCH` (session `session_01Aeyqvqf9sajCVDx9Ufef4u`,
dispatched by Manager Session 2026-10-01). Pipeline item `PI-20261006-APBY4NTV-0005`.
Operator, 2026-10-06 ~08:20Z, verbatim: *"other complex strategies (not just based on
predicting movement, but arbitraging symbols, spreads, etc)"*.

**One question:** which relative-value families are feasible for us, and what is each one's
first registered question? Everything below was checked this session against the code, the
configs and the committed results named in each row. Nothing is quoted from memory; where a
fact could not be checked it is marked **not checked**.

## 0. The verdict table

| family | data on OUR feeds | order path | venue rules | post-cost edge, expected | **verdict** | first registered question |
|---|---|---|---|---|---|---|
| Statistical pairs / cointegrated pairs **beyond M22** (ratio and z-score spread mean reversion is the SAME instrument — `scripts/backtest_pairs.py`) | YES. Crypto perps 1h–4h from Binance Vision (the Bybit proxy; Bybit REST is 403 from runners and from this sandbox), ETFs 1d from yfinance, both through `scripts/ops/fetch_backtest_corpus.py` `CANDIDATE_PAIRS`/roster table | Crypto: YES — the M22 isolated 2-leg path (`src/units/strategies/pairs_executor.py`, both-legs-or-nothing + leg-imbalance unwind, Bybit, ONE account per `config/pairs.yaml::account_id`). ETFs: **NO** — the pairs executor is Bybit-only; an Alpaca two-leg path does not exist | Props: Velotrade allows hedging within one account; Tradeify bans only same-instrument long+short; Breakout allows hedge mode within one account — a two-instrument pair is allowed on all three, but each venue lists only ETHUSD/SOLUSD(/XRPUSD) and charges swap on BOTH legs daily (Tradeify 0.033%/day, Velotrade 0.05%/night) plus commission per side, so only intraday holds can survive | Crypto taker stack is the known wall: the four M22 pairs were fee-positive in R and NET-NEGATIVE in dollars at taker 7.5 bps × 2 legs (G2, 2026-07-16). New pairs must clear fee 7.5 + slippage 3 + funding ~1 bps/8h per leg. ETF pairs: zero commission on Alpaca, slippage 5 bps rt per leg, **borrow on the short leg is unmodelled anywhere in the repo** | **feasible-now (Stage 0)**; Stage 1 on Bybit is a `config/pairs.yaml` edit (Tier-3); Stage 1 on Alpaca **needs-an-order-path** | `RQ-20261007-001` SLV/GLD 1d · `RQ-20261007-002` TLT/IEF 1d · `RQ-20261007-003` XRPUSDT/ADAUSDT 1h |
| Cross-sectional / dollar-neutral **baskets** | YES for the fixed ETF basket (SPY, QQQ, GLD, TLT, IWM 1d) wired in `research-harness-dispatch.yml`; crypto perps have data but the harness has **no funding term** (D1 list in CLAUDE.md) | **NO** — nothing places a weighted N-leg book; the pairs executor is 2-leg | n/a for Stage 0 | P7 (2026-06-26) on 6 coins: raw Sharpe ~0.05, FAIL; BTC-risk-off gate lifts to 0.21 but still FAIL(1). ETF basket of 5 is thin | **feasible-now (Stage 0, ETF basket only)**; Stage 1 **needs-an-order-path**; perp variant **out of scope until the D1 funding term lands** | `RQ-20261007-004` ETF basket, the harness's own three-clause gate |
| Funding-rate carry (perp funding harvest, directional and market-neutral) | Harness exists (`scripts/backtest_funding_carry.py`, exact per-settlement accrual, lagged). **Funding history is NOT reachable from runners**: `RQ-20260929-104` landed `producer_failed` twice (runs 37188714461, 37205968950) because `scripts/ops/fetch_bybit_funding.py` hits api.bybit.com (HTTP 403 from the sandbox, 2026-10-07). **Binance Vision publishes `futures/um/monthly/fundingRate/<SYM>/` and it answers HTTP 200 from this sandbox** — `scripts/ops/fetch_binance_vision.py` has no fundingRate arm yet | Directional variant: the ordinary single-leg path. Neutral variant: a perp leg plus a spot or inverse hedge — **no spot account is configured** (`market_type: spot` is supported by the connector and order path but dormant since the 2026-05-10 cutover) and the pairs executor is single-category | Props: NO — prop venues pay no funding and charge swap on every open position, so there is nothing to harvest | Realised funding on our own accounts IS captured (`exchange_funding` table, `src/runtime/exchange_funding_puller.py`, timer `ict-exchange-funding-pull`) — usable as a recent-months cross-check, not as a 3-year series | **needs-a-feed** (fetcher arm); the neutral variant also **needs-an-order-path** | `blocked/RQ-20261007-005` (clears when a runner-reachable funding series exists) |
| Perp-vs-spot basis (cash-and-carry; basis z-score mean reversion) | Basis series is computable today: `fetch_binance_vision.py --market spot` and `--market futures/um` both fetch; `premiumIndexKlines` also answers 200. No harness emits a basis verdict (`backtest_pairs.py` run on perp-vs-spot of the same symbol approximates it but writes no `verdict.json`) | **NO** — needs a spot leg and a perp leg on one account; pairs executor is one category per account | Props: NO (no spot) | At 1h the basis sits inside the two-leg fee stack most of the time; the question is whether episodes wide enough to pay 2 × (7.5 + 3..5) bps exist often enough. Unknown until measured | **needs-a-feed** (harness/verdict arm) **and needs-an-order-path** | `blocked/RQ-20261007-006` |
| Cross-venue / cross-instrument arbitrage (same asset on two venues) | One crypto venue with an API (Bybit). Binance is a historical data source only — no account, no order path. Prop venues are the other crypto books | **NO.** Prop execution runs through a phone app or a browser bridge with minutes of latency and manual steps; the VM tick is 60 s (`TICK_INTERVAL_SECONDS`) | **Banned where it would matter**: Breakout "cross-account hedging is banned" (`docs/integrations/breakout-compliance-2026-06-16.md` item 12), Tradeify "no cross-account hedge", Velotrade "banned across accounts/firms/platforms" | Latency arbitrage needs sub-second execution on both legs; we have neither the second venue nor the latency | **not-for-us** | none |
| Triangular / cross-rate (crypto crosses, FX) | Our perps are all USDT-quoted (no triangle); Bybit spot crosses are not in any feed we fetch; FX: `oanda_practice` is `dry_run`, roster `[]`, symbols `[XAUUSD]` only | **NO** — three legs in one tick at 60 s cadence is not a triangular arbitrage | n/a | Triangular edges are sub-bps and sub-second by the nature of the trade (reasoning, not measured here) | **not-for-us** | none |
| Index vs constituents / ETF vs underlying | No single-stock data in the corpus table or `config/instruments.yaml`; same-index ETF pairs (SPY/SPLG, GLD/IAUM) exist but SPLG returned 0 rows from yfinance (manifest gap) | Multi-leg basket: NO | n/a | Same-index ETF spreads are a few bps against 2 × (7.5 + 5) bps — fee-bound by construction; a constituents basket is the baskets order-path problem again | **not-for-us** (same-index ETF pairs) / **needs-a-feed** (constituents) | none; QQQ/QLD/TQQQ leveraged-ETF ratio is the one data-ready candidate held for the next batch |
| ETF vs index-future basis (SPY vs ES/MES) | ES_F 1d and SPY 1d both fetched (yfinance); MES resolves to ES=F, front-month continuous, so roll gaps are inside the series | **NO** — legs on two venues/accounts (Alpaca + IB paper); every executor is single-account | IB paper allows it; no prop venue lists index futures | The basis is a financing spread (rates minus dividends) that a front-month-only daily proxy cannot price | **not-for-us** now | none |
| Volatility spreads | `alpaca_options_paper` exists (paper, $150 cash, debit verticals only, 15-minute indicative feed, SLV/GDX chains). No historical options-chain or IV archive in the repo (`scripts/options/probe_alpaca_options.py` is a live probe); no VIX product in `config/instruments.yaml` (`scripts/macro/vix_term_backtest.py` is a macro probe, not a tradeable leg) | Only `express_as: debit_vertical`; no calendar, straddle or variance structure | n/a | Cannot be measured offline today — no historical chain or IV series exists in the repo (checked: docs/research/RESEARCH-CAPABILITY-INDEX.md, scripts/options/probe_alpaca_options.py, scripts/macro/vix_term_backtest.py; `grep -rli implied_vol scripts/ src/` returns only live probes and macro probes) | **needs-a-feed** (historical chains/IV) **and needs-an-order-path** → not for this batch | none |

Counts, measured over this table: 2 families feasible-now at Stage 0 (pairs incl. ratio/z-score; ETF baskets),
2 needs-a-feed (funding carry; perp-spot basis, which also needs an order path), 5 not-for-us or deferred
(cross-venue, triangular, index/ETF-vs-underlying, ETF-vs-future basis, volatility).

## 1. What was checked, and where

**Feeds.** `scripts/ops/fetch_backtest_corpus.py` is the one owner of (symbol, timeframe) → `data/<SYM>_<tf>.csv`.
Crypto comes from Binance Vision (`CRYPTO = "binance_vision"`, with the comment "Bybit is geoblocked from both
this host and GH runners"), equities/ETFs from yfinance (1d uncapped, 1h capped at ~720 d). A pair is fetchable
only if it is in the roster table or `CANDIDATE_PAIRS`; the four 2026-10-04 `producer_failed` runs died on exactly
that refusal. Pairs used below are all in the table: SLV 1d, GLD 1d, TLT 1d, IEF 1d (3650 d), XRPUSDT 1h and
ADAUSDT 1h (1095 d). Committed coverage: `docs/reference/corpus-manifest.json`.

**Public archives probed 2026-10-07 from this sandbox (HTTP status of a HEAD request):**

| URL | status |
|---|---|
| `data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-08.zip` | 200 |
| `data.binance.vision/data/futures/um/monthly/premiumIndexKlines/BTCUSDT/1h/BTCUSDT-1h-2026-08.zip` | 200 |
| `data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-08.zip` | 200 |
| `data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-08.zip` | 200 |
| `api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=1` | 403 |

A sandbox 200 is not a runner 200 — but Binance Vision is already the path every crypto harness dispatch uses
from GitHub runners, so the fundingRate archive on the same host is the right first thing to try. Binance funding
is a PROXY for Bybit funding; the realised Bybit payments on our own accounts (`exchange_funding`) are the
cross-check for the overlap window.

**Order paths.** `src/units/strategies/pairs_executor.py`: an isolated 2-leg executor, one account
(`config/pairs.yaml::account_id`), Bybit client, both-legs-or-nothing pre-placement gate (#6591) and a
leg-imbalance unwind that REPORTS a still-naked leg rather than swallowing it. There is no Alpaca, IB or
cross-account variant, and no N-leg basket executor. Tick cadence is `TICK_INTERVAL_SECONDS` (default 60) in
`src/main.py`. Prop execution is a phone app (`phone_accounts` in `config/prop_platforms.yaml`) or the browser
bridge — minutes, not milliseconds.

**Venue rules.** `config/prop_rulesets/tradeify_247_1step.yaml` (commission 0.04%/trade, swap 0.033%/day,
no same-instrument hedge, no cross-account hedge); `velotrade_classic_1step.yaml` (0.03%/side, 0.05%/night,
hedging allowed within one account, banned across accounts/firms/platforms);
`docs/integrations/breakout-compliance-2026-06-16.md` (hedge mode within one account allowed, cross-account
banned). `enabled_venue_symbols`: Breakout [SOLUSD, ETHUSD], Tradeify [ETHUSD, SOLUSD, XRPUSD], Velotrade
[ETHUSD, SOLUSD] — the only prop pair universe is ETH/SOL(/XRP). Prop pairs feasibility of the EXISTING M22 legs
is the `PAIRS-STOCKTAKE` lane's question (`PI-20261006-APBY4NTV-0004`), not re-derived here.

**Cost stack (D1).** `src/runtime/execution_costs.py` is the one shared model. Printed this session:
non-perp symbols (SLV, GLD, TLT, IEF, QQQ, SPY, MES) resolve to fee 0.0 (Alpaca commission-free) / slippage
5.0 bps rt / funding 0; perps (XRPUSDT, ADAUSDT, SOLUSDT, AVAXUSDT) to slippage 3.0 bps rt / funding 1.0 bps
per 8 h. `backtest_pairs.py` charges its `--fee-bps-roundtrip` default of 7.5 bps PER LEG whatever the venue,
and the dispatch workflow does not override it — so an ETF pair is over-charged on commission, which is the only
stand-in the stack has for the **unmodelled short-leg borrow fee**. Each unit below says so in its rule. The
`xsec_momentum` harness has no funding term (D1 list), so its perp variant stays out of any promotion corpus.

**Capacity at our sizes.** Not the binding constraint for any family: the real-money Bybit book was small enough in 2026-07
that the venue minimum lot, not market depth, decided whether a leg could place at all (not re-measured here)
(`scripts/research/pairs_dollar_lots.py`, M22 G2). Props are $5k–$10k books with fixed leverage.

## 2. Prior evidence this builds on (committed, not re-run)

- M22 pairs sleeve G2 (2026-07-16): fee-free fixed-β-hold $ edge SOL/ETH +1279, BNB/BTC +357, SOL/BTC −295,
  ETH/BTC −855; taker 7.5 bps × 2 legs × ~2800 trades tips all four negative. Recorded in `config/pairs.yaml`
  and `research/queue/blocked/RQ-20260922-011.yaml`.
- `RQ-20260929-105` pairs GLD/GDX 1d (E7 wiring proof, run 37188716030): n=144, net_total_r +18.72,
  net_expectancy_r +0.13, win 62.5%, max DD 6.38 R over 2016-10-06 → 2026-10-02. Landed `no_action_warranted`
  by design (a wiring proof); it is the only net-of-fee ETF pair number we have and the basis for the n
  expectations in RQ-20261007-001/-002.
- `RQ-20260929-104` funding_carry BNBUSDT 1h: `producer_failed` twice on the Bybit funding fetch (above).
- P7 xsec momentum first pass (`docs/research/P7-xsec-momentum-firstpass-2026-06-26.md`): FAIL on 6 coins.

## 3. Findings outside this lane's question (filed, not fixed)

1. **`RQ-20260922-011`'s blocker is cleared.** It is blocked on E37, "nothing routes `scripts/backtest_pairs.py`
   to a workflow". `research-harness-dispatch.yml` now has a `pairs` arm and has landed a measured GLD/GDX run,
   and `research-script-run.yml` can run `scripts/research/pairs_universe_scan.py`. The crypto universe scan it
   pre-registers is the "beyond M22" crypto question and belongs to the PAIRS-STOCKTAKE lane; filed as a pipeline
   row rather than moved here, to avoid two lanes editing the same unit.
2. **Funding history has no runner-reachable source**, and Binance Vision has one (table above). Filed as a build.
3. **The harness-dispatch verdict mapper cannot grade `xsec_momentum`**: it looks for `net_total_r` and
   `total_trades`; that harness emits `net_total_return`, `sharpe_annualized` and a three-clause `gate` block.
   `RQ-20261007-004` will therefore land `wiring_only` and be session-graded until the mapper reads the gate.
   Filed.

## 4. Units registered by this lane

| id | family | run | grading |
|---|---|---|---|
| `RQ-20261007-001` | pairs, ETF 1d SLV/GLD | `research-harness-dispatch.yml` pairs | mechanical (`PASS IF net_total_r > 0 AND n_trades >= 88`) |
| `RQ-20261007-002` | pairs, ETF 1d TLT/IEF | same | mechanical |
| `RQ-20261007-003` | pairs, perps 1h XRPUSDT/ADAUSDT | same | mechanical |
| `RQ-20261007-004` | baskets, ETF xsec momentum 1d | `research-harness-dispatch.yml` xsec_momentum | session (mapper gap, § 3.3) |
| `blocked/RQ-20261007-005` | funding carry, neutral + directional, BTC/ETH perps | none yet | — |
| `blocked/RQ-20261007-006` | perp-vs-spot basis, BTCUSDT | none yet | — |

No code and no config were changed by this lane.
