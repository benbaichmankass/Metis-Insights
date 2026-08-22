# E3.5 — the take-profit is not a decision, it is the venue's rejection threshold

**Date:** 2026-08-20 · **Tier-1, observe-only.** Nothing here changes a live
parameter; every bracket parameter in `config/strategies.yaml` is **Tier-3**.

**Tools (committed, each with a self-test):**
`scripts/research/e35_barrier_race.py` (39/39) ·
`scripts/research/e35_bracket_geometry_sweep.py` (44/44).

**POPULATION for every number below unless stated otherwise: 6,428 trades across
19 legs** — every donchian / pullback / squeeze leg with reachable candle data,
config-exact base args (resolved through `m20_fleet_exit_sweep.base_args`, imported
not restated), full available history **2021-08-16 → 2026-08-19**, **net of fees**
(`execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP` = 7.5 bps round-trip, charged by the
harness). 16 of the 19 are `execution: live`; `avax_pullback_2h` and
`trend_donchian_1h` are `shadow`.

**Data provenance:** `data.binance.vision` USDⓈ-M futures klines fetched this
session via `scripts/ops/fetch_backtest_candles.py --source binance_vision --days
1830` (BTC/ETH/SOL/XRP/ADA/AVAX at 1h/2h/4h). ⚠️ **SOL, XRP, ADA and AVAX are each
missing 5 calendar days** (2022-02-26→28 and 2022-04-01→02) — an upstream archive
publication gap, 0.27% of the span. BTC and ETH have zero gaps.

---

## 1. The finding

**On 16 of 19 legs the take-profit that gets placed is the exchange's rejection
threshold.** The unit computes

```
tp = min(entry * (1 + 0.099),  entry + tp_r * risk)
```

and 16 legs declare `tp_r: 50.0` — the "no R target" sentinel. The three `_prop`
legs declare `6.0`. So on 16 of 19 the **9.9% venue clamp always binds**, and the
take-profit level is not a decision anyone made about the trade.

This is the operator's *"brackets that aren't supposed to be hit"* stated as
config. On `trend_donchian` (BTC 1h, 339 trades) the take-profit is hit **15 times
— 4.4%** — at a median distance of **5.98 R** and a maximum of **40.89 R**.

---

## 2. It is worse than arbitrary: `tp_R` is volatility, exactly

Because the clamp is a fixed fraction of **price** while R is a varying fraction of
price, the distance to the placed take-profit, in R, is

```
tp_R = cap / (risk/entry)        with   risk = atr_stop_mult * ATR
     = cap / (atr_stop_mult * ATR/close)
```

so **`tp_R` and `ATR/close` are the same variable** up to the constant
`cap / atr_stop_mult`. `e35_barrier_race.collinearity_check` asserts this against
the data rather than arguing it from the formula: **`confirmed` on 19 of 19 legs**,
worst absolute deviation **2.78 × 10⁻¹⁷** (float epsilon).

Two consequences:

1. **The venue clamp silently imports a volatility filter into the exit policy that
   nobody designed.** At `atr_stop_mult: 2.5` and `cap: 0.099`, `tp_R = 1` is
   *exactly* `ATR/close = 3.96%`, and `tp_R < 0.75` is *exactly* `ATR/close > 5.28%`.
2. **The bracket is not a leg property.** `tp_R` varies **6.5× to 38.9× within every
   single leg** (min → max):

| leg | exec | declared `tp_r` | n | `tp_R` min | median | max | spread | `tp_R`<1 |
|---|---|---|---|---|---|---|---|---|
| `trend_donchian` | live | 50.0 | 339 | 1.447 | 5.977 | 40.891 | **28.3×** | 0 |
| `trend_donchian_eth` | live | 50.0 | 585 | 0.804 | 4.069 | 31.240 | **38.9×** | 3 |
| `htf_pullback_trend_2h` | live | 50.0 | 412 | 0.790 | 3.606 | 27.212 | **34.5×** | 5 |
| `trend_donchian_1h` | shadow | 50.0 | 771 | 0.967 | 5.376 | 32.391 | **33.5×** | 1 |
| `sol_pullback_2h` | live | 50.0 | 241 | 0.225 | 1.711 | 7.695 | **34.1×** | 35 |
| `eth_pullback_2h` | live | 50.0 | 344 | 0.553 | 2.737 | 13.741 | 24.9× | 13 |
| `avax_pullback_2h` | shadow | 50.0 | 377 | 0.328 | 1.814 | 7.402 | 22.6× | 56 |
| `xrp_pullback_2h` | live | 50.0 | 300 | 0.422 | 2.137 | 8.466 | 20.1× | 32 |
| `ada_pullback_2h` | live | 50.0 | 228 | 0.388 | 1.828 | 6.053 | 15.6× | 28 |
| `trend_donchian_sol_4h` | live | 50.0 | 190 | 0.305 | 1.405 | 4.228 | 13.9× | **38 (20.0%)** |
| `trend_donchian_eth_4h` | live | 50.0 | 184 | 0.735 | 1.956 | 8.172 | 11.1× | 4 |
| `trend_donchian_sol` | live | 50.0 | 298 | 0.781 | 3.263 | 8.940 | 11.4× | 3 |
| `squeeze_breakout_4h` | live | 50.0 | 106 | 1.205 | 3.065 | 12.196 | 10.1× | 0 |
| `trend_donchian_xrp_4h` | live | 50.0 | 131 | 0.567 | 2.075 | 4.434 | 7.8× | 5 |
| `trend_donchian_avax_4h` | live | 50.0 | 204 | 0.544 | 1.512 | 3.852 | 7.1× | 33 |
| `trend_donchian_ada_4h` | live | 50.0 | 186 | 0.537 | 1.576 | 3.511 | 6.5× | 21 |
| `eth_pullback_prop_2h` | live | **6.0** | 321 | 0.553 | 2.674 | 6.000 | 10.9× | 13 |
| `trend_donchian_eth_prop` | live | **6.0** | 892 | 0.804 | 4.339 | 6.000 | 7.5× | 3 |
| `trend_donchian_sol_prop` | live | **6.0** | 319 | 0.781 | 3.223 | 6.000 | 7.7× | 3 |

