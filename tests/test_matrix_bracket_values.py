"""`matrix-bracket-values` — the staleness detector for the ONE matrix column
its sibling `matrix-config-agreement` structurally cannot cover.

That guard grades whether a lever is ARMED, over exactly four levers.
`bracket_geometry` is not one of them, and correctly so: its arming test is key
PRESENCE, and every leg always declares `tp_r`/`atr_stop_mult`, so folding the
column in would demand `shipped` everywhere. The cost was a column with no
detector at all — #10419 declared validated geometry on 8 LIVE legs, real money,
and the matrix carried all 8 as `passed_unshipped` for the rest of the day while
`matrix-config-agreement` stayed green, because arming was never the question.

The question that IS falsifiable here is the VALUE: a cell id encodes its own
geometry (`tp3_sm2` => `tp_r` 3.0 AND `atr_stop_mult` 2.0), so a cell marked
`shipped` is a checkable claim about the declare.

THREE THINGS THIS FILE PINS, each of which a looser guard would get wrong:

  1. **Only the axes the cell NAMES are asserted.** A `sm2` cell says nothing
     about `tp_r`, so policing `tp_r` there would manufacture findings on legs
     whose record is correct.
  2. **`unreadable` is a FAILURE, not a pass.** A `shipped` cell whose ref
     carries no parseable cell id cannot be checked — and "we could not look"
     must never be recorded as "we looked and it agreed". Softening this is the
     cheapest way to make the guard vacuous while leaving it green.
  3. **A `to*` component can never be `shipped`.** No live trend/pullback/squeeze
     unit reads `timeout_bars`, so a shipped claim on that axis is undeliverable
     by construction (`BL-20260829-HARNESS-FORCE-CLOSES-TREND-PULLBACK-TRADES-ON-BAR-COUNT-AND-LIVE-NEVER-DOES`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_matrix_bracket_values.py"

sys.path.insert(0, str(REPO / "scripts" / "ci"))
import check_matrix_bracket_values as guard  # noqa: E402


def _matrix(leg: str, ref: str, status: str = "shipped") -> dict:
    return {"rows": [{"strategy": leg,
                      "bracket_geometry": {"status": status, "ref": ref}}]}


def test_guard_self_test_passes_and_has_not_shrunk() -> None:
    p = subprocess.run([sys.executable, str(GUARD), "--self-test"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    # A self-test that quietly lost cases would still exit 0, so the COUNT is
    # part of the contract it prints.
    assert "8 cases" in p.stdout, p.stdout


def test_guard_runs_against_the_real_tree_without_crashing() -> None:
    """Exit 1 (findings) is fine; a traceback is not.

    This is what catches a renamed matrix field or a `strategies.yaml` shape
    change — the drift that would make the guard useless on the day it lands.
    """
    p = subprocess.run([sys.executable, str(GUARD)], capture_output=True, text=True)
    assert p.returncode in (0, 1), f"crashed (rc={p.returncode}):\n{p.stdout}\n{p.stderr}"
    assert "Traceback" not in p.stderr, p.stderr
    assert "shipped bracket_geometry cell(s) checked" in p.stdout, (
        "the guard must state its denominator")


def test_only_the_named_axes_are_asserted() -> None:
    strat = {"leg": {"tp_r": 50.0, "atr_stop_mult": 2.0}}
    assert guard.disagreements(_matrix("leg", "cell `sm2`"), strat) == []
    # ...and the same leg IS caught once the cell names tp_r.
    d = guard.disagreements(_matrix("leg", "cell `tp3_sm2`"), strat)
    assert [x["key"] for x in d] == ["tp_r"], d


def test_an_unreadable_ref_is_a_finding_not_a_pass() -> None:
    d = guard.disagreements(_matrix("leg", "SHIPPED 2026-08-29, see the PR"),
                            {"leg": {"tp_r": 3.0, "atr_stop_mult": 2.0}})
    assert len(d) == 1 and d[0]["kind"] == "unreadable", d


def test_a_timeout_axis_can_never_be_shipped() -> None:
    d = guard.disagreements(_matrix("leg", "cell `tp3_sm2_to24`"),
                            {"leg": {"tp_r": 3.0, "atr_stop_mult": 2.0}})
    assert len(d) == 1 and d[0]["kind"] == "undeliverable_axis", d


def test_guard_is_registered_in_run_guards() -> None:
    """A guard nothing runs is not a guard."""
    src = (REPO / "scripts" / "ci" / "run_guards.py").read_text(encoding="utf-8")
    assert '"name": "matrix-bracket-values"' in src
    assert "check_matrix_bracket_values.py" in src
