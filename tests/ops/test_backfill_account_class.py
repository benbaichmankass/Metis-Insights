"""Tests for scripts/ops/backfill_account_class.py.

Verifies the backfill CORRECTS mis-stamped paper rows (the ib_paper
is_demo=0 bug) and leaves real-money rows alone, syncing is_demo to the
resolved account_class.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "backfill_account_class.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_account_class", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(path: Path) -> None:
    """A minimal trades table carrying the account_class column."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                is_demo INTEGER DEFAULT 0,
                account_class TEXT
            )
            """
        )
        # ib_paper rows mis-stamped as real money (is_demo=0, NULL class) —
        # the bug the backfill corrects.
        conn.executemany(
            "INSERT INTO trades (account_id, is_demo, account_class) VALUES (?, ?, ?)",
            [
                ("ib_paper", 0, None),
                ("ib_paper", 0, None),
                ("bybit_2", 0, None),    # real money, stays real
                ("bybit_1", 1, None),    # demo paper, already is_demo=1
                ("alpaca_paper", 0, None),  # paper that lost its demo stamp
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _make_accounts_yaml(path: Path) -> Path:
    import yaml

    path.write_text(
        yaml.safe_dump({
            "accounts": {
                "bybit_1": {"exchange": "bybit", "demo": True, "account_class": "paper"},
                "bybit_2": {"exchange": "bybit", "account_class": "real_money"},
                "ib_paper": {"exchange": "interactive_brokers", "account_class": "paper"},
                "alpaca_paper": {"exchange": "alpaca", "account_class": "paper"},
            }
        }),
        encoding="utf-8",
    )
    return path


def _rows(db: Path):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT account_id, is_demo, account_class FROM trades ORDER BY id"
        )]
    finally:
        conn.close()


def test_dry_run_does_not_write(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    _make_db(db)
    yaml_path = _make_accounts_yaml(tmp_path / "accounts.yaml")
    class_map = mod.build_class_map(yaml_path)

    summary = mod.plan_and_apply(db, class_map, apply=False)
    # ib_paper: 2 rows, both would flip class + is_demo.
    assert summary["ib_paper"]["account_class_changes"] == 2
    assert summary["ib_paper"]["is_demo_changes"] == 2
    # Nothing actually written.
    for r in _rows(db):
        assert r["account_class"] is None


def test_apply_corrects_ib_paper_and_keeps_real_money(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    _make_db(db)
    yaml_path = _make_accounts_yaml(tmp_path / "accounts.yaml")
    class_map = mod.build_class_map(yaml_path)

    mod.plan_and_apply(db, class_map, apply=True)
    rows = _rows(db)

    by_account: dict = {}
    for r in rows:
        by_account.setdefault(r["account_id"], []).append(r)

    # ib_paper rows corrected: paper + is_demo=1.
    for r in by_account["ib_paper"]:
        assert r["account_class"] == "paper"
        assert r["is_demo"] == 1
    # bybit_2 stays real money, is_demo 0.
    for r in by_account["bybit_2"]:
        assert r["account_class"] == "real_money"
        assert r["is_demo"] == 0
    # bybit_1 demo → paper, is_demo stays 1.
    for r in by_account["bybit_1"]:
        assert r["account_class"] == "paper"
        assert r["is_demo"] == 1
    # alpaca_paper corrected to paper + is_demo 1.
    for r in by_account["alpaca_paper"]:
        assert r["account_class"] == "paper"
        assert r["is_demo"] == 1


def test_absent_account_defaults_real_money(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "account_id TEXT, is_demo INTEGER DEFAULT 0, account_class TEXT)"
    )
    conn.execute(
        "INSERT INTO trades (account_id, is_demo, account_class) VALUES "
        "('removed_legacy_acct', 1, NULL)"
    )
    conn.commit()
    conn.close()
    yaml_path = _make_accounts_yaml(tmp_path / "accounts.yaml")
    class_map = mod.build_class_map(yaml_path)

    mod.plan_and_apply(db, class_map, apply=True)
    rows = _rows(db)
    # Account not in YAML and not in the historical-paper override set →
    # real_money, is_demo cleared to 0.
    assert rows[0]["account_class"] == "real_money"
    assert rows[0]["is_demo"] == 0
