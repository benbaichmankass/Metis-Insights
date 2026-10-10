"""DASH-PROP-OVERVIEW: aggregate prop book for the SPA Overview."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import src.prop.prop_journal as pj
import src.prop.prop_reconcile as pr
from src.prop import prop_overview as po

NOW = datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)
ACCTS = {
    "velotrade_1": {"account_class": "prop", "mode": "live"},
    "tradeify_1": {"account_class": "prop", "mode": "live"},
    "breakout_2": {"account_class": "prop", "mode": "live"},
    "bybit_2": {"account_class": "real_money"},
}
EQ = {"velotrade_1": (4993.08, "ok"), "tradeify_1": (9942.54, "ok"),
      "breakout_2": (5000.0, "stale")}


def _patch(monkeypatch, fills):
    monkeypatch.setattr(pj, "latest_account_status",
                        lambda a: {"equity": EQ[a][0]})
    monkeypatch.setattr(pr, "compute_rule_distance",
                        lambda a, s=None: {"status_freshness": EQ[a][1],
                                           "status_age_hours": 1.0})
    monkeypatch.setattr(pj, "list_fills",
                        lambda account_id=None, limit=100: [f for f in fills if f["account_id"] == account_id])


def _fill(i, acct, status, pnl=None, closed_days_ago=None, tkt=None):
    return {"id": i, "account_id": acct, "status": status, "pnl": pnl, "ticket_id": tkt or f"t{i}",
            "external_order_id": None, "symbol": "ETHUSDT", "direction": "long", "qty": 1.0,
            "entry_price": 100.0, "sl": 99.0, "tp": 110.0, "opened_at": None,
            "closed_at": (NOW - timedelta(days=closed_days_ago)).isoformat() if closed_days_ago is not None else None,
            "reported_at": NOW.isoformat()}


def test_equity_sums_fresh_only_and_lists_stale(monkeypatch):
    _patch(monkeypatch, [])
    o = po.build_overview("7d", accounts=ACCTS, now=NOW)
    assert o["equity"]["total_usd"] == round(4993.08 + 9942.54, 2)
    assert o["equity"]["accounts_counted"] == 2 and o["equity"]["accounts_total"] == 3
    stale = [a for a in o["equity"]["accounts"] if a["account_id"] == "breakout_2"][0]
    assert stale["counted_in_total"] is False and stale["equity"] == 5000.0


def test_no_fresh_equity_is_null_not_zero(monkeypatch):
    _patch(monkeypatch, [])
    monkeypatch.setattr(pr, "compute_rule_distance",
                        lambda a, s=None: {"status_freshness": "stale"})
    assert po.build_overview("7d", accounts=ACCTS, now=NOW)["equity"]["total_usd"] is None


def test_open_trade_listed_and_closed_ticket_not_open(monkeypatch):
    fills = [_fill(96, "velotrade_1", "open", tkt="A"),
             _fill(1, "velotrade_1", "open", tkt="B"),
             _fill(2, "velotrade_1", "closed", pnl=10.0, closed_days_ago=1, tkt="B"),
             _fill(3, "velotrade_1", "placed", tkt="C")]
    _patch(monkeypatch, fills)
    o = po.build_overview("7d", accounts=ACCTS, now=NOW)
    assert [t["id"] for t in o["open_trades"]] == [96]


def test_realized_window_population_and_ratios(monkeypatch):
    fills = [_fill(1, "velotrade_1", "closed", pnl=30.0, closed_days_ago=1),
             _fill(2, "tradeify_1", "closed", pnl=-10.0, closed_days_ago=2),
             _fill(3, "tradeify_1", "closed", pnl=None, closed_days_ago=2),
             _fill(4, "tradeify_1", "closed", pnl=500.0, closed_days_ago=20)]
    _patch(monkeypatch, fills)
    r = po.build_overview("7d", accounts=ACCTS, now=NOW)["realized"]
    assert (r["total_pnl"], r["closed_trades"], r["closed_priced"], r["closed_unpriced"]) == (20.0, 3, 2, 1)
    assert r["win_rate"] == 0.5 and r["profit_factor"] == 3.0
    assert po.build_overview("all", accounts=ACCTS, now=NOW)["realized"]["total_pnl"] == 520.0


def test_empty_window_is_null_not_zero(monkeypatch):
    _patch(monkeypatch, [])
    r = po.build_overview("7d", accounts=ACCTS, now=NOW)["realized"]
    assert r["total_pnl"] is None and r["win_rate"] is None and r["closed_trades"] == 0


def test_route_degrades(monkeypatch):
    from src.web.api.routers import prop as router
    monkeypatch.setattr(po, "build_overview", lambda w: (_ for _ in ()).throw(RuntimeError("x")))
    assert router.get_overview("7d")["present"] is False


def test_retired_account_excluded(monkeypatch):
    # breakout_1 is retired (operator 2026-10-09): its stale equity and its
    # never-closed historical fills must not appear in the live prop book.
    accts = dict(ACCTS, breakout_1={"account_class": "prop", "mode": "dry_run", "retired": True})
    EQ["breakout_1"] = (4698.0, "stale")
    try:
        _patch(monkeypatch, [_fill(1, "breakout_1", "open"), _fill(2, "velotrade_1", "open")])
        o = po.build_overview("7d", accounts=accts, now=NOW)
        assert [a["account_id"] for a in o["equity"]["accounts"]].count("breakout_1") == 0
        assert [t["account_id"] for t in o["open_trades"]] == ["velotrade_1"]
    finally:
        EQ.pop("breakout_1", None)
