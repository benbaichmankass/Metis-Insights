# Order-flow / microstructure capture — design proposal (S-MLOPT-S10, M14 Phase 2.2)

> **Status:** PROPOSAL, opened 2026-06-04. The Tier-1 estimator core
> ([`ml/datasets/orderflow_features.py`](../../ml/datasets/orderflow_features.py))
> + tests are shipped; **the live-capture path + storage + runtime wiring
> described here are Tier-2 and need operator sign-off before any of it is
> built.** This doc is the decision packet, not a commitment.
>
> Authority above this doc: `docs/CLAUDE-RULES-CANONICAL.md` →
> `docs/ARCHITECTURE-CANONICAL.md` → `ROADMAP.md`. Parent plan:
> [`docs/ml/optimization-roadmap.md`](optimization-roadmap.md) § Session 2.2.

## The one fact that shapes everything

Unlike S9 (range-vol, derived from OHLC we already store) and S11 (funding/OI,
backfillable from Bybit REST history), **order-flow features need L1/L2 +
trade-tick data that we neither capture nor can backfill** — exchange L2 history
is not a public REST endpoint. So OFI/VPIN cannot be A/B'd offline today. S10 is
therefore a **two-part sprint**:

1. **Tier-1 (done):** the pure estimator core — OFI (Cont), VPIN + bulk-volume
   classification (Easley-López de Prado-O'Hara), micro-price (Stoikov),
   relative spread — CI-tested, no I/O.
2. **Tier-2 (this proposal):** a forward live-capture path that ACCRUES the data
   so the features can eventually be built + A/B'd. This is the gate.

## Research caveat (carry it forward)

Microstructure alpha **decays** (the research pass was explicit). If S10 ever
earns promotion, it must be monitored via the KS/PSI drift gate — don't assume
permanence. This argues for storing the features in a way the drift tooling can
already read (the per-bar aggregate below).

## Proposed shape (for operator decision)

### 1. What we store — per-bar aggregates, NOT raw ticks
Raw L2/tick capture is enormous and most of it is noise for a bar-cadence regime
/ decision model. Propose computing the microstructure features **at capture
time over each bar's intra-bar ticks** and storing **one row per bar**, exactly
mirroring the S11 funding/OI side-stream so it reuses the as-of-join + drift
machinery:

`market_microstructure` side-stream rows `{ts, symbol, ofi, vpin, rel_spread_mean, microprice_dev}`
- `ofi` — Cont OFI summed over the bar's best-quote snapshots
- `vpin` — over the trailing N volume buckets (BVC-classified)
- `rel_spread_mean` — mean relative spread across the bar
- `microprice_dev` — mean (micro-price − mid) / mid across the bar (signed lean)

This bounds storage to ~one row/bar/symbol and lets `market_features` join it via
an optional `microstructure_path` (the S11 `funding_oi_path` pattern), adding
past-only columns (`ofi` / `ofi_zscore` / `vpin` / `rel_spread_mean` /
`microprice_dev`), `builder_version v4 → v5`. Leakage-safe by construction.

### 2. Where the capture runs — **operator decision needed**
WS9 forbids heavy data capture on the Oracle **live** VM. Options, in order of
preference:
- **(a) Trainer-VM side-car (preferred).** A small `ict-orderflow-capture`
  service on the trainer VM (autonomous territory) subscribes to Bybit public
  L2 + trades, aggregates per bar, writes `market_microstructure` shards that the
  existing trainer-mirror sync already ships. No live-VM load; reuses the mirror
  pipeline. Downside: a second always-on WS client (public data, low cost).
- **(b) Dedicated tiny host.** Cleanest isolation, but adds an OCI instance
  against the Always-Free ceiling (live 1/6 + trainer 1/6 → room for 2/12).
- **(c) Live-VM lightweight capture.** Rejected unless (a)/(b) are infeasible —
  violates the WS9 "no heavy capture on live VM" posture.

### 3. Transport — **operator decision needed**
- **WebSocket (ccxt.pro `watch_order_book` / `watch_trades`)** — accurate event
  stream, but ccxt.pro is a paid package. 
- **REST polling (`fetch_order_book` + `fetch_trades` at ~1 s)** — free (plain
  ccxt, already a dep), coarser OFI (samples the book rather than every event),
  but adequate for per-bar aggregates and the cheapest start.
- **Recommendation:** start with **REST polling on Bybit BTCUSDT** (free,
  reuses the existing connector), validate the feature signal, only move to WS if
  the per-bar aggregate proves it earns its keep.

### 4. Symbol scope
Start **BTCUSDT only** (Bybit public L2 is free + reliable). MES/IBKR L2
(`reqMktDepth`) needs a paid depth subscription and shares the single live IB
session — defer to a phase-2 follow-up.

## Tiering
- **Tier-1 (autonomous, shipped):** the estimator module + tests; later the
  `market_microstructure` family schema + the `market_features` join columns +
  the A/B manifest at `research_only`.
- **Tier-2 (operator-gated):** the capture service + its systemd unit + storage +
  mirror-sync wiring (a new always-on process + a data-mutation job).

## Open questions for the operator
1. Capture host: trainer-VM side-car (a), dedicated host (b), or other?
2. Transport: REST polling (free, recommended to start) or WS via ccxt.pro (paid)?
3. Scope: BTCUSDT-only to start (recommended), or also wire MES depth now?

## Status / next step
Awaiting the three decisions above. On sign-off, the build order is: capture
service (Tier-2) → accrue ≥ a few weeks of `market_microstructure` → build the
join columns + A/B manifest (Tier-1/3) → purged-CV A/B vs the v2 / yz champions.
Tracked in `MB-20260604-002`.
