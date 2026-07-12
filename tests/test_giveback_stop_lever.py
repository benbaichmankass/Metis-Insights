"""M20 giveback-stop — trend_donchian monitor() lever + annotate soak.

Mirrors tests/test_stale_stop_lever.py for the giveback lever (the
fleet-sweep-validated "grab the PnL" R-lock: close once the trade has
SEEN >= giveback_min_mfe_r of open profit and given back >= giveback_r
from that peak — harness reference scripts/research/backtest_trend.py):
  * YAML-declared (BOTH ``giveback_min_mfe_r`` + ``giveback_r`` in meta)
    ⇒ a real ``giveback_stop`` close, only when the peak was seen AND the
    giveback threshold is met at bar close.
  * Undeclared ⇒ behaviour unchanged (no close), but a would-fire trade
    writes ONE observe-only annotate row to exit_lever_soak.jsonl.
  * Fail-safe skips: never-profitable trade, small retrace, missing
    entry_time/risk, unrestrictable candle window.
  * order_package() threads declared cfg params into meta.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.units.strategies import trend_donchian as td


def _candles(n: int, price: float = 100.0, start: datetime | None = None,
             tf_minutes: int = 60) -> pd.DataFrame:
    start = start or datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=tf_minutes * i)
        rows.append({"timestamp": ts.isoformat(), "open": price,
                     "high": price * 1.001, "low": price * 0.999,
                     "close": price, "volume": 1.0})
    return pd.DataFrame(rows)


def _pkg(entry: float = 100.0, sl: float = 95.0, direction: str = "long",
         entry_bar: int = 5, df: pd.DataFrame | None = None,
         min_mfe_r: float | None = None, giveback_r: float | None = None,
         pkg_id: str = "pkg-gb-1") -> dict:
    df = df if df is not None else _candles(30)
    meta = {
        "atr": 1.0, "atr_period": 14, "trail_mult": 50.0,  # trail far away
        "risk_per_unit": abs(entry - sl),
        "entry_time": df["timestamp"].iloc[entry_bar],
        "timeframe": "1h",
    }
    if min_mfe_r is not None:
        meta["giveback_min_mfe_r"] = min_mfe_r
    if giveback_r is not None:
        meta["giveback_r"] = giveback_r
    tp = entry * 3 if direction == "long" else entry / 3
    return {"symbol": "USO", "direction": direction, "entry": entry,
            "sl": sl, "tp": tp, "meta": meta,
            "order_package_id": pkg_id}


@pytest.fixture(autouse=True)
def _isolated_soak(tmp_path, monkeypatch):
    from src.runtime import exit_lever_soak

    monkeypatch.setattr(exit_lever_soak, "soak_log_path",
                        lambda: tmp_path / "exit_lever_soak.jsonl")
    exit_lever_soak._ANNOTATED.clear()
    yield tmp_path


def _spike_peak(df: pd.DataFrame, bar: int, high: float) -> None:
    df.loc[df.index[bar], "high"] = high


def test_declared_giveback_fires_after_peak_and_retrace():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    _spike_peak(df, 15, 106.0)          # peak_r = 1.2 (risk 5)
    df.loc[df.index[-1], "close"] = 100.5  # r_close = 0.1 → giveback 1.1
    verdict = td.monitor({}, df, pkg)
    assert verdict == {"action": "close", "reason": "giveback_stop",
                       "exit_price": 100.5}


def test_declared_giveback_fires_for_short():
    df = _candles(30)
    pkg = _pkg(entry=100.0, sl=105.0, direction="short",
               entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    df.loc[df.index[15], "low"] = 94.0     # peak_r = 1.2 (risk 5)
    df.loc[df.index[-1], "close"] = 99.5   # r_close = 0.1 → giveback 1.1
    verdict = td.monitor({}, df, pkg)
    assert verdict == {"action": "close", "reason": "giveback_stop",
                       "exit_price": 99.5}


def test_declared_skips_when_peak_never_reached_min_mfe():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    _spike_peak(df, 15, 103.0)             # peak_r = 0.6 < 1.0
    df.loc[df.index[-1], "close"] = 99.0   # deep retrace, but no qualifying peak
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict)
                and verdict.get("reason") == "giveback_stop")


def test_declared_skips_small_retrace():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    _spike_peak(df, 15, 106.0)             # peak_r = 1.2
    df.loc[df.index[-1], "close"] = 104.0  # r_close = 0.8 → giveback 0.4 < 1
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict)
                and verdict.get("reason") == "giveback_stop")


def test_pre_entry_peak_never_counts():
    """A pre-entry spike must not fake the MFE peak — the window is
    since-entry only."""
    df = _candles(30)
    pkg = _pkg(entry_bar=10, min_mfe_r=1.0, giveback_r=1.0, df=df)
    _spike_peak(df, 3, 110.0)              # BEFORE entry — must be ignored
    df.loc[df.index[-1], "close"] = 100.5
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict)
                and verdict.get("reason") == "giveback_stop")


def test_undeclared_annotates_but_never_closes(_isolated_soak):
    df = _candles(30)
    pkg = _pkg(entry_bar=5, df=df)         # no declared params
    _spike_peak(df, 15, 106.0)
    df.loc[df.index[-1], "close"] = 100.5
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict)
                and verdict.get("reason") == "giveback_stop")
    log = _isolated_soak / "exit_lever_soak.jsonl"
    assert log.exists()
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    gb_rows = [r for r in rows if r["lever"] == "giveback_stop"]
    assert len(gb_rows) == 1
    assert gb_rows[0]["mode"] == "annotate"
    assert gb_rows[0]["params"] == {"giveback_min_mfe_r": 1.0,
                                    "giveback_r": 1.0}
    assert gb_rows[0]["state"]["peak_r"] >= 1.0
    # second tick: deduped, still one giveback row
    td.monitor({}, df, pkg)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len([r for r in rows if r["lever"] == "giveback_stop"]) == 1


def test_missing_entry_time_or_risk_is_fail_safe(_isolated_soak):
    df = _candles(30)
    _spike_peak(df, 15, 106.0)
    df.loc[df.index[-1], "close"] = 100.5
    pkg = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    pkg["meta"].pop("entry_time")
    v = td.monitor({}, df, pkg)
    assert not (isinstance(v, dict) and v.get("reason") == "giveback_stop")
    pkg2 = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    pkg2["meta"]["risk_per_unit"] = 0
    v2 = td.monitor({}, df, pkg2)
    assert not (isinstance(v2, dict) and v2.get("reason") == "giveback_stop")
    log = _isolated_soak / "exit_lever_soak.jsonl"
    if log.exists():
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert not [r for r in rows if r["lever"] == "giveback_stop"]


def test_sl_cross_still_wins_over_giveback():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    _spike_peak(df, 15, 106.0)
    df.loc[df.index[-1], "close"] = 94.0   # below sl=95
    verdict = td.monitor({}, df, pkg)
    assert verdict["reason"] == "sl_cross"


def test_stale_stop_precedes_giveback_when_both_fire():
    """Harness exit precedence: stale is checked before giveback."""
    df = _candles(30)
    pkg = _pkg(entry_bar=5, min_mfe_r=1.0, giveback_r=1.0, df=df)
    pkg["meta"]["stale_exit_bars"] = 8
    pkg["meta"]["stale_exit_below_r"] = 0.5
    _spike_peak(df, 15, 106.0)             # giveback qualifies (peak 1.2R)
    df.loc[df.index[-1], "close"] = 100.5  # r_close 0.1 < 0.5 → stale fires too
    verdict = td.monitor({}, df, pkg)
    assert verdict["reason"] == "stale_stop"


def test_order_package_threads_declared_params():
    n = 40
    df = _candles(n, price=100.0)
    df.loc[df.index[-1], "close"] = 110.0
    df.loc[df.index[-1], "high"] = 110.5
    cfg = {"symbol": "USO", "giveback_min_mfe_r": 1.0, "giveback_r": 1.0,
           "min_confidence": 0.0}
    pkg = td.order_package(cfg, df)
    assert pkg["meta"]["giveback_min_mfe_r"] == 1.0
    assert pkg["meta"]["giveback_r"] == 1.0


def test_order_package_omits_params_when_undeclared():
    n = 40
    df = _candles(n, price=100.0)
    df.loc[df.index[-1], "close"] = 110.0
    df.loc[df.index[-1], "high"] = 110.5
    pkg = td.order_package({"symbol": "USO", "min_confidence": 0.0}, df)
    assert "giveback_min_mfe_r" not in pkg["meta"]
    assert "giveback_r" not in pkg["meta"]
