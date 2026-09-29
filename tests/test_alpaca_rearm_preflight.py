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

    bid = None
    ask = None

    def latest_quote(self, symbol):
        if self.bid is None and self.ask is None:
            return None
        return {"bid": self.bid, "ask": self.ask}

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

    def update_trade(self, tid, upd):
        c = sqlite3.connect(self.path)
        c.execute("UPDATE trades SET status=?, notes=? WHERE id=?",
                  (upd.get("status"), upd.get("exit_reason"), tid))
        c.commit()
        c.close()

    def status(self, tid):
        c = sqlite3.connect(self.path)
        r = c.execute("SELECT status, notes FROM trades WHERE id=?", (tid,)).fetchone()
        c.close()
        return r


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
    monkeypatch.setattr(om, "_capture_fill_details", lambda *a, **k: None)
    monkeypatch.setattr(om, "_cascade_close_linked_package", lambda *a, **k: True)
    om._PROTECTION_UNREADABLE_STREAK.clear()
    om._ALPACA_TOPUP_ATTEMPTS.clear()
    return _Db(path), pages, monkeypatch


def _closer(mp, venue):
    """Route _send_close_to_exchange to the venue: a trade-scoped sell of the
    row's own qty (AlpacaClient.close with qty), recorded."""
    closes = []

    def _close(m):
        closes.append(m)
        venue.qty -= float(m["position_size"])
        return {"ok": True, "exchange_order_id": None}
    mp.setattr(om, "_send_close_to_exchange", _close)
    return closes


def _kinds(pages):
    return [k.get("kind") for _a, k in pages]


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
    mp.setattr(om, "_alpaca_rearm_preflight", lambda *a, **k: ("ok", {}))
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


def _close_row(db, tid):
    c = sqlite3.connect(db.path)
    c.execute("UPDATE trades SET status='closed' WHERE id=?", (tid,))
    c.commit()
    c.close()


