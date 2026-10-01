"""PROP-TRAIL: the leg's declared trail applied by amending the resting SL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd
import pytest

from src.prop import prop_trail as pt
from src.prop.platform.base import Position
from src.prop.prop_executor import ExecutorConfig

T0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)   # signal bar CLOSE
# ``decision_bar: closed`` here: signal_time at/after T0 is then already in
# harness bar entry_i+1. The live breakout legs are ``forming`` (the default);
# test_forming_leg_does_not_manage_its_signal_bar covers that shift.
SOL = {"timeframe": "1h", "atr_stop_mult": 2.5, "trail_mult": 3.5, "tp_r": 6.0, "decision_bar": "closed"}
ETH = {**SOL, "stale_exit_bars": 12, "stale_exit_below_r": 0.0, "trail_decay_stall_bars": 10,
       "trail_decay_tight_mult": 1.8, "trail_decay_arm_r": 2.99}


def bars(rows, start=T0, forming=None):
    """rows: (high, low, close) per CLOSED 1h bar from ``start``; ``forming``
    appends a still-open bar (its close is the current price)."""
    out = [{"timestamp": int((start + timedelta(hours=i)).timestamp() * 1000),
            "open": c, "high": h, "low": lo, "close": c, "volume": 1.0} for i, (h, lo, c) in enumerate(rows)]
    if forming is not None:
        h, lo, c = forming
        out.append({"timestamp": int((start + timedelta(hours=len(rows))).timestamp() * 1000),
                    "open": c, "high": h, "low": lo, "close": c, "volume": 1.0})
    return pd.DataFrame(out)


def now_after(n_closed):
    return T0 + timedelta(hours=n_closed, minutes=5)


# entry 100, sl 95 -> risk 5, atr = 5 / 2.5 = 2.0; trail = ext - 3.5*2 = ext - 7
def test_long_trail_replays_the_harness_and_floors_to_step():
    c = bars([(104.003, 99, 103), (108.007, 102, 107)], forming=(108, 106, 106.5))
    p = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=T0 + timedelta(seconds=30),
                      resting_sl=95, candles=c, now=now_after(2), price_step=0.01)
    assert p.action == "tighten"
    assert p.replay_sl == pytest.approx(108.007 - 7.0)
    assert p.sl == pytest.approx(101.0)          # floored, never past the harness value
    assert p.bars == 2


def test_never_loosens_and_needs_a_full_step():
    c = bars([(104, 99, 103)], forming=(104, 102, 103))
    p = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=T0,
                      resting_sl=97.5, candles=c, now=now_after(1), price_step=0.01)
    assert p.action == "hold" and p.sl is None   # replay 97.0 < resting 97.5
    p = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=T0,
                      resting_sl=96.995, candles=c, now=now_after(1), price_step=0.01)
    assert p.action == "hold"                    # 97.00 is under one step above 96.995


def test_forming_bar_is_not_managed():
    c = bars([(101, 99, 100)], forming=(120, 100, 119))
    p = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=T0,
                      resting_sl=95, candles=c, now=now_after(1), price_step=0.01)
    assert p.bars == 1 and p.replay_sl == pytest.approx(95.0)   # 101-7=94 < 95


def test_stop_through_current_price_is_not_amended():
    c = bars([(120, 100, 119)], forming=(113, 111, 112))
    p = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=T0,
                      resting_sl=95, candles=c, now=now_after(1), price_step=0.01)
    assert p.action == "hold" and "through the current price" in p.why


def test_short_mirror():
    c = bars([(101, 92, 93)], forming=(94, 92, 93))
    p = pt.plan_trail(leg=SOL, direction="short", entry=100, initial_sl=105, signal_time=T0,
                      resting_sl=105, candles=c, now=now_after(1), price_step=0.01)
    assert p.action == "tighten" and p.sl == pytest.approx(99.0)   # 92 + 7


def test_eth_trail_decay_arms_at_r():
    # ext 115 -> peak_r = 3.0 >= 2.99 -> mult 1.8 -> 115 - 3.6 = 111.4
    c = bars([(115, 100, 114)], forming=(114, 113, 113.5))
    p = pt.plan_trail(leg=ETH, direction="long", entry=100, initial_sl=95, signal_time=T0,
                      resting_sl=95, candles=c, now=now_after(1), price_step=0.01)
    assert p.sl == pytest.approx(111.4)


def test_stale_lever_is_flagged_never_acted_on():
    c = bars([(100.5, 98, 99)] * 12, forming=(99, 98, 99))
    p = pt.plan_trail(leg=ETH, direction="long", entry=100, initial_sl=95, signal_time=T0,
                      resting_sl=95, candles=c, now=now_after(12), price_step=0.01)
    # flagged only: the plan never closes (the 10-bar stall-decay may still tighten)
    assert p.close_lever == "stale_exit_bars" and p.action in ("hold", "tighten")
    assert p.sl == pytest.approx(100.5 - 1.8 * 2.0)
    p11 = pt.plan_trail(leg=ETH, direction="long", entry=100, initial_sl=95, signal_time=T0,
                        resting_sl=95, candles=bars([(100.5, 98, 99)] * 11), now=now_after(11), price_step=0.01)
    assert p11.close_lever is None
    sol = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=T0,
                        resting_sl=95, candles=c, now=now_after(12), price_step=0.01)
    assert sol.close_lever is None               # SOL declares no stale lever


def test_unmodelled_sl_lever_skips_the_leg():
    p = pt.plan_trail(leg={**SOL, "be_floor_r": 1.0}, direction="long", entry=100, initial_sl=95,
                      signal_time=T0, resting_sl=95, candles=bars([(120, 99, 119)]), now=now_after(1))
    assert p.action == "skip"


# ── the venue step ────────────────────────────────────────────────────────


class Adapter:
    def __init__(self, positions: List[Position], apply: bool = True):
        self.positions, self.apply, self.calls = positions, apply, []

    def read_positions(self, page):
        return [Position(**p.as_dict()) for p in self.positions]

    quote = {"bid": 109.0, "ask": 109.02}

    def read_quote(self, page, venue_symbol):
        return self.quote

    def modify_bracket(self, page, position, stop_loss, take_profit, *, arm=False):
        self.calls.append((position.symbol, stop_loss, take_profit, arm))
        if arm and self.apply:
            for p in self.positions:
                if p.symbol == position.symbol:
                    p.stop_loss = stop_loss
        return {"ok": True, "clicked": arm, "why": "x"}


class Api:
    def __init__(self):
        self.posted: List[Dict[str, Any]] = []

    def open_fills(self, account_id):
        return [{"ticket_id": "t1", "symbol": "SOLUSDT", "direction": "long", "status": "open"}]

    def all_tickets(self, account_id):
        return [{"ticket_id": "t1", "strategy": "trend_donchian_sol_prop", "entry": 100, "sl": 95,
                 "signal_time": T0.isoformat()}]

    def post_report(self, body):
        self.posted.append(body)
        return {"ok": True}


def cfg(enabled=("SOLUSD",)):
    return ExecutorConfig(account_id="breakout_1", enabled_venue_symbols=list(enabled),
                          symbols={"SOLUSDT": {"venue": "SOLUSD", "price_step": 0.01}})


def run(adapter, api, mode, tmp_path, c=None, enabled=("SOLUSD",)):
    c = c if c is not None else bars([(110, 99, 109)], forming=(109, 108, 108.5))
    return pt.run_trail_step(adapter=adapter, page=None, api=api, cfg=cfg(enabled), mode=mode,
                             state_dir=tmp_path, candles_fn=lambda s, tf: c, now=now_after(1),
                             legs={"trend_donchian_sol_prop": SOL})


def pos(sl=95.0):
    return Position(symbol="SOLUSD", side="long", quantity=1, entry_price=100, stop_loss=sl, take_profit=130)


def test_live_amends_sl_only_confirms_and_reports(tmp_path):
    a, api = Adapter([pos()]), Api()
    res = run(a, api, "live", tmp_path)
    assert a.calls == [("SOLUSD", pytest.approx(103.0), None, True)]   # TP untouched
    assert api.posted and api.posted[0]["kind"] == "amend" and api.posted[0]["sl"] == pytest.approx(103.0)
    assert not res.alerts
    a.calls.clear()
    run(a, api, "live", tmp_path)                 # resting SL now equals the replay
    assert a.calls == []


def test_read_only_walks_disarmed_and_writes_nothing(tmp_path):
    a, api = Adapter([pos()]), Api()
    run(a, api, "read_only", tmp_path)
    assert a.calls == [("SOLUSD", pytest.approx(103.0), None, False)] and api.posted == []


def test_unconfirmed_amend_alerts_and_is_capped(tmp_path):
    a, api = Adapter([pos()], apply=False), Api()
    alerts = []
    for _ in range(4):
        alerts += run(a, api, "live", tmp_path).alerts
    assert len(a.calls) == pt.MAX_ATTEMPTS_PER_TARGET
    assert api.posted == [] and sum("not confirmed" in x for x in alerts) == 2


def test_symbol_not_enabled_is_untouched(tmp_path):
    a, api = Adapter([pos()]), Api()
    run(a, api, "live", tmp_path, enabled=())     # tradeify_1 declares none
    assert a.calls == [] and api.posted == []


def test_off_does_nothing(tmp_path):
    a = Adapter([pos()])
    run(a, Api(), "off", tmp_path)
    assert a.calls == []


class LooseningAdapter(Adapter):
    """Simulates an edit form whose SL field is not a price: the venue ends
    up with a stop far below the one we had."""
    def modify_bracket(self, page, position, stop_loss, take_profit, *, arm=False):
        self.calls.append((position.symbol, stop_loss, take_profit, arm))
        if arm:
            self.positions[0].stop_loss = 50.0 if len(self.calls) == 1 else stop_loss
        return {"ok": True, "clicked": arm, "why": "x"}


def test_loosened_stop_is_restored_and_the_ticket_locks(tmp_path):
    a, api = LooseningAdapter([pos()]), Api()
    res = run(a, api, "live", tmp_path)
    assert a.calls[1] == ("SOLUSD", 95.0, None, True)          # restore to the prior SL
    assert a.positions[0].stop_loss == 95.0
    assert any("LOOSENED" in x for x in res.alerts) and api.posted == []
    a.calls.clear()
    run(a, api, "live", tmp_path)
    assert a.calls == []                                       # locked


def test_no_venue_quote_means_no_amend(tmp_path):
    a, api = Adapter([pos()]), Api()
    a.quote = None
    run(a, api, "live", tmp_path)
    assert a.calls == []


def test_stop_within_buffer_of_venue_bid_is_not_amended(tmp_path):
    a, api = Adapter([pos()]), Api()
    a.quote = {"bid": 103.1, "ask": 103.12}     # new stop 103.0; buffer 0.2 (0.1 x atr 2.0)
    run(a, api, "live", tmp_path)
    assert a.calls == []


def test_adapter_refusal_alerts_once_and_counts_nothing(tmp_path):
    class Refusing(Adapter):
        def modify_bracket(self, page, position, stop_loss, take_profit, *, arm=False):
            self.calls.append((position.symbol, stop_loss, take_profit, arm))
            return {"ok": False, "clicked": False, "why": "refused: the SL/TP edit dialog is unmeasured"}
    a, api = Refusing([pos()]), Api()
    alerts = []
    for _ in range(3):
        alerts += run(a, api, "live", tmp_path).alerts
    assert sum("refused by the adapter" in x for x in alerts) == 1 and api.posted == []


def test_an_exception_inside_the_step_is_contained(tmp_path):
    class Boom(Adapter):
        def read_quote(self, page, venue_symbol):
            raise TimeoutError("playwright")
    res = run(Boom([pos()]), Api(), "live", tmp_path)
    assert any("step failed (TimeoutError)" in x for x in res.alerts)


def test_unparsed_sl_after_amend_is_not_treated_as_loosened(tmp_path):
    class Blank(Adapter):
        def modify_bracket(self, page, position, stop_loss, take_profit, *, arm=False):
            self.calls.append((position.symbol, stop_loss, take_profit, arm))
            if arm:
                self.positions[0].stop_loss = None
            return {"ok": True, "clicked": arm, "why": "x"}
    a = Blank([pos()])
    res = run(a, Api(), "live", tmp_path)
    assert len(a.calls) == 1                       # no blind "restore" click
    assert any("not confirmed" in x for x in res.alerts)


def test_forming_leg_does_not_manage_its_signal_bar():
    # A ``decision_bar: forming`` leg (both live breakout legs) fires INSIDE
    # the bar that broke the channel: that bar is harness bar i, and the trail
    # starts at entry_i+1 (scripts/backtest_trend.py), one bar later.
    forming_leg = {k: v for k, v in SOL.items() if k != "decision_bar"}
    c = bars([(130, 99, 103), (104, 102, 103.5)], forming=(104, 103, 103.5))
    sig = T0 + timedelta(minutes=20)                         # inside the bar opening at T0
    p = pt.plan_trail(leg=forming_leg, direction="long", entry=100, initial_sl=95, signal_time=sig,
                      resting_sl=95, candles=c, now=now_after(2), price_step=0.01)
    assert p.bars == 1                                       # the T0 bar (high 130) is not replayed
    assert p.replay_sl == pytest.approx(104 - 7.0)           # extreme from the managed bar only
    closed = pt.plan_trail(leg=SOL, direction="long", entry=100, initial_sl=95, signal_time=sig,
                           resting_sl=95, candles=c, now=now_after(2), price_step=0.01)
    assert closed.bars == 2                                  # a closed-bar leg manages from the T0 bar
    assert closed.replay_sl == pytest.approx(130 - 7.0)
