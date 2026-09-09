# Supported Strategies

> **Doc status:** `unknown` · category `architecture` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Set via the `STRATEGY` environment variable.

| `STRATEGY` value    | Builder function                  | Description                                                        |
|---------------------|-----------------------------------|--------------------------------------------------------------------|
| `killzone`          | `killzone_signal_builder`         | Default. ICT kill-zone scalper using FVG + order-block detection.  |
| `vwap`              | `vwap_signal_builder`             | VWAP mean-reversion on 5 m candles.                               |
| `breakout`          | `breakout_model_signal_builder`   | ML breakout-confirmation model (XGBoost).                          |
| `ict_scalp_5m`      | `ict_scalp_signal_builder`        | ICT scalp v2 on 5 m — liquidity sweep + displacement + FVG wick-rejection + HTF bias filter. Live since 2026-05-14 (PR #1156, post pre-live gate); see `docs/strategies/ict_scalp_5m.md`. |
| `fvg_range_15m`     | `fvg_range_15m_signal_builder`    | FVG mean-reversion inside a confirmed STATIC horizontal range on 15 m — the deliberate opposite of `ict_scalp_5m` (reversion, not continuation). `execution: shadow` (data-only) since 2026-05-30; see `docs/audits/fvg-range-complement-2026-05-30.md`. |
| `multiplexed`       | `multiplexed_signal_builder`      | Tries strategies in `STRATEGIES` order; first actionable wins.     |

## Multiplexed mode

When `STRATEGY=multiplexed` the pipeline iterates `STRATEGIES` (defined in
`src/runtime/pipeline.py`) and returns the signal from the first strategy that
produces an actionable (`buy` or `sell`) result.

```python
STRATEGIES = ["breakout_confirmation", "vwap"]
```

Rules:
- **First actionable wins** — remaining strategies are not called.
- **Per-strategy sizing** — each strategy provides its own `qty`; quantities are never summed.
- **Fault-tolerant** — a strategy that raises an exception is logged and skipped; the next strategy is tried.

To change priority order, edit `STRATEGIES` in `src/runtime/pipeline.py`.

## Adding a new strategy

1. Write a builder function with signature `(settings: dict) -> dict` that returns
   `{symbol, side, qty, meta}`.
2. Register it in **`src/runtime/intent_multiplexer.py::_default_intent_builders`** —
   the ONE builder roster. ⚠️ This step used to read *"register it in
   `_STRATEGY_BUILDERS` in `src/runtime/pipeline.py`"*, a SECOND registry that
   nothing kept in step with the first; by 2026-09-09 it held 16 of 55
   strategies and, because `MULTI_STRATEGY_INTENT_LAYER=false` routes to it, the
   documented rollback had become a 78% capability outage (audit F-28). It is
   deleted — `pipeline.strategy_builders()` reads the roster above, and
   `tests/test_strategy_builder_registry_single_home.py` fails any re-split.
3. Optionally add its key to `STRATEGIES` to include it in multiplexed mode.
4. Add a doc file under `docs/strategies/`.
