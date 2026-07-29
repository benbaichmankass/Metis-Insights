# ROADMAP_MACRO M1 — free economic-calendar source probe (2026-07-29)

**Why:** FMP's free tier returned HTTP 403 on the economic-calendar endpoint, so
before wiring another adapter we **empirically probed every credible free source
on a GitHub runner** (the sandbox can't reach these hosts) to see which actually
deliver US events with a **consensus AND an actual** — validate before build.
Probe: `scripts/macro/econ_source_probe.py` on the `econ-source-probe` workflow.

## Calendar-source verdict

| source | HTTP | US rows | US+consensus | US+actual | usable |
|---|---|---|---|---|---|
| **fxstreet:calendar-api** | **200** | **92** | **34** | **43** | ✅ **WINNER** |
| faireconomy:thisweek (ForexFactory) | 200 | 26 | 20 | 0 | ⚠️ 1-wk window, no actuals at fetch |
| faireconomy:last/nextweek | 404 | — | — | — | ❌ variants gone |
| tradingeconomics:guest | 410 Gone | — | — | — | ❌ retired |
| eodhd:demo | 403 | — | — | — | ❌ |

**Chosen: FXStreet's own `calendar-api`** (`calendar-api.fxstreet.com/en/api/v1/
eventDates/{from}/{to}`) — **keyless**, the exact upstream Bigdata.com resells,
returning consensus + actual + revised + volatility in one call. Needs an
`Origin`/`Referer: https://www.fxstreet.com` header. A *better* free source than
the paid FMP we started with. Adapter: `scripts/macro/econ_calendar_fxstreet.py`.

Real captured schema (per event): `dateUtc · name · countryCode · actual ·
consensus · previous · revised · ratioDeviation · volatility · unit · isReport ·
isSpeech · …`. `surprise = actual − consensus` keys on the never-revised
consensus; `revised` (a revision of the prior value) is carried to
`previous_original` for reference only.

## FMP free-tier repurpose verdict (for backtest history)

The uniform `/api/v3/` 403 was a **retired-legacy-path** issue, not per-endpoint
gating. On the modern **`/stable/`** path the free key works for some data:

| FMP `/stable/` endpoint | result | repurpose |
|---|---|---|
| `treasury-rates` | ✅ 200 (full curve `month1…year30`) | M28 term-slope / macro context |
| `historical-price-eod/full?symbol=SPY` | ✅ 200 (OHLCV+vwap) | equity/ETF EOD — candle failover |
| `key-metrics` / `ratios` | ✅ 200 (EV, margins, P/E inputs) | M28 value sleeve fundamentals |
| `economic-indicators?name=GDP` | ⚠️ 200 but 0 rows | needs query tuning |
| `historical-price-eod/full?symbol=NGUSD` / `UNG` | ❌ 402 Payment Required | **natural gas is PAID** |
| `economics-calendar` | ❌ 404 | calendar stays with FXStreet |

**So:** FMP free is genuinely usable for **Treasury-curve + equity EOD +
fundamentals** history — but **not** commodities (natural gas 402). The M1
natural-gas price join must come from elsewhere (the repo's existing
yfinance/Stooq macro-candle fetcher covers `NG=F`/`UNG`). These FMP repurposes
are proposed follow-ons, not wired here.

## Operating-model impact

`econ-calendar-produce.yml` now fetches **FXStreet** (keyless) on its daily
schedule — no `FMP_API_KEY`, no session dependency, no PAYG. The Bigdata.com
tearsheet remains an optional richer cross-check (`.md` captures). The throwaway
`econ-source-probe` scaffolding is diagnostic-only and lives on its probe branch.
