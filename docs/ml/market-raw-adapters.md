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

In S-AI-WS5-B-PART-1 the adapter ships:

- the class with the **WS9 env-gate** (refuses to run without
  the explicit opt-in env var);
- a `NotImplementedError` on the actual fetch path;
- this doc and a follow-up filing for the operator to wire the
  exchange call.

### Bybit off-VM wiring (filed for the operator)

When the operator next builds `market_raw` on a non-VM host:

1. Stage read-only Bybit V5 credentials on the build host (env or
   config file). **Do not** commit them.
2. Set `ICT_OFFVM_BUILD_HOST=1` on the same host. Verify the host
   is NOT the Oracle live VM.
3. Wire `BybitOffvmMarketRawAdapter._fetch_bars(...)` (a follow-up
   patch) to call `src/exchange/bybit_connector.py::get_klines`
   (or the equivalent kline endpoint), translate each candle into
   the canonical row shape, and yield.
4. Re-run
   `python -m ml.datasets build market_raw -- adapter=bybit_v5_offvm symbol=BTCUSDT timeframe=1h start=...`.

The wiring patch is the first thing in S-AI-WS5-B-PART-2.

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
