# Unit 1 — Strategies workflow

## Responsibility
Generate typed `OrderPackage` objects from market signals.
Never execute trades, never read account state.

## Entry point
```python
Coordinator.strategy_order_pkg(strategy, symbol, candles_df)
```
Delegates to `src/units/strategies/<strategy>.py::order_package(cfg, candles_df)`.

## Adding a strategy
1. Add entry to `config/units.yaml → units.strategies`
2. Create `src/units/strategies/<name>.py` with:
   ```python
   def order_package(cfg: dict, candles_df=None) -> dict:
       # must return: symbol, direction, entry, sl, tp
       # optional: confidence, meta
   ```
3. Add entry to `config/strategies.yaml` (service, model, signal_prefixes)
4. Add tests in `tests/test_s008_strategies.py` using hand-crafted DataFrames

## Shared helpers
`src/units/strategies/_base.py`:
- `side_to_direction(side)` — "buy"→"long", "sell"→"short"
- `last_close(df)` — last row close price
- `derive_sl_tp(entry, direction, sl_pct, reward_ratio)` — percentage-based fallback
- `require_candles(df, name)` — raises ValueError if None/empty

## Implemented strategies
| Name | Module | Signal source |
|------|--------|---------------|
| ict | `src/units/strategies/ict.py` | FVG + OB zones |
| vwap | `src/units/strategies/vwap.py` | VWAP deviation |
| breakout_confirmation | `src/units/strategies/breakout_confirmation.py` | ML + ATR |
| killzone | `src/units/strategies/killzone.py` | Session killzone |

## Rules
- `order_package()` must be pure — no side effects, no exchange calls
- Raise `ValueError` when signal is non-actionable (side="none", doji, etc.)
- Use `candles_df` passed in; never fetch data inside the function
