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


from src.runtime.market_hours import us_equity_session as _REAL_US_EQUITY_SESSION  # noqa: E402


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
    # Age of the latest trade print at ``price`` (Data v2 trades/latest);
    # None = the read failed. Fresh by default so a price through the stop is
    # a CONFIRMED breach (REVIEW-14241 round 2, blocker 1).
    trade_age = 5.0

    def latest_trade(self, symbol):
        if self.trade_age is None:
            return None
        return {"price": self.price, "age_s": self.trade_age}

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
    # Regular session unless a test says otherwise — the real clock would make
    # these tests depend on when they run.
    monkeypatch.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "rth")
    om._PROTECTION_UNREADABLE_STREAK.clear()
    om._ALPACA_TOPUP_ATTEMPTS.clear()
    om._ALPACA_BREACH_DEFERRALS.clear()
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


def test_bid_side_of_the_marketable_test_defers_never_exits(world):
    """REVIEW-14241 round 2, blocker 1: last 720 is above the stop 716.80 but
    the IEX BID is 716.50 — a sell stop at 716.80 might trigger on arrival.
    The quote may only REFUSE the re-arm: no OCO, and no market exit either
    (a thin IEX book must not sell 56 shares at market). Deferred, not paged
    until it persists."""
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    _use(mp, v)
    closes = _closer(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == [] and closes == []
    assert s["rearm_refused_deferred_unconfirmed_breach"] == 1
    assert s["breach_exits"] == 0
    assert db.status(6024)[0] == "open"
    for _ in range(2):
        om._check_broker_naked_equity_positions(db)
    assert any(a[0] == "alpaca_protection_unreadable" and k.get("channel") == "breach"
               for a, k in pages)


def test_breach_state_reads_the_real_session_vocabulary(world, monkeypatch):
    """The session literal is ``market_hours.us_equity_session``'s own
    (``rth``/``extended``/``closed``). Pinned against the REAL function at a
    known RTH instant, so a spelling drift (this branch once compared against
    "regular" and would have read every RTH tick as closed) fails here."""
    from datetime import datetime, timezone
    from src.runtime import market_hours as mh
    real = _REAL_US_EQUITY_SESSION           # the fixture patched the module attr
    rth_ts = datetime(2026, 9, 29, 16, 41, tzinfo=timezone.utc)   # Tue 12:41 ET
    monkeypatch.setattr(mh, "us_equity_session", lambda *a, **k: real(rth_ts))
    v = _Venue(qty=56.0, price=710.0)
    st, _info = om._breach_state(v, "QQQ", "long", 716.80, 710.0, None, None)
    assert st == "confirmed"
    sat_ts = datetime(2026, 9, 26, 16, 41, tzinfo=timezone.utc)
    monkeypatch.setattr(mh, "us_equity_session", lambda *a, **k: real(sat_ts))
    assert om._breach_state(v, "QQQ", "long", 716.80, 710.0, None, None)[0] == "session_closed"


def test_stale_trade_through_the_stop_never_exits(world):
    """A last trade through the stop but older than _BREACH_TRADE_MAX_AGE_S
    cannot confirm a breach: deferred, no exit, no OCO."""
    db, _pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=710.0)
    v.trade_age = om._BREACH_TRADE_MAX_AGE_S + 60
    _use(mp, v)
    closes = _closer(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == [] and closes == []
    assert s["rearm_refused_deferred_unconfirmed_breach"] == 1


def test_unreadable_trade_through_the_stop_never_exits(world):
    db, _pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=710.0)
    v.trade_age = None
    _use(mp, v)
    closes = _closer(mp, v)
    om._check_broker_naked_equity_positions(db)
    assert v.posts() == [] and closes == []


def test_market_closed_rests_the_stop_instead_of_exiting(world):
    """Outside the regular session no stop triggers and no market exit fills:
    the row's own stop is posted to REST (it fires at the open) — never an
    exit attempt on an extended-hours print."""
    db, _pages, mp = world
    _close_row(db, 5928)
    mp.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "closed")
    v = _Venue(qty=56.0, price=740.0)
    _use(mp, v)
    closes = _closer(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert closes == [] and s["breach_exits"] == 0
    (p,) = v.posts()
    assert p["qty"] == "56" and p["stop_loss"]["stop_price"] == "716.80"


def test_deferred_close_keeps_a_stop_resting(world):
    """REVIEW-14241 round 2, blocker 3: the venue's own 'market closed — exit
    deferred' (AlpacaClient.close retCode 2) is not a refusal: counted as
    exit_deferred, not paged as exit_failed, and a stop is put back to rest."""
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=710.0)
    _use(mp, v)
    mp.setattr(om, "_send_close_to_exchange",
               lambda m: {"ok": False, "error": "market closed — exit deferred"})
    rests = []
    mp.setattr(om, "_attempt_naked_autoprotect",
               lambda row, sl, tp, db=None: rests.append(row["id"]) or True)
    s = om._check_broker_naked_equity_positions(db)
    assert s["exit_deferred"] == 1 and s["exit_failed"] == 0
    assert rests == [6024]
    assert "exit_failed" not in _kinds(pages)
    assert db.status(6024)[0] == "open"


