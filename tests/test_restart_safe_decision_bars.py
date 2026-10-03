"""RESTART-SAFE (PI-20261003-OJTPWGCC-0001): a restart never skips, and never
repeats, a leg's decision bar.

Every live leg decides from the CANDLE HISTORY and an on-disk ledger
(``src.runtime.decision_bar_ledger``), not from a wall-clock window inside one
process. ``_restart()`` below is what a systemd restart does to that state: the
in-memory copy is gone, the file and the clock remain, and the process identity
changes.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.runtime import decision_bar_ledger as ledger
from src.runtime import intent_multiplexer as im
from src.runtime.strategy_signal_builders import _closed_window_skip, _decision_frame

TF = "4h"
H4 = 4 * 3600
T0 = 1_800_000_000 - (1_800_000_000 % H4)   # a 4h boundary (epoch s)
CLOSE = T0 + 10 * H4                        # the bar opened at T0+9H4 closes here
CFG = {"decision_bar": "closed"}


def _restart(monkeypatch, pid_bump=[0]):
    """Simulate a process restart: drop the in-memory ledger, new pid + start."""
    pid_bump[0] += 1
    monkeypatch.setattr(ledger, "PID", 900_000 + pid_bump[0])
    monkeypatch.setattr(ledger, "PROCESS_STARTED", ledger.PROCESS_STARTED + pid_bump[0])
    ledger.reset_for_tests()


def _frame(last_open=CLOSE, n=5, close=1.0):
    ts = [last_open - (n - 1 - i) * H4 for i in range(n)]
    return pd.DataFrame({"timestamp": pd.to_datetime(ts, unit="s", utc=True),
                         "open": 1.0, "high": 1.0, "low": 1.0, "close": close})


def _decide(name, now):
    """One closed-leg tick: the pre-fetch gate, then the frame gate."""
    skip = _closed_window_skip(TF, CFG, name=name, now=now)
    if skip is not None:
        return skip
    return _decision_frame(_frame(), TF, CFG, name=name, now=now)[1]


# ------------------------------------------------------------ closed legs

def test_restart_mid_window_after_evaluation_evaluates_exactly_once(monkeypatch, caplog):
    assert _decide("legA", CLOSE + 120) is None                 # process 1 decides
    _restart(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert _decide("legA", CLOSE + 400) == "closed_bar_already_evaluated"
        assert _decide("legA", CLOSE + 520) == "closed_bar_already_evaluated"
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("by a previous process" in m for m in msgs) == 1   # said once, accurately
    assert not any("never evaluated" in m for m in msgs)          # the false warning is gone
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_restart_mid_window_before_evaluation_still_evaluates_once(monkeypatch):
    assert _decide("legA", CLOSE + 2) == "closed_bar_settling"  # process 1 only saw settle
    _restart(monkeypatch)
    results = [_decide("legA", CLOSE + dt) for dt in (200, 320, 330)]
    assert results == [None, "closed_bar_already_evaluated", "closed_bar_already_evaluated"]


def test_restart_after_window_catches_up_while_fresh(monkeypatch, caplog):
    # The 2026-09-30 shape when the old process had NOT decided: the new
    # process's first tick is 430 s after the close — past the old 360 s window.
    _restart(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert _decide("legA", CLOSE + 430) is None
    assert any("caught up after restart" in r.getMessage() for r in caplog.records)
    row = ledger.get("legA")
    assert row["disposition"] == "evaluated" and row["catchup"] is True
    assert row["bar_open"] == CLOSE - H4


def test_restart_after_bound_is_skipped_as_stale_with_reason(monkeypatch, caplog):
    monkeypatch.setattr(ledger, "PROCESS_STARTED", CLOSE + 1000)   # came up late
    ledger.reset_for_tests()
    with caplog.at_level(logging.WARNING):
        assert _decide("legA", CLOSE + 1100) == "closed_bar_stale_skipped"
        assert _decide("legA", CLOSE + 1300) == "closed_bar_stale_skipped"
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warns) == 1
    assert "skipped as stale" in warns[0] and "900s catch-up bound" in warns[0]
    assert "this process started 1000s after the close" in warns[0]
    assert ledger.get("legA")["disposition"] == "stale_skipped"


def test_double_evaluation_is_impossible_across_many_restarts(monkeypatch):
    decided = 0
    for dt in range(6, 900, 37):                      # every tick inside the bound
        if _decide("legA", CLOSE + dt) is None:
            decided += 1
        _restart(monkeypatch)                        # ...with a restart after each
    assert decided == 1
    on_disk = json.loads(Path(ledger.ledger_path()).read_text())["legs"]["legA"]
    assert on_disk["bar_open"] == CLOSE - H4


def test_next_bar_is_decided_independently(monkeypatch):
    assert _decide("legA", CLOSE + 100) is None
    _restart(monkeypatch)
    nxt = CLOSE + H4
    skip = _closed_window_skip(TF, CFG, name="legA", now=nxt + 100)
    assert skip is None
    _, skip = _decision_frame(_frame(last_open=nxt), TF, CFG, name="legA", now=nxt + 100)
    assert skip is None and ledger.get("legA")["bar_open"] == nxt - H4


def test_catchup_bound_is_written_down():
    assert ledger.catchup_bound_seconds(H4, floor_s=360) == 900       # 4h / 16
    assert ledger.catchup_bound_seconds(3600, floor_s=360) == 360     # floor wins
    assert ledger.catchup_bound_seconds(3600) == 225                  # forming 1h
    assert ledger.catchup_bound_seconds(300, floor_s=360) == 299      # never a full bar
    assert ledger.catchup_bound_seconds(H4, {"decision_bar_catchup_seconds": 60}) == 60


@pytest.mark.parametrize("side,cur,ok,frag", [
    ("short", 1.2958, True, "within"),
    ("short", 1.2647, False, "drifted"),     # ran toward TP: chasing
    ("short", 1.3900, False, "through SL"),
    ("short", 1.1600, False, "through TP"),
    ("short", None, False, "unreadable"),    # fail closed
    ("long", 1.4700, True, "within"),
    ("long", 1.4870, False, "drifted"),
])
def test_catchup_price_check(side, cur, ok, frag):
    entry, sl, tp = ((1.2958, 1.3837, 1.1675) if side == "short"
                     else (1.464, 1.4168, 1.6056))
    got, why = ledger.catchup_price_check(side, entry, sl, tp, cur)
    assert got is ok and frag in why


def test_unreadable_ledger_starts_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    ledger.reset_for_tests(p)
    assert ledger.get("legA") is None
    ledger.update("legA", bar_open=1.0)
    assert json.loads(p.read_text())["legs"]["legA"]["bar_open"] == 1.0


# ------------------------------------------------------------ forming legs

H1 = 3600
B = 1_800_000_000 - (1_800_000_000 % H1)   # a 1h boundary: bar [B-H1, B) closes at B


class _Venue:
    """Returns 1h bars up to and including the one forming at ``now``; the
    forming bar's close is ``live``."""
    def __init__(self, now, live):
        self.now, self.live = now, live

    def get_ohlcv(self, symbol, timeframe, limit=200):
        cur = (self.now // H1) * H1
        rows = [[(cur - (20 - i) * H1) * 1000, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(20)]
        rows.append([cur * 1000, 1.0, 1.0, 1.0, self.live, 1.0])
        return rows


def _forming_env(monkeypatch, now, live=1.0):
    from src.runtime import market_data as md
    md.reset_candle_cache()
    monkeypatch.setattr(im.time, "time", lambda: now)
    monkeypatch.setattr(im, "_strategy_timeframe_seconds", lambda name: H1)
    monkeypatch.setattr(im, "_strategy_symbol_scope", lambda: {})
    monkeypatch.setattr(im, "_strategy_cfgs", lambda: {"legF": {"timeframe": "1h"}})
    seen = []
    venue = _Venue(now, live)

    def builder(settings):
        df = md.fetch_candles("XRPUSDT", "1h", exchange_client=venue, limit=200)
        last_open = int(df["timestamp"].iloc[-1]) // 1000
        seen.append(last_open)
        if last_open == B - H1 and now >= B:   # that bar, read CLOSED, signals
            return {"symbol": "XRPUSDT", "side": "buy", "entry_price": 1.0,
                    "stop_loss": 0.9, "take_profit": 1.3, "meta": {}}
        return {"symbol": "XRPUSDT", "side": "none", "meta": {}}
    return builder, seen


def _collect(builder):
    return im._collect_intents({"SYMBOL": "XRPUSDT"}, builders={"legF": builder},
                               strategies=["legF"], target_qty_hint=1.0)


def test_forming_restart_across_close_decides_the_closed_bar_once(monkeypatch, caplog):
    builder, seen = _forming_env(monkeypatch, B - 300)          # process 1, bar's tail
    assert _collect(builder) == [] and seen == [B - H1]          # forming bar, no signal yet
    _restart(monkeypatch)                                        # down across the close
    builder, seen = _forming_env(monkeypatch, B + 60, live=1.01)
    with caplog.at_level(logging.INFO):
        out = _collect(builder)
    assert seen == [B - H1, B]                     # as-of the close, then the forming bar
    assert [i.side for i in out] == ["long"]       # the gap bar's signal, price-checked
    assert any("caught up after restart" in r.getMessage() for r in caplog.records)
    _restart(monkeypatch)
    builder, seen = _forming_env(monkeypatch, B + 120, live=1.01)
    assert _collect(builder) == [] and seen == [B]  # never decided twice


def test_forming_catchup_refused_when_price_has_moved(monkeypatch, caplog):
    builder, _ = _forming_env(monkeypatch, B - 300)
    _collect(builder)
    _restart(monkeypatch)
    builder, seen = _forming_env(monkeypatch, B + 60, live=1.05)   # 0.5 x stop away
    with caplog.at_level(logging.WARNING):
        assert _collect(builder) == []
    assert seen == [B - H1, B]
    assert any("skipped as stale" in r.getMessage() and "drifted" in r.getMessage()
               for r in caplog.records)


def test_forming_catchup_beyond_bound_is_skipped(monkeypatch):
    builder, _ = _forming_env(monkeypatch, B - 300)
    _collect(builder)
    _restart(monkeypatch)
    builder, seen = _forming_env(monkeypatch, B + 400)           # 1h bound = 225 s
    assert _collect(builder) == [] and seen == [B]                # no as-of evaluation
    assert ledger.get("legF|XRPUSDT")["catchup_disposition"] == "stale_skipped"


def test_forming_catchup_waits_out_the_settle_seconds(monkeypatch):
    builder, _ = _forming_env(monkeypatch, B - 300)
    _collect(builder)
    _restart(monkeypatch)
    builder, seen = _forming_env(monkeypatch, B + 2)
    assert _collect(builder) == [] and seen == [B]               # not yet, not disposed
    assert ledger.get("legF|XRPUSDT").get("catchup_open") is None
    builder, seen = _forming_env(monkeypatch, B + 70, live=1.01)
    out = _collect(builder)                  # the settle tick did not erase the gap
    assert seen == [B - H1, B] and [i.side for i in out] == ["long"]


def test_forming_normal_tick_spacing_never_triggers_catchup(monkeypatch):
    builder, _ = _forming_env(monkeypatch, B - 100)              # last look 100 s pre-close
    _collect(builder)
    _restart(monkeypatch)
    builder, seen = _forming_env(monkeypatch, B + 30)
    assert _collect(builder) == [] and seen == [B]                # 100 s <= 2 ticks: no gap
    assert ledger.get("legF|XRPUSDT").get("catchup_open") is None


def test_forming_first_boot_without_ledger_is_unchanged(monkeypatch):
    builder, seen = _forming_env(monkeypatch, B + 60)
    assert _collect(builder) == [] and seen == [B]
    assert ledger.get("legF|XRPUSDT")["bar_open"] == B


def test_closed_legs_are_left_out_of_the_forming_catchup(monkeypatch):
    builder, _ = _forming_env(monkeypatch, B - 300)
    monkeypatch.setattr(im, "_strategy_cfgs",
                        lambda: {"legF": {"timeframe": "1h", "decision_bar": "closed"}})
    _collect(builder)
    assert ledger.get("legF|XRPUSDT") is None


def test_trim_to_asof_records_live_price():
    df = pd.DataFrame({"timestamp": [B - 2 * H1, B - H1, B], "close": [1.0, 1.1, 1.2]})
    holder = {"asof": float(B), "current": {}}
    out = ledger.trim_to_asof(df, "XRP/USDT", "1h", holder)
    assert list(out["close"]) == [1.0, 1.1]
    assert holder["current"][("XRPUSDT", H1)] == 1.2


# ------------------------------------------------- REAL DATA: 2026-09-30 20:06Z

FIXTURE = Path(__file__).parent / "fixtures" / "xrpusdt_restart_replay_2026-09-30.json"
BAR_16Z = 1790784000          # 2026-09-30T16:00Z, closes 20:00Z
CLOSE_20Z = BAR_16Z + H4


def _real_venue(fixture, last_open, live_close):
    """Real 4h bars up to the one opened at ``last_open`` (forming), whose close
    is replaced by the real price at the replay instant."""
    rows = [list(r) for r in fixture["4h"] if r[0] <= last_open]
    rows[-1][4] = live_close
    df = pd.DataFrame([[r[0] * 1000, *r[1:]] for r in rows][-200:],
                      columns=["timestamp", "open", "high", "low", "close", "volume"])
    # exactly what src/exchange/bybit_connector.get_ohlcv hands back
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    class V:
        def get_ohlcv(self, symbol, timeframe, limit=200):
            return df.copy()
    return V()


def _replay(monkeypatch, now, venue):
    import src.runtime.market_data as md
    import src.runtime.strategy_signal_builders as sb
    md.reset_candle_cache()
    monkeypatch.setattr("time.time", lambda: now)
    monkeypatch.setattr(sb, "_build_killzone_exchange", lambda s: venue)
    monkeypatch.setattr(sb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(sb, "_emit_shadow_preds", lambda *a, **k: None)
    monkeypatch.setattr(sb, "log_signal", lambda *a, **k: None)
    return sb._trend_donchian_variant_builder("trend_donchian_xrp_4h", {})


@pytest.fixture
def real():
    return json.loads(FIXTURE.read_text())


def _price_at(fixture, key, t):
    return next(r[4] for r in fixture[key] if r[0] <= t < r[0] + 300)


def test_real_2026_09_30_restart_is_not_misreported_and_not_reevaluated(monkeypatch, real, caplog):
    """Real candles, real timeline (ict-trader-live journal, read 2026-10-03):
    the old process decided the 16:00Z bar at 20:02:07Z ("no breakout ...
    close=1.489"), the trader restarted at 20:06:39Z, and the new process's first
    tick at 20:07:10Z warned "never evaluated". Now: one decision, an accurate
    line, no warning."""
    t1 = CLOSE_20Z + 127                                          # 20:02:07Z
    out = _replay(monkeypatch, t1, _real_venue(real, CLOSE_20Z, _price_at(real, "5m_2026-09-30", t1)))
    assert out["side"] == "none" and "no breakout" in out["meta"]["reason"]
    assert "close=1.489" in out["meta"]["reason"]                 # matches the live journal
    _restart(monkeypatch)                                         # 20:06:39Z
    t2 = CLOSE_20Z + 430                                          # 20:07:10Z
    with caplog.at_level(logging.INFO):
        out = _replay(monkeypatch, t2, _real_venue(real, CLOSE_20Z, _price_at(real, "5m_2026-09-30", t2)))
    assert out["meta"]["reason"] == "closed_bar_already_evaluated"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("evaluated by a previous process" in m for m in msgs)
    assert not any("never evaluated" in m for m in msgs)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_real_2026_09_30_counterfactual_restart_before_decision_catches_up(monkeypatch, real):
    """Same candles, but the restart lands BEFORE the old process decided: the
    old code skipped the bar (its 360 s window had passed at 20:07:10Z); now it
    is decided once, from the real closed bar."""
    _restart(monkeypatch)
    t2 = CLOSE_20Z + 430
    out = _replay(monkeypatch, t2, _real_venue(real, CLOSE_20Z, _price_at(real, "5m_2026-09-30", t2)))
    assert "no breakout" in out["meta"]["reason"] and "close=1.489" in out["meta"]["reason"]
    assert ledger.get("trend_donchian_xrp_4h")["catchup"] is True
    t3 = CLOSE_20Z + 560
    out = _replay(monkeypatch, t3, _real_venue(real, CLOSE_20Z, _price_at(real, "5m_2026-09-30", t3)))
    assert out["meta"]["reason"] == "closed_bar_already_evaluated"


def test_real_2026_09_30_counterfactual_past_bound_is_skipped_as_stale(monkeypatch, real):
    monkeypatch.setattr(ledger, "PROCESS_STARTED", CLOSE_20Z + 950)
    ledger.reset_for_tests()
    t = CLOSE_20Z + 1000
    out = _replay(monkeypatch, t, _real_venue(real, CLOSE_20Z, 1.4921))
    assert out["meta"]["reason"] == "closed_bar_stale_skipped"


BAR_0915_16Z = 1789488000             # 2026-09-15T16:00Z — a real short breakout bar


def test_real_breakout_caught_up_is_price_checked(monkeypatch, real, tmp_path):
    """A REAL signal: the 2026-09-15 16:00Z XRP bar broke down (short, entry
    1.2958 / SL 1.3837 under the live config). Caught up at +7 min, it is taken
    at the real price at the close (the 20:00Z 30m bar's open, 1.2958) and
    refused at the real low printed inside that 30m bar (1.2647: the move had
    run 0.35 x the stop — entering there chases it). Which minute that low
    printed is not in the data; the refusal is on price, not on time."""
    close = BAR_0915_16Z + H4
    m30 = {r[0]: r for r in real["30m_2026-09-15"]}
    for live, accepted in ((m30[close][1], True), (m30[close][3], False)):
        _restart(monkeypatch)
        ledger.reset_for_tests(tmp_path / f"ledger-{accepted}.json")   # two worlds
        out = _replay(monkeypatch, close + 420, _real_venue(real, close, live))
        if accepted:
            assert out["side"] == "sell", out["meta"]
            assert out["entry_price"] == pytest.approx(1.2958)
        else:
            assert out["side"] == "none"
            assert out["meta"]["reason"].startswith("closed_bar_catchup_stale_price")
