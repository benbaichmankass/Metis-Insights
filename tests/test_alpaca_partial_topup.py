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

import pytest

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


@pytest.fixture(autouse=True)
def _fresh_cooldown():
    om._ALPACA_TOPUP_ATTEMPTS.clear()
    om._ALPACA_BREACH_DEFERRALS.clear()
    yield
    om._ALPACA_TOPUP_ATTEMPTS.clear()
    om._ALPACA_BREACH_DEFERRALS.clear()


_PX = {"QQQ": "740.00", "SPY": "770.00", "TLT": "80.00"}


class _Venue(AlpacaClient):
    """Real grading code; only the HTTP hop is faked. `position` is what
    GET /v2/positions/{sym} returns (the FRESH read); a POST that succeeds
    makes the new OCO rest, as the venue would."""

    def __init__(self, open_rows, position=None, post_rc=0):
        self.api_key, self.api_secret = "k", "s"
        self.open_rows = list(open_rows)
        self.position = position
        self.post_rc = post_rc
        self.calls: list = []

    def _request(self, method, path, json_body=None):  # type: ignore[override]
        self.calls.append((method, path, json_body))
        if method == "GET" and "status=open" in path:
            return {"retCode": 0, "result": self.open_rows}
        if method == "GET" and "status=closed" in path:
            return {"retCode": 0, "result": []}
        if method == "GET" and path.startswith("/v2/positions/"):
            if self.position is None:
                return {"retCode": 404, "retMsg": "position does not exist"}
            pos = dict(self.position)
            # Alpaca's position carries `current_price`; default to a price on
            # the protected side of every stop in this file (QQQ entries were
            # ~734.6 on 09-21, SPY ~759.9 on 09-24, the TLT short ~81).
            pos.setdefault("current_price", _PX.get(pos.get("symbol"), None))
            return {"retCode": 0, "result": pos}
        if method == "POST":
            if self.post_rc:
                return {"retCode": self.post_rc, "retMsg": "refused"}
            b = json_body
            self.open_rows.append(_oco(f"new-{len(self.calls)}", f"newstop-{len(self.calls)}",
                                       int(b["qty"]), float(b["take_profit"]["limit_price"]),
                                       float(b["stop_loss"]["stop_price"]), b["symbol"]))
            if b["side"] == "buy":   # short book: legs buy back
                self.open_rows[-1]["side"] = "buy"
                self.open_rows[-1]["legs"][0]["side"] = "buy"
            return {"retCode": 0, "result": {"id": "topup-oco"}}
        return {"retCode": 0, "result": {}}

    def latest_quote(self, symbol):
        return None                   # no bid/ask: the last-trade test stands

    def latest_trade(self, symbol):
        # A fresh print at the position's current price (REVIEW-14241 round 2).
        px = (self.position or {}).get("current_price")
        return None if px is None else {"price": float(px), "age_s": 5.0}

    def posts(self):
        return [b for m, _p, b in self.calls if m == "POST"]

    def deletes(self):
        return [p for m, p, _ in self.calls if m == "DELETE"]


def _run(venue, sym, size, rows, side="long"):
    pos = {"symbol": sym, "qty": str(size), "side": side}
    if venue.position is None:
        venue.position = pos
    cov = venue.protection_coverage(sym, position=pos)
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
    v = _Venue(PAPER_SPY, position={"symbol": "SPY", "qty": "19", "side": "long"})
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

    v = _SweepVenue(PAPER_QQQ, position={"symbol": "QQQ", "qty": "32", "side": "long"})
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



# ---------------------------------------------------------------------------
# REVIEW-14177: stale snapshot, short side, repeat sweep, truncated history,
# side mismatch, refused-POST cooldown.
# ---------------------------------------------------------------------------
def _qqq_rows():
    return [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            _row(6023, "QQQ", 22, 716.79928571, 787.86214286)]


def test_stale_snapshot_sibling_stop_filled_mid_sweep_refuses():
    """Graded on the 32-share snapshot; by placement time 5927's 10-sh stop
    has FILLED (position 22, its OCO gone). Placing 22 now would be right by
    accident only if the grade still held — it does not, so refuse and let
    the next sweep re-grade (which will see a fully naked 22 → naked re-arm)."""
    v = _Venue(PAPER_QQQ)
    pos = {"symbol": "QQQ", "qty": "32", "side": "long"}
    cov = v.protection_coverage("QQQ", position=pos)
    v.open_rows = []                                     # stop filled
    v.position = {"symbol": "QQQ", "qty": "22", "side": "long"}
    out = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ", cov, _qqq_rows(), NOW)
    assert out == "refused_state_changed"
    assert v.posts() == [] and v.deletes() == []


