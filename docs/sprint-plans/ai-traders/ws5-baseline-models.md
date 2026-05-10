# WS5 — Baseline models

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 🔄 IN PROGRESS — sub-sprints A + B-Part-1 + B-Part-2 PR 2A closed 2026-05-10.

## Decomposition

WS5 lands as per-baseline sub-sprints. Larger baselines (WS5-B)
further decompose into Part-1 (data prereq) + Part-2 (the
classifier).

| Sub-sprint | Baseline | Dataset prereq | Status |
|---|---|---|---|
| **S-AI-WS5-A** | Outcome probability (per-strategy historical winrate) | `trade_outcomes` | ✅ DONE 2026-05-10 |
| **S-AI-WS5-B-PART-1** | `market_raw` multi-source adapter framework + CSV adapter + Bybit off-VM scaffold | (this sprint adds the prereq builder) | ✅ DONE 2026-05-10 |
| S-AI-WS5-B-PART-2 PR 2A | Bybit off-VM `_fetch_bars` wiring (ccxt) | `market_raw` (now buildable via CSV + Bybit off-VM) | ✅ DONE 2026-05-10 |
| S-AI-WS5-B-PART-2 PR 2B | 3-class regime classifier + `market_features` family + multiclass evaluator | `market_features` (this sprint adds it) | 🔜 next |
| S-AI-WS5-C | Setup quality scorer | `setup_labels` | 📋 queued |
| S-AI-WS5-D | Execution quality | `trade_outcomes` + execution metadata | 📋 queued |
| S-AI-WS5-E | Post-trade review | `review_journal` | 📋 queued |
| S-AI-WS5-F | Prop mission policy | `account_context` | 📋 queued |

## S-AI-WS5-A — Outcome probability (closed)

Closed 2026-05-10. See
[`docs/sprint-logs/S-AI-WS5-A.md`](../../sprint-logs/S-AI-WS5-A.md).

Paired sanity baseline
[`baseline-trade-outcome-global.yaml`](../../../ml/configs/baseline-trade-outcome-global.yaml)
shipped in S-AI-WS4-FU.

## S-AI-WS5-B-PART-1 — `market_raw` adapter framework (closed)

Closed 2026-05-10. Pluggable upstream-source adapters that
normalise into the canonical `market_raw` row shape.

Deliverables:
- [`ml/datasets/adapters/`](../../../ml/datasets/adapters/) —
  `MarketRawAdapter` ABC + canonical row + adapter registry.
- `CsvMarketRawAdapter` — reads operator-staged CSVs (no network,
  no creds; the test/local adapter).
- `BybitOffvmMarketRawAdapter` — **off-VM only.** Refuses to run
  unless `ICT_OFFVM_BUILD_HOST=1` is set; the actual exchange
  call is a documented `NotImplementedError` filed for the
  operator to wire next.
- [`ml/datasets/families/market_raw.py`](../../../ml/datasets/families/market_raw.py)
  — builder dispatching to adapters by name.
- [`tests/ml/datasets/test_market_raw.py`](../../../tests/ml/datasets/test_market_raw.py)
  — CSV round-trip + bybit env-gate + registry inclusion + env-var
  name pin.
- Docs:
  [`docs/ml/market-raw-adapters.md`](../../ml/market-raw-adapters.md) +
  taxonomy + schema updates + sprint log.

The operator picked off-VM bybit as the first concrete adapter
(2026-05-10). The framework + env-gate ship in this sprint; the
actual exchange call is filed for the operator to wire on a
non-VM host with read-only credentials. See
[`docs/ml/market-raw-adapters.md`](../../ml/market-raw-adapters.md)
§ “Bybit off-VM wiring”.

## S-AI-WS5-B-PART-2 PR 2A — Bybit off-VM fetch wiring (closed)

Closed 2026-05-10. Operator chose to split Part 2 into two PRs to
keep each reviewable in isolation. PR 2A wires only the
`BybitOffvmMarketRawAdapter._fetch_bars(...)` path; the classifier
baseline lands in PR 2B.

