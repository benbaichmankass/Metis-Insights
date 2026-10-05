"""FIX-CA-17 / `CA-A14-session-reaper-dead-trigger`: session-reaper.yml must
have at least one trigger that can actually fire.

WHY THIS FILE EXISTS. session-reaper.yml's schedule cron was commented out in
the 2026-09-21 operating reset, leaving `workflow_dispatch` (manual only) and a
`push: paths:` filter watching two files — one of which,
`docs/claude/work/SESSIONS.json`, is itself one of the eight registers that
same reset ARCHIVED under `docs/archive/2026-09-21-operating-reset/`. Nothing
writes that path any more, so that half of the filter was permanently dead,
and `scripts/ops/session_reaper.py` is edited rarely — confirmed 2026-09-27:
zero runs of any kind since 2026-09-21T12:18:04Z, six days of silence on a
workflow whose whole job is noticing what a dead session left behind.

The fix drops the `paths:` filter so every push to `main` re-reaps — the same
push-on-main fallback `work-digest.yml` already uses ("the ONLY carrier that
can fire while `main` is quiet", per its own header) — rather than gating on a
file that no longer exists anywhere in the live tree.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "session-reaper.yml"


def _on_block(doc: dict):
    # YAML 1.1 parses the bare key `on:` as the boolean True.
    return doc.get("on") if "on" in doc else doc.get(True)


def test_the_dead_path_no_longer_exists_anywhere_in_the_live_tree():
    """Sanity-checks the defect itself: SESSIONS.json really is gone, so a
    `paths:` filter naming it really would never fire."""
    assert not (REPO / "docs" / "claude" / "work" / "SESSIONS.json").exists()
    archived = REPO / "docs" / "archive" / "2026-09-21-operating-reset" / "work" / "SESSIONS.json"
    assert archived.exists(), (
        "expected SESSIONS.json to have been moved under the 2026-09-21 "
        "operating-reset archive — if it moved elsewhere, this test's "
        "premise needs updating, not deleting"
    )


def test_no_push_trigger_so_a_merge_burst_cannot_cancel_runs():
    """REAPER-NOISE 2026-10-04. The push trigger (FIX-CA-17) made the reaper run on
    every main push; its concurrency group then cancelled superseded runs (16 of
    the last 30) and the failure alert paged each as a dead run. The ledger it
    writes has no live reader, so the trigger is retired, not re-filtered."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
    on = _on_block(doc) or {}
    assert isinstance(on, dict)
    assert "push" not in on, "session-reaper.yml regained a push trigger"
    assert "schedule" not in on, "session-reaper.yml regained a cron; re-arm deliberately"


def test_workflow_dispatch_is_still_available_as_a_manual_fallback():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
    on = _on_block(doc) or {}
    assert "workflow_dispatch" in on
