#!/usr/bin/env python3
"""Emit a Claude-review request artifact (layer 2 of 2).

After the machine health-check finishes (layer 1), the GitHub Action
calls this script to write a schema-valid ``comms/requests/REQ-*.json``
that asks Claude — via the existing operator/Claude comms channel — to
do a manual sanity review of the same run.

This runs **on every health-check execution**, not only WARNING/CRITICAL
ones. The user's design rule: Claude review is a mandatory second-stage
sanity check, not a fallback.

Idempotency
-----------
The slug embedded in the ``request_id`` is derived from the GitHub
``run_id``, so a re-run of the same workflow run produces the same
filename. The script will refuse to overwrite an existing file with the
same id (exit 0, "already exists") so the workflow stays idempotent.

Schema
------
The artifact validates against ``comms/schema/request.schema.json``
(``additionalProperties: false`` at the top level). The machine-result
JSON and artifact paths are inlined into the ``context`` field — that's
the only free-form string permitted by the schema, and it keeps the
review reader from having to dig through the Actions artifact.

The expected reply shape lives in
``comms/schema/health_review_response.template.json``. The reviewer
pastes a JSON blob matching that template into the request's free-text
answer, and the bot files it under ``.response.answers[0].free_text``
in the standard way.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


_REPO_ROOT = Path(__file__).resolve().parents[1]
_REQUESTS_DIR = _REPO_ROOT / "comms" / "requests"
_TEMPLATE_PATH = _REPO_ROOT / "comms" / "schema" / "health_review_response.template.json"
_RUNBOOK_REL = "docs/runbooks/health-check.md"


def _slug_from_run_id(run_id: str) -> str:
    """Schema requires ``[a-z0-9]{4,12}``. Run ids are numeric integers,
    typically 10–11 digits — fits as-is."""
    digits = "".join(ch for ch in run_id.lower() if ch.isalnum())
    if len(digits) < 4:
        digits = (digits + "0000")[:4]
    return digits[-12:]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[write_review] could not parse {path}: {exc}", file=sys.stderr)
        return None


def build_request(
    *,
    now: _dt.datetime,
    run_id: str,
    workflow_run_url: Optional[str],
    branch: Optional[str],
    commit_sha: Optional[str],
    machine_report: Optional[Dict[str, Any]],
    snapshot_path_in_repo: str,
    report_path_in_repo: Optional[str],
) -> Dict[str, Any]:
    slug = _slug_from_run_id(run_id) if run_id else now.strftime("%H%M%S")
    request_id = f"REQ-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{slug}"

    machine_status = (machine_report or {}).get("status", "UNKNOWN")
    priority = "high" if machine_status in {"WARNING", "CRITICAL"} else "normal"

    # Compose context — this is the only free-form text the schema lets
    # us carry, so we inline machine result + paths + run identity.
    ctx_lines = [
        "Automated GitHub Action health check has completed.",
        "",
        f"run_id: {run_id or 'unknown'}",
        f"branch: {branch or 'unknown'}",
        f"commit: {commit_sha or 'unknown'}",
        f"priority: {priority}",
        "",
        "Artifacts (this run):",
        f"  - workflow run:  {workflow_run_url or 'n/a'}",
        f"  - snapshot:      {snapshot_path_in_repo}  (Action artifact only)",
        f"  - machine report: {report_path_in_repo or 'n/a'}  (Action artifact only)",
        "",
        f"Machine result (status={machine_status}):",
        "```json",
        json.dumps(machine_report or {}, indent=2, sort_keys=True),
        "```",
        "",
        f"See {_RUNBOOK_REL} for the two-layer design and review template.",
    ]
    context = "\n".join(ctx_lines)

    request: Dict[str, Any] = {
        "request_id": request_id,
        "schema_version": 1,
        "created_at": now.isoformat(),
        "expires_at": (now + _dt.timedelta(hours=24)).isoformat(),
        "stuck_alert_threshold": 21600,  # 6h — re-alert before next scheduled run
        "source": {
            "actor": "system",
            "session_id": None,
            "branch": branch,
            "pr_number": None,
            "task": f"claude_health_review:{run_id or 'manual'}",
        },
        "topic": f"Health review needed — run {run_id or 'manual'} ({machine_status})",
        "context": context,
        "questions": [
            {
                "question_id": "review_json",
                "prompt": (
                    "Sanity-review the trading pipeline for this run. "
                    "Check heartbeat freshness, tick flow, signal/order/trade "
                    "logs, monitoring/watchdog functions, position sizing, "
                    "and API/error logs. Reply with a JSON blob matching "
                    "comms/schema/health_review_response.template.json."
                ),
                "input_type": "free_text",
                "allow_free_text": True,
                "required": True,
            }
        ],
        "default_on_timeout": "expire",
        "status": "pending",
        "history": [
            {
                "at": now.isoformat(),
                "from_status": None,
                "to_status": "pending",
                "actor": "system",
                "note": f"emitted by .github/workflows/health-check.yml (run {run_id or 'manual'})",
            }
        ],
    }
    return request


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--report",
        type=Path,
        help="path to the machine-side health_check JSON (latest.json). "
        "If omitted, the request is still emitted with status=UNKNOWN.",
    )
    p.add_argument(
        "--snapshot-path",
        default="health_snapshot.txt",
        help="repo-relative path of the raw snapshot (for the context field).",
    )
    p.add_argument(
        "--report-path",
        default="runtime_logs/health_checks/latest.json",
        help="repo-relative path of the machine report (for the context field).",
    )
    p.add_argument(
        "--run-id",
        default=os.environ.get("GITHUB_RUN_ID", ""),
    )
    p.add_argument(
        "--workflow-url",
        default=(
            f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID', '')}"
            if os.environ.get("GITHUB_RUN_ID")
            else ""
        ),
    )
    p.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", ""))
    p.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA", ""))
    p.add_argument(
        "--out-dir",
        type=Path,
        default=_REQUESTS_DIR,
    )
    args = p.parse_args(argv)

    machine_report = _read_json(args.report) if args.report else None

    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    request = build_request(
        now=now,
        run_id=args.run_id,
        workflow_run_url=args.workflow_url or None,
        branch=args.branch or None,
        commit_sha=args.commit_sha or None,
        machine_report=machine_report,
        snapshot_path_in_repo=args.snapshot_path,
        report_path_in_repo=args.report_path,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{request['request_id']}.json"
    if out_path.exists():
        print(f"[write_review] already exists, skipping: {out_path}")
    else:
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, out_path)
        print(f"[write_review] wrote {out_path}")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"request_path={out_path}\n")
            fh.write(f"request_id={request['request_id']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
