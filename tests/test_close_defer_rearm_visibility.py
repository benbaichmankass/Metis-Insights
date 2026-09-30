"""PI-20260930-ZPDIPMDA-0001 — a deferred close must not blind the naked re-arm.

MEASURED 2026-09-30 00:00–06:26Z on the live trader, alpaca_paper GLD
(pkg-6a8e3fb325464be3, 39 sh long, stop 416.09, last ~383): every 30 s the
monitor re-probed a standing close wedge, `AlpacaClient.close` returned
"us_equity market closed — exit deferred … (protective bracket left armed)"
before any broker call, and the naked sweep logged "skipping re-arm … an active
close is in flight". Three defects, each pinned here:

1. `mark_active_close` ran BEFORE the wedge-suppression / cooldown checks, so a
   close that sent nothing still blinded the re-arm sweep.
2. A closed-session defer (nothing sent, nothing held) kept the marker, so the
   sweep never checked that a stop rests. After an extended-hours attempt has
   cancelled the bracket and its DAY limit expired, none does — on alpaca_live
   that leaves a real-money position with nothing at the venue overnight.
3. A re-probe the session deferred observed nothing, so the ledger stayed "due"
   and the probe re-ran every tick. Now bounded, with a logged cause.

The extended-hours defer (a close-limit IS working and holds the shares) keeps
the marker: that is BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT and is unchanged.
"""
from __future__ import annotations

from collections import namedtuple

import pytest

from src.runtime import order_monitor as om
from tests.test_alpaca_rearm_preflight import (  # noqa: F401 — `world` is a fixture
    _close_row, _use, _Venue, world,
)

_D = namedtuple("RetryDecision", "attempt state reason last_seen")
# `world` stubs `is_active_close` to False; the sweep tests below need the REAL
# marker or they pass for the wrong reason. Captured before any fixture runs.
_REAL_IS_ACTIVE_CLOSE = om.is_active_close

_CLOSED_DEFER = {
    "ok": False,
    "error": ("us_equity market closed — exit deferred to next session "
              "(protective bracket left armed)"),
}
_EXT_WORKING = {
    "ok": False,
    "error": "extended-hours limit close working — not yet filled, exit deferred",
}
_VERDICT = {"action": "close", "reason": "sl_cross"}


class _FakeDB:
    def __init__(self, trade):
        self._trade = dict(trade)

    def get_trades(self, filters=None, limit=None):
        if self._trade.get("status") == "closed":
            return []
        row = dict(self._trade)
        for k, v in (filters or {}).items():
            if str(row.get(k)) != str(v):
                return []
        return [row]

    def update_order_package(self, pkg_id, updates):
        pass

    def update_trade(self, tid, updates):
        self._trade.update(updates)


def _row(account="alpaca_portfolio", symbol="QQQ", tid=6024, pkg="pkg-zpd"):
    return {
        "id": tid, "account_id": account, "symbol": symbol, "direction": "long",
        "position_size": 56, "status": "open", "order_package_id": pkg,
        "is_backtest": 0,
    }


def _pkg(pkg="pkg-zpd", tid=6024, symbol="QQQ"):
    return {"order_package_id": pkg, "linked_trade_id": tid,
            "strategy_name": "qqq_pullback_1h", "symbol": symbol}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    om._TICK_ACTIVE_CLOSE_AT.clear()
    om._WEDGE_REPROBE_SESSION_DEFERRED.clear()
    om._PENDING_CLOSE_RETRY_COOLDOWN.clear()
    om._PACKAGE_CLOSE_SKIP.clear()
    monkeypatch.setattr(om, "_close_retry_decision_for",
                        lambda t: _D(True, "no_wedge", "no standing wedge", None))
    yield
    om._TICK_ACTIVE_CLOSE_AT.clear()
    om._WEDGE_REPROBE_SESSION_DEFERRED.clear()
    om._PACKAGE_CLOSE_SKIP.clear()


def _sends(monkeypatch, result):
    calls = {"n": 0}

    def _fake(_t):
        calls["n"] += 1
        return result

    monkeypatch.setattr(om, "_send_close_to_exchange", _fake)
    return calls


