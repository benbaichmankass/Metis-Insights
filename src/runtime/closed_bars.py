"""Trim the still-forming bar off a candle frame — live scoring sees CLOSED bars.

Every ML training row (``market_features``, ``forecast_features``, the E0
exit-head rows) is a CLOSED bar. The live fetch (``market_data.fetch_candles``
on Bybit) returns the current forming bar as its last row, so a live feature
computed on that row is a partial-bar value the model never trained on —
train/serve skew. ``exit_head_shadow.py`` found and trimmed this for itself
on 2026-07-12; the code audit of 2026-09-27 found the same gap at three more
call sites (CA-B01-regime-scoring-uses-forming-bar,
CA-B01-fc-producer-forecasts-forming-bar). This is the one shared rule so a
fourth call site does not re-implement it.

A bar is forming when ``open_ts + timeframe_seconds > now``. ``timestamp`` is
the bar's OPEN time (Bybit kline start). Duck-typed: accepts a pandas
DataFrame (live), any frame whose ``["timestamp"]`` yields a list and whose
rows can be sliced (``iloc`` or a ``_rows`` list — the regime tests'
``_FrameLike``), or a list of row mappings keyed ``timestamp`` / ``ts`` /
``time`` (the forecast producer's records). An unknown timeframe or an unparsable timestamp returns the
frame unchanged: trimming needs certainty, and a spurious trim would drop a
closed bar.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Mapping

#: Bar length in seconds per timeframe string.
TF_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400,
}


def _epoch_seconds(value: Any) -> float | None:
    """Best-effort epoch seconds for a bar timestamp (ms int, s int, ISO str,
    datetime / pandas Timestamp). ``None`` when it cannot be read."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v != v:  # NaN
            return None
        return v / 1000.0 if v > 1e11 else v
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    to_py = getattr(value, "to_pydatetime", None)  # pandas Timestamp
    if callable(to_py):
        try:
            return _epoch_seconds(to_py())
        except Exception:  # noqa: BLE001 — NaT etc.
            return None
    if isinstance(value, str):
        s = value.strip()
        try:
            return _epoch_seconds(float(s))
        except ValueError:
            pass
        try:
            return _epoch_seconds(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _last_timestamp(candles: Any) -> Any:
    if isinstance(candles, (list, tuple)):  # list of row mappings
        if not candles or not isinstance(candles[-1], Mapping):
            return None
        last = candles[-1]
        for key in ("timestamp", "ts", "time"):
            if last.get(key) is not None:
                return last[key]
        return None
    try:
        col = candles["timestamp"]
    except Exception:  # noqa: BLE001
        return None
    try:
        values = col.tolist()
    except AttributeError:
        try:
            values = list(col)
        except TypeError:
            return None
    return values[-1] if values else None


def last_bar_is_forming(candles: Any, timeframe: str, *, now: float | None = None) -> bool:
    """True when the frame's last bar has not closed yet at *now* (epoch s)."""
    tf_s = TF_SECONDS.get(str(timeframe or ""))
    if not tf_s or candles is None:
        return False
    open_s = _epoch_seconds(_last_timestamp(candles))
    if open_s is None:
        return False
    now_s = time.time() if now is None else float(now)
    return open_s + tf_s > now_s


def drop_forming_bar(candles: Any, timeframe: str, *, now: float | None = None) -> Any:
    """*candles* without its last row when that row is a still-forming bar.

    Returns the input object unchanged when nothing is trimmed (unknown
    timeframe, unreadable timestamp, or the last bar already closed).
    """
    if not last_bar_is_forming(candles, timeframe, now=now):
        return candles
    iloc = getattr(candles, "iloc", None)
    if iloc is not None:
        return iloc[:-1]
    rows = getattr(candles, "_rows", None)
    if isinstance(rows, list):
        return type(candles)(rows[:-1])
    if isinstance(candles, (list, tuple)):
        return candles[:-1]
    return candles


__all__ = ["TF_SECONDS", "drop_forming_bar", "last_bar_is_forming"]
