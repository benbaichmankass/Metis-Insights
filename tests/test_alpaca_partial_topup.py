"""PI-20260929-PR6YRTQY-0003: a detected Alpaca quantity shortfall is TOPPED UP.

Before: `_check_broker_naked_equity_positions` graded `partially_naked`,
alerted, and re-armed nothing, because a sibling row's OCO made the symbol read
`stop: True`. MEASURED 2026-09-29 via /api/diag/alpaca_order_history: after the
entry brackets of 6023 (alpaca_paper QQQ 22), 6024 (alpaca_portfolio QQQ 56)
and 6131 (alpaca_paper SPY 8) lapsed at the 20:00Z close, each symbol carried
only the sibling's OCO:

* alpaca_paper QQQ: 32 sh; OCO 2f3d2304 (limit 787.33) + held stop 7e901ac0
  (736.35) for 10 sh — row 5927;
* alpaca_portfolio QQQ: 58 sh; OCO ffd50a25 + held stop d1078e14 (736.35) for
  2 sh — row 5928;
* alpaca_paper SPY: 19 sh; OCO 6682fcfd + held stop 8b4956ce (744.48) for 11.

Order ids, classes, types, sides, qtys, prices and statuses below are those
captured values; journal sl/tp are the rows' own.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime import order_monitor as om
from src.units.accounts.alpaca_client import AlpacaClient

NOW = datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc)


def _oco(parent_id, stop_id, qty, limit, stop, sym):
    return {"id": parent_id, "symbol": sym, "order_class": "oco", "type": "limit",
            "side": "sell", "qty": str(qty), "limit_price": str(limit),
            "stop_price": None, "time_in_force": "gtc", "status": "new",
            "legs": [{"id": stop_id, "symbol": sym, "order_class": "oco",
                      "type": "stop", "side": "sell", "qty": str(qty),
                      "stop_price": str(stop), "limit_price": None,
                      "time_in_force": "gtc", "status": "held", "legs": None}]}


PAPER_QQQ = [_oco("2f3d2304-b9e8-45d7-b117-52e96634159c",
                  "7e901ac0-397a-4145-ad09-2338f29d6498", 10, 787.33, 736.35, "QQQ")]
PORT_QQQ = [_oco("ffd50a25-0fd7-4f49-9f64-4a30640a2f5f",
                 "d1078e14-3bed-4a3f-a08b-6f831175af28", 2, 787.33, 736.35, "QQQ")]
PAPER_SPY = [_oco("6682fcfd-ed19-41ab-984e-674189d2f8d1",
                  "8b4956ce-1782-47cc-a6f0-379bddbe4aba", 11, 830.43, 744.48, "SPY")]


def _row(id_, sym, qty, sl, tp, created="2026-09-21T14:25:28+00:00"):
    return {"id": id_, "account_id": "alpaca_paper", "symbol": sym,
            "direction": "long", "position_size": float(qty), "stop_loss": sl,
            "take_profit_1": tp, "created_at": created, "notes": None}


class _Venue(AlpacaClient):
    def __init__(self, open_rows):
        self.api_key, self.api_secret = "k", "s"
        self.open_rows = open_rows
        self.calls: list = []

    def _request(self, method, path, json_body=None):  # type: ignore[override]
        self.calls.append((method, path, json_body))
        if method == "GET" and "status=open" in path:
            return {"retCode": 0, "result": self.open_rows}
        if method == "GET" and "status=closed" in path:
            return {"retCode": 0, "result": []}
        if method == "POST":
            return {"retCode": 0, "result": {"id": "topup-oco"}}
        return {"retCode": 0, "result": {}}

    def posts(self):
        return [b for m, _p, b in self.calls if m == "POST"]

    def deletes(self):
        return [p for m, p, _ in self.calls if m == "DELETE"]


def _run(venue, sym, size, rows):
    cov = venue.protection_coverage(sym, position={"qty": str(size), "side": "long"})
    return cov, om._alpaca_top_up_uncovered(None, venue, "alpaca_paper", sym, cov, rows, NOW)


def test_6023_paper_qqq_topped_up_for_22_uncovered_shares():
    v = _Venue(PAPER_QQQ)
    rows = [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            _row(6023, "QQQ", 22, 716.79928571, 787.86214286)]
    cov, out = _run(v, "QQQ", 32, rows)
    assert cov["stop_qty"] == 10 and cov["stop_leg_qtys"] == [10.0]
    assert out == "topped_up"
    (body,) = v.posts()
    assert body["qty"] == "22" and body["order_class"] == "oco" and body["side"] == "sell"
    assert body["stop_loss"]["stop_price"] == "716.80"
    assert body["take_profit"]["limit_price"] == "787.86"
    assert v.deletes() == []                     # the sibling OCO is untouched


def test_6024_portfolio_qqq_topped_up_for_56():
    v = _Venue(PORT_QQQ)
    rows = [_row(5928, "QQQ", 2, 736.34571429, 787.33459, "2026-09-18T16:09:39+00:00"),
            _row(6024, "QQQ", 56, 716.79928571, 787.86214286)]
    _cov, out = _run(v, "QQQ", 58, rows)
    assert out == "topped_up"
    assert v.posts()[0]["qty"] == "56" and v.deletes() == []


def test_6131_paper_spy_topped_up_for_8():
    v = _Venue(PAPER_SPY)
    rows = [_row(5555, "SPY", 11, 744.48, 830.43, "2026-08-04T08:00:00+00:00"),
            _row(6131, "SPY", 8, 763.69357143, 840.652575, "2026-09-24T13:37:54+00:00")]
    cov = v.protection_coverage("SPY", position={"qty": "19", "side": "long"})
    out = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "SPY", cov, rows,
                                      datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc))
    assert out == "topped_up"
    assert v.posts()[0]["qty"] == "8"
    assert v.posts()[0]["stop_loss"]["stop_price"] == "763.69"
    assert v.deletes() == []


def test_same_size_sibling_refuses():
    v = _Venue(PAPER_QQQ)
    rows = [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            _row(6023, "QQQ", 10, 716.8, 787.86), _row(6030, "QQQ", 10, 716.8, 787.86)]
    _cov, out = _run(v, "QQQ", 30, rows)
    assert out.startswith("refused_") and v.posts() == [] and v.deletes() == []


def test_ambiguous_attribution_refuses():
    v = _Venue(PAPER_QQQ)
    rows = [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            _row(6023, "QQQ", 22, 716.8, 787.86), _row(6031, "QQQ", 5, 716.8, 787.86)]
    _cov, out = _run(v, "QQQ", 37, rows)
    assert out == "refused_ambiguous_attribution" and v.posts() == []


def test_shortfall_not_matching_the_unmatched_row_refuses():
    """Venue holds more than the journal explains (e.g. an external add)."""
    v = _Venue(PAPER_QQQ)
    rows = [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            _row(6023, "QQQ", 22, 716.8, 787.86)]
    _cov, out = _run(v, "QQQ", 40, rows)
    assert out == "refused_qty_mismatch" and v.posts() == []


def test_fresh_row_is_left_to_settle():
    v = _Venue(PAPER_QQQ)
    rows = [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            _row(6023, "QQQ", 22, 716.8, 787.86, "2026-09-22T01:58:00+00:00")]
    _cov, out = _run(v, "QQQ", 32, rows)
    assert out == "refused_within_grace" and v.posts() == []


def test_fully_stop_naked_is_not_this_path():
    v = _Venue([])
    rows = [_row(6023, "QQQ", 22, 716.8, 787.86)]
    _cov, out = _run(v, "QQQ", 22, rows)
    assert out == "refused_not_partial" and v.posts() == []


# ------------------------------------------------------ the sweep, end to end
def test_sweep_tops_up_6023_and_leaves_the_sibling_oco(tmp_path, monkeypatch):
    """Wiring: the real sweep, the real AlpacaClient grading, the captured
    alpaca_paper QQQ book (32 sh, 5927's 10-sh OCO resting)."""
    import sqlite3
    path = tmp_path / "j.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
        " direction TEXT, position_size REAL, stop_loss REAL, take_profit_1 REAL,"
        " created_at TEXT, notes TEXT, status TEXT, is_backtest INTEGER DEFAULT 0);")
    for r in ((5927, 10, 736.34571429, 787.33459, "2026-09-18T16:09:37+00:00"),
              (6023, 22, 716.79928571, 787.86214286, "2026-09-21T14:25:28+00:00")):
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,NULL,'open',0)",
                     (r[0], "alpaca_paper", "QQQ", "long", r[1], r[2], r[3], r[4]))
    conn.commit()
    conn.close()

    class _Db:
        def connect(self):
            return sqlite3.connect(path)

    class _SweepVenue(_Venue):
        def positions(self):
            return [{"symbol": "QQQ", "qty": "32", "side": "long"}]

    v = _SweepVenue(PAPER_QQQ)
    monkeypatch.setattr("src.bot.data_loaders.list_accounts",
                        lambda: [{"account_id": "alpaca_paper", "exchange": "alpaca"}])
    monkeypatch.setattr("src.units.accounts.clients.alpaca_client_for", lambda acc: v)
    monkeypatch.setattr(om, "_emit_partial_stop_coverage_alert", lambda **kw: None)
    monkeypatch.setattr(om, "_emit_target_naked_alert", lambda **kw: None)
    monkeypatch.setattr(om, "is_active_close", lambda *a: False)

    summary = om._check_broker_naked_equity_positions(_Db())
    assert summary["partially_naked"] == 1
    assert summary["topped_up"] == 1 and summary["topup_refused"] == 0
    assert summary["rearmed"] == 0                 # stop side read armed: no naked re-arm
    (body,) = v.posts()
    assert body["qty"] == "22" and body["stop_loss"]["stop_price"] == "716.80"
    assert v.deletes() == []
