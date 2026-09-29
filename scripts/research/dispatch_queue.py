#!/usr/bin/env python3
# wiring: .github/workflows/research-queue-dispatch.yml (cron + workflow_dispatch)
"""Read ``research/queue/``, decide, and fire. The R5 scheduler's acting half.

Operator-decided 2026-08-27: the dispatcher **fires** what it routes, including
GPU bursts, relying on the burst workflow's own preflight spend-gate.

⚠️ **WHAT ACTUALLY BOUNDS GPU SPEND, since this fires it unattended.** Measured
from ``comms/gpu_spend_ledger.json`` on 2026-08-27: **7 RunPod runs in 2026-07,
lifetime $0.2164, largest single run $0.0987**, against a **$10/month** cap. So
the adapter is verified and DOES spend — ``gpu-burst-train.yml``'s own header
claimed the opposite until this session corrected it, and it was stale in the
reassuring direction. Two gates hold and neither is in this file:
``scripts/ml/gpu_burst/preflight.py`` aborts when month-to-date + est > the cap,
and the ARM gate needs ``GPU_BURST_ARMED=1`` + ``GPU_PROVIDER``. This dispatcher
adds a third, cheap one — ``--max-gpu-dispatches-per-run`` — because a queue bug
that fires the same job in a loop is the failure this design newly makes
possible, and the ledger cap is a *monthly* bound, not a per-run one.

**DRY-RUN IS THE DEFAULT.** ``--fire`` is opt-in. A scheduler whose default
action is to spend is one typo away from spending; a scheduler whose default is
to print what it would do costs nothing to run wrong.

THE OUTPUT STATES ITS OWN DERIVATION. Every line carries the job id, the two
verdicts, and WHY — never a bare count. ``0 dispatched`` is meaningless without
knowing whether the queue was empty, unreadable, all blocked, or all not-due, and
those four are printed as separate counters for exactly that reason.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research.research_queue import (  # noqa: E402
    BLOCKED_POWER, BLOCKED_ROUTE, DISPATCHED, DISPATCH_FAILED, GPU, INVALID,
    NOT_DUE, grade_power, grade_route, load_queue,
)

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_QUEUE = _REPO / "research" / "queue"

#: How long after a run a cadence is considered satisfied. `once` never repeats.
_CADENCE_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise — never raises.

    `Path.relative_to` raises when the path sits outside the repo, which is a
    legitimate call (`--queue-dir /tmp/...` in a test). A display helper must
    never be the thing that fails a dispatch run.
    """
    try:
        return str(path.resolve().relative_to(_REPO))
    except ValueError:
        return str(path)


def _is_due(entry: Dict[str, Any], now: datetime) -> tuple:
    """(due, reason). A job with no recorded run has never run and IS due."""
    cadence = str(entry.get("cadence") or "once")
    last = entry.get("last_dispatched_at")
    if not last:
        return True, "never dispatched"
    if cadence == "once":
        return False, f"cadence=once and it ran at {last}"
    try:
        when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        # ⚠️ An undateable stamp is NOT "long ago". We cannot show the cadence
        # has elapsed, so we do not fire — the fail-safe direction for a thing
        # that spends money and burns runner minutes.
        return False, f"last_dispatched_at={last!r} is unparseable — refusing to " \
                      "treat an undateable stamp as elapsed"
    gap = timedelta(days=_CADENCE_DAYS.get(cadence, 1))
    if now - when >= gap:
        return True, f"last ran {when.isoformat()}, cadence {cadence} elapsed"
    return False, f"last ran {when.isoformat()}, cadence {cadence} not yet elapsed"


def due_within(entry: Dict[str, Any], now: datetime, hours: float = 0.0) -> bool:
    """Would the dispatcher fire this unit at ``now + hours``? The ONE due rule
    (`_is_due`), exposed so the refill (`queue_replenish.runnable`) and the alarm
    (`queue_grade.health`) count exactly what this dispatcher would fire and
    never a looser notion of "runnable". A `once` unit that already ran and a
    monthly unit that ran yesterday both carry `status: queued` and are NOT due;
    measured 2026-09-29 04:35Z on main they were 18 of the 21 "runnable" units,
    so the refill never fired and the alarm stayed quiet while the runners idled."""
    return bool(_is_due(entry, now + timedelta(hours=hours))[0])


