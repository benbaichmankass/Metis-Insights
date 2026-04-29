# Testing policy

## Test data sources (read first)

Tests and notebooks **must not** pull market data from Binance or any other
exchange that requires API keys, account auth, or geo-restricted endpoints.
This has been a repeated cause of notebook failures (HTTP 451, missing keys,
rate limits, region blocks).

Use one of these instead, in order of preference:

1. **Hand-crafted in-memory OHLCV** — fastest, deterministic, no network.
   ```python
   df = pd.DataFrame([...])  # columns: open, high, low, close, volume
   ```
2. **Repo fixtures** — small CSV/Parquet samples committed under
   `tests/fixtures/` or `data/samples/`. Reuse before adding new ones.
3. **Open, keyless public data sources** — only when real candles are
   required for a backtest or notebook demo. Approved sources:
   - Bybit public REST (`https://api.bybit.com/v5/market/kline`) — no key needed.
   - Coinbase Exchange public (`https://api.exchange.coinbase.com/products/<pair>/candles`).
   - Kraken public (`https://api.kraken.com/0/public/OHLC`).
   - CryptoCompare public histo endpoints.
   - yfinance for non-crypto reference series.
4. **Hugging Face datasets** — for larger historical pulls already mirrored
   to our HF org. See `docs/claude/huggingface-workflows.md`.

Rules:

- Never `import ccxt` against Binance in tests or notebook smoke cells.
- Never call `binance.com` / `api.binance.com` / `fapi.binance.com` from a
  test or a delegated Colab/HF notebook.
- A live Binance call is only acceptable inside the live-trading runtime on
  the Oracle VM, gated behind real secrets — never in CI, tests, or
  development notebooks.
- If a notebook fails because it tried to hit Binance, fix the data source
  before re-running. Do not paper over it with retries or VPN tricks.

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
