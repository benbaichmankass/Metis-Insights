"""Offline tests for the Binance-vision fallback in fetch_backtest_candles.

Network is never touched: `_download_vision_zip` is monkeypatched to serve
synthetic in-memory batches. Validates the parser (ms/µs disambiguation, header
skipping), interval mapping, month enumeration, and the monthly→daily fallback +
range filtering + dedup in `fetch_klines_binance_vision`.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "fetch_backtest_candles",
    Path(__file__).resolve().parent.parent / "scripts" / "ops" / "fetch_backtest_candles.py",
)
fbc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fbc)


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def test_interval_mapping_covers_workflow_codes():
    # The research-panel-build workflow emits these Bybit codes.
    for code, label in [("5", "5m"), ("15", "15m"), ("60", "1h"),
                        ("120", "2h"), ("240", "4h"), ("D", "1d")]:
        assert fbc._BYBIT_TO_BINANCE_INTERVAL[code] == label


def test_parse_row_milliseconds():
    row = fbc._parse_vision_kline_row(
        ["1595030400000", "9000.0", "9100.0", "8900.0", "9050.0", "12.5", "x"]
    )
    assert row is not None
    assert row["_ts_ms"] == 1595030400000
    assert row["open"] == 9000.0 and row["close"] == 9050.0
    assert row["timestamp"].year == 2020


def test_parse_row_microseconds_downscaled():
    # 2025-era vision files stamp open_time in microseconds.
    us = 1595030400000 * 1000
    row = fbc._parse_vision_kline_row(
        [str(us), "1", "2", "0.5", "1.5", "3", "x"]
    )
    assert row is not None
    assert row["_ts_ms"] == 1595030400000  # scaled back to ms


def test_parse_row_header_returns_none():
    assert fbc._parse_vision_kline_row(
        ["open_time", "open", "high", "low", "close", "volume"]
    ) is None
    assert fbc._parse_vision_kline_row([]) is None


def test_months_in_range_spans_year_boundary():
    months = fbc._months_in_range(
        datetime(2025, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 15, tzinfo=timezone.utc),
    )
    assert months == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_download_vision_zip_parses_real_zip(monkeypatch):
    # Build a real one-member zip like Binance ships and feed it through requests.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        csv_text = io.StringIO()
        w = csv.writer(csv_text)
        w.writerow([_ms(2024, 1, 1), 1, 2, 0.5, 1.5, 10, "x"])
        w.writerow([_ms(2024, 1, 1, 0, 5), 1.5, 2.5, 1, 2, 11, "x"])
        zf.writestr("BTCUSDT-5m-2024-01-01.csv", csv_text.getvalue())
    payload = buf.getvalue()

    class _Resp:
        status_code = 200
        content = payload

        def raise_for_status(self):
            pass

    monkeypatch.setattr(fbc.requests, "get", lambda *a, **k: _Resp())
    rows = fbc._download_vision_zip("https://example/x.zip")
    assert len(rows) == 2
    assert rows[0]["open"] == 1.0 and rows[1]["close"] == 2.0


def test_fetch_vision_monthly_then_daily_fallback_and_filter(monkeypatch):
    # Monthly present for 2024-01; monthly 404 for 2024-02 -> daily fallback.
    jan = [
        {"timestamp": None, "open": 1, "high": 1, "low": 1, "close": 1,
         "volume": 1, "_ts_ms": _ms(2024, 1, 10)},
        {"timestamp": None, "open": 2, "high": 2, "low": 2, "close": 2,
         "volume": 2, "_ts_ms": _ms(2024, 1, 20)},
        # out of range (before start) — must be filtered out
        {"timestamp": None, "open": 9, "high": 9, "low": 9, "close": 9,
         "volume": 9, "_ts_ms": _ms(2024, 1, 1)},
    ]
    feb_day = {
        "timestamp": None, "open": 3, "high": 3, "low": 3, "close": 3,
        "volume": 3, "_ts_ms": _ms(2024, 2, 5),
    }

    def fake_download(url: str):
        if "monthly" in url and "2024-01" in url:
            return list(jan)
        if "monthly" in url and "2024-02" in url:
            return None  # not published -> daily fallback
        if "daily" in url and "2024-02-05" in url:
            return [dict(feb_day)]
        if "daily" in url:
            return []  # other Feb days empty
        return None

    monkeypatch.setattr(fbc, "_download_vision_zip", fake_download)
    rows = fbc.fetch_klines_binance_vision(
        "BTCUSDT", "5", _ms(2024, 1, 5), _ms(2024, 2, 6)
    )
    ms_kept = [r for r in rows]
    # Jan-1 row filtered (before start); Jan-10/Jan-20 + Feb-5 kept, sorted.
    assert len(ms_kept) == 3
    assert all("_ts_ms" not in r for r in ms_kept)  # scratch key stripped
    closes = [r["close"] for r in ms_kept]
    assert closes == [1, 2, 3]  # oldest-first
