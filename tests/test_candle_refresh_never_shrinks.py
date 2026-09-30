"""Plants the PI-20260929-FCJRWVAK-0002 defect: a 90-day pull over a
multi-year file must be refused; a refresh must only ever extend."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_backtest_candles as fbc  # noqa: E402
import refresh_candle_history as rch  # noqa: E402


def _frame(start, n):
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    px = 100.0 + pd.Series(range(n)).mul(0.37).mod(5).values  # moving series (MI-157 guard)
    return pd.DataFrame({"timestamp": ts, "open": px, "high": px + 0.1,
                         "low": px - 0.1, "close": px, "volume": 1.0})


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


# ---- review round (manager, 2026-09-30) ---------------------------------
from datetime import datetime, timezone  # noqa: E402


def _write(path, fmt_naive=False, n=90):
    df = _frame("2026-06-18 00:00", n)
    df["timestamp"] = [t.strftime("%Y-%m-%d %H:%M:%S") if fmt_naive else t.isoformat(sep=" ")
                       for t in df["timestamp"]]
    df.to_csv(path, index=False)


def _fake(monkeypatch, start, n):
    seen = {}
    def fake(sym, iv, s, e):
        seen["end_ms"] = e
        ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
        return [{"timestamp": t.to_pydatetime(), "open": 100.0 + i % 4, "high": 105.0, "low": 99.0,
                 "close": 100.0 + i % 4, "volume": 2.0}
                for i, t in enumerate(ts) if int(t.timestamp() * 1000) < e]
    monkeypatch.setattr(fbc, "fetch_klines", fake)
    return seen


def test_forming_bar_never_fetched_or_written(tmp_path, monkeypatch):
    p = tmp_path / "SOLUSDT_15m.csv"
    _write(p)
    seen = _fake(monkeypatch, "2026-06-19 07:00", 72)  # source would emit bars past 'now'
    now = datetime(2026, 6, 19, 23, 37, tzinfo=timezone.utc)
    rch.refresh_one(p, "SOLUSDT", "15", now)
    last = pd.read_csv(p)["timestamp"].iloc[-1]
    assert pd.Timestamp(last) == pd.Timestamp("2026-06-19 23:15", tz="UTC")  # 23:30 is forming
    assert seen["end_ms"] == int(datetime(2026, 6, 19, 23, 30, tzinfo=timezone.utc).timestamp() * 1000)


def test_naive_timestamp_file_stays_loadable(tmp_path, monkeypatch):
    p = tmp_path / "SOLUSDT_15m.csv"
    _write(p, fmt_naive=True, n=90)
    _fake(monkeypatch, "2026-06-19 00:00", 8)
    rch.refresh_one(p, "SOLUSDT", "15", datetime(2026, 6, 19, 6, 0, tzinfo=timezone.utc))
    df = pd.read_csv(p)
    assert len(df) > 90 and "+" not in df["timestamp"].iloc[-1]
    import candle_io
    assert len(candle_io.load_candles(str(p))) == len(df)


def test_mismatched_format_is_caught_and_original_untouched(tmp_path, monkeypatch):
    p = tmp_path / "SOLUSDT_15m.csv"
    _write(p, n=90)
    before = p.read_bytes()
    _fake(monkeypatch, "2026-06-19 00:00", 8)
    monkeypatch.setattr(rch, "ts_formatter", lambda s: (lambda t: t.strftime("%d/%m/%Y %H:%M")))
    try:
        rch.refresh_one(p, "SOLUSDT", "15", datetime(2026, 6, 19, 6, 0, tzinfo=timezone.utc))
        raise AssertionError("must refuse")
    except RuntimeError:
        pass
    assert p.read_bytes() == before and not (tmp_path / "SOLUSDT_15m.csv.tmp").exists()


def test_guard_fails_closed_on_unreadable_or_headerless(tmp_path):
    new = _frame("2024-01-01", 10)
    bad = tmp_path / "a.csv"
    bad.write_text("not,a,candle\n1,2,3\n")          # no timestamp column
    assert fbc.shrink_refusal(bad, new) is not None
    junk = tmp_path / "b.csv"
    junk.write_bytes(b"\x00\xff\xfe garbage")         # unreadable
    assert fbc.shrink_refusal(junk, new) is not None
    empty = tmp_path / "c.csv"
    empty.write_bytes(b"")                            # zero-byte: safe to replace
    assert fbc.shrink_refusal(empty, new) is None


def test_rate_limit_retries_are_bounded(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"retCode": 10006}
    monkeypatch.setattr(fbc.requests, "get", lambda *a, **k: R())
    slept = []
    monkeypatch.setattr(fbc.time, "sleep", lambda s: slept.append(s))
    try:
        fbc.fetch_klines("SOLUSDT", "15", 0, 10**13)
        raise AssertionError("must raise")
    except RuntimeError as exc:
        assert "10006" in str(exc)
    assert len(slept) == fbc._RATE_LIMIT_MAX_RETRIES


def test_flag_renamed_and_window_caller_passes_it():
    assert "--allow-window-replace" in (Path(__file__).resolve().parents[1] /
        "scripts/ops/vwap_backtest_sweep_action.sh").read_text()