Deliverables:

- [`ml/datasets/adapters/bybit_offvm.py`](../../../ml/datasets/adapters/bybit_offvm.py)
  — live ccxt fetch path. Lazy-imports `ccxt`; constructs a
  `ccxt.bybit` client via the `_build_exchange` classmethod (tests
  monkeypatch this hook to inject a fake exchange). Pagination by
  `since` (ms) over `[start, end]`; bar-length-in-ms map covers
  `1m`/`5m`/`15m`/`1h`/`4h`/`1d`. Defensive guards: drop pre-`start`
  bars; break on a stale `since`; halt at `end_ms`.
- [`ml/datasets/builder.py`](../../../ml/datasets/builder.py) +
  [`ml/datasets/families/market_raw.py`](../../../ml/datasets/families/market_raw.py)
  — `DatasetBuilder.build` auto-forwards `symbol_scope` /
  `timeframe` into `iter_rows` kwargs (operator-supplied wins via
  `setdefault`). `MarketRawBuilder.iter_rows` translates them into
  the adapter's `symbol` / `timeframe` defaults so the operator
  doesn't have to pass scope twice. Other builders ignore the extra
  kwargs via their existing `**_: Any` catchall.
- [`tests/ml/datasets/test_market_raw.py`](../../../tests/ml/datasets/test_market_raw.py)
  — adds `_FakeBybitExchange` + 7 wiring tests covering pagination,
  end-window cutoff, empty pages, unknown timeframes, inverted
  windows, canonical row shape, credential threading
  (env / explicit-kwarg). Also fixes a pre-existing test bug
  (`TestMarketRawBuilder::test_build_round_trip_via_csv` was
  missing `timeframe` in the adapter kwargs — now auto-forwarded).
- [`docs/ml/market-raw-adapters.md`](../../ml/market-raw-adapters.md)
  — Bybit section refreshed: build runbook + implementation notes
  + ccxt's `fetch_ohlcv` semantics; "filed for operator" replaced
  with "wired in PR 2A".
- This sprint plan + roadmap + ai-model-platform.md change log.

The env-gate (`ICT_OFFVM_BUILD_HOST=1`) was NOT weakened. Live VM
is unaffected; the adapter still refuses to run without the env
var. The non-negotiable from PART-1 stands.

## S-AI-WS5-B-PART-2 PR 2B — Regime classifier baseline (queued)

Next sub-sprint. Plan:

1. Add [`ml/datasets/families/market_features.py`](../../../ml/datasets/families/market_features.py)
   — derived family taking a `market_raw` dataset path (or rows)
   and emitting per-bar features (`log_return`,
   `rolling_log_return_vol_N`, `vol_bucket`) plus a 3-class
   `regime_label` ∈ {`trend`, `range`, `volatile`}. Operator picked
   "new family" over "extend market_raw" — keeps `market_raw`
   canonical OHLCV-only.
2. Add `ml/trainers/regime_classifier.py` — simplest baseline:
   per-bucket modal class. Pairs with a new
   `PerBucketModeMulticlassPredictor`.
3. Add `ml/evaluators/multiclass_classification.py` — multi-class
   accuracy + per-class precision/recall/f1 + macro/weighted f1 +
   `n_eval`. Reuses the predictor-resolution machinery in
   `Evaluator._resolve_predictor`.
4. Add `ml/configs/baseline-regime-classifier.yaml` with
   `split_strategy: time_aware_holdout` (time-series).
5. CSV-built `market_raw` → `market_features` → trainer → evaluator
   round-trip test using a synthetic OHLCV fixture.
6. Leakage discipline doc: features cannot include forward-looking
   derivatives of the regime label; same WS9 rule extends to the
   new family.

## Acceptance (per baseline)

- [ ] Each baseline has a dataset, trainer, evaluator, summary.
- [ ] No advanced model family is introduced before a baseline
  exists for the same task.
- [ ] Each baseline produces decision-useful metrics, not only
  generic ML metrics.
