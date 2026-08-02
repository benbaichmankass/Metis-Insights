"""Tests for scripts/check_timestamp_comparisons.py (the created_at/closed_at
raw-comparison guard, BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED).

The guard must FAIL-CLOSED on a raw ordering comparison and stay quiet on a
wrapped one or on English prose that merely mentions the column names.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD_PATH = os.path.join(_ROOT, "scripts", "check_timestamp_comparisons.py")

_spec = importlib.util.spec_from_file_location("check_timestamp_comparisons", _MOD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_flags_raw_created_at_comparison():
    assert guard._scan_line('    q = "WHERE created_at >= ?"')
    assert guard._scan_line("      AND op.created_at <  ?")
    assert guard._scan_line("WHERE closed_at > '2026-07-30T00:00'")
    assert guard._scan_line("WHERE created_at BETWEEN datetime(?) AND datetime(?)")


def test_ignores_wrapped_comparison():
    # Wrapped column -> the `)` sits between the column and the operator.
    assert not guard._scan_line("      AND datetime(created_at) >= datetime(?)")
    assert not guard._scan_line("closed_at_norm_sql('closed_at') >= datetime(?)")
    assert not guard._scan_line("ORDER BY datetime(created_at) DESC")


def test_ignores_prose_and_assignments():
    # Bareword RHS (docstring prose) and print-string arrows are not SQL.
    assert not guard._scan_line("    ``created_at >= since`` and ``is_backtest = 0``.")
    assert not guard._scan_line("    # created_at <= bybit.createdTime + 2s (open before close)")
    assert not guard._scan_line('    print(f"<= {N}m before closed_at  <-- estimator")')
    # Assignments / column defs are not ordering comparisons.
    assert not guard._scan_line("SET closed_at = ?, notes = ?")
    assert not guard._scan_line("created_at TEXT DEFAULT CURRENT_TIMESTAMP")


def test_ok_marker_suppresses():
    assert not guard._scan_line(
        "WHERE created_at >= ?  # ts-compare-ok: both sides proven ISO-T at call site"
    )
    # ...but a bare marker with no reason still trips.
    assert guard._scan_line("WHERE created_at >= ?  # ts-compare-ok:")


def test_whole_tree_is_clean():
    # The tree was made clean when the guard landed; this is the standing audit.
    hits = guard._scan_all()
    assert hits == [], f"raw created_at/closed_at comparisons present: {hits}"


def test_diff_mode_flags_added_line(tmp_path):
    diff = tmp_path / "pr.diff"
    diff.write_text(
        "+++ b/scripts/ml/_probe.py\n"
        "@@ -0,0 +1,1 @@\n"
        '+    q = "SELECT * FROM trades WHERE created_at >= ?"\n'
    )
    assert guard._scan_diff(str(diff))


def test_diff_mode_clean_on_wrapped(tmp_path):
    diff = tmp_path / "pr.diff"
    diff.write_text(
        "+++ b/scripts/ml/_probe.py\n"
        "@@ -0,0 +1,1 @@\n"
        '+    q = "WHERE datetime(created_at) >= datetime(?)"\n'
    )
    assert guard._scan_diff(str(diff)) == []
