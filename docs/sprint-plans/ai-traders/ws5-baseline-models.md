# WS5 — Baseline models

**Master plan:** [`docs/AI-TRADERS-ROADMAP.md`](../../AI-TRADERS-ROADMAP.md)
**Milestone:** M9
**Status:** 🔄 IN PROGRESS — sub-sprint A closed 2026-05-10.

## Decomposition

WS5 lands as per-baseline sub-sprints. Each:
1. Adds its dataset family builder if not yet buildable.
2. Adds trainer + evaluator (paired via `PREDICTOR_CLASS`).
3. Round-trips through the WS4 training-center harness.
4. Lands a manifest under `ml/configs/`.
5. Documents leakage discipline.

| Sub-sprint | Baseline | Dataset prereq | Status |
|---|---|---|---|
| **S-AI-WS5-A** | Outcome probability (per-strategy historical winrate) | `trade_outcomes` | ✅ DONE 2026-05-10 |
| S-AI-WS5-B | Regime classifier | `market_raw` | 📋 queued (see design note below) |
| S-AI-WS5-C | Setup quality scorer | `setup_labels` | 📋 queued |
| S-AI-WS5-D | Execution quality | `trade_outcomes` + execution metadata | 📋 queued |
| S-AI-WS5-E | Post-trade review | `review_journal` | 📋 queued |
| S-AI-WS5-F | Prop mission policy | `account_context` | 📋 queued |

## S-AI-WS5-A — Outcome probability (closed)

Closed 2026-05-10. Decision-useful question: "does the historical
win rate per strategy carry signal for the next closed trade?"

Paired sanity baseline shipped in S-AI-WS4-FU as
[`ml/configs/baseline-trade-outcome-global.yaml`](../../../ml/configs/baseline-trade-outcome-global.yaml)
— use `python -m ml compare` to test whether the per-strategy
feature beats the global mean.

Deliverables: `ml/datasets/families/trade_outcomes.py`,
`ml/trainers/per_strategy_winrate.py`,
`ml/evaluators/classification.py`,
`ml/configs/baseline-trade-outcome-winrate.yaml`,
plus tests + docs. See
[`docs/sprint-logs/S-AI-WS5-A.md`](../../sprint-logs/S-AI-WS5-A.md).

## S-AI-WS5-B — Regime classifier (design notes)

**Status: queued. Needs an operator-driven decision on
`market_raw` data acquisition.**

### `market_raw` multi-source design (operator directive 2026-05-10)

The operator's directive: "we should have a running list of various
sources to choose from — we should have capacity to intake different
types from different sources and normalize it to the training
center format."

Design sketch for the WS5-B builder:

- Builder is a **pluggable adapter framework**, not a single
  hard-coded source. Each adapter normalises a specific source
  into the canonical `market_raw` row shape.
- Adapter interface (proposed):
  ```python
  class MarketRawAdapter(ABC):
      source: ClassVar[str]   # e.g. "yfinance", "bybit_v5", "csv"
      timeframe_support: ClassVar[tuple[str, ...]]  # e.g. ("1d", "1h")
      @abstractmethod
      def iter_bars(self, **kwargs) -> Iterator[Mapping[str, Any]]: ...
  ```
- Canonical `market_raw` row shape (proposed):
  `{ts, symbol, timeframe, open, high, low, close, volume, source}`
  where `ts` is an ISO 8601 UTC string and `source` records the
  adapter that produced the row. Source-specific extra columns
  go into a sidecar `source_metadata.json` keyed by row id.
- First adapters to ship together:
  - `csv` — reads operator-staged CSVs (no network).
  - `yfinance` — free public source for daily / hourly bars.
  - `bybit_v5_offvm` — reuses the existing exchange connector but
    is hard-locked off the Oracle live VM (WS9 rule). Activated
    only when the build runs on an off-VM host with read-only
    API credentials.
- Each adapter records its name + version in dataset
  `metadata.notes`, plus the request parameters (symbol, range)
  so the dataset is reproducible.
- Leakage discipline: `market_raw` carries no labels (`label_version: n/a`,
  `leakage_test_status: n/a`). Downstream `setup_labels` /
  `regime_labels` builders that use `market_raw` are responsible
  for their own leakage tests.

When this lands, it follows the same dataset-builder pattern as
`backtest_results` and `trade_outcomes`; the difference is the
adapter dispatch instead of a single SQLite source.

## Acceptance (per baseline)

- [ ] Each baseline has a dataset, trainer, evaluator, summary.
- [ ] No advanced model family is introduced before a baseline
  exists for the same task.
- [ ] Each baseline produces decision-useful metrics, not only
  generic ML metrics.
