"""decision_bar: closed|forming for the donchian variant builders
(PI-20260930-QZSE4AMA-0002). Closed = the frame Stage 0 scores."""
import pandas as pd
import pytest

from src.runtime.strategy_signal_builders import _decision_frame

TF = "4h"
H4 = 4 * 3600
T0 = 1_800_000_000 - (1_800_000_000 % H4)  # a 4h boundary (epoch s)


def _frame(n=5, last_open=None):
    last_open = T0 + 10 * H4 if last_open is None else last_open
    ts = [last_open - (n - 1 - i) * H4 for i in range(n)]
    return pd.DataFrame({"timestamp": pd.to_datetime(ts, unit="s", utc=True),
                         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})


def test_forming_default_is_unchanged():
    df = _frame()
    out, skip = _decision_frame(df, TF, {}, now=T0 + 10 * H4 + 100)
    assert out is df and skip is None


def test_closed_drops_forming_bar_and_evaluates_fresh_close():
    df = _frame(last_open=T0 + 10 * H4)  # bar opened at T0+10H4; forming
    now = T0 + 10 * H4 + 120             # 2 min after the PREVIOUS bar's... 
    out, skip = _decision_frame(df, TF, {"decision_bar": "closed"}, now=now)
    assert len(out) == len(df) - 1 and skip is None
    assert int(out["timestamp"].iloc[-1].timestamp()) == T0 + 9 * H4


def test_stale_after_in_window_eval_reads_already_evaluated():
    df = _frame(last_open=T0 + 10 * H4)
    cfg = {"decision_bar": "closed"}
    _, skip = _decision_frame(df, TF, cfg, name="legA", now=T0 + 10 * H4 + 120)
    assert skip is None
    _, skip = _decision_frame(df, TF, cfg, name="legA", now=T0 + 10 * H4 + 3600)
    assert skip == "closed_bar_already_evaluated"


def test_stale_without_in_window_eval_reads_missed_and_warns_once(caplog):
    df = _frame(last_open=T0 + 10 * H4)
    cfg = {"decision_bar": "closed"}
    with caplog.at_level("WARNING"):
        for dt in (3600, 3700):
            _, skip = _decision_frame(df, TF, cfg, name="legB", now=T0 + 10 * H4 + dt)
            assert skip == "closed_bar_stale_window_missed"
    assert sum("never evaluated" in r.message for r in caplog.records) == 1


def test_fresh_window_is_configurable():
    df = _frame(last_open=T0 + 10 * H4)
    now = T0 + 10 * H4 + 3600
    _, skip = _decision_frame(
        df, TF, {"decision_bar": "closed", "decision_bar_fresh_seconds": 7200}, now=now)
    assert skip is None


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        _decision_frame(_frame(), TF, {"decision_bar": "nope"})


# ---- PI-20260930-GQPT6PQF-0002: a cached pre-close frame must not decide ----

class _Venue:
    """get_ohlcv returns whatever `rows` holds now; counts calls."""
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def get_ohlcv(self, symbol, timeframe, limit=200):
        self.calls += 1
        return self.rows


def _rows(last_close):
    # [ts_ms, o, h, l, c, v] — bar closes are what the cache can get wrong
    return [[(T0 + i * H4) * 1000, 1.0, 1.0, 1.0, last_close if i == 9 else 1.0, 1.0]
            for i in range(10)]


def test_fetch_candles_cached_pre_close_frame_vs_bypass():
    from src.runtime import market_data as md
    md.reset_candle_cache()
    venue = _Venue(_rows(1.5143))            # pre-close partial value
    first = md.fetch_candles("XRPUSDT", "4h", exchange_client=venue, limit=200)
    assert first["close"].iloc[-1] == 1.5143 and venue.calls == 1
    venue.rows = _rows(1.5161)               # the venue now has the final close
    stale = md.fetch_candles("XRPUSDT", "4h", exchange_client=venue, limit=200)
    assert stale["close"].iloc[-1] == 1.5143 and venue.calls == 1   # cache hit: the bug
    fresh = md.fetch_candles("XRPUSDT", "4h", exchange_client=venue, limit=200,
                             bypass_cache=True)
    assert fresh["close"].iloc[-1] == 1.5161 and venue.calls == 2   # the fix
    # the fresh frame is written back, so later default readers see it
    again = md.fetch_candles("XRPUSDT", "4h", exchange_client=venue, limit=200)
    assert again["close"].iloc[-1] == 1.5161 and venue.calls == 2
    md.reset_candle_cache()


def _builder_env(monkeypatch, decision_bar, now):
    import src.runtime.market_data as md
    import src.runtime.strategy_signal_builders as sb
    import src.units.strategies as strategies
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setattr(strategies, "load_strategy_config", lambda: {"legX": {
        "enabled": True, "symbols": ["XRPUSDT"], "timeframe": "4h",
        "decision_bar": decision_bar}})
    monkeypatch.setattr(sb, "_build_killzone_exchange", lambda s: object())
    calls = []

    def fake_fetch(symbol, timeframe, **kw):
        calls.append(kw)
        return None                      # RuntimeError after the fetch: enough to observe it
    monkeypatch.setattr(md, "fetch_candles", fake_fetch)
    sb._CLOSED_BAR_EVALUATED.clear()
    sb._CLOSED_BAR_MISS_LOGGED.clear()
    return sb, calls


def test_builder_closed_leg_fetches_uncached_inside_window(monkeypatch):
    sb, calls = _builder_env(monkeypatch, "closed", T0 + 10 * H4 + 90)
    with pytest.raises(RuntimeError):
        sb._trend_donchian_variant_builder("legX", {})
    assert calls == [{"limit": 200, "exchange_client": calls[0]["exchange_client"],
                      "bypass_cache": True}]


def test_builder_closed_leg_does_not_fetch_outside_window(monkeypatch):
    sb, calls = _builder_env(monkeypatch, "closed", T0 + 10 * H4 + 3600)
    out = sb._trend_donchian_variant_builder("legX", {})
    assert calls == []                                    # no venue round-trip
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "closed_bar_stale_window_missed"


def test_builder_forming_leg_is_unchanged(monkeypatch):
    sb, calls = _builder_env(monkeypatch, "forming", T0 + 10 * H4 + 3600)
    with pytest.raises(RuntimeError):
        sb._trend_donchian_variant_builder("legX", {})
    assert len(calls) == 1 and "bypass_cache" not in calls[0]


def test_builder_closed_leg_waits_out_the_settle_seconds(monkeypatch):
    sb, calls = _builder_env(monkeypatch, "closed", T0 + 10 * H4 + 2)
    out = sb._trend_donchian_variant_builder("legX", {})
    assert calls == [] and out["meta"]["reason"] == "closed_bar_settling"
    # not recorded as evaluated: the next tick in the window still evaluates
    assert "legX" not in sb._CLOSED_BAR_EVALUATED
