#!/usr/bin/env python3
"""Publish the latest live TSFM quantile-forecast row per symbol (M19 Track-1 PR 1a).

Tier-1 **trainer-side** tooling. For each requested live symbol it fetches the
trailing close window from the SAME candle path the signal builders use
(`src.runtime.market_data.connector_for_symbol` + `fetch_candles`), runs the
frozen `amazon/chronos-bolt-tiny` quantile FORECASTER over it, and writes the
most-recent bar's scale-free `fc_*` feature row to a trainer-mirror artifact
(`runtime_logs/trainer_mirror/forecasts/<SYMBOL>.json`). `publish_trainer_mirror.sh`
rsyncs that dir to the live VM so a FUTURE live reader (PR 1b) can serve the row.

### The parity contract

The published `fc_*` block MUST match the offline `market_features` forecast
block bit-for-bit for the same bar, because BOTH sides import the SAME constants
(`DEFAULT_CONTEXT_LEN` / `DEFAULT_STRIDE` / `DEFAULT_MIN_CONTEXT` /
`DEFAULT_HORIZON` / `FORECAST_QUANTILES` / `FORECAST_MODEL_ID`) and call the SAME
pure fn (`ml.datasets.forecast_features.compute_forecast_feature_rows`). This
producer never re-derives or copies any of those — it imports them. The only
producer-local logic is the candle→`{ts, close}` shaping (`_candles_to_rows`) +
taking the most-recent row (`latest_forecast_row`), both covered by the parity
test in `tests/ml/test_publish_live_forecasts.py`. **This PR does NOT touch the
live money-box** (no live reader yet — that's PR 1b).

### Optional trainer-side deps + import safety

Running with the REAL forecaster needs `chronos-forecasting` + `torch`
(`pip install -r requirements-backtest.txt`), guarded by `forecast_available()`
exactly like `build_forecasts.py`. The MODULE itself imports WITHOUT torch —
and without pandas — at import time: `forecast_features` is stdlib-only, and the
pandas-importing `src.runtime.market_data` is imported **lazily** inside the
fetch path, so `import scripts.ml.publish_live_forecasts` succeeds in a bare CI
env (proven by the import-discipline test).

Run on the trainer VM:

    python -m scripts.ml.publish_live_forecasts \
      --symbols BTCUSDT,ETHUSDT --timeframe 15m --context-len 64 --horizon 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.datasets.forecast_features import (  # noqa: E402
    DEFAULT_CONTEXT_LEN,
    DEFAULT_HORIZON,
    DEFAULT_MIN_CONTEXT,
    DEFAULT_STRIDE,
    FORECAST_FEATURE_COLUMNS,
    FORECAST_MODEL_ID,
    FORECAST_QUANTILES,
    chronos_forecast_fn,
    compute_forecast_feature_rows,
    forecast_available,
)

# Where the per-symbol artifacts land (trainer-mirror dir; rsynced to the live
# VM by scripts/ops/publish_trainer_mirror.sh under the same `forecasts/` name).
DEFAULT_FORECASTS_DIR = (
    Path(__file__).resolve().parents[2] / "runtime_logs" / "trainer_mirror" / "forecasts"
)

# A candle's timestamp may be keyed differently by source (the `fetch_candles`
# DataFrame uses `timestamp`; a market_raw row uses `ts`). Read whichever is
# present — the value is only ever a sort key + an opaque as-of marker.
_TIME_KEYS: tuple[str, ...] = ("ts", "timestamp", "time")

Candle = Mapping[str, Any]
ForecastFn = Callable[
    [Sequence[Sequence[float]], int, Sequence[float]], list[dict[float, float]]
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _candle_time_value(candle: Candle) -> Any:
    for key in _TIME_KEYS:
        if key in candle and candle[key] is not None:
            return candle[key]
    return None


def _candles_to_rows(candles: Sequence[Candle]) -> list[dict[str, Any]]:
    """Shape fetched candles into the ``{ts, close}`` rows the pure fn consumes.

    Mirrors the offline `market_raw` shape (`ts` + `close`) so the forecast
    computed here is identical to the one the offline `market_features` build
    would compute from the same closes — the parity contract. Candles missing a
    usable close are dropped (they can't form a window anchor).
    """
    rows: list[dict[str, Any]] = []
    for c in candles:
        close = c.get("close")
        if close is None:
            continue
        ts = _candle_time_value(c)
        rows.append({"ts": "" if ts is None else str(ts), "close": close})
    return rows


def latest_forecast_row(
    candles: Sequence[Candle],
    *,
    forecast_fn: ForecastFn,
    context_len: int = DEFAULT_CONTEXT_LEN,
    stride: int = DEFAULT_STRIDE,
    min_context: int = DEFAULT_MIN_CONTEXT,
    horizon: int = DEFAULT_HORIZON,
    quantile_levels: Sequence[float] = FORECAST_QUANTILES,
) -> dict[str, Any] | None:
    """Compute the fc_* rows for *candles* and return the MOST-RECENT one.

    The shared core the CLI and the parity test both call. It shapes the candles
    (`_candles_to_rows`) then delegates to the SAME pure
    :func:`compute_forecast_feature_rows` the offline producer uses, so the row
    returned here is bit-for-bit what the offline build computes for that bar.
    `_strided_indices` always includes the last bar, so the returned row is the
    latest bar's forecast (or ``None`` when there is too little history to emit).
    """
    rows = _candles_to_rows(candles)
    out = compute_forecast_feature_rows(
        rows,
        context_len=context_len,
        stride=stride,
        min_context=min_context,
        forecast_fn=forecast_fn,
        horizon=horizon,
        quantile_levels=quantile_levels,
    )
    if not out:
        return None
    return out[-1]


def build_forecast_artifact(
    symbol: str,
    timeframe: str,
    candles: Sequence[Candle],
    *,
    forecast_fn: ForecastFn,
    model_id: str = FORECAST_MODEL_ID,
    context_len: int = DEFAULT_CONTEXT_LEN,
    stride: int = DEFAULT_STRIDE,
    min_context: int = DEFAULT_MIN_CONTEXT,
    horizon: int = DEFAULT_HORIZON,
    quantile_levels: Sequence[float] = FORECAST_QUANTILES,
) -> dict[str, Any] | None:
    """Assemble the serializable artifact for one symbol (``None`` if no row)."""
    row = latest_forecast_row(
        candles,
        forecast_fn=forecast_fn,
        context_len=context_len,
        stride=stride,
        min_context=min_context,
        horizon=horizon,
        quantile_levels=quantile_levels,
    )
    if row is None:
        return None
    fc_row = {col: row[col] for col in FORECAST_FEATURE_COLUMNS}
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": _iso_now(),
        "context_len": context_len,
        "horizon": horizon,
        "min_context": min_context,
        "quantile_levels": list(quantile_levels),
        "model_id": model_id,
        "feature_columns": list(FORECAST_FEATURE_COLUMNS),
        "fc_row": fc_row,
        "as_of_ts": row.get("ts"),
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write *payload* to *path* atomically (temp file in the same dir + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_forecast_artifact(
    out_dir: Path,
    symbol: str,
    timeframe: str,
    candles: Sequence[Candle],
    *,
    forecast_fn: ForecastFn,
    model_id: str = FORECAST_MODEL_ID,
    context_len: int = DEFAULT_CONTEXT_LEN,
    stride: int = DEFAULT_STRIDE,
    min_context: int = DEFAULT_MIN_CONTEXT,
    horizon: int = DEFAULT_HORIZON,
    quantile_levels: Sequence[float] = FORECAST_QUANTILES,
) -> Path | None:
    """Build + atomically write ``<out_dir>/<SYMBOL>.json``; ``None`` if no row."""
    artifact = build_forecast_artifact(
        symbol,
        timeframe,
        candles,
        forecast_fn=forecast_fn,
        model_id=model_id,
        context_len=context_len,
        stride=stride,
        min_context=min_context,
        horizon=horizon,
        quantile_levels=quantile_levels,
    )
    if artifact is None:
        return None
    path = Path(out_dir) / f"{symbol}.json"
    _atomic_write_json(path, artifact)
    return path


def _fetch_candles(
    symbol: str,
    timeframe: str,
    *,
    limit: int,
    settings: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch the trailing candle window for *symbol* as a list of dict rows.

    LAZY import of `src.runtime.market_data` (it pulls in pandas) so the module
    stays importable in a bare env — the import-discipline guarantee.
    """
    from src.runtime.market_data import connector_for_symbol, fetch_candles

    resolved = dict(settings or {})
    client = connector_for_symbol(symbol, resolved)
    df = fetch_candles(
        symbol,
        timeframe,
        settings=resolved,
        limit=limit,
        exchange_client=client,
    )
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated live symbols (e.g. BTCUSDT,ETHUSDT).",
    )
    ap.add_argument("--timeframe", default="15m", help="Candle timeframe (default 15m).")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_FORECASTS_DIR,
        help="Output dir for the per-symbol artifacts.",
    )
    ap.add_argument("--model-id", default=FORECAST_MODEL_ID)
    ap.add_argument("--context-len", type=int, default=DEFAULT_CONTEXT_LEN)
    ap.add_argument("--stride", type=int, default=DEFAULT_STRIDE)
    ap.add_argument("--min-context", type=int, default=DEFAULT_MIN_CONTEXT)
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON,
                    help="how many bars ahead to forecast (default 1 = next bar).")
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Bars to fetch (default context_len + 8, a small buffer).",
    )
    args = ap.parse_args(argv)

    if not forecast_available():
        ap.error(
            "TSFM forecast deps not installed. On the trainer VM run "
            "`pip install -r requirements-backtest.txt` (chronos-forecasting + torch)."
        )

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        ap.error("--symbols resolved to an empty list")

    limit = args.limit if args.limit is not None else args.context_len + 8
    forecast_fn = chronos_forecast_fn(args.model_id)

    results: list[dict[str, Any]] = []
    written = 0
    for symbol in symbols:
        entry: dict[str, Any] = {"symbol": symbol}
        try:
            candles = _fetch_candles(symbol, args.timeframe, limit=limit)
            if not candles:
                entry["status"] = "no_candles"
                results.append(entry)
                continue
            path = write_forecast_artifact(
                args.out_dir,
                symbol,
                args.timeframe,
                candles,
                forecast_fn=forecast_fn,
                model_id=args.model_id,
                context_len=args.context_len,
                stride=args.stride,
                min_context=args.min_context,
                horizon=args.horizon,
            )
            if path is None:
                entry["status"] = "insufficient_history"
            else:
                entry["status"] = "written"
                entry["path"] = str(path)
                written += 1
        except Exception as exc:  # noqa: BLE001 - per-symbol fail-permissive
            entry["status"] = "error"
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)

    print(json.dumps(
        {"timeframe": args.timeframe, "written": written, "symbols": results},
        sort_keys=True,
    ))
    # Non-zero only when NOTHING was published (a total failure); a partial run
    # where at least one symbol wrote is a success (the rest can catch up).
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
