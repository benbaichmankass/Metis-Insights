"""PREVBAR (PI-20261003-LRP-PREVBAR): at the US open a session leg must decide on
THIS session's bar, never on the previous session's.

2026-09-28 (real data, ``tests/fixtures/slv_gdx_session_open_2026-09-28.json``):
the first fetch after the 13:30Z open returned 1d frames ending in Friday
09-25's bar, and the candle cache served that frame to the next two ticks.
``slv_pullback_1d`` / ``gdx_pullback_1d`` printed three buys each at Friday's
close (58.14 / 92.89, adx 11.673 / 15.2446). The day's own bar did not support
them, and live_replay_parity.py reports them as ``live_only_signal``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import src.runtime.market_data as md
import src.runtime.strategy_signal_builders as sb

FIX = json.loads((Path(__file__).parent / "fixtures"
                  / "slv_gdx_session_open_2026-09-28.json").read_text())
MON = FIX["monday_open_utc"]              # 2026-09-28T04:00Z (Alpaca 1d bar open)
T_OPEN_TICK = 1790602273                  # 2026-09-28T13:31:13Z — live's first eval


def _df(rows):
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df


def _friday_frame(sym):
    return _df([r for r in FIX[f"{sym}|1d"] if r[0] < MON][-200:])


def _monday_frame(sym):
    """Friday history + Monday's bar as it stood in the 13:30Z 15m candle."""
    t, o, h, low, c, v = FIX[f"{sym}|15m_1330"][0]
    hist = [r for r in FIX[f"{sym}|1d"] if r[0] < MON][-199:]
    return _df(hist + [[MON, o, h, low, c, v]])


class _Venue:
    """Serves ``frames`` in order (the last one repeats); counts calls."""
    def __init__(self, *frames):
        self.frames, self.calls = list(frames), 0

    def get_ohlcv(self, symbol, timeframe, limit=200):
        f = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return f.copy()


@pytest.fixture(autouse=True)
def _clean():
    md.reset_candle_cache()
    getattr(sb, "_SESSION_BAR_LOGGED", {}).clear()
    yield
    md.reset_candle_cache()


# ------------------------------------------------------------- the helper

def test_stale_frame_is_refetched_uncached_and_the_session_bar_is_used():
    venue = _Venue(_friday_frame("SLV"), _monday_frame("SLV"))
    frame, skip = sb._fetch_session_frame("slv_pullback_1d", "SLV", "1d", venue,
                                          now=T_OPEN_TICK)
    assert skip is None and venue.calls == 2
    assert int(frame["timestamp"].iloc[-1].timestamp()) == MON


def test_bar_not_yet_published_is_skipped_never_decided_on_the_old_bar(caplog):
    venue = _Venue(_friday_frame("SLV"))
    with caplog.at_level("INFO"):
        for _ in range(3):
            _, skip = sb._fetch_session_frame("slv_pullback_1d", "SLV", "1d", venue,
                                              now=T_OPEN_TICK)
            assert skip == "session_bar_not_published"
    assert sum("has not published" in r.getMessage() for r in caplog.records) == 1


def test_current_frame_costs_no_extra_fetch():
    venue = _Venue(_monday_frame("SLV"))
    frame, skip = sb._fetch_session_frame("slv_pullback_1d", "SLV", "1d", venue,
                                          now=T_OPEN_TICK)
    assert skip is None and venue.calls == 1


def test_cached_pre_publication_frame_does_not_reach_the_next_tick():
    """The 13:33Z/13:35Z ticks: the cache still held the 13:31Z frame."""
    venue = _Venue(_friday_frame("SLV"), _monday_frame("SLV"))
    first = md.fetch_candles("SLV", "1d", exchange_client=venue, limit=200)  # cached
    assert int(first["timestamp"].iloc[-1].timestamp()) < MON and venue.calls == 1
    frame, skip = sb._fetch_session_frame("slv_pullback_1d", "SLV", "1d", venue,
                                          now=T_OPEN_TICK + 127)
    assert skip is None and int(frame["timestamp"].iloc[-1].timestamp()) == MON
    assert venue.calls == 2                      # cache hit, then one uncached read


def test_none_frame_and_unknown_timeframe_pass_through():
    class Empty:
        def get_ohlcv(self, *a, **k):
            return None
    assert sb._fetch_session_frame("x", "SLV", "1d", Empty(), now=T_OPEN_TICK) == (None, None)
    venue = _Venue(_friday_frame("SLV"))
    _, skip = sb._fetch_session_frame("x", "SLV", "7d", venue, now=T_OPEN_TICK)
    assert skip is None and venue.calls == 1


