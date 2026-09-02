"""The E0 live arm must be scoped to the round's OWN legs.

`BL-20260813-EXIT-HEAD-LIVE-ARM-DROPPED-ON-NO-CANDLES`, defect 1. The live arm
loaded EVERY strategy-attributed closed trade in the journal and
`build_exit_head_dataset.family_of()` then buckets on the strategy NAME — so a
scalp round asking for `ict_scalp_xrp_15m` also inhaled `xrp_pullback_2h` and
manufactured `donchian` / `pullback` families it never asked for. MEASURED
2026-08-13 (trainer relays #8854/#8855), `runtime_logs/m20_exit_head/scalp_15m/
build_report.json`: `families: donchian {live: 3}, pullback {live: 6}` beside
`ict_scalp_xrp_15m {harness: 353}` — the graded leg's own live count was ZERO
while two families the round never named carried live rows, and those
0-harness families then died in training on
`ValueError: Expected 2D array, got 1D array instead: array=[]`.

These tests run against a REAL sqlite journal built here, not a stub for the
loader — the defect was in what the query population is, so a mocked loader
would pass forever while the population drifted underneath it.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SCRIPT = REPO / "scripts" / "ml" / "build_exit_head_dataset.py"

spec = importlib.util.spec_from_file_location("build_exit_head_dataset", _SCRIPT)
behd = importlib.util.module_from_spec(spec)
sys.modules["build_exit_head_dataset"] = behd
spec.loader.exec_module(behd)

_SCALP = "ict_scalp_xrp_15m"
_SIBLING = "xrp_pullback_2h"


def _journal(path: Path) -> Path:
    """Two legs on the SAME symbol — the exact shape that caused the pull."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, timestamp TEXT, "
        "closed_at TEXT, symbol TEXT, direction TEXT, entry_price REAL, "
        "stop_loss REAL, position_size REAL, pnl REAL, strategy_name TEXT, "
        "status TEXT, is_backtest INTEGER, setup_type TEXT, notes TEXT, "
        "reconcile_status TEXT)")
    rows = [(_SCALP, 1), (_SCALP, 2), (_SIBLING, 3), (_SIBLING, 4), (_SIBLING, 5)]
    for name, i in rows:
        con.execute(
            "INSERT INTO trades (id, timestamp, closed_at, symbol, direction, "
            "entry_price, stop_loss, position_size, pnl, strategy_name, "
            "status, is_backtest) VALUES (?,?,?,?,?,?,?,?,?,?,'closed',0)",
            (i, "1700000000", "1700003600", "XRPUSDT", "long",
             1.0, 0.9, 100.0, 5.0, name))
    con.commit()
    con.close()
    return path


def _load(tmp_path, legs):
    db = _journal(tmp_path / "j.db")
    report: dict = {}
    out = behd.load_live_trades(db, REPO / "config" / "instruments.yaml",
                                report, legs=legs)
    return out, report


def test_unscoped_load_pulls_the_same_symbol_sibling(tmp_path):
    """The POSITIVE CONTROL for the defect: without scoping, it still pulls.

    Without this the scoped test below proves nothing — a loader returning two
    rows for any reason would pass it.
    """
    out, report = _load(tmp_path, legs=None)
    assert {t["strategy"] for t in out} == {_SCALP, _SIBLING}
    assert report["legs_filter_state"] == "not_requested"


def test_scoping_keeps_only_the_named_legs(tmp_path):
    out, report = _load(tmp_path, legs=[_SCALP])
    assert {t["strategy"] for t in out} == {_SCALP}
    assert len(out) == 2
    assert report["legs_filter_state"] == "applied"
    assert report["rows_after_leg_filter"] == 2


def test_the_dropped_siblings_are_NAMED_not_silently_discarded(tmp_path):
    """A silent drop would fix the symptom and hide the condition.

    The pull is what a reader needs to see to understand a past round's
    `build_report.json`, so the excluded names and their counts are reported.
    """
    _out, report = _load(tmp_path, legs=[_SCALP])
    assert report["legs_dropped"] == {_SIBLING: 3}


def test_rows_matching_filters_still_means_what_it_meant(tmp_path):
    """Pre-existing key, unchanged population.

    `rows_matching_filters` is the SQL-filter count and is compared across
    rounds; silently redefining it to the post-leg-filter count would hand a
    reader a different population under the same key.
    """
    _out, report = _load(tmp_path, legs=[_SCALP])
    assert report["rows_matching_filters"] == 5
    assert report["rows_after_leg_filter"] == 2


def test_no_match_is_not_the_same_state_as_no_filter(tmp_path):
    """`we did not scope` and `we scoped and the journal holds nothing` are
    opposite statements. Collapsing them is how an empty live arm gets blamed
    on data accrual again — which is exactly what happened on 2026-08-12."""
    out, report = _load(tmp_path, legs=["a_leg_that_never_traded"])
    assert out == []
    assert report["legs_filter_state"] == "applied_no_match"
    assert report["legs_filter"] == ["a_leg_that_never_traded"]


def test_the_round_driver_forwards_legs_whenever_it_passes_db():
    """A flag the driver never sends is a flag that does not exist."""
    src = (REPO / "scripts" / "research" / "m20_exit_head_round.py").read_text()
    assert 'build_cmd += ["--db", a.db, "--legs", a.legs]' in src, (
        "the round driver passes --db without scoping the live arm to --legs")
