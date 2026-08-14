"""Keep `matrix-config-agreement` alive while it waits to be registered.

The guard is deliberately NOT in `run_guards.py` yet: it fails on the current
tree because the six cells it found are real and unreconciled, and reconciling
them is an operator-gated judgement call rather than a mechanical sync (see the
guard's module docstring). A guard sitting unregistered is a guard that quietly
stops working — its imports drift, the sweep's key sets move, and nobody
notices until the day it is switched on and reports a clean pass over nothing.

So pytest runs its self-test. That keeps the mechanism honest without making CI
red on unrelated PRs, and it means the registration change is a one-liner rather
than a debugging session.
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