# --------------------------------------------- real-data replay of 09-28

@pytest.mark.parametrize("sym,leg,adx,entry", [
    ("SLV", "slv_pullback_1d", 11.673, 58.14),
    ("GDX", "gdx_pullback_1d", 15.2446, 92.89),
])
def test_real_friday_frame_reproduces_the_live_signal(sym, leg, adx, entry):
    """The defect, pinned: Friday's frame gives live's exact fingerprint + buy."""
    from src.units.strategies import load_strategy_config
    from src.units.strategies.htf_pullback_trend_2h import order_package
    df = _friday_frame(sym)
    assert sb._stamp_regime({}, df)["adx_14"] == adx
    cfg = {"symbol": sym, "timeframe": "1d", **load_strategy_config()[leg],
           "strategy_label": leg}
    pkg = order_package(cfg, candles_df=df)
    assert pkg["direction"] == "long" and pkg["entry"] == pytest.approx(entry)


def _run_builder(monkeypatch, leg, sym, venue, now=T_OPEN_TICK):
    import src.runtime.market_hours as mh
    import src.units.strategies as strategies
    real = strategies.load_strategy_config()
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setattr(mh, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(strategies, "load_strategy_config",
                        lambda: {**real, leg: {**real[leg], "enabled": True}})
    monkeypatch.setattr(sb, "_build_killzone_exchange", lambda s: venue)
    monkeypatch.setattr(sb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(sb, "_emit_shadow_preds", lambda *a, **k: None)
    monkeypatch.setattr(sb, "log_signal", lambda *a, **k: None)
    return getattr(sb, f"{leg}_signal_builder")({"SYMBOL": sym})


@pytest.mark.parametrize("sym,leg", [("SLV", "slv_pullback_1d"), ("GDX", "gdx_pullback_1d")])
def test_real_open_tick_decides_on_mondays_bar(monkeypatch, sym, leg):
    """13:31:13Z, venue first hands back Friday's frame, then Monday's: the
    leg now decides on Monday's bar -> no signal, matching the replay."""
    venue = _Venue(_friday_frame(sym), _monday_frame(sym))
    out = _run_builder(monkeypatch, leg, sym, venue)
    assert out["side"] == "none"
    assert "no trend-pullback-confirmation" in out["meta"]["reason"]


@pytest.mark.parametrize("sym,leg", [("SLV", "slv_pullback_1d"), ("GDX", "gdx_pullback_1d")])
def test_real_open_tick_before_publication_skips_instead_of_buying(monkeypatch, sym, leg):
    venue = _Venue(_friday_frame(sym))
    out = _run_builder(monkeypatch, leg, sym, venue)
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "session_bar_not_published"


# --------------------------------------- every US-session leg is wired

SESSION_LEGS = [
    "spy_trend_long_1d", "iwm_trend_long_1d", "splg_trend_long_1d", "scha_trend_long_1d",
    "qqq_trend_long_1d", "tqqq_trend_long_1d", "qld_trend_long_1d", "gld_pullback_1d",
    "iaum_pullback_1d", "tlt_pullback_1d", "slv_pullback_1d", "gdx_pullback_1d",
    "ief_pullback_1d", "gld_pullback_1h", "slv_trend_1h", "spy_pullback_1h",
    "qqq_pullback_1h", "tlt_pullback_1h", "uso_trend_1h",
]


def test_every_session_gated_builder_is_in_the_list():
    import inspect
    import re
    src = inspect.getsource(sb)
    gated = set()
    for m in re.finditer(r"^def (\w+)_signal_builder\(settings.*?(?=^def )", src, re.M | re.S):
        if 'is_market_open("us_equity")' in m.group(0):
            gated.add(m.group(1))
    assert gated == set(SESSION_LEGS)


@pytest.mark.parametrize("leg", SESSION_LEGS)
def test_every_session_leg_refuses_the_previous_sessions_bar(monkeypatch, leg):
    from src.units.strategies import load_strategy_config
    tf = str(load_strategy_config().get(leg, {}).get("timeframe") or "1d")
    tf_s = {"1d": 86400, "1h": 3600}[tf]
    # a frame whose last bar closed one bar before the session's current bar
    cur = (T_OPEN_TICK // tf_s) * tf_s if tf == "1h" else MON
    ts = [cur - (200 - i) * tf_s for i in range(200)]
    stale = _df([[t, 10.0, 10.5, 9.5, 10.0, 1000.0] for t in ts])
    venue = _Venue(stale)
    out = _run_builder(monkeypatch, leg, "SPY", venue)
    assert out["meta"]["reason"] == "session_bar_not_published", (leg, out["meta"])
    assert venue.calls == 2
