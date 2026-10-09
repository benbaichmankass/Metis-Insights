"""VOLTRAIL-FETCH-FIX — the exit monitor must fetch enough bars for the
vol_trail ATR percentile to be computable at the last CLOSED bar.

The defect (PR #17212 P5 parity, measured on ada_pullback_2h): the monitor
fetched ``limit=200`` for every leg, ``trail_vol`` drops the forming bar,
199 < ``vol_pctl_window`` 200, and the lever silently returned the base mult —
56 of 76 shadow rows had ``atr_pctl_closed=null``. These tests pin:

1. A planted 200-bar frame ending on a forming bar is ``insufficient_bars``
   (base mult, and SAID — a WARNING), while a ``monitor_fetch_limit``-sized
   frame yields a closed percentile and the declared lever fires.
2. ``monitor_fetch_limit`` is 200 for every leg without a vol_trail need, so
   their fetch is byte-identical.
3. The fetcher passes the per-leg limit, including for an IB-routed client,
   and IB-routed legs without vol_trail stay at 200.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import pytest

import src.main as main_module
from src.runtime import trail_vol
from src.runtime.trail_vol import (
    MONITOR_BASE_LIMIT,
    monitor_fetch_limit,
    resolve_vol_trail_mult,
)
from src.runtime.trail_vol_shadow import SHADOW_STRATEGY

TF = "2h"
TF_S = 7200
HOT_CFG = {"trail_vol_above_pctl": 0.90, "trail_vol_tight_mult": 2.5,
           "vol_pctl_window": 200, "atr_period": 14, "timeframe": TF}


def _frame(n_closed: int, *, forming: bool = True) -> pd.DataFrame:
    """``n_closed`` closed 2h bars (calm body, hot tail ⇒ last closed ATR in
    the top decile) plus, optionally, one still-forming bar opened 30m ago."""
    ranges = [0.5] * (n_closed - 20) + [12.0] * 20
    now = int(time.time())
    last_open = now - 1800 if forming else now - TF_S - 60
    n = n_closed + (1 if forming else 0)
    opens = [last_open - (n - 1 - i) * TF_S for i in range(n)]
    if forming:
        ranges = ranges + [0.1]  # forming bar: partial, tiny range
    rows = [{"timestamp": opens[i] * 1000, "open": 100.0,
             "high": 100 + r / 2, "low": 100 - r / 2, "close": 100.0,
             "volume": 1.0} for i, r in enumerate(ranges)]
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _no_soak_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.runtime.exit_lever_soak.record_exit_lever_annotation",
        lambda **kw: None,
    )
    trail_vol._INSUFFICIENT_LOGGED.clear()


def test_planted_199_closed_bars_is_insufficient_and_logged(caplog):
    # The live shape before the fix: limit=200 with the last row forming.
    df = _frame(199, forming=True)
    assert len(df) == 200
    pkg = {"order_package_id": "pkg-199", "symbol": "ADAUSDT"}
    with caplog.at_level(logging.WARNING, logger="src.runtime.trail_vol"):
        out = resolve_vol_trail_mult({}, HOT_CFG, df, 4.0, "long", pkg)
        resolve_vol_trail_mult({}, HOT_CFG, df, 4.0, "long", pkg)
    assert out == 4.0
    hits = [r for r in caplog.records if "insufficient_bars" in r.getMessage()]
    assert len(hits) == 1  # once per package, not once per pass


def test_fixed_limit_yields_closed_percentile_and_fires():
    limit = monitor_fetch_limit("anything", HOT_CFG)
    assert limit == 215
    df = _frame(limit - 1, forming=True)
    assert len(df) == limit
    assert resolve_vol_trail_mult({}, HOT_CFG, df, 4.0, "long",
                                  {"order_package_id": "pkg-ok"}) == 2.5


def test_shadow_row_reads_ok_at_fixed_limit(monkeypatch, tmp_path):
    from src.runtime import trail_vol_shadow as tvs

    monkeypatch.setattr(tvs, "shadow_log_path", lambda: tmp_path / "s.jsonl")
    tvs._SEEN.clear()
    cfg = {"atr_period": 14, "timeframe": TF}
    common = dict(meta={"strategy_label": SHADOW_STRATEGY}, cfg_dict=cfg,
                  window=None, live_mult=4.0, atr=1.0, sl=90.0,
                  current_price=100.0, direction="long")
    old = tvs.record_vol_trail_shadow(
        open_pkg={"order_package_id": "a"}, candles_df=_frame(199), **common)
    assert old["atr_pctl_closed"] is None
    assert old["atr_pctl_closed_read_state"] == "insufficient_bars"
    lim = monitor_fetch_limit(SHADOW_STRATEGY, cfg)
    new = tvs.record_vol_trail_shadow(
        open_pkg={"order_package_id": "b"}, candles_df=_frame(lim - 1), **common)
    assert new["atr_pctl_closed"] is not None
    assert new["atr_pctl_closed_read_state"] == "ok"


@pytest.mark.parametrize("cfg", [
    {},
    {"timeframe": "1h", "atr_period": 14},
    {"trail_vol_tight_mult": 2.5},                       # no tail ⇒ undeclared
    {"trail_vol_above_pctl": 0.8},                       # no tight ⇒ undeclared
    {"trail_vol_above_pctl": 0.8, "trail_vol_tight_mult": 0},
])
def test_legs_without_vol_trail_keep_200(cfg):
    assert monitor_fetch_limit("trend_donchian_eth", cfg) == MONITOR_BASE_LIMIT


def test_limit_scales_with_declared_window_and_period():
    cfg = {"trail_vol_below_pctl": 0.1, "trail_vol_tight_mult": 2.5,
           "vol_pctl_window": 300, "atr_period": 20}
    assert monitor_fetch_limit("x", cfg) == 300 + 20 + 1
    assert monitor_fetch_limit(SHADOW_STRATEGY, {}) == 215
    # Garbage never raises and never shrinks below the old limit.
    assert monitor_fetch_limit("x", {"trail_vol_tight_mult": 2.5,
                                     "trail_vol_above_pctl": 0.9,
                                     "vol_pctl_window": "bad"}) == 215


def _spy_fetcher(monkeypatch, client, strategies):
    monkeypatch.setattr("src.units.strategies.load_strategy_config",
                        lambda: strategies)
    monkeypatch.setattr("src.runtime.market_data.connector_for_symbol",
                        lambda symbol, settings: client)
    seen = []

    def _spy(symbol, timeframe, *, settings=None, exchange_client=None, limit):
        seen.append((symbol, limit))
        return pd.DataFrame()

    monkeypatch.setattr("src.runtime.market_data.fetch_candles", _spy)
    return main_module._build_monitor_ohlcv_fetcher({}), seen


def test_fetcher_passes_per_leg_limit(monkeypatch):
    fetcher, seen = _spy_fetcher(monkeypatch, object(), {
        SHADOW_STRATEGY: {"timeframe": "2h"},
        "trend_donchian_eth": {"timeframe": "1h"},
        "armed_leg": dict(HOT_CFG),
    })
    fetcher("ADAUSDT", None, SHADOW_STRATEGY)
    fetcher("ETHUSDT", None, "trend_donchian_eth")
    fetcher("SOLUSDT", None, "armed_leg")
    fetcher("BTCUSDT", "1h", "not_in_yaml")
    assert seen == [("ADAUSDT", 215), ("ETHUSDT", 200),
                    ("SOLUSDT", 215), ("BTCUSDT", 200)]


def test_ib_routed_leg_without_vol_trail_stays_at_200(monkeypatch):
    from src.exchange.ib_connector import IBMarketData

    ib_client = IBMarketData.__new__(IBMarketData)
    monkeypatch.setattr("src.exchange.ib_connector.consume_queue_timeout",
                        lambda: False)
    fetcher, seen = _spy_fetcher(monkeypatch, ib_client, {
        "mes_trend": {"timeframe": "1h"},
    })
    fetcher("MES", None, "mes_trend")
    assert seen == [("MES", 200)]


def test_ib_duration_for_fixed_limit_is_bounded():
    # An IB-routed vol_trail leg would request 215 bars; the duration string
    # scales with it and stays a day-count well inside IB's historical limits.
    from src.exchange.ib_connector import _duration_str

    assert _duration_str("2h", 215).endswith(" D")
    assert int(_duration_str("2h", 215).split()[0]) <= 30
