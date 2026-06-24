"""Tests for the canonical clean-trades filters (src.web.api._clean_trades).

Covers (1) the paper/real/reconciler predicates against a fixture DB, (2) the
account_class wire fallback, and (3) a DRIFT GUARD asserting the raw paper/prop
predicate literal lives ONLY in the helper — so a future router can't quietly
re-introduce a divergent copy (the bug class this module was created to kill).
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from src.web.api._clean_trades import (
    account_class_wire,
    exclude_reconciler_predicate,
    not_paper_predicate,
    paper_predicate,
)


# ---------------------------------------------------------------- predicates
def _fixture_db(tmp_path) -> pathlib.Path:
    """A trades table seeded with one row per funding-class / artifact case."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, strategy_name TEXT, "
        "pnl REAL, status TEXT, is_backtest INTEGER, is_demo INTEGER, "
        "account_class TEXT)"
    )
    rows = [
        # strategy_name, pnl, status, is_backtest, is_demo, account_class
        ("trend_donchian", 5.0, "closed", 0, 0, "real_money"),   # real win  ✓
        ("ict_scalp_5m", -2.0, "closed", 0, 0, "real_money"),    # real loss ✓
        ("eth_pullback_2h", 9.0, "closed", 0, 1, "paper"),       # paper      ✗
        ("trend_donchian_sol", 3.0, "closed", 0, 0, "prop"),     # prop       ✗
        ("orphan_adopt", -50.0, "closed", 0, 0, "real_money"),   # reconciler ✗
        ("vwap", None, "closed", 0, 0, "real_money"),            # null pnl   ✗
        ("turtle_soup", 1.0, "closed", 1, 0, "real_money"),      # backtest   ✗
        # legacy un-backfilled paper row (account_class NULL, is_demo=1)       ✗
        ("legacy_paper", 4.0, "closed", 0, 1, None),
    ]
    conn.executemany(
        "INSERT INTO trades (strategy_name,pnl,status,is_backtest,is_demo,"
        "account_class) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def _clean_real_query() -> str:
    return (
        "SELECT strategy_name FROM trades "
        "WHERE status='closed' AND COALESCE(is_backtest,0)=0 AND pnl IS NOT NULL"
        + not_paper_predicate("")
        + exclude_reconciler_predicate("")
    )


def test_clean_real_money_excludes_paper_prop_orphan_null_backtest(tmp_path):
    db = _fixture_db(tmp_path)
    conn = sqlite3.connect(str(db))
    got = {r[0] for r in conn.execute(_clean_real_query()).fetchall()}
    conn.close()
    # Only the two genuine real-money resolved strategy trades survive.
    assert got == {"trend_donchian", "ict_scalp_5m"}
    assert "orphan_adopt" not in got        # reconciler artifact dropped
    assert "vwap" not in got                # NULL-pnl dropped
    assert "legacy_paper" not in got        # is_demo fallback → paper, dropped


def test_paper_predicate_selects_only_paper_not_prop(tmp_path):
    db = _fixture_db(tmp_path)
    conn = sqlite3.connect(str(db))
    sql = (
        "SELECT strategy_name FROM trades WHERE status='closed' "
        "AND COALESCE(is_backtest,0)=0 AND pnl IS NOT NULL" + paper_predicate("")
    )
    got = {r[0] for r in conn.execute(sql).fetchall()}
    conn.close()
    # paper rows: explicit account_class='paper' + legacy is_demo fallback.
    # prop is NOT paper (third, isolated class).
    assert got == {"eth_pullback_2h", "legacy_paper"}
    assert "trend_donchian_sol" not in got   # prop excluded from paper too


def test_prefixed_predicate_matches_bare(tmp_path):
    # The ``t.`` prefix variant is the same logic for a joined query.
    assert not_paper_predicate("t.") == not_paper_predicate("").replace(
        "account_class", "t.account_class"
    ).replace("is_demo", "t.is_demo")


# ----------------------------------------------------------- account_class_wire
@pytest.mark.parametrize(
    "ac,demo,expected",
    [
        ("real_money", 0, "real_money"),
        ("paper", 0, "paper"),
        ("  Real_Money  ", 0, "real_money"),  # trimmed + lowercased
        (None, 1, "paper"),                    # NULL → is_demo fallback
        (None, 0, "real_money"),               # NULL + not demo → real
        ("", 1, "paper"),                      # blank → fallback
    ],
)
def test_account_class_wire(ac, demo, expected):
    assert account_class_wire(ac, demo) == expected


# ------------------------------------------------------------- drift guard
def test_paper_prop_predicate_literal_only_in_helper():
    """The raw ``IN ('paper','prop')`` SQL literal must appear ONLY in
    _clean_trades.py. Any other src/web/api/*.py carrying its own copy is a
    re-introduced duplicate — the exact drift this module exists to prevent."""
    api_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "web" / "api"
    needle = "IN ('paper','prop')"
    offenders = [
        str(p.relative_to(api_dir.parents[3]))
        for p in api_dir.rglob("*.py")
        if p.name != "_clean_trades.py" and needle in p.read_text()
    ]
    assert not offenders, (
        "raw paper/prop predicate literal re-introduced outside the canonical "
        f"helper: {offenders}. Import from src.web.api._clean_trades instead."
    )
