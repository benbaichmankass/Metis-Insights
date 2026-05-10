# S-AI-WS5-B-PART-2 PR 2A — Bybit off-VM fetch wiring

**Date:** 2026-05-10
**Authority:** [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md), [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md), [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md), [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
**Status:** ✅ COMPLETE

## Goal

Wire `BybitOffvmMarketRawAdapter._fetch_bars(...)`. PART-1 shipped
the class, env-gate, and registry entry but stopped at
`NotImplementedError` on the actual fetch path. This PR connects
that path to ccxt's Bybit V5 connector with paginated time-range
fetches, bringing the off-VM `bybit_v5_offvm` adapter to feature
parity with the CSV adapter.

## Decisions

- **Sprint id `S-AI-WS5-B-PART-2 PR 2A`.** Operator picked "two PRs"
  (this one + PR 2B for the regime classifier) so the network-
  adapter contract and the classifier baseline can be reviewed
  independently. PR 2A's blast radius is contained to the off-VM
  adapter and a small builder-framework auto-forward.
- **Direct ccxt over `BybitConnector` reuse.** `BybitConnector` is
  pandas-typed and `limit`-bounded (recent N bars). The off-VM
  build pattern is "pull a date range" — `ccxt.bybit.fetch_ohlcv`
  with `since` is the right primitive. Reusing `BybitConnector`
  would have dragged in pandas and required a wrapper to expose
  `since`-style pagination.
- **Lazy-import `ccxt` inside `_build_exchange`.** A build host
  without ccxt still hits the env-gate first, and tests that
  monkeypatch `_build_exchange` don't need ccxt either. Keeps the
  on-VM import surface untouched (ccxt is not added to the
  runtime requirements).
- **`_build_exchange` is a classmethod.** Tests inject a
  `_FakeBybitExchange` that records calls and replays canned pages.
  CI never touches the network.
- **Pagination guards.** Defensive against three real-world
  pathologies of `fetch_ohlcv`: (1) ccxt sometimes returns a
  small pre-`since` prefix → drop those bars; (2) `since` may
  fail to advance → bump cursor by one bar; (3) end-window cutoff
  → halt the iterator the moment a bar's ts >= `end_ms`.
- **Builder auto-forwards scope into `iter_rows` kwargs.** Without
  this, the operator (or a test) has to pass `--symbol-scope`
  (path layout) AND `symbol=` (adapter kwarg) — Python doesn't
  allow the same kwarg twice through `**`-spread, so the
  pre-existing `TestMarketRawBuilder::test_build_round_trip_via_csv`
  was actually broken on `main`. Auto-forwarding fixes the test
  and removes a footgun. Other builders use `**_: Any` so they
  ignore the extra kwargs.

## Deliverables

Code:
- [`ml/datasets/adapters/bybit_offvm.py`](../../ml/datasets/adapters/bybit_offvm.py)
  — `_fetch_bars` wired via ccxt, pagination + canonical-row
  normalisation, `_build_exchange` classmethod hook.
- [`ml/datasets/builder.py`](../../ml/datasets/builder.py)
  — `DatasetBuilder.build` auto-forwards `symbol_scope` /
  `timeframe` into `iter_rows_kwargs` via `setdefault`.
- [`ml/datasets/families/market_raw.py`](../../ml/datasets/families/market_raw.py)
  — `MarketRawBuilder.iter_rows` translates the forwarded
  `symbol_scope` / `timeframe` into adapter `symbol` / `timeframe`
  defaults (only when the scope isn't the family default `"all"`).

Tests (no network, no ccxt dep in CI):
- [`tests/ml/datasets/test_market_raw.py`](../../tests/ml/datasets/test_market_raw.py):
  - `TestBybitOffvmFetch` (7 cases): pagination across pages,
    end-window cutoff, empty first page, unknown timeframe,
    inverted window, canonical row shape, credential threading
    (env vs explicit kwargs), pre-`since` prefix dropped.
  - Existing `TestBybitOffvmEnvGate::test_with_env_invokes_fetch`
    rewritten to assert the wired path, not `NotImplementedError`.
  - Pre-existing CSV round-trip test now passes against the
    auto-forwarded scope kwargs.

Docs:
- [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md)
  — Bybit section refreshed: live build runbook (creds, env-gate,
  `pip install ccxt`, CLI invocation), implementation notes, ccxt
  `fetch_ohlcv` semantics, supported timeframes.
- [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md)
  — live audit row updated; change log row added; banner refreshed.
- [`docs/sprint-plans/ai-traders/ws5-baseline-models.md`](../sprint-plans/ai-traders/ws5-baseline-models.md)
  — PR 2A row split out; PR 2B plan refined (3-class regime,
  new `market_features` family, multiclass evaluator).
- [`docs/AI-TRADERS-ROADMAP.md`](../AI-TRADERS-ROADMAP.md)
  — implementation order + ledger.
- This file.

## Acceptance

- [x] `BybitOffvmMarketRawAdapter._fetch_bars` returns canonical
  rows when called with a mocked exchange.
- [x] Pagination correctly advances `since` and respects `end`.
- [x] Pre-`start` bars are dropped (defensive against ccxt prefix).
- [x] Empty first page yields zero rows (no infinite loop).
- [x] Unknown timeframe + inverted window raise `ValueError`.
- [x] Stale-`since` defensive bump prevents infinite loops.
- [x] Env-gate retained: `ICT_OFFVM_BUILD_HOST=1` still required.
- [x] CI does not require `ccxt` (lazy import + monkeypatched
  `_build_exchange`).
- [x] Pre-existing `TestMarketRawBuilder::test_build_round_trip_via_csv`
  (broken on main) now green.
- [x] Full `tests/ml/` suite green (110 tests).

## Out of scope (filed for PR 2B)

- `market_features` derived family (rolling vol, log returns,
  3-class regime label).
- `RegimeClassifierTrainer` (per-bucket modal class).
- `MulticlassClassificationEvaluator`.
- `ml/configs/baseline-regime-classifier.yaml` manifest.
- Live exchange invocation by the operator on a non-VM build host.

## Hand-off

1. **Operator: stage Bybit read-only credentials on a non-VM build
   host** when ready to materialise actual `bybit_v5_offvm`
   datasets. Runbook: [`docs/ml/market-raw-adapters.md`](../ml/market-raw-adapters.md)
   § "Bybit off-VM build runbook".
2. **PR 2B** then lands the regime classifier baseline.

## Live runtime impact

None. All changes are under `ml/`. The Oracle live VM env-gate
(`ICT_OFFVM_BUILD_HOST=1`) is not weakened — the adapter still
refuses to run without the explicit env var, and the new fetch
path is reached only after the gate. Operator-hold paths
(`src/runtime/`, `src/units/accounts/`, `src/main.py`,
`config/accounts.yaml`, `deploy/*`) untouched.
