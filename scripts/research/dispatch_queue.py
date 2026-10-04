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
import functools
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research.research_queue import (  # noqa: E402
    BLOCKED_POWER, BLOCKED_ROUTE, DISPATCHED, DISPATCH_FAILED, GPU, INVALID,
    NOT_DUE, grade_power, grade_route, load_queue, load_themes,
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


def precondition_met(entry: Dict[str, Any], root: Optional[Path] = None) -> tuple:
    """(met, reason) for the structured `requires_result: {unit, verdict?}` field: at least one MEASURED
    record under research/results/<unit>/*.jsonl (and, when `verdict` is given, one carrying it). A unit
    without the field is always met. The free-text `dispatch_precondition` is prose for humans and is
    NOT evaluated (research/queue/README.md): an unevaluated precondition reads as a gate and is not."""
    req = entry.get("requires_result")
    if not req:
        return True, "no precondition"
    unit, want = str(req.get("unit")), req.get("verdict")
    rdir = (root or _REPO) / "research" / "results" / unit
    seen = 0
    for f in sorted(rdir.glob("*.jsonl")) if rdir.is_dir() else []:
        try:
            rows = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except (OSError, ValueError):
            continue
        for r in rows:
            if str(r.get("read_state")) == "measured":
                seen += 1
                if want is None or str(r.get("verdict")) == str(want):
                    return True, f"{unit} landed a measured record" + (f" with verdict {want}" if want else "")
    return False, (f"precondition unmet: needs a measured record from {unit}"
                   + (f" with verdict {want}" if want else "") + f" (found {seen} measured record(s) without it)")


#: Values that mean "a human has not decided this yet". A unit carrying one must never reach `gh`:
#: the string is sent verbatim as an input value and lands in a numeric parameter.
_PLACEHOLDER_RE = re.compile(r"NOT YET DECIDED|\bTBD\b|\bTODO\b|\bFIXME\b", re.I)


@functools.lru_cache(maxsize=1)
def _e35_sweepable_legs() -> frozenset:
    """The legs e35-bracket-sweep's own planner would accept -- the sweep's resolver
    (`plan_legs`), never a second copy of its scope. Config-only: `ignore_missing_data`."""
    from scripts.research.e35_bracket_geometry_sweep import plan_legs
    runnable, _ = plan_legs(Path("data"), None, 0.099, ignore_missing_data=True)
    return frozenset(r["leg"] for r in runnable)


def _e35_scope_problem(workflow: str, inputs: Dict[str, Any], repo: Optional[Path]) -> Optional[str]:
    """A unit naming an e35 leg the planner refuses is DETERMINISTICALLY doomed: the run dies in
    its `plan` job (~90 s, "shard-plan produced ZERO jobs") AFTER the dispatcher stamped the
    unit, so it reads as dispatched while the corpus never grows. MEASURED 2026-10-04:
    RQ-20260929-005 / RQ-20261002-001 / RQ-20261003-001 / RQ-20261003-006 name `ict_scalp_*`
    legs (family `scalp`, out_of_scope_family) -- runs 37086544045 and 37106772916, red in `plan`."""
    if workflow.split("/")[-1] != "e35-bracket-sweep.yml" or (repo is not None and repo != _REPO):
        return None
    only = [x.strip() for x in str(inputs.get("only") or "").split(",") if x.strip()]
    if not only:
        return None
    try:
        sweepable = _e35_sweepable_legs()
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        # could not look: never doom a unit on an unreadable scope -- but say so
        print(f"::warning::e35 scope unreadable ({type(exc).__name__}: {exc}); not applying the scope check",
              file=sys.stderr)
        return None
    out = sorted(x for x in only if x not in sweepable)
    if out:
        return (f"misconfigured: run.inputs.only {out} are outside the e35 sweep's scope "
                "(its planner refuses them as out_of_scope_family) -- the run would fail in `plan`")
    return None


def config_problem(entry: Dict[str, Any], repo: Optional[Path] = None) -> Optional[str]:
    """Why this unit's dispatch is DETERMINISTICALLY doomed, or None.

    Two shapes, both measured on research-queue-dispatch run 36798175014 (2026-10-01): RQ-20260928-010
    named inputs its workflow does not declare (HTTP 422 on every cycle, which reddened the run) and
    carried `fee_frac: "NOT YET DECIDED ..."`. A doomed unit is not due: it reports `not_due` with this
    reason instead of failing the whole run each cycle, and the runnable count (queue health, refill)
    stops counting it. The fix belongs in the unit, so the reason names it."""
    run = entry.get("run") or {}
    workflow = str(run.get("workflow") or "")
    if not workflow.endswith((".yml", ".yaml")) or any(ch.isspace() for ch in workflow):
        return None   # session-bound: reported separately
    bad = [k for k, v in (run.get("inputs") or {}).items()
           if isinstance(v, str) and _PLACEHOLDER_RE.search(v)]
    if bad:
        return f"misconfigured: run.inputs {bad} hold an unresolved placeholder ('NOT YET DECIDED'/TBD/TODO)"
    declared = declared_inputs(workflow, repo=repo or _REPO)
    if declared is None or workflow.split("/")[-1] == "research-script-run.yml":
        return None
    inputs, _ = dispatch_inputs(entry, power_state="x", repo=repo or _REPO)
    unknown = sorted(k for k in inputs if k not in declared)
    if unknown:
        return (f"misconfigured: run.inputs {unknown} are not declared by {workflow.split('/')[-1]} "
                f"(declared: {declared}); GitHub would refuse the dispatch with HTTP 422")
    return None


def _is_due(entry: Dict[str, Any], now: datetime) -> tuple:
    """(due, reason). A job with no recorded run has never run and IS due."""
    met, why = precondition_met(entry)
    if not met:
        return False, why
    problem = config_problem(entry)
    if problem:
        return False, problem
    # Scope is a different class from a misconfigured input: the unit is well-formed and
    # the workflow would accept it, then refuse the LEG. Kept out of config_problem so the
    # "committed queue has no doomed unit" test keeps meaning "no malformed unit"; the
    # out-of-scope units already on main are listed for retirement, not hidden.
    scope = _e35_scope_problem(str((entry.get("run") or {}).get("workflow") or ""),
                               (entry.get("run") or {}).get("inputs") or {}, None)
    if scope:
        return False, scope
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


def _waiting_since(entry: Dict[str, Any], uid: str) -> Optional[datetime]:
    """When the unit started waiting: its last stamp, else the date in its id."""
    last = entry.get("last_dispatched_at")
    if last:
        try:
            when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    m = re.match(r"^RQ-(\d{4})(\d{2})(\d{2})-", uid)
    return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc) if m else None


