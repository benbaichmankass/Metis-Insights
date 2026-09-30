"""Plants the PI-20260929-FCJRWVAK-0002 defect: a 90-day pull over a
multi-year file must be refused; a refresh must only ever extend."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import fetch_backtest_candles as fbc  # noqa: E402
import refresh_candle_history as rch  # noqa: E402


def _frame(start, n):
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.0,
                         "low": 1.0, "close": 1.0, "volume": 1.0})


def test_shorter_replacement_refused(tmp_path):
    p = tmp_path / "SOLUSDT_15m.csv"
    _frame("2024-01-01", 1000).to_csv(p, index=False)
    assert fbc.shrink_refusal(p, _frame("2024-01-05", 200)) is not None


def test_longer_or_equal_replacement_allowed(tmp_path):
    p = tmp_path / "SOLUSDT_15m.csv"
    _frame("2024-01-01", 1000).to_csv(p, index=False)
    assert fbc.shrink_refusal(p, _frame("2024-01-01", 1200)) is None
    assert fbc.shrink_refusal(tmp_path / "absent.csv", _frame("2024-01-01", 5)) is None


def test_main_refuses_and_leaves_file(tmp_path, monkeypatch):
    p = tmp_path / "SOLUSDT_15m.csv"
    _frame("2024-01-01", 1000).to_csv(p, index=False)
    before = p.read_bytes()
    short = _frame("2024-01-05", 50).to_dict("records")
    monkeypatch.setattr(fbc, "fetch_klines_binance_vision", lambda *a, **k: short)
    rc = fbc.main(["prog", "--symbol", "SOLUSDT", "--interval", "15", "--source",
                   "binance_vision", "--days", "3", "--output", str(p)])
    assert rc == 3 and p.read_bytes() == before


def test_merge_extend_appends_only_after_last():
    old = _frame("2024-01-01", 100)
    new = _frame("2024-01-01 12:00", 100)  # overlaps + extends
    out = rch.merge_extend(old, new)
    assert len(out) > len(old) and out["timestamp"].is_unique
    assert out.iloc[:100].equals(old)
