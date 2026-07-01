# Leveraged & Inverse Equity ETF Research (TQQQ et al.) — 2026-06-30

> **Tier-1 research memo.** Analysis + evidence + proposals only. Nothing here
> touches the live order path, `config/strategies.yaml`, `config/accounts.yaml`,
> or any unit the live VM consumes. Every promotion named below is **Tier-3**
> and **held for explicit operator approval** — this memo is the evidence step
> that precedes that decision.
>
> Origin: operator direction 2026-06-30 — *"research if there are leveraged
> equities that would be good symbols for us to be trading on … things like TQQQ
> and similar ideas. Do the backtesting, build what you need … hold off any
> Tier-3 decisions for when I can answer."*

---

## 0. TL;DR

- **The question is well-posed because the bot sizes by risk, not by notional.**
  Every cell sets share count so a fixed % of the account is at risk to a
  2.5×ATR stop. A 3× ETF has ~3× the ATR, so the position holds ~⅓ the shares —
  **leverage does not automatically mean "bigger bets."** The real question is:
  *net of the leveraged ETF's volatility decay and fees, does the bot's
  validated trend cell preserve risk-normalised performance (R) on a leveraged
  ETF?* The backtests answer that directly because they run on the **actual
  leveraged-ETF price series**, which already embeds decay, the expense ratio,
  and financing cost.

- **Answer: YES for the smooth large-cap growth indices (Nasdaq-100, S&P 500),
  NO for the choppier ones (small-cap, Dow, semiconductors).** This is exactly
  what the academic literature predicts (Gayed–Bilello *Leverage for the Long
  Run*; Avellaneda–Zhang): a trend filter sidesteps the high-volatility chop
  that generates leveraged decay, so leverage compounds favourably in trends.

- **The standout is TQQQ (3× Nasdaq-100).** On the bot's *own* validated daily
  Donchian trend-long cell, run through the repo's *own* canonical gate
  (5-fold anchored walk-forward → `classify_strategy_tier`), **TQQQ grades
  `paper_ready` and beats the already-LIVE QQQ cell (+13.8R vs +10.4R OOS,
  2019–2026) with 2×-fee headroom and lower R-drawdown.** QLD (2×) is similar
  (+12.7R). A leveraged Nasdaq/S&P **book** (TQQQ+QLD+UPRO+SSO) grades the same
  way the operator's already-deployed ETF-breadth book did: net +144.7R, **all
  recent holdouts positive**, bootstrap P(net>0)=0.999, breakeven added-cost
  +0.62 R/trade.

- **Leveraged S&P (UPRO/SPXL/SSO) ≈ unleveraged SPY** — `paper_ready`, no R
  improvement but no degradation. They add capital efficiency, not edge.

- **Reject for the trend cell:** leveraged small-cap (TNA), Dow (UDOW), and —
  more surprisingly — semis (SOXL +0.9R vs unleveraged **SMH +15.4R**). Decay
  destroys the edge on higher-σ / choppier underlyings.

- **Inverse ETFs (SQQQ/SPXS/SOXS/TZA/SDOW) as a long-trend instrument: hard
  reject** (−8 to −17R). Indices trend *up*; "long the inverse" only catches
  sharp, mean-reverting downtrends that chop a trend-follower to pieces.
  Downside exposure needs a *different* edge (mean-reversion/fade), not the
  trend cell.

- **Capital-efficiency angle (real, but nuanced).** On the live `alpaca_live`
  account (~$150, `risk_pct` 0.10, whole-share bracket orders), **QQQ rounds to
  0 shares — literally untradeable — while TQQQ/QLD/UPRO give a valid 1-share
  position.** So on that tiny account leveraged ETFs are the *only* way to hold
  an index-trend position at all. **Caveat:** this only works because the
  account runs an aggressive 10% per-trade risk; at a prudent 2% risk *nothing*
  (leveraged or not) is tradeable on $150. The clean fix is a larger balance or
  fractional-share bracket support — leverage is a partial workaround, not a
  free lunch.

- **Side-finding worth flagging:** **SMH (unleveraged semiconductors) grades
  `paper_ready` at +15.4R** — the strongest single daily-trend equity cell in
  this whole sweep, and the bot trades *neither* SMH nor SOXX today. A genuine
  un-leveraged expansion candidate independent of the leverage question.

