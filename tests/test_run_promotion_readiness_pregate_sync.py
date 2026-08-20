"""The scheduled promotion-readiness sweep must refresh its evidence before gating.

BL-20260820-PROMOREADY-GATES-UNREACHABLE.

`ml/promotion/gates.py::_gate_live_parity` judges serving fidelity only over rows
logged since the CURRENT artifact's training run. On the trainer's own clocks
(measured 2026-08-20) the live->trainer pull lands at 00:51, the retrain finishes
at 01:14, and this sweep runs at 04:22 — so without a pre-gate refresh it counts
rows logged after 01:14 inside a log that ends at 00:51, and `n_fresh_rows` is 0
BY CONSTRUCTION, every day, for reasons that are not about any model.

`scripts/ml/gate_check_candidates.sh` — the hand-run path — has avoided this
since 2026-08-01 by syncing first. These tests hold the SCHEDULED path to the
same contract, and hold the two paths to each other so they cannot drift apart
again.

They assert on the shipping script text rather than a copy of it: a test that
embeds its own copy of the thing it checks passes forever after the original
changes.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
SCHEDULED = REPO / "scripts" / "ops" / "run_promotion_readiness.sh"
MANUAL = REPO / "scripts" / "ml" / "gate_check_candidates.sh"
SYNC = REPO / "scripts" / "ops" / "sync_trainer_data.sh"


def test_the_sync_script_the_two_paths_depend_on_exists() -> None:
    """Positive control. If this file is ever renamed, both callers become
    silent no-ops (each is best-effort by design), and every assertion below
    would still pass while the gates starve again."""
    assert SYNC.is_file(), (
        "scripts/ops/sync_trainer_data.sh is gone — both the scheduled and the "
        "manual gate paths call it best-effort, so its absence is SILENT and "
        "re-creates the stale-evidence bug in both."
    )


def test_scheduled_sweep_refreshes_evidence_before_gating() -> None:
    text = SCHEDULED.read_text()
    assert "sync_trainer_data.sh" in text, (
        "run_promotion_readiness.sh must refresh the shadow log before grading "
        "live_parity/labels_accruing, as gate_check_candidates.sh already does."
    )
    # Anchor on the EXECUTABLE invocation, not the first textual occurrence:
    # `-m ml promotion-readiness` appears in the file's header comment long
    # before it is run, and matching that made this assertion fail against a
    # correct script. Same docstring-vs-code trap the audit kept finding.
    sync_at = text.index('bash "$REPO_ROOT/scripts/ops/sync_trainer_data.sh"')
    gate_at = text.index('"$VENV_DIR/bin/python" -m ml promotion-readiness')
    assert sync_at < gate_at, (
        "the sync must run BEFORE the sweep; refreshing evidence after grading "
        "it is the same as not refreshing it."
    )


def test_scheduled_sweep_runs_the_sync_inside_the_heavy_lock() -> None:
    """The pull is I/O + disk on a 6 GB box that OOMs when two heavy jobs
    overlap (BL-20260715). It must sit after the lock is taken."""
    text = SCHEDULED.read_text()
    lock_at = text.index("take_trainer_heavy_lock")
    sync_at = text.index("bash \"$REPO_ROOT/scripts/ops/sync_trainer_data.sh\"")
    assert lock_at < sync_at, "the pre-gate sync must run inside the heavy lock"


def test_evidence_state_is_recorded_and_is_three_state() -> None:
    """A gate graded on stale evidence and one graded on fresh evidence must not
    be indistinguishable in the artifact. `ok` / `failed` / `skipped_absent` are
    three different facts and the third is not the second."""
    text = SCHEDULED.read_text()
    assert "evidence_state.json" in text, (
        "the sweep must stamp what evidence it graded against beside the report"
    )
    for state in ("ok", "failed", "skipped_absent"):
        assert re.search(rf'PREGATE_SYNC_STATE="{state}"', text), (
            f"pre-gate sync state '{state}' is never set — a collapsed state "
            f"here means a reader cannot tell why a gate said insufficient_data"
        )
    assert "oos_edge_mode" in text, (
        "the stamped evidence state must also carry whether oos_edge was "
        "computed at all; it is the gate that decides `promote`, and it is off "
        "by default (MB-20260719-PROMOREADY-OOSEDGE-OOM)."
    )


def test_a_failed_sync_does_not_abort_the_run() -> None:
    """Best-effort, like the manual path: a sync failure must degrade to gating
    on the existing log, never skip the report. Losing the packet entirely is
    strictly worse than an honestly-labelled stale one."""
    text = SCHEDULED.read_text()
    m = re.search(
        r'if bash "\$REPO_ROOT/scripts/ops/sync_trainer_data\.sh".*?\n  else\n(.*?)\n  fi',
        text, re.S,
    )
    assert m, "expected an if/else around the pre-gate sync"
    failure_branch = m.group(1)
    assert "exit" not in failure_branch, (
        "a failed pre-gate sync must not exit — it must fall through and gate "
        f"against the existing log. Failure branch was:\n{failure_branch}"
    )
    assert "PREGATE_SYNC_STATE=\"failed\"" in failure_branch


def test_both_gate_paths_still_refresh_evidence() -> None:
    """The two paths drifted once — the manual one was fixed 2026-08-01 and the
    scheduled one was not, for 19 days. Hold them together."""
    for path in (SCHEDULED, MANUAL):
        assert "sync_trainer_data.sh" in path.read_text(), (
            f"{path.relative_to(REPO)} no longer refreshes evidence before "
            f"gating — the two paths have drifted apart again."
        )


def test_script_is_syntactically_valid() -> None:
    assert subprocess.run(["bash", "-n", str(SCHEDULED)]).returncode == 0
