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
For each owed row, the number of commits to `PIPELINE.jsonl` since that row's
own content last changed — read from `git log`, not from anybody's self-report.

    carries = (leading register commits whose content for this id == the
               working tree's) - 1, floored at 0

The `- 1` is the commit that MADE the current content: a row just edited and
committed reads 0 carries, one later register commit that left it alone reads
1, two reads 2 and escalates.

⚠️ ONLY A **DUE** ROW IS GRADED, AND THAT IS THE DEFER PATH, NOT A LOOPHOLE.
`scripts/ops/pipeline.py::is_due` is the one home for that question, and a row
that is not due is legitimately waiting behind its own declared condition —
which is precisely the canonical rule's third way out, *"defer behind a named
trigger event"*. Grading a not-yet-due row would punish the correct behaviour.
MEASURED 2026-09-22: **9 of the 90** open owed rows are due, so this is the
difference between a finding and a wall of 90.

⚠️ THE COUNT UNDER-REPORTS AND CAN NEVER OVER-REPORT. A session that never
touches the register at all leaves no commit and is invisible here.

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
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = pathlib.Path(__file__).resolve().parents[2]

REGISTER = "docs/claude/work/PIPELINE.jsonl"

#: The schema's own name for "this is owed to the operator".
OWED_ACTION = "ask_operator"

#: How many register commits may pass with a due row unmoved before it escalates.
CARRY_LIMIT = 2

#: ── THE DEBT LIST — MEASURED 2026-09-22, AND IT MAY ONLY SHRINK ───────────
#:
#: Every owed row that was ALREADY due and already carried past the limit at the
#: moment this guard was re-pointed. Arming against them would red-wall every
#: PR on day one over questions no contributor can answer — the shape
#: `check_pr_queue_watch.py` refuses in terms, and the shape § "could not
#: measure is its own outcome" records burying the only fact that mattered under
#: 117 findings from one absent import.
#:
#: ⚠️ THIS IS THE `check_soak_registered.py` PATTERN ON PURPOSE, and what makes
#: it acceptable is that it is NOT SILENT: every id is a visible line here, in
#: the diff, under a comment saying the list may only SHRINK, and the count
#: prints on every run.
#:
#: ⚠️ A BASELINED ID THAT NO LONGER EXISTS, OR IS NO LONGER OWED, IS A FAILURE.
#: The list cannot accumulate slots nobody can audit. Removing an id is the good
#: direction and needs no ceremony: act on it, move it, defer it behind a
#: condition, or kill it with a `terminal_reason` — then delete the line.
#:
#: MEASURED: 8 of the 9 due owed rows on 2026-09-22 (carries 4 … 23 against a
#: limit of 2). The ninth, `PI-20260922-UTN353OZ-0001`, read 0 carries and is
#: deliberately NOT listed — it is the live proof that the guard's clean state
#: is reachable without the hatch.
BASELINE_2026_09_22: Dict[str, str] = {
    "PI-20260921-M08": "carried 23 at the re-point",
    "PI-20260921-E01": "carried 21 at the re-point",
    "PI-20260921-0005": "carried 21 at the re-point",
    "PI-20260921-E16-BALANCE-FETCHER-PROP-BRANCH-RARELY-REACHED":
        "carried 18 at the re-point",
    "PI-20260921-E16-PROP-TICKET-TTL-SHORTER-THAN-THE-HUMAN":
        "carried 18 at the re-point",
    "B2-ALPACA-MIRROR-ROSTER-EDIT-AWAITING-OPERATOR":
        "carried 14 at the re-point",
    "PI-20260922-R5-CLAUDE-MD-FEE-ONLY-CORPUS-CLAIM-IS-STALE-FOR-FOUR-HARNESS-FAMILIES":
        "carried 4 at the re-point",
    "PI-20260922-R5-PATH-STATS-MISSING-FOR-THE-12-LEGS-WHOSE-SERIES-IS-IN-A-DEAD-TMP-DIR":
        "carried 4 at the re-point",
}


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


def measure_carries(
    repo: pathlib.Path,
    path: str,
    current: Dict[str, Any],
    shas: List[str],
) -> Tuple[Dict[str, Optional[int]], Dict[str, int]]:
    """Carries per id, and observed transitions per id, measured from git.

    Carries is ``None`` for every id when the register has no history yet — no
    carry EXISTS to count, which is `not_measurable`, never zero.
    """
    carries: Dict[str, Optional[int]] = {}
    transitions: Dict[str, int] = {row_id: 0 for row_id in current}

    if not shas:
        return {row_id: None for row_id in current}, transitions

    history: List[Optional[Dict[str, Any]]] = [
        _rows_at(repo, sha, path) for sha in shas]

    for row_id, row in current.items():
        leading = 0
        for snapshot in history:
            if snapshot is None:
                break
            if snapshot.get(row_id) == row:
                leading += 1
                continue
            break
        carries[row_id] = max(0, leading - 1)

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
    return carries, transitions


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

    owed = owed_rows(rows, pipeline)
    due = {rid: r for rid, r in owed.items() if pipeline.is_due(r)}

    shas = register_commits(repo, path)
    carries, transitions = measure_carries(repo, path, due, shas)

    print(f"operator-owed: {len(rows)} row(s) in {path} · {len(owed)} owed to "
          f"the operator · {len(due)} of those DUE now")
    print(f"operator-owed: register commits measured = {len(shas)} "
          f"(the denominator — a carry count over a short history is a weak "
          f"reading, not a clean one)")
    print(f"operator-owed: {len(BASELINE_2026_09_22)} row(s) carried as dated "
          f"2026-09-22 debt (the list may only SHRINK)")

    not_measurable = [rid for rid, c in carries.items() if c is None]
    if not_measurable:
        print(f"operator-owed: {len(not_measurable)} row(s) NOT MEASURABLE — the "
              f"register history does not cover them, so no carry EXISTS to "
              f"count. This is 'we did not look', NOT a pass: "
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
            print(f"  - {rid}: carries={carries.get(rid)} "
                  f"transitions={transitions.get(rid, 0)}"
                  + (" [BASELINED]" if rid in BASELINE_2026_09_22 else ""))

    escalated = sorted(
        rid for rid, c in carries.items()
        if c is not None and c >= CARRY_LIMIT and rid not in BASELINE_2026_09_22)

    stale = sorted(rid for rid in BASELINE_2026_09_22 if rid not in due)

    if not escalated and not stale:
        carried_debt = sorted(set(BASELINE_2026_09_22) & set(due))
        print(f"operator-owed: OK — {len(owed)} owed row(s), none due-and-carried "
              f"past the limit of {CARRY_LIMIT}")
        if carried_debt:
            print("  Carried debt (each is a question the operator has not "
                  "answered): " + ", ".join(carried_debt))
        return 0

    for rid in escalated:
        print()
        print(f"::error::operator-owed: {rid} is owed to the operator, is DUE, "
              f"and has been carried across {carries[rid]} register commit(s) "
              f"without changing (limit {CARRY_LIMIT}). Re-listing it is what "
              f"this guard replaces.")
        print("  To clear it, do ONE of these — none of them is 'ask again':")
        print("   1. ACT on it: record the answer and close the row with a "
              "`terminal_reason` (state `done`).")
        print("   2. MOVE it: change `next_action` to the thing that is "
              "actually next, and say so in `what`.")
        print("   3. DEFER it honestly: give `due_when` a condition or a date "
              "that has not passed, so it comes back when it can move.")
        print("   4. KILL it: state `killed` + a `terminal_reason` saying why "
              "it is no longer owed. `killed` is a first-class outcome.")

    for rid in stale:
        print()
        print(f"::error::operator-owed: BASELINE STALE — {rid} is on the dated "
              f"2026-09-22 debt list but is no longer a due owed row. Delete "
              f"the line. The list may only SHRINK, and an id outliving its row "
              f"is a slot a future question could quietly reuse.")

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
    """
    import shutil
    import tempfile

    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    def row(rid, *, action=OWED_ACTION, state="queued", due=None, what="q"):
        return {
            "id": rid,
            "what": what,
            "origin": {"kind": "session", "ref": "s1", "rerun": "re-ask"},
            "due_when": due or {"kind": "date", "due_date": "2020-01-01"},
            "next_action": action,
            "state": state,
        }

    def build(rows_per_commit):
        """A git repo whose register is committed once per element.

        ⚠️ EACH COMMIT CARRIES A UNIQUE FILLER ROW, and that is not padding.
        `register_commits` reads `git log -- <path>`, so a commit that changes
        nothing in the file is not a register commit at all — which is exactly
        what a carry IS in the real store: someone appended a DIFFERENT row and
        left this one alone. Without the filler the fixture would silently
        measure zero commits and every positive below would pass vacuously.
        """
        td = pathlib.Path(tempfile.mkdtemp())
        (td / "docs" / "claude" / "work").mkdir(parents=True)
        subprocess.run(["git", "-C", str(td), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(td), "config", "user.email", "s@e.com"],
                       check=True)
        subprocess.run(["git", "-C", str(td), "config", "user.name", "s"], check=True)
        for i, rows in enumerate(rows_per_commit):
            filler = row(f"PI-FILL-{i}", action="dispatch_lane")
            body = "// header\n" + "\n".join(
                json.dumps(r) for r in [*rows, filler]) + "\n"
            (td / REGISTER).write_text(body, encoding="utf-8")
            subprocess.run(["git", "-C", str(td), "add", "-A"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(td), "commit", "-qm", f"c{i}"],
                           check=True, capture_output=True)
        return td

    import io
    import contextlib

    def run(td, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check(td, **kw)
        return rc, buf.getvalue()

    saved = dict(BASELINE_2026_09_22)
    try:
        BASELINE_2026_09_22.clear()

        # ── P1 THE PLANTED VIOLATION ───────────────────────────────────────
        # A due row owed to the operator, unchanged across three register
        # commits: carries = 2, the limit.
        owed = row("PI-OWED")
        td = build([[owed], [owed, row("PI-OTHER", action="dispatch_lane")],
                    [owed, row("PI-OTHER", action="dispatch_lane"),
                     row("PI-THIRD", action="dispatch_lane")]])
        rc, out = run(td)
        ok(rc == 1 and "PI-OWED" in out,
           "P1 a DUE owed row carried across the limit FAILS — the planted "
           "positive against the re-pointed subject")
        ok("Re-listing it is what this guard replaces" in out,
           "P1b and the failure says what it is for")
        ok("killed" in out,
           "P1c and it offers the four ways out, `killed` among them")

        # ── N1 the same row, MOVED on the last commit ──────────────────────
        moved = dict(owed, what="answered: yes")
        td = build([[owed], [owed], [moved]])
        rc, out = run(td)
        ok(rc == 0, "N1 the same row whose content CHANGED on the last commit "
                    "passes — moving it is the fix, and the fix works")

        # ── N2 NOT DUE is the defer path, not a loophole ───────────────────
        deferred = row("PI-DEFER",
                       due={"kind": "date", "due_date": "2099-01-01"})
        td = build([[deferred], [deferred], [deferred]])
        rc, out = run(td)
        ok(rc == 0, "N2 a row deferred behind a future date is NOT graded — "
                    "that is the canonical rule's third way out, and grading it "
                    "would punish the correct behaviour")

        # ── N3 a row nobody owes the operator is not this guard's business ──
        lane = row("PI-LANE", action="dispatch_lane")
        td = build([[lane], [lane], [lane]])
        rc, out = run(td)
        ok(rc == 0, "N3 a due row whose next_action is NOT ask_operator is out "
                    "of population — the guard grades what is owed, not the "
                    "whole pipeline")

        # ── N4 a TERMINAL row is out of population ─────────────────────────
        done = row("PI-DONE", state="done")
        done["terminal_reason"] = "answered"
        td = build([[done], [done], [done]])
        rc, out = run(td)
        ok(rc == 0, "N4 a closed row is not carried — it ended, which is the "
                    "outcome the rule wants")

        # ── N5 under the limit is silent ───────────────────────────────────
        td = build([[owed], [owed]])
        rc, out = run(td)
        ok(rc == 0, "N5 one carry is under the limit of 2 and stays quiet — the "
                    "guard escalates, it does not nag")

        # ── P2 the BASELINE exempts, and is COUNTED rather than hidden ─────
        td = build([[owed], [owed], [owed]])
        BASELINE_2026_09_22["PI-OWED"] = "planted debt"
        rc, out = run(td)
        ok(rc == 0 and "1 row(s) carried as dated" in out,
           "P2 a baselined id is exempt AND its debt count prints — an "
           "exemption nobody can see is how a baseline becomes a hole")
        ok("Carried debt" in out and "PI-OWED" in out,
           "P2b ...and the row itself is named on the clean path, so the hatch "
           "cannot conceal what it is carrying")

        # ── P3 a baseline entry whose row is gone FAILS ────────────────────
        BASELINE_2026_09_22.clear()
        BASELINE_2026_09_22["PI-GHOST"] = "planted stale"
        rc, out = run(td)
        ok(rc == 1 and "BASELINE STALE" in out and "PI-GHOST" in out,
           "P3 a baselined id that is no longer a due owed row FAILS — the list "
           "may only shrink")
        BASELINE_2026_09_22.clear()

        # ── P4 could not look is 2, and is never a pass ────────────────────
        (td / REGISTER).write_text("{not json\n", encoding="utf-8")
        rc, out = run(td)
        ok(rc == 2 and "COULD NOT LOOK" in out,
           "P4 ⚠️ an unparseable register exits COULD NOT LOOK (2), never 0 — "
           "reading it as an empty register would report every owed row as gone")

        (td / REGISTER).unlink()
        rc, out = run(td)
        ok(rc == 2 and "COULD NOT LOOK" in out,
           "P4b ...and a MISSING register is the same state, which is exactly "
           "what this guard read on every run from 2026-09-21 to the re-point")

        shutil.rmtree(td, ignore_errors=True)
    finally:
        BASELINE_2026_09_22.clear()
        BASELINE_2026_09_22.update(saved)

    # The shipped baseline must describe the REAL register, not a fixture.
    pipeline = _pipeline_module()
    if pipeline is not None and (REPO / REGISTER).exists():
        rows = parse_rows((REPO / REGISTER).read_text(encoding="utf-8")) or {}
        ok(not (set(BASELINE_2026_09_22) - set(rows)),
           "N6 every baselined id exists in the live register — measured "
           "against the real file, not a fixture")

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
