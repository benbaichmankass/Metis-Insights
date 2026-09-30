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

#14899 review: detection is by phrase only (the regular-hours "position size …
unreadable … is DEFERRED rather than …" retCode 2 is a real failure), and an
Alpaca defer seen during regular hours escalates like a failure.
"""
from __future__ import annotations

from collections import namedtuple

import pytest

from src.runtime import order_monitor as om
from src.units.accounts.alpaca_client import AlpacaClient

_D = namedtuple("RetryDecision", "attempt state reason last_seen")


def _client(monkeypatch, position):
    cli = AlpacaClient(api_key="k", api_secret="s")
    monkeypatch.setattr(cli, "_position_raw", lambda *a: position)
    monkeypatch.setattr(cli, "position_present", lambda *a: None)

    def _no_request(*a, **k):
        raise AssertionError("a defer must place nothing")
    monkeypatch.setattr(cli, "_request", _no_request, raising=False)
    return cli


@pytest.fixture
def spy_msg(monkeypatch):
    """The REAL extended-hours trade-scoped defer text, so wording drift in
    AlpacaClient fails here (#14899 review item 4)."""
    cli = _client(monkeypatch, {"qty": "11", "side": "long", "current_price": "600"})
    out = cli._close_extended_hours("SPY", 8)
    assert out["retCode"] == 2
    return out["retMsg"]


@pytest.fixture
def unreadable_msg(monkeypatch):
    """The REAL `_resolve_close_scope` unreadable-position text — retCode 2, and
    reachable on the REGULAR-hours path, where it is a failure that must page."""
    cli = _client(monkeypatch, None)
    out = cli._resolve_close_scope("SPY", 8).envelope
    assert out["retCode"] == 2
    return out["retMsg"]


def _result(msg):
    return {"ok": False, "error": msg,
            "exchange_response": {"retCode": 2, "retMsg": msg}}


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
    monkeypatch.setattr(om, "_us_equity_session", lambda now=None: "extended")
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


def test_the_spy_shape_never_pages_and_never_counts(monkeypatch, _clean, spy_msg):
    pages = _clean
    db = _ticks(monkeypatch, _result(spy_msg), 10)    # 10 ticks = 5 min of 30 s ticks
    assert pages == [], "a venue defer paged 'won't flatten'"
    assert om._CLOSE_FAIL_STREAK.get(_KEY, 0) == 0, "a defer counted as a failure"
    assert db._trade["status"] == "open", "a defer must not close the row"


def test_the_spy_shape_releases_the_marker_so_the_sweep_can_check(monkeypatch, spy_msg):
    _ticks(monkeypatch, _result(spy_msg), 1)
    assert not om.is_active_close("alpaca_paper", "SPY"), (
        "nothing was placed, so the sweep must still verify SPY's protection")


def test_regular_hours_unreadable_retcode_2_still_pages(monkeypatch, _clean, unreadable_msg):
    """Review BLOCKING 1: retCode 2 + 'DEFERRED' from the regular-hours scope
    read is a real failure; on alpaca_live it must not sit silent forever."""
    monkeypatch.setattr(om, "_us_equity_session", lambda now=None: "rth")
    _ticks(monkeypatch, _result(unreadable_msg), 3)
    assert om._CLOSE_FAIL_STREAK.get(_KEY) == 3
    assert len(_clean) == 1, "a real failure must still page at the threshold"


def test_an_alpaca_defer_seen_in_rth_escalates(monkeypatch, _clean, spy_msg):
    """Review item 3: a defer still reported once the regular session is open
    counts toward the streak and pages — no wording stays silent past the open."""
    monkeypatch.setattr(om, "_us_equity_session", lambda now=None: "rth")
    _ticks(monkeypatch, _result(spy_msg), 3)
    assert om._CLOSE_FAIL_STREAK.get(_KEY) == 3
    assert len(_clean) == 1


def test_control_a_real_failure_still_pages_at_the_threshold(monkeypatch, _clean):
    _ticks(monkeypatch, {"ok": False, "error": "insufficient qty available"}, 3)
    assert om._CLOSE_FAIL_STREAK.get(_KEY) == 3
    assert len(_clean) == 1


@pytest.mark.parametrize("msg,session,expect", [
    ("us_equity market closed — exit deferred to next session", "closed", True),
    ("us_equity market closed — exit deferred to next session", "rth", False),
    ("extended-hours limit close working — not yet filled, exit deferred", "extended", True),
    ("extended-hours limit close working — not yet filled, exit deferred", "rth", False),
    ("extended-hours: position unreadable — exit deferred", "extended", True),
    # IB follows its own venue session, not the US equity clock.
    ("IB venue for MHG is closed — exit deferred to next session", "rth", True),
    ("IB venue for MHG is closed — exit deferred to next session", "closed", True),
    ("insufficient qty available", "extended", False),
    ("", "extended", False),
])
def test_is_session_defer_table(monkeypatch, msg, session, expect):
    monkeypatch.setattr(om, "_us_equity_session", lambda now=None: session)
    assert om._is_session_defer(msg) is expect


@pytest.mark.parametrize("session,expect", [("extended", True), ("closed", True),
                                            ("rth", False)])
def test_real_spy_text_is_a_defer_only_outside_rth(monkeypatch, spy_msg, session, expect):
    monkeypatch.setattr(om, "_us_equity_session", lambda now=None: session)
    assert om._is_session_defer(spy_msg) is expect


@pytest.mark.parametrize("session", ["rth", "extended", "closed"])
def test_real_unreadable_text_is_never_a_defer(monkeypatch, unreadable_msg, session):
    """Review BLOCKING 1: carries retCode 2 AND 'DEFERRED' — must not match."""
    monkeypatch.setattr(om, "_us_equity_session", lambda now=None: session)
    assert om._is_session_defer(unreadable_msg) is False


def test_an_unknown_session_does_not_silence_an_alpaca_defer(monkeypatch):
    """#14899 re-review: if the clock seam raises, the session is unknown, and
    an Alpaca defer must count toward the streak rather than be honoured."""
    def _boom(now=None):
        raise RuntimeError("clock unavailable")
    monkeypatch.setattr(om, "_us_equity_session", _boom)
    msg = ("us_equity market closed — exit deferred to next session "
           "(protective bracket left armed)")
    assert om._is_session_defer(msg) is False
