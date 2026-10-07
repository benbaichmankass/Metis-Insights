"""prop-ticket-expire (VELOTRADE-TICKET, 2026-10-07): ONE dead prop ticket on a
REST-executed account -> `expired`; every other transition refused, nothing written."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.ops import prop_ticket_expire as pte

NOW = "2026-10-07T18:00:00+00:00"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "trade_journal.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE prop_tickets (ticket_id TEXT PRIMARY KEY, account_id TEXT, strategy TEXT, "
              "symbol TEXT, direction TEXT, status TEXT, valid_until TEXT, created_at TEXT)")
    c.execute("CREATE TABLE prop_fills (id INTEGER PRIMARY KEY, ticket_id TEXT)")
    rows = [
        ("prop-manual-aaa111", "velotrade_1", "expiry_prompted", "2026-10-07T02:59:24+00:00"),
        ("prop-manual-bbb222", "velotrade_1", "emitted", "2026-10-07T17:00:00+00:00"),
        ("prop-manual-ccc333", "velotrade_1", "emitted", "2026-10-07T19:00:00+00:00"),
        ("prop-manual-ddd444", "velotrade_1", "placed", "2026-10-07T02:00:00+00:00"),
        ("prop-manual-eee555", "breakout_1", "expiry_prompted", "2026-10-07T02:00:00+00:00"),
        ("prop-manual-fff666", "velotrade_1", "expiry_prompted", "2026-10-07T02:00:00+00:00"),
        ("prop-manual-ggg777", "velotrade_1", "emitted", None),
    ]
    for tid, acct, st, vu in rows:
        c.execute("INSERT INTO prop_tickets VALUES (?,?,?,?,?,?,?,?)",
                  (tid, acct, "trend_donchian_eth_prop", "ETHUSDT", "short", st, vu, "2026-10-07T01:59:22+00:00"))
    c.execute("INSERT INTO prop_fills (ticket_id) VALUES ('prop-manual-fff666')")
    c.commit()
    c.close()
    return p


@pytest.fixture
def platforms(tmp_path: Path) -> Path:
    p = tmp_path / "prop_platforms.yaml"
    p.write_text("accounts:\n"
                 "  breakout_1: {platform: dxtrade}\n"
                 "  velotrade_1: {platform: dxtrade_api, login_url: https://x.example/dxsca-web}\n"
                 "phone_accounts:\n  breakout_2: {platform: breakout_phone}\n")
    return p


def _run(db, platforms, account, tid, capsys, apply=False):
    argv = ["--db", str(db), "--account", account, "--ticket-id", tid,
            "--platforms", str(platforms), "--now", NOW] + (["--apply"] if apply else [])
    rc = pte.main(argv)
    return rc, json.loads(capsys.readouterr().out)


def _status(db, tid):
    return sqlite3.connect(db).execute("SELECT status FROM prop_tickets WHERE ticket_id=?", (tid,)).fetchone()[0]


@pytest.mark.parametrize("tid,was", [("prop-manual-aaa111", "expiry_prompted"),
                                     ("prop-manual-bbb222", "emitted")])
def test_allowed_transition_dry_run_writes_nothing_then_apply_writes_once(db, platforms, capsys, tid, was):
    rc, out = _run(db, platforms, "velotrade_1", tid, capsys)
    assert rc == 0 and out["ok"] and out["mode"] == "dry_run"
    assert out["before"]["status"] == was and out["after"]["status"] == "expired"
    assert _status(db, tid) == was
    rc, out = _run(db, platforms, "velotrade_1", tid, capsys, apply=True)
    assert rc == 0 and out["after"]["status"] == "expired" and Path(out["backup"]).is_file()
    assert _status(db, tid) == "expired"
    # re-run is a clean refusal, not a second write
    rc, out = _run(db, platforms, "velotrade_1", tid, capsys, apply=True)
    assert rc == 3 and "not expirable" in out["why"]


@pytest.mark.parametrize("account,tid,needle", [
    ("velotrade_1", "prop-manual-ccc333", "still valid"),
    ("velotrade_1", "prop-manual-ddd444", "not expirable"),
    ("breakout_1", "prop-manual-eee555", "not a REST-executed account"),
    ("breakout_2", "prop-manual-eee555", "not a REST-executed account"),
    ("velotrade_1", "prop-manual-eee555", "belongs to breakout_1"),
    ("velotrade_1", "prop-manual-fff666", "prop_fills"),
    ("velotrade_1", "prop-manual-ggg777", "could not look"),
    ("velotrade_1", "prop-manual-zzz999", "not found"),
])
def test_refusals_write_nothing(db, platforms, capsys, account, tid, needle):
    before = sqlite3.connect(db).execute("SELECT ticket_id, status FROM prop_tickets ORDER BY 1").fetchall()
    rc, out = _run(db, platforms, account, tid, capsys, apply=True)
    assert rc == 3 and not out["ok"] and needle in out["why"]
    assert sqlite3.connect(db).execute("SELECT ticket_id, status FROM prop_tickets ORDER BY 1").fetchall() == before


def test_unreadable_platform_file_refuses_every_account(db, tmp_path, capsys):
    rc, out = _run(db, tmp_path / "missing.yaml", "velotrade_1", "prop-manual-aaa111", capsys, apply=True)
    assert rc == 3 and "not a REST-executed account" in out["why"]
    assert _status(db, "prop-manual-aaa111") == "expiry_prompted"


def test_real_config_counts_velotrade_1_as_rest_and_breakout_as_not():
    rest = pte.rest_accounts()
    assert "velotrade_1" in rest and "breakout_1" not in rest and "breakout_2" not in rest
