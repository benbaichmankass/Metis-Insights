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
        self._trade = trade
        self.pkg_updates = []
        self.trade_updates = []

    def get_trades(self, filters=None):
        return [dict(self._trade)]

    def update_order_package(self, pkg_id, updates):
        self.pkg_updates.append((pkg_id, updates))

    def update_trade(self, tid, updates):
        self.trade_updates.append((tid, updates))


_MATCHED = {
    "id": 2832, "account_id": "ib_paper", "symbol": "MHG",
    "direction": "long", "position_size": 3,
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