# ── 2. the closed-session defer ─────────────────────────────────────────────


def test_closed_session_defer_clears_the_marker(monkeypatch):
    _sends(monkeypatch, _CLOSED_DEFER)
    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    assert not om.is_active_close("alpaca_portfolio", "QQQ"), (
        "a defer that sent nothing still blinds the naked re-arm sweep")


def test_ib_closed_venue_defer_clears_the_marker_too(monkeypatch):
    _sends(monkeypatch, {"ok": False, "error": (
        "IB venue for MHG is closed — exit deferred to next session "
        "(protective bracket left armed): outside liquidHours")})
    om._apply_update(_FakeDB(_row("ib_paper", "MHG")), _pkg(symbol="MHG"),
                     _VERDICT, om._StrategyTickSummary())
    assert not om.is_active_close("ib_paper", "MHG")


def test_extended_hours_working_limit_keeps_the_marker(monkeypatch):
    """A working close-limit holds the shares: re-arming would fight it."""
    _sends(monkeypatch, _EXT_WORKING)
    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    assert om.is_active_close("alpaca_portfolio", "QQQ")


def test_a_real_failure_keeps_the_marker(monkeypatch):
    _sends(monkeypatch, {"ok": False, "error": "insufficient qty available"})
    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    assert om.is_active_close("alpaca_portfolio", "QQQ")


def test_after_a_closed_defer_the_sweep_rests_a_stop_instead_of_skipping(world, monkeypatch):  # noqa: F811
    """End to end over the sweep: QQQ gapped to 710, below 6024's 716.80 stop,
    nothing resting. Before the fix the sweep counted `active_close_skipped` and
    left it naked; now it takes its designed closed-market posture."""
    db, _pages, mp = world
    mp.setattr(om, "is_active_close", _REAL_IS_ACTIVE_CLOSE)
    _close_row(db, 5928)
    _use(mp, _Venue(qty=56.0, price=710.0))
    mp.setattr(om, "_attempt_naked_autoprotect", lambda row, sl, tp, db=None: True)
    mp.setattr(om, "_send_close_to_exchange", lambda m: dict(_CLOSED_DEFER))

    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    s = om._check_broker_naked_equity_positions(db)
    assert s["active_close_skipped"] == 0
    assert s["exit_deferred"] == 1 and s["exit_deferred_rearmed"] == 1


def test_control_extended_defer_still_skips_in_the_sweep(world, monkeypatch):  # noqa: F811
    db, _pages, mp = world
    mp.setattr(om, "is_active_close", _REAL_IS_ACTIVE_CLOSE)
    _close_row(db, 5928)
    _use(mp, _Venue(qty=56.0, price=710.0))
    mp.setattr(om, "_send_close_to_exchange", lambda m: dict(_EXT_WORKING))
    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    s = om._check_broker_naked_equity_positions(db)
    assert s["active_close_skipped"] == 1


# ── 1. a close that sends nothing marks nothing ─────────────────────────────


def test_wedge_suppressed_close_sends_nothing_and_marks_nothing(monkeypatch):
    calls = _sends(monkeypatch, _CLOSED_DEFER)
    monkeypatch.setattr(om, "_close_retry_decision_for",
                        lambda t: _D(False, "suppressed", "evidenced wedge", "x"))
    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    assert calls["n"] == 0
    assert not om.is_active_close("alpaca_portfolio", "QQQ")


def test_cooling_down_close_marks_nothing(monkeypatch):
    """REGRESSION PIN, not a fix: on main the cooldown `continue` already ran
    before the mark (this passes on main too). Kept so a future move of the
    mark above the cooldown check is caught."""
    from datetime import datetime, timezone
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "300")
    calls = _sends(monkeypatch, _CLOSED_DEFER)
    om._PENDING_CLOSE_RETRY_COOLDOWN[("alpaca_portfolio", "QQQ", "long")] = (
        datetime.now(timezone.utc))
    om._apply_update(_FakeDB(_row()), _pkg(), _VERDICT, om._StrategyTickSummary())
    assert calls["n"] == 0
    assert not om.is_active_close("alpaca_portfolio", "QQQ")


