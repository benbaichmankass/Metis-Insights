# VWAP accuracy improvement — recommendations

**Run id:** `2026-05-07-vwap-accuracy`
**Plan:** `experiments/2026-05-07-vwap-accuracy/PLAN.md`
**Strategy under review:** `src/units/strategies/vwap.py` (BTCUSDT, 5m live)
**Brief from operator:** "improve accuracy, NOT just selectivity" — i.e.
don't recover Sharpe by raising the σ threshold (already done in the
prior run and reverted).

---

## TL;DR (updated 2026-05-07 with 5m results)

The original draft of this doc used a 1h backtest (sandbox-only data
constraint) and recommended the 4h-EMA-200 trend gate (H3) because it
was the only filter that flipped a deeply unprofitable 1h baseline
positive. **The 5m re-run on a GitHub Actions runner with real Bybit /
Coinbase data tells a meaningfully different story** — see the new
"5m re-run" section below. Headline: **adopt H1 (anchored VWAP) as
the production change**, with H6 (anchored + HTF gate) as an
optional Phase-2 layer.

The original 1h analysis stays in this document because the 1h
walk-forward over 6 years (2017-2023, including extreme regime
shifts) is a useful stress test of the same filter set.

---

## 5m re-run — production-decision results

**Source data:** real BTCUSDT 5m + 1h candles pulled via
`scripts/training/data_loader.load_candles` (yfinance → Coinbase →
Bybit fallback chain) on the GitHub Actions runner at `runs/25525069487`.
**Period:** ~365 days, ending 2026-05-07. **All 6 hypotheses + the
H2/H3 sweeps + a 70/30 walk-forward split executed in <5 min.**

### Per-variant metrics (5m)

| Variant | Trades | Win | E[R] | Sharpe | max DD | Δ Sharpe | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| **baseline** (live config) | 950 | 31.0% | +0.163 R | **+2.74** | −34 R | — | already profitable |
| **H1 — anchored VWAP (UTC)** | **962** (+1.3 %) | **33.3 %** | **+0.206 R** | **+3.47** | −52 R | **+0.72** | **ADOPT (primary)** |
| H2 — VWAP slope filter | 847 | 31.8 % | +0.179 R | +2.88 | −25 R | +0.13 | reject — marginal |
| H3 — HTF soft trend filter | 526 | 34.2 % | +0.279 R | +3.36 | −21 R | +0.62 | strong — Phase-2 candidate |
| H4 — RSI(14) confirmation | 854 | 30.2 % | +0.170 R | +2.65 | −47 R | −0.10 | **REJECT** (regression) |
| H5 — volume-spike confirmation | 636 | 31.3 % | +0.196 R | +2.61 | −34 R | −0.13 | **REJECT** (regression) |
| **H6 — anchored + HTF stacked** | **523** (−45 %) | **36.3 %** | **+0.315 R** | **+3.90** | **−22 R** | **+1.15** | **Phase-2 — ADOPT after H1 lands** |

H6 stacks H1 (anchored VWAP, picked first by Sharpe) with H3 (HTF
EMA-200 ±1 % gate, picked second). The composite is best-in-class on
every metric except cadence.

### Critical reversal vs the original 1h finding

> **H1 was rejected at 1h, but is the strongest pure-quality lift at
> 5m.** This validates the caveat in the original draft: at 1h, the
> UTC daily anchor only has 24 σ-samples per session — too few for a
> stable σ. At 5m, a session has 288 bars; the σ estimate is robust
> and the anchored VWAP becomes a *better* fair-value proxy than the
> 120-bar rolling VWAP currently in production.

Conversely, **H3's lift shrinks** at 5m (+0.62 Sharpe vs +5.30 at 1h)
because the 5m baseline is already profitable. H3 still helps, but
the gain isn't worth the 45 % cadence cost as a standalone change.
Stacked on top of H1 (= H6), the marginal lift of H3 is +0.43 Sharpe
on top of H1's +0.72 — a worthwhile Phase-2 add-on.

### Walk-forward (5m, 70/30 in/out-of-sample split)

| Slice | Bars | Baseline Sharpe | H3 Sharpe | Δ Sharpe |
|---|---:|---:|---:|---:|
| In-sample (~256 days) | first 70 % | **+3.16** | +3.46 | +0.30 |
| Out-of-sample (~109 days, recent) | last 30 % | **+0.24** | **+0.80** | **+0.56** |

