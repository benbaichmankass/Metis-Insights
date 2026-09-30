"""PI-20260930-ZIFJ1RKM-0004 and -0005, root-caused on the live trader 2026-09-30.

-0004. In EXTENDED hours the equity sweep's breach exit reaches
`_close_extended_hours`, which POSTs a DAY close-limit and returns "extended-hours
limit close working — not yet filled, exit deferred". `_exit_alpaca_row` matched
the word "deferred" and returned `deferred`, and the sweep then re-armed AT ONCE:
`_scoped_rearm_cancel` counts a reducing limit sized to the row as the position's
own leg, so the re-arm CANCELLED the exit just placed and put back a stop Alpaca
does not trigger outside regular hours. `_exit_alpaca_row` now returns `working`
for that case: no re-arm, no top-up, and the key is marked actively closing.

-0005. `close_wedge_standing.observe()` was reached only on the alert path, gated
by the close-failure streak, and a market-session defer resets that streak. So
after every closed session the wedge re-probe ran on every tick until streak 3.
MEASURED on alpaca_paper GLD: probes at 08:02:06 / 08:02:22 / 08:02:51, then
suppression at 08:03:21. `touch_standing` now refreshes `last_seen` on the first
re-evidencing failure. Paging stays on the alert path, unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import close_wedge_standing as cws
from src.runtime import order_monitor as om
from tests.test_alpaca_rearm_preflight import (  # noqa: F401 — `world` is a fixture
    _close_row, _set_rows, _use, _Venue, world,
)

# `world` stubs `is_active_close` to False; the tests that read the marker need
# the real one. Captured before any fixture runs.
_REAL_IS_ACTIVE_CLOSE = om.is_active_close

_WORKING = {"ok": False, "error": (
    "extended-hours limit close working — not yet filled, exit deferred")}
_CLOSED = {"ok": False, "error": (
    "us_equity market closed — exit deferred to next session "
    "(protective bracket left armed)")}


@pytest.fixture(autouse=True)
def _clean_markers():
    om._TICK_ACTIVE_CLOSE_AT.clear()
    yield
    om._TICK_ACTIVE_CLOSE_AT.clear()


def _record_scoped_cancel(mp, venue):
    """Wrap the venue's REAL `_scoped_rearm_cancel` so each call is recorded."""
    calls = []
    real = venue._scoped_rearm_cancel

    def _spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    mp.setattr(venue, "_scoped_rearm_cancel", _spy)
    return calls


# ── -0004: _exit_alpaca_row's return ────────────────────────────────────────


def _row(tid=6024, qty=56.0):
    return {"id": tid, "direction": "long", "position_size": qty, "notes": None}


def test_working_close_limit_returns_working_and_marks_active(monkeypatch):
    monkeypatch.setattr(om, "_send_close_to_exchange", lambda m: dict(_WORKING))
    res = om._exit_alpaca_row(None, _row(), "alpaca_live", "QQQ",
                              reason="sl", why="t")
    assert res == "working"
    assert om.is_active_close("alpaca_live", "QQQ"), (
        "a working close-limit must mark the key so later sweeps leave it alone")


def test_closed_session_defer_is_still_deferred_and_marks_nothing(monkeypatch):
    monkeypatch.setattr(om, "_send_close_to_exchange", lambda m: dict(_CLOSED))
    res = om._exit_alpaca_row(None, _row(), "alpaca_live", "QQQ",
                              reason="sl", why="t")
    assert res == "deferred"
    assert not om.is_active_close("alpaca_live", "QQQ")


# ── -0004: the sweep ────────────────────────────────────────────────────────


def test_sweep_breach_exit_working_limit_is_not_rearmed(world, monkeypatch):  # noqa: F811
    """The 0004 shape: QQQ gapped to 710, below 6024's 716.80 stop, extended
    hours. The breach exit's close-limit is working, so NOTHING may reach
    `_scoped_rearm_cancel` (it would cancel that limit) and no OCO is POSTed."""
    db, _pages, mp = world
    mp.setattr(om, "is_active_close", _REAL_IS_ACTIVE_CLOSE)
    _close_row(db, 5928)
    venue = _Venue(qty=56.0, price=710.0)
    _use(mp, venue)
    scoped = _record_scoped_cancel(mp, venue)
    mp.setattr(om, "_send_close_to_exchange", lambda m: dict(_WORKING))

    s = om._check_broker_naked_equity_positions(db)
    assert s["exit_working"] == 1
    assert s["exit_deferred"] == 0 and s["exit_deferred_rearmed"] == 0
    assert scoped == [], "_scoped_rearm_cancel was reached over a working close-limit"
    assert venue.posts() == [], "an OCO was POSTed over a working close-limit"
    assert s["unaccounted"] == 0

    # The NEXT sweep sees the key marked and leaves it to flatten.
    s2 = om._check_broker_naked_equity_positions(db)
    assert s2["active_close_skipped"] == 1
    assert scoped == [] and venue.posts() == []


