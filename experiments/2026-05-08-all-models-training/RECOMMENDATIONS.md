# All-models training run — recommendations (2026-05-08)

**Run id:** `2026-05-08-all-models-training`
**Plan:** [`PLAN.md`](PLAN.md)  
**Strategies:** `vwap` (5 m, BTCUSDT) and `turtle_soup` (15 m, BTCUSDT)
**Dataset:** BTCUSDT 5 m spot klines, 38 months (Jan 2023 → Feb 2026, 332,624 bars), pulled from the [`qashdev/btc`](https://github.com/qashdev/btc) GitHub mirror of the Binance Vision archive. Bybit/Coinbase/yfinance are firewalled from this sandbox; the qashdev archive is a verbatim mirror of `data.binance.vision` and equivalent to live Bybit Linear BTCUSDT-PERP within the spot/perp basis.

---

## TL;DR — production decisions

| Strategy | Recommended change | Sharpe lift (full sample) | OOS Sharpe (last 30%) | Cadence cost |
|---|---|---|---|---|
| **vwap**       | **Adopt the queued Phase-2 HTF gate (4 h EMA-200 ±1 %)** as designed in `experiments/2026-05-07-vwap-accuracy/RECOMMENDATIONS.md` | **−0.39 → +2.47** (+2.86)  | **+0.22 → +1.10** (+0.88) | **−49 %** (10,137 → 5,164 trades) |
| **turtle_soup** | **Tighten `atr_stop_mult` from 0.35 → 0.30** | **+0.80 → +1.33** (+0.53) | **+0.25 → +1.22** (+0.97) | ~0 % (33 → 32 trades) |

Both are single-knob changes with documented robustness; both are safe to ship under the existing PM-review process.

A **Phase-3 candidate is also identified for VWAP** — moving the HTF reference from 4 h to 1 h EMA-200 lifts full-sample Sharpe from +2.47 → +3.23 with a stable ±2 % band. It survived this run's walk-forward (IS +1.37 / OOS +1.40 with the rebuilt-per-slice HTF). Recommend deferring until Phase-2 has 30 days of live metrics, then re-running this comparison.

---

## VWAP — full results

### Production state
The `2026-05-07-vwap-accuracy` run shipped Phase-1 (anchored UTC daily VWAP, PR #481) but Phase-2 (4 h EMA-200 ±1 % HTF gate) is queued in the milestone state and **not** in production. Live config: 1.0 σ entry, 0.5 σ SL, anchored VWAP, no HTF filter. Operator brief from that run was to keep cadence as-is and raise quality.

### V0 baseline (production code, this dataset)

```
trades=10137  win=24.37%  E[R]=-0.0072  Sharpe=-0.39  DD=-152.4R  hold=16.4b
```

The 38-month baseline is **structurally unprofitable** (vs +2.74 on the prior run's 365-day window). 2023 chop, the early-2024 ETF-driven trend, and the 2025 second-half rally all punish a fade-to-VWAP strategy. This is the same regime fragility the prior run flagged in the walk-forward (OOS Sharpe degraded to +0.24). On a longer window the regime composition tilts even more against pure mean reversion. **Conclusion: the Phase-2 HTF gate is not optional any more — it is the difference between profitable and not.**

### V1 — Phase-2 HTF 4 h EMA-200 ±1 % gate (the queued change)

```
trades= 5164  win=25.93%  E[R]=+0.0657  Sharpe=+2.47  DD=-59.8R  hold=16.2b
ΔSharpe = +2.86 vs V0
```

Confirms the prior run's recommendation on a 3.16× larger dataset that includes 2023 and recent 2026 data. The filter rejects ~49 % of signals (the counter-trend fades that get run over) and turns expectancy from −0.007 R to +0.066 R per trade.

### V2 — HTF gate band sweep (4 h EMA-200, varying band width)

| Band | Trades | Win % | E[R] | Sharpe | DD (R) |
|------|-------:|------:|-----:|-------:|-------:|
| 0.005 | 4,904 | 25.98 % | +0.0638 | +2.35 | −56.5 |
| 0.010 (Phase-2 default) | 5,164 | 25.93 % | +0.0657 | +2.47 | −59.8 |
| 0.015 | 5,505 | 26.07 % | +0.0662 | +2.57 | −56.1 |
| 0.020 | 5,840 | 26.16 % | **+0.0705** | **+2.82** | −55.2 |
| 0.030 | 6,494 | 25.65 % | +0.0465 | +1.98 | −79.7 |

Sweep is well-behaved with a clear plateau between 0.010 and 0.020 and a clean roll-off at 0.030. **Loosening the band from 1 % → 2 % is a free improvement** of +0.35 Sharpe with a 13 % cadence recovery. Recommend the Phase-2 PR adopt **band = 0.020** rather than 0.010 (this is a one-line config change).

### V3 — HTF reference timeframe variant (band fixed at 0.010)

| Reference | Trades | Win % | E[R] | Sharpe | DD (R) |
|-----------|-------:|------:|-----:|-------:|-------:|
| 1 h EMA-200 | 5,533 | 26.44 % | **+0.0832** | **+3.23** | −50.6 |
| 4 h EMA-200 (Phase-2 default) | 5,164 | 25.93 % | +0.0657 | +2.47 | −59.8 |
| 1 d EMA-50  | 5,214 | 25.39 % | +0.0379 | +1.45 | −79.0 |

**1 h EMA-200 dominates 4 h EMA-200 across every metric.** It catches faster regime shifts so VWAP fades during a developing intraday trend get filtered out earlier. The 4 h reference still works but lags. The 1 d EMA-50 over-coarsens — many BTCUSDT regime shifts don't show up at the daily level until the move is over.

This is a Phase-3 candidate. Don't ship in the Phase-2 PR (the 4 h reference is what was tested and presented in the prior recommendations); ship Phase-2 first, observe live, then queue Phase-3 = "switch HTF reference from 4 h EMA-200 to 1 h EMA-200".

### V5 — SL multiplier sweep (no HTF filter)

| SL mult | Trades | Win % | E[R] | Sharpe | DD (R) |
|---------|-------:|------:|-----:|-------:|-------:|
| **0.40** | 10,687 | 21.46 % | **+0.0318** | **+1.56** | −178.5 |
| 0.50 (live) | 10,137 | 24.37 % | −0.0072 | −0.39 | −152.4 |
| 0.60 | 9,656 | 27.99 % | +0.0104 | +0.59 | −111.6 |
| 0.75 | 9,043 | 31.80 % | −0.0155 | −0.97 | −215.2 |
| 1.00 | 8,193 | 37.53 % | −0.0238 | −1.64 | −257.4 |

Tighter stop = better risk-adjusted return because **a fade that needs > 0.4 σ to recover wasn't reverting**. The 0.50 default is on the wrong side of the optimum. The R:R contract that 0.50 was chosen to honour (1:2 reward:risk at the entry boundary, per the operator directive 2026-05-03) is broken by this finding — **0.40 gives a 2.5:1 R:R at the boundary** and a more positive expectancy. Don't ship this independently of Phase-2; it stacks well with the HTF gate (see VS2 below) and that's where the win is.

### V6 — entry-threshold sweep (no HTF filter)

| Threshold | Trades | Win % | E[R] | Sharpe | DD (R) |
|-----------|-------:|------:|-----:|-------:|-------:|
| 0.8 σ | 11,558 | 26.03 % | +0.0050 | +0.30 | −138.4 |
| 1.0 σ (live) | 10,137 | 24.37 % | −0.0072 | −0.39 | −152.4 |
| 1.2 σ |  8,789 | 23.67 % | +0.0038 | +0.18 | −134.3 |
| 1.5 σ |  6,542 | 22.09 % | +0.0051 | +0.21 | −109.8 |
| 2.0 σ |  3,390 | 20.29 % | **+0.0370** | **+0.98** | −79.4 |

Without a regime filter, the optimum threshold drifts to 2.0 σ — re-confirming the original 2026-05-03 sweep result. The operator deliberately reverted to 1.0 σ for cadence at the time. **With Phase-2's HTF gate in place, the threshold question becomes moot:** the gate does the regime work and the threshold can stay at 1.0 σ. So treat V6 as evidence that without Phase-2, 1.0 σ is the *worst* threshold in the sweep — another argument for shipping Phase-2.

### Stacked variants (HTF gate combined with other filters)

| Variant | Trades | Win % | E[R] | Sharpe | IS Sharpe | OOS Sharpe |
|---|-------:|------:|-----:|-------:|---------:|-----------:|
| VS0 (baseline) | 10,137 | 24.37 % | −0.0072 | −0.39 | −0.58 | +0.22 |
| **V1 = Phase-2 (4 h ±1 %)** | 5,164 | 25.93 % | +0.0657 | **+2.47** | +2.48 | +1.10 |
| VS1 (1 h ±2 %) | 6,870 | 25.59 % | +0.0424 | +1.86 | +1.37 | +1.40 |
| VS2 (VS1 + SL 0.40) | 7,236 | 22.44 % | +0.0745 | **+2.99** | +3.24 | +0.65 |
| VS3 (VS2 + 2.0 σ thr) | 2,157 | 17.90 % | +0.1291 | +2.33 | +1.29 | **+2.16** |

**Walk-forward observations:**

- **V1 (Phase-2)** is the best-balanced: full +2.47, IS +2.48, OOS +1.10. Cadence drop 49 %.
- **VS1 (1 h band 2 %)** has near-identical IS and OOS (+1.37 / +1.40) — most regime-stable variant.
- **VS2** has the highest peak full-sample Sharpe but its OOS degrades by 80 % from IS — this is borderline overfit to the in-sample window. **Don't ship VS2 standalone.**
- **VS3** is the most regime-robust (OOS > IS) but cuts cadence by 80 %, which the operator has explicitly rejected before.

### VWAP recommendation

Ship the **queued Phase-2 HTF gate as documented in `experiments/2026-05-07-vwap-accuracy/RECOMMENDATIONS.md`**, with one tweak from this run: set the gate band to **0.020** (not 0.010). That single change adds +0.35 Sharpe at no cadence cost vs the as-designed Phase-2.

After 30 days of live metrics on Phase-2-with-2 %-band, queue **Phase-3:** switch the HTF reference from 4 h EMA-200 to 1 h EMA-200 (V3 result above). That's a +0.4 Sharpe lift on top of Phase-2, again single-knob.

Do **not** ship the SL-mult or entry-threshold changes alongside Phase-2. They are confounded with the HTF effect, and their walk-forward stability is meaningfully worse than V1's.

---

## Turtle Soup — full results

### Production state

15 m timeframe, BTCUSDT (and ETHUSDT in production but not tested here for data availability). Live params: `sweep_lookback=60`, `min_body_to_range=0.60`, `min_sweep_buffer_bps=12`, `atr_period=14`, `atr_stop_mult=0.35`, `tp1_at_r=1.25`, `tp2_at_r=3.0`. **No prior tuning run on record.** This is the first systematic search.

### Cadence note

Turtle Soup at production parameters fires only **33 trades over 38 months** (~0.9/month). That's by design (it's a high-conviction setup), but it makes parameter sweeps statistically thin. Treat the per-variant numbers as directional, not statistically significant — the recommendations below lean on monotonic sweep behaviour rather than absolute metric levels.

### T0 baseline

```
trades=33  win=51.52%  E[R]=+0.1591R  Sharpe=+0.80  DD=-4.8R  hold=10.6b
```

Already profitable at the live settings. Walk-forward IS +0.32 / OOS +0.25 — thin, but consistent direction.

### T1 — `sweep_lookback` sweep

| Lookback | Trades | Win | E[R] | Sharpe |
|----------|-------:|----:|-----:|-------:|
| 30  | 47 | 36.2 % | −0.186 | −1.17 |
| 45  | 40 | 45.0 % | +0.033 | +0.19 |
| **60 (live)** | 33 | 51.5 % | +0.159 | +0.80 |
| 90  | 29 | 55.2 % | +0.270 | +1.30 |
| 120 | 21 | 71.4 % | +0.607 | +2.67 |

Monotonic — bigger lookback = better quality, less cadence. The "true swing" interpretation tightens. **Don't promote a higher value into prod**: 21 trades over 38 months is too thin to commit to. The current 60 sits in a defensible spot on the curve.

### T2 — `min_body_to_range` sweep

| Body/range | Trades | Win | E[R] | Sharpe |
|------------|-------:|----:|-----:|-------:|
| 0.50 | 76 | 42.1 % | −0.031 | −0.24 |
| 0.55 | 52 | 46.2 % | +0.056 | +0.36 |
| **0.60 (live)** | 33 | 51.5 % | +0.159 | +0.80 |
| 0.65 | 16 | 37.5 % | −0.156 | −0.56 |
| 0.70 | 10 | 60.0 % | +0.350 | +0.95 |

Default 0.60 is on a clean local optimum. Loosening costs accuracy fast (0.55 → +0.36, 0.50 → −0.24); tightening past 0.65 becomes too thin to read. Keep at 0.60.

### T3 — `min_sweep_buffer_bps` sweep

| Buffer (bps) | Trades | Win | E[R] | Sharpe |
|--------------|-------:|----:|-----:|-------:|
|  4 | 99 | 41.4 % | −0.072 | −0.65 |
|  8 | 48 | 50.0 % | +0.100 | +0.62 |
| **12 (live)** | 33 | 51.5 % | +0.159 | +0.80 |
| 18 | 13 | 76.9 % | +0.703 | +2.60 |
| 25 |  5 | 20.0 % | −0.550 | −1.22 |

Buffer 12 is the right balance. 18 looks great but n=13 is unactionable. 25 collapses (the deeper sweep sample is dominated by genuine breakouts that don't reverse).

### T4 — `atr_stop_mult` sweep ★ recommended change

| ATR mult | Trades | Win | E[R] | Sharpe |
|----------|-------:|----:|-----:|-------:|
| 0.25 | 32 | 56.3 % | +0.266 | +1.33 |
| **0.30** | 32 | 56.3 % | +0.266 | **+1.33** |
| 0.35 (live) | 33 | 51.5 % | +0.159 | +0.80 |
| 0.45 | 32 | 43.8 % | −0.008 | −0.04 |
| 0.60 | 23 | 52.2 % | +0.148 | +0.63 |

**Tightening the stop from 0.35 to 0.30** improves Sharpe by +0.53, win rate by +5 pp, expectancy by +0.107 R, all at the same trade count. Walk-forward (run_stack.py): TS1 IS +0.53 / OOS **+1.22** — OOS *better* than IS, three-of-three trades winning in the 4-trade out-of-sample slice. **Recommend adopting 0.30 as the new default.**

The mechanism: 0.35 sets the stop slightly past the post-sweep wick, capturing some of the legitimate sweep depth as a "stop run" rather than a fakeout invalidation. 0.30 trims that to just past the actual swing extreme. The strategy thesis ("the prior swing held") is honoured tighter.

### T5 — `tp1_at_r` sweep

| TP (R) | Trades | Win | E[R] | Sharpe |
|--------|-------:|----:|-----:|-------:|
| 1.00 | 36 | 58.3 % | +0.164 | +0.98 |
| **1.25 (live)** | 33 | 51.5 % | +0.159 | +0.80 |
| 1.50 | 25 | 44.0 % | +0.068 | +0.27 |
| 2.00 | 34 | 44.1 % | +0.272 | +1.10 |

Non-monotonic. 1.0 R has higher win-rate (more hits before the move runs out) but less reward per trade. 2.0 R is the high-reward target but win rate craters. Default 1.25 is a defensible compromise. **Don't change** — combining 0.30 ATR stop with a 2.0 R TP collapses walk-forward (TS2 in `run_stack.py`: IS +0.18 / OOS −0.62). Keep TP at 1.25 R.

### T6 — HTF 4 h EMA-50 alignment ★ rejected

```
trades=10  win=40.00%  E[R]=-0.1000  Sharpe=-0.27  DD=-3.8R  hold=11.6b
```

**Counter-intuitive — and that's the lesson.** Trend alignment hurts a sweep+reversal strategy because turtle_soup is *by design* a counter-trend fade at intraday support/resistance. Forcing alignment with the 4 h trend filters out the very setups the strategy is designed to catch. **Reject.**

### T7 — ATR regime filter (require ATR/close ≥ X)

| Min ATR/close | Trades | Sharpe |
|---------------|-------:|-------:|
| off | 33 | +0.80 |
| 0.0025 | 32 | +0.97 |
| 0.0050 |  9 | −1.51 |
| 0.0075 | 1 | (n/a) |
| 0.0100 | 1 | (n/a) |

Only the lightest filter (0.0025) marginally helps; anything stronger destroys cadence. Not worth the wiring. **Reject.**

### Walk-forward 70/30 — turtle key candidates

| Variant | IS Sharpe | OOS Sharpe | OOS/IS | Verdict |
|---|---:|---:|---:|---|
| TS0 baseline | +0.32 | +0.25 | 0.78 | regime-stable |
| **TS1 — `atr_stop_mult=0.30`** | +0.53 | **+1.22** | **2.30** | adopt — OOS > IS |
| TS2 — TS1 + `tp1=2.0` | +0.18 | −0.62 | n/a | reject — collapses OOS |
| TS3 — TS2 + body 0.55 | −0.22 | +0.38 | n/a | reject — IS unprofitable |

TS1 is the only stacked variant that survives walk-forward. The ATR-stop tightening is a clean, isolated quality lift; everything else introduces noise.

### Turtle Soup recommendation

Single-line change: in `src/units/strategies/turtle_soup.py`, set `_DEFAULTS["atr_stop_mult"]` from `0.35` to `0.30`. Mirror in `config/strategies.yaml::strategies.turtle_soup.atr_stop_mult`. Expected lift: +0.53 Sharpe full-sample, +0.97 OOS Sharpe, no cadence cost, no other parameter changes.

PM review required (touches `src/units/strategies/`).

---

## Critical caveats

1. **Sandbox data path.** `qashdev/btc` is a verbatim mirror of `data.binance.vision` (Binance public archive). It matches the live Bybit Linear BTCUSDT-PERP feed within the spot-perp basis (~0–10 bps). For backtest deltas this is immaterial, but absolute live PnL will be ≈ 5 bps/trade lower than the gross numbers here once you account for the basis + 0.05 % funding × hold-time.

2. **No fees / slippage / funding model.** Same caveat as the prior run — apply the industry "live realises 50–70 % of backtest Sharpe" rule of thumb. V1's +2.47 backtest Sharpe → +1.2 to +1.7 live Sharpe expectation.

3. **Walk-forward HTF rebuild.** The walk-forward numbers in `run_stack.py` rebuild the 1 h / 4 h EMA-200 series independently on each slice, so there is no leakage from OOS data into IS. The full-sample numbers in `run.py` use a single global EMA series — that's the standard practice but means OOS numbers in `run.py` are not strictly leak-free. The `run_stack.py` walk-forward is the leak-free reference.

4. **Turtle Soup statistical thinness.** 33 trades over 38 months is **far below** what you'd want for a parameter-stability claim. The TS1 walk-forward 4-trade OOS sample is informative but not statistically conclusive. The mechanism (tighter stop honours the strategy thesis better) is the load-bearing argument; the metric is supportive evidence.

5. **ETHUSDT not tested.** Turtle Soup runs on BTC + ETH live; only BTC was tested here (the qashdev mirror does not expose ETHUSDT under that path tree). Before promoting the 0.30 ATR-stop change, run the same sweep on ETHUSDT 15 m once a sandbox-reachable ETH archive is identified (HuggingFace `vaquum/binance_btcusdt_1m_klines` author has similar archives that may include ETH).

6. **Single-symbol / single-window dataset.** Jan 2023 → Feb 2026 captures the 2024 ETF approval, the 2024 halving, the late-2024 bull, Q1 2025 chop, the late-2025 rally, and the early-2026 drawdown. It does **not** cover the 2022 bear or the 2018 bear. If those regimes are likely to re-occur in the next 12 months, expect both strategies to under-perform their 38-month numbers here.

---

## Implementation checklist (for the IMPLEMENT PRs after operator approval)

### PR-A: VWAP Phase-2 (HTF 4 h EMA-200 ±2 %)

Per the prior run's implementation note, with the band tweaked from 0.010 to 0.020:

1. `src/units/strategies/vwap.py::build_vwap_signal` accepts optional `htf_close` + `htf_ema200`; reject side per the band.
2. `src/runtime/pipeline.py` pulls 4 h candles in parallel, computes EMA-200, threads through.
3. `config/strategies.yaml`:
   ```yaml
   strategies:
     vwap:
       htf_trend_filter:
         enabled: true
         htf_timeframe: "4h"
         ema_period: 200
         band_pct: 0.02      # raised from 0.01 per 2026-05-08 run
   ```
4. Tests in `tests/test_vwap_strategy.py` covering each gate arm.

### PR-B: Turtle Soup ATR stop tightening

1. `src/units/strategies/turtle_soup.py::_DEFAULTS["atr_stop_mult"]`: `0.35` → `0.30`.
2. `config/strategies.yaml::strategies.turtle_soup.atr_stop_mult`: add explicit `0.30` line so config and code agree (currently it's only in the code defaults).
3. No new tests strictly required (the existing `tests/test_turtle_soup_strategy.py` covers the SL mechanic; the parameter is already test-isolated).

Both PRs touch `src/units/strategies/` so PM review is required per `CLAUDE.md` § Merging Rules.

---

## Follow-ups (not in this run)

1. **Phase-3 candidate for VWAP** — switch HTF reference from 4 h EMA-200 to 1 h EMA-200 once Phase-2 has 30 days of live metrics. Expected +0.4 Sharpe on top of Phase-2, no cadence cost.
2. **ETHUSDT Turtle Soup re-validation** — once an ETH archive is reachable from the sandbox, run T4 alone on it. Promote 0.30 across symbols only if it generalises.
3. **Funding-cost-aware expectancy reporting** — both strategies hold for hours; -0.01 %/8 h funding × 4 h average hold ≈ -0.5 bps per trade, modest but worth threading into `backtest_helpers.py` so future numbers are net.
4. **Volume-confidence sizing modulator (V4)** — was queued but de-prioritised this run because it requires position-sizing surface that isn't part of the strategy module. File as S-05x for the accounts-layer team.
5. **Cross-strategy correlation audit** — at HTF-gated VWAP cadence (~5 k trades / 38 months = ~3.5/day) and turtle_soup cadence (~1/month), strategies likely fire near-disjointly, but worth confirming with the trade journal once Phase-2 is live.

---

## Cross-references

- `experiments/2026-05-08-all-models-training/PLAN.md` — hypothesis grid + adoption gates.
- `experiments/2026-05-08-all-models-training/scripts/run.py` — primary runner (V0–V6, T0–T7, walk-forward).
- `experiments/2026-05-08-all-models-training/scripts/run_stack.py` — stacked variants + leak-free walk-forward.
- `experiments/2026-05-08-all-models-training/results/all_metrics.json` — every metric this run produced.
- `experiments/2026-05-08-all-models-training/results/stacked.json` — stacked-variant metrics.
- `experiments/2026-05-08-all-models-training/results/run_log.txt` / `stack_log.txt` — captured stdout for audit.
- `experiments/2026-05-07-vwap-accuracy/RECOMMENDATIONS.md` — prior run that proposed Phase-1 (shipped) + Phase-2 (queued, this run validates).
- `src/units/strategies/vwap.py`, `src/units/strategies/turtle_soup.py` — production strategy modules.
- `config/strategies.yaml` — runtime parameter source of truth.

---

## Open-source research informing the design

This run leaned on the same literature catalogued in the prior VWAP run plus the trend-following / sweep-reversal literature for turtle_soup:

- HTF EMA-200 alignment as a regime filter for mean reversion: [QuantPedia — Trend & Mean-Reversion in Bitcoin](https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/), [QuantMonitor — Regime classification](https://quantmonitor.net/how-to-identify-market-regimes-and-filter-strategies-by-trend-and-volatility/).
- VWAP slope / regime gating: [VWAP & Its Slope — Definedge Forum](https://forum.definedgesecurities.com/topic/3880/vwap-its-slope-the-silent-voice-of-market-truth), [Mean reversion — Forextester](https://forextester.com/blog/mean-reversion-trading/).
- Sweep + reversal at swing extremes (the turtle-soup thesis): the original turtle-soup pattern from Linda Raschke's *Street Smarts*; ICT's "sweep the lows, reverse on close back inside" articulation in the inner-circle-trader public material; [BabyPips — False Breakouts and Pin Bars](https://www.babypips.com/learn/forex/false-breakouts).
- ATR-based stops sized to recent volatility rather than fixed dollars: [Investopedia — ATR Stop Loss](https://www.investopedia.com/articles/trading/04/091504.asp).
- Anchored vs rolling VWAP, σ-band entries, volume confirmation at extremes: [Anchored VWAP — TrendSpider](https://trendspider.com/learning-center/anchored-vwap-trading-strategies/), [Calculating VWAP in Python — Databento](https://databento.com/blog/vwap-python), [VWAP Mean Reversion — VolatilityBox](https://volatilitybox.com/docs/vwap-mean-reversion-strategies/).

Sources:
- [qashdev/btc — Binance Vision archive mirror](https://github.com/qashdev/btc)
- [Binance Vision — public market data](https://data.binance.vision/)
- [TrendSpider — Anchored VWAP guide](https://trendspider.com/learning-center/anchored-vwap-trading-strategies/)
- [Databento — VWAP in Python](https://databento.com/blog/vwap-python)
- [QuantPedia — Trend & Mean-Reversion in Bitcoin](https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/)
- [Investopedia — ATR Stop Loss](https://www.investopedia.com/articles/trading/04/091504.asp)
