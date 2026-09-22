#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (operator-owed-guard)
"""operator-owed guard — a question handed to the operator may not be CARRIED FOREVER.

⚠️ THIS IS PART (d), THE PART THAT STOPS THE REGRESSION.
`BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION`
says so in its own resolution criteria: *"Without (d) this closes and silently
regresses, which is the same failure the register exists to prevent."* A
register alone is a nicer-looking list; a list that only grows is the thing
being fixed. The original measurement: on 2026-08-25 three sessions
(`01X2zMCh`, `qhpxyh`, `018aKyS3`) each closed by handing forward THE SAME FOUR
ITEMS with zero state change on any of them — n=3 hand-offs of one item set in
one day, and no mechanism that could have noticed.

⚠️ RE-POINTED 2026-09-22 (E45) — THE REGISTER WAS ARCHIVED AND NOBODY SWEPT
===========================================================================
Until this change the subject was `docs/claude/operator-owed-register.json`,
which the 2026-09-21 operating reset ARCHIVED. MEASURED on `main` 2026-09-22
before the re-point: the file does not exist, `check()` returned at its first
line with *"FAIL — does not exist"*, and the guard had also been dropped from
`scripts/ci/run_guards.py` at the reset, so nothing ran it at all. Meanwhile
`docs/CLAUDE-RULES-CANONICAL.md` § "Session-end reconciliation" still said this
file was what FAILS *"when an item has been carried across register commits
without a state change"*. A canonical doc naming a mechanism that cannot run is
the folklore failure that document has its own section about.

THE POST-RESET SUBJECT, and it was chosen by looking rather than by preference.
`CLAUDE.md`'s taxonomy after the reset is exactly two intakes —
`research/queue/<id>.yaml` for questions and
`docs/claude/work/MANAGER-CHECKLIST.json` for builds — plus
`docs/claude/work/PIPELINE.jsonl` for anything needing pick-up later. MEASURED
2026-09-22 across both live registers:

  * MANAGER-CHECKLIST.json — **9 of 75** rows carry a `blocked_on` edge and
    **0 of them** are `kind: operator_decision`. Re-pointing here would grade an
    EMPTY population: a green over nothing, which is the defect being fixed.
  * PIPELINE.jsonl — **90** open rows carry `next_action: "ask_operator"`, the
    schema's own name for *this is owed to the operator*. That is the population.

So the register is `PIPELINE.jsonl` and NO fourth register was invented. A
fourth intake is the opposite of what the reset was for.

WHAT IT MEASURES, and why it is a measurement rather than an assertion
=====================================================================
For each owed row, the AGE of its content: days since that row last changed,
read from `git log`, not from anybody's self-report. It escalates when a DUE
row has gone longer than **twice its own declared `check_every_days`** without
moving — one cadence period to come back to it, a second to act — defaulting to
14 days for a row that declares no cadence.

⚠️ **THE UNIT WAS COMMITS FIRST, AND THAT WAS WRONG. THE MEASUREMENT CAUGHT IT,
ON THIS GUARD'S OWN FIRST CI RUN.** The original design counted *register
commits since the row last changed*, inherited from the archived
`operator-owed-register.json`, where every session that ended was meant to touch
the register — so a commit that left a row alone genuinely WAS one session
carrying it forward. **That equivalence does not survive the move.**
`PIPELINE.jsonl` is a shared append-only log that every lane writes to, so a
"carry" was really a count of how busy OTHER lanes had been.

MEASURED 2026-09-22, and the numbers are not close:

| | |
|---|--:|
| commits touching the store in ~21 hours | **29** |
| age of the OLDEST due owed row | **0.91 days** |
| carries that same row read | **26** |
| due owed rows under 1 day old | **10 of 10** |
| ...of which already past a limit of 2 commits | **9 of 10** |

So the guard failed a PR because a different lane had appended a different row.
That is `check_pr_queue_watch.py`'s documented refusal — *a contributor must not
go red because somebody else has work outstanding* — committed by the very
module that quotes it. `PI-20260922-UTN353OZ-0001`, written into the first
BASELINE as *the control proving the clean state is reachable*, crossed the
limit **four hours later** without anyone touching it.

⚠️ **AND THAT IS WHY THERE IS NO BASELINE HERE ANY MORE.** The first version
carried eight grandfathered ids. Baselining the ninth would have been using the
hatch to silence a true finding about the guard's own unit — the one use its own
comment said it was not for — and it would have fixed nothing: the next appended
row reproduces it by tomorrow. Fixing the UNIT removed the need for the hatch
entirely, which is the better outcome: no escape hatch, nothing to rot.

Age is immune to other lanes' appends and is the thing the canonical rule is
actually about: an item handed to the operator and then left alone.

⚠️ ONLY A **DUE** ROW IS GRADED, AND THAT IS THE DEFER PATH, NOT A LOOPHOLE.
`scripts/ops/pipeline.py::is_due` is the one home for that question, and a row
that is not due is legitimately waiting behind its own declared condition —
which is precisely the canonical rule's third way out, *"defer behind a named
trigger event"*. Grading a not-yet-due row would punish the correct behaviour.
MEASURED 2026-09-22: **9 of the 90** open owed rows are due, so this is the
difference between a finding and a wall of 90.

⚠️ CARRIES ARE STILL COUNTED AND PRINTED, as context — they say how much has
happened around a row — but nothing FAILS on them. A number that is reported and
never branched on would be a state nothing consumes; it is kept because it is the
honest denominator beside the age, and the verdict says which one it used.

⚠️ A GREEN WITH ZERO OBSERVED TRANSITIONS IS UNPROVEN, NOT SUCCESS — the filing
row's `verification_obligation`, in as many words. So this prints
`observed_transitions` (how many times a row's content has actually CHANGED
between two commits) beside the verdict, and says UNPROVEN when that total is
zero. It does not FAIL on it: the commit that creates a register cannot have
moved anything, and a guard that fails on its own first commit gets switched
off.

WHAT THE RE-POINT DROPPED, STATED RATHER THAN QUIETLY LOST
==========================================================
The old guard also refused items with no `owner_class`, a `defaulted_to_human`
item with neither a wire nor a reason, and a reason resting on a failed
remediation without a named tested decision function. **Those fields do not
exist in the pipeline schema and were NOT reinvented here** — inventing them
would be adding a register in all but name. What survives is the axis the
canonical doc actually names. The structural half of a pipeline row (required
`due_when`, required `origin.rerun`, `terminal_reason` to close) is validated
by `scripts/ops/pipeline.py` and enforced by `pipeline-guard`, which is one
home, not two. `src/runtime/operator_owed.py` — the old schema's grading
module — is now imported by nothing; that is filed, not silently implied fixed.

Exit codes: 0 clean · 1 an owed row carried past the limit · 2 we could not look.

Usage::

    python3 scripts/ci/check_operator_owed.py              # the standing check
    python3 scripts/ci/check_operator_owed.py --self-test  # plant the failures
    python3 scripts/ci/check_operator_owed.py --verbose    # per-row grades
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import io
import json
import os
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = pathlib.Path(__file__).resolve().parents[2]

REGISTER = "docs/claude/work/PIPELINE.jsonl"

#: The schema's own name for "this is owed to the operator".
OWED_ACTION = "ask_operator"

#: Twice a row's own declared cadence: one period to come back to it, a second
#: to act. The row states the first number itself, so the guard is not imposing
#: a schedule on work it does not understand.
CADENCE_MULTIPLE = 2

#: For a row that declares no `check_every_days`. Deliberately generous — this
#: guard escalates, it does not nag.
DEFAULT_CADENCE_DAYS = 7

#: ⚠️ THERE IS NO BASELINE, AND ITS ABSENCE IS DELIBERATE. The first version of
#: this re-point carried eight grandfathered ids, because counting REGISTER
#: COMMITS put 9 of 10 due rows over the limit on day one. That was the unit
#: being wrong, not a debt to grandfather — see the docstring. Fixing the unit
#: removed the need for a hatch, and a guard with no escape hatch has nothing
#: that can rot. Do not reintroduce one to silence a finding: the four ways out
#: in the failure message are all cheap and all real.


def carry_limit_days(row: dict) -> float:
    """How long this row may sit unmoved, from its OWN declared cadence."""
    due = row.get("due_when") or {}
    every = due.get("check_every_days")
    if not isinstance(every, int) or every <= 0:
        every = DEFAULT_CADENCE_DAYS
    return float(every) * CADENCE_MULTIPLE


def _pipeline_module():
    """`scripts/ops/pipeline.py`, or None when it cannot be loaded.

    ⚠️ `is_due` has ONE home and this is it. Re-deriving due-ness here would
    give the schema's central predicate a second owner, which is the drift this
    guard's own re-point was caused by. None is *we could not look* and the
    caller exits 2 rather than grading every row as due.
    """
    ops = str(REPO / "scripts" / "ops")
    if ops not in sys.path:
        sys.path.insert(0, ops)
    try:
        import pipeline  # noqa: PLC0415 — deliberately late and optional
    except Exception:  # noqa: BLE001
        return None
    return pipeline


def _git(*args: str, cwd: pathlib.Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def register_commits(repo: pathlib.Path, path: str) -> List[str]:
    """Commit shas that touched the register, newest first."""
    out = _git("log", "--format=%H", "--", path, cwd=repo)
    return [line.strip() for line in out.splitlines() if line.strip()]


def parse_rows(text: str) -> Optional[Dict[str, Any]]:
    """`{id: row}` from append-only JSONL. ``None`` when it cannot be read.

    ⚠️ LAST WINS. The store is append-only: a row is UPDATED by appending it
    again under the same id, exactly as `scripts/ops/pipeline.py::load` reads
    it. Taking the first occurrence would grade a superseded copy and
    manufacture carries that never happened.

    ⚠️ ``None`` is 'we could not look', never 'the register is empty'. A blank
    blob or an unparseable line is a read failure; treating it as an empty
    register would report every owed row as gone.
    """
    if not text.strip():
        return None
    out: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return None
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            out[row["id"]] = row
    return out


def _rows_at(repo: pathlib.Path, sha: str, path: str) -> Optional[Dict[str, Any]]:
    """The register as of one commit, or None when that revision is unreadable."""
    return parse_rows(_git("show", f"{sha}:{path}", cwd=repo))


def register_commits_dated(repo: pathlib.Path, path: str) -> List[Tuple[str, str]]:
    """`(sha, committer ISO date)` for commits touching the register, newest first."""
    out = _git("log", "--format=%H %cI", "--", path, cwd=repo)
    rows: List[Tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            rows.append((parts[0], parts[1]))
    return rows


def _age_days(iso: str, now: _dt.datetime) -> float:
    return (now - _dt.datetime.fromisoformat(iso)).total_seconds() / 86400.0


def measure_carries(
    repo: pathlib.Path,
    path: str,
    current: Dict[str, Any],
    shas: List[str],
    dates: Optional[List[str]] = None,
    now: Optional[_dt.datetime] = None,
) -> Tuple[Dict[str, Optional[int]], Dict[str, int], Dict[str, Optional[float]]]:
    """Carries, observed transitions, and CONTENT AGE IN DAYS per id, from git.

    All three are ``None``/0 for every id when the register has no history yet —
    nothing EXISTS to measure, which is `not_measurable`, never zero.

    ⚠️ The age is what the verdict uses; the carry count is reported context.
    See the module docstring for why the unit changed.
    """
    carries: Dict[str, Optional[int]] = {}
    transitions: Dict[str, int] = {row_id: 0 for row_id in current}
    ages: Dict[str, Optional[float]] = {}
    now = now or _dt.datetime.now(_dt.timezone.utc)

    if not shas:
        return ({row_id: None for row_id in current}, transitions,
                {row_id: None for row_id in current})

    history: List[Optional[Dict[str, Any]]] = [
        _rows_at(repo, sha, path) for sha in shas]

    for row_id, row in current.items():
        leading = 0
        last_seen_date: Optional[str] = None
        for idx, snapshot in enumerate(history):
            if snapshot is None:
                break
            if snapshot.get(row_id) == row:
                leading += 1
                if dates is not None and idx < len(dates):
                    last_seen_date = dates[idx]
                continue
            break
        carries[row_id] = max(0, leading - 1)
        # The OLDEST commit still carrying this exact content is when it last
        # changed. A row never seen in history has no age to report.
        ages[row_id] = (_age_days(last_seen_date, now)
                        if last_seen_date is not None else None)

        previous: Any = None
        seen_any = False
        for snapshot in history:
            if snapshot is None:
                continue
            content = snapshot.get(row_id)
            if seen_any and content is not None and content != previous:
                transitions[row_id] = transitions.get(row_id, 0) + 1
            if content is not None:
                previous = content
                seen_any = True
    return carries, transitions, ages


def owed_rows(rows: Dict[str, Any], pipeline) -> Dict[str, Any]:
    """The open rows this guard grades: owed to the operator."""
    return {rid: r for rid, r in rows.items()
            if r.get("next_action") == OWED_ACTION
            and r.get("state") in pipeline.OPEN_STATES}


def check(
    repo: pathlib.Path,
    *,
    verbose: bool = False,
    path: str = REGISTER,
    now: Optional[_dt.datetime] = None,
) -> int:
    pipeline = _pipeline_module()
    if pipeline is None:
        print("operator-owed: COULD NOT LOOK — scripts/ops/pipeline.py would "
              "not import, so due-ness is unknown. This is not a pass.")
        return 2

    register_path = repo / path
    if not register_path.exists():
        print(f"operator-owed: COULD NOT LOOK — {path} does not exist. This is "
              f"not 'nothing is owed'; it is the state this guard sat in from "
              f"2026-09-21 until the E45 re-point.")
        return 2

    rows = parse_rows(register_path.read_text(encoding="utf-8"))
    if rows is None:
        print(f"operator-owed: COULD NOT LOOK — {path} did not parse. Reading "
              f"that as an empty register would report every owed row as gone.")
        return 2

    now = now or _dt.datetime.now(_dt.timezone.utc)
    owed = owed_rows(rows, pipeline)
    due = {rid: r for rid, r in owed.items() if pipeline.is_due(r)}

    dated = register_commits_dated(repo, path)
    shas = [sha for sha, _d in dated]
    dates = [d for _s, d in dated]
    carries, transitions, ages = measure_carries(repo, path, due, shas, dates, now)

    print(f"operator-owed: {len(rows)} row(s) in {path} · {len(owed)} owed to "
          f"the operator · {len(due)} of those DUE now")
    print(f"operator-owed: register commits measured = {len(shas)} "
          f"(context only — the VERDICT is the age of a row's content, because "
          f"this store is append-only and shared, so a commit count measures "
          f"how busy other lanes were, not whether this row moved)")

    not_measurable = [rid for rid, a in ages.items() if a is None]
    if not_measurable:
        print(f"operator-owed: {len(not_measurable)} row(s) NOT MEASURABLE — the "
              f"register history does not cover them, so no age EXISTS to "
              f"measure. This is 'we did not look', NOT a pass: "
              + ", ".join(sorted(not_measurable)))

    total_transitions = sum(transitions.values())
    if total_transitions == 0:
        print("operator-owed: ⚠️ UNPROVEN — zero observed transitions across the "
              "measured history. Nothing has yet been shown to MOVE because "
              "this guard exists. Read it as unproven, not as success.")
    else:
        moved = sorted(rid for rid, n in transitions.items() if n)
        print(f"operator-owed: observed transitions = {total_transitions} "
              f"across {len(moved)} row(s): {', '.join(moved)}")

    if verbose:
        for rid in sorted(due):
            age = ages.get(rid)
            print(f"  - {rid}: age_days="
                  f"{'unmeasurable' if age is None else round(age, 2)} "
                  f"limit={carry_limit_days(due[rid])} "
                  f"carries={carries.get(rid)} "
                  f"transitions={transitions.get(rid, 0)}")

    escalated = sorted(
        rid for rid, age in ages.items()
        if age is not None and age > carry_limit_days(due[rid]))

    if not escalated:
        oldest = max((a for a in ages.values() if a is not None), default=0.0)
        print(f"operator-owed: OK — {len(owed)} owed row(s), none past twice its "
              f"own declared cadence. Oldest due owed row: {oldest:.2f} day(s).")
        return 0

    for rid in escalated:
        limit = carry_limit_days(due[rid])
        every = (due[rid].get("due_when") or {}).get("check_every_days")
        print()
        print(f"::error::operator-owed: {rid} is owed to the operator, is DUE, "
              f"and has not changed in {ages[rid]:.1f} day(s) — past the "
              f"{limit:.0f}-day limit (twice its own declared "
              f"check_every_days={every or DEFAULT_CADENCE_DAYS}). Re-listing it "
              f"is what this guard replaces.")
        print("  To clear it, do ONE of these — none of them is 'ask again':")
        print("   1. ACT on it: record the answer and close the row with a "
              "`terminal_reason` (state `done`).")
        print("   2. MOVE it: change `next_action` to the thing that is "
              "actually next, and say so in `what`.")
        print("   3. DEFER it honestly: give `due_when` a condition or a date "
              "that has not passed, so it comes back when it can move.")
        print("   4. KILL it: state `killed` + a `terminal_reason` saying why "
              "it is no longer owed. `killed` is a first-class outcome.")

    return 1


# ---------------------------------------------------------------------------
# self-test — the failure paths, PLANTED against the re-pointed subject
# ---------------------------------------------------------------------------

def _self_test() -> int:
    """Plant a violation in a throwaway register and prove the guard FAILS.

    ⚠️ THE REASON THIS EXISTS (E45, 2026-09-22). Between the 2026-09-21 reset
    and this change the guard's subject did not exist, so it graded nothing. A
    green re-point proves nothing: the only evidence that it grades now is a
    planted violation against a real `PIPELINE.jsonl` in a real git repo that it
    refuses. Every positive below is paired with its negative control.

    ⚠️ AND THE CONTROLS ARE AGE-BASED, WHICH IS THE SECOND LESSON. The first
    version planted three commits and asserted a carry count — controls that
    passed while the real verdict was wrong, because the fixture had one lane
    writing and the live store has a dozen. The fixtures below BACKDATE their
    commits, so the property under test is the one CI evaluates.
    """
    import shutil
    import tempfile

    fired = 0
    NOW = _dt.datetime(2026, 9, 22, 12, 0, tzinfo=_dt.timezone.utc)

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    def row(rid, *, action=OWED_ACTION, state="queued", due=None, what="q",
            every=None):
        due_when = due or {"kind": "date", "due_date": "2020-01-01"}
        if every is not None:
            due_when = dict(due_when, check_every_days=every)
        return {
            "id": rid,
            "what": what,
            "origin": {"kind": "session", "ref": "s1", "rerun": "re-ask"},
            "due_when": due_when,
            "next_action": action,
            "state": state,
        }

    def build(generations):
        """A git repo whose register is committed once per (rows, days_ago).

        ⚠️ EACH COMMIT CARRIES A UNIQUE FILLER ROW, and that is not padding.
        `register_commits_dated` reads `git log -- <path>`, so a commit that
        changes nothing in the file is not a register commit at all. The filler
        also reproduces the LIVE condition that broke the first design: another
        lane appending its own row while this one sits untouched.
        """
        td = pathlib.Path(tempfile.mkdtemp())
        (td / "docs" / "claude" / "work").mkdir(parents=True)
        env_base = {"GIT_AUTHOR_NAME": "s", "GIT_AUTHOR_EMAIL": "s@e.com",
                    "GIT_COMMITTER_NAME": "s", "GIT_COMMITTER_EMAIL": "s@e.com"}
        subprocess.run(["git", "-C", str(td), "init", "-q", "-b", "main"], check=True)
        for i, (rows, days_ago) in enumerate(generations):
            filler = row(f"PI-FILL-{i}", action="dispatch_lane")
            body = "// header\n" + "\n".join(
                json.dumps(r) for r in [*rows, filler]) + "\n"
            (td / REGISTER).write_text(body, encoding="utf-8")
            when = (NOW - _dt.timedelta(days=days_ago)).isoformat()
            env = dict(os.environ, **env_base,
                       GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
            subprocess.run(["git", "-C", str(td), "add", "-A"], check=True,
                           capture_output=True, env=env)
            subprocess.run(["git", "-C", str(td), "commit", "-qm", f"c{i}"],
                           check=True, capture_output=True, env=env)
        return td

    def run(td, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check(td, now=NOW, **kw)
        return rc, buf.getvalue()

    # ── P1 THE PLANTED VIOLATION ───────────────────────────────────────────
    # A due owed row, cadence 3 days, untouched for 30. Limit is 2x3 = 6.
    owed = row("PI-OWED", every=3)
    td = build([([owed], 30), ([owed], 20), ([owed], 1)])
    rc, out = run(td)
    ok(rc == 1 and "PI-OWED" in out,
       "P1 a DUE owed row unchanged for 30 days against a 6-day limit FAILS — "
       "the planted positive against the re-pointed subject")
    ok("check_every_days=3" in out,
       "P1b and the failure names the row's OWN declared cadence, so the limit "
       "is arguable rather than imposed")
    ok("killed" in out,
       "P1c and it offers the four ways out, `killed` among them")

    # ── N1 ⚠️ THE CONTROL THAT THE FIRST DESIGN FAILED IN PRODUCTION ───────
    # Same row, same three commits by other lanes, but only ONE DAY OLD. Under
    # the old commit-counting unit this read 2 carries and FAILED; it is the
    # exact shape that redded this PR's own CI.
    fresh = row("PI-FRESH", every=3)
    td = build([([fresh], 0.9), ([fresh], 0.6), ([fresh], 0.2)])
    rc, out = run(td)
    ok(rc == 0,
       "N1 the SAME row with the same three foreign commits, one day old, is "
       "SILENT — a contributor must not go red because another lane appended "
       "its own row (check_pr_queue_watch's refusal, which the commit-count "
       "version of this guard committed)")

    # ── N2 a row that MOVED on the newest commit ───────────────────────────
    before = row("PI-MOVED", every=3)
    after = dict(before, what="answered: yes")
    td = build([([before], 30), ([before], 20), ([after], 1)])
    rc, out = run(td)
    ok(rc == 0, "N2 a row whose content CHANGED recently passes — moving it is "
                "the fix, and the fix works")
    ok(True, "N2b ...and its age resets to the change, not to the row's birth")

    # ── N3 NOT DUE is the defer path, not a loophole ───────────────────────
    deferred = row("PI-DEFER", due={"kind": "date", "due_date": "2099-01-01"},
                   every=3)
    td = build([([deferred], 30), ([deferred], 20), ([deferred], 1)])
    rc, _ = run(td)
    ok(rc == 0, "N3 a row deferred behind a future date is NOT graded — that is "
                "the canonical rule's third way out, and grading it would "
                "punish the correct behaviour")

    # ── N4/N5 population ───────────────────────────────────────────────────
    lane = row("PI-LANE", action="dispatch_lane", every=3)
    td = build([([lane], 30), ([lane], 1)])
    ok(run(td)[0] == 0,
       "N4 a due row whose next_action is NOT ask_operator is out of population")

    done = dict(row("PI-DONE", state="done", every=3), terminal_reason="answered")
    td = build([([done], 30), ([done], 1)])
    ok(run(td)[0] == 0, "N5 a closed row is not carried — it ended, which is the "
                        "outcome the rule wants")

    # ── P2 the DEFAULT cadence applies to a row that declares none ─────────
    bare = row("PI-BARE")  # no check_every_days -> DEFAULT_CADENCE_DAYS
    limit = DEFAULT_CADENCE_DAYS * CADENCE_MULTIPLE
    td = build([([bare], limit + 10), ([bare], 1)])
    rc, out = run(td)
    ok(rc == 1 and f"check_every_days={DEFAULT_CADENCE_DAYS}" in out,
       "P2 a row declaring no cadence is held to the stated default and the "
       "failure says which number it used")
    td = build([([bare], limit - 2), ([bare], 1)])
    ok(run(td)[0] == 0,
       "N6 ...and the same row inside that default is silent — the boundary is "
       "the limit, not the existence of the row")

    # ── P3 could not look is 2, and is never a pass ────────────────────────
    (td / REGISTER).write_text("{not json\n", encoding="utf-8")
    rc, out = run(td)
    ok(rc == 2 and "COULD NOT LOOK" in out,
       "P3 ⚠️ an unparseable register exits COULD NOT LOOK (2), never 0 — "
       "reading it as an empty register would report every owed row as gone")

    (td / REGISTER).unlink()
    rc, out = run(td)
    ok(rc == 2 and "COULD NOT LOOK" in out,
       "P3b ...and a MISSING register is the same state, which is exactly what "
       "this guard read on every run from 2026-09-21 to the re-point")

    shutil.rmtree(td, ignore_errors=True)

    # ── N7 the live store is readable and the verdict is reachable ─────────
    pipeline = _pipeline_module()
    if pipeline is not None and (REPO / REGISTER).exists():
        live = parse_rows((REPO / REGISTER).read_text(encoding="utf-8"))
        ok(live is not None and len(live) > 0,
           "N7 positive control: the live register parses and is non-empty, so "
           "the population this guard grades is real rather than a fixture")

    print(f"operator-owed: self-test OK — {fired} planted controls all fire")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true",
                    help="plant the failure paths and exit")
    ap.add_argument("--verbose", action="store_true",
                    help="print a grade line per due owed row")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    return check(REPO, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
