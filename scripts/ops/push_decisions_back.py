#!/usr/bin/env python3
"""The decision push-back DRAIN — the repo's half of delivering a committed answer.

Read ``src/runtime/decision_push.py`` first; it owns the three delivery states.
The design, with PROVEN / NOT PROVEN marked per mechanism, is
``docs/design/decision-push-back-DESIGN.md``.

⚠️ **THIS SCRIPT DELIVERS NOTHING, AND THAT IS THE WHOLE POINT.**
An earlier version shelled ``claude -p --cloud <session-id>`` from a GitHub
runner. The operator ruled that mechanism out on 2026-09-02 — it needs a
claude.ai OAuth credential with **no long-lived CI form** (30-day cap), and
*"we definitely can't have a flow that relies on my minting new tokens every
month"*. So the credential path is GONE rather than left behind a secret that
will never be set: a step wired to a secret nobody will ever mint is the
"looks armed, is not" failure this repo keeps paying for.

WHAT DELIVERS INSTEAD. A Claude session — and only a session, because
``create_trigger(persistent_session_id=…)`` + ``fire_trigger`` are ``mcp__*``
tools a runner does not have. This script gives that session the two things the
REPO must own, and nothing it cannot:

    # 1. WHAT NEEDS PUSHING — the queue, with the message already rendered.
    python3 scripts/ops/push_decisions_back.py --queue

    # 2. RECORD WHAT HAPPENED — after the session fires the Routine.
    python3 scripts/ops/push_decisions_back.py \\
        --record WO-… --request DEC-… --state pushed --detail "trig_…"

    # 3. THE EMPTY RUN IS EVIDENCE — record it even when the queue was empty.
    python3 scripts/ops/push_decisions_back.py --receipt

**Why (3) exists and is not busywork.** A drain that only leaves a trace when
it has work is indistinguishable from one that has silently stopped running —
and this account already carries Routines that are enabled, correct, and have
never fired. The empty receipt is what lets
``scripts/ops/check_drain_liveness.py`` tell *"nothing needed pushing"* apart
from *"the drain is dead"*. It is the same argument
``work-decision-commit.yml`` makes for its own empty runs.

⚠️ **IT ADDS PUSH; IT DOES NOT REPLACE PULL.** ``GET /api/bot/work/decisions``
still grades ``committed`` from the repo exactly as before. If the drain never
runs again, the system behaves exactly as it does today — which is what makes a
dead asker survivable rather than a lost answer.

Tier-1: reads YAML, writes a nested ``push:`` mapping under an
already-committed answer plus a bounded receipt file. No order path, no config,
no VM, no credential.
"""
# wiring: manual-only - run by the hourly decision-drain Routine's fresh
# session, which is created from the web UI or `/schedule` and CANNOT be created
# from this repo (a Routine is not a repo artifact). Deliberately NOT wired to a
# workflow: a GitHub runner has no `mcp__*` tools, so it cannot perform the
# `create_trigger` + `fire_trigger` delivery this script prepares the queue for
# — a workflow caller would run the queue and then be unable to deliver any of
# it. That the Routine does not yet exist is tracked, loudly, as
# OI-20260902-DECISION-DRAIN-ROUTINE-DOES-NOT-EXIST-AND-NOTHING-HAS-EVER-DRAINED,
# whose probe (`scripts/ops/check_drain_liveness.py`) grades `never_ran` until
# it does. See docs/design/decision-push-back-DESIGN.md § 5.
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    plan_push,
    render_push_message,
    render_push_yaml_block,
)
from src.runtime.work_decisions import (  # noqa: E402
    atomic_write_text,
    normalise_requests,
)

OBJECTS_DIR = REPO / "docs" / "claude" / "work" / "objects"

# The receipt. COMMITTED, deliberately: the watcher runs on a GitHub runner
# with no MCP and no VM access, so the only evidence it can possibly read is
# evidence that is in the repo. A receipt on the live VM would be invisible to
# the one thing that has to check it.
RECEIPT_PATH = REPO / "docs" / "claude" / "work" / "DECISION-DRAIN.json"