def test_control_closed_session_defer_still_rests_a_stop(world, monkeypatch):  # noqa: F811
    """Control: the SAME shape with the closed-session defer must still re-arm
    through the real `place_protective` path, so the probe above can see a
    positive."""
    db, _pages, mp = world
    mp.setattr(om, "is_active_close", _REAL_IS_ACTIVE_CLOSE)
    _close_row(db, 5928)
    venue = _Venue(qty=56.0, price=710.0)
    _use(mp, venue)
    scoped = _record_scoped_cancel(mp, venue)
    mp.setattr(om, "_send_close_to_exchange", lambda m: dict(_CLOSED))

    s = om._check_broker_naked_equity_positions(db)
    assert s["exit_deferred"] == 1 and s["exit_working"] == 0
    assert len(scoped) >= 1, "control: the real re-arm path never ran"
    assert s["unaccounted"] == 0


def test_exit_working_is_a_per_row_outcome_and_topup_working_is_not():
    assert "exit_working" not in om._EQUITY_SWEEP_NON_OUTCOME_KEYS
    assert "topup_exit_working" in om._EQUITY_SWEEP_NON_OUTCOME_KEYS


# ── -0005: touch_standing ───────────────────────────────────────────────────

_OLD = datetime(2026, 9, 29, 23, 22, 43, tzinfo=timezone.utc)
_NOW = datetime(2026, 9, 30, 8, 2, 15, tzinfo=timezone.utc)
_ENTRY = {
    "account": "alpaca_paper", "symbol": "GLD", "side": "long",
    "share_hold": "cancel_accepted_ineffective",
    "detail": "d", "evidence": "891ddff0:held",
    "first_seen": "2026-09-08T14:12:54+00:00",
    "last_seen": _OLD.isoformat(),
    "last_paged_at": "2026-09-29T08:00:00+00:00",
    "pages_suppressed": 7,
}


def _seed(tmp_path, entry=_ENTRY):
    p = tmp_path / "close_wedge_standing.json"
    key = cws.wedge_key("alpaca_paper", "GLD", "long")
    p.write_text(json.dumps({"schema": cws.SCHEMA, "wedges": {key: dict(entry)}}))
    return p, key


def _entry(p, key):
    return json.loads(p.read_text())["wedges"][key]


@pytest.mark.parametrize("hold", ["broker_cancel_wedged", "cancel_accepted_ineffective"])
def test_touch_refreshes_last_seen_only(tmp_path, hold):
    p, key = _seed(tmp_path)
    assert cws.touch_standing("alpaca_paper", "GLD", "long", hold, now=_NOW, path=p)
    e = _entry(p, key)
    assert e["last_seen"] == _NOW.isoformat()
    # Nothing the paging policy reads may move: a hold flip must not be
    # recorded here, or the alert path's next observe() loses its transition.
    for k in ("share_hold", "evidence", "first_seen", "last_paged_at",
              "pages_suppressed", "detail"):
        assert e[k] == _ENTRY[k], k


def test_touch_never_creates_an_entry(tmp_path):
    """A first sighting is observe()'s: it must still page as newly_wedged."""
    p = tmp_path / "close_wedge_standing.json"
    p.write_text(json.dumps({"schema": cws.SCHEMA, "wedges": {}}))
    assert not cws.touch_standing("alpaca_paper", "GLD", "long",
                                  "broker_cancel_wedged", now=_NOW, path=p)
    assert json.loads(p.read_text())["wedges"] == {}