**Recommendation (all Tier-3, paper-first, operator-gated):** add **TQQQ** and
**QLD** as daily trend-long cells on `alpaca_paper` first (they match/beat the
live QQQ cell); treat **UPRO/SSO** as optional capital-efficiency variants of
the SPY cell; **do not** add leveraged small-cap/Dow/semis or any inverse ETF to
the trend cell; consider **SMH** as a separate un-leveraged candidate. Full
ranked list in §6.

---

## 1. Method (what was actually run)

All backtests run **locally in this session** against **real OHLCV pulled from
the Yahoo chart API** (full history per symbol, 2006/2008/2010 inception →
2026-06-30), using the repo's **own** harnesses and gate — i.e. the canonical
methodology, run in the sandbox rather than dispatched to the trainer:

- **Strategy = the live daily trend-long cell**, exact params from
  `config/strategies.yaml::spy_trend_long_1d` / `qqq_trend_long_1d` /
  `iwm_trend_long_1d`: Donchian 30, ATR 14, atr-stop 2.5, trail 4.0, **long
  only**. (`scripts/backtest_trend.py`.) The daily pullback family
  (`gld_pullback_1d` params) was run too as a cross-check.
- **Fees:** net-of-fee at 7.5 bps round-trip (the existing ETF-research base),
  stress-tested at 15 bps (2×). Alpaca is commission-free; these cover
  spread/slippage.
- **Decay + expense ratio are already in the data.** Because the backtest runs
  on the *actual* TQQQ/SPXL/… price series, the daily-rebalance volatility decay
  AND the ~0.75–1.0% expense ratio AND the embedded financing cost are already
  reflected in every net-R figure. Nothing needs to be added for them. The only
  effects *not* captured are transaction fees (modelled separately above) and
  idealised stop fills (see the gap-risk caveat in §5).
- **Canonical gate:** per symbol, base-fee + 2×-fee per-trade emits →
  `scripts/ops/m15_ws_b_fold_report.py` (5-fold anchored walk-forward,
  2019-01-01 → 2026) → `scripts/ops/classify_strategy_tier.py`
  (reject / paper_ready / live_ready). A leveraged **book** was additionally
  graded through `scripts/ops/portfolio_robustness.py` (per-year, multi-cutoff
  holdout, leave-one-out, block bootstrap) — the same gate chain the live ETF
  cells passed.
- Matched-window comparisons restrict every symbol in a group to the latest
  inception date in that group, so leveraged-vs-baseline is apples-to-apples.

Reproduction scripts and the raw fetched CSVs live under `scratch_levetf/` in
this session's working tree (not committed); a ready-to-run trainer study spec
ships alongside this memo at
`config/research/studies/leveraged_etf_trend.yaml`.

---

## 2. The candidate universe (verified specs)

Liquid US leveraged/inverse equity-index ETFs, verified against issuer pages +
StockAnalysis/etf.com/etfdb (late-June-2026 data; AUM/volume drift daily). All
are **daily-reset** products — unsuitable for buy-and-hold, by issuer + SEC
framing.

