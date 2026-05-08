#!/usr/bin/env python3
"""Close the current Claude Code session by writing/updating the handoff
artifact, committing it, pushing the branch, and (optionally) triggering
the continue-work GitHub Actions workflow.

Usage examples
--------------

Validate the live handoff file without touching git::

    python scripts/session_handoff/close_session.py --validate-only

Update only specific fields and commit::

    python scripts/session_handoff/close_session.py \\
        --reason context_limit_near \\
        --append-completed "Wired up handoff schema" \\
        --append-completed "Added continue-work workflow"

Update + commit + push + dispatch the workflow (the full session-close
flow Claude runs at end of session)::

    python scripts/session_handoff/close_session.py \\
        --sprint-id S-061-session-handoff \\
        --reason natural_checkpoint \\
        --commit --push --dispatch

Notes
-----
* This script is **idempotent** when no flags would mutate the file —
  re-running with no edits is a safe no-op.
* The dispatch path uses ``gh workflow run`` with
  ``--ref <branch> -f sprint_id=… -f handoff_file=…``. ``gh`` must be
  authenticated. If ``gh`` is not present, the script prints the exact
  ``curl`` equivalent and exits 0 (so the operator can dispatch
  manually from the Actions UI).
* No secrets, tokens, or .env content are ever written into the
  handoff file.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HANDOFF = REPO_ROOT / "automation" / "session_handoff" / "next_session.json"
DEFAULT_SCHEMA = REPO_ROOT / "automation" / "session_handoff" / "schema" / "handoff.schema.json"
WORKFLOW_FILE_NAME = "continue-work.yml"

VALID_REASONS = (
    "context_limit_near",
    "session_too_long",
    "fragmented_state",
    "blocked_on_input",
    "natural_checkpoint",
    "operator_requested",
    "other",
)


def _now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def _current_branch() -> str:
    res = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    return res.stdout.strip()


def _load_handoff(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"handoff file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"handoff file is not valid JSON: {e}") from e


def _apply_updates(data: dict, args: argparse.Namespace) -> dict:
    """Apply CLI-supplied updates onto the handoff dict in-place. Returns the
    same dict so callers can chain."""
    if args.sprint_id:
        data["sprint_id"] = args.sprint_id
    if args.sprint_title:
        data["sprint_title"] = args.sprint_title
    if args.branch:
        data["branch"] = args.branch
    if args.reason:
        if args.reason not in VALID_REASONS:
            raise SystemExit(
                f"--reason must be one of {VALID_REASONS}, got {args.reason!r}"
            )
        data["handoff_reason"] = args.reason
    if args.reason_note is not None:
        data["handoff_reason_note"] = args.reason_note
    if args.checkpoint_summary is not None:
        data["checkpoint_summary"] = args.checkpoint_summary
    if args.continuation_prompt is not None:
        data["continuation_prompt"] = args.continuation_prompt
    if args.ready_for_continue is not None:
        data["ready_for_continue"] = args.ready_for_continue

    for entry in args.append_completed or []:
        data.setdefault("completed_items", []).append(entry)
    for entry in args.append_open or []:
        data.setdefault("open_items", []).append(entry)
    for entry in args.append_next_action or []:
        data.setdefault("next_actions", []).append({"title": entry})
    for entry in args.append_guardrail or []:
        data.setdefault("guardrails", []).append(entry)
    for entry in args.append_file_to_review or []:
        data.setdefault("files_to_review", []).append(entry)

    # Always refresh created_at + created_by metadata when we mutate.
    if any(
        getattr(args, k)
        for k in (
            "sprint_id",
            "sprint_title",
            "branch",
            "reason",
            "reason_note",
            "checkpoint_summary",
            "continuation_prompt",
            "append_completed",
            "append_open",
            "append_next_action",
            "append_guardrail",
            "append_file_to_review",
        )
    ) or args.ready_for_continue is not None:
        data["created_at"] = _now_iso()
        data.setdefault("created_by", "claude")
        data.setdefault("history", []).append(
            {
                "at": data["created_at"],
                "event": "updated",
                "actor": data["created_by"],
                "run_url": None,
                "note": "Handoff updated by close_session.py.",
            }
        )
    return data


def _validate(handoff_path: Path, schema_path: Path, sprint_id: str | None) -> dict:
    # Lazy import so unit tests don't have to install jsonschema.
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.session_handoff.validate_handoff import (  # noqa: E402
        HandoffError,
        validate_path,
    )

    try:
        return validate_path(
            handoff_path,
            schema_path=schema_path,
            expect_sprint_id=sprint_id,
            require_ready=False,
        )
    except HandoffError as e:
        raise SystemExit(f"handoff validation failed: {e}") from e


def _git_commit_and_push(handoff_path: Path, *, push: bool, branch: str) -> bool:
    """Stage + commit the handoff. Returns True if a commit was created."""
    rel = handoff_path.relative_to(REPO_ROOT)
    diff = _run(
        ["git", "diff", "--quiet", "--", str(rel)],
        check=False,
    )
    cached = _run(
        ["git", "diff", "--cached", "--quiet", "--", str(rel)],
        check=False,
    )
    if diff.returncode == 0 and cached.returncode == 0:
        print(">>> No change to handoff file; skipping commit.")
        return False
    _run(["git", "add", str(rel)])
    msg = "chore(session-handoff): update next_session handoff"
    _run(["git", "commit", "-m", msg])
    if push:
        _git_push(branch)
    return True


def _git_push(branch: str) -> None:
    last_err: subprocess.CalledProcessError | None = None
    for attempt, delay in enumerate((0, 2, 4, 8, 16), start=1):
        if delay:
            print(f">>> push retry {attempt} after {delay}s")
            import time

            time.sleep(delay)
        try:
            _run(["git", "push", "-u", "origin", branch])
            return
        except subprocess.CalledProcessError as e:
            last_err = e
    if last_err is not None:
        raise SystemExit(f"git push failed after retries: {last_err}")


def _dispatch(sprint_id: str, handoff_rel: str, branch: str) -> None:
    """Trigger the continue-work workflow. Prefers `gh`; otherwise prints
    the exact curl payload and exits gracefully."""
    if shutil.which("gh"):
        _run(
            [
                "gh",
                "workflow",
                "run",
                WORKFLOW_FILE_NAME,
                "--ref",
                branch,
                "-f",
                f"sprint_id={sprint_id}",
                "-f",
                f"handoff_file={handoff_rel}",
                "-f",
                f"branch={branch}",
            ]
        )
        print(">>> Dispatched continue-work via gh.")
        return
    payload = {
        "event_type": "continue-work",
        "client_payload": {
            "sprint_id": sprint_id,
            "handoff_file": handoff_rel,
            "branch": branch,
        },
    }
    print(
        ">>> `gh` not on PATH. To dispatch the workflow manually, either\n"
        "    open the Actions tab and click 'Run workflow' on continue-work,\n"
        "    or POST the payload below to the repository_dispatch endpoint:\n\n"
        f"{json.dumps(payload, indent=2)}\n"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--handoff-file", type=Path, default=DEFAULT_HANDOFF)
    p.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    p.add_argument("--sprint-id", default=None)
    p.add_argument("--sprint-title", default=None)
    p.add_argument("--branch", default=None, help="Branch to push to + check out")
    p.add_argument("--reason", default=None, choices=VALID_REASONS)
    p.add_argument("--reason-note", default=None)
    p.add_argument("--checkpoint-summary", default=None)
    p.add_argument("--continuation-prompt", default=None)
    p.add_argument(
        "--ready-for-continue",
        dest="ready_for_continue",
        default=None,
        type=lambda s: s.lower() in {"1", "true", "yes", "y"},
        help="Set ready_for_continue explicitly (true/false).",
    )
    p.add_argument("--append-completed", action="append", default=[])
    p.add_argument("--append-open", action="append", default=[])
    p.add_argument("--append-next-action", action="append", default=[])
    p.add_argument("--append-guardrail", action="append", default=[])
    p.add_argument("--append-file-to-review", action="append", default=[])
    p.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the handoff file then exit; never edits, commits, pushes, or dispatches.",
    )
    p.add_argument("--commit", action="store_true", help="git add+commit the handoff if it changed.")
    p.add_argument("--push", action="store_true", help="git push -u origin <branch> after commit.")
    p.add_argument(
        "--dispatch",
        action="store_true",
        help="Trigger the continue-work workflow via `gh` (or print payload).",
    )
    return p


def _require_in_repo(path: Path) -> Path:
    """Resolve and assert *path* lives inside REPO_ROOT.

    Used for the ops that must produce a repo-relative path (git
    commit/push and workflow dispatch). Plain validation / file edits
    are allowed against any path so unit tests can use tmpdirs.
    """
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError as e:
        raise SystemExit(
            f"handoff_file must be inside the repo for this operation: {resolved}"
        ) from e
    return resolved


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handoff_path: Path = args.handoff_file.resolve()

    if args.validate_only:
        _validate(handoff_path, args.schema.resolve(), sprint_id=args.sprint_id)
        print(">>> Handoff is valid.")
        return 0

    data = _load_handoff(handoff_path)
    data = _apply_updates(data, args)
    handoff_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # Always re-validate after edits to keep the artifact safe to consume.
    _validate(handoff_path, args.schema.resolve(), sprint_id=args.sprint_id)
    print(">>> Handoff written and validated.")

    if args.commit or args.push:
        repo_path = _require_in_repo(handoff_path)
        branch = args.branch or data.get("branch") or _current_branch()
        if not branch:
            raise SystemExit("Cannot determine branch for commit/push.")
        committed = _git_commit_and_push(repo_path, push=args.push, branch=branch)
        if not committed and args.push:
            print(">>> Nothing committed; skipping push.")

    if args.dispatch:
        repo_path = _require_in_repo(handoff_path)
        sprint_id = args.sprint_id or data.get("sprint_id")
        if not sprint_id:
            raise SystemExit("--dispatch requires sprint_id (CLI flag or in handoff file).")
        branch = args.branch or data.get("branch") or _current_branch()
        rel = str(repo_path.relative_to(REPO_ROOT)).replace("\\", "/")
        _dispatch(sprint_id, rel, branch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