def test_refused_close_is_exit_failed_and_paged(world):
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=710.0)
    _use(mp, v)
    mp.setattr(om, "_send_close_to_exchange",
               lambda m: {"ok": False, "error": "insufficient qty available"})
    s = om._check_broker_naked_equity_positions(db)
    assert s["exit_failed"] == 1 and s["exit_deferred"] == 0
    assert "exit_failed" in _kinds(pages)


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


def test_venue_holding_oco_is_not_reposted_every_tick(world):
    """REVIEW-14241 round 2: a refused additive OCO for the venue holding is
    attempted once per cooldown, not re-POSTed on every sweep."""
    db, _pages, mp = world
    _set_rows(db, [
        (1, "alpaca_portfolio", "QQQ", "long", 10.0, 700.0, 790.0, "2026-09-18T16:00:00+00:00"),
        (2, "alpaca_portfolio", "QQQ", "long", 10.0, 725.0, 780.0, "2026-09-19T16:00:00+00:00"),
        (3, "alpaca_portfolio", "QQQ", "long", 20.0, 710.0, 785.0, "2026-09-20T16:00:00+00:00"),
    ])

    class _Refuse(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "POST":
                self.calls.append((method, path, json_body))
                return {"retCode": 403, "retMsg": "insufficient qty available"}
            return super()._request(method, path, json_body)

    v = _Refuse(qty=30.0, price=735.0)
    _use(mp, v)
    s1 = om._check_broker_naked_equity_positions(db)
    s2 = om._check_broker_naked_equity_positions(db)
    assert len(v.posts()) == 1
    assert s1["exit_failed"] == 1 and s2["venue_holding_cooldown"] == 1


def test_venue_holding_fractional_qty_is_floored(world):
    """A fractional holding of 30.6 protects 30 shares, never rounds up to 31."""
    db, _pages, mp = world
    _set_rows(db, [
        (1, "alpaca_portfolio", "QQQ", "long", 10.0, 700.0, 790.0, "2026-09-18T16:00:00+00:00"),
        (2, "alpaca_portfolio", "QQQ", "long", 10.0, 725.0, 780.0, "2026-09-19T16:00:00+00:00"),
        (3, "alpaca_portfolio", "QQQ", "long", 20.0, 710.0, 785.0, "2026-09-20T16:00:00+00:00"),
    ])
    v = _Venue(qty=30.6, price=735.0)
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    (p,) = v.posts()
    assert p["qty"] == "30"


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
              "topped_up", "topup_refused", "exit_deferred",
              "venue_holding_cooldown", "escalated_post_rejected",
              "rearm_refused_deferred_unconfirmed_breach"):
        assert s[k] == 0, k


def test_latest_trade_parses_the_real_data_v2_payload(monkeypatch):
    """GET /v2/stocks/{sym}/trades/latest — Alpaca Data v2 shape, nanosecond
    timestamp. age_s is measured from the print; a failed read is None."""
    from datetime import datetime, timedelta, timezone

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    t = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f") + "789Z"
    body = {"symbol": "QQQ", "trade": {"t": t, "x": "V", "p": 710.12, "s": 100,
                                       "c": ["@"], "i": 52983525033527, "z": "C"}}
    seen = {}

    def _get(url, params=None, headers=None, timeout=None):
        seen["url"], seen["params"] = url, params
        return _Resp(body)

    monkeypatch.setattr("src.units.accounts.alpaca_client.requests.get", _get)
    c = _Venue()
    c.timeout = 5.0
    out = AlpacaClient.latest_trade(c, "qqq")
    assert seen["url"].endswith("/v2/stocks/QQQ/trades/latest")
    assert out["price"] == 710.12 and 25 <= out["age_s"] <= 60

    def _boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr("src.units.accounts.alpaca_client.requests.get", _boom)
    assert AlpacaClient.latest_trade(c, "QQQ") is None