| Ticker | Lev | Underlying | Structure | Expense | Inception | Liquidity (≈ $/day) |
|---|---|---|---|---|---|---|
| **TQQQ** | +3× | Nasdaq-100 | ETF (ProShares) | 0.84–0.95% | 2010-02-11 | very high |
| **QLD** | +2× | Nasdaq-100 | ETF (ProShares) | 0.95% | 2006-06-21 | high |
| SQQQ | −3× | Nasdaq-100 | ETF (ProShares) | ~0.95% | 2010-02-11 | very high |
| **UPRO** | +3× | S&P 500 | ETF (ProShares) | 0.89% | 2009-06-25 | ~$164M |
| **SPXL** | +3× | S&P 500 | ETF (Direxion) | ~0.84% | 2008-11-05 | ~$200M |
| **SSO** | +2× | S&P 500 | ETF (ProShares) | 0.87% | 2006-06-19 | ~$80M |
| SPXS | −3× | S&P 500 | ETF (Direxion) | ~1.04% | 2008-11-05 | ~$77M |
| SPXU | −3× | S&P 500 | ETF (ProShares) | 0.90% | 2009-06-25 | ~$88M |
| TNA | +3× | Russell 2000 | ETF (Direxion) | ~1.05% | 2008-11-05 | ~$210M |
| TZA | −3× | Russell 2000 | ETF (Direxion) | ~0.99% | 2008-11-05 | high |
| UWM | +2× | Russell 2000 | ETF (ProShares) | 0.95% | 2007-01-23 | ~$5M (thin) |
| UDOW | +3× | Dow 30 | ETF (ProShares) | 0.95% | 2010-02-09 | ~$50M |
| SDOW | −3× | Dow 30 | ETF (ProShares) | 0.95% | 2010-02-09 | ~$32M |
| **SOXL** | +3× | NYSE Semiconductor | ETF (Direxion) | 0.75% | 2010-03-11 | ~$6.2B |
| SOXS | −3× | NYSE Semiconductor | ETF (Direxion) | 1.00% | 2010-03-11 | ~$1.3B |
| TECL | +3× | Technology Select | ETF (Direxion) | ~0.94% | 2008-12-17 | high |
| ~~FNGU/FNGG~~ | 3×/2× | NYSE FANG+ | **ETN / short hist** | — | reset 2025 / 2023 | **excluded** |

