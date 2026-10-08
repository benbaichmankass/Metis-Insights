"""PROP-SUPERSEDE (operator 2026-10-08 ~20:03Z, "Approve as proposed";
PI-20261008-3QRUJSYR-0001): a newer same-strategy signal replaces a RESTING,
unfilled entry. Replays the tradeify_1 ETH-short incident: e6badec7ae10, a
LIMIT sell at 2471.31 placed 15:28Z, never filled; the 16:00Z signal
2116662b7d8d (entry 2433.80) was suppressed.

What these prove: the happy path runs cancel -> re-read confirm -> old
``skipped: superseded by <new>`` -> new released, in that order; and each
block condition (a position, a partial fill, an unreadable read, a cancel
that does not remove the row, another strategy or direction, the new
ticket's entry band failing) clicks nothing (or releases nothing) and leaves
today's block standing. The release half reuses the reissue checks and the
emission half marks candidates only for a ``placed`` same-strategy blocker.

What they do NOT prove: that the live DXtrade cancel removes the row; that
is #17158/#17167's measurement.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.prop import prop_executor as pe
from src.prop import prop_supersede as ps
from src.prop.platform.base import AccountSnapshot, Position, WorkingOrder

NOW = datetime(2026, 10, 8, 16, 5, tzinfo=timezone.utc)
OLD = "prop-manual-e6badec7ae10"
NEW = "prop-manual-2116662b7d8d"
STRAT = "trend_donchian_eth_prop"
BAND = "Entry : 2433.8 (only if live price is within 2417.4 … 2450.2)"
SPEC = {"ticket_id": OLD, "venue_symbol": "ETHUSD", "side": "short", "quantity": 0.76,
        "limit_price": 2471.31, "stop_loss": 2532.158, "take_profit": 2226.65}


def cfg() -> pe.ExecutorConfig:
    return pe.ExecutorConfig(
        account_id="tradeify_1", account_size_usd=50000.0, daily_loss_pct=0.03, max_dd_pct=0.06,
        safety_margin_usd=5.0, risk_cap_usd=75.0, unconfirmed_reads=3,
        symbols={"ETHUSDT": {"venue": "ETHUSD", "cvpp": 1.0, "lot_units": 1.0, "min_lots": 0.01,
                             "lot_step": 0.01}})


def _order(**kw) -> WorkingOrder:
    d = dict(symbol="ETHUSD", side="short", quantity=0.76, price=2471.31, stop_loss=2532.158,
             take_profit=2226.65, order_id="18912998")
    d.update(kw)
    return WorkingOrder(**d)


def _pos(**kw) -> Position:
    d = dict(symbol="ETHUSD", side="short", quantity=0.3, entry_price=2471.31, stop_loss=2532.158,
             take_profit=2226.65, unrealized_pnl=0.0)
    d.update(kw)
    return Position(**d)


def _old(**kw):
    t = {"ticket_id": OLD, "status": "placed", "strategy": STRAT, "symbol": "ETHUSDT", "direction": "short",
         "created_at": "2026-10-08T15:28:00+00:00"}
    t.update(kw)
    return t


def _new(**kw):
    t = {"ticket_id": NEW, "status": "suppressed", "strategy": STRAT, "symbol": "ETHUSDT", "direction": "short",
         "created_at": "2026-10-08T16:00:01+00:00",
         "supersede": {"blocked_by": OLD, "message": BAND,
                       "valid_until": (NOW + timedelta(minutes=55)).isoformat()}}
    t.update(kw)
    return t


class Adapter:
    """A terminal with one resting ETH-short LIMIT. ``cancel_removes`` decides
    whether the cancel click takes the row off the next read."""

    def __init__(self, positions=(), orders=None, quote=None, cancel_removes=True, cancel_result=None,
                 reread_error=False, fill_on_cancel=False):
        self.positions = list(positions)
        self.orders = [_order()] if orders is None else list(orders)
        self.quote = quote if quote is not None else {"bid": 2430.0, "ask": 2430.5}
        self.cancel_removes, self.cancel_result = cancel_removes, cancel_result
        self.reread_error, self.fill_on_cancel = reread_error, fill_on_cancel
        self.calls, self.cancelled = [], False

    def read_account(self, page):
        return AccountSnapshot(balance=50000.0, equity=50000.0, unrealized=0.0, realized_today=0.0)

    def read_positions(self, page):
        if self.cancelled and self.reread_error:
            raise LookupError("selector drift")
        return list(self.positions)

    def read_orders(self, page):
        return list(self.orders)

    def read_quote(self, page, venue):
        self.calls.append(("read_quote", venue))
        return self.quote

    def cancel_order(self, page, order, *, arm=False):
        self.calls.append(("cancel_order", order.order_id, arm))
        r = self.cancel_result or {"ok": True, "clicked": arm}
        if arm and r.get("clicked"):
            self.cancelled = True
            if self.cancel_removes:
                self.orders = [o for o in self.orders if o.order_id != order.order_id]
            if self.fill_on_cancel:
                self.positions = [_pos(quantity=0.76)]
        return r

    def place_bracket(self, page, spec, *, arm=False):  # pragma: no cover - never reached here
        raise AssertionError("supersede must never place in the cycle that cancelled")


class Api:
    def __init__(self, all_tickets, release=None, fills=()):
        self._all, self._fills = list(all_tickets), list(fills)
        self.posts = []
        self.release = release if release is not None else {"ok": True, "kind": "supersede", "released": True,
                                                              "valid_until": "2026-10-08T17:05:00+00:00"}

    def tickets(self, account_id):
        return [t for t in self._all if t.get("status") == "emitted"]

    def all_tickets(self, account_id, limit=200):
        return list(self._all)

    def open_fills(self, account_id):
        return pe.open_from_fills(self._fills)

    def post_report(self, body):
        self.posts.append(body)
        return self.release if body.get("kind") == "supersede" else {"ok": True}


@pytest.fixture
def env(tmp_path):
    ledger = pe.IntentLedger(tmp_path / "ledger.jsonl")
    state = pe.ExecutorState(tmp_path)
    state.save({"day": pe.trading_day(NOW), "day_start_captured": 50000.0})
    ledger.record(OLD, "placed", spec=dict(SPEC), valid_until="2026-10-08T16:26:53+00:00")
    return ledger, state


def run(adapter, api, env, mode="live"):
    ledger, state = env
    return pe.run_cycle(adapter=adapter, page=None, api=api, cfg=cfg(), mode=mode, ledger=ledger,
                        state=state, now=NOW)


def _whats(res):
    return [a["what"] for a in res.actions]


def _blocked(res):
    return [a for a in res.actions if a["what"] == "supersede_blocked"]


# ── the happy path: the incident replayed ────────────────────────────────


def test_happy_path_cancel_confirm_superseded_then_released(env):
    ad, api = Adapter(), Api([_old(), _new()])
    res = run(ad, api, env)
    assert ("cancel_order", "18912998", True) in ad.calls
    kinds = [(p.get("kind"), p.get("status"), p.get("reason"), p.get("ticket_id")) for p in api.posts
             if p.get("kind") != "account_status"]
    # the old ticket is reported skipped BEFORE the new one is released
    assert kinds == [("fill", "skipped", f"superseded by {NEW}", OLD), ("supersede", None, None, NEW)]
    assert api.posts[-1]["supersedes"] == OLD
    assert "superseded" in _whats(res) and not res.alerts
    row = env[0].latest()[OLD]
    assert row["state"] == "skipped" and row["superseded_by"] == NEW and row["released"] == NEW
    # intake holds this cycle (its read predates the cancel): nothing placed
    assert not [c for c in ad.calls if c[0] == "place_bracket"]


def test_new_ticket_is_then_taken_by_normal_intake(env):
    ad, api = Adapter(), Api([_old(), _new()])
    run(ad, api, env)
    released = {**_new(status="emitted", supersede=None), "qty": 0.7, "risk_usd": 50.0, "message": BAND,
                "entry": 2433.8, "sl": 2499.24, "tp": 2192.85,
                "valid_until": "2026-10-08T17:05:00+00:00"}
    api2 = Api([_old(status="skipped"), released])
    res = run(Adapter(orders=[]), api2, env, mode="read_only")
    # the old entry is out of the watched ledger; intake sees the new ticket
    assert OLD not in env[0].watched()
    assert any(a.get("ticket_id") == NEW for a in res.actions if a["what"] in ("band_ok", "guards"))


def test_read_only_clicks_nothing_and_writes_nothing(env):
    ad, api = Adapter(), Api([_old(), _new()])
    res = run(ad, api, env, mode="read_only")
    assert not [c for c in ad.calls if c[0] == "cancel_order"] and not api.posts
    assert "would_supersede" in _whats(res)
    assert env[0].latest()[OLD]["state"] == "placed"


# ── block conditions: today's block stands, nothing is clicked ───────────


def _assert_blocked(env, ad, api, res, needle):
    assert not [c for c in ad.calls if c[0] == "cancel_order" and c[2]], ad.calls
    assert not [p for p in api.posts if p.get("kind") in ("supersede", "fill")]
    assert env[0].latest()[OLD]["state"] in ("placed", "contained")
    if needle:
        assert any(needle in b["why"] for b in _blocked(res)) or any(needle in a for a in res.alerts), \
            (res.actions, res.alerts)


def test_block_when_a_position_exists(env):
    # a position on the venue nobody journaled: the reconcile halts on the
    # orphan, and supersede never runs while halted
    ad, api = Adapter(positions=[_pos(quantity=0.76, side="long")]), Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, None)
    assert res.halted and "would_supersede" not in _whats(res)


@pytest.mark.parametrize("pos", [_pos(quantity=0.76), _pos(quantity=0.76, side="long"), _pos(quantity=0.3)])
def test_block_guard_refuses_any_position_on_the_symbol(env, pos):
    # the guard itself, without the reconcile in front of it
    why, order = pe._supersede_block(None, Adapter(), None, cfg(), env[0].latest()[OLD], _old(), _new(),
                                     [pos], [_order()], [], NOW)
    assert order is None and "position(s) on ETHUSD" in why


def test_block_on_a_partial_fill(env):
    ad, api = Adapter(positions=[_pos(quantity=0.3)]), Api([_old(), _new()])
    res = run(ad, api, env)
    # step 3 contains it as a suspected partial fill and halts; supersede never runs
    _assert_blocked(env, ad, api, res, "partial_fill_suspected")
    assert "supersede_blocked" not in _whats(res) and "would_supersede" not in _whats(res)


def test_block_when_the_journal_holds_an_open_position(env):
    fills = [{"id": 1, "account_id": "tradeify_1", "symbol": "ETHUSDT", "direction": "short", "status": "open",
              "ticket_id": "prop-manual-zzz", "created_at": "2026-10-08T15:00:00+00:00"}]
    ad, api = Adapter(), Api([_old(), _new()], fills=fills)
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, None)
    assert "superseded" not in _whats(res)


def test_block_when_the_terminal_read_is_unreadable(env):
    ad = Adapter()
    ad.read_orders = lambda page: (_ for _ in ()).throw(LookupError("orders table not found"))
    api = Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, None)
    assert res.halted and "terminal read failed" in res.halted


def test_block_when_the_quote_is_unreadable(env):
    ad, api = Adapter(quote={}), Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, "entry band check is blind")


def test_cancel_click_that_does_not_remove_the_row_releases_nothing(env):
    ad, api = Adapter(cancel_removes=False), Api([_old(), _new()])
    res = run(ad, api, env)
    assert ("cancel_order", "18912998", True) in ad.calls
    assert not [p for p in api.posts if p.get("kind") in ("supersede", "fill")]
    assert any("STILL on the terminal" in a for a in res.alerts)
    row = env[0].latest()[OLD]
    assert row["state"] == "placed" and row["supersede_attempts"] == 1 and not row.get("supersede_requested")


def test_cancel_that_does_not_click_releases_nothing(env):
    ad = Adapter(cancel_result={"ok": False, "clicked": False, "why": "need exactly 1 row and 1 control"})
    api = Api([_old(), _new()])
    res = run(ad, api, env)
    assert not [p for p in api.posts if p.get("kind") in ("supersede", "fill")]
    assert any("cancel did not click" in a for a in res.alerts)
    assert env[0].latest()[OLD]["supersede_attempts"] == 1


def test_attempts_are_bounded(env):
    ledger, _ = env
    ledger.record(OLD, "placed", supersede_attempts=pe.SUPERSEDE_MAX_ATTEMPTS)
    ad, api = Adapter(), Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, "supersede cancel attempts spent")


def test_reread_failure_after_the_cancel_releases_nothing(env):
    ad, api = Adapter(reread_error=True), Api([_old(), _new()])
    res = run(ad, api, env)
    assert not [p for p in api.posts if p.get("kind") in ("supersede", "fill")]
    assert any("re-read failed" in a for a in res.alerts)
    assert env[0].latest()[OLD]["supersede_requested"] == NEW


def test_reread_failure_then_order_gone_reports_superseded_and_releases(env):
    ad, api = Adapter(reread_error=True), Api([_old(), _new()])
    run(ad, api, env)
    ad.reread_error = False  # the order is gone (the click took)
    run(ad, api, env)        # first miss
    api.posts.clear()
    res = run(ad, api, env)  # second miss: skipped with the supersede reason, then released
    kinds = [(p.get("kind"), p.get("reason")) for p in api.posts if p.get("kind") != "account_status"]
    assert ("fill", f"superseded by {NEW}") in kinds and kinds[-1] == ("supersede", None)
    assert "superseded" in _whats(res)


def test_a_fill_in_the_race_releases_nothing(env):
    ad, api = Adapter(fill_on_cancel=True), Api([_old(), _new()])
    res = run(ad, api, env)
    assert not [p for p in api.posts if p.get("kind") in ("supersede", "fill")]
    assert any("filled in the race" in a for a in res.alerts)


@pytest.mark.parametrize("new_kw,needle", [
    ({"strategy": "fade_eth_prop"}, "strategy differs"),
    ({"direction": "long"}, "direction differs"),
])
def test_block_on_a_different_strategy_or_direction(env, new_kw, needle):
    ad, api = Adapter(), Api([_old(), _new(**new_kw)])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, needle)


def test_block_when_the_new_ticket_fails_its_entry_band(env):
    # bid 2460 is outside the new ticket's band 2417.4..2450.2: intake would
    # not place it now, so the resting entry is NOT cancelled (fail closed).
    ad, api = Adapter(quote={"bid": 2460.0, "ask": 2460.5}), Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, "entry band check is wait")


def test_block_when_the_new_ticket_is_stale(env):
    stale = _new()
    stale["supersede"]["valid_until"] = (NOW - timedelta(minutes=1)).isoformat()
    ad, api = Adapter(), Api([_old(), stale])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, "validity passed")


def test_block_when_another_order_rests_on_the_symbol(env):
    ad = Adapter(orders=[_order(), _order(order_id="999", quantity=0.2, price=2480.0)])
    api = Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, "other working order(s) on ETHUSD")


def test_block_when_the_expiry_cancel_owns_the_order(env):
    env[0].record(OLD, "placed", cancel_attempts=1)
    ad, api = Adapter(), Api([_old(), _new()])
    res = run(ad, api, env)
    _assert_blocked(env, ad, api, res, "expiry cancel already owns")


def test_release_refusal_alerts_once(env):
    ad = Adapter()
    api = Api([_old(), _new()], release={"ok": True, "kind": "supersede", "released": False, "why": "too old"})
    res = run(ad, api, env)
    assert any("was NOT released (too old)" in a for a in res.alerts)
    assert env[0].latest()[OLD]["state"] == "skipped"


# ── the release half (server side, reuses the reissue checks) ────────────

COLS = ("ticket_id, account_id, strategy, symbol, direction, side, entry, sl, tp, qty, risk_usd, "
        "signal_time, valid_until, status, order_package_id, message, meta, created_at")


@pytest.fixture
def conn(tmp_path: Path):
    c = sqlite3.connect(tmp_path / "j.db")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE prop_tickets (ticket_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, strategy TEXT, "
              "symbol TEXT, direction TEXT, side TEXT, entry REAL, sl REAL, tp REAL, qty REAL, risk_usd REAL, "
              "signal_time TEXT, valid_until TEXT, status TEXT NOT NULL, order_package_id TEXT, message TEXT, "
              "meta TEXT, created_at TEXT NOT NULL)")
    c.execute("CREATE TABLE prop_fills (id INTEGER PRIMARY KEY, ticket_id TEXT, status TEXT, reason TEXT)")

    def ins(tid, status, message, meta=None, strategy=STRAT):
        c.execute(f"INSERT INTO prop_tickets ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, "tradeify_1", strategy, "ETHUSDT", "short", None, 2433.80, 2499.24, 2192.85, None, None,
                   "2026-10-08T16:00:00+00:00", None, status, "pkg-1", message,
                   json.dumps(meta) if meta else None, "2026-10-08T16:00:01+00:00"))
    ins(OLD, "skipped", "ticket text")
    ins(NEW, "suppressed", f"reticket suppressed — outstanding_ticket:placed: {OLD}",
        {"supersede": {"blocked_by": OLD, "message": BAND}})
    c.execute("INSERT INTO prop_fills (ticket_id, status, reason) VALUES (?, 'skipped', ?)",
              (OLD, f"superseded by {NEW}"))
    c.commit()
    yield c
    c.close()


FIELDS = {"side": "Sell", "qty": 0.76, "risk_usd": 50.0, "valid_until": "2026-10-08T17:05:00+00:00",
          "message": BAND, "meta": {}}
ACCT = {"mode": "live", "strategies": [STRAT], "account_id": "tradeify_1"}


def _release(conn, **kw):
    args = dict(now=NOW, conn=conn, account_cfg=ACCT, strategy_info=lambda s: ("live", "1h"),
                guard=lambda *a: None, rebuild=lambda row, cfg, now, timeframe: (dict(FIELDS), ""))
    args.update(kw)
    return ps.release("tradeify_1", NEW, OLD, **args)


def _status(conn, tid):
    return conn.execute("SELECT status FROM prop_tickets WHERE ticket_id=?", (tid,)).fetchone()[0]


def test_release_rebuilds_and_emits(conn):
    out = _release(conn)
    assert out["released"], out
    r = dict(conn.execute("SELECT * FROM prop_tickets WHERE ticket_id=?", (NEW,)).fetchone())
    assert (r["status"], r["qty"], r["valid_until"]) == ("emitted", 0.76, FIELDS["valid_until"])
    meta = json.loads(r["meta"])
    assert meta["supersede"]["supersedes"] == OLD and meta["reissue"]["blocked_by"] == OLD


def test_release_refuses_while_the_old_ticket_is_still_placed(conn):
    conn.execute("UPDATE prop_tickets SET status='placed' WHERE ticket_id=?", (OLD,))
    out = _release(conn)
    assert not out["released"] and "not skipped" in out["why"] and _status(conn, NEW) == "suppressed"


def test_release_refuses_without_the_superseded_fill_row(conn):
    conn.execute("UPDATE prop_fills SET reason='expired'")
    out = _release(conn)
    assert not out["released"] and "fill row" in out["why"]


def test_release_refuses_without_a_candidate_naming_the_old_ticket(conn):
    conn.execute("UPDATE prop_tickets SET meta=NULL WHERE ticket_id=?", (NEW,))
    out = _release(conn)
    assert not out["released"] and "no supersede candidate" in out["why"]


def test_release_refuses_when_the_old_ticket_had_a_position(conn):
    conn.execute("INSERT INTO prop_fills (ticket_id, status) VALUES (?, 'open')", (OLD,))
    out = _release(conn)
    assert not out["released"] and "position-bearing" in out["why"]


def test_release_refuses_on_a_different_strategy(conn):
    conn.execute("UPDATE prop_tickets SET strategy='other' WHERE ticket_id=?", (OLD,))
    out = _release(conn)
    assert not out["released"] and "strategy differs" in out["why"]


def test_release_refuses_when_the_rebuild_skips(conn):
    out = _release(conn, rebuild=lambda row, cfg, now, timeframe: (None, "sizing skips"))
    assert not out["released"] and "sizing skips" in out["why"] and _status(conn, NEW) == "suppressed"


def test_release_refuses_when_the_guard_still_suppresses(conn):
    out = _release(conn, guard=lambda *a: "open_position: 0.7 @ 2440")
    assert not out["released"] and "still suppresses" in out["why"]


def test_ingest_report_routes_the_supersede_kind(monkeypatch):
    from src.prop import prop_report
    seen = {}
    monkeypatch.setattr(ps, "release", lambda a, n, o: seen.setdefault("args", (a, n, o)) and {"released": True})
    out = prop_report.ingest_report({"kind": "supersede", "account_id": "tradeify_1", "ticket_id": NEW,
                                     "supersedes": OLD})
    assert out["kind"] == "supersede" and seen["args"] == ("tradeify_1", NEW, OLD)
    with pytest.raises(ValueError):
        prop_report.ingest_report({"kind": "supersede", "account_id": "tradeify_1", "ticket_id": NEW})


# ── the emission half: candidates only for a placed same-strategy blocker ─


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    from src.prop import prop_journal
    return prop_journal


def _blocker(journal, status="placed", strategy=STRAT):
    journal.record_ticket({"ticket_id": OLD, "account_id": "tradeify_1", "strategy": strategy,
                           "symbol": "ETHUSDT", "direction": "short", "entry": 2471.31, "sl": 2532.158,
                           "tp": 2226.65, "status": status,
                           "valid_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})


def _emit(monkeypatch, rebuild=None):
    from scripts.ops import prop_ticket_reissue as ptr
    from src.prop import breakout_executor as be
    monkeypatch.setattr(ptr, "rebuild_fields", rebuild or (lambda row, cfg, now, timeframe: (dict(FIELDS), "")))
    order = {"symbol": "ETHUSDT", "direction": "short", "entry": 2433.80, "sl": 2499.24, "tp": 2192.85,
             "strategy": STRAT}
    return be.emit_prop_ticket(order, {"account_id": "tradeify_1", "exchange": "breakout"},
                               _emitter=lambda t: None)


def test_emission_marks_a_candidate_for_a_placed_same_strategy_blocker(journal, monkeypatch):
    _blocker(journal)
    tid = _emit(monkeypatch)
    row = journal.get_ticket(tid)
    assert row["status"] == "suppressed" and OLD in row["message"]
    assert row["meta"]["supersede"]["blocked_by"] == OLD and row["meta"]["supersede"]["message"] == BAND
    view = [t for t in journal.list_outbound_tickets(account_id="tradeify_1") if t["ticket_id"] == tid]
    assert view and view[0]["supersede"]["blocked_by"] == OLD


@pytest.mark.parametrize("status,strategy", [("placed", "fade_eth_prop"), ("awaiting_report", STRAT),
                                             ("claimed", STRAT), ("emitted", STRAT)])
def test_emission_marks_no_candidate_otherwise(journal, monkeypatch, status, strategy):
    _blocker(journal, status=status, strategy=strategy)
    tid = _emit(monkeypatch)
    row = journal.get_ticket(tid)
    assert row["status"] == "suppressed" and not (row.get("meta") or {}).get("supersede")


def test_emission_marks_no_candidate_when_the_rebuild_skips(journal, monkeypatch):
    _blocker(journal)
    tid = _emit(monkeypatch, rebuild=lambda row, cfg, now, timeframe: (None, "sizing skips"))
    row = journal.get_ticket(tid)
    assert row["status"] == "suppressed" and not (row.get("meta") or {}).get("supersede")


def test_a_different_direction_is_not_suppressed_at_all(journal, monkeypatch):
    _blocker(journal)
    from src.prop import breakout_executor as be
    assert be._reticket_suppress_reason("tradeify_1", "ETHUSDT", "long") is None
