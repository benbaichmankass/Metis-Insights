# ROADMAP_MACRO M28 — FMP `/stable/` free-tier: verified limits + NO-BUILD verdict (2026-07-29)

**Question:** the earlier source probe
([`M1-econ-calendar-source-probe-2026-07-29.md`](M1-econ-calendar-source-probe-2026-07-29.md))
found FMP's `/stable/` path serves *some* data on the free key. Is any of it worth
wiring as an **M28 backtest-history source**?

**Answer: no.** Per the `macro-research` skill's verify-before-build invariant we
captured the **real** `/stable/` response shapes on a runner (the sandbox can't reach
FMP) rather than building against the optimistic "treasury/EOD/fundamentals are
usable" read. The captured shapes show the usable data is either **redundant** with
feeds M28 already has, or **too shallow / wrong-grain** to fill the one real gap. No
adapter is built; this doc is the durable record so no future session re-probes.

## Captured `/stable/` free-tier reality (2 probe rounds, real responses)

| Endpoint (free key) | Result | Verdict for M28 |
|---|---|---|
| `treasury-rates` (+ `from`/`to`) | ✅ 200, full curve `month1…year30`, **deep history via range** | **Redundant** — M28 prices rates off FRED (`DFII10`, curve series); FMP adds no gap |
| `historical-price-eod/full?symbol=SPY` | ✅ 200, ~1253 rows (≈5yr OHLCV+vwap) | **Redundant** — the wired yfinance→Stooq fetcher already covers equity/ETF EOD |
| `key-metrics` / `ratios` (single company, e.g. AAPL) | ✅ 200 but **capped at 5 ANNUAL rows** (`limit>5` → 402; `period=quarter` → 402) | **Too shallow** — 5 annual points can't backtest; and M28's seed universe is ETFs, no single-name sleeve exists |
| `key-metrics?symbol=SPY` / `ratios?symbol=SPY` | ⚠️ 200 but **0 rows** — no fundamentals for ETFs | **Can't wire** the `sp500_earnings_yield` slot |
| `key-metrics?symbol=^GSPC` (index) | ❌ 402 "special endpoint symbol" | Index-level earnings yield is paywalled |
| `historical-price-eod` `UNG` / `NGUSD` | ❌ 402 "special endpoint symbol" | Natgas price stays with NG=F via yfinance/Stooq (confirms M1) |
| `economic-indicators?name=GDP\|realGDP` | ✅ 200 but **latest single point only** (no history as-probed) | No backtest series |

## Why this is a NO-BUILD (not a gap)

The one concrete M28 gap FMP *could* have filled is the `equity_risk_premium`
metric's `earnings_yield: {source: sp500_earnings_yield}` input (it honest-nulls
today). FMP free **cannot** serve it: the SPY ETF returns zero fundamentals and the
`^GSPC` index is 402. So the ERP slot stays FRED-derived or unfilled — FMP doesn't
change that. Everything FMP free *does* serve (treasury curve, equity EOD) M28
already has from FRED + yfinance/Stooq. Building an `fmp_stable` adapter + a scheduled
producer for redundant data would add a feed to maintain, a key dependency, and PAYG
surface for **zero** new capability — the opposite of what rec #2 ("backtest-history
sources") was after.

## What WOULD change the calculus (future, not now)

- A **paid FMP tier** — lifts the 5-row fundamentals cap + unlocks index/ETF
  fundamentals + commodities. Only worth it if a **single-name value sleeve** exists
  to consume company fundamentals (M28's seed universe is ETF-level today, so there's
  nowhere to land them regardless).
- An **index earnings-yield** need met elsewhere (a FRED-derivable proxy, or Shiller
  CAPE data) would wire the ERP slot without FMP.

## Disposition

- **No adapter, no producer workflow, no config change.** Honest-negative.
- The throwaway probe (`scripts/macro/fmp_stable_probe.py` + `fmp-stable-probe.yml`)
  is **retired** — its output is captured in the table above.
- The optimistic "FMP free-tier repurpose" table in
  `M1-econ-calendar-source-probe-2026-07-29.md` is corrected by this doc (its
  "usable" reading predated the depth/grain probe).