**FNGU is excluded:** it is an **ETN** (unsecured BMO debt → issuer credit risk,
unlike the swap/futures ETFs), and the ticker was redeemed & relaunched in 2025
(new 2045-dated series) so there is **no continuous price history** to backtest
(our fetch returned only 341 bars). **FNGG** (Direxion 2× ETF, a *different*
product, not FNGU's successor) only adopted the FANG+ mandate in March 2023 →
also too short. Both fail the "clean, long, continuous history" bar.

**Notes on history changes that matter for backtests:** SOXL/SOXS switched
benchmark (PHLX SOX → ICE/NYSE Semiconductor Index) on 2021-08-25 — index
change only, leverage stayed 3×; the price series is continuous. No other
candidate changed leverage factor over its life.

---

## 3. Why leverage + a trend filter is the academically-supported combination

The daily reset makes a leveraged ETF's multi-day return **path-dependent**:
return ≈ L·r − ½·L·(L−1)·σ² per unit time. The drag term is worst in
**high-σ, sideways** markets and *negative* (a tailwind) in **steady trends** —
which is why a 3× fund can beat naive-3× in a bull run yet lose money while the
index ends flat.

- Regulators' canonical example (FINRA 09-31 / SEC): an index **+2%** over a
  choppy stretch with a 2× fund **−6%** and the 2× inverse **−25%**; a +8%
  financials index with the 3× fund **−53%**. Decay is real and non-linear in L.
- Academic consensus (Cheng–Madhavan 2009; Avellaneda–Zhang 2010,
  *Path-Dependence of Leveraged ETF Returns*; Trainor–Baryla 2008): **buy-and-
  hold of leveraged ETFs is unsuitable** — but path-dependence cuts both ways.
- **Gayed–Bilello, *Leverage for the Long Run* (2016 Dow Award):** applying
  leverage *only* when the index is above its 200-day MA (a trend filter), else
  cash, beats **both** buy-and-hold **and** constant leverage on **return AND
  drawdown** — precisely because the filter avoids the high-vol regimes that
  generate decay.

The bot's Donchian-30 trend-long cell **is** a trend filter — it is long only
during established uptrends and flat otherwise. So testing it on leveraged ETFs
is testing exactly the configuration the literature says should work. The
results below confirm it empirically on the bot's own cell.

*(Evidence-confidence: the regulator worked-examples and the three papers'
qualitative findings are primary/strong; precise Gayed–Bilello Sharpe/return
decimals are from secondary summaries — treat the direction as solid, the
decimals as indicative. Sources listed in §8.)*

---

## 4. Results

### 4.1 Matched-window, leveraged vs baseline — daily TREND long-only (net 7.5 bps)

Each group windowed from the latest inception in the group (apples-to-apples).
R = risk-normalised return; maxDD in R.

| Group (window) | sym | lev | trades | net R | win% | exp R | maxDD R |
|---|---|---|---|---|---|---|---|
| **Nasdaq-100** (2010-02) | QQQ | 1× | 48 | **39.0** | 52.1 | 0.81 | 4.8 |
|  | QLD | 2× | 53 | 37.7 | 52.8 | 0.71 | 4.8 |
|  | **TQQQ** | 3× | 55 | **39.9** | 49.1 | 0.73 | **4.0** |
| **S&P 500** (2009-06) | SPY | 1× | 50 | 25.9 | 48.0 | 0.52 | 4.9 |
|  | SSO | 2× | 55 | 24.5 | 49.1 | 0.45 | 4.4 |
|  | **UPRO** | 3× | 56 | **29.2** | 50.0 | 0.52 | 4.1 |
|  | SPXL | 3× | 59 | 22.1 | 49.1 | 0.37 | 4.3 |
| **Russell 2k** (2008-11) | IWM | 1× | 50 | 16.0 | 46.0 | 0.32 | 7.4 |
|  | UWM | 2× | 58 | 10.6 | 39.7 | 0.18 | 5.2 |
|  | TNA | 3× | 67 | **−3.1** | 40.3 | −0.05 | 9.5 |
| **Dow 30** (2010-02) | DIA | 1× | 55 | 21.0 | 38.2 | 0.38 | 9.2 |
|  | UDOW | 3× | 65 | 10.8 | 38.5 | 0.17 | 8.2 |
| **Semis** (2010-03) | SMH | 1× | 60 | **30.4** | 43.3 | 0.51 | 6.1 |
|  | SOXX | 1× | 64 | 18.1 | 39.1 | 0.28 | 6.6 |
|  | SOXL | 3× | 70 | 17.4 | 38.6 | 0.25 | 7.3 |

**Read:** on Nasdaq-100 leverage is *R-neutral to slightly better* (TQQQ ≥ QQQ,
lower maxDD); on S&P leverage is ~neutral (UPRO > SPY, SPXL < SPY — tracking
difference between the two 3× S&P funds); on Russell/Dow/Semis leverage
**destroys** R. The split tracks underlying volatility exactly as the decay
theory predicts.

### 4.2 Fee robustness + recent anchored holdouts (daily trend)

All survivors barely move 7.5→15 bps (few trades, wide trend). The recent
holdouts are the clincher — **leveraged ≥ baseline, with lower R-drawdown:**

| from 2019-01 | net R | maxDD R | | from 2022-01 (incl. bear) | net R | maxDD R |
|---|---|---|---|---|---|---|
| QQQ | 21.8 | 3.4 | | QQQ | 10.4 | 3.1 |
| **TQQQ** | **25.1** | **2.0** | | **TQQQ** | **13.8** | **1.9** |
| SPY | 11.0 | 4.9 | | SPY | 8.1 | 2.8 |
| **UPRO** | **13.0** | 3.6 | | UPRO | 8.0 | 2.6 |

The trend filter kept the bot *mostly flat through 2022*, so the leveraged
versions avoided the decay-heavy bear and slightly out-earned the underlying in
the trends — TQQQ's worst calendar year (2022) was −1.2R vs QQQ's −2.4R.

### 4.3 Canonical tier gate (5-fold anchored WF 2019–2026, net, 2×-fee stress)

| Underlying | 1× (current live cell) | 2× | 3× |
|---|---|---|---|
| Nasdaq-100 | QQQ `paper_ready` +10.4 | QLD `paper_ready` +12.7 | **TQQQ `paper_ready` +13.8** |
| S&P 500 | SPY `paper_ready` +7.1 | SSO `paper_ready` +5.4 | UPRO +6.9 / SPXL +7.1 `paper_ready` |
| Russell | IWM **`reject`** +0.2 | — | TNA **`reject`** −3.3 |
| Dow | DIA `paper_ready` +4.0 | — | UDOW **`reject`** −1.4 |
| Semis | **SMH `paper_ready` +15.4** | — | SOXL `paper_ready` +0.9 |

No cell is `live_ready` on the 2019+ window (none is every-fold positive — all
have a negative fold in the 2022 bear), which is the **same** result the live
QQQ/SPY cells get on this window. **TQQQ outgrades the already-live QQQ cell.**
(IWM grading `reject` on 2019+ is a property of the window, not the leverage —
IWM was `live_ready` on full 19-yr history in the 2026-06-20 study; small-cap
trend simply weakened post-2019. It does not affect the leverage conclusion.)

### 4.4 Leveraged-book portfolio robustness (the deployable-grade check)

Book = TQQQ + QLD + UPRO + SSO, daily trend-long, full history, via
`portfolio_robustness.py`:

| axis | result |
|---|---|
| Headline | **net +144.7R**, positive in 11 of 16 years |
| **Recent holdouts (5 cutoffs)** | **ALL POSITIVE** — ≥2023-07 +40.6R (Sh 3.32) … ≥2025-07 +5.9R (Sh 1.0) |
| Leave-one-out | all additive (QLD most load-bearing: book-without = 98.0R) |
| **Block bootstrap (2000×)** | **P(net>0)=0.999**, 5th-pct **+65.0R** |
| Added-cost breakeven | **+0.62 R/trade** (huge headroom vs ~1–2 bps ETF cost) |
| every-calendar-year | ✗ 5 of 16 slightly negative (chop years — normal) |

Verdict string: **"NOT fully robust"** — but *only* because it fails the
strictest every-calendar-year bar; it **passes holdouts + leave-one-out +
bootstrap**, the axes that predict forward performance. **This is the identical
verdict pattern the operator's already-deployed ETF-breadth book carries** — a
deployable-grade, fee-resilient book by the repo's own canonical standard.

### 4.5 Inverse ETFs (trend-long-only, full history) — REJECT

SQQQ −14.3R · SPXS −14.1R · SPXU −15.8R · SOXS −9.1R · TZA −7.9R · SDOW −16.7R.
Long-trend-following an inverse ETF means betting on *sustained* index
downtrends; those are rare, sharp, and mean-reverting, so the trend cell is
repeatedly chopped. Inverse ETFs are not a trend instrument. If downside
participation is wanted, it is a separate mean-reversion/fade research track.

---

## 5. Honest caveats

1. **Gap / tail risk is not in the backtest.** The harness fills at the stop
   price; in reality a 3× ETF can **gap through** an overnight stop. The dreaded
   single-day index −33% = −100% on a 3× fund. The bot's daily cells hold
   overnight, so this tail is real and 3×-amplified even though R-drawdown looks
   tame. This is the single biggest reason to **paper-soak first** and to prefer
   the most liquid names (TQQQ) where the spread/depth is best.
2. **R-drawdown ≠ dollar-drawdown intuition.** R normalises by each trade's
   stop; with correct risk sizing the *dollar* risk per trade is the same as on
   the unleveraged ETF. Leverage doesn't reduce dollar risk — it lets a smaller
   balance express the position (§4 capital efficiency), at the cost of higher
   concentration per share.
3. **The $150 account is genuinely undersized.** At a prudent 2% per-trade risk,
   *nothing* (leveraged or not) clears whole-share sizing on $150. The leveraged
   advantage materialises only at the account's current aggressive 10% risk,
   where a single 1-share TQQQ position is still ~54% of the account. The
   structural fixes (bigger balance; fractional-share bracket support) dominate
   the leverage question for that account.
4. **Local run, not the trainer.** This used the canonical scripts on
   Yahoo full-history daily data. A trainer cross-check on its longer-history
   parquet cache + an intraday (1h) sweep on TQQQ/QQQ would further harden the
   result; recommended as the next step, not a blocker (the daily evidence is
   already multi-method-consistent).
5. **Sample size is modest** (~50–70 daily trades over 16 yr per cell) — wide
   trend cells trade rarely. The book-level bootstrap (152+ trades) is the more
   reliable grade than any single cell.

---

## 6. Recommendations (ranked) — ALL Tier-3, paper-first, HELD for operator

| # | Action | Tier | Why |
|---|---|---|---|
| **1** | Add **TQQQ** daily trend-long cell (`tqqq_trend_long_1d`, QQQ params) to **`alpaca_paper`** | T3 | Outgrades the live QQQ cell (+13.8 vs +10.4R OOS), 2×-fee headroom, holdouts positive, most-liquid 3× fund. The single best candidate. |
| **2** | Add **QLD** (2× Nasdaq) daily trend-long cell to `alpaca_paper` | T3 | `paper_ready` +12.7R; lower decay than 3×; lower per-share price aids the small account; book leave-one-out shows it most load-bearing. |
| 3 | Consider **UPRO** and/or **SSO** as capital-efficiency variants of the SPY cell on `alpaca_paper` | T3 | `paper_ready`, R-neutral-to-better vs SPY; UPRO/SSO lower share price than SPY → tradeable on the tiny account. Prefer UPRO (3×, beat SPY) or SSO (2×, less decay). Skip SPXL (worse of the two 3× S&P funds here). |
| 4 | Consider **SMH** (un-leveraged semis) as a separate daily trend-long cell | T3 | Side-finding: **+15.4R `paper_ready`**, strongest equity-trend cell in the sweep; bot trades no semis today. Independent of the leverage question. |
| — | **Do NOT** add TNA / UWM / UDOW / SOXL / TECL leveraged trend cells | — | Decay destroys the edge on choppier/higher-σ underlyings (Russell/Dow/Semis: `reject` or near-zero). |
| — | **Do NOT** add any inverse ETF (SQQQ/SPXS/SOXS/TZA/SDOW) to a trend cell | — | −8 to −17R; wrong edge type for inverse instruments. |

**Sequencing if approved:** (a) add TQQQ + QLD `instruments.yaml` entries
(`exchange: alpaca`, equity, whole-share, max_leverage already handled by
Alpaca) + `alpaca_paper` strategy cells in `config/strategies.yaml`; (b) soak on
paper; (c) run the **mandatory `account_compat_matrix` daily/equity gate**
(`scripts/ops/etf_account_compat.sh`) before any `alpaca_live` promotion — the
same gate the existing ETF cells passed; (d) only then consider a real-money
flip, operator-approved. A ready-to-run trainer sweep spec is shipped at
`config/research/studies/leveraged_etf_trend.yaml`.

---

## 7. Open questions for the operator (Tier-3 calls held for you)

1. **Paper-soak TQQQ + QLD now?** (Tier-3 `alpaca_paper` wire — lowest-risk way
   to start accruing a live track record. My recommendation: yes.)
2. **Is the leveraged-ETF *capital-efficiency* motivation primary, or the
   *edge* motivation?** If capital efficiency for the $150 account is the driver,
   the bigger lever is account size / fractional-share bracket support — worth a
   separate decision. If it's the edge, TQQQ/QLD stand on their own R.
3. **Appetite for the 3× overnight-gap tail?** Even paper-validated, a 3× fund
   carries a fatter overnight tail than the current ETF book. Comfort level sets
   how aggressively (TQQQ 3× vs QLD 2×) to lead with.
4. **Add SMH (un-leveraged semis) regardless?** It's the strongest cell found
   and orthogonal to the leverage decision.
5. **Want the trainer cross-check + a 1h intraday TQQQ sweep** before any wire,
   or is the daily evidence sufficient to paper-soak now?

---

## 8. Sources

Specs (issuer + aggregator, late-June-2026): ProShares / Direxion product pages;
StockAnalysis.com per-ticker pages (TQQQ, QLD, SQQQ, UPRO, SPXL, SSO, SPXS,
SPXU, TNA, TZA, UWM, UDOW, SDOW, SOXL, SOXS, FNGU, FNGG); SEC EDGAR (BMO ETN
filings; Direxion 497 SOXL/SOXS index change); etf.com / etfdb / US News /
Morningstar cross-checks. Decay/path-dependence: SEC Investor.gov leveraged-ETF
bulletin; FINRA Regulatory Notice 09-31 + Non-Traditional ETF FAQ; Cheng &
Madhavan (2009) SSRN 1393995; Avellaneda & Zhang (2010) SIAM J. Fin. Math.
1:586–603 / SSRN 1404708; Trainor & Baryla (2008) FPA Journal; Gayed & Bilello,
*Leverage for the Long Run* (2016 Dow Award) + CXO Advisory / Proactive Advisor
summaries; Double-Digit Numerics "Big Myth about Leveraged ETFs"; QuantPedia.
(Full URL list captured in this session's research transcript.)

*Research memo only — nothing here changes runtime behaviour. Survivors wire to
paper via the normal Tier-3 PR; real-money promotion stays operator-gated and
`account_compat_matrix`-gated.*