def effective_priority(entry: Dict[str, Any], uid: str, now: datetime, aging_hours: float) -> int:
    """priority minus one step per `aging_hours` waited (floor 0): nothing starves inside its theme."""
    prio = entry.get("priority")
    prio = prio if isinstance(prio, int) and not isinstance(prio, bool) else 3
    since = _waiting_since(entry, uid)
    steps = int(max(0.0, (now - since).total_seconds()) // (aging_hours * 3600)) if since else 0
    return max(0, prio - steps)


def fair_order(jobs: List[Any], now: datetime, themes_doc: Dict[str, Any]) -> List[Any]:
    """Dispatch order: weighted fair share across themes, aged priority within a theme.

    Repeatedly pick the theme with the lowest (fired in the last `share_window_hours` + picked so far)
    / weight, among themes that still hold a due job, and take that theme's best job (lowest effective
    priority, then id). Stateless: the only memory is the stamps already on the units. A theme with
    runnable work and no recent fire has usage 0, so it is served before any theme over its share:
    low weight means fewer slots, never none. Jobs that are not due (done, cadence not elapsed, unmet
    precondition, session-bound, invalid) keep id order after the candidates, unchanged."""
    themes = themes_doc["themes"]
    window = timedelta(hours=float(themes_doc["share_window_hours"]))
    aging = float(themes_doc["aging_hours"])
    used: Dict[str, float] = {t: 0.0 for t in themes}
    for j in jobs:
        ts = _waiting_since({"last_dispatched_at": j.raw.get("last_dispatched_at")}, "") \
            if j.raw.get("last_dispatched_at") else None   # a stamp, never the id-date fallback
        th = str(j.raw.get("theme") or "")
        if ts and now - ts <= window and th in used:
            used[th] += 1
    cand: Dict[str, List[Any]] = {}
    rest: List[Any] = []
    for j in sorted(jobs, key=lambda x: x.id):
        e = j.raw
        th = str(e.get("theme") or "")
        wf = str((e.get("run") or {}).get("workflow") or "")
        fireable = (j.valid and j.status == "queued" and th in themes and wf.endswith((".yml", ".yaml"))
                    and not any(ch.isspace() for ch in wf) and _is_due(e, now)[0])
        (cand.setdefault(th, []) if fireable else rest).append(j)
    for th in cand:
        # Within one effective priority a hand-written unit (someone ASKED this question) goes
        # before a template-generated one (the refill keeps the queue stocked): MEASURED
        # 2026-10-04, RQ-20260929-101..108 -- eight pre-registered Stage-0 units the operator
        # asked for -- sat behind ~15 generated prop-fit units in the same theme for five days,
        # because ids sort by creation and the 09-29 replenish batch numbered 025..060 < 101.
        cand[th].sort(key=lambda x: (effective_priority(x.raw, x.id, now, aging),
                                     isinstance(x.raw.get("generated"), dict), x.id))
    out: List[Any] = []
    while any(cand.values()):
        th = min((t for t in cand if cand[t]),
                 key=lambda t: (used[t] / float(themes[t]["weight"]), -float(themes[t]["weight"]), t))
        out.append(cand[th].pop(0))
        used[th] += 1
    return out + rest


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
    # A unit may spell the workflow as `.github/workflows/x.yml` (RQ-20260928-010 did). Joining that
    # onto the workflows dir gave a path that never exists, so this returned None, the undeclared-inputs
    # preflight in `_fire` was skipped, and gh answered HTTP 422 on every cycle (run 36798175014).
    path = repo / ".github" / "workflows" / workflow.split("/")[-1]
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
    workflow = str(run.get("workflow"))
    declared = declared_inputs(workflow, repo=repo)
    # A unit that forgot `research_unit` in its inputs lands its result under
    # research/results/_unattributed/ -- MEASURED 2026-10-04: RQ-20260928-006/-007 fired
    # m20-exit-lever-sweep runs 36434140504 / 36434144288, which sit in _unattributed/ although
    # their unit ids were known to the dispatcher. The dispatcher knows the id, so when the
    # workflow declares the input it supplies it (declared_inputs None = unreadable: never guess).
    if declared and "research_unit" in declared and not inputs.get("research_unit") and entry.get("id"):
        inputs["research_unit"] = str(entry["id"])
    if inputs.get("research_unit") and power_state:
        inputs["power_state"] = power_state
    if declared is None or workflow != "research-script-run.yml":
        return inputs, []
    dropped = sorted(k for k in inputs if k not in declared)
    return {k: v for k, v in inputs.items() if k in declared}, dropped



# ── backpressure (manager review 2026-09-29, 01:27Z) ────────────────────────
# MEASURED at 01:27Z on 2026-09-29: 67 runs queued repo-wide and 12 research
# compute runs in progress at once (6 research-script-run, 2 harness, 2 m20,
# 1 e35, 1 macro backfill) after the dispatcher fanned the whole queue out in
# one cycle. Every system-action -- set-env, flatten, the Breakout AUTO-REVERT
# -- waited in the same runner pool. Research must never starve a safety
# action, so a fire is DEFERRED (not failed, not stamped) when research
# compute already holds `--max-research-inflight` runs or the repo already has
# more than `--max-repo-queued` runs waiting. A deferred unit is due again on
# the next cycle exactly as if it had never been looked at.
DEFERRED = "deferred"

# ── same-workflow serialization (PI-20260929-RQRUN-E35CORPUS-0001) ─────────
# MEASURED 2026-09-29 00:53Z: one cycle fired three e35-bracket-sweep units.
# e35-bracket-sweep.yml declares `concurrency: {group: e35-bracket-sweep,
# cancel-in-progress: false}`, and GitHub keeps ONE pending run per group, so
# the third dispatch CANCELLED the second (RQ-20260928-017 ran nothing, stayed
# stamped, and would never have re-fired). The two survivors then each
# rewrote docs/research/e35-bracket-corpus.jsonl from their own checkout and
# their landing PRs (#13910, #13801) stranded CONFLICTING. m20-exit-lever-
# sweep has no group but the same whole-file corpus rewrite. So a cycle fires
# at most `--max-fires-per-workflow` unit(s) per workflow FILE; the rest are
# DEFERRED to the next cycle, unstamped. Only workflows that can collide are
# serialized (see serialized_workflow()): a per-run or per-unit concurrency
# group -- the harness dispatcher, the exit-head build, the token-free runner
# whose results land in ONE batch PR -- fans out as before.
#: Workflows whose runs collide even without a constant concurrency group:
#: they rewrite a whole corpus file and land it via commit-to-main.
SERIALIZED_WORKFLOWS = frozenset({"e35-bracket-sweep.yml", "m20-exit-lever-sweep.yml",
                                  "macro-valuation-backfill.yml"})
_CONCURRENCY_GROUP_RE = re.compile(r"^concurrency:\s*\n(?:[ \t]+.*\n)*?[ \t]+group:[ \t]*(.+?)[ \t]*$", re.M)


def serialized_workflow(wf_file: str, repo: Path = _REPO) -> bool:
    """Should the dispatcher fire at most one unit of this workflow per cycle?
    Yes when it is a known corpus rewriter (SERIALIZED_WORKFLOWS) or its file
    declares a CONSTANT `concurrency.group` (no `${{ ... }}`), because GitHub
    keeps one pending run per group and cancels the rest. A per-run group
    (`${{ github.run_id }}`, a per-unit input) never cancels, so those
    workflows -- the harness dispatcher, the exit-head build, the token-free
    runner -- fan out as before."""
    name = wf_file.split("/")[-1]
    if name in SERIALIZED_WORKFLOWS:
        return True
    try:
        text = (repo / ".github" / "workflows" / name).read_text(encoding="utf-8")
    except OSError:
        return False
    m = _CONCURRENCY_GROUP_RE.search(text)
    return bool(m) and "${{" not in m.group(1)


def research_workflow_names(jobs: List[Any], repo: Path = _REPO) -> Dict[str, str]:
    """{workflow file -> its `name:`} for every real workflow the queue routes
    to, plus the token-free runner. A run counts as research compute when its
    workflowName is one of these names (gh run list reports names, not files)."""
    files = {"research-script-run.yml"}
    for job in jobs:
        wf = str(((getattr(job, "raw", None) or {}).get("run") or {}).get("workflow") or "")
        if wf.endswith((".yml", ".yaml")) and not any(ch.isspace() for ch in wf):
            files.add(wf.split("/")[-1])
    out: Dict[str, str] = {}
    for f in sorted(files):
        path = repo / ".github" / "workflows" / f
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("name:"):
                out[f] = line.split(":", 1)[1].strip().strip("\"'")
                break
    return out


def gh_runs(status: str, *, limit: int = 200) -> Optional[List[Dict[str, Any]]]:
    """`gh run list` rows for one status, or None when gh could not answer --
    a count we could not take is reported as such, never as zero."""
    cmd = ["gh", "run", "list", "--status", status, "--limit", str(limit),
           "--json", "databaseId,workflowName,status,createdAt"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


#: A run still `queued` or `in_progress` this long after it was created is not
#: waiting for a hosted runner -- it is STUCK (an `issues`-triggered job whose
#: label no runner serves, a lost runner) and adds nothing to the pool's load.
#: MEASURED 2026-09-29 06:56Z on the first fired cycle after #13907: every one
#: of the 36 "queued" runs was created on 2026-05-15, and the "3 research runs
#: in flight" were three `trainer-vm-diag` issue dispatches from the same
#: morning. Counting them deferred every fire, and would have forever.
STALE_PRESSURE_HOURS = 24.0


def _run_age_hours(row: Dict[str, Any], now: datetime) -> Optional[float]:
    """Hours since the run was created, or None when createdAt is unreadable."""
    raw = row.get("createdAt")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 3600.0


def backpressure(research_names: Dict[str, str], *, max_inflight: int, max_queued: int,
                 runs_by_status: Optional[Dict[str, Optional[List[Dict[str, Any]]]]] = None,
                 now: Optional[datetime] = None, stale_after_hours: float = STALE_PRESSURE_HOURS
                 ) -> Dict[str, Any]:
    """What the runner pool looks like before this cycle fires anything.

    Returns {inflight_research, repo_queued, stale_ignored, block, detail}.
    `block` is None when firing may proceed (subject to the per-fire count),
    else a reason. A status gh could not list is a BLOCK with that reason: the
    conservative direction for research spend is to wait one cycle.

    A run created more than `stale_after_hours` ago and still queued / in
    progress is STUCK, not load (see STALE_PRESSURE_HOURS): it is reported in
    `stale_ignored` and counted against neither cap. A run whose `createdAt`
    cannot be read counts as fresh -- the conservative direction."""
    if runs_by_status is None:
        runs_by_status = {st: gh_runs(st) for st in ("queued", "in_progress")}
    now = now or datetime.now(timezone.utc)
    names = set(research_names.values())
    unreadable = [st for st, rows in runs_by_status.items() if rows is None]
    if unreadable:
        return {"inflight_research": None, "repo_queued": None, "stale_ignored": None,
                "block": f"could not count {'/'.join(unreadable)} runs via gh -- deferring every fire this cycle"}

    def fresh(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            age = _run_age_hours(r, now)
            if age is None or age <= stale_after_hours:
                out.append(r)
        return out

    queued_all = list(runs_by_status.get("queued") or [])
    inprog_all = list(runs_by_status.get("in_progress") or [])
    queued, inprog = fresh(queued_all), fresh(inprog_all)
    stale = (len(queued_all) - len(queued)) + (len(inprog_all) - len(inprog))
    inflight = sum(1 for r in queued + inprog if str(r.get("workflowName")) in names)
    out = {"inflight_research": inflight, "repo_queued": len(queued), "stale_ignored": stale, "block": None,
           "detail": f"research compute in flight {inflight} (cap {max_inflight}); repo runs queued "
                     f"{len(queued)} (cap {max_queued}); {stale} stuck run(s) older than "
                     f"{stale_after_hours:g} h ignored"}
    if len(queued) > max_queued:
        out["block"] = (f"{len(queued)} workflow runs are queued repo-wide (> {max_queued}): research "
                        "must not add to a saturated runner pool -- deferring every fire this cycle")
    elif inflight >= max_inflight:
        out["block"] = (f"{inflight} research compute run(s) already queued/in progress (cap "
                        f"{max_inflight}) -- deferring every fire this cycle")
    return out


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
    ap.add_argument("--max-research-inflight", type=int, default=3,
                    help="defer every fire while this many research compute runs are already "
                         "queued or in progress (counted via gh; see backpressure())")
    ap.add_argument("--pressure-stale-hours", type=float, default=STALE_PRESSURE_HOURS,
                    help="a run still queued/in progress this many hours after creation is stuck, "
                         "not load, and counts against neither cap (see STALE_PRESSURE_HOURS)")
    ap.add_argument("--max-repo-queued", type=int, default=10,
                    help="defer every fire while more than this many workflow runs are queued "
                         "repo-wide (research must never starve a system-action)")
    ap.add_argument("--max-fires-per-workflow", type=int, default=1,
                    help="fire at most this many units per workflow FILE per cycle; the rest are "
                         "deferred (same-workflow runs cancel each other in a concurrency group "
                         "and collide on whole-file corpus rewrites). research-script-run.yml "
                         "is exempt: per-unit concurrency, batch-landed results")
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

    # Fire order: weighted fair share across themes (research/THEMES.yaml), aged priority within
    # a theme. load_queue returns files sorted by id, which made this a pure FIFO: a cycle fires at
    # most --max-research-inflight units, so the newest ids waited behind every older one.
    try:
        jobs = fair_order(jobs, now, load_themes())
    except (OSError, ValueError) as exc:
        print(f"::error::research-queue: COULD NOT READ research/THEMES.yaml — {exc}. Falling back "
              "to id order; this is a broken scheduler, not a quiet one.", file=sys.stderr)

    decisions: List[Dict[str, Any]] = []
    # Backpressure is read ONCE per cycle, then every successful fire counts
    # against the in-flight cap so one cycle cannot fan the whole queue out.
    pressure: Dict[str, Any] = {"block": None, "inflight_research": 0, "detail": "dry run: not counted"}
    if args.fire:
        pressure = backpressure(research_workflow_names(jobs), max_inflight=args.max_research_inflight,
                                max_queued=args.max_repo_queued,
                                stale_after_hours=args.pressure_stale_hours)
        print(f"backpressure: {pressure.get('detail') or pressure.get('block')}", file=sys.stderr)
        if pressure["block"]:
            print(f"::notice::research-queue-dispatch deferred every fire: {pressure['block']}", file=sys.stderr)
    inflight = int(pressure.get("inflight_research") or 0)
    gpu_fired = 0
    fired_by_workflow: Dict[str, int] = {}
    for job in jobs:
        entry = job.raw
        row: Dict[str, Any] = {"id": job.id, "path": _display_path(job.path),
                               "theme": entry.get("theme"), "priority": entry.get("priority")}

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

        # A session-bound note (`run.workflow` is prose, not a workflow file)
        # can never be fired. Until 2026-09-30 it reached _fire(), returned
        # DISPATCH_FAILED on EVERY cycle and turned each firing run red
        # ("Fail the job if grading reported a problem"), while reading as
        # would_dispatch in a dry run. It is not due for THIS dispatcher.
        _wf = str((entry.get("run") or {}).get("workflow") or "")
        if not _wf.endswith((".yml", ".yaml")) or any(ch.isspace() for ch in _wf):
            row.update(outcome=NOT_DUE,
                       reason=f"session-bound: run.workflow {_wf[:40]!r} is not a workflow file "
                              "(retarget to research-script-run.yml to make it dispatchable)")
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
        if pressure["block"]:
            row.update(outcome=DEFERRED, reason=pressure["block"])
            decisions.append(row)
            continue
        if inflight >= args.max_research_inflight:
            row.update(outcome=DEFERRED,
                       reason=f"research compute cap {args.max_research_inflight} reached this cycle "
                              f"({inflight} in flight incl. fires above) -- due again next cycle")
            decisions.append(row)
            continue
        wf_file = str((entry.get("run") or {}).get("workflow") or "").split("/")[-1]
        if serialized_workflow(wf_file) and fired_by_workflow.get(wf_file, 0) >= args.max_fires_per_workflow:
            row.update(outcome=DEFERRED,
                       reason=f"{wf_file} already fired {fired_by_workflow[wf_file]} unit(s) this cycle "
                              f"(cap {args.max_fires_per_workflow}): same-workflow runs cancel each other "
                              "in its concurrency group and collide on the corpus landing -- due again next cycle")
            decisions.append(row)
            continue

        ok, detail = _fire(entry, route=route.state, ref=args.ref,
                           power_state=power.state)
        if ok:
            inflight += 1
            fired_by_workflow[wf_file] = fired_by_workflow.get(wf_file, 0) + 1
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
