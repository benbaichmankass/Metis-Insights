# M19 Data workstream — the wide multi-asset context corpus — design

> **Status:** 📋 DESIGN (2026-07-02, autonomous overnight push). This specs the
> **wide multi-asset "read-mostly" context corpus** — the fuel the M19 roadmap's
> in-house self-supervised encoder (T1.2) reads, and a near-term feature source
> for the Tier-0 heads. It ships **no code and no data** in this doc; it's the
> plan the first free adapter PR then executes. Everything here is
> **offline/`candidate`, trainer-side, read-mostly** — it never touches the money
> DB and never influences a live order. Roadmap:
> [`ai-model-strategy-roadmap-2026-07-01.md`](ai-model-strategy-roadmap-2026-07-01.md)
> § "Parallel workstream — the wide multi-asset data corpus". Sibling designs:
> [`T0.4-live-parity-spike-DESIGN.md`](T0.4-live-parity-spike-DESIGN.md),
> [`T1-gpu-burst-spend-SPEC.md`](T1-gpu-burst-spend-SPEC.md).

## Why a corpus, and why now

The M19 Tier-0 sweep landed a clear map: the class-weighted **LightGBM** regime
head over hand-engineered + **forecast** features is the thing to beat, and every
*frozen off-the-shelf* model class we tried (T0.1 Chronos embeddings, T0.2
Gaussian-HMM) **matches-or-loses** to it. The T0.1 follow-up sharpened *why*: the
frozen embedding lift is **base-rate-dependent** (real at 0.003–0.004, a cliff at
the shipped 0.005) — a *generic* representation only helps a niche. The two levers
that could actually beat the incumbent both need something we don't yet have:

- a **task-specific** encoder (T1.2) — one that learns *our* market structure
  rather than a generic univariate-forecasting prior; and
- **more signal to learn from** — the encoder is label-free, so its ceiling is set
  by the **breadth and history of the unlabeled panel** it reads, not by our ~350
  labeled trades.

The corpus is the second lever, and it is the **long pole**: an encoder is a
weekend of GPU burst (T1.1/T1.2), but a broad, clean, leakage-safe, incrementally
-maintained multi-asset panel is weeks of data plumbing. Per the roadmap it must
**start early, in parallel** with the Tier-0 work — which is why it's designed now
even though the encoder that consumes it is Tier-1.

**The thesis in one line:** the binding constraint is *labels*, not compute — so
the highest-leverage next data investment is the one that feeds the *label-free*
representation model: a wide *context* panel of everything the market is doing,
far beyond the five symbols we trade.

## What already exists (extend, do not rebuild)

The repo already has the *architecture* for as-of-joined external side-streams —
the corpus generalizes it, it does not invent it. Grounding (cite, don't
re-derive):

| Existing piece | What it does | How the corpus extends it |
|---|---|---|
| `ml/datasets/adapters/yfinance_macro.py` | Fetches a **narrow** daily macro complex (VIX, VIX3M, DXY, UST10y, UST3m) for the MES head | The corpus is the **wide** generalization: many more series across 6 asset groups, same fetch/guard pattern |
| `ml/datasets/macro_features.py` | Pure, leakage-safe **z-score / term-structure** features from those daily series, **lagged one day** | Corpus reuses the *exact* daily-cadence + one-day-lag leakage discipline; adds nothing to the money path |
| `ml/datasets/cross_asset_features.py` + `scripts/ml/build_cross_asset.py` | Per-target **peer-OHLCV** block (`xa_*`) via positional peer slots, as-of joined into `market_features` | Same producer→side-stream→as-of-join shape; the corpus is the standing panel those peers are *drawn from* |
| `market_features` family (`*_path` kwargs: `macro_path`, `cross_asset_path`, `embedding_path`, `forecast_path`) | The as-of, past-only join point every side-stream already plugs into | The corpus exposes the **same `*_path` interface** (a new `corpus_path`) — zero new join machinery |
| `ICT_OFFVM_BUILD_HOST=1` guard (`ml/datasets/adapters/bybit_offvm.py` `OFFVM_ENV`) | Hard refuses to run heavy network pulls on the live VM | **Every** corpus adapter inherits this guard verbatim — the corpus is trainer/build-host only, never the money box |
| `trainer_store.db` (`src/utils/paths.py::trainer_store_db_path`) | The read-mostly federated sidecar for trainer/ML lifecycle data, browsable in the Data Explorer | The corpus's catalog + small tables live here; the bulk time series live as **parquet** beside it (see Storage) |

