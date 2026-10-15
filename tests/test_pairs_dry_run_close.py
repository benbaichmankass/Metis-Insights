"""ORDER-AUDIT-3 / PI-20260927-ECILBBVH-0004: a ``mode: dry_run`` account must
never have a pairs close, unwind or TP amend sent to the venue.

Before the fix ``_close_pair`` / ``_unwind_legs`` / ``_revise_leg_tps`` called
``close_open_position`` / ``modify_open_order`` with no mode gate, unlike the
order_monitor close helpers (``{"ok": True, "skipped": "dry_run"}``) and unlike
``execute_pkg`` placement. A pair opened dry was closed LIVE.
"""
from __future__ import annotations

import pytest


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []

    def get_trades(self, filters=None, limit=None):
        strat = (filters or {}).get("strategy_name")
        return [r for r in self._rows if r.get("strategy_name") == strat][: (limit or 99)]

    def update_trade(self, trade_id, payload):
        self.updates.append((trade_id, payload))


@pytest.fixture
def wired(monkeypatch):
    from src.units.strategies import pairs_executor as px
    import src.units.db.database as dbmod
    import src.units.accounts.execute as execmod

    rows = [
        {"id": 1, "strategy_name": "pairs_x_a", "direction": "long",
         "position_size": 2.0, "take_profit_1": 10.0},
        {"id": 2, "strategy_name": "pairs_x_b", "direction": "short",
         "position_size": 3.0, "take_profit_1": 20.0},
    ]
    db = _FakeDb(rows)
    venue_calls = []
    monkeypatch.setattr(dbmod, "Database", lambda *a, **k: db, raising=False)
    monkeypatch.setattr(execmod, "close_open_position",
                        lambda *a, **k: venue_calls.append(("close", k)) or {"ok": True})
    monkeypatch.setattr(execmod, "modify_open_order",
                        lambda *a, **k: venue_calls.append(("amend", k)) or {"ok": True})
    monkeypatch.setattr(px, "_cascade_close_pair_package", lambda *a, **k: True)
    monkeypatch.setattr(px, "_leg_strats", lambda p: ("pairs_x_a", "pairs_x_b"))
    return px, db, venue_calls


PAIR = {"symbol_a": "AAA", "symbol_b": "BBB", "name": "x"}


@pytest.mark.parametrize("mode", ["dry_run", "dry", "dry-run", "paper"])
def test_dry_close_never_reaches_the_venue_but_is_journalled(wired, mode):
    px, db, venue_calls = wired
    out = px._close_pair(object(), {"account_id": "a", "mode": mode}, PAIR,
                         "revert", 100.0, 50.0)
    assert venue_calls == []
    assert out["closed"] is True
    assert [u[1]["status"] for u in db.updates] == ["closed", "closed"]


def test_live_close_still_reaches_the_venue(wired):
    """Control: the gate must not swallow live closes."""
    px, db, venue_calls = wired
    px._close_pair(object(), {"account_id": "a", "mode": "live"}, PAIR,
                   "revert", 100.0, 50.0)
    assert [c[0] for c in venue_calls] == ["close", "close"]


def test_dry_unwind_never_reaches_the_venue(wired):
    px, _db, venue_calls = wired
    naked = px._unwind_legs(object(), {"account_id": "a", "mode": "dry_run"},
                            [("AAA", "long", 2.0)])
    assert venue_calls == [] and naked == []


def test_live_unwind_still_reaches_the_venue(wired):
    px, _db, venue_calls = wired
    px._unwind_legs(object(), {"account_id": "a"}, [("AAA", "long", 2.0)])
    assert [c[0] for c in venue_calls] == ["close"]


def test_dry_tp_revision_never_reaches_the_venue(wired, monkeypatch):
    px, db, venue_calls = wired
    monkeypatch.setattr(px.pe, "reversion_target_spread",
                        lambda *a, **k: {"spread_now": 1.0, "target_spread": 0.0, "beta": 1.0})
    monkeypatch.setattr(px.pe, "leg_target_prices",
                        lambda *a, **k: {"tp_a": 11.0, "tp_b": 19.0})
    out = px._revise_leg_tps(object(), {"account_id": "a", "mode": "dry_run"}, PAIR,
                             None, [1.0], [1.0], "long_spread")
    assert venue_calls == []
    assert all(r["revised"] for r in out)
    assert {u[1]["take_profit_1"] for u in db.updates} == {11.0, 19.0}
