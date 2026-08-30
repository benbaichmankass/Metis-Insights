# Phase 1 — what bounds `base_oos`, measured

**2026-08-29 · operator-directed** ("we shouldn't need the paper soak to make the live
decision in this case. We should be able to backtest, walk forward on the data").
**Tier-1: two dispatches of `yfinance-lane-proof`, nothing built, no config touched.**

---

## The question

`docs/research/exit-refinement-coverage.json` carries **329 `honest_negative`** verdicts, and
its own headline caveat says they are not safe to read as "the lever does not work":

> *"the **power** one: over the 113 cells that state a base count the **median OOS base is 33
> trades and 94.7% are under 50**. Base size does not separate pass from fail fleet-wide"*
> — `BL-20260827-EXIT-COVERAGE-MATRIX-IS-SUBSTANTIALLY-A-POWER-TEST-FLEET-WIDE`

`base_oos` is a **backtest holdout count, not a live-trade count**, so it is bounded by the
DATA WINDOW — which is testable today rather than accruable over months. That is the whole
reason the paper soak is not the gate.

## Result — the family splits cleanly in two

| | requested | obtained | verdict |
|---|---|---|---|
| **Daily (1d)** — 14 roster symbols, run [33245127184](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33245127184) | 1825 d | **1,823 d / 1,254 bars** | ✅ **full 5 years, no cap** |
| **Hourly (1h)** — GLD SPY QQQ TLT SLV USO, run [33245130364](https://github.com/benbaichmankass/Metis-Insights/actions/runs/33245130364) | 1825 d | **724 d / 3,463–3,464 bars** | ⛔ **capped** |

The hourly run flagged itself, which is the check working rather than a reader noticing:

> `PARTIAL window (yfinance cap), obtained < 80% of 1825d: GLD=724d, SPY=724d, QQQ=724d,
> TLT=724d, SLV=724d, USO=724d`

**724 d against the ~730 d cap the workflow header documents.** The cap is now MEASURED, not
quoted — and identically across all six symbols, which is what a hard API cap looks like as
opposed to a per-symbol data gap.

## What follows

- **The 1d legs are data-expandable.** 5 years is reachable, so their thin `base_oos`
  (4–36 today) is a function of the window previously swept, not of what exists. **Three of the
  four surviving roster candidates are 1d** (`tlt_pullback_1d`, `ief_pullback_1d`, and the
  1d half of the TLT pair), so this is the useful half.
- **The 1h legs are at the ceiling.** `base_is` 197–492 / `base_oos` 26–40 is already what
  724 d yields. **Re-sweeping them on a wider window is not available via this lane** — a
  different data source is the only route, and that is a scoping question, not a run.
- ⚠️ **This does NOT say the 1d re-sweep will clear `MIN_OOS_TRADES = 25`.** It says the
  input exists. A 1d leg trading a few times a year can hold 5 years and still be thin —
  `iaum_pullback_1d` sits at `base_oos 4`. The re-sweep is what settles it.

## A separate finding that resolves an earlier non-finding

**`SPLG` came back UNSOUND: `thin 1b; stale 43d`** — one bar, 43 days stale, on a 5-year
daily request. That re-confirms `BL-20260825-SPLG-HAS-NO-USABLE-YFINANCE-HISTORY` four days
on, unfixed.

**It also explains `splg_trend_long_1d`'s zero order packages in 74 days.** On 2026-08-29 I
recorded that as *"cannot distinguish broken from low-frequency"*, because its siblings
(`spy` 1, `qqq` 2, `iwm` 3) are near-silent too and n=0 against n=1–3 separates nothing.
**That is now resolved in favour of BROKEN:** a strategy whose data lane serves one stale bar
cannot emit a signal at any frequency. The earlier caution was right at the time and the
probe is what settled it.

⚠️ Note the consequence for the roster: **SPLG is the only S&P proxy wired to `alpaca_live`**,
and it has never been able to trade. The sub-$200 S&P exposure the 2026-07-07 promotion was
supposed to deliver **has never existed in practice**. `SCHX` ($30.13) and `VTWO`/`SCHA` are
alternate routes the affordability sweep already found sound.

## Method notes

Both runs go through `fetch_backtest_candles.py --source yfinance` — the real lane, not a
bypass. `as_of` 2026-08-27 (daily) and 2026-08-28 (hourly). The daily run returned 13 sound
of 14 requested; the single unsound symbol is SPLG above.

---

## ⚠️ CORRECTION, same session — the conclusion above is WRONG about the bottleneck

**Everything measured above is accurate. The inference drawn from it is not.** I concluded *"the
1h legs are at the ceiling and cannot be widened via this lane"* and treated data depth as the
thing gating Phase 2. The trainer inventory (issue #10401) refutes that.

### What the sweep's own source directory actually holds

`/home/ubuntu/ict-trading-bot/data` — the directory `m20-exit-lever-sweep` reads for the
trend/pullback family — 2.2 G, 67 files:

| symbol | daily | first | hourly | first |
|---|---|---|---|---|
| SPY | **4,152** | 2010-01-04 | **13,854** | 2017-02-16 |
| QQQ | **4,897** | 2007-01-03 | **13,782** | 2017-03-01 |
| TLT | **4,897** | 2007-01-03 | **13,812** | 2017-03-01 |
| IEF | **4,897** | 2007-01-03 | **12,416** | 2018-02-01 |
| SLV | **4,897** | 2007-01-03 | **12,431** | 2018-02-01 |
| USO | **4,897** | 2007-01-03 | **13,808** | 2017-03-01 |
| GLD | 4,152 | 2010-01-04 | 13,800 | 2017-03-01 |
| IWM | 4,152 | 2010-01-04 | 13,814 | 2017-03-01 |
| SPLG | 4,149 | 2010-01-04 | — | — |
| SCHA | 4,152 | 2010-01-04 | — | — |
| GDX · QLD · TQQQ | 2,515 | 2016-07-11 | — | — |
| IAUM | 1,259 | 2021-07-01 | — | — |

**The hourly files hold ~9 years (12,400–13,850 bars). yfinance serves 724 days.** So the
724 d cap bounds **RE-FETCHING**, not what the sweep can read — and the sweep does not fetch.
The 1h legs are **not** data-starved, and "widen the window" was never the unblock.

### What this means for the diagnosis

`base_is` 197–492 and `base_oos` 26–40 on legs holding **13,800 hourly bars** is not a data
limit — it is **how often the strategy trades**. A leg with 9 years of bars and ~500 lifetime
trades is a low-frequency leg, and no amount of history changes that. So the power problem the
coverage matrix flags is a **trade-frequency** property, not a fetch-depth one.

⚠️ **I have NOT established why the derived `oos-trades` split lands at ~30 rather than its
50 target on this much data** — that is the next question, and it is a question about the sweep's
own window selection, not about the files. Do not read this correction as "the sweep is
misconfigured"; read it as "the input I blamed is not the constraint."

### Two findings that survive, and one that changes shape

- **The 724 d intraday cap is real** and correctly measured. It bounds the LIVE fetch lane and
  any fresh pull, which still matters for a NEW symbol that has no file yet.
- **No inverse-ETF file exists at all.** `SH`, `PSQ`, `RWM`, `DOG`, `TBF`, `TBX`, `DGZ`, `SEF`,
  `EUM`, `VTWO`, `SCHX` — **zero files**, under any naming scheme (the listing was checked
  directly, not inferred from a failed grep). Any inverse-leg backtest needs a fetch first, and
  that fetch IS bound by the 724 d cap at 1h — though **daily is unbounded and the inverse
  candidates are all daily-timeframe candidates anyway**.
- **SPLG changes shape rather than resolving.** It has **4,149 daily bars from 2010** on the
  trainer, so it is fully **backtestable** — while its **yfinance** lane returns one 43-day-stale
  bar. Those are two different lanes, and the earlier entry in this document conflated them.
  What survives: `splg_trend_long_1d` produced zero packages, and the live candle path is the
  plausible cause. What is now wrong: calling SPLG "broken" without qualification. It is broken
  **for the live fetch**, sound **for research**. ⚠️ I have not verified which lane serves live
  ETF candles on this account, so the causal link to the zero packages is *plausible and
  unproven*, not established.

### The transferable lesson

I measured the fetch lane, found a cap, and inferred a constraint on a consumer **that does not
use that lane** — the sweep reads files. The measurement was sound and the inference skipped a
step: *establish that the thing you measured is the thing the consumer depends on.* One
`ls` on the source directory would have caught it before the conclusion was written, and the
sweep's own header names that directory explicitly.
