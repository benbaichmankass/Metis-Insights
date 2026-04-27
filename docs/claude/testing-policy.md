# Testing policy

## Local checks

```bash
PYTHONPATH=. pytest --collect-only -q tests
PYTHONPATH=. pytest -q tests
python scripts/secret_scan.py
```

## Remote checks

Delegate these unless explicitly requested locally:

- Full backtests.
- Large data validation.
- Training sessions.
- Live exchange smoke tests.

## VWAP strategy tests

VWAP tests are fully offline. Use fake OHLCV DataFrames — never call a live exchange:

```python
df = pd.DataFrame([...])  # hand-crafted candles
signal = build_vwap_signal(df, symbol="BTCUSDT", qty=1.0)
```

Run focused:
```bash
PYTHONPATH=. pytest tests/test_vwap_strategy.py -q
```

## Missing dependencies

If tests fail from missing optional packages, report the exact package and do not silently install broad dependency sets.
