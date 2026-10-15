"""ORDER-AUDIT-3 P0: a resting stop the venue is not executing.

LIVE, read 2026-10-15 via /api/diag/alpaca_open_orders + journalctl:
alpaca_paper GLD 39 sh long (entry 419.51, unrealised -1581 -> mark ~379),
OCO stop leg 891ddff0 @416.09 `held` and TP 2e843e04 @461.27 `new`, both
resting since 2026-08-27; a cancel Alpaca ACCEPTED left both in place
(share_hold=cancel_accepted_ineffective), so every close is refused
"insufficient qty available (available: 0)". The equity naked sweep's
`if state.get("stop"): ... continue` counted that stop -- ~37 points through
the market -- as a healthy `stop_resting` on every sweep.
"""
from __future__ import annotations

from src.runtime import order_monitor as om
from tests import test_alpaca_rearm_preflight as _pre
from tests.test_alpaca_rearm_preflight import _Venue, _close_row, _kinds, _use

world = _pre.world  # the replay fixture, reused


def _resting_oco(v, stop):
    v.resting.append({
        "id": "oco-1", "symbol": "QQQ", "order_class": "oco", "type": "limit",
        "side": "sell", "qty": "56", "limit_price": "787.86", "status": "new",
        "legs": [{"id": "stop-1", "symbol": "QQQ", "order_class": "oco",
                  "type": "stop", "side": "sell", "qty": "56",
                  "stop_price": str(stop), "status": "held", "legs": None}]})


def _sweeps(db, n):
    return [om._check_broker_naked_equity_positions(db) for _ in range(n)]


def test_a_breached_resting_stop_is_not_counted_healthy(world):
    db, pages, mp = world
    om._RESTING_STOP_BREACH_STREAK.clear()
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=680.0)          # fresh print ~37 through 716.80
    _resting_oco(v, 716.80)
    _use(mp, v)
    s1, s2, s3 = _sweeps(db, 3)
    # a stop may sit breached for a moment while it fires: not flagged at once
    assert s1["stop_resting"] == 1 and s2["stop_resting"] == 1
    assert s3["stop_resting_breached"] == 1 and s3["stop_resting"] == 0
    assert _kinds(pages).count("resting_stop_not_executing") == 1
    # detection only: nothing placed, nothing cancelled
    assert v.posts() == [] and not [c for c in v.calls if c[0] == "DELETE"]


def test_a_resting_stop_above_the_market_stays_healthy(world):
    db, pages, mp = world
    om._RESTING_STOP_BREACH_STREAK.clear()
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=740.0)
    _resting_oco(v, 716.80)
    _use(mp, v)
    for s in _sweeps(db, 4):
        assert s["stop_resting"] == 1 and s["stop_resting_breached"] == 0
    assert "resting_stop_not_executing" not in _kinds(pages)


def test_outside_rth_a_breached_resting_stop_is_not_flagged(world):
    """No stop triggers outside the regular session -- resting is correct."""
    db, pages, mp = world
    om._RESTING_STOP_BREACH_STREAK.clear()
    _close_row(db, 5928)
    mp.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "closed")
    v = _Venue(qty=56.0, price=680.0)
    _resting_oco(v, 716.80)
    _use(mp, v)
    for s in _sweeps(db, 4):
        assert s["stop_resting"] == 1
    assert "resting_stop_not_executing" not in _kinds(pages)


def test_a_known_standing_wedge_is_counted_but_not_paged_again(world):
    """Operator 2026-10-15 on the GLD wedge: "Leave it for now". The wedge
    ledger already re-pages that position on its own floor; this detector
    must not add a second page for it."""
    db, pages, mp = world
    om._RESTING_STOP_BREACH_STREAK.clear()
    _close_row(db, 5928)
    mp.setattr(om, "_has_standing_wedge", lambda a, s, d: True)
    v = _Venue(qty=56.0, price=680.0)
    _resting_oco(v, 716.80)
    _use(mp, v)
    s = _sweeps(db, 4)[-1]
    assert s["stop_resting_breached"] == 1
    assert "resting_stop_not_executing" not in _kinds(pages)


def test_the_page_fires_once_per_cooldown_not_every_sweep(world):
    db, pages, mp = world
    om._RESTING_STOP_BREACH_STREAK.clear()
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=680.0)
    _resting_oco(v, 716.80)
    _use(mp, v)
    _sweeps(db, 8)
    assert _kinds(pages).count("resting_stop_not_executing") == 1