def _stamp(path: Path, when: datetime) -> Optional[str]:
    """Record ``last_dispatched_at`` on the job file. Returns an error or None.

    ⚠️ **THE STAMP IS BOOKKEEPING, AND BOOKKEEPING CAN LOSE A RACE.** `main` is
    branch-protected, so the workflow lands this through the shared
    commit-to-main action as an auto-merge PR rather than a direct push
    (BL-20260706-GPU-BURST-LEDGER-PUSH-RACE is the row that established that).
    Between firing and that PR merging, a second dispatcher run would read the
    OLD stamp and fire the same job again.

    What bounds it today: the cron is daily (so the window is minutes against a
    24 h cadence), the dispatcher is `concurrency`-grouped so two runs cannot
    overlap, and `--max-gpu-dispatches-per-run` caps the only route that costs
    money. What does NOT bound it: nothing stops a same-day double-fire of a
    free runner job if the stamp PR is still open.

    **The stronger fix is result-based idempotence, not a better stamp** — every
    job already declares `lands.store`, so the dispatcher could ask "are this
    job's rows already there?" and skip on the RESULT rather than on
    bookkeeping it has to write. That is deliberately not built here: it needs
    the store readable from the runner and it is a larger change than this PR
    should carry. Tracked, not silently omitted.
    ⚠️ **WRITTEN AS A TARGETED TEXT EDIT, NEVER A YAML ROUND-TRIP.** It used
    ``yaml.safe_dump`` until 2026-08-31, which is lossy in a way nobody sees
    until the prose is gone: PyYAML does not model comments, so a load/dump
    cycle DELETES every ``#`` line and reflows every ``>-`` block scalar into a
    plain or single-quoted one. Measured on the first real stamp
    (PR #10534, run 33340458710): ``RQ-20260827-001.yaml`` went from 2 comments
    to 0, with `question`, `why_not_inferential`, `basis` and `note` all
    reflowed — 27 insertions, 39 deletions for what should be ONE added line.

    Those blocks are the job's REASONING (why it is not inferential, why it
    routes to a runner, what its landing assertion actually proves). Losing
    them silently, inside an auto-merged "chore(...): dispatch stamps (auto)"
    PR nobody reads, is how a queue becomes a set of opaque job names.

    So the write is a line-level edit: replace an existing ``last_dispatched_at:``
    line in place, or append one. Everything else stays byte-for-byte. The file
    is still PARSED first, so a malformed job is still an error rather than a
    file we append to blindly.
    """
    import yaml

    try:
        text = path.read_text()
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            return f"{path.name}: not a YAML mapping"
        stamp = when.replace(microsecond=0).isoformat()
        line = f"last_dispatched_at: '{stamp}'"

        lines = text.splitlines()
        for i, existing in enumerate(lines):
            # Top-level key only: a nested one would be indented.
            if existing.startswith("last_dispatched_at:"):
                lines[i] = line
                break
        else:
            lines.append(line)
        path.write_text("\n".join(lines) + "\n")

        # Read back and CHECK, rather than trusting the edit: the value must
        # parse to what we meant to write. A targeted text edit can produce a
        # file that still parses and says something else.
        back = yaml.safe_load(path.read_text())
        if not isinstance(back, dict) or str(back.get("last_dispatched_at")) != stamp:
            return f"{path.name}: stamp did not read back as written"
        return None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        # NARROW, and the failure is RETURNED rather than swallowed: the caller
        # records it on the decision row and prints a ::warning::. A broad
        # except would also catch a bug in this function and report it as a
        # filesystem problem the reader would then go looking for.
        return f"{type(exc).__name__}: {exc}"



def declared_inputs(workflow: str, *, repo: Path = _REPO) -> Optional[List[str]]:
    """The ``workflow_dispatch.inputs`` keys ``<repo>/.github/workflows/<workflow>``
    declares, or ``None`` when the file cannot be read (not "no inputs" — the
    two are different claims, and only the second is a reason to filter)."""
    path = repo / ".github" / "workflows" / workflow
    try:
        import yaml  # noqa: PLC0415 — optional at import time, required here
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, ValueError, ImportError):
        return None
    # PyYAML parses the bare `on:` key as boolean True.
    on = doc.get("on") if isinstance(doc.get("on"), dict) else doc.get(True)
    if not isinstance(on, dict):
        return None
    wd = on.get("workflow_dispatch")
    inputs = wd.get("inputs") if isinstance(wd, dict) else None
    return sorted(inputs.keys()) if isinstance(inputs, dict) else []


