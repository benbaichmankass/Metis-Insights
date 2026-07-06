"""Unit tests for score_order_packages.py --emit-delta-only.

Covers the 2026-07-06 delta-emit mode: a VM-side, side-effect-free way to
grade closed order packages and print ONLY the ungraded delta as NDJSON to
stdout — the fix for the diag-relay's ~55KB comment-size wall that a full
~650KB trades-table pull routinely blew past.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "scripts", "ops", "score_order_packages.py")

_spec = importlib.util.spec_from_file_location("score_order_packages", _SCRIPT)
sop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sop)


def _make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE order_packages (
            order_package_id TEXT, strategy_name TEXT, symbol TEXT,
            direction TEXT, status TEXT, close_reason TEXT,
            linked_trade_id INTEGER, signal_logic TEXT,
            entry REAL, sl REAL, tp REAL, created_at TEXT)"""
    )
    con.execute(
        """CREATE TABLE trades (
            id INTEGER, pnl REAL, exit_price REAL, exit_reason TEXT,
            position_size REAL)"""
    )
    for r in rows:
        con.execute(
            "INSERT INTO order_packages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r
        )
    con.commit()
    con.close()
    return con


def _pkg_row(pid, status, linked_trade_id, created_at, dev=2.6):
    return (
        pid, "vwap", "BTCUSDT", "long", status, "sl_hit", linked_trade_id,
        json.dumps({"deviation_std": dev}), 100.0, 98.0, 105.0, created_at,
    )


def _add_trade(db_path, trade_id, pnl, exit_price, exit_reason):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?)",
        (trade_id, pnl, exit_price, exit_reason, 1.0),
    )
    con.commit()
    con.close()


def _run(*args):
    return subprocess.run(
        [sys.executable, _SCRIPT, *args], capture_output=True, text=True
    )


def test_emits_only_missing_closed_rows(tmp_path):
    db = str(tmp_path / "j.db")
    _make_db(db, [
        _pkg_row("pkg1", "closed", 1, "2026-07-01T00:00:00Z"),
        _pkg_row("pkg2", "closed", 2, "2026-07-02T00:00:00Z"),
        _pkg_row("pkg3", "open", None, "2026-07-03T00:00:00Z"),
    ])
    _add_trade(db, 1, 10.0, 106, "tp_hit")
    _add_trade(db, 2, -2.0, 97, "sl_hit")

    scores = tmp_path / "scores.jsonl"
    scores.write_text(json.dumps({"order_package_id": "pkg1", "decision_grade": "B"}) + "\n")
    original_scores_text = scores.read_text()

    out = _run(db, str(scores), "--emit-delta-only")
    assert out.returncode == 0, out.stderr

    lines = [json.loads(ln) for ln in out.stdout.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if "_delta_summary" not in ln]
    summary = [ln for ln in lines if "_delta_summary" in ln][0]

    # pkg1 already graded -> skipped; pkg3 open -> skipped (closed_only default)
    assert {r["order_package_id"] for r in data_lines} == {"pkg2"}
    assert summary["emitted"] == 1
    assert summary["truncated"] is False
    assert summary["more_available"] == 0

    # The scores file must be byte-for-byte untouched (read-only contract).
    assert scores.read_text() == original_scores_text


def test_include_open_widens_scope(tmp_path):
    db = str(tmp_path / "j.db")
    _make_db(db, [
        _pkg_row("pkg1", "open", None, "2026-07-01T00:00:00Z"),
    ])
    scores = tmp_path / "scores.jsonl"
    scores.write_text("")

    out_closed_only = _run(db, str(scores), "--emit-delta-only")
    lines = [json.loads(ln) for ln in out_closed_only.stdout.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if "_delta_summary" not in ln]
    assert data_lines == []  # open package excluded by default

    out_include_open = _run(db, str(scores), "--emit-delta-only", "--include-open")
    lines2 = [json.loads(ln) for ln in out_include_open.stdout.splitlines() if ln.strip()]
    data_lines2 = [ln for ln in lines2 if "_delta_summary" not in ln]
    assert {r["order_package_id"] for r in data_lines2} == {"pkg1"}


def test_limit_truncation_is_never_silent(tmp_path):
    db = str(tmp_path / "j.db")
    rows = []
    for i in range(5):
        rows.append(_pkg_row(f"pkg{i}", "closed", i, f"2026-07-0{i + 1}T00:00:00Z"))
    _make_db(db, rows)
    for i in range(5):
        _add_trade(db, i, -1.0, 97, "sl_hit")

    scores = tmp_path / "scores.jsonl"
    scores.write_text("")

    out = _run(db, str(scores), "--emit-delta-only", "--limit", "2")
    assert out.returncode == 0, out.stderr
    lines = [json.loads(ln) for ln in out.stdout.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if "_delta_summary" not in ln]
    summary = [ln for ln in lines if "_delta_summary" in ln][0]

    assert len(data_lines) == 2
    assert summary["emitted"] == 2
    assert summary["truncated"] is True
    assert summary["more_available"] == 3
    assert summary["limit"] == 2
    # The truncation marker must be present -- never a silent drop.
    assert "TRUNCATED" in out.stderr


def test_since_filters_by_created_at(tmp_path):
    db = str(tmp_path / "j.db")
    _make_db(db, [
        _pkg_row("old", "closed", 1, "2026-06-01T00:00:00Z"),
        _pkg_row("new", "closed", 2, "2026-07-05T00:00:00Z"),
    ])
    _add_trade(db, 1, -1.0, 97, "sl_hit")
    _add_trade(db, 2, -1.0, 97, "sl_hit")
    scores = tmp_path / "scores.jsonl"
    scores.write_text("")

    out = _run(db, str(scores), "--emit-delta-only", "--since", "2026-07-01T00:00:00Z")
    lines = [json.loads(ln) for ln in out.stdout.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if "_delta_summary" not in ln]
    assert {r["order_package_id"] for r in data_lines} == {"new"}


def test_rewrite_and_append_modes_unaffected(tmp_path):
    """The pre-existing --append / default modes still write to disk as before."""
    db = str(tmp_path / "j.db")
    _make_db(db, [_pkg_row("pkg1", "closed", 1, "2026-07-01T00:00:00Z")])
    _add_trade(db, 1, -1.0, 97, "sl_hit")

    out_path = tmp_path / "out.jsonl"
    out = _run(db, str(out_path))
    assert out.returncode == 0, out.stderr
    assert out_path.exists()
    lines = [json.loads(ln) for ln in out_path.read_text().splitlines() if ln.strip()]
    assert any(ln.get("order_package_id") == "pkg1" for ln in lines)