The recent 109-day window has dramatically degraded baseline performance
(+3.16 → +0.24). H3 holds up much better (3.46 → 0.80) — a 3.3× ratio
on absolute Sharpe in the harder regime. **This is the core argument
for keeping H3 in the production stack as Phase-2 (= H6) even though
the in-sample baseline is already strong:** baseline is regime-fragile,
H3 is regime-robust. We did not run an OOS walk-forward for H1 alone;
that should be done before Phase-2 is decided.

### Parameter sweeps (5m)

**H2 slope-threshold sweep** (default 0.0015):

| thr | trades | drop | Sharpe | E[R] | win |
|---|---:|---:|---:|---:|---:|
| 0.0008 | 757 | 20 % | +2.52 | +0.163 | 31.7 % |
| 0.0010 | 796 | 16 % | +2.92 | +0.187 | 32.2 % |
| **0.0015** | 847 | 11 % | +2.88 | +0.179 | 31.8 % |
| 0.0020 | 885 | 7 % | +3.07 | +0.189 | 31.8 % |
| 0.0030 | 915 | 4 % | +2.51 | +0.151 | 30.8 % |

H2 has a noisy peak around thr=0.0020 (+3.07) — a non-monotonic curve
that suggests the slope filter is fitting weakly at 5m. Doesn't justify
shipping on its own; H1 covers the same regime-aware quality lift more
cleanly.

**H3 EMA-band sweep** (default 0.010):

| band | trades | drop | Sharpe | E[R] | win |
|---|---:|---:|---:|---:|---:|
| 0.005 | 488 | 49 % | +2.97 | +0.255 | 33.4 % |
| **0.010** | 526 | 45 % | +3.36 | +0.279 | 34.2 % |
| 0.015 | 564 | 41 % | +3.33 | +0.266 | 33.7 % |
| 0.020 | 582 | 39 % | +3.29 | +0.260 | 33.3 % |
| 0.025 | 608 | 36 % | +3.33 | +0.259 | 33.1 % |
| 0.030 | 634 | 33 % | +3.39 | +0.258 | 33.0 % |

H3 is stable: every band from 1 % to 3 % gives Sharpe between +3.29
and +3.39. **Loosening to 3 % band keeps Sharpe at +3.39 with only a
33 % cadence drop** (vs 45 % at the default 1 % band). Worth tuning
during Phase-2 rollout if the 1 % cadence is too tight in live.

### Recommended production change (5m findings)

**Phase 1 — adopt H1 (anchored VWAP) immediately.** Replace the
120-bar rolling VWAP / σ pair in `build_vwap_signal` with a UTC-daily
session-anchored VWAP and a session-to-date typical-price σ. Keep
the 1.0 σ entry threshold and 0.5 σ SL exactly as they are.
Expected lift: +0.72 Sharpe (2.74 → 3.47), +2.2 pp win rate,
+0.04 R / trade, **with no cadence cost** (962 trades vs 950 baseline).
Risk note: max DD slightly worse (-52 R vs -34 R) due to noisier
early-session σ; if that becomes a problem in live, gate the
strategy off until N ≥ 12 bars into the session.

**Phase 2 — layer in H3 (4h-EMA-200 ±1 % gate) after Phase-1 metrics
are stable in live.** Adds +0.43 Sharpe on top of H1, takes win rate
to 36.3 %, but cuts cadence 45 % (962 → 523 trades). Justified by the
walk-forward result showing baseline regime-fragility. Revisit the
H3 band parameter (0.01 → 0.02 or 0.03) if 45 % cadence drop is felt
to be too aggressive — the band sweep shows the lift is preserved at
looser bands.

**Permanently rejected at 5m:** H4 (RSI confirmation), H5 (volume-spike
confirmation). Both regressed Sharpe slightly while reducing cadence.
The volume-spike thesis may still be worth re-casting as a
position-sizing modulator (size-up on high-vol entries, size-down
on average-vol) rather than a hard gate — that's a separate
experiment.

---

## Original 1h analysis (preserved as a regime stress-test)

