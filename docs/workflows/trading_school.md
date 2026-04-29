# Unit 7 — Trading School workflow

## Responsibility
Validate live strategy performance and trigger backtests / model retraining.
Gate strategy updates — a strategy with degrading metrics does not get promoted.

## Entry points
```python
Coordinator.validate_strategy_update(strategy, metrics, thresholds=None)
# → {"ok": bool, "strategy": str, "metrics": dict, "issues": list[str]}

Coordinator.trigger_backtest(strategy, config=None)
# → raises NotImplementedError until PR #126 wires Colab pipeline
```

## Validation thresholds
Default thresholds (overrideable in `units.yaml → trading_school.thresholds`):

| Metric | Default | YAML key |
|--------|---------|----------|
| Win rate | ≥ 40% | `min_win_rate` |
| Profit factor | ≥ 1.0 | `min_profit_factor` |
| Max drawdown | ≤ 30% | `max_drawdown_pct` |
| Min trades | ≥ 5 | `min_trades` |

Per-call overrides via `thresholds` argument take precedence over YAML.

## Validation flow
1. Caller (e.g. scheduled job) collects live metrics dict
2. `Coordinator.validate_strategy_update(strategy, metrics)`
3. If `ok=True` → proceed with strategy update / model promotion
4. If `ok=False` → log issues, push alert, skip update

## Backtest trigger flow (planned PR #126)
1. `Coordinator.trigger_backtest(strategy)` → Colab notebook API call
2. Colab runs backtest, writes results to HF Hub / `backtest_results` DB table
3. Results fed back to `validate_strategy_update()` for gating

## Rules
- Validator is stateless — no side effects, no DB writes
- Always push an alert when validation fails for a live strategy
- `trigger_backtest()` must not block the calling thread (async/fire-and-forget)
