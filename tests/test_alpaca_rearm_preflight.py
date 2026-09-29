"""PI-20260929-PR6YRTQY-0005: the Alpaca naked re-arm pre-flight.

REPLAY of alpaca_portfolio QQQ, 2026-09-24, from /api/diag/alpaca_order_history
(captured 2026-09-29). Rows: 5928 (2 sh, sl 736.35) and 6024 (56 sh, sl 716.80).

* 6024's entry bracket (67498187…): its legs lapsed at the 09-21 close —
  take-profit 1989e18b… `expired`, stop 1445d7ee… `canceled` 20:01:43Z. From
  then on 6024 had NO resting protection of its own.
* 5928's re-armed OCO ffd50a25… (stop leg d1078e14… @736.35, 2 sh) FILLED at
  13:32:10Z. Venue position: 58 -> 56. Journal: both rows still `open`.
* What happened next on main: the sweep re-armed 5928 (a 2-sh OCO @736.35 with
  QQQ ~735) 28 more times; each filled on arrival — 56 shares, all 6024's, sold
  above 6024's own stop.

With the pre-flight: 5928 is refused (`row_shares_gone` — the 2-share deficit
is exactly its size), 6024 is re-armed ONCE at its own 716.80 stop for its 56
shares, and no sweep ever sells 6024's shares.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.runtime import order_monitor as om
from src.units.accounts.alpaca_client import AlpacaClient

_BRACKET_6024 = {
    "id": "67498187-5a00-4c5b-bd8b-8d1d234fc8b0", "symbol": "QQQ",
    "order_class": "bracket", "type": "market", "side": "buy", "qty": "56",
    "filled_qty": "56", "status": "filled", "time_in_force": "day",
    "submitted_at": "2026-09-21T14:25:29.000000Z",
    "legs": [
        {"id": "1989e18b-8b97-4bf9-b641-5fcd56100d4f", "symbol": "QQQ",
         "order_class": "bracket", "type": "limit", "side": "sell", "qty": "56",
         "limit_price": "787.86", "status": "expired", "legs": None},
        {"id": "1445d7ee-5b53-47d9-873c-dbb23a6ade54", "symbol": "QQQ",
         "order_class": "bracket", "type": "stop", "side": "sell", "qty": "56",
         "stop_price": "716.8", "status": "canceled",
         "canceled_at": "2026-09-21T20:01:43.593Z", "legs": None},
    ],
}
_OCO_5928_FILLED = {
    "id": "ffd50a25-0fd7-4f49-9f64-4a30640a2f5f", "symbol": "QQQ",
    "order_class": "oco", "type": "limit", "side": "sell", "qty": "2",
    "limit_price": "787.33", "status": "canceled",
    "submitted_at": "2026-09-18T16:16:04.000000Z",
    "legs": [{"id": "d1078e14-3bed-4a3f-a08b-6f831175af28", "symbol": "QQQ",
              "order_class": "oco", "type": "stop", "side": "sell", "qty": "2",
              "stop_price": "736.35", "status": "filled",
              "filled_avg_price": "735.31", "legs": None}],
}


class _Venue(AlpacaClient):
    """alpaca_portfolio at 2026-09-24 13:32:11Z. A POSTed OCO whose stop is
    at/above the price fills on arrival (what the venue did 28 times)."""

    def __init__(self, qty=56.0, price=735.0):
        self.api_key, self.api_secret = "k", "s"
        self.qty, self.price = qty, price
        self.resting: list = []
        self.calls: list = []

    def _request(self, method, path, json_body=None):  # type: ignore[override]
        self.calls.append((method, path, json_body))
        if method == "GET" and "status=open" in path:
            return {"retCode": 0, "result": self.resting}
        if method == "GET" and "status=closed" in path:
            return {"retCode": 0, "result": [_OCO_5928_FILLED, _BRACKET_6024]}
        if method == "GET" and path.startswith("/v2/positions/"):
            if self.qty <= 0:
                return {"retCode": 404, "retMsg": "position does not exist"}
            return {"retCode": 0, "result": {"symbol": "QQQ", "qty": str(self.qty),
                                             "side": "long",
                                             "current_price": str(self.price)}}
        if method == "POST":
            b = json_body
            stop = float(b["stop_loss"]["stop_price"])
            q = float(b["qty"])
            if stop >= self.price:            # marketable: fills on arrival
                self.qty -= q
            else:
                n = len(self.calls)
                self.resting.append({
                    "id": f"new-{n}", "symbol": "QQQ", "order_class": "oco",
                    "type": "limit", "side": "sell", "qty": b["qty"],
                    "limit_price": b["take_profit"]["limit_price"], "status": "new",
                    "legs": [{"id": f"newstop-{n}", "symbol": "QQQ",
                              "order_class": "oco", "type": "stop", "side": "sell",
                              "qty": b["qty"], "stop_price": b["stop_loss"]["stop_price"],
                              "status": "held", "legs": None}]})
            return {"retCode": 0, "result": {"id": f"oco-{len(self.calls)}"}}
        if method == "DELETE":
            return {"retCode": 0, "result": {}}
        return {"retCode": 500, "retMsg": "unrouted"}

    def positions(self):
        return ([] if self.qty <= 0 else
                [{"symbol": "QQQ", "qty": str(self.qty), "side": "long"}])

    def posts(self):
        return [b for m, _p, b in self.calls if m == "POST"]


class _Db:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / "j.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, symbol TEXT,"
        " direction TEXT, position_size REAL, stop_loss REAL, take_profit_1 REAL,"
        " created_at TEXT, notes TEXT, status TEXT, is_backtest INTEGER DEFAULT 0);"
        "CREATE TABLE order_packages (order_package_id TEXT, symbol TEXT,"
        " direction TEXT, sl REAL, tp REAL, created_at TEXT);")
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,NULL,'open',0)", [
        (5928, "alpaca_portfolio", "QQQ", "long", 2.0, 736.34571429, 787.33459,
         "2026-09-18T16:09:39+00:00"),
        (6024, "alpaca_portfolio", "QQQ", "long", 56.0, 716.79928571, 787.86214286,
         "2026-09-21T14:25:31+00:00"),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(om, "_alert_state_path", lambda kind: tmp_path / f"{kind}.json")
    monkeypatch.setattr("src.bot.data_loaders.list_accounts",
                        lambda: [{"account_id": "alpaca_portfolio", "exchange": "alpaca"}])
    pages = []
    monkeypatch.setattr("src.runtime.outcomes.report", lambda *a, **k: pages.append((a, k)))
    for name in ("_emit_partial_stop_coverage_alert", "_emit_target_naked_alert"):
        monkeypatch.setattr(om, name, lambda **kw: None)
    monkeypatch.setattr(om, "is_active_close", lambda *a: False)
    om._PROTECTION_UNREADABLE_STREAK.clear()
    om._ALPACA_TOPUP_ATTEMPTS.clear()
    return _Db(path), pages, monkeypatch


def _use(monkeypatch, venue):
    monkeypatch.setattr("src.units.accounts.clients.alpaca_client_for", lambda acc: venue)


def test_replay_2026_09_24_no_rearm_of_5928_and_6024_never_liquidated(world):
    db, pages, mp = world
    v = _Venue(qty=56.0, price=735.0)
    _use(mp, v)
    for _ in range(30):                              # the loop ran ~29 sweeps
        om._check_broker_naked_equity_positions(db)
    posts = v.posts()
    # 5928 is never re-armed: no 2-share OCO, nothing at 736.35.
    assert not any(p["qty"] == "2" for p in posts)
    assert not any(p["stop_loss"]["stop_price"] == "736.35" for p in posts)
    # 6024's 56 shares are never sold, and it is protected ONCE at its own stop.
    assert v.qty == 56.0
    assert len(posts) == 1
    assert posts[0]["qty"] == "56" and posts[0]["stop_loss"]["stop_price"] == "716.80"
    assert any(a[0] == "alpaca_rearm_refused" and k.get("kind") == "row_shares_gone"
               for a, k in pages)


def test_main_behaviour_would_have_looped_without_the_preflight(world):
    """Negative control: bypass the pre-flight and the replay reproduces the
    measured loop — 5928 re-armed and its marketable stop eating 6024's shares."""
    db, _pages, mp = world
    mp.setattr(om, "_alpaca_rearm_preflight", lambda *a, **k: "ok")
    v = _Venue(qty=56.0, price=735.0)
    _use(mp, v)
    for _ in range(5):
        om._check_broker_naked_equity_positions(db)
    assert any(p["qty"] == "2" and p["stop_loss"]["stop_price"] == "736.35" for p in v.posts())
    assert v.qty < 56.0


