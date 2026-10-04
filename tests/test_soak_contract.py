"""SOAK CONTRACT (2026-10-04): every soak carries a definition of done, and its
state is computed live from the journal — never from a stale or wrong DB."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_soak_contracts as guard  # noqa: E402
import soak_state as ss  # noqa: E402


def _db(tmp_path: Path, newest_pkg: str, closes: int) -> str:
    p = tmp_path / "j.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE trades (account_id TEXT, strategy_name TEXT, status TEXT, "
              "is_backtest INTEGER, closed_at TEXT)")
    c.execute("CREATE TABLE order_packages (strategy_name TEXT, created_at TEXT)")
    for _ in range(closes):
        c.execute("INSERT INTO trades VALUES ('bybit_1','leg','closed',0,'2026-10-02T00:00:00')")
    c.execute("INSERT INTO order_packages VALUES ('leg', ?)", (newest_pkg,))
    c.commit()
    c.close()
    return str(p)


def _contracts(tmp_path: Path) -> Path:
    c = ss.make_contract(kind="stage1", leg="leg", account="bybit_1", started="2026-10-01")
    c.update(design="ok", end_date="2026-10-10", expected_per_week=7.0, n_needed=10)
    p = tmp_path / "SOAKS.json"
    p.write_text(json.dumps({"soaks": [c]}))
    return p


def test_stale_journal_is_unknown_never_dead(tmp_path):
    db = _db(tmp_path, "2026-01-01T00:00:00", closes=0)
    rows = ss.soak_states(today=date(2026, 10, 4), db_path=db, contracts_path=_contracts(tmp_path))
    assert rows[0]["state"] == "unknown"
    assert "not live" in rows[0]["reason"]


def test_fresh_journal_counts_closes(tmp_path):
    db = _db(tmp_path, "2026-10-04T00:00:00", closes=10)
    rows = ss.soak_states(today=date(2026, 10, 4), db_path=db, contracts_path=_contracts(tmp_path))
    assert rows[0]["state"] == "ready" and rows[0]["progress"] == "10/10 closes"


def test_every_row_has_the_agreed_interface_keys(tmp_path):
    rows = ss.soak_states(today=date(2026, 10, 4), db_path=str(tmp_path / "absent.db"),
                          contracts_path=_contracts(tmp_path))
    for k in ("id", "leg", "account", "state", "started", "end_date", "progress", "reason"):
        assert k in rows[0]
    assert rows[0]["state"] == "unknown"


def test_contract_longer_than_14_days_must_say_so():
    c = ss.make_contract(kind="stage1", leg="no_such_leg_xyz", account="bybit_1", started="2026-10-01")
    assert c["design"] == "no_backtest_rate" and c["recommended_fix"]


def test_committed_contracts_cover_the_current_soak_population():
    import yaml

    acc = yaml.safe_load(open(REPO / "config/accounts.yaml"))["accounts"]
    strat = yaml.safe_load(open(REPO / "config/strategies.yaml"))["strategies"]
    assert guard.tree_findings(acc, strat, ss.load_contracts()) == []


def test_guard_self_tests():
    assert guard._self_test() == 0
    assert ss._self_test() == 0
