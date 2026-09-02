#!/usr/bin/env python3
"""Commit submitted decision answers from the transit log into the work store.

**This is the half that makes a submitted answer TRUE.** The SPA's
``POST /api/bot/work/decision`` appends a submission to the live layer; nothing
is decided until that answer is written into the work object in the repo,
because ``operating-layer-schema-and-state-DESIGN.md`` § 2 puts truth in the
repo and observations/transit in the live layer. Until this script (or a
session doing the same thing by hand) runs, the question reads **unanswered**
and its transit window is OPEN and visible on ``GET /api/bot/work/decisions``.

⚠️ **A writer with no committer is worse than no writer.** It would leave the
operator believing they had answered while the store said nobody had — the
forward failure the transit contract exists to refuse. So this ships in the same
change as the route, not after it.

WHERE IT RUNS. The transit log lives on the live VM; the work store lives in the
repo. So the two inputs are given separately and neither is fetched implicitly:

    # 1. pull the transit log off the VM (read-only, existing relay)
    scripts/ops/diag_fetch.sh '/api/diag/log_file?name=work_decision_transit&lines=1000' \\
      | python3 -c 'import json,sys; print("\\n".join(json.load(sys.stdin)["lines"]))' \\
      > /tmp/transit.jsonl

    # 2. see what would change — NOTHING is written
    python3 scripts/ops/commit_work_decisions.py --transit /tmp/transit.jsonl

    # 3. apply, then commit the changed object files as a normal PR
    python3 scripts/ops/commit_work_decisions.py --transit /tmp/transit.jsonl --apply

**Dry-run is the default**, deliberately: this edits the state of record for
work, and a tool that writes by default is one bad path argument away from
stamping answers onto the wrong objects.

WHAT IT REFUSES, and why each refusal is the safe direction:

* **An object it cannot parse** — skipped, reported. Writing into a file we
  could not read would corrupt the source of truth.
* **A request that already carries an ``answer``** — skipped as ``already_committed``.
  A committed decision is changed by editing the repo deliberately, never by a
  replay of the transit log.
* **A ``chosen`` that is not one of the request's declared options** — refused.
  The route validates this too; validating it again here is not redundant, it is
  the check that survives a transit log edited by hand or replayed from an older
  schema.
* **A submission naming an object or request that does not exist** — refused as
  ``orphan``. Reported rather than dropped: an answer the operator gave that
  matches nothing is a finding, not noise.

⚠️ **It never DELETES from the transit log.** The log is the audit trail of what
was submitted and when; committedness is read from the repo, so a committed row
left in the log is not a duplicate risk — re-running is idempotent because the
second pass sees the ``answer`` block and skips it. Pruning would destroy the
evidence that the round-trip worked.

Tier-1 tooling: reads a log, edits YAML under ``docs/claude/work/objects/``,
touches no order path, no config, no VM.
"""
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

from src.runtime.work_decisions import (  # noqa: E402
    atomic_write_text,
    latest_submissions,
    normalise_requests,
    read_transit,
    render_answer_yaml_block,
)

OBJECTS_DIR = REPO / "docs" / "claude" / "work" / "objects"