**Cross-check:** these medians are reproduced **exactly — 18 of 18, worst
|difference| 0.0** — against `tp_r_effective_median`, which the harnesses compute
independently per entry. ⚠️ `backtest_squeeze.py` is the 19th and emits no such
field while `backtest_trend.py` and `backtest_pullback.py` both do — the same
"metric present in one harness, absent in its sibling" shape as the `mfe_r` and
`capital_efficiency` gaps. Filed as
`BL-20260820-SQUEEZE-HARNESS-EMITS-NO-TP-R-EFFECTIVE`.

---

## 3. The barrier race

For each trade, which bracket was reached first, bucketed by the entry-time `tp_R`.
`take_profit` and `stop` are the entry-time brackets; **`trail_stop` is counted as
`other`, deliberately** — a stop that MOVED is a managed exit, and booking it as
`stop` would attribute a managed exit to the entry-time bracket.

| `tp_R` bucket | n | % pop | P(TP first) | P(stop) | P(other) | net_R | net_R/trade |
|---|---|---|---|---|---|---|---|
| `0–0.75` | 106 | 1.6% | **49.1%** | 32.1% | 18.9% | **−17.81** | **−0.168** |
| `0.75–1.25` | 442 | 6.9% | 45.2% | 34.4% | 20.4% | +3.70 | +0.008 |
| `1.25–2` | 1108 | 17.2% | 34.5% | 32.6% | 32.9% | +70.89 | +0.064 |
| `2–3` | 1371 | 21.3% | 18.9% | 33.6% | 47.5% | −5.04 | −0.004 |
| `3–5` | 1919 | 29.9% | 10.1% | 37.7% | 52.2% | +12.91 | +0.007 |
| `>5` | 1482 | 23.1% | **3.2%** | 39.1% | 57.7% | **+206.91** | **+0.140** |

Three readings:

**(a) P(TP first) is strongly monotone in `tp_R`** — 49.1% → 45.2% → 34.5% → 18.9%
→ 10.1% → 3.2% — which is the positive control that the measurement is sane;
geometry says it must be.

**(b) P(stop) is nearly flat** — 32.1 / 34.4 / 32.6 / 33.6 / 37.7 / 39.1%. The stop
is 2.5 ATR in every bucket, so its hit rate barely moves. **The race is decided
almost entirely by where the take-profit is, and the take-profit is set by the
venue.**

**(c) 76.2% of the fleet's net R (206.91 of 271.56) comes from the 23.1% of trades
whose take-profit is more than 5 R away** — i.e. from trades the bracket cannot
close, exited by trail / stop / timeout. This is § 0.1 of the PROCESS doc
(*"a bracket isn't an exit strategy, it's a safeguard"*) stated as a share of P&L.

### The race-inverted population

`tp_R < 0.75` is where the take-profit sits **nearer than the stop** — the race
inverts, and the trade wins it about half the time for less than 0.75 R while
losing 1 R when it doesn't.

| leg | n | net_R | net_R/trade | P(TP first) |
|---|---|---|---|---|
| `trend_donchian_avax_4h` | 11 | −6.70 | −0.609 | 18.2% |
| `xrp_pullback_2h` | 14 | −3.77 | −0.270 | 42.9% |
| `sol_pullback_2h` | 19 | −3.27 | −0.172 | 52.6% |
| `ada_pullback_2h` | 14 | −2.40 | −0.172 | 50.0% |
| `trend_donchian_ada_4h` | 5 | −1.77 | −0.355 | 40.0% |
| `trend_donchian_sol_4h` | 9 | −1.59 | −0.177 | 55.6% |
| `trend_donchian_eth_4h` | 1 | −1.01 | −1.012 | 0.0% |
| `trend_donchian_xrp_4h` | 2 | −0.39 | −0.193 | 50.0% |
| `avax_pullback_2h` | 21 | +0.73 | +0.035 | 61.9% |
| `eth_pullback_2h` | 5 | +1.08 | +0.217 | 60.0% |
| `eth_pullback_prop_2h` | 5 | +1.28 | +0.257 | 60.0% |
| **POOLED** | **106** | **−17.81** | **−0.168** | **49.1%** |

