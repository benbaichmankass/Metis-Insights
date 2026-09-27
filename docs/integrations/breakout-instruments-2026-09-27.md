# Breakout — full tradable-instrument reference (2026-09-27)

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Durable reference so a later session does not re-derive this. Companion to
[`docs/integrations/breakout-compliance-2026-06-16.md`](breakout-compliance-2026-06-16.md)
(account-level rules: eval target, drawdown, daily-loss) — this document is
**per-instrument**: which symbols exist, their size/leverage caps, and the
platform (DXTrade vs "Breakout Terminal") that prices them.

## Source and its limits — read this before trusting any row

**Primary source**: `https://www.breakoutprop.com/symbols/`. **Attempted fetch
2026-09-27T08:39:05Z from this session's sandbox — BLOCKED, verifiably, not just
unattempted.** Both `WebFetch` and a direct `curl` (browser User-Agent, through the
environment's outbound proxy) returned `HTTP/2 403` with response header
`cf-mitigated: challenge` — Cloudflare's interactive bot-challenge page, which requires
a real browser (JS execution + challenge solve) and cannot be passed by any
non-browser HTTP client. This is the same result `docs/integrations/breakout-compliance-2026-06-16.md`
already recorded for the FAQ/pricing pages on 2026-06-16 — the block is structural
to this venue's site, not a one-off.

**What this document is built from instead, and how each row is marked:**

| marker | meaning |
|---|---|
| `[operator screenshot, unverified against the live page]` | Transcribed by the operator from a screenshot of the live page, 2026-09-27. Not independently re-confirmed by this session against the primary source (blocked, above). Treat as the best available figure, not as FAQ-grade confirmed. |
| `[WEB]` | Corroborated via `WebSearch` (a different retrieval path than the blocked direct fetch) against third-party sources (quantvps.com, proptradingvibes.com, both read 2026-09-27). Independent of the operator's screenshot. |
| `[CONFIRMED — FAQ]` | Carried over from `breakout-compliance-2026-06-16.md`, sourced from Breakout's FAQ center (`intercom.help/breakoutprop`) on 2026-06-16 — a different page than `/symbols/`, not subject to the same block at that time. |

**Follow-up filed** (pipeline, see the B6 writeup) for a session with real browser access
or a different network path to re-attempt the live fetch and either confirm or correct
every `[operator screenshot]` row below.

## Account-level economics (unchanged from `breakout-compliance-2026-06-16.md`, restated here for one-stop reading)

- Commission: **0.04% notional per side** on every symbol `[operator screenshot, unverified]`,
  independently corroborated `[WEB]` (quantvps.com, proptradingvibes.com, 2026-09-27) —
  matches `scripts/research/prop_ev_sim.py`'s `--costs breakout` default
  (`commission-bps-rt=8.0`).
- Financing/swap RATE (not a sample statistic — no population applies):
  <!-- population-ok: a fee/financing RATE quoted from the venue, not a sample statistic with a denominator -->
  **0.033%/day (3.3 bps) per open position, on notional**
  `[operator screenshot, unverified]`, matching the value already `[CONFIRMED — FAQ]`
  in `config/prop_rulesets/breakout.yaml`'s comment trail (PB-20260616-004 lineage) —
  matches `prop_ev_sim.py`'s `swap-daily=0.00033` default.
  - **DXTrade** (the platform this repo's crypto bridge uses for SOL/ETH/BTC —
    `src/prop/breakout_executor.py`, `config/prop_rulesets/breakout_routing.yaml`):
    books the charge at 00:00 UTC, deducts at 00:25 UTC — once per calendar day.
    `prop_ev_sim.py --swap-model dxtrade` models this.
  - **Breakout Terminal** (non-crypto instruments, and per the operator's screenshot
    all crypto majors too — see below): deducts the SAME per-day rate but split into
    charges every 4 hours rather than one daily lump. `prop_ev_sim.py --swap-model prorated`
    models this. **This is a settlement-mechanics difference, not a different total daily
    rate** — both models charge 0.033%/day of notional per day held; they differ only in
    how that charge is timed relative to a position's open/close, which matters for
    short intraday holds (a `prorated` position pays financing proportional to the hours
    held; a `dxtrade` position pays the full daily charge if it's open at any midnight
    UTC crossing, zero otherwise).

## Non-crypto instruments (Breakout Terminal only) `[operator screenshot, unverified against the live page]`

| symbol | market | max total position size (USD) | leverage |
|---|---|--:|--:|
| CL | Crude oil | 200,000 | 5x |
| S&P500 | US large-cap index CFD | 2,000,000 | 10x |
| SILVER | Silver | 200,000 | 5x |
| XYZ100 | Nasdaq-100 index CFD (see below) | 1,000,000 | 10x |

**XYZ100 identity**: the operator's transcription flagged this as unconfirmed —
established this lane, `[WEB]`, via two independent sources (2026-09-27): XYZ100 is
Breakout's ticker for a **Nasdaq-100 index CFD**. One older-dated web source states 5x
leverage for it rather than the operator's screenshot's 10x; the screenshot is dated
2026-09-27 and this repo has independent evidence that Breakout raised several
leverage tiers in August 2026 (per the same web sources), so the newer, higher figure
is treated as current and the discrepancy is stated rather than silently resolved
either way.

**Instrument mapping to this repo's existing legs** — see
[`docs/research/b6-prop-ev/w6-crypto-candidates-2026-09-27.md`](../research/b6-prop-ev/w6-crypto-candidates-2026-09-27.md)
§5 for the full mapping table, basis caveats, and EV scoring:

| Breakout symbol | our leg(s) today | account |
|---|---|---|
| S&P500 | `spy_trend_long_1d`, `spy_pullback_1h` (SPY ETF) | `alpaca_paper` |
| S&P500 | `mes_trend_long_1d` (MES micro futures — direct index instrument, not an ETF proxy) | `ib_paper` |
| XYZ100 | `qqq_trend_long_1d`, `qqq_pullback_1h`, `tqqq_trend_long_1d` (3x), `qld_trend_long_1d` (2x) | `alpaca_paper` |
| SILVER | `slv_pullback_1d` | `alpaca_live` (**real money**) and `alpaca_paper` |
| CL | `uso_trend_1h` (USO ETF — ⚠️ poor basis proxy for CL/WTI futures, see the writeup) | `alpaca_paper` |

No existing leg maps to a Breakout Terminal instrument this repo doesn't already
route through Alpaca/IBKR (i.e., nothing new was found beyond the table above).

## Crypto instruments `[operator screenshot, unverified against the live page]`

Fuller product list (28 named tickers plus ~50 smaller alts) than the compliance doc's
2026-06-16 read, consistent with `[WEB]`-corroborated evidence (quantvps.com,
proptradingvibes.com, 2026-09-27) that Breakout expanded its crypto product line and
raised leverage on major markets in August 2026.

| symbol | max total position size (USD) | leverage |
|---|--:|--:|
| BTCUSD | 1,000,000 | 10x |
| ETHUSD | 1,000,000 | 5x |
| SOLUSD | 500,000 | 5x |
| BNBUSD | 400,000 | 5x |
| XRPUSD | 300,000 | 5x |
| HYPEUSD | 200,000 | 3x |
| TRXUSD | 200,000 | 5x |
| AAVEUSD, ADAUSD, FILUSD, ONDOUSD | 150,000 | (per-symbol, screenshot did not carry individual leverage — treat as unconfirmed until re-read) |
| DOGEUSD, LINKUSD, LTCUSD, SUIUSD, UNIUSD, ZECUSD | 100,000 | (same caveat) |
| ~50 smaller alts | 25,000–50,000 | 2x–5x |

**This repo's wire-ready subset remains just three**: BTCUSD, ETHUSD, SOLUSD have a
confirmed DXTrade symbol mapping in `config/prop_rulesets/breakout_routing.yaml`
(operator-confirmed 2026-06-23). **ADA and XRP are on Breakout's product list but have
NO confirmed DXTrade symbol/contract-size mapping in this repo** — filed to the
pipeline in the B6 writeup (`PI-20260927-ODDTM5QY-0002`) before either can be wired,
independent of whether their EV evidence clears the bar.

⚠️ **Whether crypto majors trade on DXTrade or the Breakout Terminal is NOT settled by
this lane.** This repo's own bridge code (`breakout_executor.py`) uses DXTrade symbols
(`ETHUSD`, `SOLUSD`, `BTCUSD` — dropping the perp `T` suffix) for the three wired pairs;
the operator's screenshot lists crypto majors without naming a platform per row. Treated
here as DXTrade (consistent with the existing wiring and the crypto-candidate scoring in
the B6 writeup §2, which uses `--swap-model dxtrade`), but this is an assumption carried
forward from existing code, not re-confirmed against the live page this session.

## What this document does NOT do

- Does not change `config/prop_rulesets/breakout_routing.yaml` or
  `config/prop_rulesets/breakout.yaml` (Tier-2) — any ruleset update from this data is a
  proposal, not applied here.
- Does not authorize any roster or routing change (Tier-3).
- Does not resolve the `[operator screenshot, unverified]` rows to confirmed fact — that
  requires either a working fetch of the live page or the operator's own terminal access.
