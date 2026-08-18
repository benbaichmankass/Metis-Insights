"""Package-wide effectuation: a verdict must reach every leg of the package.

BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG. `Coordinator
.multi_account_execute` fans ONE order package across N accounts, so N trade
rows share one `order_package_id` while `linked_trade_id` names exactly one.
Both effectuation branches resolved that single id, so N-1 legs were never
trailed and never closed — and a monitor close flipped the PARENT package to
`closed`, dropping the survivors out of the loop's `status="open"` selection
for good. Measured live 2026-08-18: 4 of 8 multi-leg open packages carried
sibling stops that had never moved, and 6 of 35 open trades were stranded.
"""
from __future__ import annotations

from typing import Any, Dict, List


from src.runtime import order_monitor as om


class FakeDB:
    """Minimal stand-in for the two accessors the resolver uses."""

    def __init__(self, trades: List[Dict[str, Any]]):
        self.trades = trades
        self.package_writes: List[Dict[str, Any]] = []
        self.trade_writes: List[tuple] = []
        self.raise_on_get = False

    def get_trades(self, filters=None, limit=None):
        if self.raise_on_get:
            raise RuntimeError("db locked")
        rows = self.trades
        for k, v in (filters or {}).items():
            rows = [r for r in rows if str(r.get(k)) == str(v)]
        return [dict(r) for r in rows]

    def update_order_package(self, pkg_id, updates):
        self.package_writes.append({"pkg": pkg_id, **updates})

    def update_trade(self, trade_id, updates):
        self.trade_writes.append((trade_id, updates))


def _leg(tid, account, pkg="P", status="open", sl=1.0):
    return {"id": tid, "account_id": account, "order_package_id": pkg,
            "status": status, "stop_loss": sl, "position_size": 1.0,
            "symbol": "XRPUSDT", "direction": "short",
            "strategy_name": "xrp_pullback_2h", "is_backtest": 0}


def _pkg(linked=1, status="open"):
    return {"order_package_id": "P", "linked_trade_id": linked,
            "status": status, "sl": 1.0, "tp": 0.9,
            "strategy_name": "xrp_pullback_2h", "symbol": "XRPUSDT"}


# --------------------------------------------------------------- resolver


def test_resolver_returns_every_open_leg_not_just_the_linked_one():
    db = FakeDB([_leg(4163, "bybit_2"), _leg(4164, "bybit_portfolio")])
    legs, state = om._package_open_legs(db, _pkg(linked=4163))
    assert state == "resolved"
    assert [x["id"] for x in legs] == [4163, 4164]


def test_resolver_puts_the_linked_leg_first():
    db = FakeDB([_leg(4164, "bybit_portfolio"), _leg(4163, "bybit_2")])
    legs, _ = om._package_open_legs(db, _pkg(linked=4163))
    assert legs[0]["id"] == 4163


def test_resolver_excludes_closed_and_backtest_rows():
    rows = [_leg(1, "a"), _leg(2, "b", status="closed")]
    rows.append({**_leg(3, "c"), "is_backtest": 1})
    legs, _ = om._package_open_legs(FakeDB(rows), _pkg(linked=1))
    assert [x["id"] for x in legs] == [1]


def test_resolver_read_failure_is_not_an_empty_leg_set():
    """`we could not look` must never present as `there are no legs`."""
    db = FakeDB([_leg(1, "a")])
    db.raise_on_get = True
    legs, state = om._package_open_legs(db, _pkg())
    assert state == "read_failed"
    assert legs == []


def test_resolver_ignores_a_linked_id_whose_row_is_closed():
    """A closed linked leg must not be handed back as the leg to act on."""
    db = FakeDB([_leg(9, "a", pkg=None, status="closed")])
    legs, state = om._package_open_legs(
        db, {"order_package_id": None, "linked_trade_id": 9,
             "strategy_name": "s", "symbol": "X"})
    assert state == "resolved"
    assert legs == []


# ------------------------------------------------------- close: draining


