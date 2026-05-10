# S-AI-WS5-B-PART-1 — AI traders WS5-B Part 1: `market_raw` adapter framework

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md)
**Status:** ✅ COMPLETE

## Goal

Land the WS5-B prereq: a pluggable `market_raw` source-adapter
framework + the first concrete adapters. CSV (test/local) +
Bybit off-VM scaffold (the operator's pick).

## Decisions

- **Sprint id `S-AI-WS5-B-PART-1`.** WS5-B is large enough that
  splitting into Part-1 (prereq + framework) and Part-2 (classifier
  + actual bybit fetch wiring) keeps PRs reviewable. Operator
  confirmed "WS5-B regime classifier" with "off-VM exchange
  adapter first"; this sprint ships the off-VM adapter as the
  first concrete one (env-gated; fetch wiring filed for them).
- **Canonical row shape pinned in code** (`adapters/base.py:CANONICAL_SCHEMA`)
  rather than just docs. The dataset builder reads this constant
  for its schema, so any drift between adapters and the dataset
  fails the row-validation gate.
- **Bybit adapter ships with `NotImplementedError` on the actual
  fetch path.** The class, the env-gate, and the registry entry
  are real; the exchange call requires operator-supplied
  credentials + a non-VM build host, neither of which is
  available in CI. The operator wires the call when they next
  build on a non-VM host. This is honest scope rather than a
  pretended-implementation.
- **WS9 enforced via env var.** `ICT_OFFVM_BUILD_HOST=1`
  required; no env, no run. The Oracle live VM must never set
  this. Operator-facing constant pinned by a test.
- **CSV adapter is intentionally simple.** Case-insensitive
  headers, optional volume column, required
  `ts,open,high,low,close`. Works against any operator-staged
  CSV.

## Deliverables

Code (stdlib only):
- [`ml/datasets/adapters/`](../../ml/datasets/adapters/): `__init__`,
  `base`, `csv`, `bybit_offvm`, `registry`.
- [`ml/datasets/families/market_raw.py`](../../ml/datasets/families/market_raw.py).
- [`ml/datasets/registry.py`](../../ml/datasets/registry.py)
  registers `MarketRawBuilder`.
- [`tests/ml/datasets/test_market_raw.py`](../../tests/ml/datasets/test_market_raw.py).

Docs:
- [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md) (new).
- [`docs/data/{dataset-taxonomy,dataset-schema}.md`](../data/) updates.
- [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
  — sub-sprint table + Part-1 details + Part-2 plan.
- [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
  — Live audit row + Known Gaps + Change Log.
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md),
  [`ROADMAP.md`](../../ROADMAP.md) — ledger / change-log.
- This file.

## Acceptance

- [x] `market_raw` family is buildable via CSV.
- [x] Adapter framework supports new sources via subclass +
  registry entry.
- [x] Off-VM env-gate is real and tested (refuses without
  `ICT_OFFVM_BUILD_HOST=1`; refuses with wrong value; raises
  `NotImplementedError` past the gate).
- [x] Dataset metadata records adapter + builder version so the
  build is reproducible.
- [x] Operator-facing env var name is pinned by a test
  (`ICT_OFFVM_BUILD_HOST` is a contract, not an implementation
  detail).

## Out of scope (deferred to PART-2)

- Bybit off-VM `_fetch_bars(...)` actual wiring — operator owns
  this on a non-VM host with read-only credentials.
- Regime classifier trainer + evaluator + manifest.
- `market_features` derivation builder.
- Other adapters (yfinance, Binance off-VM, on-disk parquet,
  WebSocket-derived snapshots).

## Hand-off

1. **Operator wires `BybitOffvmMarketRawAdapter._fetch_bars` on a
   non-VM host.** Reuse `src/exchange/bybit_connector.py` (or
   call ccxt / pybit directly). Translate each candle into the
   canonical row shape. Refer to
   [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md)
   § “Bybit off-VM wiring”.
2. **WS5-B-PART-2** then lands the regime classifier.
3. Optional: yfinance adapter as a third concrete example, when
   useful.

## Live runtime impact

None. Stdlib-only additive code under `ml/datasets/adapters/`,
`ml/datasets/families/market_raw.py`, and `tests/ml/datasets/`.
Operator-hold paths not modified. The Bybit adapter does NOT
import `src/exchange/*` in this sprint (the import + call land in
PART-2 alongside the operator wiring).
