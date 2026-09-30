"""PI-20260930-ZIFJ1RKM-0003 — the extended-hours TRADE-SCOPED defer is a defer.

AlpacaClient._close_extended_hours returns retCode 2 with "… trade-scoped exit of
N of M on SYM DEFERRED to the regular session … (protective bracket left armed;
nothing placed)" when a close names fewer shares than the netted position.
order_monitor recognised a defer by three phrases, none of which that message
contains, so every extended-session tick booked a FAILURE. MEASURED 2026-09-30 on
alpaca_paper SPY pkg-51f1eff527d44b2e: 49 ERROR lines in 28 min and 7 false
"won't flatten" pages (20:02:30–20:39:18Z) from operator_alerts, plus the sweep
skipping the position every sweep because the failure path kept the active-close
marker.
"""
from __future__ import annotations

from collections import namedtuple

import pytest

from src.runtime import order_monitor as om

_D = namedtuple("RetryDecision", "attempt state reason last_seen")

_SPY_MSG = (
    "extended-hours: trade-scoped exit of 8 of 11.0 on SPY DEFERRED to the "
    "regular session — the extended-hours limit path cannot close part of a "
    "symbol without either cancelling a sibling trade's protection or stacking "
    "duplicate close-limits (protective bracket left armed; nothing placed)")
_SPY_DEFER = {"ok": False, "error": _SPY_MSG,
              "exchange_response": {"retCode": 2, "retMsg": _SPY_MSG}}


class _FakeDB:
    def __init__(self, trade):
        self._trade = dict(trade)

    def get_trades(self, filters=None, limit=None):
        row = dict(self._trade)
        for k, v in (filters or {}).items():
            if str(row.get(k)) != str(v):
                return []
        return [row]

    def update_order_package(self, pkg_id, updates):
        pass

    def update_trade(self, tid, updates):
        self._trade.update(updates)


_ROW = {"id": 6100, "account_id": "alpaca_paper", "symbol": "SPY",
        "direction": "long", "position_size": 8, "status": "open",
        "order_package_id": "pkg-51f1eff527d44b2e", "is_backtest": 0}
_PKG = {"order_package_id": "pkg-51f1eff527d44b2e", "linked_trade_id": 6100,
        "strategy_name": "spy_pullback_1h", "symbol": "SPY"}
_VERDICT = {"action": "close", "reason": "sl_cross"}
_KEY = ("alpaca_paper", "SPY", "long")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for d in (om._CLOSE_FAIL_STREAK, om._CLOSE_FAIL_ALERT_AT, om._CLOSE_FAIL_ALERT_COUNT,
              om._PACKAGE_CLOSE_SKIP, om._TICK_ACTIVE_CLOSE_AT,
              om._WEDGE_REPROBE_SESSION_DEFERRED, om._PENDING_CLOSE_RETRY_COOLDOWN):
        d.clear()
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "3")
    monkeypatch.setattr(om, "_close_retry_decision_for",
                        lambda t: _D(True, "no_wedge", "no standing wedge", None))
    pages = []
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(ed, "enqueue_close_failure", lambda **k: pages.append(k))
    yield pages
    for d in (om._CLOSE_FAIL_STREAK, om._CLOSE_FAIL_ALERT_AT, om._CLOSE_FAIL_ALERT_COUNT,
              om._PACKAGE_CLOSE_SKIP, om._TICK_ACTIVE_CLOSE_AT):
        d.clear()


def _ticks(monkeypatch, result, n):
    monkeypatch.setattr(om, "_send_close_to_exchange", lambda t: dict(result))
    db = _FakeDB(_ROW)
    for _ in range(n):
        om._apply_update(db, _PKG, _VERDICT, om._StrategyTickSummary())
    return db


def test_the_spy_shape_never_pages_and_never_counts(monkeypatch, _clean):
    pages = _clean
    db = _ticks(monkeypatch, _SPY_DEFER, 10)          # 10 ticks = 5 min of 30 s ticks
    assert pages == [], "a venue defer paged 'won't flatten'"
    assert om._CLOSE_FAIL_STREAK.get(_KEY, 0) == 0, "a defer counted as a failure"
    assert db._trade["status"] == "open", "a defer must not close the row"


def test_the_spy_shape_releases_the_marker_so_the_sweep_can_check(monkeypatch):
    _ticks(monkeypatch, _SPY_DEFER, 1)
    assert not om.is_active_close("alpaca_paper", "SPY"), (
        "nothing was placed, so the sweep must still verify SPY's protection")


def test_structured_retcode_2_with_defer_text_is_a_defer(monkeypatch, _clean):
    msg = "venue: exit held for the next session (deferred)"
    _ticks(monkeypatch, {"ok": False, "error": msg,
                         "exchange_response": {"retCode": 2, "retMsg": msg}}, 5)
    assert _clean == [] and om._CLOSE_FAIL_STREAK.get(_KEY, 0) == 0


def test_retcode_2_without_defer_text_is_still_a_failure(monkeypatch, _clean):
    """An unrelated venue's retCode 2 must not buy quiet."""
    msg = "insufficient buying power"
    _ticks(monkeypatch, {"ok": False, "error": msg,
                         "exchange_response": {"retCode": 2, "retMsg": msg}}, 3)
    assert om._CLOSE_FAIL_STREAK.get(_KEY) == 3
    assert len(_clean) == 1, "a real failure must still page at the threshold"


def test_control_a_real_failure_still_pages_at_the_threshold(monkeypatch, _clean):
    _ticks(monkeypatch, {"ok": False, "error": "insufficient qty available"}, 3)
    assert om._CLOSE_FAIL_STREAK.get(_KEY) == 3
    assert len(_clean) == 1


@pytest.mark.parametrize("msg,resp,expect", [
    ("us_equity market closed — exit deferred to next session", None, True),
    ("IB venue for MHG is closed — exit deferred to next session", None, True),
    ("extended-hours limit close working — not yet filled, exit deferred", None, True),
    (_SPY_MSG, None, True),                                   # phrase alone
    ("extended-hours: position unreadable — exit deferred", None, True),
    ("something deferred", {"retCode": 2}, True),             # structured
    ("something deferred", {"retCode": 1}, False),
    ("insufficient qty available", {"retCode": 2}, False),
    ("insufficient qty available", None, False),
    ("", None, False),
])
def test_is_session_defer_table(msg, resp, expect):
    ex = {"ok": False, "error": msg}
    if resp is not None:
        ex["exchange_response"] = resp
    assert om._is_session_defer(ex, msg) is expect