So the corpus is a **new dataset family + a set of new adapters + one new
`market_features` side-stream path + a parquet store** — all plugging into
scaffolding that already exists and is already leakage-safe by construction.

## The universe — a wide context panel *beyond what we trade*

We trade five symbols (BTC/ETH/SOL + MES/MGC/MHG). The corpus deliberately reads
**far more than that** — the point is *context*, the cross-sectional and
macro backdrop a single-symbol head is blind to. Grouped, with free sources:

| Group | Representative series | Free source | Why it carries signal |
|---|---|---|---|
| **Equity indices** | SPX/ES, NDX, RUT, sector ETFs (XLK/XLF/XLE/…) | yfinance / stooq | Broad risk-on/off; sector rotation is regime information MES can't see alone |
| **FX** | DXY, EURUSD, USDJPY, AUDUSD (risk proxy) | yfinance / stooq | Dollar + carry regime; JPY/AUD are classic risk barometers |
| **Rates curve** | UST 3m/2y/10y/30y, 2s10s slope | **FRED** (keyless CSV) | Curve shape = growth/stress regime; already half-wired for MES |
| **Commodities** | WTI, Brent, Gold, Copper, NatGas | yfinance / stooq | Inflation/growth complex; gold/copper ratio is a known macro tell |
| **Crypto breadth** | BTC.D (dominance), total mcap, ETH/BTC, top-N alt breadth | yfinance / free crypto APIs | Intra-crypto rotation + breadth — the risk-appetite read for our crypto book |
| **Volatility complex** | VIX, VIX3M (term structure), MOVE (bond vol), OVX (oil vol) | yfinance / FRED | Cross-asset implied-vol backdrop; the term-structure slope is the strongest existing macro feature |

**Cadence:** most series are **daily** (the free sources' native granularity for
the breadth/macro reads); a subset (indices, FX majors) can be pulled intraday
later if a head needs it. Daily is the right default — the corpus is *context*
(slow-moving backdrop), and daily keeps the leakage discipline simple and the
data volume tiny.

**History depth:** free daily history runs 10–20+ years for the macro/index
series — orders of magnitude more rows than our labeled trades. That depth is
exactly what a label-free encoder needs; it is the corpus's whole reason to exist.

## Storage — parquet beside `trainer_store`, never the money DB

Two-part store, both trainer-side, both read-mostly:

1. **A catalog + small tables in `trainer_store.db`** — a `corpus_series`
   registry (series_id, group, source, source_ticker, cadence, first/last date,
   row count, last_refresh_at) so the Data Explorer can browse *what's in the
   corpus* and a builder can check freshness. Small, SQL-queryable, federated
   into the existing Data Explorer exactly like the other trainer tables.
2. **The bulk time series as partitioned parquet** under a corpus root
   (e.g. `runtime_logs/trainer_mirror/corpus/<group>/<series_id>.parquet`, resolved
   via a `corpus_root()` helper alongside `trainer_store_db_path()`). Parquet
   because the panel is wide + long + append-mostly and the encoder reads it in
   columnar bulk — SQLite rows would be the wrong shape. Partitioned by group so a
   single adapter refresh rewrites only its slice.

**Hard rule (inherited, non-negotiable):** the corpus **never** lives in, is
**never** joined from, and **never** writes to `trade_journal.db`. It is
trainer/build-host data behind the `ICT_OFFVM_BUILD_HOST` guard, mirrored to the
live VM read-only *only if* a promoted head needs it (and then via the T0.4
mirror-publish path, computed on the trainer — **zero external fetch on the money
box**, same contract as the forecast/embedding side-streams).

## Leakage discipline — reuse the macro block's contract verbatim

The corpus does not invent a new leakage story; it reuses the one
`macro_features.py` already documents and tests:

1. **Compute features at the series' own cadence, past-only.** A daily series'
   z-score / slope / momentum uses only that day and prior days (a trailing
   window), stamped as a fully-computed per-day row — never a step-function
   re-windowed across intraday target bars.
2. **Lag one cadence step.** A day-`D` feature is built from day-`D`'s *close*,
   not known until day `D` ends, so the row is stamped at **`<D+1>T00:00:00Z`**.
   An intraday target bar on day `D+1` then as-of-joins day `D`'s closed reads —
   never a same-day close before it printed.
3. **As-of, backward join only.** The `market_features` join is already
   backward-as-of (each target bar takes the most recent *strictly-prior* corpus
   row); the corpus adds a `corpus_path` that rides that same join. Missing series
   → `0.0` (neutral), exactly like an omitted macro/cross-asset side-stream.