def test_unconfirmed_breach_deferral_is_bounded_then_the_stop_is_posted(world):
    """REVIEW-14241 round 3, B2: latest_trade keeps failing (None) while the
    IEX bid is through 6024's stop. A deferred row has nothing resting, so
    after _BREACH_DEFER_MAX deferrals the row's own stop is posted anyway
    (an Alpaca stop triggers on trades: against a stale bid it just rests).
    Never a market exit; the page names what it is."""
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    v.trade_age = None
    _use(mp, v)
    closes = _closer(mp, v)
    for _ in range(om._BREACH_DEFER_MAX):
        s = om._check_broker_naked_equity_positions(db)
        assert s["rearm_refused_deferred_unconfirmed_breach"] == 1
    assert v.posts() == []
    breach = [(a, k) for a, k in pages
              if a[0] == "alpaca_protection_unreadable" and k.get("channel") == "breach"]
    assert breach and "UNCONFIRMED" in breach[-1][1]["reason"]
    assert "unreadable" not in breach[-1][1]["reason"]
    s = om._check_broker_naked_equity_positions(db)
    (p,) = v.posts()
    assert p["qty"] == "56" and p["stop_loss"]["stop_price"] == "716.80"
    assert closes == [] and s["rearm_refused_deferred_unconfirmed_breach"] == 0
    assert "breach_deferral_escalated" in _kinds(pages)


def test_escalated_post_still_respects_the_cap(world):
    db, _pages, mp = world
    _close_row(db, 5928)
    for _ in range(om._ALPACA_REARM_CAP):
        om._record_rearm_attempt("alpaca_portfolio", 6024)
    om._ALPACA_BREACH_DEFERRALS[("alpaca_portfolio", "QQQ", 6024)] = om._BREACH_DEFER_MAX
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    v.trade_age = None
    _use(mp, v)
    closes = _closer(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert v.posts() == [] and [c["id"] for c in closes] == [6024]
    assert s["cap_exits"] == 1


def test_a_clear_read_resets_the_deferral_count(world):
    db, _pages, mp = world
    _close_row(db, 5928)
    om._ALPACA_BREACH_DEFERRALS[("alpaca_portfolio", "QQQ", 6024)] = 2
    v = _Venue(qty=56.0, price=735.0)
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    assert ("alpaca_portfolio", "QQQ", 6024) not in om._ALPACA_BREACH_DEFERRALS


def test_extended_hours_deferred_to_the_regular_session_is_a_deferral(world):
    """REVIEW-14241 round 3: AlpacaClient.close's near-16:00 retCode-2 text
    ('... DEFERRED to the regular session ...') is a deferral, not exit_failed."""
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=710.0)
    _use(mp, v)
    mp.setattr(om, "_send_close_to_exchange", lambda m: {"ok": False, "error": (
        "extended-hours: trade-scoped exit of 56 of 56 on QQQ DEFERRED to the "
        "regular session — the extended-hours limit path cannot close part of "
        "a symbol")})
    rests = []
    mp.setattr(om, "_attempt_naked_autoprotect",
               lambda row, sl, tp, db=None: rests.append(row["id"]) or True)
    s = om._check_broker_naked_equity_positions(db)
    assert s["exit_deferred"] == 1 and s["exit_failed"] == 0 and rests == [6024]
    assert "exit_failed" not in _kinds(pages)


# --- K1XNYYAQ-0002: #14241 follow-ups ---------------------------------------
def test_escalating_sweep_pages_escalated_not_nothing_resting(world):
    """(3) On the sweep that POSTS the escalated stop, the page says
    'escalated' (not 're-arm refused') and no breach-channel 'NOTHING is
    resting' page fires on that sweep."""
    db, pages, mp = world
    _close_row(db, 5928)
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    v.trade_age = None
    _use(mp, v)
    for _ in range(om._BREACH_DEFER_MAX):
        om._check_broker_naked_equity_positions(db)
    n_before = sum(1 for a, k in pages if k.get("channel") == "breach")
    om._check_broker_naked_equity_positions(db)
    assert sum(1 for a, k in pages if k.get("channel") == "breach") == n_before
    esc = [k for a, k in pages if k.get("kind") == "breach_deferral_escalated"]
    assert esc and "re-arm escalated" in esc[-1]["reason"]
    assert "refused" not in esc[-1]["reason"]
    assert ("alpaca_portfolio", "QQQ", "breach") not in om._PROTECTION_UNREADABLE_STREAK
    assert len(v.posts()) == 1