def dispatch_inputs(entry: Dict[str, Any], *, power_state: str = "",
                    repo: Path = _REPO) -> tuple:
    """``(inputs, dropped)`` — what ``gh workflow run -f`` will carry.

    RQ-RUN (2026-09-28), MEASURED on research-queue-dispatch run 36476810004:
    GitHub refuses the whole dispatch with ``HTTP 422: Unexpected inputs
    provided: ["script"]`` when a unit's ``run.inputs`` names a key the
    workflow does not declare — four token-free-runner units carried a
    leftover ``script:`` key from their retarget and none of them fired, while
    the dry run (which never builds the command) said ``would_dispatch``. The
    runner reads the command from the unit FILE, so for it every key beyond
    ``research_unit``/``power_state`` is provably noise: drop it, and NAME it.
    Any other workflow keeps its inputs verbatim — an undeclared key there is
    a unit bug, refused by ``_fire`` before gh is called, in those words.
    """
    run = entry.get("run") or {}
    inputs = dict(run.get("inputs") or {})
    if inputs.get("research_unit") and power_state:
        inputs["power_state"] = power_state
    workflow = str(run.get("workflow"))
    declared = declared_inputs(workflow, repo=repo)
    if declared is None or workflow != "research-script-run.yml":
        return inputs, []
    dropped = sorted(k for k in inputs if k not in declared)
    return {k: v for k, v in inputs.items() if k in declared}, dropped