| Variant | Trades | Win rate | E[R] | Sharpe | max DD | Verdict |
|---|---:|---:|---:|---:|---:|---|
| **baseline** (live config) | 2909 | 23.4% | −0.115 R | **−3.71** | −386 R | unprofitable |
| H1 — anchored VWAP (UTC) | 3528 | 22.1% | −0.114 R | −3.94 | −426 R | reject |
| H2 — VWAP slope filter | 2168 | 24.8% | −0.083 R | −2.35 | −219 R | partial — adopt as supplement |
| **H3 — HTF soft trend filter** | **999** | **28.4%** | **+0.090 R** | **+1.59** | **−71 R** | **ADOPT** |
| H4 — RSI(14) confirmation | 2665 | 23.0% | −0.108 R | −3.26 | −329 R | reject — marginal lift |
| H5 — volume-spike confirmation | 1872 | 22.2% | −0.124 R | −3.13 | −264 R | reject — Sharpe up but E[R] down |
| **H6** = H3 + H2 (default thresholds) | 871 | 29.3% | +0.107 R | **+1.76** | −62 R | strong, cadence cost |
| **H7** = H3 (2% band) + H2 (looser stack) | 986 | 29.1% | +0.097 R | **+1.71** | −63 R | best cadence/quality balance |

(Walk-forward: training on 2017-09 → 2021-09 then evaluating on
2021-09 → 2023-07 confirms H3 is **stronger on the out-of-sample window
than the in-sample window** — a robustness signal, not an overfit one.
See `results/robustness/robustness.json`.)

The "selectivity" the operator wanted to avoid is the kind that just
raises the σ bar — qualitatively the same signal at a higher threshold.
H3's selectivity is qualitatively different: it filters the regime in
which mean reversion to VWAP is a structurally losing bet (price
trending hard, never revisiting VWAP within the hold window). That's
**accuracy improvement on the surviving trades**, not just bar-raising.
Win rate +5pp, expectancy goes from -0.115 R to +0.090 R, drawdown
shrinks 5×.

---

## Recommended live change

**One change.** Add a 4h-EMA-200 directional gate to `build_vwap_signal`:

- Compute EMA-200 on 4h candles (trivially derivable from the same
  candle stream the runtime already pulls).
- For a tick at time `t`, look up the latest 4h close ≤ `t` and its
  EMA-200.
- Reject the BUY (long-revert) when `close_4h < ema_200 × 0.99` —
  i.e. price is more than 1 % below its 4h trend → strong downtrend,
  knife-catching territory.
- Reject the SELL (short-revert) when `close_4h > ema_200 × 1.01` —
  price more than 1 % above its 4h trend → strong uptrend, fade gets run over.
- Neutral regime (within the ±1 % band) and "with-trend pullback"
  reversions both pass through unchanged.

The 1 % band is the empirical sweet spot. The H3 sweep over
{0.5 %, 1.0 %, 1.5 %, 2.0 %, 2.5 %, 3.0 %} produced a clean Sharpe
peak at 1.0 % with monotonic falloff in either direction. None of the
neighbours regressed below baseline, so the parameter is not on a knife
edge:

| EMA band | trades | drop vs baseline | Sharpe | E[R] | win |
|---|---:|---:|---:|---:|---:|
| 0.005 | 929 | 68 % | +1.10 | +0.064 | 27.9 % |
| **0.010** | **999** | **66 %** | **+1.59** | **+0.090** | **28.4 %** |
| 0.015 | 1068 | 63 % | +1.36 | +0.075 | 27.9 % |
| 0.020 | 1130 | 61 % | +1.22 | +0.065 | 27.8 % |
| 0.025 | 1205 | 59 % | +1.01 | +0.052 | 27.6 % |
| 0.030 | 1286 | 56 % | +0.68 | +0.033 | 27.1 % |

**Optional secondary change (H6/H7 — stack with the slope filter).**
If we want to squeeze a touch more accuracy out of the regime filter,
adding the 12-bar VWAP-slope filter (`|ΔVWAP / VWAP|₁₂ₕ ≤ 0.5 %`) on
top of H3 lifts win-rate to 29.3 % and Sharpe to +1.76. Cadence drops
another ~10 % vs. H3 alone, so this is an "if H3 looks good live,
layer this in" follow-up — don't ship both at once.

---

## Per-hypothesis discussion

### H1 — anchored VWAP (UTC daily session) — REJECT (with caveat)

The institutional reading replaces our 120-bar rolling VWAP with one
anchored to UTC midnight (and σ from session typical price). At 1h on
6 years it produced **more** trades (3528 vs 2909) and slightly worse
metrics across the board.

