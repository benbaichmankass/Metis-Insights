"""P5 log-only shadow for vol_trail: writes rows, never changes the stop."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.runtime import trail_vol_shadow as tvs


def _df(ranges):
    rows = [{"timestamp": 1_700_000_000_000 + i * 7_200_000,
             "high": 100 + r / 2, "low": 100 - r / 2, "close": 100.0}
            for i, r in enumerate(ranges)]
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(tvs, "shadow_log_path", lambda: tmp_path / "s.jsonl")
    tvs._SEEN.clear()
    return tmp_path / "s.jsonl"


def _call(df, meta=None, label="ada_pullback_2h"):
    m = {"strategy_label": label}
    m.update(meta or {})
    return tvs.record_vol_trail_shadow(
        meta=m, cfg_dict={"atr_period": 14}, open_pkg={"order_package_id": "p1", "symbol": "ADAUSDT"},
        candles_df=df, window=df.tail(10), live_mult=5.0, atr=6.0, sl=90.0,
        current_price=110.0, direction="long")


def test_hot_bar_logs_shadow_tighter_than_live(_tmp):
    df = _df([0.5] * 230 + [12.0] * 30)
    row = _call(df)
    assert row["would_fire_closed"] is True and row["shadow_mult"] == 2.5
    assert row["stops_differ"] is True
    assert json.loads(_tmp.read_text().splitlines()[0])["live_mult"] == 5.0


def test_cold_bar_does_not_fire(_tmp):
    row = _call(_df([12.0] * 230 + [0.5] * 30))
    assert row["would_fire_closed"] is False and row["stops_differ"] is False


def test_dedup_other_leg_declared_and_short_frame(_tmp):
    df = _df([0.5] * 230 + [12.0] * 30)
    assert _call(df) is not None
    assert _call(df) is None  # same package + bar
    tvs._SEEN.clear()
    assert _call(df, label="eth_pullback_2h") is None
    assert _call(df, meta={"trail_vol_tight_mult": 2.5}) is None  # declared
    assert _call(df.head(50)) is None  # window unfilled


def test_never_raises_on_garbage():
    assert tvs.record_vol_trail_shadow(
        meta=None, cfg_dict=None, open_pkg=None, candles_df=None, window=None,
        live_mult=5.0, atr=1.0, sl=1.0, current_price=1.0, direction="long") is None
