"""prop-ticket-reissue (PROP-REISSUE, 2026-10-08): ONE `suppressed` prop ticket
whose blocker is terminal -> `emitted`, rebuilt with emission's own sizing;
every other case refused (exit 3), nothing written."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.ops import prop_ticket_reissue as ptr

NOW = "2026-10-08T16:40:00+00:00"
SUP = "prop-manual-2116662b7d8d"
BLOCKER = "prop-manual-e6badec7ae10"
COLS = ("ticket_id, account_id, strategy, symbol, direction, side, entry, sl, tp, qty, risk_usd, "
        "signal_time, valid_until, status, order_package_id, message, meta, created_at")


def _ins(c, tid, acct="tradeify_1", status="suppressed", message=None, signal_time="2026-10-08T16:00:00+00:00",
         strategy="trend_donchian_eth_prop", created_at="2026-10-08T16:00:01+00:00"):
    c.execute(f"INSERT INTO prop_tickets ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (tid, acct, strategy, "ETHUSDT", "short", None, 2433.80, 2499.24464286, 2192.8538,
               None, None, signal_time, None, status, "pkg-d2f2ad9712c34a3a", message, None, created_at))


def _sup_msg(blocker=BLOCKER, status="placed"):
    return f"reticket suppressed — outstanding_ticket:{status}: {blocker}"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "trade_journal.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE prop_tickets (ticket_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, strategy TEXT, "
              "symbol TEXT, direction TEXT, side TEXT, entry REAL, sl REAL, tp REAL, qty REAL, risk_usd REAL, "
              "signal_time TEXT, valid_until TEXT, status TEXT NOT NULL, order_package_id TEXT, message TEXT, "
              "meta TEXT, created_at TEXT NOT NULL)")
    c.execute("CREATE TABLE prop_fills (id INTEGER PRIMARY KEY, ticket_id TEXT)")
    _ins(c, BLOCKER, status="skipped", message="ticket text", signal_time="2026-10-08T15:26:53+00:00")
    _ins(c, SUP, message=_sup_msg())
    c.commit()
    c.close()
    return p


@pytest.fixture
def cfgs(tmp_path: Path):
    a = tmp_path / "accounts.yaml"
    a.write_text("accounts:\n"
                 "  tradeify_1: {exchange: breakout, type: prop, mode: live,\n"
                 "               strategies: [trend_donchian_eth_prop]}\n"
                 "  velotrade_1: {exchange: breakout, type: prop, mode: live,\n"
                 "               strategies: [trend_donchian_eth_prop]}\n"
                 "  dry_1: {exchange: breakout, type: prop, mode: dry_run,\n"
                 "          strategies: [trend_donchian_eth_prop]}\n")
    s = tmp_path / "strategies.yaml"
    s.write_text("strategies:\n"
                 "  trend_donchian_eth_prop: {execution: live, timeframe: 1h}\n"
                 "  shadow_leg: {execution: shadow, timeframe: 1h}\n")
    return a, s


FIELDS = {"side": "Sell", "qty": 0.76400447, "risk_usd": 50.0, "valid_until": "2026-10-08T17:40:00+00:00",
          "message": "Entry : 2433.8 (only if live price is within 2417.4 … 2450.2)", "meta": {}}


def _ok_rebuild(row, cfg, now, *, timeframe):
    assert timeframe == "1h"
    return dict(FIELDS), ""


def _run(db, cfgs, capsys, tid=SUP, account="tradeify_1", apply=False, guard=None, rebuild=None, now=NOW):
    a, s = cfgs
    argv = ["--db", str(db), "--account", account, "--ticket-id", tid, "--accounts", str(a),
            "--strategies", str(s), "--now", now] + (["--apply"] if apply else [])
    rc = ptr.main(argv, guard=guard or (lambda *x: None), rebuild=rebuild or _ok_rebuild)
    return rc, json.loads(capsys.readouterr().out)


def _row(db, tid=SUP):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return dict(c.execute("SELECT * FROM prop_tickets WHERE ticket_id=?", (tid,)).fetchone())


def test_dry_run_plans_and_writes_nothing(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys)
    assert rc == 0 and out["ok"] and out["mode"] == "dry_run"
    assert out["after"]["status"] == "emitted" and out["after"]["qty"] == FIELDS["qty"]
    assert _row(db)["status"] == "suppressed" and _row(db)["qty"] is None


def test_apply_writes_rebuilt_ticket_backs_up_and_rerun_refuses(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys, apply=True)
    assert rc == 0 and Path(out["backup"]).exists()
    r = _row(db)
    assert (r["status"], r["side"], r["qty"], r["risk_usd"], r["valid_until"]) == (
        "emitted", "Sell", FIELDS["qty"], 50.0, FIELDS["valid_until"])
    assert r["message"] == FIELDS["message"]
    meta = json.loads(r["meta"])
    assert meta["reissue"]["blocked_by"] == BLOCKER and meta["reissue"]["blocker_status"] == "skipped"
    # identity columns untouched
    assert (r["entry"], r["sl"], r["tp"], r["order_package_id"]) == (2433.80, 2499.24464286, 2192.8538,
                                                                     "pkg-d2f2ad9712c34a3a")
    rc, out = _run(db, cfgs, capsys, apply=True)
    assert rc == 3 and "not 'suppressed'" in out["why"]


# ── one planted refusal per guard; each must write nothing ─────────────────

def _assert_refused(db, rc, out, needle, tid=SUP):
    assert rc == 3 and not out["ok"] and needle in out["why"], out.get("why")
    assert _row(db, tid)["status"] in ("suppressed", "placed", "emitted")
    assert _row(db, tid)["qty"] is None


def test_refuses_not_suppressed(db, cfgs, capsys):
    c = sqlite3.connect(db)
    _ins(c, "prop-manual-aaaaaa111111", status="emitted", message=_sup_msg())
    c.commit()
    rc, out = _run(db, cfgs, capsys, tid="prop-manual-aaaaaa111111")
    _assert_refused(db, rc, out, "not 'suppressed'", tid="prop-manual-aaaaaa111111")


@pytest.mark.parametrize("status", ["placed", "awaiting_report", "claimed", "emitted", "expiry_prompted"])
def test_refuses_blocker_not_terminal(db, cfgs, capsys, status):
    sqlite3.connect(db).execute("UPDATE prop_tickets SET status=? WHERE ticket_id=?", (status, BLOCKER)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "not terminal")


def test_refuses_missing_blocker(db, cfgs, capsys):
    sqlite3.connect(db).execute("DELETE FROM prop_tickets WHERE ticket_id=?", (BLOCKER,)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "not found")


def test_refuses_open_position_suppression(db, cfgs, capsys):
    msg = "reticket suppressed — open_position: 0.8 @ 2471.3 since x (ticket prop-manual-e6badec7ae10)"
    sqlite3.connect(db).execute("UPDATE prop_tickets SET message=? WHERE ticket_id=?", (msg, SUP)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "names no blocking ticket")


def test_refuses_other_accounts_ticket(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys, account="velotrade_1")
    _assert_refused(db, rc, out, "belongs to tradeify_1")


def test_refuses_blocker_of_other_account(db, cfgs, capsys):
    sqlite3.connect(db).execute("UPDATE prop_tickets SET account_id='velotrade_1' WHERE ticket_id=?",
                                (BLOCKER,)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "belongs to velotrade_1")


def test_refuses_account_not_live(db, cfgs, capsys):
    sqlite3.connect(db).execute("UPDATE prop_tickets SET account_id='dry_1'").connection.commit()
    rc, out = _run(db, cfgs, capsys, account="dry_1")
    _assert_refused(db, rc, out, "not 'live'")


def test_refuses_unknown_account(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys, account="nobody_1")
    assert rc == 3 and "not in config/accounts.yaml" in out["why"]


def test_refuses_ticket_with_fills(db, cfgs, capsys):
    sqlite3.connect(db).execute("INSERT INTO prop_fills (ticket_id) VALUES (?)", (SUP,)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "prop_fills")


def test_refuses_too_old(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys, now="2026-10-08T18:00:01+00:00")
    _assert_refused(db, rc, out, "re-issue bound")


def test_refuses_unreadable_age(db, cfgs, capsys):
    sqlite3.connect(db).execute("UPDATE prop_tickets SET signal_time=NULL, created_at='' WHERE ticket_id=?",
                                (SUP,)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "age unreadable")


def test_refuses_strategy_off_roster_or_shadow(db, cfgs, capsys):
    sqlite3.connect(db).execute("UPDATE prop_tickets SET strategy='shadow_leg' WHERE ticket_id=?",
                                (SUP,)).connection.commit()
    rc, out = _run(db, cfgs, capsys)
    _assert_refused(db, rc, out, "roster")


def test_refuses_when_live_guard_still_suppresses(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys, guard=lambda *x: "outstanding_ticket:emitted: prop-manual-ffffff000000")
    _assert_refused(db, rc, out, "still suppresses")


def test_refuses_when_rebuild_skips(db, cfgs, capsys):
    rc, out = _run(db, cfgs, capsys, rebuild=lambda *a, **k: (None, "leg skips: size rounds to zero"))
    _assert_refused(db, rc, out, "rebuild refused")


# ── the rebuild is emission's own sizing, not a second formula ─────────────

def test_rebuild_matches_emit_prop_ticket(monkeypatch, tmp_path):
    """The SAME order through ``emit_prop_ticket`` and ``rebuild_fields`` gives
    the same side / qty / risk / validity / ticket text (bar the ticket id
    the text carries). Run on the real config/accounts.yaml tradeify_1 entry
    so a ruleset/sizing drift between the two paths fails here."""
    from datetime import datetime, timezone

    from src.prop import breakout_executor as be
    from src.prop import prop_journal

    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "tj.db"))
    monkeypatch.setattr(be, "_reticket_suppress_reason", lambda *a, **k: None)
    fixed = datetime(2026, 10, 8, 16, 40, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(be, "datetime", _DT)
    recorded = {}
    monkeypatch.setattr(prop_journal, "record_ticket", lambda t: recorded.update(t) or t["ticket_id"])

    cfg = ptr.account_cfg_for("tradeify_1")
    assert cfg is not None and cfg.get("backtest_ruleset"), "tradeify_1 must carry its ruleset"
    order = {"symbol": "ETHUSDT", "direction": "short", "side": "Sell", "entry": 2433.80,
             "sl": 2499.24464286, "tp": 2192.8538, "strategy": "trend_donchian_eth_prop", "meta": {}}
    tid = be.emit_prop_ticket(order, cfg, timeframe="1h", _emitter=lambda t: None)
    assert recorded.get("status") == "emitted", recorded

    row = {"ticket_id": tid, "account_id": "tradeify_1", "strategy": "trend_donchian_eth_prop",
           "symbol": "ETHUSDT", "direction": "short", "entry": 2433.80, "sl": 2499.24464286, "tp": 2192.8538}
    fields, why = ptr.rebuild_fields(row, cfg, fixed, timeframe="1h")
    assert why == "" and fields is not None
    for k in ("side", "qty", "risk_usd", "valid_until", "message"):
        assert fields[k] == recorded[k], k
    assert fields["valid_until"] == "2026-10-08T17:40:00+00:00"
