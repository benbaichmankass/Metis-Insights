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


def _load_push_trigger() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
    on = _on_block(doc) or {}
    assert isinstance(on, dict), f"unexpected `on:` shape in {WORKFLOW}: {on!r}"
    assert "push" in on, f"{WORKFLOW} lost its push trigger entirely"
    push = on["push"]
    assert isinstance(push, dict)
    return push


def test_the_dead_path_no_longer_exists_anywhere_in_the_live_tree():
    """Sanity-checks the defect itself: SESSIONS.json really is gone, so a
    `paths:` filter naming it really would never fire."""
    assert not (REPO / "docs" / "claude" / "work" / "SESSIONS.json").exists()
    archived = (
        REPO / "docs" / "archive" / "2026-09-21-operating-reset"
        / "work" / "SESSIONS.json"
    )
    assert archived.exists(), (
        "expected SESSIONS.json to have been moved under the 2026-09-21 "
        "operating-reset archive — if it moved elsewhere, this test's "
        "premise needs updating, not deleting"
    )


def test_push_trigger_fires_on_main_with_no_dead_path_filter():
    """The must-fail-on-main case: on current main this filtered on `paths:`
    including the now-archived SESSIONS.json, so it would still assert False
    on the `"paths" not in push` half."""
    push = _load_push_trigger()
    branches = push.get("branches")
    assert branches == ["main"], f"expected push on main only, got {branches!r}"
    assert "paths" not in push, (
        "session-reaper.yml's push trigger still filters on `paths:` — if "
        "re-adding one, every path must correspond to a file something "
        "still writes, or the filter silently goes dead again"
    )


def test_workflow_dispatch_is_still_available_as_a_manual_fallback():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
    on = _on_block(doc) or {}
    assert "workflow_dispatch" in on
