#!/usr/bin/env python3
"""FRESHNESS BELONGS IN CI, NOT ON A TIMER — because a timer can be the thing that did not fire.

THE REGRESS THE OBVIOUS FIX CONTAINS, AND WE HAVE ALREADY PAID FOR IT
---------------------------------------------------------------------
Three silent-check incidents in the week of 2026-09-22 share one shape: a check
reported a passing or quiet state about a thing it could no longer see. The
natural fix — *"make every check emit a receipt, and have something grade receipt
freshness"* — **is already built, already armed, and is instance #3.**
`scripts/ci/check_manager_queue_watch.py` IS that design, and the Routine it
grades has fired ~479 times over 479 hours and written **ZERO** receipts. The
receipt-grader was the thing that never ran.

So adding receipts does not close the loop. It moves the silence up one level and
asks who watches the watcher.

⚠️ **THE WAY OUT IS NOT A BETTER TIMER, IT IS A DIFFERENT SUBSTRATE.** A scheduled
job can always be the thing that did not fire, and its not-firing is invisible BY
CONSTRUCTION. CI is different in exactly one decisive way: it runs on every push,
and **this repo's CI liveness is PROVEN BY THE FACT THAT PRs MERGE AT ALL.** A
freshness check that lives in `run_guards.py` cannot be silently dead — if it
stopped running, nothing would merge, which is very loud. That is why this module
is a guard and not a cron.

MEASURED ROOT CAUSE OF INSTANCE #3, ESTABLISHED AT THE PRIMARY SOURCE
---------------------------------------------------------------------
Worth recording here because it explains why "just add a receipt" fails in this
environment specifically. Read 2026-09-22 via `list_triggers` and `get_session`:
`trig_01TWdAvrwFLe6T9XFoNopTeo` is `enabled: true`, cron `56 * * * *`, last fired
12:56:24Z, `last_run.status: SUCCEEDED` — and **its fired sessions get neither a
checkout of this repo nor any `mcp__*` connector tool.** Its prompt's step 1 needs
`list_sessions`; its step 2 needs `python3 scripts/ops/queue_latency.py
--write-receipt` from the repo root. It could perform NEITHER, from the day it was
armed, while every signal a reader would naturally check stayed green. Confirmed
independently on a second Routine created and fired the same day, with the
negative control run (an ordinary lane DOES return `session_context.sources`).

**So `enabled: true` is not evidence it fires, and FIRING IS NOT EVIDENCE IT DID
ANYTHING** — the run succeeds because the agent correctly reports that it cannot
do the work.

THE POPULATION IS DERIVED FROM WHAT EXISTS, NEVER HAND-MAINTAINED
-----------------------------------------------------------------
⚠️ **A HAND-MAINTAINED LIST REINTRODUCES THE BUG IN NEW CLOTHES**: a check that
never registers is invisible again. So the population is DERIVED — from
`.github/workflows/*.yml` carrying `on.schedule.cron` and from `deploy/*.timer`
carrying `OnCalendar=` — and **anything derived but not registered FAILS.** The
derivation is the deliverable; `CADENCE_REGISTRY` is only where a derived entry
declares WHERE its receipt lands.

MEASURED 2026-09-22 over this repo: **38 in-repo cadence declarations** — 18 of
143 workflows carry a `schedule:`, and 20 `deploy/*.timer` units exist (11 with a
readable `OnCalendar=`; 9 are triggered by another unit and declare no calendar of
their own, which is `schedule_unknown` rather than a fault).

⚠️ **AND THE RESIDUAL, NAMED RATHER THAN HIDDEN: A ROUTINE IS NOT DERIVABLE FROM
THE REPO.** The Routines live in the Claude Code Remote API, which CI cannot
reach, so the omission of a Routine is **NOT** detectable here. That is exactly
the hole this module's own design brief warns about, and it is not closed: it is
declared, and `ROUTINE_RESIDUAL` below is the standing disclosure printed on every
run. Instance #3 was a Routine. **Read this guard as covering the in-repo half of
the class and no more.** Closing the other half needs a receipt a Routine can
actually write, which today it cannot — see the pipeline row named below.

FIVE STATES, NEVER COLLAPSED
----------------------------
  ``fresh``        a receipt exists and is newer than its declared cadence × the
                   margin. The only state that means the thing ran.
  ``stale``        a receipt exists and is OLDER than that. It ran and STOPPED.
  ``never_ran``    the declared receipt path has NEVER existed in git history.
                   ⚠️ DIFFERENT FROM `stale`: *"it was never wired"* and *"it
                   broke"* send a reader to investigate different things, and
                   collapsing them is how ten days of a SUCCEEDED-reporting
                   watchdog went unremarked (`check_manager_queue_watch.py`
                   records that instance).
  ``no_receipt``   registered, and declares NO in-repo receipt, with a stated
                   reason. ⚠️ **WE CANNOT GRADE IT. Its own count, never folded
                   into `fresh`** — folding it would make this module commit the
                   defect it exists to find, and it is the LARGEST bucket on day
                   one, so the temptation is real.
  ``unregistered`` derived from the tree and absent from `CADENCE_REGISTRY`.
                   **FAILS**, because an unregistered cadence is an invisible one.

WHY REPORT-FIRST, AND WHY THAT IS NOT TIMIDITY
-----------------------------------------------
⚠️ **TURNING 38 ENTRIES RED ON DAY ONE GETS THIS REVERTED AND THEN WE HAVE
NOTHING.** `check_pr_queue_watch.py` records refusing to fail PRs on backlog size
for that reason, and § "could not measure is its own outcome" records a guard
whose first CI run emitted 117 fake findings from one absent import, *"burying the
only fact that mattered"*. So:

  * the day-one population is carried in a dated `BASELINE`, PRINTED as counts on
    every run, and the list **may only SHRINK** — the `check_soak_registered.py`
    pattern, whose acceptability rests on every entry being a visible line in the
    diff rather than a silent skip;
  * a **NEW** cadence declaration that is not registered **FAILS**. That is the
    omission-detectable property, and it costs nothing today;
  * a baselined entry that has RECOVERED, or whose source file is gone, fails
    too, so the baseline cannot rot into a permanent hole;
  * `--strict` fails on the baselined debt as well. That is the shape to run when
    asking whether the debt actually shrank, and **what becomes blocking beyond
    the two rules above is the operator's call, not this module's.**

Run `--self-test` to plant each state, or bare for the sweep.
EXIT: 0 report-only pass · 1 an unregistered NEW cadence, a graded receipt gone
stale, or baseline rot · 2 the derivation itself could not run.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

REPO = Path(__file__).resolve().parents[2]

WORKFLOW_DIR = ".github/workflows"
TIMER_DIR = "deploy"

#: `- cron: "..."`, tolerating a trailing `# comment` (four of the 18 carry one).
CRON_RE = re.compile(r"^\s*-\s*cron:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(?:#.*)?$", re.M)
SCHEDULE_RE = re.compile(r"^\s*schedule:\s*$", re.M)
ONCALENDAR_RE = re.compile(r"^\s*OnCalendar\s*=\s*(.+?)\s*$", re.M | re.I)

FRESH = "fresh"
STALE = "stale"
NEVER_RAN = "never_ran"
NO_RECEIPT = "no_receipt"
UNREGISTERED = "unregistered"
SCHEDULE_UNKNOWN = "schedule_unknown"

#: How many declared intervals may elapse before a receipt is `stale`.
#:
#: ⚠️ 3, AND ANCHORED RATHER THAN TUNED. One interval would red on any single
#: skipped run, and a scheduled GitHub Actions cron is documented as best-effort:
#: `check_manager_queue_watch.py` records this repo's own instance of
#: `probes.yml` firing ~4h50m late and once instead of daily. Two intervals still
#: fires on one late run plus one skip, which is ordinary. Three means the thing
#: has missed three consecutive windows, which is not a delay — it is a stop. The
#: same reasoning R6/R7 in `check_manager_scope.py` record for choosing a
#: threshold off the lease TTL rather than off the target cadence.
STALE_MARGIN = 3

#: ⚠️ THE STANDING DISCLOSURE. Printed on EVERY verdict, including a clean one,
#: so no reader prices this guard above its evidence.
ROUTINE_RESIDUAL = (
    "⚠️ WHAT THIS GUARD CANNOT SEE: a Routine (Claude Code Remote scheduled "
    "trigger) is NOT derivable from this repository — the Routines live in an API "
    "CI cannot reach — so the omission of a Routine is NOT detected here. "
    "Instance #3 of this defect class WAS a Routine. Read a clean run as 'no "
    "unregistered IN-REPO cadence', never as 'every scheduled thing is alive'."
)


class Cadence(NamedTuple):
    kind: str            # "workflow" | "timer"
    name: str            # the file's basename — the registry key
    source: str          # repo-relative path that DECLARES the cadence
    schedule: str        # the cron expression or OnCalendar value, verbatim
    minutes: Optional[int]   # expected interval, or None when not confidently parseable


#: Where a registered cadence's receipt lands, or why it has none.
#:
#: ⚠️ THIS IS NOT THE POPULATION — the population is DERIVED. This only answers
#: "where would I look to see that it ran?". An entry with `receipt: None` is
#: NOT graded and is counted as `no_receipt`, which is a gap this guard reports
#: rather than a pass it grants.
#:
#: ⚠️ A `receipt` MUST BE A PATH THE THING ITSELF WRITES, never one a session
#: maintains by hand. `check_manager_queue_watch.py` records why: a hand-written
#: receipt "would arm the freshness grading against a file nobody maintains — a
#: healthy-looking watch forever". That is the new-table-wiring lesson: a guard
#: cheaper to lie to than to satisfy is worse than no guard.
CADENCE_REGISTRY: dict[str, dict] = {
    # ── the one verified pairing on day one ──────────────────────────────
    "trainer-capture-watch.yml": {
        "receipt": "docs/claude/work/TRAINER-CAPTURE-WATCH.json",
        "why": "commits its own watch file back to main, so the last commit "
               "touching it IS the receipt — no new artifact needed",
    },
    # ── E57 (2026-09-24): the research loss detector. Its receipt is written
    #    by the detector itself on every run and landed through commit-to-main,
    #    so a detector that stops firing goes STALE here and reds CI rather
    #    than leaving "no alert" to be read as "nothing lost".
    "research-loss-detector.yml": {
        "receipt": "docs/claude/work/RESEARCH-LOSS-RECEIPT.json",
        "why": "the detector writes the receipt on every run and commits it "
               "back through commit-to-main; the last commit touching it IS "
               "the receipt",
    },
    # ── workflows that COMMIT BACK, so a receipt is derivable from git and
    #    just has not been declared yet. Measured 2026-09-22: 9 of the 18
    #    scheduled workflows contain a commit-to-main / git push step. Naming
    #    the output path for each is the obvious next shrink of this file.
    "econ-calendar-produce.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "econ-event-study.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "macro-producer-liveness.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "macro-valuation-snapshot.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "replay-pregate-nightly.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "research-queue-dispatch.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "stale-automation-sweep.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    "strategy-review-packets.yml": {"receipt": None, "why": "commits back; output path not yet declared"},
    # ── workflows that leave NO in-repo trace. Their run history lives in the
    #    Actions API, which this guard does not call (a guard that needs the
    #    network is a guard that reds on an outage). Declared, not graded.
    "alpaca-settlement-soak-watch.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "broker-bracket-reconcile.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "diag-relay-sweep.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "doc-audit-weekly.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "health-snapshot.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "main-tree-watch.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "oci-inventory.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "purge-artifacts.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    "research-backtest-augment.yml": {"receipt": None, "why": "no in-repo trace; Actions API only"},
    # ── systemd timers run ON THE VM. Nothing they do reaches this repo unless
    #    a workflow pulls it, so a CI guard has no trace of them at all. The
    #    surface for their liveness is the diag relays, not this file. Declared
    #    so they are COUNTED rather than silently outside the population.
    "ict-db-integrity.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-devnull-guard.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-exchange-fills-pull.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-exchange-funding-pull.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-git-sync.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-health-snapshot.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-heartbeat.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-hourly-snapshot.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-ib-executions-pull.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-ib-gateway-reset.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-ib-gateway-watchdog.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-insights-generator-strategies.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-insights-generator.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-liveness-watchdog.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-mes-ibkr-pull.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-research-results-gate.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-shadow-log-rotate.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-trainer-git-sync.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-web-api-watchdog.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
    "ict-work-digest.timer": {"receipt": None, "why": "VM-side; diag relay is the surface"},
}

#: The day-one debt, by name and the state it held on 2026-09-22.
#:
#: ⚠️ MAY ONLY SHRINK, and this module FAILS on an entry that recovered or whose
#: source file is gone — a baseline that outlives its subject accumulates slots
#: nobody audits. Every `no_receipt` here is one declared-output-path away from
#: being gradeable; that is what shrinking it looks like.
BASELINE_2026_09_22: dict[str, str] = {
    name: (NO_RECEIPT if spec["receipt"] is None else FRESH)
    for name, spec in CADENCE_REGISTRY.items()
}


def _git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def cron_interval_minutes(expr: str) -> Optional[int]:
    """Expected minutes between firings, or None when not CONFIDENTLY parseable.

    ⚠️ `None` IS A REAL ANSWER AND IS NOT TREATED AS "FINE". Only the shapes this
    repo actually uses are decoded; anything else returns None and grades
    `schedule_unknown`, which is reported. Guessing an interval would put a
    fabricated threshold under a freshness verdict — the same defect as a
    fabricated `$0` under a cost.
    """
    parts = expr.split()
    if len(parts) != 5:
        return None
    minute, hour, dom, _mon, dow = parts
    step = re.fullmatch(r"\*/(\d+)", hour)
    if step:                                  # e.g. `0 */6 * * *`
        return int(step.group(1)) * 60
    if hour == "*":                           # e.g. `37 * * * *`
        return 60
    if minute.isdigit() and hour.isdigit():
        if dow != "*" and dom == "*":
            # a weekday list (`1-5`) fires ~5x/week; a single day, weekly.
            return 24 * 60 if re.search(r"[-,]", dow) else 7 * 24 * 60
        if dom == "*":
            return 24 * 60                    # daily
    return None


def oncalendar_interval_minutes(value: str) -> Optional[int]:
    """Same contract as `cron_interval_minutes`, for systemd OnCalendar."""
    v = value.strip().rstrip("UTC").strip().lower()
    if v in ("hourly",):
        return 60
    if v in ("daily", "midnight"):
        return 24 * 60
    if v in ("weekly",):
        return 7 * 24 * 60
    # `*-*-* *:20:00` — every hour at :20. `*-*-* 00:35:00` — daily.
    m = re.fullmatch(r"\*-\*-\*\s+(\S+):(\S+):(\S+)", v)
    if m:
        return 60 if m.group(1) == "*" else 24 * 60
    return None


def derive(root: Path) -> tuple[list[Cadence], list[str]]:
    """Every IN-REPO cadence declaration. The population, never a list."""
    notes: list[str] = []
    out: list[Cadence] = []

    wf_dir = root / WORKFLOW_DIR
    wfs = sorted(wf_dir.glob("*.yml")) if wf_dir.is_dir() else []
    for f in wfs:
        text = f.read_text(errors="replace")
        if not SCHEDULE_RE.search(text):
            continue
        crons = CRON_RE.findall(text)
        if not crons:
            # `schedule:` with no readable cron is *we could not look*, and it is
            # kept in the population with an unknown interval rather than dropped.
            out.append(Cadence("workflow", f.name, f"{WORKFLOW_DIR}/{f.name}",
                               "(schedule: present, no readable cron)", None))
            continue
        expr = crons[0].strip()
        out.append(Cadence("workflow", f.name, f"{WORKFLOW_DIR}/{f.name}",
                           expr, cron_interval_minutes(expr)))

    t_dir = root / TIMER_DIR
    timers = sorted(t_dir.glob("*.timer")) if t_dir.is_dir() else []
    for f in timers:
        text = f.read_text(errors="replace")
        m = ONCALENDAR_RE.search(text)
        if m:
            val = m.group(1)
            out.append(Cadence("timer", f.name, f"{TIMER_DIR}/{f.name}", val,
                               oncalendar_interval_minutes(val)))
        else:
            # Triggered by another unit rather than by a calendar. NOT a fault,
            # and not excluded: it still claims to run, so it stays counted.
            out.append(Cadence("timer", f.name, f"{TIMER_DIR}/{f.name}",
                               "(no OnCalendar — triggered by another unit)", None))

    notes.append(
        f"population DERIVED from the tree (never a hand-maintained list): "
        f"{len(out)} in-repo cadence declaration(s) — "
        f"{sum(1 for c in out if c.kind == 'workflow')} of {len(wfs)} workflow(s) "
        f"carry a `schedule:`, plus {sum(1 for c in out if c.kind == 'timer')} "
        f"{TIMER_DIR}/*.timer unit(s)")
    return out, notes


def receipt_age_minutes(root: Path, rel: str) -> tuple[Optional[int], str]:
    """(age_minutes, state) for a receipt path, from its LAST COMMIT.

    ⚠️ THE LAST COMMIT TOUCHING THE PATH, not the file's mtime. A checkout sets
    every mtime to clone time, so mtime in CI measures the runner's age and
    nothing else — it would report every receipt as seconds old. That is a
    fabricated freshness, which is worse than no reading.
    """
    rc, out = _git(root, "log", "-1", "--format=%cI", "--", rel)
    if rc != 0 or not out:
        return None, NEVER_RAN
    try:
        when = datetime.fromisoformat(out.strip())
    except ValueError:
        return None, NEVER_RAN
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    return age, FRESH


def classify(root: Path, c: Cadence) -> tuple[str, str]:
    """(state, detail) for one derived cadence."""
    spec = CADENCE_REGISTRY.get(c.name)
    if spec is None:
        return UNREGISTERED, (
            f"declares a cadence ({c.schedule}) in {c.source} and is NOT in "
            f"CADENCE_REGISTRY, so nothing knows where to look to see that it "
            f"ran — an unregistered cadence is an invisible one")
    receipt = spec.get("receipt")
    if not receipt:
        return NO_RECEIPT, spec.get("why") or "no reason declared"
    age, state = receipt_age_minutes(root, receipt)
    if state == NEVER_RAN:
        return NEVER_RAN, (
            f"{receipt} has NEVER existed in git history. Not 'it broke' — it "
            f"was never wired, and those send a reader to different places")
    if c.minutes is None:
        return SCHEDULE_UNKNOWN, (
            f"{receipt} last written {age} minute(s) ago, but the cadence "
            f"'{c.schedule}' is not confidently parseable, so there is no "
            f"threshold to grade it against. Reported, never assumed fine")
    limit = c.minutes * STALE_MARGIN
    if age > limit:
        return STALE, (
            f"{receipt} last written {age} minute(s) ago; '{c.schedule}' expects "
            f"every ~{c.minutes} minute(s), so it has missed at least "
            f"{STALE_MARGIN} consecutive windows. It ran and STOPPED")
    return FRESH, f"{receipt} written {age} minute(s) ago (limit {limit})"


def sweep(root: Path):
    cadences, notes = derive(root)
    result = {c.name: (c,) + classify(root, c) for c in cadences}
    return result, notes


def report(result) -> list[str]:
    buckets: dict[str, list[str]] = {}
    for name, (_c, state, _d) in sorted(result.items()):
        buckets.setdefault(state, []).append(name)
    order = [FRESH, STALE, NEVER_RAN, SCHEDULE_UNKNOWN, NO_RECEIPT, UNREGISTERED]
    out = []
    for state in order:
        names = buckets.get(state, [])
        gloss = {
            FRESH: "a receipt exists and is inside its cadence — the ONLY state that means it ran",
            STALE: "ran and STOPPED",
            NEVER_RAN: "its receipt has never existed — never wired, NOT 'it broke'",
            SCHEDULE_UNKNOWN: "cadence not confidently parseable, so no threshold — reported, not assumed fine",
            NO_RECEIPT: "declares NO in-repo receipt, so WE CANNOT GRADE IT. Its own count, never folded into fresh",
            UNREGISTERED: "derived from the tree and unregistered — an invisible cadence",
        }[state]
        out.append(f"  {state}: {len(names)} — {gloss}")
    for state in (UNREGISTERED, NEVER_RAN, STALE, SCHEDULE_UNKNOWN):
        names = buckets.get(state, [])
        if not names:
            continue
        out.append("")
        out.append(f"  {state.upper()}:")
        for name in names:
            c, _s, detail = result[name]
            tag = " [BASELINED]" if name in BASELINE_2026_09_22 else " ⚠️ NEW"
            out.append(f"    {name}{tag} ({c.kind}, {c.schedule})")
            out.append(f"        {detail}")
    return out


def findings(result, strict: bool = False) -> list[str]:
    fails: list[str] = []
    for name, (c, state, detail) in sorted(result.items()):
        new = name not in BASELINE_2026_09_22
        if state == UNREGISTERED:
            fails.append(
                f"UNREGISTERED CADENCE: {name} — {detail}.\n"
                f"      Add it to CADENCE_REGISTRY naming the path it WRITES (a "
                f"path the thing itself produces, never one a session maintains "
                f"by hand — a receipt cheaper to lie to than to satisfy is worse "
                f"than no receipt), or declare `receipt: None` WITH a reason so "
                f"it is counted as ungradeable rather than invisible.")
        elif state in (STALE, NEVER_RAN) and (new or strict):
            fails.append(f"{state.upper()}: {name} ({c.kind}) — {detail}")
        elif strict and state in (NO_RECEIPT, SCHEDULE_UNKNOWN):
            fails.append(f"--strict {state.upper()}: {name} — {detail}")
    live = set(result)
    for name, was in sorted(BASELINE_2026_09_22.items()):
        if name not in live:
            fails.append(
                f"BASELINE STALE: {name} is baselined as {was} and no longer "
                f"declares a cadence in the tree. Remove the entry — a baseline "
                f"that outlives its subject accumulates slots nobody audits.")
            continue
        now = result[name][1]
        if was == NO_RECEIPT and now == FRESH:
            fails.append(
                f"BASELINE RECOVERED: {name} was baselined as {NO_RECEIPT} and "
                f"now grades {FRESH} — it has a working receipt. REMOVE the "
                f"entry; the list may only shrink.")
    return fails


# --------------------------------------------------------------------------
def _self_test() -> int:
    import shutil
    import tempfile

    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'PASS' if passed else 'FAIL'}  {label}"
              + (f" — {detail}" if detail and not passed else ""))
        ok = ok and passed

    print("check_cadence_liveness self-test")

    # ── the interval decoders, including their REFUSALS ─────────────────
    check("P1 `0 */6 * * *` decodes to 360 min",
          cron_interval_minutes("0 */6 * * *") == 360)
    check("P2 `37 * * * *` decodes to hourly",
          cron_interval_minutes("37 * * * *") == 60)
    check("P3 `0 3 * * *` decodes to daily",
          cron_interval_minutes("0 3 * * *") == 24 * 60)
    check("P4 `0 12 * * 1` decodes to weekly",
          cron_interval_minutes("0 12 * * 1") == 7 * 24 * 60)
    check("P5 `30 14 * * 1-5` (weekdays) is ~daily, not weekly",
          cron_interval_minutes("30 14 * * 1-5") == 24 * 60)
    check("N1 an unparseable cron returns None rather than a GUESS",
          cron_interval_minutes("0 0 1 */3 *") is None,
          "a fabricated interval would put an invented threshold under a "
          "freshness verdict — the same defect as a fabricated $0 under a cost")
    check("P6 OnCalendar `*-*-* *:20:00` is hourly",
          oncalendar_interval_minutes("*-*-* *:20:00") == 60)
    check("P7 OnCalendar `hourly` / `daily` shorthands decode",
          oncalendar_interval_minutes("hourly") == 60
          and oncalendar_interval_minutes("daily") == 24 * 60)
    check("N2 an unparseable OnCalendar returns None",
          oncalendar_interval_minutes("Mon,Fri *-*-1..7 03:00") is None)

    tmp = Path(tempfile.mkdtemp())
    saved_reg = dict(CADENCE_REGISTRY)
    saved_base = dict(BASELINE_2026_09_22)
    try:
        root = tmp / "repo"
        (root / WORKFLOW_DIR).mkdir(parents=True)
        (root / TIMER_DIR).mkdir(parents=True)
        (root / "docs").mkdir()
        (root / WORKFLOW_DIR / "hourly-thing.yml").write_text(
            "on:\n  schedule:\n    - cron: '37 * * * *'  # trailing comment\n")
        (root / WORKFLOW_DIR / "unregistered-thing.yml").write_text(
            "on:\n  schedule:\n    - cron: '0 4 * * *'\n")
        (root / WORKFLOW_DIR / "not-scheduled.yml").write_text(
            "on:\n  push:\n    branches: [main]\n")
        (root / TIMER_DIR / "a.timer").write_text("[Timer]\nOnCalendar=hourly\n")
        (root / TIMER_DIR / "b.timer").write_text("[Timer]\nUnit=b.service\n")
        (root / "docs" / "receipt.json").write_text("{}\n")
        for cmd in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "s@e.com"],
                    ["config", "user.name", "s"]):
            subprocess.run(["git", "-C", str(root), *cmd], check=True,
                           capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"],
                       check=True, capture_output=True)

        # ⚠️ THE DERIVATION CONTROL: a workflow with no `schedule:` must NOT be in
        # the population. Without this, a probe that counted every workflow would
        # look like it was working while grading 143 things that claim nothing.
        cadences, _n = derive(root)
        names = {c.name for c in cadences}
        check("P8 the population is DERIVED and includes both kinds",
              {"hourly-thing.yml", "unregistered-thing.yml", "a.timer",
               "b.timer"} <= names, str(names))
        check("N3 DERIVATION CONTROL: a workflow with no `schedule:` is NOT in "
              "the population", "not-scheduled.yml" not in names, str(names))
        check("P9 a timer with no OnCalendar stays COUNTED with an unknown "
              "interval", any(c.name == "b.timer" and c.minutes is None
                              for c in cadences))

        CADENCE_REGISTRY.clear()
        CADENCE_REGISTRY.update({
            "hourly-thing.yml": {"receipt": "docs/receipt.json", "why": "x"},
            "a.timer": {"receipt": None, "why": "VM-side, stated"},
            "b.timer": {"receipt": None, "why": "VM-side, stated"},
        })
        BASELINE_2026_09_22.clear()
        BASELINE_2026_09_22.update({"a.timer": NO_RECEIPT, "b.timer": NO_RECEIPT})

        result, _n = sweep(root)
        states = {k: v[1] for k, v in result.items()}
        check("P10 a registered receipt committed just now is FRESH",
              states["hourly-thing.yml"] == FRESH, str(states))
        check("P11 a derived cadence absent from the registry is UNREGISTERED",
              states["unregistered-thing.yml"] == UNREGISTERED, str(states))
        check("P12 `receipt: None` with a reason is NO_RECEIPT, not fresh",
              states["a.timer"] == NO_RECEIPT, str(states))
        f = findings(result)
        check("P13 an UNREGISTERED cadence FAILS",
              any("UNREGISTERED CADENCE" in x for x in f), str(f))
        check("P13b ...and the remedy refuses a hand-maintained receipt",
              any("never one a session maintains" in x for x in f), str(f))
        check("N4 a BASELINED no_receipt entry does NOT fail",
              not any("a.timer" in x for x in f), str(f))
        txt = "\n".join(report(result))
        for state in (FRESH, NO_RECEIPT, UNREGISTERED):
            check(f"N5 `{state}` is reported as its own count", state in txt, txt)

        # never_ran: a registered receipt path that has never existed
        CADENCE_REGISTRY["hourly-thing.yml"] = {"receipt": "docs/absent.json",
                                                "why": "x"}
        r2, _n = sweep(root)
        check("P14 a receipt path that never existed is NEVER_RAN, distinct "
              "from stale", r2["hourly-thing.yml"][1] == NEVER_RAN,
              str(r2["hourly-thing.yml"]))
        check("P14b ...and a NEW one FAILS",
              any("NEVER_RAN" in x for x in findings(r2)), str(findings(r2)))

        # baseline rot, both directions
        BASELINE_2026_09_22["gone-entirely.yml"] = NO_RECEIPT
        check("P15 a baseline entry naming a vanished cadence FAILS",
              any("BASELINE STALE" in x for x in findings(r2)))
        BASELINE_2026_09_22.pop("gone-entirely.yml")
        BASELINE_2026_09_22["hourly-thing.yml"] = NO_RECEIPT
        CADENCE_REGISTRY["hourly-thing.yml"] = {"receipt": "docs/receipt.json",
                                                "why": "x"}
        r3, _n = sweep(root)
        check("P16 a baseline entry that RECOVERED a receipt FAILS, so the list "
              "cannot rot", any("BASELINE RECOVERED" in x for x in findings(r3)),
              str(findings(r3)))
    finally:
        CADENCE_REGISTRY.clear()
        CADENCE_REGISTRY.update(saved_reg)
        BASELINE_2026_09_22.clear()
        BASELINE_2026_09_22.update(saved_base)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nself-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="also FAIL on the baselined debt and on every "
                         "ungradeable entry. NOT the default: 38 red entries on "
                         "day one gets the guard reverted, not the debt fixed.")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    result, notes = sweep(REPO)
    if not result:
        print("cadence-liveness: could_not_check — the derivation found NO "
              "cadence declaration at all, which in this repo means the scan "
              "broke rather than that nothing is scheduled. NOTHING was graded; "
              "this is not a pass.")
        return 2

    print("cadence-liveness: freshness belongs in CI, not on a timer")
    for n in notes:
        print(f"  · {n}")
    for line in report(result):
        print(line)
    baselined = sum(1 for k in BASELINE_2026_09_22 if k in result)
    print("")
    print(f"  baselined debt: {baselined} of {len(BASELINE_2026_09_22)} entries "
          f"still present — this list may only SHRINK. Each `no_receipt` is one "
          f"declared output path away from being gradeable.")
    print(f"  {ROUTINE_RESIDUAL}")

    fails = findings(result, strict=args.strict)
    if fails:
        print("")
        print("A SCHEDULED THING THAT CANNOT BE SEEN TO HAVE RUN:")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("")
    print("  No unregistered in-repo cadence, nothing newly stale, and no "
          "baseline rot. Read that as 'no regression in the IN-REPO half', never "
          "as 'everything scheduled is alive' — see the residual above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