def _fire(entry: Dict[str, Any], *, route: str, ref: str,
          power_state: str = "") -> tuple:
    """Dispatch via `gh workflow run`. Returns (ok, detail)."""
    run = entry.get("run") or {}
    workflow = str(run.get("workflow"))
    # ⚠️ A `run.workflow` that is not a workflow FILE is a note to a human, not
    # a dispatch target (research/queue/README.md § "DECLARED vs. actually
    # dispatchable"). Until 2026-09-28 this ran `gh workflow run "none — ..."`
    # and reported gh's own error; the outcome is the same DISPATCH_FAILED,
    # but the reason now says what the unit needs (a real runner — see
    # research-script-run.yml) instead of quoting a gh usage message.
    if not workflow.endswith((".yml", ".yaml")) or any(ch.isspace() for ch in workflow):
        return False, (f"run.workflow {workflow[:60]!r} is not a workflow file — a "
                       "session-bound note; retarget it to research-script-run.yml "
                       "(scripts/research/script_run.py) or a real *.yml")
    # RQ-RUN (2026-09-28): the token-free runner reads the unit's command from
    # its YAML. Refuse here what the runner would refuse there, so a bad unit
    # is a DISPATCH_FAILED row instead of a spent runner + a stamped unit.
    if workflow == "research-script-run.yml":
        from scripts.research import script_run
        preflight = script_run.plan(str(entry.get("id")), run_id="preflight")
        if not preflight.ok:
            return False, "research-script-run preflight refused: " + "; ".join(preflight.errors)[:400]
    # ⚠️ THE UNIT DECLARES ITS IDENTITY; THE DISPATCHER SUPPLIES THE VERDICT.
    # `power_state` is deliberately NOT hand-written in the YAML: it is a SAFETY
    # label ("do not read this run's output as a test result"), and a
    # hand-declared safety label can drift from the verdict the gate actually
    # computed — which is the whole class of defect this chain exists to close.
    # So the unit opts in by naming itself, and the COMPUTED state rides along.
    #
    # Injected only when the unit already declares `research_unit`, because
    # `gh workflow run -f <input-the-workflow-never-declared>` ERRORS. Opting in
    # by declaring the identity is the unit asserting its workflow accepts both.
    inputs, dropped = dispatch_inputs(entry, power_state=power_state)
    if dropped:
        print(f"::notice::{entry.get('id')}: run.inputs {dropped} are not declared by "
              f"{workflow} and were not sent — the runner reads the command from the "
              "unit file; drop them from the unit on its next edit", file=sys.stderr)
    declared = declared_inputs(workflow)
    if declared is not None:
        unknown = sorted(k for k in inputs if k not in declared)
        if unknown:
            return False, (f"run.inputs {unknown} are not declared by {workflow} "
                           f"(declared: {declared}) — GitHub would refuse the dispatch "
                           "with HTTP 422; fix the unit's run.inputs")
    cmd = ["gh", "workflow", "run", workflow, "--ref", ref]
    for key, value in inputs.items():
        cmd += ["-f", f"{key}={value}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:400]
    return True, f"{workflow} dispatched on {ref} (route={route})"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue-dir", default=str(_DEFAULT_QUEUE))
    ap.add_argument("--fire", action="store_true",
                    help="actually dispatch; default is a dry run that only reports")
    ap.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME") or "main")
    ap.add_argument("--only", default=None, help="dispatch just this job id")
    ap.add_argument("--max-gpu-dispatches-per-run", type=int, default=1,
                    help="per-RUN cap on GPU bursts; the ledger cap is monthly and "
                         "cannot bound a loop inside one run")
    ap.add_argument("--json", action="store_true", help="emit the decisions as JSON")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    # Resolve BEFORE loading: a relative --queue-dir (how anyone invokes this by
    # hand) otherwise yields relative job paths that blow up the repo-relative
    # display below. An unhandled traceback in a cron dispatcher reads as "the
    # queue is broken" when the truth is "the argument was spelled differently".
    queue_dir = Path(args.queue_dir).resolve()
    jobs, read_error = load_queue(queue_dir)

    # ⚠️ "could not look" is not "nothing to do". Exit non-zero so a broken
    # read can never render as a quiet, successful, empty run.
    if read_error:
        print(f"::error::research-queue: COULD NOT READ the queue at {queue_dir} — {read_error}. "
              f"This is NOT an empty queue.", file=sys.stderr)
        return 2

    decisions: List[Dict[str, Any]] = []
    gpu_fired = 0
    for job in jobs:
        entry = job.raw
        row: Dict[str, Any] = {"id": job.id, "path": _display_path(job.path)}

        if not job.valid:
            row.update(outcome=INVALID, errors=job.errors)
            decisions.append(row)
            continue
        if args.only and job.id != args.only:
            row.update(outcome=NOT_DUE, reason=f"--only={args.only}")
            decisions.append(row)
            continue
        if job.status != "queued":
            row.update(outcome=NOT_DUE, reason=f"status={job.status}")
            decisions.append(row)
            continue

        due, due_reason = _is_due(entry, now)
        if not due:
            row.update(outcome=NOT_DUE, reason=due_reason)
            decisions.append(row)
            continue

        power = grade_power(entry)
        route = grade_route(entry)
        row.update(power_state=power.state, power_reason=power.reason,
                   required_n=power.required_n, expected_n=power.expected_n,
                   route_state=route.state, route_reason=route.reason)

        if not power.runnable:
            row["outcome"] = BLOCKED_POWER
            decisions.append(row)
            continue
        if not route.runnable:
            row["outcome"] = BLOCKED_ROUTE
            decisions.append(row)
            continue
        if route.state == GPU and gpu_fired >= args.max_gpu_dispatches_per_run:
            row.update(outcome=NOT_DUE,
                       reason=f"per-run GPU cap {args.max_gpu_dispatches_per_run} reached")
            decisions.append(row)
            continue

        if not args.fire:
            row.update(outcome="would_dispatch", detail=f"route={route.state} (dry run)")
            decisions.append(row)
            continue

        ok, detail = _fire(entry, route=route.state, ref=args.ref,
                           power_state=power.state)
        row.update(outcome=DISPATCHED if ok else DISPATCH_FAILED, detail=detail)
        if ok:
            # Stamp only a SUCCESSFUL fire. Stamping a failed one would mark the
            # job as run and silently drop it for a whole cadence period.
            stamp_err = _stamp(job.path, now)
            if stamp_err:
                # Report it loudly rather than swallowing: an unstamped job
                # re-fires next run, and that is a fact the reader needs.
                row["stamp_error"] = stamp_err
                print(f"::warning::{job.id} fired but its last_dispatched_at could "
                      f"NOT be stamped ({stamp_err}) — it will re-fire next run",
                      file=sys.stderr)
            if route.state == GPU:
                gpu_fired += 1
        decisions.append(row)

    counts: Dict[str, int] = {}
    for row in decisions:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1

    if args.json:
        print(json.dumps({"generated_at": now.isoformat(), "queue_dir": str(queue_dir),
                          "read_error": None, "jobs_seen": len(jobs),
                          "counts": counts, "decisions": decisions}, indent=2))
    else:
        # Never a bare count: the denominator and every outcome bucket print,
        # so "0 dispatched" can be told from "0 jobs" and from "all blocked".
        print(f"research-queue: {len(jobs)} job file(s) in {queue_dir} "
              f"({'FIRING' if args.fire else 'dry run'})")
        for row in decisions:
            extra = row.get("reason") or row.get("detail") or ""
            if row["outcome"] in (BLOCKED_POWER, BLOCKED_ROUTE):
                extra = row.get("power_reason") if row["outcome"] == BLOCKED_POWER \
                    else row.get("route_reason")
            if row["outcome"] == INVALID:
                extra = "; ".join(row.get("errors") or [])
            print(f"  {row['id']:<18} {row['outcome']:<15} {extra}")
        summary = " · ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        # `"  " + summary or fallback` would be truthy even when summary is
        # empty, so the empty-queue case could never print. Branch on the
        # summary itself.
        print(f"  {summary}" if summary else "  (queue read OK and it is EMPTY — "
                                             "0 jobs, which is not a read failure)")

    # An invalid or failed-to-dispatch job is a non-zero exit: it is work the
    # queue holds and did not do, and a green run would hide it.
    return 1 if counts.get(INVALID) or counts.get(DISPATCH_FAILED) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