For the **encoder** (T1.2) the discipline is the same but the consumer differs:
the encoder pretrains on the corpus panel *directly* (masked-reconstruction /
contrastive over the multi-series matrix), and any window it reconstructs is
past-only by construction; its output embedding then re-enters a head via the
same lagged as-of join. No labels are involved in pretraining, so the only
leakage surface is the eventual embedding→head join — which is the T0.1 contract
we already validated.

## Phased plan — start with one free adapter

Deliberately incremental so the corpus accretes value without a big-bang PR and
never starves the Tier-0 wins:

| Phase | Deliverable | Gate / check |
|---|---|---|
| **C0 — first free adapter (next PR)** | One new adapter (**FRED rates curve**, keyless CSV — the cleanest free source, and it completes the half-wired MES rates leg) writing to the parquet store + `corpus_series` catalog, behind the off-VM guard, with monkeypatched-network tests | Adapter runs on the trainer, writes N years of daily rows, catalog row appears in Data Explorer; **no** money-DB touch (CI guard) |
| **C1 — corpus side-stream + first A/B** | A `corpus_path` on `market_features` exposing a small curated feature set (curve slope, DXY z, VIX term-structure — the proven-useful macro reads) + an A/B on the MES/BTC regime head | Does the wide-context block lift the head in purged-CV vs no-corpus? (An honest negative is fine — it still seeds the encoder.) |
| **C2 — breadth + commodities + FX adapters** | The remaining free daily adapters (yfinance/stooq groups), each same pattern | Corpus reaches ~30–60 daily series with 10y+ history; catalog complete |
| **C3 — encoder-ready panel export** | A single `corpus_panel` dataset family that assembles the aligned multi-series matrix (date × series, forward-filled to a common daily grid, past-only) the T1.2 encoder pretrains on | Panel builds deterministically; feeds a masked-reconstruction smoke-train (still Tier-0/CPU on a tiny slice) |

C0 is the only near-term commitment; C1–C3 are the workstream's spine, each its
own Tier-appropriate PR, and C3 is explicitly gated on the T1.1/T1.2 GPU tier
being approved (it's the encoder's input, and the encoder is the first GPU spend).

## Trade-offs & honest gaps

- **Plumbing, not modeling.** The corpus is mostly ingestion + hygiene; it earns
  nothing on its own. Its value is entirely downstream (a head A/B lift, or the
  encoder's ceiling). The risk is it becomes a data-janitor sink that starves the
  cheaper Tier-0 wins — mitigated by shipping *one* adapter at a time, each with a
  concrete A/B or catalog deliverable, never a speculative bulk pull.
- **Free-source fragility.** yfinance/stooq are unofficial and rate-limit/break;
  FRED is keyless + stable and is therefore the C0 pick. Each adapter monkeypatches
  its network hook (like the existing yfinance adapters) so CI never touches the
  net, and a fetch failure degrades to "stale catalog row + neutral `0.0`
  features", never a build crash.
- **The corpus doesn't fix the label wall — it routes around it.** It feeds the
  *label-free* encoder; the supervised heads still wait on trades. If the encoder
  (T1.2) shows no lift over the frozen Chronos baseline, the corpus's payoff shrinks
  to "a few extra macro features" — an acceptable, cheap outcome, and one we can
  read early from the C1 A/B before committing to C2/C3.
- **Daily-cadence context on intraday decisions.** A daily backdrop is slow; it
  conditions *regime*, not entry timing. That's the right role (it's why the macro
  block only ever helped the vol-regime family), and the doc sets the expectation
  so a flat intraday-direction A/B isn't misread as failure.

## Gates / discipline (unchanged from the M19 line)

- **Offline/`candidate` only.** No shadow promotion (Tier-3, operator-gated); no
  paid compute incurred by the corpus (free sources only; the encoder that needs
  GPU is the separately-gated Tier-1). Nothing here influences a live order.
- **Trainer/build-host only.** Every adapter behind `ICT_OFFVM_BUILD_HOST=1`; the
  money DB is never read or written; a promoted head's serve path uses the
  mirror-publish contract (compute on trainer, ship read-only) — never a live
  external fetch.
- **Leakage-safe by construction.** Daily compute + one-cadence lag + backward
  as-of join, reusing the `macro_features` contract that already has tests.
- **Every A/B fixes the dataset and varies only the feature block**, purged-CV,
  honest-negative-friendly — same bar as the rest of M19.
