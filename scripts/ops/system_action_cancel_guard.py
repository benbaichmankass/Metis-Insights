#!/usr/bin/env python3
"""A system-action that was cancelled before it ran is never silent (OA-17).

Fired by .github/workflows/system-actions-cancel-guard.yml on every
system-actions run that concludes ``cancelled``. GitHub keeps only ONE pending
run per concurrency group, so a newer request cancels an older pending one; the
requester's issue used to stay open with zero comments and the action simply
never happened (2026-10-04: #16251 and #16263, two OA-03/OA-01 de-risk steps).

It NEVER re-queues (manager review of #16289, 2026-10-04). A pending run is
cancelled precisely because a NEWER request queued behind it in the shared
mutating lane; re-filing the older one would run it AFTER the newer one and
invert their order -- an older ``set-env PROP_EXECUTOR_MODE=live`` displaced by
a newer ``=read_only`` would silently undo the de-risk. Read-only actions no
longer share a lane (scripts/ops/system_action_route.py), so they are not
displaced at all. What it does, per cancelled run (the issue number comes from
the run's ``system-action-request`` artifact, written by its ``route`` job):

  * main job NEVER STARTED and the issue is OPEN -> comment "CANCELLED, NOT RUN"
    naming the run that displaced it, AND send one direct Telegram alert (not a
    system-action: that would sit in the same droppable lane), so a dropped
    de-risk step is seen. The issue is left open; re-filing is a human/manager
    decision taken AFTER checking what ran since.
  * main job STARTED, then was cancelled -> comment only (may have partially
    applied; read the run log and the VM first).
  * issue CLOSED -> nothing (closing the issue is how a requester withdraws).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional, Sequence

MAIN_JOB = "system-action"
FOOTER = "\n\n---\n_system-actions-cancel-guard (OA-17)_"


def never_ran(jobs: Sequence[Mapping[str, Any]]) -> bool:
    """True when the main job did not execute a single step. A job cancelled
    while pending has no steps; a missing main job never ran either."""
    main = [j for j in jobs if j.get("name") == MAIN_JOB]
    if not main:
        return True
    return all(not (j.get("steps") or []) for j in main)


def decide(*, issue_state: str, jobs: Sequence[Mapping[str, Any]]) -> str:
    """``skip_closed`` / ``comment_started`` / ``comment_and_alert``. There is
    deliberately no re-queue verdict (it would invert request order)."""
    if issue_state != "open":
        return "skip_closed"
    if not never_ran(jobs):
        return "comment_started"
    return "comment_and_alert"


def displacer(runs: Sequence[Mapping[str, Any]], cancelled_id: int,
              cancelled_created: str) -> Optional[Mapping[str, Any]]:
    """The earliest system-actions run created after the cancelled one -- the
    request that queued behind it and took its pending slot (best effort)."""
    later = [r for r in runs if r.get("id") != cancelled_id
             and (r.get("created_at") or "") > (cancelled_created or "")]
    return min(later, key=lambda r: r.get("created_at") or "") if later else None


def cancelled_comment(run_ref: str, issue_no: str, disp: Optional[Mapping[str, Any]]) -> str:
    by = (f"superseded/displaced by run {disp.get('html_url') or disp.get('id')} "
          f"(\"{disp.get('display_title', '')}\")") if disp else "superseded by a newer queued request"
    return (f"⚠️ **CANCELLED, NOT RUN** — system-actions run {run_ref} for #{issue_no} was cancelled "
            f"while pending: {by}. GitHub keeps one pending run per concurrency group. **Nothing was "
            "executed on the VM.** It is NOT re-queued automatically: re-running it now would place it "
            "AFTER the request that displaced it. Re-file only if it is still wanted, after checking "
            "what ran since." + FOOTER)


def started_comment(run_ref: str) -> str:
    return (f"⚠️ **CANCELLED AFTER IT STARTED** — run {run_ref} began executing and was then "
            "cancelled. It may have partially applied. Read the run log and the VM state before "
            "filing it again." + FOOTER)


def _api(method: str, path: str, token: str, body: Optional[dict] = None) -> Any:
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def _telegram(text: str) -> bool:
    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not tok or not chat:
        print("::warning::Telegram secrets not configured; the CANCELLED comment was still posted")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage",
                                    data=data, timeout=20):
            return True
    except Exception as exc:  # noqa: BLE001 -- best effort; the comment is the durable record
        print(f"::warning::Telegram alert failed ({type(exc).__name__}); the comment was still posted")
        return False


def run(env: Mapping[str, str], api=_api, telegram=_telegram) -> str:
    repo = env["GITHUB_REPOSITORY"]
    run_id = env["CANCELLED_RUN_ID"]
    run_ref = env.get("CANCELLED_RUN_URL") or run_id
    issue_no = (env.get("ISSUE_NUMBER") or "").strip()
    token = env["GH_TOKEN"]
    if not issue_no.isdigit():
        print(f"cancel-guard: run {run_id} carries no issue number -- nothing to notify")
        return "no_issue"
    issue = api("GET", f"/repos/{repo}/issues/{issue_no}", token)
    jobs = (api("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=50", token) or {}).get("jobs", [])
    verdict = decide(issue_state=issue.get("state", ""), jobs=jobs)
    print(f"cancel-guard: run {run_id} issue #{issue_no} -> {verdict}")
    if verdict == "skip_closed":
        return verdict
    if verdict == "comment_started":
        api("POST", f"/repos/{repo}/issues/{issue_no}/comments", token, {"body": started_comment(run_ref)})
        return verdict
    me = api("GET", f"/repos/{repo}/actions/runs/{run_id}", token) or {}
    runs = (api("GET", f"/repos/{repo}/actions/workflows/system-actions.yml/runs?per_page=30",
                token) or {}).get("workflow_runs", [])
    disp = displacer(runs, int(run_id), me.get("created_at", ""))
    api("POST", f"/repos/{repo}/issues/{issue_no}/comments", token,
        {"body": cancelled_comment(run_ref, issue_no, disp)})
    telegram(f"⚠️ system-action CANCELLED, NOT RUN: #{issue_no} \"{issue.get('title', '')}\" was displaced "
             f"while pending{' by run ' + str(disp.get('id')) if disp else ''}. Nothing ran; it was NOT "
             f"re-queued. Re-file only if still wanted. {issue.get('html_url', '')}")
    return verdict


if __name__ == "__main__":
    run(os.environ)
    sys.exit(0)
