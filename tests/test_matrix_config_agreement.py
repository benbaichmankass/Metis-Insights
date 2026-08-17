"""`matrix-config-agreement` — the guard, and the reason its scope is what it is.

It asserts one thing in both directions: the set of levers `config/strategies.yaml`
ARMS and the set the coverage matrix acknowledges as armed must be the same set.
Config is the field the trader loads; the matrix is prose about it, so a
disagreement is always a stale RECORD — never a reason to touch a declare.

Registered in `run_guards.py` on 2026-08-14, in the SAME change as the
reconciliation it demanded (operator decision (j)). That ordering was
deliberate: a guard whose first CI run is red teaches everyone to skip it.

TWO THINGS THIS FILE PINS, both of which were wrong in a first draft of the
guard and found by USING it rather than reading it:

  1. `shipped_gate_failed` counts as ARMED. The legend defines it as *"LIVE in
     config, but a LATER re-sweep failed its gate and the operator chose to
     HOLD"* — so it asserts the lever runs, exactly as `shipped` does; they
     differ on whether the VALIDATION stands. The first version accepted only
     `shipped`, so it demanded a status that would OVERCLAIM 'validated + live'
     on precisely the five cells the operator had just correctly moved to
     `shipped_gate_failed`. It would have punished the right answer.

  2. The lever key sets are IMPORTED from the sweep, never restated. A second
     copy would drift from the one that actually decides which levers a leg
     arms, and the failure that produces — grading a lever the sweep does not
     recognise — is indistinguishable from a real finding.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_matrix_config_agreement.py"


def test_guard_self_test_passes() -> None:
    p = subprocess.run([sys.executable, str(GUARD), "--self-test"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    # The self-test must actually assert both directions; a self-test that
    # silently shrank to one case would still exit 0.
    for needed in ("config arms it while the matrix denies",
                   "matrix claims shipped while config is silent",
                   "a non-leg roll-up row is skipped"):
        assert needed in p.stdout, f"self-test no longer covers: {needed}"


def test_guard_still_imports_the_sweeps_key_sets() -> None:
    """The guard must not grow its own copy of LEVER_DECLARED_KEYS.

    A second copy would be free to drift from the one `declared_levers_present`
    actually uses, and the failure that produces — grading a lever the sweep
    does not recognise — looks exactly like a real finding.
    """
    src = GUARD.read_text()
    assert "import m20_fleet_exit_sweep" in src
    assert "sweep.LEVER_DECLARED_KEYS" in src
    assert "stale_exit_bars" not in src, (
        "the guard appears to restate a lever's config keys; import them"
    )


def test_guard_runs_against_the_real_tree_without_crashing() -> None:
    """Exit 1 (findings) is fine; a traceback is not.

    This is the check that catches import drift, a renamed matrix field, or a
    strategies.yaml shape change — the things that would make the guard useless
    on the day it is registered.
    """
    p = subprocess.run([sys.executable, str(GUARD)],
                       capture_output=True, text=True)
    assert p.returncode in (0, 1), (
        f"guard crashed (rc={p.returncode}):\n{p.stdout}\n{p.stderr}"
    )
    assert "Traceback" not in p.stderr, p.stderr
    assert "matrix rows" in p.stdout, "the guard must state its denominator"
