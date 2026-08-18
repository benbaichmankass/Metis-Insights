"""Close-retry cooldown — BL-20260624-MHG-CLOSE-CONFIRM follow-up.

After IBClient.close confirms-flatten and returns retCode 1 ("not confirmed
flat") for a close that was accepted but never filled, the monitor's full-close
path (`_apply_update`) must NOT re-attempt the active close every tick — doing so
cancels the re-armed protective bracket and places another non-filling order
(churn). It defers the active close for `IB_CLOSE_RETRY_COOLDOWN_S`, leaving the
bracket armed, and clears the cooldown on a confirmed close.

These tests patch `_send_close_to_exchange` so no IB/Bybit I/O happens.
"""
from __future__ import annotations

import pytest

from src.runtime import order_monitor as om


@pytest.fixture(autouse=True)
def _clear_cooldown_state():
    om._PENDING_CLOSE_RETRY_COOLDOWN.clear()
    yield
    om._PENDING_CLOSE_RETRY_COOLDOWN.clear()


# --------------------------------------------------------------------------- #
# _close_retry_cooldown_seconds parsing
# --------------------------------------------------------------------------- #


def test_cooldown_seconds_default_when_unset(monkeypatch):
    monkeypatch.delenv("IB_CLOSE_RETRY_COOLDOWN_S", raising=False)
    assert om._close_retry_cooldown_seconds() == float(
        om._DEFAULT_CLOSE_RETRY_COOLDOWN_SECONDS
    )


def test_cooldown_seconds_reads_env(monkeypatch):
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "45")
    assert om._close_retry_cooldown_seconds() == 45.0


def test_cooldown_seconds_clamps_negative_and_zero_disables(monkeypatch):
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "-10")
    assert om._close_retry_cooldown_seconds() == 0.0
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "0")
    assert om._close_retry_cooldown_seconds() == 0.0


def test_cooldown_seconds_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "not-a-number")
    assert om._close_retry_cooldown_seconds() == float(
        om._DEFAULT_CLOSE_RETRY_COOLDOWN_SECONDS
    )


# --------------------------------------------------------------------------- #
# Cooldown gating in _apply_update's full-close path
# --------------------------------------------------------------------------- #


class _FakeDB:
    """Minimal db surface used by _apply_update's close branch."""

    def __init__(self, trade):
        # COPY: update_trade now reflects the close onto this row, and the
        # callers pass a module-level literal — sharing it would leak a
        # `status: closed` into every later test in the file.
        self._trade = dict(trade)
        self.pkg_updates = []
        self.trade_updates = []

    def get_trades(self, filters=None, limit=None):
        # 2026-08-18: this fake used to return the row unconditionally and
        # never reflect `update_trade`, so the row was permanently "open" no
        # matter what the code under test did. That fiction was invisible
        # while the close path resolved ONE leg by id and never re-read; the
        # package-wide fix re-reads to decide whether any leg remains, so an
        # unfaithful fake now reports a package that never drains. Honour the
        # filters and the recorded status — the pairs-suite lesson about tests
        # passing against a schema production does not have.
        if self._trade.get("status") == "closed":
            return []
        row = dict(self._trade)
        for k, v in (filters or {}).items():
            if str(row.get(k)) != str(v):
                return []
        return [row]

    def update_order_package(self, pkg_id, updates):
        self.pkg_updates.append((pkg_id, updates))

    def update_trade(self, tid, updates):
        self.trade_updates.append((tid, updates))
        if str(tid) == str(self._trade.get("id")):
            self._trade.update(updates)


_MATCHED = {
    "id": 2832, "account_id": "ib_paper", "symbol": "MHG",
    "direction": "long", "position_size": 3,
    # Required by the package-leg resolver's filters (2026-08-18).
    "status": "open", "order_package_id": "pkg-f58a249d", "is_backtest": 0,
}
_OPEN_PKG = {
    "order_package_id": "pkg-f58a249d", "linked_trade_id": 2832,
    "strategy_name": "mhg_pullback_1d", "symbol": "MHG",
}
_VERDICT = {"action": "close", "reason": "sl_cross"}

_UNCONFIRMED = {
    "ok": False,
    "error": ("close not confirmed flat: live_qty=3 after ~6.0s — close order "
              "123 was accepted but the position is still open"),
}


def _patch_close(monkeypatch, results):
    """Patch _send_close_to_exchange to pop canned results; counts calls."""
    calls = {"n": 0}

    def _fake(_trade):
        i = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[i]

    monkeypatch.setattr(om, "_send_close_to_exchange", _fake)
    return calls