# ── 3. the re-probe loop is bounded ─────────────────────────────────────────


def test_session_deferred_reprobe_is_bounded_then_retried(monkeypatch):
    monkeypatch.setenv("CLOSE_WEDGE_DEFERRED_REPROBE_S", "300")
    t = [1000.0]
    monkeypatch.setattr(om.time, "monotonic", lambda: t[0])
    calls = _sends(monkeypatch, _CLOSED_DEFER)
    monkeypatch.setattr(om, "_close_retry_decision_for",
                        lambda tr: _D(True, "reprobe_due", "last observed 420min", "x"))
    db = _FakeDB(_row())
    for _ in range(10):                       # ten 30 s ticks
        om._apply_update(db, _pkg(), _VERDICT, om._StrategyTickSummary())
        t[0] += 30.0
    assert calls["n"] == 1, "a session-deferred re-probe re-ran every tick"
    t[0] = 1000.0 + 301.0
    om._apply_update(db, _pkg(), _VERDICT, om._StrategyTickSummary())
    assert calls["n"] == 2, "the bound became a stop — the probe never re-ran"


def test_non_reprobe_close_is_not_bounded(monkeypatch):
    """Only a wedge re-probe is held. An ordinary close keeps attempting every
    tick — suppression is bought by the evidenced wedge alone."""
    calls = _sends(monkeypatch, _CLOSED_DEFER)
    db = _FakeDB(_row())
    for _ in range(3):
        om._apply_update(db, _pkg(), _VERDICT, om._StrategyTickSummary())
    assert calls["n"] == 3


def test_deferred_reprobe_seconds_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("CLOSE_WEDGE_DEFERRED_REPROBE_S", "nope")
    assert om._deferred_reprobe_seconds() == om._DEFERRED_REPROBE_S_DEFAULT
    monkeypatch.setenv("CLOSE_WEDGE_DEFERRED_REPROBE_S", "0")
    assert om._deferred_reprobe_seconds() == om._DEFERRED_REPROBE_S_DEFAULT


def test_reprobe_with_a_working_extended_limit_keeps_marking(world, monkeypatch):  # noqa: F811
    """#14585 review F1. A re-probe whose close-limit IS working reached the
    broker, so it must NOT be held: the marker has to stay set across ticks or
    it lapses after ACTIVE_CLOSE_WINDOW_S and the sweep cancels the working
    limit to re-arm an OCO (REARM-VS-CLOSE-FIGHT on a 5-minute cycle)."""
    db, _pages, mp = world
    mp.setattr(om, "is_active_close", _REAL_IS_ACTIVE_CLOSE)
    _close_row(db, 5928)
    _use(mp, _Venue(qty=56.0, price=710.0))
    monkeypatch.setenv("ACTIVE_CLOSE_WINDOW_S", "90")
    t = [1000.0]
    mp.setattr(om.time, "monotonic", lambda: t[0])
    calls = _sends(mp, _EXT_WORKING)
    mp.setattr(om, "_close_retry_decision_for",
               lambda tr: _D(True, "reprobe_due", "last observed 61min", "x"))
    rearms = []
    mp.setattr(om, "_attempt_naked_autoprotect",
               lambda row, sl, tp, db=None, trace=None: rearms.append(row) or True)
    fdb = _FakeDB(_row())
    for _ in range(4):                        # four 30 s ticks
        om._apply_update(fdb, _pkg(), _VERDICT, om._StrategyTickSummary())
        t[0] += 30.0
        assert om.is_active_close("alpaca_portfolio", "QQQ"), (
            "marker lapsed while the extended-hours close-limit was working")
        s = om._check_broker_naked_equity_positions(db)
        assert s["active_close_skipped"] == 1
    assert calls["n"] == 4, "a broker-reaching re-probe was held"
    assert rearms == [], "the sweep re-armed over a working close-limit"
    assert om._WEDGE_REPROBE_SESSION_DEFERRED == {}