def test_rejected_escalated_stop_spends_the_cap(world):
    """(2) An escalated stop the venue REJECTS still counts against the re-arm
    cap, so it is not re-posted every sweep: once the cap is spent the row
    escalates to the close path."""
    db, _pages, mp = world
    _close_row(db, 5928)

    class _Reject(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "POST":
                self.calls.append((method, path, json_body))
                return {"retCode": 422, "retMsg": "stop price must be below market"}
            return super()._request(method, path, json_body)

    v = _Reject(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    v.trade_age = None
    _use(mp, v)
    closes = _closer(mp, v)
    om._ALPACA_BREACH_DEFERRALS[("alpaca_portfolio", "QQQ", 6024)] = om._BREACH_DEFER_MAX
    posts_per_sweep = []
    for _ in range(om._ALPACA_REARM_CAP):
        before = len(v.posts())
        s = om._check_broker_naked_equity_positions(db)
        posts_per_sweep.append(len(v.posts()) - before)
        assert s["escalated_post_rejected"] == 1
    assert posts_per_sweep == [1] * om._ALPACA_REARM_CAP
    assert om._rearm_attempts("alpaca_portfolio", 6024) == om._ALPACA_REARM_CAP
    s = om._check_broker_naked_equity_positions(db)
    assert s["cap_exits"] == 1 and [c["id"] for c in closes] == [6024]


def test_non_escalated_rejected_rearm_does_not_spend_the_cap_positive_control(world):
    """Only the ESCALATED post is charged on rejection; an ordinary re-arm's
    behaviour is unchanged."""
    db, _pages, mp = world
    _close_row(db, 5928)

    class _Reject(_Venue):
        def _request(self, method, path, json_body=None):  # type: ignore[override]
            if method == "POST":
                self.calls.append((method, path, json_body))
                return {"retCode": 422, "retMsg": "refused"}
            return super()._request(method, path, json_body)

    v = _Reject(qty=56.0, price=735.0)
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    assert s["escalated_post_rejected"] == 0
    assert om._rearm_attempts("alpaca_portfolio", 6024) == 0


def test_breach_deferrals_are_pruned_for_closed_rows(world):
    """(5) _ALPACA_BREACH_DEFERRALS drops entries of rows no longer open."""
    db, _pages, mp = world
    _close_row(db, 5928)
    om._ALPACA_BREACH_DEFERRALS[("alpaca_portfolio", "QQQ", 5928)] = 2   # closed row
    om._ALPACA_BREACH_DEFERRALS[("alpaca_portfolio", "QQQ", 999)] = 1    # never existed
    v = _Venue(qty=56.0, price=720.0)
    v.bid, v.ask = 716.50, 720.10
    v.trade_age = None
    _use(mp, v)
    om._check_broker_naked_equity_positions(db)
    assert set(om._ALPACA_BREACH_DEFERRALS) == {("alpaca_portfolio", "QQQ", 6024)}


def test_every_equity_sweep_logs_all_counters_including_zeros(world, caplog):
    """(6) The per-sweep counters line is the ONLY place these are observable
    (src/main.py discards the tick's summaries), so it carries every key, zeros
    included, as JSON."""
    import json as _json
    import logging as _logging
    db, _pages, mp = world
    _set_rows(db, [])
    v = _Venue(qty=0.0, price=735.0)
    _use(mp, v)
    s = om._check_broker_naked_equity_positions(db)
    with caplog.at_level(_logging.INFO, logger="src.runtime.order_monitor"):
        om._log_equity_sweep_summary(s)
    (rec,) = [r for r in caplog.records
              if r.getMessage().startswith("_check_broker_naked_equity_positions: sweep ")]
    logged = _json.loads(rec.getMessage().split(" sweep ", 1)[1])
    assert logged == s
    for k in ("breach_exits", "cap_exits", "exit_deferred", "escalated_post_rejected",
              "rearm_refused_deferred_unconfirmed_breach", "topped_up"):
        assert logged[k] == 0, k


def test_reconciliation_tick_logs_the_equity_sweep_on_every_tick():
    """Static pin: run_reconciliation_tick logs the equity sweep's summary right
    after running it — unconditionally, not only when a counter is non-zero."""
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[1] / "src" / "runtime" / "order_monitor.py").read_text()
    body = src.split("def run_reconciliation_tick", 1)[1].split("\ndef ", 1)[0]
    i = body.index("broker_naked_summary = _check_broker_naked_equity_positions(db)")
    j = body.index("_log_equity_sweep_summary(broker_naked_summary)")
    k = body.index("if any(v for k, v in broker_naked_summary.items()")
    assert i < j < k
