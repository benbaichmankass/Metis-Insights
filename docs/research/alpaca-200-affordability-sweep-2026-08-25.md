# What a $200 no-margin whole-share Alpaca account can actually hold

**Measured 2026-08-25.** Operator-directed, after the directive: *"we should
definitely be checking for [sprint logs], but we also shouldn't rely — don't
accept them as canonical truth if we have the possibility of verifying. And it
just doesn't make sense that there's no proxies."*

## The claim under test, and the verdict

`docs/sprint-logs/S-PROXY-EQUITIES-ALPACA-LIVE-2026-07-07.md` § Deferred:

> **No QQQ proxy.** Nasdaq-100 has no sub-$100 ETF; the QQQ cell stays
> unaffordable on `alpaca_live` until the account is funded higher.

**The literal sub-claim is narrowly true. The operational conclusion is FALSE,
and it stood for seven weeks** — long enough that a session repeated it as
established fact on 2026-08-25 before measuring it.

- True: there is no sub-$100 **Nasdaq-100** ETF. `QQQM` is **$290.81**.
- False: that Nasdaq/tech exposure is unreachable. **Four routes size today** —
  `ONEQ` $102.40 (Nasdaq Composite), `VGT` $116.42, `IGM` $156.61,
  `QQQJ` $45.64 (Nasdaq Next Gen 100).

The same holds for the other "unreachable" legs. `IWM` at $303.03 does not
size; **`VTWO` at $120.47 tracks the same Russell 2000 index and does.**
`GLD` at $371.52 does not; six gold routes under $100 do.

**A proxy does not have to be a 100% index match** (operator directive, same
day). A loose match that SIZES beats a perfect match that cannot. These are
markers for what an account this size can hold; strategy params are expected to
need re-tuning onto whichever clear, exactly as SPLG/IAUM were.

## Population — state it before reading anything below

