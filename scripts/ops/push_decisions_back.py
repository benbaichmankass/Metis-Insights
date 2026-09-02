#!/usr/bin/env python3
"""Push COMMITTED decision answers back to the sessions that asked them.

The runner-side half of ``src/runtime/decision_push.py``. Read that module's
docstring first — it owns the three delivery states and why each is distinct.
The feasibility work behind the whole thing, with TESTED / READ / RECORDED
marked per claim, is ``docs/design/decision-push-back-FEASIBILITY.md``.

WHERE IT RUNS, AND WHEN. On the GitHub Actions runner, in
``work-decision-commit.yml``, **after** the committed answers have landed on
``main``. The ordering is load-bearing: pushing before the answer is on ``main``
would tell a session an answer that might still fail to commit, which is the
forward failure the transit contract exists to refuse. So this reads the repo —
the landed truth — and never the transit log.

    # see what would be delivered; NOTHING is sent and nothing is written
    python3 scripts/ops/push_decisions_back.py

    # actually deliver, and write the idempotence markers
    python3 scripts/ops/push_decisions_back.py --apply

**Dry-run is the default**, matching ``commit_work_decisions.py``: this one
sends a message into somebody else's session, and a tool that does that by
default is one bad argument away from waking every session on the account.

⚠️ **IT IS INERT WITHOUT A CREDENTIAL, AND SAYS SO RATHER THAN PASSING.**
``claude -p … --cloud`` needs a claude.ai OAuth token (READ: API keys are not
accepted, and there is no long-lived CI token — the ``user:sessions:claude_code``
scope is capped at 30 days). With the credential env var unset, every candidate
grades ``unknown``, **no marker is written**, and the run reports the channel as
off. It must never grade ``session_gone`` — we did not contact anybody — and it
must never silently report success.

⚠️ **THE PULL PATH IS UNTOUCHED.** ``GET /api/bot/work/decisions`` still grades
``committed`` from the object file exactly as before. This ADDS a push. If every
delivery here fails forever, the system behaves exactly as it did yesterday.

Tier-1: reads YAML, writes a nested ``push:`` mapping under an already-committed
answer, and shells one CLI command. No order path, no config, no VM.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from src.runtime.decision_push import (  # noqa: E402
    DELIVER,
    DELIVERY_STATES,
    PLAN_ACTIONS,
    PUSHED,
    SESSION_GONE,
    SKIP_ASKER_MALFORMED,
    UNKNOWN,
    classify_delivery,
    plan_push,
    render_push_message,
    render_push_yaml_block,
)
from src.runtime.work_decisions import (  # noqa: E402
    atomic_write_text,
    normalise_requests,
)

OBJECTS_DIR = REPO / "docs" / "claude" / "work" / "objects"

# The NAME only. This repo is PUBLIC; the value lives in Actions secrets.
CREDENTIAL_ENV = "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"

# A delivery must not hang a workflow. The CLI posts and exits without waiting
# for a reply, so this is generous rather than tight.
DELIVERY_TIMEOUT_S = 120


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def credential_present() -> bool:
    return bool((os.environ.get(CREDENTIAL_ENV) or "").strip())


def deliver_via_cli(session_id: str, message: str) -> tuple[int | None, str, str]:
    """Post one message into an existing cloud session.

    ``claude -p "<msg>" --cloud <session-id> --output-format json`` — documented
    for exactly this (*"send follow-ups from a CI script"*). It queues the
    message and exits **without waiting for a reply**, which is what makes this
    one-way by construction rather than by our own discipline.

    The session id and the message are passed as **argv**, never through a
    shell, so neither can inject a command. The id has additionally already been
    validated against ``work_decisions.is_session_id`` when it was read.
    """
    cmd = ["claude", "-p", message, "--cloud", session_id, "--output-format", "json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=DELIVERY_TIMEOUT_S, check=False
        )
    except FileNotFoundError:
        # The CLI is not installed on this runner. We did not contact anyone.
        return None, "", "claude CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "", f"delivery timed out after {DELIVERY_TIMEOUT_S}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _iter_objects() -> list[tuple[str, Path, dict[str, Any] | None, str | None]]:
    out: list[tuple[str, Path, dict[str, Any] | None, str | None]] = []
    if not OBJECTS_DIR.exists():
        return out
    for path in sorted(OBJECTS_DIR.glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            out.append((path.stem, path, None, str(exc)))
            continue
        if not isinstance(data, dict):
            out.append((path.stem, path, None, "object file is not a mapping"))
            continue
        out.append((path.stem, path, data, None))
    return out


def _write_push_marker(
    path: Path, data: dict[str, Any], request_id: str, block: dict[str, Any]
) -> None:
    """Nest the ``push:`` block inside the request's already-committed answer.

    Round-trips the YAML like ``commit_work_decisions.py::_write_answer`` does,
    and for the same reason: regex-splicing a nested mapping into hand-formatted
    YAML is how a source of truth gets silently corrupted. It loses comments, so
    it touches only a file it actually changes and the diff is reviewed in a PR.
    """
    for raw in data.get("decision_requests") or []:
        if isinstance(raw, dict) and raw.get("id") == request_id:
            answer = raw.get("answer")
            if isinstance(answer, dict):
                answer["push"] = block
    atomic_write_text(
        path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    )


def run(
    *,
    apply: bool,
    deliver: Callable[[str, str], tuple[int | None, str, str]] = deliver_via_cli,
    has_credential: Callable[[], bool] = credential_present,
    pushed_by: str = "push_decisions_back.py",
) -> dict[str, Any]:
    cred = has_credential()
    results: list[dict[str, Any]] = []
    read_errors: list[dict[str, str]] = []

    for object_id, path, data, err in _iter_objects():
        if err is not None or data is None:
            # Reported, never dropped: an object we could not read is not an
            # object with nothing in it.
            read_errors.append({"object": object_id, "error": err or "unreadable"})
            continue
        for request in normalise_requests(data, object_id):
            plan = plan_push(request)
            if plan["action"] != DELIVER:
                results.append({**plan, "deliveryState": None})
                continue

            session_id = plan["sessionId"]
            message = render_push_message(request)

            if not cred:
                # We did not contact anybody. `unknown`, no marker, retried next
                # run — never `session_gone`.
                results.append({
                    **plan, "deliveryState": UNKNOWN,
                    "detail": "no delivery credential configured", "markerWritten": False,
                })
                continue

            if not apply:
                results.append({
                    **plan, "deliveryState": None,
                    "detail": "dry run — nothing sent", "markerWritten": False,
                    "messagePreview": message.splitlines()[0],
                })
                continue

            rc, out, errout = deliver(session_id, message)
            state, detail = classify_delivery(
                returncode=rc, stdout=out, stderr=errout, credential_present=True
            )

            # Three states, three different things to do with the result.
            if state == PUSHED:
                # Settled. The asking session has been told; never push again.
                outcome = "delivered"
                write_marker = True
            elif state == SESSION_GONE:
                # ALSO settled, and NOT an error. The asking session cannot
                # receive — so we record why nobody was woken and stop trying.
                # The answer remains discoverable exactly as before: this
                # subsystem adds push, it never replaced pull.
                outcome = ("asking session cannot receive; the answer stays "
                           "discoverable on the pull path")
                write_marker = True
            else:
                # UNKNOWN — we could not establish anything. Writing a marker
                # here would permanently suppress retry on the strength of a
                # blip, so we deliberately leave no trace and try again.
                outcome = "not settled — no marker written, will retry"
                write_marker = False

            if write_marker:
                _write_push_marker(
                    path, data, request["id"],
                    render_push_yaml_block(
                        state=state, attempted_at=_now_iso(), session_id=session_id,
                        detail=detail, pushed_by=pushed_by,
                    ),
                )

            results.append({**plan, "deliveryState": state,
                            "detail": f"{detail} — {outcome}",
                            "markerWritten": write_marker})

    action_counts = {a: sum(1 for r in results if r.get("action") == a) for a in PLAN_ACTIONS}
    state_counts = {
        s: sum(1 for r in results if r.get("deliveryState") == s) for s in DELIVERY_STATES
    }
    return {
        "applied": apply,
        "credentialPresent": cred,
        "channelState": "armed" if cred else "off_no_credential",
        "graded": len(results),
        "actionCounts": action_counts,
        "deliveryStateCounts": state_counts,
        "readErrors": read_errors,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually deliver and write markers. Omit for a dry run (the default).")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    payload = run(apply=args.apply)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        mode = "APPLIED" if payload["applied"] else "DRY RUN (nothing sent, nothing written)"
        print(f"push_decisions_back — {mode}")
        # Stated on every run: a channel that is off must say so, or an inert
        # run is indistinguishable from a quiet one.
        print(f"  channel: {payload['channelState']}"
              + ("" if payload["credentialPresent"]
                 else f"  ({CREDENTIAL_ENV} unset — every candidate grades "
                      f"`unknown`, NOT `session_gone`, and is retried)"))
        print(f"  graded : {payload['graded']} request(s)")
        for action, n in payload["actionCounts"].items():
            print(f"    {action:<22} {n}")
        for state, n in payload["deliveryStateCounts"].items():
            print(f"    delivery/{state:<13} {n}")
        for r in payload["readErrors"]:
            print(f"  ! unreadable object {r['object']}: {r['error']}")
        for r in payload["results"]:
            if r.get("action") == "skip_not_committed":
                continue  # the ordinary state of most requests; not worth a line
            detail = f" — {r['detail']}" if r.get("detail") else ""
            state = f" [{r['deliveryState']}]" if r.get("deliveryState") else ""
            print(f"  · {r['objectId']} / {r['requestId']}: {r['action']}{state}{detail}")

    # A malformed asker is a FINDING that needs a human: a question whose answer
    # can never be delivered while looking as though it will be. An unreadable
    # object is the same class. Neither is a reason to fail on `unknown` — a
    # channel that is off, or a blip, is retried by design.
    findings = payload["actionCounts"].get(SKIP_ASKER_MALFORMED, 0) + len(payload["readErrors"])
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