def test_gap_through_with_row_present_exits_labelled_sl(world):
    """REVIEW-14241 item 2: sole row 6024 present, QQQ gapped to 710 below its
    stop 716.80 — exit via the trade-scoped close, labelled sl; no OCO."""
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=710.0)
    _use(mp, v)
    closes = _closer(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == []
    assert [c["id"] for c in closes] == [6024] and closes[0]["position_size"] == 56.0
    assert s["breach_exits"] == 1
    assert db.status(6024) == ("closed", "sl")


def test_bid_side_of_the_marketable_test(world):
    """REVIEW-14241 item 5: last 720 is above the stop 716.80 but the BID is
    716.50 — a sell stop at 716.80 would trigger on arrival. Treated as
    breached (exit), not re-armed."""
    db, _pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    _use(mp, v)
    closes = _closer(mp, v)
    om._check_broker_naked_equity_positions(db)
    assert v.posts() == [] and [c["id"] for c in closes] == [6024]


def test_attempt_cap_escalates_to_the_close_path(world, tmp_path):
    db, pages, mp = world
    _close_row(db, 5928)
    for _ in range(om._ALPACA_REARM_CAP):
        om._record_rearm_attempt("alpaca_portfolio", 6024)
    om._ALPACA_TOPUP_ATTEMPTS.clear()                 # in-process memo only
    v = _Venue(qty=56.0, price=735.0)
    _use(mp, v)
    closes = _closer(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == []
    assert [c["id"] for c in closes] == [6024]
    assert s["cap_exits"] == 1
    assert db.status(6024) == ("closed", "protection_rearm_exhausted")
    assert (tmp_path / "alpaca_rearm_attempts.json").exists()


def test_cap_budget_resets_on_a_confirmed_resting_stop_and_prunes_closed_rows(world):
    db, _pages, mp = world
    _close_row(db, 5928)
    om._record_rearm_attempt("alpaca_portfolio", 6024)
    om._record_rearm_attempt("alpaca_portfolio", 5928)   # closed row: pruned
    v = _Venue(qty=56.0, price=735.0)
    v.resting = [{"id": "oco-x", "symbol": "QQQ", "order_class": "oco",
                  "type": "limit", "side": "sell", "qty": "56",
                  "limit_price": "787.86", "status": "new",
                  "legs": [{"id": "stop-x", "symbol": "QQQ", "order_class": "oco",
                            "type": "stop", "side": "sell", "qty": "56",
                            "stop_price": "716.80", "status": "held", "legs": None}]}]
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    assert om._rearm_attempts("alpaca_portfolio", 6024) == 0
    assert om._rearm_attempts("alpaca_portfolio", 5928) == 0
    state, _ = om._load_alert_state("alpaca_rearm_attempts")
    assert "alpaca_portfolio|5928" not in (state or {})


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



def test_replay_with_price_above_5928s_stop(world):
    """REVIEW-14241 item 7: the same 09-24 book with QQQ at 737 (ABOVE 5928's
    736.35). 5928's shares are gone (deficit = its unique 2 sh) -> refused
    even though its stop would now rest; 6024 is re-armed once at 716.80."""
    db, pages, mp = world
    v = _Venue(qty=56.0, price=737.0)
    _use(mp, v)
    for _ in range(10):
        om._check_broker_naked_equity_positions(db)
    posts = v.posts()
    assert v.qty == 56.0 and len(posts) == 1
    assert posts[0]["qty"] == "56" and posts[0]["stop_loss"]["stop_price"] == "716.80"
    assert "row_shares_gone" in _kinds(pages)


def _set_rows(db, rows):
    c = sqlite3.connect(db.path)
    c.execute("DELETE FROM trades")
    c.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,NULL,'open',0)", rows)
    c.commit()
    c.close()


def test_ambiguous_netting_protects_what_the_venue_holds(world):
    """REVIEW-14241 item 3: rows 10 + 10 + 20 (=40) against a venue of 30 —
    no single row explains the 10-share deficit. One additive OCO for the 30
    held, at the tightest non-marketable stop (the highest, 725), nothing
    cancelled, paged."""
    db, pages, mp = world
    _set_rows(db, [
        (1, "alpaca_portfolio", "QQQ", "long", 10.0, 700.0, 790.0, "2026-09-18T16:00:00+00:00"),
        (2, "alpaca_portfolio", "QQQ", "long", 10.0, 725.0, 780.0, "2026-09-19T16:00:00+00:00"),
        (3, "alpaca_portfolio", "QQQ", "long", 20.0, 710.0, 785.0, "2026-09-20T16:00:00+00:00"),
    ])
    v = _Venue(qty=30.0, price=735.0)
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    posts = v.posts()
    assert len(posts) == 1
    assert posts[0]["qty"] == "30" and posts[0]["stop_loss"]["stop_price"] == "725.00"
    assert posts[0]["take_profit"]["limit_price"] == "780.00"
    assert not any(m == "DELETE" for m, *_ in v.calls)
    assert s["venue_holding_protected"] == 1
    assert "venue_holding_protected" in _kinds(pages)


def test_ambiguous_netting_skips_breached_candidates(world):
    """The tightest stop (740) is already above the price 735 -> not a
    candidate; the next (725) is used."""
    db, _pages, mp = world
    _set_rows(db, [
        (1, "alpaca_portfolio", "QQQ", "long", 10.0, 740.0, 790.0, "2026-09-18T16:00:00+00:00"),
        (2, "alpaca_portfolio", "QQQ", "long", 10.0, 725.0, 780.0, "2026-09-19T16:00:00+00:00"),
        (3, "alpaca_portfolio", "QQQ", "long", 20.0, 710.0, 785.0, "2026-09-20T16:00:00+00:00"),
    ])
    v = _Venue(qty=30.0, price=735.0)
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    assert [p["stop_loss"]["stop_price"] for p in v.posts()] == ["725.00"]


def test_mixed_direction_rows_are_treated_as_ambiguous(world):
    """A long 40 and a short 10 on one symbol net to a 30 long at the venue:
    no row maps to it -> protect the 30 held at the long rows' tightest stop."""
    db, pages, mp = world
    _set_rows(db, [
        (1, "alpaca_portfolio", "QQQ", "long", 40.0, 720.0, 790.0, "2026-09-18T16:00:00+00:00"),
        (2, "alpaca_portfolio", "QQQ", "short", 10.0, 760.0, 700.0, "2026-09-19T16:00:00+00:00"),
    ])
    v = _Venue(qty=30.0, price=735.0)
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    posts = v.posts()
    assert len(posts) == 1 and posts[0]["qty"] == "30"
    assert posts[0]["side"] == "sell" and posts[0]["stop_loss"]["stop_price"] == "720.00"


class _ShortVenue(_Venue):
    """A short book: position side 'short', OCOs buy back, a buy stop that is
    at/below the price fills on arrival."""

    def _request(self, method, path, json_body=None):  # type: ignore[override]
        if method == "GET" and path.startswith("/v2/positions/"):
            self.calls.append((method, path, json_body))
            if self.qty <= 0:
                return {"retCode": 404, "retMsg": "position does not exist"}
            return {"retCode": 0, "result": {"symbol": "TLT", "qty": str(self.qty),
                                             "side": "short",
                                             "current_price": str(self.price)}}
        if method == "POST":
            self.calls.append((method, path, json_body))
            if float(json_body["stop_loss"]["stop_price"]) <= self.price:
                self.qty -= float(json_body["qty"])
            return {"retCode": 0, "result": {"id": "oco-short"}}
        return super()._request(method, path, json_body)

    def positions(self):
        return ([] if self.qty <= 0 else
                [{"symbol": "TLT", "qty": str(self.qty), "side": "short"}])


def test_short_side_rearm_and_breach(world):
    db, _pages, mp = world
    _set_rows(db, [
        (5912, "alpaca_portfolio", "TLT", "short", 738.0, 81.58, 73.38, "2026-09-18T13:31:28+00:00"),
    ])
    v = _ShortVenue(qty=738.0, price=80.00)          # stop 81.58 above price: safe
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    (p,) = v.posts()
    assert p["side"] == "buy" and p["qty"] == "738" and p["stop_loss"]["stop_price"] == "81.58"

    om._ALPACA_TOPUP_ATTEMPTS.clear()
    v2 = _ShortVenue(qty=738.0, price=81.60)         # already through the stop
    v2.ask = 81.61
    _use(mp, v2)
    om._reset_rearm_attempts("alpaca_portfolio", 5912)
    closes = _closer(mp, v2)
    s = om._check_broker_naked_equity_positions(db)
    assert v2.posts() == [] and [c["id"] for c in closes] == [5912]
    assert s["breach_exits"] == 1


def test_preflight_could_not_look_pages_on_the_streak(world):
    """REVIEW-14241 item 1: an unreadable pre-flight read is streaked on its
    own channel and paged from the 3rd consecutive sweep."""
    db, pages, mp = world
    _close_row(db, 5928)

    class _Down(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "GET" and path.startswith("/v2/positions/"):
                self.calls.append((method, path, json_body))
                return {"retCode": 503, "retMsg": "down"}
            return super()._request(method, path, json_body)

    v = _Down(qty=56.0, price=735.0)
    _use(mp, v)
    for _ in range(2):
        om._check_broker_naked_equity_positions(db)
    assert not any(a[0] == "alpaca_protection_unreadable" for a, _k in pages)
    om._check_broker_naked_equity_positions(db)
    assert any(a[0] == "alpaca_protection_unreadable" and k.get("channel") == "preflight"
               for a, k in pages)
    assert v.posts() == []


def test_summary_declares_every_outcome_key_at_zero(world):
    db, _pages, mp = world
    _set_rows(db, [])
    v = _Venue(qty=0.0, price=735.0)
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    for k in ("rearm_refused_could_not_look", "rearm_refused_flat",
              "rearm_refused_side_mismatch", "rearm_refused_row_shares_gone",
              "rearm_refused_all_stops_breached", "breach_exits", "cap_exits",
              "venue_holding_protected", "exit_failed", "protection_read_failed",
              "topped_up", "topup_refused"):
        assert s[k] == 0, k
