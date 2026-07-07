# IB intraday-strategy survey — MES / MGC / MHG (2026-07-07)

**Author:** Claude (IBKR-pipeline investigation, step 3 — "research higher-frequency
IB strategies"). Read-only survey of the strategy corpus + research evidence.
**Status:** research finding + a test matrix to run on native data. **No live wiring** here
(any survivor is Tier-3, operator-gated, `ib_paper`-first).

## Why this exists

The `ib_paper` account (MES S&P-micro, MGC gold-micro, MHG copper-micro) is "too
quiet": 3 of its 4 strategies are **daily** cadence (`mes_trend_long_1d`,
`mgc_pullback_1d`, `mhg_pullback_1d`) and the only intraday cell (`mgc_trend_1h`) is
`execution: shadow` because it is net-negative on gold 1h. The operator asked whether a
**higher-frequency** (intraday: 5m/15m/1h/4h) strategy could trade these instruments more
actively — **with a real edge, never activity for its own sake.**

## Bottom line (honest)

**No validated intraday edge exists on native IBKR MES/MGC/MHG data today.**

- The only intraday family ever tested on a *real* futures 1h series (`mgc_trend_1h`,
  `trend_donchian` @1h) went **−15.5R over 2.4y on real GC=F futures** and was correctly
  **demoted to shadow** (2026-06-18, Tier-3).
- The only native MES *intraday* test (ORB on native MES 5m, 86k bars) **failed its P5 gate
  at every config** and was shelved (`docs/research/P5-orb-mes-result-2026-06-26.md`).
- Copper has **no intraday data at all** — it cannot even be backtested without a fresh
  native pull.
- Every *positive* intraday number for these underlyings is a **spot (Dukascopy XAUUSD) or
  ETF (GLD/SPY/QQQ) proxy** — screening evidence, not promotion evidence. The one time a
  proxy positive was re-checked on the real instrument, it **flipped sign**.

So the account's low activity is, as of today, **structurally appropriate**: the validated
edges for these instruments are daily. Making it more active requires *finding* an intraday
edge on native data first — this note is the test matrix to do that, not a set of ready cells.

## The XAUUSD +78R vs MGC −15.5R contradiction — reconciled

The gold 1h trend cell looks great on proxy data and terrible on real futures. Not a single
cause — three stacked differences, and **the real-futures pull is the trustworthy one**:

| Source | Series | Window | Result |
|---|---|---|---|
| `docs/research/m15-phase0-results-2026-06-10.md:41` | Dukascopy **spot** XAUUSD 1h | OOS = 2025+ only | +78.4R train / +36.8R OOS |
| `config/strategies.yaml:1136` | Dukascopy **spot** gold 1h @1.5bps | 2019–24 / 2025–26 | +49.4R / +32.2R (flagged cost-sensitive ~−19R/bps) |
| `docs/research/recombination-sweep-2026-06-18.md:106` | **real GC=F futures** 1h (yfinance) | 2024-01…2026-06 (2.4y) | **−15.5R** (2024 −19.4, 2025 −11.9, 2026 +15.8) |
| same recombination re-pull | spot XAUUSD 1h | 2024+ | **−50.7R** |

1. **Instrument/vendor:** phase-0 used a spot *proxy*; the demotion used real futures — but
   the fresh re-pull of *spot itself* also went −50.7R, so vendor + period (not spot-vs-futures
   alone) flipped the sign.
2. **Window:** phase-0 OOS was **2025+ only**, missing 2024 (−19.4R, the dominant losing year).
3. **Screening vs promotion:** phase-0 labels itself a *screening pass on a proxy series*
   ("not the promotion evidence"); the real-futures pull is the promotion-grade verdict.

**Verdict: gold 1h *trend* on real futures is contradicted; treat the +78R as a proxy
mirage.** (This is why `mgc_trend_1h` is correctly `shadow` and needs no promotion backtest.)

## Per-instrument intraday evidence

- **MES (S&P):** no validated *intraday* edge on native data. Native MES 5m ORB failed P5.
  The positives are ETF proxies — SPY 5m `ict_scalp` +6.9/+4.6, SPY 15m `fvg_range` +7.4/+7.9,
  SPY/QQQ 1h `htf_pullback` live_ready (+42/+45R) — not MES.
- **MGC (gold):** 1h trend **contradicted** (above). Other cells are spot/ETF-proxy positive:
  `GLD pullback 1h` **live_ready +78.9/+61.5** (strongest gold-proxy intraday cell), XAUUSD 15m
  `ict_scalp` +39.4/+10.2, XAUUSD 4h trend +18/+20. All untested on real MGC futures.
