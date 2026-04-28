# Supported Strategies

Set via the `STRATEGY` environment variable.

| `STRATEGY` value    | Builder function                  | Description                                                        |
|---------------------|-----------------------------------|--------------------------------------------------------------------|
| `killzone`          | `killzone_signal_builder`         | Default. ICT kill-zone scalper using FVG + order-block detection.  |
| `vwap`              | `vwap_signal_builder`             | VWAP mean-reversion on 5 m candles.                               |
| `breakout`          | `breakout_model_signal_builder`   | ML breakout-confirmation model (XGBoost).                          |
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
2. Register it in `_STRATEGY_BUILDERS` in `src/runtime/pipeline.py`.
3. Optionally add its key to `STRATEGIES` to include it in multiplexed mode.
4. Add a doc file under `docs/strategies/`.
