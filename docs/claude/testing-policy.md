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

## Sandbox network egress (S-015 BUG-015)

When Claude Code runs in the **anthropic-managed sandbox** (the
default environment for autonomous sessions), the egress gateway is
allowlisted to:

```
✓ pypi.org, files.pythonhosted.org   (pip)
✓ github.com, raw.githubusercontent.com, objects.githubusercontent.com
✗ api.bybit.com, api.coinbase.com, api.kraken.com,
✗ query1.finance.yahoo.com, min-api.cryptocompare.com,
✗ api.coingecko.com, api.coinpaprika.com,
✗ huggingface.co, datasets-server.huggingface.co,
✗ kaggle.com, archive.org, data-api.binance.vision,
✗ everything else (incl. Anthropic's own console.anthropic.com)
```

Probed exhaustively in S-015 T0 / S-015 post-clarification re-probe.
Even with `curl -k` (insecure mode) the keyless market-data hosts
return HTTP 403 — this is a **proxy allowlist**, not a TLS issue.

### Implications

- Real-time market-data fetches (any exchange API, even keyless) FAIL
  in autonomous sandbox sessions. `scripts/sprint015/data_sources.py`
  raises `DataUnavailableError` from any tier-1/2 adapter.
- The **github-raw adapter** (S-015 PR #207) is the only working
  source from the sandbox. Currently exposes `coinmetrics/data` daily
  BTC + ETH from 2010 onwards. Hard-rule: only daily timeframes;
  sub-daily requests return None so daily reference rates can't
  masquerade as 5m / 15m bars.
- `git clone` against arbitrary github repos works — useful for
  pre-staging fixture data via repos under our control.

### What to do when you need real intraday data

For 5m / 15m / 1m bars — i.e. anything matching the live trader's
timeframe — the autonomous sandbox cannot fetch them. Choose one:

1. **Run on the Oracle VM** — the VM has unrestricted egress and
   already-vetted credentials. The `/vm` Telegram dispatcher
   (S-014.5) can launch a Claude session there. Tier 2 mutations
   (e.g. `pip install`) need real-time operator confirmation in
   Telegram.
2. **Pre-stage data manually** — operator downloads from kaggle /
   polygon / alpaca on a personal box, commits via `git lfs`. One-off,
   no recurring pipeline.
3. **Self-host an intraday mirror** — VM-side scheduled job mirrors
   Coinbase / Kraken 5m to a public github repo. Harness adapter
   targets that mirror.

This note is the canonical place to document this — earlier sprints
re-discovered it via a 30-host probe each time. If you're a Claude
session reading this, the answer is "not from this sandbox".
