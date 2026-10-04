"""OA-17 (2026-10-04): a queued system-action must never be lost silently.

Pins both halves of the fix:
  (a) system-actions.yml has NO workflow-level single shared group; the
      system-action job takes its group from the route job, read-only actions
      get a group of their own, mutating ones keep the shared serial lane.
  (b) system-actions-cancel-guard.yml fires on cancelled runs and the guard
      comments / re-queues correctly.
A regression to one workflow-level "system-actions" group fails here.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from scripts.ops import system_action_cancel_guard as guard
from scripts.ops import system_action_route as route

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows"


def _wf(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def test_no_workflow_level_shared_group():
    d = _wf("system-actions.yml")
    assert "concurrency" not in d, (
        "a workflow-level concurrency group puts every request in one lane, where GitHub's "
        "one-pending-run rule silently cancels older requests (OA-17)")


def test_main_job_takes_group_from_route():
    jobs = _wf("system-actions.yml")["jobs"]
    assert jobs["system-action"]["needs"] == "route"
    assert jobs["system-action"]["concurrency"]["group"] == "${{ needs.route.outputs.group }}"
    assert jobs["system-action"]["concurrency"]["cancel-in-progress"] is False
    assert "concurrency" not in jobs["route"], "route must never wait: it records the issue"
    # Same actor/label gate on both jobs, so route cannot run for a request the main job refuses.
    assert jobs["route"]["if"] == jobs["system-action"]["if"]
    steps = jobs["route"]["steps"]
    assert any((s.get("with") or {}).get("name") == "system-action-request" for s in steps)


def test_read_only_actions_get_their_own_group_mutations_share_one():
    for a in route.READ_ONLY:
        assert route.concurrency_group(a, "123") == "system-actions-ro-123"
    assert route.concurrency_group("get-env", "1") != route.concurrency_group("get-env", "2")
    for a in ("set-env", "breakout-login-check", "pull-and-deploy", "flatten-bybit-position",
              "set-account-mode", "send-ping", "", "unknown-new-action"):
        assert route.concurrency_group(a, "123") == route.SHARED_GROUP, a


def test_read_only_allowlist_is_documented_read_only():
    doc = (REPO / "docs" / "claude" / "system-actions.md").read_text(encoding="utf-8")
    for a in route.READ_ONLY:
        row = next((ln for ln in doc.splitlines() if ln.startswith(f"| `{a}` |")), None)
        assert row is not None, f"{a} has no row in docs/claude/system-actions.md"
        assert row.split("|")[2].strip() == "1", f"{a} is not Tier-1 in the allowlist doc"


def test_issue_body_parse_matches_the_workflow():
    assert route.action_from_issue_body("reason: x\nAction:  get-env \naction: set-env") == "get-env"
    assert route.action_from_issue_body("no action here") == ""


def test_cancel_guard_workflow_fires_on_cancelled_issue_runs():
    d = _wf("system-actions-cancel-guard.yml")
    on = d.get("on") or d.get(True)
    assert on["workflow_run"]["workflows"] == ["system-actions"]
    assert "completed" in on["workflow_run"]["types"]
    cond = d["jobs"]["guard"]["if"]
    assert "conclusion == 'cancelled'" in cond and "event == 'issues'" in cond
    step_env = [s.get("env") or {} for s in d["jobs"]["guard"]["steps"]]
    assert any("BRANCH_PROTECTION_TOKEN" in str(e.get("REQUEUE_TOKEN", "")) for e in step_env), \
        "re-queue must use a PAT: a GITHUB_TOKEN-created issue never triggers system-actions"


PENDING_CANCELLED = [{"name": "route", "steps": [{"name": "x"}]},
                     {"name": "system-action", "conclusion": "cancelled", "steps": []}]
STARTED_CANCELLED = [{"name": "system-action", "conclusion": "cancelled",
                      "steps": [{"name": "Set up job"}]}]


def test_guard_decisions():
    d = guard.decide
    assert d(issue_state="open", title="[system-action] x", jobs=PENDING_CANCELLED,
             have_requeue_token=True) == "requeue"
    assert d(issue_state="closed", title="x", jobs=PENDING_CANCELLED,
             have_requeue_token=True) == "skip_closed"
    assert d(issue_state="open", title="x", jobs=STARTED_CANCELLED,
             have_requeue_token=True) == "comment_started"
    assert d(issue_state="open", title="x", jobs=PENDING_CANCELLED,
             have_requeue_token=False) == "comment_no_token"
    assert d(issue_state="open", title="[requeue 3] x", jobs=PENDING_CANCELLED,
             have_requeue_token=True) == "comment_cap"


def test_requeue_title_counts_and_does_not_nest():
    assert guard.requeue_title("[system-action] get-env") == (0, "[requeue 1] [system-action] get-env")
    assert guard.requeue_title("[requeue 1] [system-action] get-env") == (
        1, "[requeue 2] [system-action] get-env")