def test_close_leaves_the_package_open_while_a_sibling_leg_remains(monkeypatch):
    """The whole repair: flipping the parent strands every surviving leg."""
    db = FakeDB([_leg(4163, "bybit_2"), _leg(4164, "bybit_portfolio")])
    monkeypatch.setattr(om, "_send_close_to_exchange",
                        lambda t: {"ok": True, "exchange_order_id": None})
    monkeypatch.setattr(om, "_capture_fill_details", lambda t, o: None)
    monkeypatch.setattr(om, "mark_active_close", lambda a, s: None)

    def _closes_first_leg(*a, **k):
        db.trades = [t for t in db.trades if t["id"] != 4163]
        return None
    monkeypatch.setattr(db, "update_trade", _closes_first_leg)

    summary = om._StrategyTickSummary()
    om._apply_update(db, _pkg(linked=4163),
                     {"action": "close", "reason": "exit_head"}, summary)

    assert db.package_writes == [], (
        "the package was flipped to closed while leg 4164 is still open — "
        "that is the strand")
    assert summary.closed_count == 1


def test_close_flips_the_package_once_the_last_leg_is_gone(monkeypatch):
    db = FakeDB([_leg(4163, "bybit_2")])
    monkeypatch.setattr(om, "_send_close_to_exchange",
                        lambda t: {"ok": True, "exchange_order_id": None})
    monkeypatch.setattr(om, "_capture_fill_details", lambda t, o: None)
    monkeypatch.setattr(om, "mark_active_close", lambda a, s: None)

    def _closes(*a, **k):
        db.trades = []
        return None
    monkeypatch.setattr(db, "update_trade", _closes)

    summary = om._StrategyTickSummary()
    om._apply_update(db, _pkg(linked=4163),
                     {"action": "close", "reason": "exit_head"}, summary)

    assert [w["status"] for w in db.package_writes] == ["closed"]
    assert db.package_writes[0]["close_reason"] == "exit_head"


def test_close_refuses_on_an_unconfirmed_leg_read(monkeypatch):
    db = FakeDB([_leg(1, "a")])
    db.raise_on_get = True
    sent = []
    monkeypatch.setattr(om, "_send_close_to_exchange",
                        lambda t: sent.append(t) or {"ok": True})
    summary = om._StrategyTickSummary()
    om._apply_update(db, _pkg(), {"action": "close", "reason": "x"}, summary)
    assert sent == [], "closed a position on a read we could not confirm"
    assert summary.error_count == 1


# ------------------------------------------------------- modify: fan-out


def test_modify_reaches_every_leg(monkeypatch):
    db = FakeDB([_leg(4163, "bybit_2"), _leg(4164, "bybit_portfolio")])
    seen = []

    def _modify(trade, **kw):
        seen.append(trade["account_id"])
        return {"ok": True}
    monkeypatch.setattr(om, "_send_modify_to_exchange", _modify)
    monkeypatch.setattr(om, "_note_unsupported_management_op",
                        lambda **kw: None)

    summary = om._StrategyTickSummary()
    om._apply_update(db, _pkg(linked=4163), {"sl": 0.95}, summary)

    assert seen == ["bybit_2", "bybit_portfolio"]
    assert sorted(t for t, _ in db.trade_writes) == [4163, 4164]
    assert [w.get("sl") for w in db.package_writes] == [0.95]


def test_modify_leaves_the_package_sl_unchanged_when_a_leg_fails(monkeypatch):
    """Writing it would make the identical verdict a no-op next tick, so the
    missed leg would keep its entry-time bracket forever."""
    db = FakeDB([_leg(4163, "bybit_2"), _leg(4164, "bybit_portfolio")])

    def _modify(trade, **kw):
        return {"ok": trade["account_id"] == "bybit_2", "error": "boom"}
    monkeypatch.setattr(om, "_send_modify_to_exchange", _modify)
    monkeypatch.setattr(om, "_note_unsupported_management_op",
                        lambda **kw: None)

    summary = om._StrategyTickSummary()
    om._apply_update(db, _pkg(linked=4163), {"sl": 0.95}, summary)

    assert db.package_writes == []
    assert summary.error_count == 1
    # the leg that DID land still has its stored level moved...
    assert [t for t, _ in db.trade_writes] == [4163]
    # ...and the one that did not keeps showing its real venue level
    assert 4164 not in [t for t, _ in db.trade_writes]


def test_modify_refuses_on_an_unconfirmed_leg_read(monkeypatch):
    db = FakeDB([_leg(1, "a")])
    db.raise_on_get = True
    sent = []
    monkeypatch.setattr(om, "_send_modify_to_exchange",
                        lambda t, **k: sent.append(t) or {"ok": True})
    summary = om._StrategyTickSummary()
    om._apply_update(db, _pkg(), {"sl": 0.95}, summary)
    assert sent == []
    assert summary.error_count == 1
