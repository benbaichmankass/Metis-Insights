# WS5 — Baseline models

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 🔄 IN PROGRESS — sub-sprints A + B-Part-1 closed 2026-05-10.

## Decomposition

WS5 lands as per-baseline sub-sprints. Larger baselines (WS5-B)
further decompose into Part-1 (data prereq) + Part-2 (the
classifier).

| Sub-sprint | Baseline | Dataset prereq | Status |
|---|---|---|---|
| **S-AI-WS5-A** | Outcome probability (per-strategy historical winrate) | `trade_outcomes` | ✅ DONE 2026-05-10 |
| **S-AI-WS5-B-PART-1** | `market_raw` multi-source adapter framework + CSV adapter + Bybit off-VM scaffold | (this sprint adds the prereq builder) | ✅ DONE 2026-05-10 |
| S-AI-WS5-B-PART-2 | Regime classifier baseline + Bybit off-VM fetch wiring | `market_raw` (✅ buildable via CSV; off-VM bybit scaffold pending operator wiring) | 🔜 next |
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

## S-AI-WS5-B-PART-2 — Regime classifier (queued)

Next sub-sprint. Plan:

1. Wire `BybitOffvmMarketRawAdapter._fetch_bars(...)` (operator;
   needs read-only Bybit V5 creds + a non-VM build host).
2. Build a `market_features` family that derives realized
   volatility + (optional) other rolling stats from
   `market_raw`.
3. Add `RegimeClassifierTrainer` (simplest baseline: predict the
   modal regime; or per-feature mean using lagged volatility
   quantile) + matching evaluator (multi-class extension of
   `ClassificationEvaluator`, OR keep binary high/low-vol for
   simplicity).
4. Manifest under `ml/configs/baseline-regime-classifier.yaml`.
5. Round-trip tests against a CSV-built `market_raw` fixture.

## Acceptance (per baseline)

- [ ] Each baseline has a dataset, trainer, evaluator, summary.
- [ ] No advanced model family is introduced before a baseline
  exists for the same task.
- [ ] Each baseline produces decision-useful metrics, not only
  generic ML metrics.
