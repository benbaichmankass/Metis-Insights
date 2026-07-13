"""M20 conditional stale-stop — trend_donchian monitor() lever + annotate soak.

Covers the operator-approved Tier-3 rollout contract
(docs/research/M20-exit-refinement-2026-07-12.md § 5):
  * YAML-declared (`stale_exit_bars` in meta) ⇒ a real `stale_stop` close,
    only when the position is old enough AND still below the threshold.
  * Undeclared ⇒ behaviour unchanged (no close), but a would-fire trade
    writes ONE observe-only annotate row to exit_lever_soak.jsonl.
  * Fail-safe skips: young trade, positive trade, missing entry_time/risk.
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
         declared_bars: int | None = None, below_r: float | None = None,
         pkg_id: str = "pkg-1") -> dict:
    df = df if df is not None else _candles(30)
    meta = {
        "atr": 1.0, "atr_period": 14, "trail_mult": 50.0,  # trail far away
        "risk_per_unit": abs(entry - sl),
        "entry_time": df["timestamp"].iloc[entry_bar],
        "timeframe": "1h",
    }
    if declared_bars is not None:
        meta["stale_exit_bars"] = declared_bars
    if below_r is not None:
        meta["stale_exit_below_r"] = below_r
    return {"symbol": "SOLUSDT", "direction": direction, "entry": entry,
            "sl": sl, "tp": entry * 3, "meta": meta,
            "order_package_id": pkg_id}


@pytest.fixture(autouse=True)
def _isolated_soak(tmp_path, monkeypatch):
    from src.runtime import exit_lever_soak

    monkeypatch.setattr(exit_lever_soak, "soak_log_path",
                        lambda: tmp_path / "exit_lever_soak.jsonl")
    exit_lever_soak._ANNOTATED.clear()
    yield tmp_path


def test_declared_stale_stop_fires_on_old_flat_trade():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, declared_bars=8, below_r=0.0, df=df)
    # 24 bars after entry, price back at entry*0.999 → open_r < 0
    df.loc[df.index[-1], "close"] = 99.9
    verdict = td.monitor({}, df, pkg)
    assert verdict == {"action": "close", "reason": "stale_stop",
                       "exit_price": 99.9}


def test_declared_stale_stop_skips_young_trade():
    df = _candles(30)
    pkg = _pkg(entry_bar=25, declared_bars=8, df=df)  # only ~4 bars old
    df.loc[df.index[-1], "close"] = 99.9
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict) and verdict.get("reason") == "stale_stop")


def test_declared_stale_stop_skips_winning_trade():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, declared_bars=8, below_r=0.0, df=df)
    df.loc[df.index[-1], "close"] = 103.0  # +0.6R — above threshold
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict) and verdict.get("reason") == "stale_stop")


def test_undeclared_annotates_but_never_closes(_isolated_soak):
    df = _candles(30)
    pkg = _pkg(entry_bar=5, df=df)  # no declared params
    df.loc[df.index[-1], "close"] = 99.9
    verdict = td.monitor({}, df, pkg)
    assert not (isinstance(verdict, dict) and verdict.get("reason") == "stale_stop")
    log = _isolated_soak / "exit_lever_soak.jsonl"
    assert log.exists()
    # filter to THIS lever's rows — other annotate-only levers (e.g. the P4.1
    # trail-decay reference cell) may legitimately fire on the same fixture
    rows = [r for r in (json.loads(line)
                        for line in log.read_text().splitlines())
            if r["lever"] == "stale_stop"]
    assert len(rows) == 1
    assert rows[0]["mode"] == "annotate"
    assert rows[0]["params"] == {"stale_exit_bars": 8, "stale_exit_below_r": 0.0}
    assert rows[0]["state"]["open_r"] < 0
    # second tick: deduped, still one row
    td.monitor({}, df, pkg)
    rows = [r for r in (json.loads(line)
                        for line in log.read_text().splitlines())
            if r["lever"] == "stale_stop"]
    assert len(rows) == 1


def test_missing_entry_time_or_risk_is_fail_safe(_isolated_soak):
    df = _candles(30)
    pkg = _pkg(entry_bar=5, declared_bars=8, df=df)
    df.loc[df.index[-1], "close"] = 99.9
    pkg["meta"].pop("entry_time")
    assert td.monitor({}, df, pkg) is None or \
        td.monitor({}, df, pkg).get("reason") != "stale_stop"
    pkg2 = _pkg(entry_bar=5, declared_bars=8, df=df)
    pkg2["meta"]["risk_per_unit"] = 0
    v2 = td.monitor({}, df, pkg2)
    assert not (isinstance(v2, dict) and v2.get("reason") == "stale_stop")
    log = _isolated_soak / "exit_lever_soak.jsonl"
    assert not log.exists()


def test_sl_cross_still_wins_over_stale_stop():
    df = _candles(30)
    pkg = _pkg(entry_bar=5, declared_bars=8, below_r=0.0, df=df)
    df.loc[df.index[-1], "close"] = 94.0  # below sl=95
    verdict = td.monitor({}, df, pkg)
    assert verdict["reason"] == "sl_cross"


def test_order_package_threads_declared_params(monkeypatch):
    # Build a breakout frame the strategy accepts, with cfg declaring params.
    n = 40
    df = _candles(n, price=100.0)
    # force a breakout on the last bar
    df.loc[df.index[-1], "close"] = 110.0
    df.loc[df.index[-1], "high"] = 110.5
    cfg = {"symbol": "SOLUSDT", "stale_exit_bars": 8,
           "stale_exit_below_r": 0.0, "min_confidence": 0.0}
    pkg = td.order_package(cfg, df)
    assert pkg["meta"]["stale_exit_bars"] == 8
    assert pkg["meta"]["stale_exit_below_r"] == 0.0


def test_order_package_omits_params_when_undeclared():
    n = 40
    df = _candles(n, price=100.0)
    df.loc[df.index[-1], "close"] = 110.0
    df.loc[df.index[-1], "high"] = 110.5
    pkg = td.order_package({"symbol": "SOLUSDT", "min_confidence": 0.0}, df)
    assert "stale_exit_bars" not in pkg["meta"]