- **MHG (copper):** **no intraday copper data exists anywhere** — the futures universe is
  daily-grade only. `mhg_pullback_1d` is +85R standalone but "lukewarm OOS." Un-backtestable
  intraday without a fresh native pull.

## Strategy-unit portability

Useful fact: **none of the `src/units/strategies/` units are crypto-specific** — every one is
a pure OHLCV `order_package(cfg, candles_df)` generator (no orderbook/funding/OI reads). So any
unit runs on an IBKR futures candle stream unchanged. The crypto-only edges (funding carry,
cross-sectional momentum) live in `scripts/backtest_*.py` harnesses, not in the unit corpus.
Intraday-capable units: `trend_donchian`, `htf_pullback_trend_2h`, `fvg_range_15m`, `ict_scalp`,
`turtle_soup`, `squeeze_breakout_4h`, `fade_breakout_4h` (+ research-only `hf_displacement_cont`,
`hf_vwap_revert`; `vwap` is KILLED). Their backtest harnesses (`backtest_{trend,pullback,
fvg_range,fade,squeeze}.py`) all take `--resample`, so one native 5m/15m pull sweeps 15m→4h.

## Test matrix — ranked intraday cells to backtest on NATIVE IBKR data

Ranked by proxy-evidence strength × data availability. **[tag]** = evidence class. "Data" =
what native pull each needs (✅ = already available for this session).

| # | cell (unit × instr × TF) | tag | data | rationale |
|---|---|---|---|---|
| 1 | `htf_pullback` × **MGC × 1h** | speculative, strongest proxy | ✅ MGC 1h (pulled) | GLD 1h pullback live_ready +78.9/+61.5; pullback is the gold edge that *held* daily (+56R) where trend was contradicted |
| 2 | `htf_pullback` × **MES × 1h** | speculative | ✅ MES 5m→resample 1h | SPY/QQQ 1h pullback both live_ready (+42/+45R) |
| 3 | `ict_scalp` × MGC × 15m | speculative | needs MGC 15m pull | strongest scalp proxy (XAUUSD 15m +39.4/+10.2); low futures cost *helps* this fee-sensitive edge |
| 4 | `fvg_range` × MGC × 15m | speculative, thin | needs MGC 15m pull | XAUUSD 15m +6.8/+3.5 (BTC-scale width filter may need re-param) |
| 5 | `fvg_range` × **MES × 15m** | speculative | ✅ MES 5m→resample 15m | SPY 15m +7.4/+7.9 both windows (corrected RTH) |
| 6 | `ict_scalp` × **MES × 5m** | speculative, thin | ✅ MES 5m | SPY 5m +6.9/+4.6 (modest, positive both) |
| 7 | `trend_donchian` × **MGC × 4h** | speculative | ✅ MGC 1h→resample 4h | gold 4h spot +18/+20; 4h dodges the fee-sensitivity that sank the 1h cell |
| 8 | `trend_donchian` × **MGC × 1h** | contradicted | ✅ MGC 1h (pulled) | re-test only, to confirm/deny the −15.5R GC=F demote on native MGC futures (closes the basis/roll caveat) |
| 9 | ORB × MES × 5m | contradicted | ✅ MES 5m | already failed P5; revisit only with a materially different exit |
| 10 | any MHG intraday | speculative, NO DATA | needs MHG 5m/15m pull | blocked until native copper intraday exists |

**This session runs #1, #2, #5, #6, #7, #8** on data already available (MGC 1h pull + existing
MES 5m) — plus the native-1d validation of the two LIVE daily pullback cells. #3, #4, #10 need
additional native 15m pulls and are deferred to a scoped follow-up.

## Cross-cutting cautions (carry into any run)

1. **Grade with the repo's real gate, not headline R:** `research_sweep.py` →
   `m15_ws_b_fold_report.py` (5-fold anchored walk-forward) → `classify_strategy_tier.py` at
   **2× fees**. A single-window R number is screening, not a verdict.
2. **A positive standalone backtest is not a go-live:** `fade`, `squeeze`, `turtle`, and
   `mgc_trend_1h` all passed standalone and failed live. Paper (`ib_paper`) soak first.
3. **Live wiring of any survivor is Tier-3, operator-gated.**

## Provenance

Evidence cited inline (`file:line`). Cross-checked against `docs/research/`
(m15-phase0, recombination-sweep, P5-orb-mes, expansion-backtesting, ws-a-s1), the
`config/strategies.yaml` strategy comments, and `src/units/strategies/`.