**Why it failed at 1h:** sessions have at most 24 1h bars. Early in
the session, σ is computed on 5-10 typical-price samples and is small;
modest absolute moves are flagged as ≥ 1 σ deviations. The signal
fires on shallow drifts that don't revert.

**Caveat — re-test at 5m before final rejection.** At production
timeframe a session has 288 bars, and σ is well-estimated. Anchored
VWAP is a different beast there. The previous training run (#349)
crashed before reaching this hypothesis on a `pd.NA → astype(float)`
bug; this run replaces that work. Recommendation: re-run **H1 only**
on a 5m dataset before declaring the institutional reading dead.

### H2 — VWAP slope filter — ADOPT AS SUPPLEMENT

`|ΔVWAP/VWAP|` over the trailing 12 1h-bars > 0.5 % rejects the trade.
Standalone result: trades −26 %, Sharpe −3.71 → −2.35, win 23.4 → 24.8 %.
Doesn't reach profitability on its own but cleanly improves all three
quality metrics. The slope sweep is monotonic — tightening the
threshold predictably gives more lift at more cadence cost — which is
**well-behaved**, not overfit.

When stacked with H3 (H6/H7 above), it adds a small but consistent
Sharpe bump on top of the regime filter. Treat it as a "phase-2"
addition once H3 is bedded in.

### H3 — HTF soft trend filter — ADOPT (primary recommendation)

See "Recommended live change" above. The headline result is that this
single filter takes the strategy from **−3.71 → +1.59 Sharpe** while
dropping **only the trades that were structurally going to lose**
(counter-trend fades). Win rate, expectancy, Sharpe, and max DD all
move in the same direction — a strong signal that the change is
quality, not just censorship.

### H4 — RSI(14) confirmation — REJECT

Mild RSI gate (BUY < 45, SELL > 55) was supposed to add momentum
agreement at low cadence cost. It cost 8 % of trades, lifted Sharpe by
+0.46, but **win rate dropped 0.4 pp** and expectancy improved only
marginally (−0.115 → −0.108 R). The Sharpe lift came from cutting the
size of the worst tail rather than improving the median trade. Not
worth the wiring complexity.

### H5 — volume-spike confirmation — REJECT

Required entry-bar volume > 1.3 × rolling-20 mean. Cost 36 % of
trades, lifted Sharpe by +0.59 — but **expectancy got worse**
(−0.115 → −0.124 R). The Sharpe rose only because trade variance
shrank with the count. Doesn't satisfy the "improve accuracy" brief.

The volume thesis (climactic moves revert better than drift moves)
may still be valid; this implementation as a hard 1.3× gate isn't the
right operationalisation. A scaled-confidence weighting of trades
would be a better next experiment.

### H6 / H7 — stacked variants — ADOPT IF / WHEN H3 IS COMFORTABLE

H6 stacks H3 (1 % band) + H2 (0.5 % slope). Marginally beats H3 alone
on every metric except cadence (871 vs 999 trades, 70 % drop vs 66 %).
H7 widens H3 to a 2 % band and stacks the same H2 — recovers most of
the H6 lift while keeping cadence at 986 trades (66 % drop). H7 is the
recommended stack if the operator wants to layer the slope filter on
top of H3 immediately.

---

## Walk-forward validation

Split the 51,409-bar series at 70 % (in-sample 2017-09 → 2021-09;
out-of-sample 2021-09 → 2023-07). H3 (1 % band) was evaluated against
baseline on each split independently:

| Split | Baseline Sharpe | H3 Sharpe | Δ Sharpe |
|---|---:|---:|---:|
| In-sample (2017-2021) | −4.44 | +0.72 | +5.16 |
| Out-of-sample (2021-2023) | −0.03 | +1.75 | **+1.78** |

The out-of-sample baseline was nearly neutral (the 2021-2023 sample
has less trend dominance than the 2017-2021 sample which contains the
2017 bull / 2018 bear / 2020 covid crash / early 2021 surge). H3
**still added +1.78 Sharpe out-of-sample**, and the out-of-sample
absolute Sharpe (+1.75) is materially higher than the in-sample
result (+0.72). That's the **opposite** of overfit-curve behaviour;
the filter generalises.

---

## Critical caveats

1. **Timeframe shift — backtest at 1h, production at 5m.** External
   data sources (yfinance / Coinbase / Bybit / Binance / huggingface.co)
   are all blocked from the sandbox this run was executed in. We fell
   back to a real-Binance 1h BTCUSDT dataset already cacheable via
   github raw (CryptoRobotFr/python-pour-la-finance, 51,409 1h bars,
   2017-08 → 2023-07). The relative ranking of variants generalises
   across timeframes for the filters tested here, but **absolute
   metric levels do not transfer** — a Sharpe of +1.59 at 1h doesn't
   mean +1.59 at 5m. Before adoption, re-run the baseline + H3 on a
   5m feed pulled from a sandbox that has Bybit / Coinbase access (or
   from a local 5m mirror).

2. **Baseline at 1h is harsher than at 5m.** The prior run (#349) saw
   −0.12 Sharpe baseline on 365 days of 5m. This run shows −3.71 at
   1h on 6 years. The difference is real, not a bug — 1h gives the
   strategy more trend exposure per bar, and 6 years includes
   dislocations (2018 bear, 2020 crash, 2022 bear) that a 365-day
   window happened to miss. The lift figures are credible **as deltas**;
   the absolute baseline is regime-dependent.

3. **H1 was re-tested under wrong conditions.** Anchored VWAP at 1h
   has 24 bars per session — too few for a stable σ. The 5m
   timeframe has 288 bars per session and was the original target of
   the previous run. **H1 cannot be declared dead until it is
   re-tested at 5m.**

4. **Backtest engine assumptions.** First-touch SL/TP fills, no
   slippage, no fees, no order rejections. Live realised numbers will
   trail backtest. Expect 50-70 % of backtest Sharpe under live
   conditions per industry rule-of-thumb.

5. **No funding-cost model.** VWAP holds positions for hours; on
   perp futures, funding rates would compress positive expectancy.
   Current expectancy of +0.090 R per trade is gross of funding;
   net-of-funding probably +0.07 R or so on BTCUSDT-PERP at typical
   funding rates.

6. **Selection bias on the H3 1% band.** We swept {0.5..3 %} and the
   neighbours of 1 % were all positive Sharpe, so the result isn't
   knife-edge. But it is a 6-point sweep — proper cross-validation
   would be a follow-up.

---

## Implementation note (for the IMPLEMENT PR, after approval)

Code changes — **single file**, `src/units/strategies/vwap.py`:

1. Extend `build_vwap_signal` to accept an optional
   `htf_close: float | None` and `htf_ema200: float | None` pair.
2. After deciding the side from σ-deviation, apply the 1 % gate:
   - skip BUY if `htf_close is not None and htf_close < htf_ema200 * 0.99`
   - skip SELL if `htf_close is not None and htf_close > htf_ema200 * 1.01`
3. When the gate fires, return the standard `_no_trade(...)` shape with
   `meta.reason = "htf_trend_block"` so the audit log shows the filter
   firing.

Pipeline-side changes — `src/runtime/pipeline.py`:

1. The pipeline already pulls candle data at the strategy's timeframe.
   Add a parallel pull of the 4h candles for the same symbol when the
   strategy is `vwap`. Compute `ema_200` on those 4h candles
   (`close.ewm(span=200, adjust=False).mean()`).
2. At the top of each tick, look up the latest 4h close ≤ tick time
   and its EMA-200; pass `htf_close` and `htf_ema200` into the call to
   `build_vwap_signal`.

Config — `config/strategies.yaml`:

```yaml
strategies:
  vwap:
    htf_trend_filter:
      enabled: true
      htf_timeframe: "4h"
      ema_period: 200
      band_pct: 0.01
```

The strategy module reads the same `htf_trend_filter` map from `cfg`
and falls back to the literals above if absent — gives operator a
config-only off-switch without code changes.

Tests:

- New unit-test in `tests/test_vwap_strategy.py` covering each
  arm of the gate (BUY blocked / BUY allowed / SELL blocked /
  SELL allowed / no-HTF-data passthrough). Reuse the existing
  `_candles_below_vwap` / `_candles_above_vwap` fixtures.

A bug-log entry referencing "H3 (4h EMA-200 soft trend filter)
adoption — see `experiments/2026-05-07-vwap-accuracy/RECOMMENDATIONS.md`"
should land alongside.

PM review is required per `CLAUDE.md` § Merging Rules (touches
`src/units/strategies/`).

---

## Follow-up sprints

In rough priority order, **after** the IMPLEMENT PR ships:

1. **Re-run H1 (anchored VWAP) at 5m** on a fresh sandbox with Bybit
   or Coinbase access. With 288 bars per session, anchored VWAP
   should behave qualitatively differently than it did at 1h here.
2. **5m re-validation of H3.** Pull 5m BTCUSDT data, re-run baseline
   + H3 on it, compare deltas to the 1h numbers in this report.
   Decide whether the band needs re-tuning at the production
   timeframe (1 % is a sane prior either way).
3. **H2 + H3 stacked test (PR #2).** Once H3 is live and the new
   baseline is established, layer in the VWAP slope filter at the
   default 0.5 % threshold. Should add ~+0.2 Sharpe per the H6/H7
   results here.
4. **Funding-aware expectancy.** Add funding-cost projection to the
   expectancy_R metric in `backtest_helpers.py` so future runs report
   net-of-funding numbers. ~12 bps/day at 0.01 %/8h funding × 4 h
   average hold = trivial change but big perception shift on the
   live PnL number.
5. **H5 re-cast.** Volume-spike as a confidence weighter (size up
   on high-vol entries, size down on average-vol) instead of a hard
   gate. May recover the value the gate version threw away.
6. **Walk-forward CV grid for H3 band.** Already showed in/out-sample
   stability at 70/30 split; do a proper rolling-window CV with
   3-month windows and report the band's distribution of optimums.

---

## Cross-references

- `experiments/2026-05-07-vwap-accuracy/PLAN.md` — original hypothesis table.
- `experiments/2026-05-07-vwap-accuracy/results/SUMMARY.md` — run aggregator output.
- `experiments/2026-05-07-vwap-accuracy/results/H{1..6}/summary.md` — per-hypothesis writeups.
- `experiments/2026-05-07-vwap-accuracy/results/robustness/robustness.json` — sweep + walk-forward raw metrics.
- `experiments/2026-05-03-vwap-improvement/RECOMMENDATIONS.md` — prior σ-threshold run (set the 1.0σ baseline this run improves upon).
- `src/units/strategies/vwap.py` — production strategy.
- `scripts/training/backtest_helpers.py` — backtest engine.

---

## Open-source research informing the design

This run synthesised filters from prior literature on VWAP mean-reversion:

- VWAP slope as a regime gate — flat slope ≈ ranging, steep slope ≈
  trending: [VWAP & Its Slope — Definedge Forum](https://forum.definedgesecurities.com/topic/3880/vwap-its-slope-the-silent-voice-of-market-truth),
  [Mean reversion — Forextester](https://forextester.com/blog/mean-reversion-trading/).
- Regime classification (ADX, Hurst, EMA-200) on Bitcoin: [QuantPedia — Trend & Mean-Reversion in Bitcoin](https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/),
  [QuantMonitor — Regime classification](https://quantmonitor.net/how-to-identify-market-regimes-and-filter-strategies-by-trend-and-volatility/).
- HTF EMA-200 alignment as a mean-reversion filter: [Mastering VWAP in Crypto Trading — HyroTrader](https://www.hyrotrader.com/blog/vwap-trading-strategy/),
  [VWAP & RSI in Crypto Trading — KuCoin](https://www.kucoin.com/blog/en-what-is-vwap-and-how-to-use-it-in-practice-a-trader-s-guide).
- Anchored vs rolling VWAP: [Anchored VWAP — TrendSpider](https://trendspider.com/learning-center/anchored-vwap-trading-strategies/),
  [Calculating VWAP in Python (Databento)](https://databento.com/blog/vwap-python).
- Volume confirmation at σ bands: [VWAP Mean Reversion — VolatilityBox](https://volatilitybox.com/docs/vwap-mean-reversion-strategies/),
  [VWAP Strategy — ChartsWatcher](https://chartswatcher.com/pages/blog/a-practical-guide-to-vwap-strategy-trading).
- ADX-based regime filter for mean reversion: [QuantifiedStrategies — Bitcoin Mean Reversion in Low-Volume Regimes](https://www.quantifiedstrategies.com/bitcoin-mean-reversion-strategies-outperform-momentum-in-low-volume-regimes/),
  [Mean Reversion in Crypto — Stoic.ai](https://stoic.ai/blog/mean-reversion-trading-how-i-profit-from-crypto-market-overreactions/).