# Bounded, like MANAGER-LEASE.json: this is a liveness record, not an audit
# trail. The audit trail is the `push:` marker on each answer, which is
# per-decision and never pruned.
RECENT_KEEP = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def build_queue() -> dict[str, Any]:
    """Everything that needs delivering, with the message already rendered.

    The message is rendered HERE rather than by the session, so the *"carry the
    answer, never a pointer"* rule is enforced by the repo instead of trusted to
    whoever writes the Routine prompt. A woken turn has no ``mcp__*`` tools, so
    a message telling it to go read a PR would strand it.
    """
    queue: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    read_errors: list[dict[str, str]] = []

    for object_id, _path, data, err in _iter_objects():
        if err is not None or data is None:
            # Reported, never dropped: an object we could not read is not an
            # object with nothing in it.
            read_errors.append({"object": object_id, "error": err or "unreadable"})
            continue
        for request in normalise_requests(data, object_id):
            plan = plan_push(request)
            if plan["action"] != DELIVER:
                skipped.append(plan)
                continue
            queue.append({
                **plan,
                "message": render_push_message(request),
            })

    action_counts = {a: sum(1 for r in skipped if r.get("action") == a) for a in PLAN_ACTIONS}
    action_counts[DELIVER] = len(queue)
    return {
        "generatedAt": _now_iso(),
        "queue": queue,
        "queueDepth": len(queue),
        "actionCounts": action_counts,
        "readErrors": read_errors,
    }