def test_single_row_whose_stop_filled_is_never_rearmed_on_a_flat_position(world):
    db, pages, mp = world
    import sqlite3 as _s
    c = _s.connect(db.path)
    c.execute("UPDATE trades SET status='closed' WHERE id=6024")
    c.commit()
    c.close()
    v = _Venue(qty=0.0, price=735.0)                  # 5928's stop filled; flat
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == []
    assert s.get("rearm_refused_flat") == 1
    assert any(k.get("kind") == "flat" for _a, k in pages)


def test_marketable_stop_is_refused_for_a_sole_row(world):
    db, pages, mp = world
    import sqlite3 as _s
    c = _s.connect(db.path)
    c.execute("UPDATE trades SET status='closed' WHERE id=5928")
    c.commit()
    c.close()
    v = _Venue(qty=56.0, price=710.0)                 # below 6024's stop 716.80
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == [] and v.qty == 56.0
    assert s.get("rearm_refused_marketable_stop") == 1


def test_attempt_cap_is_durable_and_pages(world, tmp_path):
    db, pages, mp = world
    import sqlite3 as _s
    c = _s.connect(db.path)
    c.execute("UPDATE trades SET status='closed' WHERE id=5928")
    c.commit()
    c.close()
    for _ in range(om._ALPACA_REARM_CAP):
        om._record_rearm_attempt("alpaca_portfolio", 6024)
    # A restart clears in-process memos, not the durable budget.
    om._ALPACA_TOPUP_ATTEMPTS.clear()
    v = _Venue(qty=56.0, price=735.0)
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == []
    assert s.get("rearm_refused_cap") == 1
    assert (tmp_path / "alpaca_rearm_attempts.json").exists()
    assert any(k.get("kind") == "cap" for _a, k in pages)


def test_unreadable_position_is_could_not_look(world):
    db, _pages, mp = world
    import sqlite3 as _s
    c = _s.connect(db.path)
    c.execute("UPDATE trades SET status='closed' WHERE id=5928")
    c.commit()
    c.close()

    class _Down(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "GET" and path.startswith("/v2/positions/"):
                self.calls.append((method, path, json_body))
                return {"retCode": 503, "retMsg": "down"}
            return super()._request(method, path, json_body)

    v = _Down(qty=56.0, price=735.0)
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == []
    assert s.get("rearm_refused_could_not_look") == 1