def test_unconfirmed_close_arms_cooldown_and_defers_next_tick(monkeypatch):
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "300")
    calls = _patch_close(monkeypatch, [_UNCONFIRMED])
    db = _FakeDB(_MATCHED)

    # Tick 1: close attempted, comes back unconfirmed → cooldown armed.
    s1 = om._StrategyTickSummary()
    om._apply_update(db, _OPEN_PKG, _VERDICT, s1)
    assert calls["n"] == 1
    assert s1.error_count == 1
    key = ("ib_paper", "MHG", "long")
    assert key in om._PENDING_CLOSE_RETRY_COOLDOWN

    # Tick 2 (within cooldown): active close DEFERRED — no second exchange call,
    # DB left untouched, counted as no_change so the bracket stays armed.
    s2 = om._StrategyTickSummary()
    om._apply_update(db, _OPEN_PKG, _VERDICT, s2)
    assert calls["n"] == 1            # NOT re-attempted
    assert s2.no_change_count == 1
    assert db.pkg_updates == [] and db.trade_updates == []


def test_cooldown_zero_disables_defer(monkeypatch):
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "0")
    calls = _patch_close(monkeypatch, [_UNCONFIRMED, _UNCONFIRMED])
    db = _FakeDB(_MATCHED)

    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    # With the cooldown disabled the close is retried every tick (legacy churn).
    # The marker is still recorded but the gate ignores it (cooldown <= 0).
    assert calls["n"] == 2
    assert ("ib_paper", "MHG", "long") in om._PENDING_CLOSE_RETRY_COOLDOWN


def test_confirmed_close_clears_cooldown(monkeypatch):
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "300")
    # Pre-arm the cooldown as if a prior tick saw an unconfirmed close.
    key = ("ib_paper", "MHG", "long")
    om._PENDING_CLOSE_RETRY_COOLDOWN[key] = om.datetime.now(om.timezone.utc)
    # But disable the gate for THIS tick so the close runs and confirms.
    monkeypatch.setenv("IB_CLOSE_RETRY_COOLDOWN_S", "0")
    _patch_close(monkeypatch, [{"ok": True, "exchange_order_id": "X",
                                "exchange_response": {"retCode": 0}, "error": None}])
    db = _FakeDB(_MATCHED)

    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    # Confirmed close clears the marker and writes the DB close.
    assert key not in om._PENDING_CLOSE_RETRY_COOLDOWN
    assert db.pkg_updates and db.trade_updates


# --------------------------------------------------------------------------- #
# Consecutive close-failure alert (item #3)
# --------------------------------------------------------------------------- #


_GENERIC_FAIL = {"ok": False, "error": "venue error retCode=10001 SL race"}
_OK = {"ok": True, "exchange_order_id": "X",
       "exchange_response": {"retCode": 0}, "error": None}


def test_close_fail_streak_alerts_at_threshold(monkeypatch):
    from src.runtime import execution_diagnostics as ed
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "3")
    om._CLOSE_FAIL_STREAK.clear()
    alerts = []
    monkeypatch.setattr(ed, "enqueue_close_failure", lambda **kw: alerts.append(kw))
    _patch_close(monkeypatch, [_GENERIC_FAIL])
    db = _FakeDB(_MATCHED)

    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    assert alerts == []                       # below threshold, silent retry
    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    assert len(alerts) == 1                    # 3rd consecutive failure → alert
    assert alerts[0]["consecutive"] == 3
    assert alerts[0]["symbol"] == "MHG"
    assert alerts[0]["account"] == "ib_paper"


def test_close_fail_streak_reset_on_success(monkeypatch):
    from src.runtime import execution_diagnostics as ed
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "2")
    om._CLOSE_FAIL_STREAK.clear()
    alerts = []
    monkeypatch.setattr(ed, "enqueue_close_failure", lambda **kw: alerts.append(kw))
    # fail (streak 1) → success (clears) → fail on a LATER position (streak 1
    # again) — never reaches 2.
    #
    # Restructured 2026-08-18: this used to drive three calls against one
    # _FakeDB, which worked only because the fake never reflected the close and
    # so served a permanently-open row. Now that the close is reflected (the
    # package-wide path re-reads to decide whether any leg remains), the third
    # call has to act on a genuinely new position — which is also the property
    # actually worth pinning: a cleared streak must start FRESH for the next
    # position on the same (account, symbol, direction), not resume.
    _patch_close(monkeypatch, [_GENERIC_FAIL, _OK, _GENERIC_FAIL])
    key = ("ib_paper", "MHG", "long")

    db = _FakeDB(_MATCHED)
    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    assert om._CLOSE_FAIL_STREAK[key] == 1

    om._apply_update(db, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    assert key not in om._CLOSE_FAIL_STREAK, "a confirmed close must clear it"

    db2 = _FakeDB(_MATCHED)  # a later position on the same signature
    om._apply_update(db2, _OPEN_PKG, _VERDICT, om._StrategyTickSummary())
    assert alerts == []
    assert om._CLOSE_FAIL_STREAK[key] == 1