def _load_receipt() -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not RECEIPT_PATH.exists():
            return None, None
        return json.loads(RECEIPT_PATH.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, str(exc)


def write_receipt(*, queue_depth: int, note: str, delivered: int = 0,
                  session_gone: int = 0, unknown: int = 0) -> dict[str, Any]:
    """Append one bounded run record. THIS is what proves the drain is alive."""
    existing, err = _load_receipt()
    if err:
        # Do not silently start a fresh file over an unreadable one — that would
        # destroy the very history the watcher reads.
        raise OSError(f"receipt file exists and could not be read: {err}")
    doc = existing if isinstance(existing, dict) else {}
    recent = doc.get("recent")
    if not isinstance(recent, list):
        recent = []
    entry = {
        "at": _now_iso(),
        "queued": queue_depth,
        "delivered": delivered,
        "session_gone": session_gone,
        "unknown": unknown,
        "note": note,
    }
    recent.append(entry)
    doc = {
        "_doc": (
            "Liveness receipt for the decision push-back drain. One entry per "
            "RUN, including runs that found nothing to push — an empty run is "
            "the evidence the cadence is alive, and without it 'nothing needed "
            "pushing' and 'the drain is dead' are indistinguishable. Graded by "
            "scripts/ops/check_drain_liveness.py. NOT an audit trail: the "
            "per-decision record is the `push:` block on each committed answer."
        ),
        "schema": 1,
        "last_run_at": entry["at"],
        "runs_recorded": int(doc.get("runs_recorded") or 0) + 1,
        "recent": recent[-RECENT_KEEP:],
    }
    atomic_write_text(RECEIPT_PATH, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return entry


def record_outcome(*, object_id: str, request_id: str, state: str,
                   detail: str | None, pushed_by: str) -> dict[str, Any]:
    """Write the idempotence marker for ONE delivered answer.

    ⚠️ **``unknown`` writes NOTHING and that is deliberate.** A marker suppresses
    every future attempt, so recording an unsettled outcome would permanently
    strand an answer on the strength of a blip. Only a settled outcome —
    delivered, or the platform saying the session cannot receive — is final.
    """
    if state not in DELIVERY_STATES:
        raise ValueError(f"{state!r} is not one of {DELIVERY_STATES}")

    for object_id_seen, path, data, err in _iter_objects():
        if object_id_seen != object_id:
            continue
        if err is not None or data is None:
            raise OSError(f"object {object_id} unreadable: {err}")
        matched = [r for r in normalise_requests(data, object_id) if r["id"] == request_id]
        if not matched:
            raise KeyError(f"{object_id} declares no request {request_id!r}")
        if not matched[0].get("answer"):
            # Refusing here is the transit contract one level up: a push marker
            # on an UNANSWERED question would assert a delivery of nothing.
            raise ValueError(f"{object_id}/{request_id} carries no committed answer")

        if state == UNKNOWN:
            return {"object": object_id, "request": request_id, "state": state,
                    "markerWritten": False,
                    "note": "unsettled — no marker written, will be retried"}

        block = render_push_yaml_block(
            state=state, attempted_at=_now_iso(),
            session_id=(matched[0].get("askedBy") or {}).get("sessionId"),
            detail=detail, pushed_by=pushed_by,
        )
        for raw in data.get("decision_requests") or []:
            if isinstance(raw, dict) and raw.get("id") == request_id:
                answer = raw.get("answer")
                if isinstance(answer, dict):
                    answer["push"] = block
        atomic_write_text(
            path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
        )
        return {"object": object_id, "request": request_id, "state": state,
                "markerWritten": True, "note": "settled — will not be pushed again"}

    raise KeyError(f"no such object: {object_id}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", action="store_true",
                    help="print what needs pushing, with rendered messages (default)")
    ap.add_argument("--record", metavar="OBJECT_ID",
                    help="record a delivery outcome for one request")
    ap.add_argument("--request", metavar="REQUEST_ID")
    ap.add_argument("--state", choices=list(DELIVERY_STATES))
    ap.add_argument("--detail", default=None,
                    help="evidence for the outcome, e.g. the trigger id that fired")
    ap.add_argument("--pushed-by", default="routine:decision-drain")
    ap.add_argument("--receipt", action="store_true",
                    help="record a run that pushed nothing — the empty-run evidence")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.record:
        if not args.request or not args.state:
            print("--record needs --request and --state", file=sys.stderr)
            return 2
        try:
            out = record_outcome(object_id=args.record, request_id=args.request,
                                 state=args.state, detail=args.detail,
                                 pushed_by=args.pushed_by)
        except (OSError, KeyError, ValueError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        write_receipt(
            queue_depth=0, note=f"recorded {args.state} for {args.record}/{args.request}",
            delivered=1 if args.state == PUSHED else 0,
            session_gone=1 if args.state == SESSION_GONE else 0,
            unknown=1 if args.state == UNKNOWN else 0,
        )
        print(json.dumps(out, indent=2) if args.json else
              f"{out['state']}: {out['note']} (marker written: {out['markerWritten']})")
        return 0

    payload = build_queue()

    if args.receipt:
        entry = write_receipt(queue_depth=payload["queueDepth"],
                              note="drain ran; nothing delivered on this pass")
        print(json.dumps(entry, indent=2) if args.json else
              f"receipt recorded at {entry['at']} (queue depth {entry['queued']})")
        return 0

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"push_decisions_back — queue depth {payload['queueDepth']}")
        for action, n in payload["actionCounts"].items():
            print(f"    {action:<22} {n}")
        for r in payload["readErrors"]:
            print(f"  ! unreadable object {r['object']}: {r['error']}")
        for item in payload["queue"]:
            print(f"\n  ── {item['objectId']} / {item['requestId']} "
                  f"→ {item['sessionId']}\n")
            print("\n".join("     " + ln for ln in item["message"].splitlines()))
        if not payload["queue"]:
            print("\n  Nothing to push. Record the empty run with --receipt so "
                  "'nothing to do' stays distinguishable from 'the drain died'.")

    # A malformed asker is a FINDING that needs a human — a question whose
    # answer can never be delivered while looking as though it will be.
    findings = payload["actionCounts"].get(SKIP_ASKER_MALFORMED, 0) + len(payload["readErrors"])
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