def test_position_gone_flat_before_placement_refuses():
    v = _Venue(PAPER_QQQ)
    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    v.position = None                                    # 404: flat now
    out = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ", cov, _qqq_rows(), NOW)
    assert out.startswith("refused_") and v.posts() == []


def test_short_side_top_up_buys_back():
    """A short 22+10 TLT-style book: legs are BUY; the top-up is a BUY OCO."""
    oco = _oco("11111111-0000-4000-8000-00000000000a", "11111111-0000-4000-8000-00000000000b",
               10, 70.00, 90.00, "TLT")
    oco["side"] = "buy"
    oco["legs"][0]["side"] = "buy"
    v = _Venue([oco])
    rows = [dict(_row(5912, "TLT", 10, 90.0, 70.0, "2026-09-18T13:31:28+00:00"), direction="short"),
            dict(_row(5913, "TLT", 22, 84.62, 74.67), direction="short")]
    _cov, out = _run(v, "TLT", 32, rows, side="short")
    assert out == "topped_up"
    (body,) = v.posts()
    assert body["side"] == "buy" and body["qty"] == "22"
    assert body["stop_loss"]["stop_price"] == "84.62"


def test_repeat_sweep_places_no_second_oco():
    v = _Venue(PAPER_QQQ)
    _cov, first = _run(v, "QQQ", 32, _qqq_rows())
    assert first == "topped_up"
    # Next sweep: the venue now shows the new OCO resting -> fully covered.
    cov2 = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    assert cov2["stop_qty"] == 32
    out2 = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ", cov2, _qqq_rows(), NOW)
    assert out2 == "refused_not_partial"
    # And even if a read lagged and re-graded the old 10/32, the cooldown holds.
    out3 = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ",
                                       {**cov2, "stop_qty": 10.0, "stop_leg_qtys": [10.0]},
                                       _qqq_rows(), NOW)
    assert out3 == "refused_cooldown"
    assert len(v.posts()) == 1


def test_refused_post_is_not_retried_every_tick():
    v = _Venue(PAPER_QQQ, post_rc=403)
    _cov, first = _run(v, "QQQ", 32, _qqq_rows())
    assert first == "refused_by_venue"
    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    second = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ", cov, _qqq_rows(), NOW)
    assert second == "refused_cooldown" and len(v.posts()) == 1


def test_truncated_history_refuses(monkeypatch):
    """REVIEW-14177 blocker b: a truncated closed-order scan may hide an old
    bracket stop; the fresh read is None and the top-up refuses."""
    from src.units.accounts import alpaca_client as ac
    v = _Venue(PAPER_QQQ)
    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    v.position = {"symbol": "QQQ", "qty": "32", "side": "long"}
    monkeypatch.setattr(ac, "_FILLED_PARENT_SCAN_PAGE", 1)
    monkeypatch.setattr(ac, "_FILLED_PARENT_SCAN_MAX_PAGES", 1)
    real = v._request

    def full_pages(method, path, json_body=None):
        if "status=closed" in path:
            v.calls.append((method, path, json_body))
            return {"retCode": 0, "result": [{"id": "x", "symbol": "QQQ", "status": "filled",
                                              "submitted_at": "2026-09-01T00:00:00Z", "legs": None}]}
        return real(method, path, json_body)
    v._request = full_pages
    out = om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ", cov, _qqq_rows(), NOW)
    assert out == "refused_fresh_read_unavailable" and v.posts() == []


def test_side_mismatch_refuses():
    v = _Venue(PAPER_QQQ)
    rows = [_row(5927, "QQQ", 10, 736.35, 787.33, "2026-09-18T16:09:37+00:00"),
            dict(_row(6023, "QQQ", 22, 716.8, 787.86), direction="short")]
    _cov, out = _run(v, "QQQ", 32, rows)
    assert out == "refused_side_mismatch" and v.posts() == []



def test_top_up_with_a_breached_stop_exits_the_row_labelled_sl(monkeypatch):
    """PR6YRTQY-0005 / REVIEW-14241 item 2, top-up form: QQQ at 710 is below
    6023's stop 716.80 — a top-up stop there would fill on arrival. The row is
    confirmed present, so it is EXITED via the trade-scoped close labelled sl;
    no OCO is placed and no sibling leg is touched."""
    monkeypatch.setattr(om, "_cooldown_admits", lambda *a, **k: False)
    monkeypatch.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "rth")
    closes = []
    monkeypatch.setattr(om, "_send_close_to_exchange",
                        lambda m: closes.append(m) or {"ok": True, "exchange_order_id": None})
    monkeypatch.setattr(om, "_capture_fill_details", lambda *a, **k: None)
    v = _Venue(PAPER_QQQ, position={"symbol": "QQQ", "qty": "32", "side": "long",
                                    "current_price": "710.00"})

    class _Db:
        updates = []

        def update_trade(self, tid, upd):
            self.updates.append((tid, upd))

    db = _Db()
    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    out = om._alpaca_top_up_uncovered(db, v, "alpaca_paper", "QQQ", cov, _qqq_rows(), NOW)
    assert out == "exited_breached_stop"
    assert v.posts() == [] and v.deletes() == []
    assert [c["id"] for c in closes] == [6023] and closes[0]["position_size"] == 22.0
    assert db.updates[0][0] == 6023 and db.updates[0][1]["exit_reason"] == "sl"


