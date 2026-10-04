"""OA-17 (2026-10-04): a queued system-action must never be lost silently.

Pins both halves of the fix:
  (a) system-actions.yml has NO workflow-level single shared group; the
      system-action job takes its group from the route job, read-only actions
      get a group of their own, mutating ones keep the shared serial lane.
  (b) system-actions-cancel-guard.yml fires on cancelled runs and the guard
      comments + alerts and NEVER re-queues (re-queue inverts request order).
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
    env = {k: v for s in d["jobs"]["guard"]["steps"] for k, v in (s.get("env") or {}).items()}
    assert "TELEGRAM_BOT_TOKEN" in env, "the alert must be direct, not a droppable system-action"
    assert "REQUEUE_TOKEN" not in env and "BRANCH_PROTECTION_TOKEN" not in str(d), \
        "the guard must never be able to re-file a request (it would invert request order)"


PENDING_CANCELLED = [{"name": "route", "steps": [{"name": "x"}]},
                     {"name": "system-action", "conclusion": "cancelled", "steps": []}]
STARTED_CANCELLED = [{"name": "system-action", "conclusion": "cancelled",
                      "steps": [{"name": "Set up job"}]}]


def test_guard_decisions_have_no_requeue():
    d = guard.decide
    assert d(issue_state="open", jobs=PENDING_CANCELLED) == "comment_and_alert"
    assert d(issue_state="closed", jobs=PENDING_CANCELLED) == "skip_closed"
    assert d(issue_state="open", jobs=STARTED_CANCELLED) == "comment_started"
    assert not hasattr(guard, "requeue_title")


class _FakeGH:
    def __init__(self, issue_state="open", jobs=PENDING_CANCELLED):
        self.calls = []
        self.issue_state, self.jobs = issue_state, jobs

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))
        if path.endswith("/issues/42"):
            return {"state": self.issue_state, "title": "[system-action] set-env X=live",
                    "html_url": "https://example/42"}
        if path.endswith("/jobs?per_page=50"):
            return {"jobs": self.jobs}
        if path.endswith("/actions/runs/100"):
            return {"created_at": "2026-10-04T10:00:00Z"}
        if "/workflows/system-actions.yml/runs" in path:
            return {"workflow_runs": [
                {"id": 100, "created_at": "2026-10-04T10:00:00Z"},
                {"id": 101, "created_at": "2026-10-04T10:00:30Z", "display_title": "set-env X=read_only",
                 "html_url": "https://example/run/101"},
                {"id": 99, "created_at": "2026-10-04T09:59:00Z"}]}
        return {}


ENV = {"GITHUB_REPOSITORY": "o/r", "CANCELLED_RUN_ID": "100", "ISSUE_NUMBER": "42", "GH_TOKEN": "t"}


def test_mutating_cancel_comments_and_alerts_and_opens_no_issue():
    gh, alerts = _FakeGH(), []
    assert guard.run(ENV, api=gh, telegram=alerts.append) == "comment_and_alert"
    posts = [(p, b) for m, p, b in gh.calls if m == "POST"]
    assert [p for p, _ in posts] == ["/repos/o/r/issues/42/comments"], "exactly one comment, no new issue"
    body = posts[0][1]["body"]
    assert "CANCELLED, NOT RUN" in body and "run/101" in body and "NOT re-queued" in body
    assert not [c for c in gh.calls if c[0] == "PATCH"], "the issue is left open"
    assert len(alerts) == 1 and "#42" in alerts[0] and "101" in alerts[0]


def test_started_cancel_comments_only_and_closed_issue_is_untouched():
    gh, alerts = _FakeGH(jobs=STARTED_CANCELLED), []
    assert guard.run(ENV, api=gh, telegram=alerts.append) == "comment_started"
    assert [p for m, p, _ in gh.calls if m == "POST"] == ["/repos/o/r/issues/42/comments"] and not alerts
    gh, alerts = _FakeGH(issue_state="closed"), []
    assert guard.run(ENV, api=gh, telegram=alerts.append) == "skip_closed"
    assert not [c for c in gh.calls if c[0] != "GET"] and not alerts
