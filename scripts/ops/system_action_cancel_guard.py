#!/usr/bin/env python3
"""A system-action that was cancelled before it ran is never silent (OA-17).

Fired by .github/workflows/system-actions-cancel-guard.yml on every
system-actions run that concludes ``cancelled``. GitHub keeps only ONE pending
run per concurrency group, so a newer request cancels an older pending one; the
requester's issue used to stay open with zero comments and the action simply
never happened (2026-10-04: #16251 and #16263, two OA-03/OA-01 de-risk steps).

What it does, per cancelled run (issue number comes from the run's
``system-action-request`` artifact, written by the workflow's ``route`` job):

  * main job NEVER STARTED (no steps) and the issue is still OPEN
      -> comment "CANCELLED, NOT RUN", re-file the identical body as a new
         ``system-action`` issue (title prefixed ``[requeue N]``), close the
         original as not_planned. At most MAX_REQUEUES times; after that, or
         without a re-queue token, comment only and leave the issue open.
  * main job STARTED, then was cancelled -> comment only, never re-queue: the
    action may have partially applied and must be re-read before a retry.
  * issue CLOSED -> nothing (closing the issue is how a requester withdraws).

The re-queue uses REQUEUE_TOKEN (a PAT): an issue created with GITHUB_TOKEN
does not trigger workflows (the same trap as OA-10's hold-merge alarm).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from typing import Any, Mapping, Optional, Sequence

MAX_REQUEUES = 3
MAIN_JOB = "system-action"
_REQUEUE_RE = re.compile(r"^\[requeue (\d+)\]\s*")


def never_ran(jobs: Sequence[Mapping[str, Any]]) -> bool:
    """True when the main job did not execute a single step. A job cancelled
    while pending has no steps; a missing main job never ran either."""
    main = [j for j in jobs if j.get("name") == MAIN_JOB]
    if not main:
        return True
    return all(not (j.get("steps") or []) for j in main)


def requeue_title(title: str) -> tuple[int, str]:
    """(attempt number of THIS title, title for the next attempt)."""
    m = _REQUEUE_RE.match(title or "")
    n = int(m.group(1)) if m else 0
    base = _REQUEUE_RE.sub("", title or "", count=1)
    return n, f"[requeue {n + 1}] {base}"


def decide(*, issue_state: str, title: str, jobs: Sequence[Mapping[str, Any]],
           have_requeue_token: bool) -> str:
    """One of: ``skip_closed`` / ``comment_started`` / ``requeue`` /
    ``comment_cap`` / ``comment_no_token``."""
    if issue_state != "open":
        return "skip_closed"
    if not never_ran(jobs):
        return "comment_started"
    n, _ = requeue_title(title)
    if n >= MAX_REQUEUES:
        return "comment_cap"
    if not have_requeue_token:
        return "comment_no_token"
    return "requeue"


def _api(method: str, path: str, token: str, body: Optional[dict] = None) -> Any:
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["CANCELLED_RUN_ID"]
    run_url = os.environ.get("CANCELLED_RUN_URL", "")
    issue_no = (os.environ.get("ISSUE_NUMBER") or "").strip()
    token = os.environ["GH_TOKEN"]
    requeue_token = (os.environ.get("REQUEUE_TOKEN") or "").strip()
    if not issue_no.isdigit():
        print(f"cancel-guard: run {run_id} carries no issue number (workflow_dispatch or "
              "pre-route cancel) -- nothing to notify")
        return 0
    issue = _api("GET", f"/repos/{repo}/issues/{issue_no}", token)
    jobs = (_api("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=50", token) or {}).get("jobs", [])
    verdict = decide(issue_state=issue.get("state", ""), title=issue.get("title", ""),
                     jobs=jobs, have_requeue_token=bool(requeue_token))
    print(f"cancel-guard: run {run_id} issue #{issue_no} -> {verdict}")
    head = (f"⚠️ **CANCELLED, NOT RUN** — system-actions run {run_url or run_id} was cancelled "
            "while pending (GitHub keeps one pending run per concurrency group; a newer "
            "request superseded it). Nothing was executed on the VM.")
    if verdict == "skip_closed":
        return 0
    if verdict == "comment_started":
        msg = (f"⚠️ **CANCELLED AFTER IT STARTED** — run {run_url or run_id} began executing and was "
               "then cancelled. It is NOT re-queued: the action may have partially applied. "
               "Read the run log and the VM state before filing it again.")
    elif verdict == "comment_cap":
        msg = head + f" This was already re-queued {MAX_REQUEUES} times; not re-queued again — re-file it."
    elif verdict == "comment_no_token":
        msg = head + " No re-queue token is configured; re-file this request."
    else:
        _, new_title = requeue_title(issue["title"])
        body = (issue.get("body") or "").rstrip("\n") + f"\nrequeue_of: #{issue_no}\n"
        new = _api("POST", f"/repos/{repo}/issues", requeue_token,
                   {"title": new_title, "body": body, "labels": ["system-action"]})
        msg = head + f" Re-queued automatically as #{new['number']} (OA-17)."
        _api("POST", f"/repos/{repo}/issues/{issue_no}/comments", token,
             {"body": msg + "\n\n---\n_system-actions-cancel-guard_"})
        _api("PATCH", f"/repos/{repo}/issues/{issue_no}", token,
             {"state": "closed", "state_reason": "not_planned"})
        print(f"cancel-guard: re-queued #{issue_no} as #{new['number']}")
        return 0
    _api("POST", f"/repos/{repo}/issues/{issue_no}/comments", token,
         {"body": msg + "\n\n---\n_system-actions-cancel-guard_"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
