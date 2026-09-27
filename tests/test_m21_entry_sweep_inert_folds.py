"""The M21 entry sweep's walk-forward must not count INERT folds as wins.

WHY THIS FILE EXISTS
--------------------
FIX-CA-26 / `CA-B04-m21-entry-sweep-inert-fold-inflation` (code audit
2026-09-27). `m21_entry_sweep.py` graded each yearly fold inline with
``fc.net >= fb.net and fc.dd <= fb.dd``, so a fold in which the entry filter
changed NOTHING (cell book == base book) satisfied the gate by construction and
was tallied as a win. That is the same class as
`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`, fixed in the sibling
`m20_fleet_exit_sweep.walkforward` but never in this file.

It matters because the printed ``wins/usable`` is cited verbatim in
`config/strategies.yaml` as the evidence for three real-money entry declares
(`trend_donchian_xrp_4h` skip_h0 5/6 and vol_hi90 4/6, `ada_pullback_2h`
vol_lo10 4/6). A 4/6 pass sits exactly on the ≥2/3 threshold: one inert fold
among its wins makes it 3/6, a wf_fail.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))

import m21_entry_sweep as m21  # noqa: E402


def _r(net: float, dd: float) -> dict:
    return {"net_total_r": net, "max_drawdown_r": dd}


def _six_folds_two_inert_two_real_wins() -> list:
    """(fold, base, lever) — 2 inert ties, 2 real wins, 2 real losses."""
    return [
        ("2021", _r(5.0, 3.0), _r(5.0, 3.0)),   # inert (cell == base)
        ("2022", _r(-2.0, 4.0), _r(-2.0, 4.0)),  # inert (cell == base)
        ("2023", _r(1.0, 2.0), _r(2.5, 1.5)),   # real win
        ("2024", _r(3.0, 2.0), _r(3.4, 2.0)),   # real win (dd tie, net up)
        ("2025", _r(4.0, 2.0), _r(3.0, 2.5)),   # loss
        ("2026", _r(1.0, 1.0), _r(0.5, 1.0)),   # loss
    ]


def test_raw_count_is_preserved_and_passes():
    """POSITIVE CONTROL: the recorded (raw) figure still reads 4/6 PASS, so the
    effective assertion below cannot hold for a reason unrelated to inertness."""
    wf = m21.grade_walkforward(_six_folds_two_inert_two_real_wins())
    assert wf["walkforward"] == "4/6"
    assert wf["verdict"] == "PASS"


def test_inert_folds_are_neither_wins_nor_usable_in_the_effective_grade():
    wf = m21.grade_walkforward(_six_folds_two_inert_two_real_wins())
    assert wf["inert"] == 2
    assert wf["walkforward_effective"] == "2/4"
    # 4 effective-usable is exactly the >=4 floor; 2/4 misses 2/3.
    assert wf["verdict_effective"] == "wf_fail"


def test_a_clean_pass_is_unaffected():
    folds = [(str(y), _r(1.0, 2.0), _r(2.0, 1.0)) for y in range(2021, 2027)]
    wf = m21.grade_walkforward(folds)
    assert wf["walkforward"] == "6/6" and wf["verdict"] == "PASS"
    assert wf["walkforward_effective"] == "6/6"
    assert wf["verdict_effective"] == "PASS"
    assert wf["inert"] == 0


def test_error_folds_are_unusable_on_both_counts():
    folds = _six_folds_two_inert_two_real_wins()
    folds[4] = ("2025", {"error": "boom"}, _r(1.0, 1.0))
    wf = m21.grade_walkforward(folds)
    assert wf["walkforward"] == "4/5"
    assert wf["walkforward_effective"] == "2/3"
    # 3 effective-usable is below the >=4 floor.
    assert wf["verdict_effective"] == "wf_fail"


def test_inert_needs_both_deltas_at_zero():
    """A fold that moved drawdown but not net R DID exercise the filter."""
    folds = [(str(y), _r(1.0, 3.0), _r(1.0, 2.0)) for y in range(2021, 2027)]
    wf = m21.grade_walkforward(folds)
    assert wf["inert"] == 0
    assert wf["walkforward_effective"] == "6/6"


def test_sweep_uses_the_shared_inert_predicate_not_a_private_one():
    """Detector: the grader imports `m20_wf_effective.is_inert`, so the
    definition of inert cannot drift between the WF graders."""
    src = (REPO / "scripts" / "research" / "m21_entry_sweep.py").read_text()
    tree = ast.parse(src)
    imported = {
        (n.module, a.name)
        for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        for a in n.names
    }
    assert ("m20_wf_effective", "is_inert") in imported
    # ...and main() no longer carries its own inline fold predicate.
    assert "float(fc[\"net_total_r\"]) >= float(fb[\"net_total_r\"])" not in src
