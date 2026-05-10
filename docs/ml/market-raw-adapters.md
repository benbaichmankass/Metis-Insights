# `market_raw` source adapters

> **Status:** Canonical (adapter-framework scope). Adopted in
> **S-AI-WS5-B-PART-1** (2026-05-10).
>
> **Authority:** Subordinate to
> [`docs/architecture/ai-model-platform.md`](../architecture/ai-model-platform.md).
> Family roster: [`docs/data/dataset-taxonomy.md`](../data/dataset-taxonomy.md).
> Family schema: [`docs/data/dataset-schema.md`](../data/dataset-schema.md).

## Operator directive

> "We should have a running list of various sources to choose from
> — we should have capacity to intake different types from
> different sources and normalize it to the training-center
> format." — 2026-05-10.

This doc captures the framework that delivers on that directive.

## Canonical row shape

Every adapter normalises its upstream into the canonical
`market_raw` row:

| Field | Type | Notes |
|---|---|---|
| `ts` | str | ISO 8601 UTC timestamp of the bar. |
| `symbol` | str | e.g. `BTCUSDT`. |
| `timeframe` | str | `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. |
| `open`, `high`, `low`, `close` | float | OHLC prices. |
| `volume` | float | Base-units volume. `0.0` when unavailable. |
| `source` | str | Adapter name (`MarketRawAdapter.source`). |

Dataset metadata records the adapter used + its kwargs (in
`metadata.notes`) so the build is reproducible.

## Adapters shipping in S-AI-WS5-B-PART-1

### `csv` — [`CsvMarketRawAdapter`](../../ml/datasets/adapters/csv.py)

Reads a local CSV staged by the operator. **No network, no creds.**
Cleanest first adapter and the one used by tests.

Expected CSV columns (case-insensitive; extras ignored):
`ts, open, high, low, close[, volume]`. The `symbol` and
`timeframe` are passed via the build CLI because they describe
the file's scope, not the rows.

```
python -m ml.datasets build market_raw \
  --output-dir ./datasets-out --version v001 \
  --source ./bars-btcusdt-1h.csv \
  --symbol-scope BTCUSDT --timeframe 1h \
  -- adapter=csv csv_path=./bars-btcusdt-1h.csv symbol=BTCUSDT timeframe=1h
```

### `bybit_v5_offvm` — [`BybitOffvmMarketRawAdapter`](../../ml/datasets/adapters/bybit_offvm.py)

**Off-VM only.** Refuses to run unless
`ICT_OFFVM_BUILD_HOST=1` is set. The Oracle live VM must NEVER
set that env var; market_raw builds are meant to run on a
separate build host (developer laptop, HF Space, or a CI runner
with operator-supplied read-only credentials).

What the adapter ships:

- the class with the **WS9 env-gate** (refuses to run without
  the explicit opt-in env var); **S-AI-WS5-B-PART-1**.
- the live Bybit V5 fetch path via ccxt's `fetch_ohlcv` — paginated
  by `since` (ms) over `[start, end]`, normalised to the canonical
  row shape; tests mock the exchange object so CI never hits the
  network. **S-AI-WS5-B-PART-2 (PR 2A)**.

### Bybit off-VM build runbook (operator)

When the operator next builds `market_raw` on a non-VM host:

1. Verify the host is NOT the Oracle live VM. The env-gate is a
   tripwire, not a substitute for human judgement.
2. Stage read-only Bybit V5 credentials in the build-host env:
   - `BYBIT_API_KEY`, `BYBIT_API_SECRET` (read-only).
   - `BYBIT_TESTNET=true` to point at the sandbox.
   The klines endpoint is public so credentials are technically
   optional, but authenticated reads get higher rate limits and
   identifiable usage in Bybit's audit. **Do not** commit them.
3. Set `ICT_OFFVM_BUILD_HOST=1` on the same host.
4. `pip install ccxt` (lazy-imported by the adapter).
5. Run:

   ```
   python -m ml.datasets build market_raw \
     --output-dir ./datasets-out --version v001 \
     --source bybit_v5 \
     --symbol-scope BTCUSDT --timeframe 1h \
     -- adapter=bybit_v5_offvm \
        start=2024-01-01T00:00:00Z end=2025-01-01T00:00:00Z
   ```

   `symbol` and `timeframe` are auto-forwarded from
   `--symbol-scope` / `--timeframe` (S-AI-WS5-B-PART-2 PR 2A); pass
   them explicitly via `-- ...` only when you intentionally want to
   stamp rows differently from the path-layout scope.

### Implementation notes (PR 2A)

- ccxt is **lazy-imported** in `_build_exchange`. A build host
  without ccxt still hits the env-gate first; the import error is
  raised only when the gate has been opened.
- The exchange object is constructed via the `_build_exchange`
  classmethod. Tests monkeypatch this hook to inject a fake
  exchange, so no network or ccxt dependency is required for CI.
- Pagination: `fetch_ohlcv(symbol, timeframe=tf, since=cursor,
  limit=1000)`. Cursor advances to the last bar's `ts + bar_ms`.
  Defensive guards: drop pre-`start` bars (ccxt occasionally
  returns a small prefix), break if `since` doesn't advance, halt
  when a bar reaches `end_ms`.
- Supported timeframes: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. Other
  ccxt-supported timeframes are easy to add — extend `_TIMEFRAME_MS`
  and update this list.
- Volume normalisation: `None` is coerced to `0.0` (ccxt sometimes
  emits null volume on illiquid bars).

## Adding a new adapter

1. Subclass `MarketRawAdapter`. Set `source` to a stable token
   (e.g. `yfinance_daily`).
2. Implement `iter_bars(**kwargs) -> Iterator[Mapping]` yielding
   canonical rows.
3. Register the class in
   [`ml/datasets/adapters/registry.py`](../../ml/datasets/adapters/registry.py).
4. Update this doc (the table above) and the dataset taxonomy /
   schema.
5. Add a regression test under
   `tests/ml/datasets/test_market_raw_<adapter>.py` against a
   synthetic source fixture (no live network, no live creds).
6. If the adapter touches network or credentials, gate it behind
   an env var following the WS9 rule (the bybit adapter is the
   reference example).

## WS9 reminder

Heavy market-data pulls (full historical, multi-symbol,
multi-timeframe) MUST run off the Oracle live VM. The CSV adapter
is safe anywhere because the operator stages the file. Network
adapters (Bybit, future Binance / yfinance / ...) MUST carry an
env-gate that refuses to run on the live VM.