@pytest.mark.parametrize("hold", ["not_classified", "orders_still_resting", "", None])
def test_touch_ignores_a_clearable_or_unclassified_reading(tmp_path, hold):
    p, key = _seed(tmp_path)
    assert not cws.touch_standing("alpaca_paper", "GLD", "long", hold, now=_NOW, path=p)
    assert _entry(p, key)["last_seen"] == _OLD.isoformat()


def test_touch_on_an_unreadable_store_writes_nothing(tmp_path):
    p = tmp_path / "close_wedge_standing.json"
    p.write_text("{not json")
    assert not cws.touch_standing("alpaca_paper", "GLD", "long",
                                  "broker_cancel_wedged", now=_NOW, path=p)
    assert p.read_text() == "{not json"


def test_after_touch_the_retry_decision_is_suppressed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOSE_WEDGE_REPROBE_MINUTES", "60")
    p, _key = _seed(tmp_path)
    before = cws.close_retry_decision("alpaca_paper", "GLD", "long", now=_NOW, path=p)
    assert before.state == "reprobe_due"
    cws.touch_standing("alpaca_paper", "GLD", "long", "broker_cancel_wedged",
                       now=_NOW, path=p)
    after = cws.close_retry_decision(
        "alpaca_paper", "GLD", "long", now=_NOW + timedelta(seconds=30), path=p)
    assert after.state == "suppressed", after


# ── -0005: end to end through _apply_update ─────────────────────────────────

_WEDGED = {"ok": False, "error": (
    "extended-hours limit close rejected: insufficient qty available for order "
    "(requested: 39, available: 0) [share_hold=broker_cancel_wedged: 1 order(s) "
    "wedged broker-side — Alpaca REFUSED our cancel naming the order's own stuck "
    "status (891ddff0-b24d-4039-9322-8492bcd51e66 refused with 'order pending "
    "cancel')]")}


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


def test_first_reevidencing_failure_resumes_suppression(tmp_path, monkeypatch):
    """The 08:02Z shape: a standing wedge whose last_seen is 519 min old, the
    streak reset by the overnight defer. ONE failing re-probe must be enough;
    before, the next two ticks re-probed as well."""
    monkeypatch.setenv("CLOSE_WEDGE_REPROBE_MINUTES", "60")
    monkeypatch.setenv("MONITOR_CLOSE_FAIL_ALERT_AFTER", "3")
    p, key = _seed(tmp_path)
    monkeypatch.setattr(cws, "STANDING_LOG", p)
    for d in (om._CLOSE_FAIL_STREAK, om._CLOSE_FAIL_ALERT_AT,
              om._CLOSE_FAIL_ALERT_COUNT, om._PACKAGE_CLOSE_SKIP,
              om._WEDGE_REPROBE_SESSION_DEFERRED, om._PENDING_CLOSE_RETRY_COOLDOWN):
        d.clear()
    sends = []
    monkeypatch.setattr(om, "_send_close_to_exchange",
                        lambda m: sends.append(m) or dict(_WEDGED))
    alerts = []
    import src.runtime.execution_diagnostics as ed
    monkeypatch.setattr(ed, "enqueue_close_failure", lambda **k: alerts.append(k))

    trade = {"id": 6200, "account_id": "alpaca_paper", "symbol": "GLD",
             "direction": "long", "position_size": 39, "status": "open",
             "order_package_id": "pkg-6a8e3fb325464be3", "is_backtest": 0}
    pkg = {"order_package_id": "pkg-6a8e3fb325464be3", "linked_trade_id": 6200,
           "strategy_name": "gld_pullback_1h", "symbol": "GLD"}
    db = _FakeDB(trade)
    for _ in range(3):
        om._apply_update(db, pkg, {"action": "close", "reason": "sl_cross"},
                         om._StrategyTickSummary())
    assert len(sends) == 1, f"re-probed {len(sends)} times; the first failure must suppress"
    assert alerts == [], "alert semantics changed: paged before the streak threshold"
    e = _entry(p, key)
    assert e["last_seen"] != _OLD.isoformat()
    assert e["share_hold"] == _ENTRY["share_hold"], (
        "touch recorded the hold flip; that is observe()'s job on the alert path")
    for d in (om._CLOSE_FAIL_STREAK, om._CLOSE_FAIL_ALERT_AT,
              om._CLOSE_FAIL_ALERT_COUNT, om._PACKAGE_CLOSE_SKIP):
        d.clear()