`yfinance-lane-proof` run
[32828398224](https://github.com/benbaichmankass/Metis-Insights/actions/runs/32828398224)
(job 97741405818), daily bars, 120 d requested:

| | count |
|---|---|
| requested | **53** |
| sound price | **51** |
| unsound price | **1** — `SPLG` (1 bar, 39 d stale; `BL-20260825-SPLG-HAS-NO-USABLE-YFINANCE-HISTORY`) |
| no data | **1** — `DDG` `fetch_failed` (drop or correct the ticker) |

Every sound symbol returned 83 bars / 119 d, `as_of 2026-08-24`.

Verdicts below come from **`RiskManager.position_size`** at `equity 200.0`,
`risk_pct 0.05`, `whole_units=True`, `available_usd=200.0` — the real sizer, not
arithmetic over the table.

## Result: 42 of 51 reachable

### ⚠️ Two things that decide the answer, and neither is obvious

**1. The cash ceiling is `0.9 × equity`, not equity.**
`risk.py::_MARGIN_SAFETY_BUFFER = 0.9`, so on $200 the wall is **$180.00**.
`XLK` at **$180.05 misses by five cents**. All 9 refusals are **cash-bound
against that buffered ceiling** — `XLK` and `XOP` cost less than the account and
still refuse. A reader comparing price to $200 gets both wrong.

**2. Hand arithmetic is wrong, in the permissive direction.**
`budget / stop >= 1` cannot see `risk.py::_ROUND_UP_BUDGET_MULT = 1.5`, the
round-up-to-one-share relaxation. It produced a false *"5% adds GDX"* claim in
this same session; the sizer refuted it. **Run the sizer.**

### Reachable, by exposure (price · stop$ · qty · risk$ · %equity)

**Nasdaq / tech** — `QQQJ` 45.64 · 0.51 · 3 · $1.52 · 0.8% — `ONEQ` 102.40 ·
1.12 · 1 · $1.12 · 0.6% — `VGT` 116.42 · 1.91 · 1 · $1.91 · 1.0% — `IGM`
156.61 · 2.57 · 1 · $2.56 · 1.3%

**US large cap** — `SCHX` 30.13 · 0.20 · 5 · $0.99 · 0.5%

**Small cap / Russell 2000** — `SCHA` 34.47 · 0.42 · 5 · $2.12 · 1.1% —
`VTWO` 120.47 · 1.17 · 1 · $1.17 · 0.6% — `IJR` 146.77 · 1.36 · 1 · $1.36 · 0.7%

**Gold** — `SGOL` 44.29 · 4 · $3.27 — `OUNZ` 44.71 · 4 · $3.30 — `BAR` 45.82 ·
3 · $2.51 — `AAAU` 45.87 · 3 · $2.52 — `IAU` 87.47 · 2 · $3.26 — `GLDM` 92.02 ·
1 · $1.72

**Silver** — `SIVR` 65.37 · 2 · $3.75 · 1.9%

**Energy** — `USL` 52.22 · 3 · $2.72 — `BNO` 52.78 · 3 · $4.12 — `XLE` 63.11 ·
2 · $2.39 — `IEO` 135.40 · 1 · $2.90 — `VDE` 177.87 · 1 · $3.24

**Treasuries** — `GOVT` 22.49 · 8 · $0.51 — `SCHO` 24.09 · 7 · $0.17 — `SCHR`
24.42 · 7 · $0.43 — `SPTL` 25.23 · 7 · $1.31 — `VGLT` 53.05 · 3 · $1.16 —
`BND` 72.37 · 2 · $0.44 — `SHY` 82.00 · 2 · $0.15 — `TLH` 97.14 · 1 · $0.64 —
`IEI` 116.50 · 1 · $0.26

**Gold miners** — `GOAU` 50.44 · 3 · $5.10 · 2.6% — `SGDM` 86.19 · 2 · $6.22 ·
3.1% — `RING` 91.24 · 1 · $3.46 — `GDXJ` 133.58 · 1 · $5.12 · 2.6%

**−1x inverse (the long-only short side)** — `DGZ` 4.80 · 29 · **$9.69 · 4.8%**
— `RWM` 13.49 · 13 · $1.75 — `EUM` 15.99 · 11 · $2.50 — `DOG` 21.32 · 8 · $1.23
— `TBF` 25.36 · 7 · $1.46 — `PSQ` 26.18 · 6 · $1.84 — `TBX` 28.81 · 6 · $0.64 —
`SEF` 29.09 · 6 · $1.44 — `SH` 32.55 · 5 · $1.05

⚠️ **`DGZ` is the outlier and should not be waved through**: 29 shares consumes
**$9.69 of a $10.00 budget (4.8% of equity)** — by far the largest single-trade
risk in the set — and its ATR is **6.96% of price**, 2–70× every other
candidate. Cheap shares buy granularity, not safety.

### Refused — all 9 cash-bound against the $180.00 buffered ceiling

`XLK` 180.05 (**by $0.05**) · `XOP` 186.24 · `IYW` 244.11 · `FTEC` 277.79 ·
`QQQM` 290.81 · `VB` 302.90 · `QTEC` 309.86 · `OIH` 408.71 · `SMH` 546.80

## What this does NOT establish

Affordability is **one** gate. Nothing here measures tracking error against the
leg's intended exposure, liquidity/AUM (`TBX` was already flagged thin at ~$14M),
expense ratio, or whether any strategy is profitable on these instruments. The
−1x funds are **daily-rebalanced and path-dependent over multi-day holds** —
that decay and the 0.89–0.95% expense ratios must sit INSIDE the backtest, not
in a footnote.

**Membership in the ticker map is not tradeability.** None of these are in
`config/instruments.yaml`, nothing routes to them, and
`tests/test_fetch_backtest_candles_yfinance.py` asserts that — going RED when
real wiring lands, which is the signal to update it deliberately.

Declaring any of them is **Tier-3**, and `scripts/prop/account_compat_matrix.py`
is owed at the new 5% `risk_pct` before any leg here is called gate-cleared.

## The transferable lesson

The blocking claim was a sentence in a sprint log, written against a ~$150
account by a session solving a different problem. It was correct in its own
context and wrong as a general fact, and nothing about reading it revealed
which. **RULE ONE: read the field, not the prose about it** — a sprint log is
prose. One 81-second runner job answered what seven weeks of quotation could not.