# Every outcome a submission can reach. Reported per row and counted, so a run
# that commits nothing says WHY rather than printing a bare zero.
OUTCOMES = (
    "committed",
    "already_committed",
    "orphan_object",
    "orphan_request",
    "object_unreadable",
    "invalid_option",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_object(object_id: str) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    for suffix in (".yaml", ".yml"):
        path = OBJECTS_DIR / f"{object_id}{suffix}"
        try:
            resolved = path.resolve()
            resolved.relative_to(OBJECTS_DIR.resolve())
        except (ValueError, OSError):
            return None, None, "path escapes the objects directory"
        if not resolved.exists():
            continue
        try:
            data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return None, resolved, str(exc)
        if not isinstance(data, dict):
            return None, resolved, "object file is not a mapping"
        return data, resolved, None
    return None, None, None


def _write_answer(
    path: Path, data: dict[str, Any], request_id: str, answer: dict[str, Any]
) -> None:
    """Write the answer back, preserving the rest of the file's content.

    ⚠️ This round-trips the YAML rather than splicing text. That LOSES COMMENTS,
    which is a real cost on a store whose files carry load-bearing ⚠️ warnings in
    comments — so it is applied ONLY to a file the committer actually changes,
    and the diff is expected to be reviewed in a PR like any other. The
    alternative (regex-splicing a nested mapping into hand-formatted YAML) is
    how a source of truth gets silently corrupted.
    """
    for raw in data.get("decision_requests") or []:
        if isinstance(raw, dict) and raw.get("id") == request_id:
            raw["answer"] = answer
    atomic_write_text(
        path,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--transit",
        required=True,
        help="path to the transit JSONL pulled off the live VM",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually write. Omit for a dry run (the default).",
    )
    ap.add_argument("--committed-by", default="commit_work_decisions.py")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    rows, state, error = read_transit(Path(args.transit))
    if state == "unreadable":
        # ⚠️ NOT "nothing to commit". We could not look.
        print(f"transit log unreadable: {error}", file=sys.stderr)
        return 2
    if state == "absent":
        print(f"transit log absent at {args.transit} — nothing has been submitted.")
        return 0

    results: list[dict[str, Any]] = []
    for (object_id, request_id), sub in sorted(latest_submissions(rows).items()):
        data, path, err = _load_object(object_id)
        if err:
            results.append({"object": object_id, "request": request_id,
                            "outcome": "object_unreadable", "detail": err})
            continue
        if data is None or path is None:
            results.append({"object": object_id, "request": request_id,
                            "outcome": "orphan_object", "detail": "no such object file"})
            continue

        matched = [
            r for r in normalise_requests(data, object_id) if r["id"] == request_id
        ]
        if not matched:
            results.append({"object": object_id, "request": request_id,
                            "outcome": "orphan_request",
                            "detail": "the object declares no such request"})
            continue
        req = matched[0]
        if req.get("answer"):
            results.append({"object": object_id, "request": request_id,
                            "outcome": "already_committed", "detail": None})
            continue

        chosen = sub.get("chosen")
        keys = {o["key"] for o in req["options"]}
        if chosen is not None and keys and chosen not in keys:
            results.append({"object": object_id, "request": request_id,
                            "outcome": "invalid_option",
                            "detail": f"{chosen!r} not in {sorted(keys)}"})
            continue

        answer = render_answer_yaml_block(
            chosen=chosen,
            free_text=sub.get("free_text"),
            answered_at=str(sub.get("submitted_at") or _now_iso()),
            answered_by=str(sub.get("submitted_by") or "operator"),
            committed_by=args.committed_by,
            submission_id=str(sub.get("submission_id") or ""),
        )
        if args.apply:
            _write_answer(path, data, request_id, answer)
        results.append({"object": object_id, "request": request_id,
                        "outcome": "committed", "detail": chosen,
                        "written": bool(args.apply)})

    counts = {o: sum(1 for r in results if r["outcome"] == o) for o in OUTCOMES}
    payload = {
        "applied": bool(args.apply),
        "transitState": state,
        "submissionsGraded": len(results),
        "counts": counts,
        "results": results,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        mode = "APPLIED" if args.apply else "DRY RUN (nothing written)"
        print(f"commit_work_decisions — {mode}")
        print(f"  transit: {args.transit} ({len(rows)} row(s), state={state})")
        print(f"  graded : {len(results)} submission(s)")
        for outcome, n in counts.items():
            print(f"    {outcome:<20} {n}")
        for r in results:
            detail = f" — {r['detail']}" if r.get("detail") else ""
            print(f"  · {r['object']} / {r['request']}: {r['outcome']}{detail}")
        if not args.apply and counts["committed"]:
            print("\n  Re-run with --apply to write, then open a PR with the changed "
                  "object files. Nothing is decided until that lands on main.")
    # A refusal that needs a human is a non-zero exit so a workflow cannot
    # report a clean run over an orphan or an invalid option.
    return 1 if (counts["orphan_object"] or counts["orphan_request"]
                 or counts["object_unreadable"] or counts["invalid_option"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