**8 of the 11 legs that have any such trade are net-negative in it.**

⚠️ **STATE THE LIMITS.** n = 106 pooled, **1.6% of the population**, and per-leg n
runs 1–21 — **no single leg supports a claim here**, and the three positive legs
are all n ≤ 21. The direction is consistent (8 of 11) and the magnitude is
strikingly similar across the four largest negative legs (−0.172, −0.172, −0.177,
−0.270), but this is a pooled signal at small n, not a per-leg result.

---

## 4. What this does NOT establish — the refusal

**This axis cannot distinguish "the bracket geometry is wrong" from "the strategy
loses money in high volatility".** Per § 2 they are the same variable: `tp_R < 0.75`
*is* `ATR/close > 5.28%`. At a fixed `atr_stop_mult` the two are not separable on
any amount of this data, and any claim naming one cause over the other is
unsupported. `e35_barrier_race.py` refuses to make it.

Separating them requires `atr_stop_mult` to **vary**, which is what
`e35_bracket_geometry_sweep.py` does (a `(tp_r × atr_stop_mult × timeout_bars)`
grid through the existing m20 fold structure and Path A/B gate). That run was
in flight when this document was written; its result is a **separate** finding and
must not be back-read into this one.

⚠️ **A second reason the stop axis needs care:** moving `atr_stop_mult` moves R
itself, so `net_R` is not unit-comparable across it. An earlier version of the
sweep tool computed a fixed-notional companion and **refused** any stop-axis cell
where the two disagreed in sign. **That rule was wrong and was removed** —
`RiskManager.position_size` sizes by risk (`qty = risk_budget / risk`), so `net_R`
*is* the unit the account experiences. Measured on `ada_pullback_2h` (228 trades),
the disagreements were all in the *desirable* direction:

| `atr_stop_mult` | net_R | mean risk/entry | notional per unit risk-budget |
|---|---|---|---|
| 1.5 | −8.63 | 0.0375 | 26.67 |
| 2.0 | −9.17 | 0.0503 | 19.89 |
| **2.5 (live)** | **+14.98** | 0.0630 | 15.89 |
| 3.0 | **+17.23** | 0.0760 | **13.16** |
| 3.5 | +14.70 | 0.0884 | 11.31 |

`sm3` beats base in R **and** needs less notional per unit of risk budget; the old
rule refused it. What the notional figure is genuinely for is **leverage** — a
tighter stop buys its R with more of it, and the margin pre-flight cap really does
bind (`bybit_2`'s 110007 refusals). Every stop-axis cell now carries
`leverage_multiple`, **reported and never gated** (a ceiling with no measured
distribution behind it is what `gross-exposure-governance-DESIGN.md` §§ 6–7
refuses).

---

## 5. What follows

The operator's reframe (2026-08-20, recorded in
`docs/design/exit-mechanism-construction-PROCESS.md` § E3.6) is the consequence:

> *"the brackets should be predictive … when we enter a trade, we should know what
> the expectation is for when it's gonna exit … we're really not just looking for
> exit points anymore. We're looking for how to correctly manage trades actively."*

§§ 1–3 are the measurement that makes that concrete. The current take-profit
carries **no expectation at all** — it is a venue constant divided by realised
volatility — and the fleet's P&L comes overwhelmingly from trades where it is
unreachable.

**Open, in order:**

1. **The bracket-geometry sweep** (in flight) — the static optimum, net of fees,
   with its `m20_split_dispersion` band. Also the only way to separate § 4's
   confound.
2. **The conditional race** — P(sl first | state at bar *t*), the mid-trade half of
   the operator's question. Decision-time-only by construction, so it does not
   inherit the E3 stratification problem. **This is the natural target for the
   exit-head ML rig** (`analyze_exit_head.py`), which today trains on `label_hold`
   — the label `e3-barrier-geometry-2026-08-20.md` showed is up to 46.6% barrier
   composition. Pointing the rig at the race is a new model, not a re-run.
3. **A predictive bracket** — an entry-time expectation of where and when the trade
   should exit, revisable upward when the trade outperforms. Note that **every
   lever screened to date can only cut a trade short**; "extend the target" has no
   implementation anywhere in the harness or the live monitor.

**Cost constraint, unchanged and binding** (`e3-joint-lever-screen-2026-08-20.md`):
a round trip costs **0.082–0.163 R** against a fee-free mean edge of **+0.1376 R**
(XRP, 503 trades) / **+0.1167 R** (SOL, 567). Amending a *resting* level is free;
crossing the spread to exit early is not. So bracket adjustment can be frequent and
"drop the trade" must be rare and decisive.
