"""FIX-CA-16 / `CA-A13-gpu-burst-actor-guard`: every issues-triggered workflow
that reads a privileged secret must gate on WHO opened the issue, not the
label alone.

WHY THIS FILE EXISTS. `gpu-burst-train.yml` triggered on `issues: opened`
(public repo — any account that can open an issue can start the run) and
gated its job on nothing but
`contains(github.event.issue.labels.*.name, 'gpu-burst-train')`, while its env
held RUNPOD_API_KEY, VAST_API_KEY, RUNPOD_SSH_KEY, VM_SSH_KEY and
BRANCH_PROTECTION_TOKEN. Every sibling issues-triggered workflow that holds a
secret (system-actions.yml, vm-ib-gateway-live-login-test.yml, ~27 more) ANDs
the label check with an actor-identity clause; this one file was the gap.

This runs `scripts/ci/check_workflow_actor_guard.py` against the REAL
`.github/workflows/` tree (not a copy), so a future PR that re-introduces an
unguarded, secret-holding issues-triggered job fails here — the same
guarantee the CI guard gives on every PR, exercised in the test suite too.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_workflow_actor_guard as guard  # noqa: E402


def test_self_test_passes():
    assert guard.self_test() == 0


def test_no_workflow_in_the_real_tree_is_missing_the_actor_guard():
    """The must-fail-on-main case: before the fix, this listed gpu-burst-train.yml."""
    violations = []
    for path in sorted(guard.WORKFLOWS_DIR.glob("*.yml")):
        violations.extend(guard.check_file(path))
    for path in sorted(guard.WORKFLOWS_DIR.glob("*.yaml")):
        violations.extend(guard.check_file(path))
    assert violations == [], (
        f"issues-triggered, secret-holding job(s) with no actor-identity "
        f"guard: {violations}"
    )


def test_gpu_burst_train_specifically_carries_the_guard():
    path = guard.WORKFLOWS_DIR / "gpu-burst-train.yml"
    assert guard.check_file(path) == []


def test_reset_daily_risk_state_specifically_carries_the_guard():
    """Found by this guard while landing FIX-CA-16 — same vulnerability class,
    not named in the CA-A13 brief, fixed in the same PR per Generation
    Discipline Rule 2 (a non-compliant precedent that touches what is shipped
    is fixed in the same PR, not silently replicated)."""
    path = guard.WORKFLOWS_DIR / "reset-daily-risk-state.yml"
    assert guard.check_file(path) == []