def test_top_up_breach_exit_deferred_when_the_market_is_closed(monkeypatch):
    """REVIEW-14241 round 2: the venue's own 'market closed — exit deferred'
    on a confirmed top-up breach is reported as exit_deferred (the sweep
    counts it so), never as exited or as a refusal; the row stays open."""
    monkeypatch.setattr(om, "_cooldown_admits", lambda *a, **k: False)
    monkeypatch.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "rth")
    monkeypatch.setattr(om, "_send_close_to_exchange",
                        lambda m: {"ok": False, "error": "market closed — exit deferred"})
    v = _Venue(PAPER_QQQ, position={"symbol": "QQQ", "qty": "32", "side": "long",
                                    "current_price": "710.00"})

    class _Db:
        updates = []

        def update_trade(self, tid, upd):
            self.updates.append((tid, upd))

    db = _Db()
    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    out = om._alpaca_top_up_uncovered(db, v, "alpaca_paper", "QQQ", cov, _qqq_rows(), NOW)
    assert out == "exit_deferred"
    assert v.posts() == [] and db.updates == []


def test_unconfirmed_top_up_breach_sets_no_cooldown_and_escalates(monkeypatch):
    """REVIEW-14241 round 3, B1+B2: QQQ's position price 710 is through 6023's
    stop 716.80 but no last trade can be read. Nothing is sent, so NO cooldown
    is set — every sweep looks again (never refused_cooldown) — and after
    _BREACH_DEFER_MAX deferrals the top-up stop is posted anyway."""
    monkeypatch.setattr(om, "_cooldown_admits", lambda *a, **k: False)
    monkeypatch.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "rth")

    class _NoTrade(_Venue):
        def latest_trade(self, symbol):
            return None

    v = _NoTrade(PAPER_QQQ, position={"symbol": "QQQ", "qty": "32", "side": "long",
                                      "current_price": "710.00"})

    class _Db:
        def update_trade(self, tid, upd):
            raise AssertionError("an unconfirmed breach must never close the row")

    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    outs = [om._alpaca_top_up_uncovered(_Db(), v, "alpaca_paper", "QQQ", cov,
                                        _qqq_rows(), NOW)
            for _ in range(om._BREACH_DEFER_MAX)]
    assert outs == ["deferred_unconfirmed_breach"] * om._BREACH_DEFER_MAX
    assert v.posts() == []
    assert om._ALPACA_TOPUP_ATTEMPTS == {}          # nothing sent -> no cooldown
    out = om._alpaca_top_up_uncovered(_Db(), v, "alpaca_paper", "QQQ", cov,
                                      _qqq_rows(), NOW)
    assert out == "topped_up"
    (p,) = v.posts()
    assert p["qty"] == "22" and p["stop_loss"]["stop_price"] == "716.80"


def test_top_up_clears_the_breach_strike_once_the_stop_is_clear(monkeypatch):
    monkeypatch.setattr(om, "_cooldown_admits", lambda *a, **k: False)
    monkeypatch.setattr("src.runtime.market_hours.us_equity_session", lambda *a, **k: "rth")
    om._ALPACA_BREACH_DEFERRALS[("alpaca_paper", "QQQ", 6023)] = 2
    om._PROTECTION_UNREADABLE_STREAK[("alpaca_paper", "QQQ", "breach")] = 2
    v = _Venue(PAPER_QQQ, position={"symbol": "QQQ", "qty": "32", "side": "long",
                                    "current_price": "740.00"})
    cov = v.protection_coverage("QQQ", position={"qty": "32", "side": "long"})
    assert om._alpaca_top_up_uncovered(None, v, "alpaca_paper", "QQQ", cov,
                                       _qqq_rows(), NOW) == "topped_up"
    assert ("alpaca_paper", "QQQ", 6023) not in om._ALPACA_BREACH_DEFERRALS
    assert ("alpaca_paper", "QQQ", "breach") not in om._PROTECTION_UNREADABLE_STREAK
