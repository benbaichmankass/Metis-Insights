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

import itertools

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
    om._CLOSE_FAIL_ALERT_AT.clear()
    om._CLOSE_FAIL_ALERT_COUNT.clear()
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
    om._CLOSE_FAIL_ALERT_AT.clear()
    om._CLOSE_FAIL_ALERT_COUNT.clear()
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


# --------------------- close-failure alert BACKOFF (BL-20260901-CLOSE-FAIL-
# ALARM-DESENSITISED, the alpaca_paper GLD pages)
#
# The repeat cadence used to be a fixed modulo of the streak
# (`_streak % _after == 0`) — tied to the tick rate, never widening, never
# capped. At the 30s EXIT_LOOP_INTERVAL_SECONDS default that is a page every
# 90s for as long as the position stays wedged (~160 over one extended-hours
# session). The operator walks past an alarm that behaves like that, which is
# the desensitised-alarm failure CLAUDE.md calls a bug in its own right.
#
# NOTE the property these pin that the pre-existing tests did NOT: those only
# ever drove the FIRST alert, so the repeat cadence was entirely uncovered.


def _reset_alert_state():
    om._CLOSE_FAIL_STREAK.clear()
    om._CLOSE_FAIL_ALERT_AT.clear()
    om._CLOSE_FAIL_ALERT_COUNT.clear()


def test_alert_backoff_first_page_at_threshold_then_quiet(monkeypatch):
    """Pages at the threshold, then stays QUIET on the immediately-following
    failures — where the old fixed modulo paged again at 2x the threshold."""
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "3")
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_BACKOFF_S", "300")
    _reset_alert_state()
    key = ("alpaca_paper", "GLD", "long")

    assert om._should_alert_close_failure(key, 1, now=0.0) is False
    assert om._should_alert_close_failure(key, 2, now=30.0) is False
    assert om._should_alert_close_failure(key, 3, now=60.0) is True   # first page
    # Ticks 4..10 land inside the 300s backoff → silent. The OLD behaviour
    # would have paged at streak 6 and 9.
    for streak, t in [(4, 90.0), (5, 120.0), (6, 150.0), (9, 240.0), (10, 270.0)]:
        assert om._should_alert_close_failure(key, streak, now=t) is False, streak


def test_alert_backoff_widens_and_caps_but_never_silences(monkeypatch):
    """Interval doubles per page, clamps at the max, and keeps pinging forever —
    a wedged position must never become SILENT."""
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "1")
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_BACKOFF_S", "100")
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_MAX_BACKOFF_S", "400")
    _reset_alert_state()
    key = ("alpaca_paper", "GLD", "long")

    now = 0.0
    fired = []
    # Drive 4h of 30s ticks; record when a page fires.
    for i in range(480):
        if om._should_alert_close_failure(key, i + 1, now=now):
            fired.append(now)
        now += 30.0

    gaps = [round(b - a) for a, b in itertools.pairwise(fired)]
    assert fired[0] == 0.0                       # first failure pages at once
    assert gaps[:3] == [120, 210, 420], gaps     # ~100 → 200 → 400, tick-quantised
    assert all(g <= 420 for g in gaps), gaps     # clamped at the max
    assert all(g >= 120 for g in gaps), gaps     # never faster than the floor
    # Never silent: it kept paging to the end of the window...
    assert fired[-1] > now - 500
    # ...but bounded by the cap: ~window/max_interval pages, not one per tick.
    # 4h / 400s ≈ 36, vs 480 pages from the old every-tick cadence at AFTER=1.
    assert 30 <= len(fired) <= 42, len(fired)


def test_alert_backoff_default_knobs_page_count_over_a_wedged_session(monkeypatch):
    """The operator-facing number, at the SHIPPED defaults: a position wedged for
    a whole 4h extended-hours session at the 30s exit-loop tick.

    Old cadence (fixed modulo, AFTER=3): a page every 3rd tick = every 90s = 160
    pages. That is the alarm the operator learned to walk past.
    """
    monkeypatch.delenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", raising=False)
    monkeypatch.delenv("MONITOR_CLOSE_FAIL_ALERT_BACKOFF_S", raising=False)
    monkeypatch.delenv("MONITOR_CLOSE_FAIL_ALERT_MAX_BACKOFF_S", raising=False)
    _reset_alert_state()
    key = ("alpaca_paper", "GLD", "long")

    now, fired = 0.0, []
    for i in range(480):                      # 480 ticks x 30s = 4 hours
        if om._should_alert_close_failure(key, i + 1, now=now):
            fired.append(now)
        now += 30.0

    old_cadence_pages = 480 // om._DEFAULT_CLOSE_FAIL_ALERT_AFTER   # 160
    assert old_cadence_pages == 160
    assert len(fired) <= 10, fired            # ~7 at the shipped defaults
    assert len(fired) >= 4, fired             # still clearly audible
    assert fired[0] == 60.0                   # 3rd consecutive failure, unchanged
    # Final gap has widened to the hourly clamp, and it never went silent.
    assert round(fired[-1] - fired[-2]) == round(
        om._DEFAULT_CLOSE_FAIL_ALERT_MAX_BACKOFF_S / 30.0) * 30


def test_alert_backoff_cleared_state_pages_immediately(monkeypatch):
    """A confirmed close / market DEFER clears the backoff, so the NEXT genuine
    failure pages at the threshold again rather than inheriting a wide interval."""
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "1")
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_BACKOFF_S", "300")
    _reset_alert_state()
    key = ("alpaca_paper", "GLD", "long")

    assert om._should_alert_close_failure(key, 1, now=0.0) is True
    assert om._should_alert_close_failure(key, 2, now=60.0) is False
    om._clear_close_fail_alert_state(key)        # what a defer / success does
    assert key not in om._CLOSE_FAIL_STREAK
    assert om._should_alert_close_failure(key, 1, now=90.0) is True


def test_alert_backoff_env_fallbacks_are_safe(monkeypatch):
    """Unparseable knobs fall back to the defaults rather than disabling paging."""
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "1")
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_BACKOFF_S", "not-a-number")
    _reset_alert_state()
    key = ("alpaca_paper", "GLD", "long")
    assert om._should_alert_close_failure(key, 1, now=0.0) is True
    # default 300s floor applies, so a 60s-later failure is still quiet
    assert om._should_alert_close_failure(key, 2, now=60.0) is False
    assert om._should_alert_close_failure(
        key, 3, now=0.0 + om._DEFAULT_CLOSE_FAIL_ALERT_BACKOFF_S + 1) is True
