#!/usr/bin/env python3
"""Fail when a `# allow-degraded:` annotation lacks an owner + an unexpired expiry.

WHY
---
`# allow-degraded: <reason>` is the escape hatch that tells `artifact-validity-guard`
a producer/fetch step is ALLOWED to swallow its own failure (e.g. an off-VM candle
fetch that degrades to an honest n=0). Left unenforced it is exactly the **silence
list** that `KNOWN_VACUOUS` in `check_artifact_validity.py` was deliberately designed
NOT to be: an un-owned, never-expiring exception that sits forever
(`BL-20260730-ALLOW-DEGRADED-NEEDS-EXPIRY`). The four annotations in the macro
backfill/grade workflows carried a backlog id but no expiry, so nothing forced a
re-review of whether the degradation was still acceptable.

This guard mirrors `known_vacuous_problems()`: every real `# allow-degraded:`
annotation MUST carry
  (a) a tracking id that RESOLVES to an OPEN row in a live register, and
  (b) an `until:YYYY-MM-DD` date; the guard FAILS once that date has passed —
so a degradation exception can never quietly become permanent, and an unowned one
can never be added.

⚠️ RE-POINTED 2026-09-22 (E45) — IT WAS UNSATISFIABLE, WHICH IS WORSE THAN DEAD
-------------------------------------------------------------------------------
Found by the E42 lane hitting it in USE, not by a sweep of path constants, and
it does not present as a broken guard: it presents as an ordinary finding whose
remedy line — *"file the row"* — cannot be performed. A session that does not
stop to check the register either invents a plausible `BL-` id that dangles
forever, or weakens the guard to get past it. Either way the population of
genuinely-degraded paths quietly shrinks.

⚠️ THE MECHANISM IS NOT THE ONE THE REPORT NAMED, AND THE DIFFERENCE MATTERS.
The report said the id universe is EMPTY because `docs/claude/*backlog*.json`
was archived. MEASURED 2026-09-22 by running it rather than inferring:
`check_backlog_refs.filed_ids()` returns **29** ids, all `FU-`, from
`comms/follow_ups.json` — that register is alive. The real defect is one level
earlier: **`check_backlog_refs.REF` does not match a `PI-` id at all**
(`(?:BL|MB|FU)-\d{8}-…`), so a citation of the live intake fails on *"names no
backlog id"*, not on *"resolves to nothing"*. And a `BL-` id that DOES live in
`PIPELINE.jsonl` fails the other way, because `filed_ids` never reads that
store. Both were reproduced with planted annotations. So: **0 of 1,210
PIPELINE.jsonl rows were citable**, against a 29-id universe of `FU-` rows.

WHAT CHANGED, AND WHAT WAS DELIBERATELY NOT CHANGED
----------------------------------------------------
`docs/claude/work/PIPELINE.jsonl` joins the id universe, and an id is accepted
when it names an **OPEN** row there (`queued`/`due`/`routed`) or resolves
through `filed_ids()` as before. A terminal row is refused for the same reason
an expired `until:` is: a `done`/`killed` row will never come back, so it cannot
force the re-review this guard exists to force.

⚠️ **`check_backlog_refs.REF` IS NOT WIDENED, on purpose.** It is shared, and
`check_backlog_refs --all` sweeps `docs`, `scripts`, `.github`, `config` and
`src` — teaching it `PI-` would make every `PI-` mentioned in prose anywhere a
reference it must resolve, on a probe that `main-tree-watch.yml` runs on a
cadence. That is a real question and a much larger blast radius than this
annotation format; it is filed rather than decided here. The id matching below
is LOCAL to this module for that reason.

Detection is precise, not presence-only (the `new-table-wiring-guard` lesson — a guard
cheaper to lie to than to satisfy is worse than none): a marker counts only in its
COMMENT form `# allow-degraded: <payload>`, and a payload containing `<` is a syntax
placeholder (this module's own examples, the guard-workflow's docstring) and is skipped.
So the guard never flags the very text that documents it, without a file allow-list.

Stdlib-only.

Usage:
  python scripts/ops/check_allow_degraded.py                 # full scan (CI)
  python scripts/ops/check_allow_degraded.py --today 2026-12-01
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_backlog_refs import REF, filed_ids  # noqa: E402  (single source of id resolution)

REPO = pathlib.Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SCAN_DIRS = (".github", "scripts", "src", "config")
SCAN_SUFFIXES = {".yml", ".yaml", ".py", ".sh"}

# The COMMENT form only: `# allow-degraded: <payload>`. A bare `allow-degraded:` inside
# a string literal (the guard-workflow's `'allow-degraded:' not in buf` detection) is
# NOT preceded by `# ` and so is correctly not a marker.
MARKER = re.compile(r"#\s*allow-degraded:\s*(?P<payload>.*)$")
UNTIL = re.compile(r"until:(\d{4}-\d{2}-\d{2})")


#: The live follow-through pipeline — the post-reset intake for anything that
#: needs picking up later, and therefore the register an allow-degraded
#: exception is owned by.
PIPELINE = "docs/claude/work/PIPELINE.jsonl"

#: An OPEN pipeline row can still come back. A terminal one cannot, which is why
#: citing one is refused for the same reason an expired `until:` is.
_OPEN_STATES = ("queued", "due", "routed")

#: A token SHAPED like a tracking id. The authority on whether it resolves is
#: MEMBERSHIP in the universe below — this only decides whether the payload
#: *named* something.
#:
#: ⚠️ IT EXISTS SO TWO FINDINGS DO NOT COLLAPSE INTO ONE. Without it, a `PI-` id
#: that is simply absent from the store reports as *"names no tracking id"*,
#: because the shared `REF` pattern does not match `PI-` — sending a reader to
#: hunt for a register when the real fix is a typo. *"We could not resolve this"*
#: and *"you named nothing"* are different statements with different remedies,
#: which is the collapse this repo has a guard family about.
#:
#: The live store carries at least four shapes (`PI-`, `BL-`, `MB-`, `B2-…`), so
#: the prefix is left open and the STRUCTURE carries the precision: at least two
#: hyphen-separated segments. That is what keeps ordinary prose out — `OPEN-ITEMS`
#: and `allow-degraded` have one hyphen or lowercase and are not ids.
_ID_SHAPE = re.compile(r"\b[A-Z][A-Z0-9]{0,3}(?:-[A-Z0-9]+){2,}\b")


class CouldNotLook(RuntimeError):
    """The register could not be read. NOT 'nothing is filed'."""


def pipeline_ids(repo: pathlib.Path) -> set[str]:
    """Ids of OPEN rows in the pipeline. Raises CouldNotLook rather than lying.

    ⚠️ AN UNREADABLE STORE MUST NOT READ AS AN EMPTY ONE. That collapse would
    turn every correctly-owned annotation in the tree into a dangling reference
    at once — the 1,574-false-finding shape `check_backlog_refs.filed_ids_with_state`
    has its own measurement of.
    """
    path = repo / PIPELINE
    if not path.is_file():
        raise CouldNotLook(f"{PIPELINE} is missing")
    out: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise CouldNotLook(f"{PIPELINE}:{lineno} did not parse: {exc}") from exc
        if isinstance(row, dict) and isinstance(row.get("id"), str) \
                and row.get("state") in _OPEN_STATES:
            out.add(row["id"])
    return out


def marker_problems(payload: str, filed: set[str], today: str) -> list[str]:
    """Problems with ONE annotation payload ([] = a well-formed, unexpired exception)."""
    problems: list[str] = []
    # Two ways to name an id, and the SECOND is why this guard was unsatisfiable.
    # `REF` (shared with check_backlog_refs) matches BL-/MB-/FU- only, so a live
    # `PI-` id read as "names no id at all" rather than as an unresolved one.
    # Tokens are matched against the universe by MEMBERSHIP instead.
    ids = list(dict.fromkeys(REF.findall(payload) + _ID_SHAPE.findall(payload)))
    if not ids:
        problems.append("names no tracking id that resolves — an un-owned "
                        "degradation exception is the silence list KNOWN_VACUOUS "
                        "exists not to be. Cite an OPEN row id from "
                        f"{PIPELINE} (or a filed FU- id)")
    else:
        dangling = [i for i in ids if i not in filed]
        if dangling:
            problems.append(f"id(s) resolve to NOTHING OPEN: {', '.join(dangling)} "
                            f"— file the row in {PIPELINE}, fix a typo, or (if the "
                            f"row is already `done`/`killed`) cite one that can "
                            f"still come back: a closed row cannot force the "
                            f"re-review this annotation exists to force")
    m = UNTIL.search(payload)
    if not m:
        problems.append("no `until:YYYY-MM-DD` — a degradation exception must expire so it "
                        "cannot become permanent (mirrors KNOWN_VACUOUS's `until`)")
    elif today > m.group(1):
        problems.append(f"EXPIRED on {m.group(1)} — re-justify the degradation (is it still "
                        "acceptable?) and bump `until:`, or remove the annotation if the "
                        "underlying issue is fixed")
    return problems


# This module is the marker's DEFINITION site — its docstring necessarily quotes
# `# allow-degraded:` in the comment form to explain it. A guard does not police its
# own definition (the one legitimate self-exclusion; every other file is scanned).
SELF = pathlib.Path(__file__).resolve()


def scan(repo: pathlib.Path, today: str) -> tuple[list[dict], int]:
    """Return (findings, n_valid_markers) over the scanned tree.

    Raises CouldNotLook when the pipeline cannot be read — the caller exits 2.
    """
    filed = filed_ids(repo) | pipeline_ids(repo)
    findings: list[dict] = []
    n_valid = 0
    for d in SCAN_DIRS:
        root = repo / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            if p.suffix.lower() not in SCAN_SUFFIXES:
                continue
            if p.resolve() == SELF:
                continue  # the guard's own definition file (see SELF above)
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                m = MARKER.search(line)
                if not m:
                    continue
                payload = m.group("payload")
                if "<" in payload:
                    continue  # syntax placeholder (docs / this module's own examples)
                probs = marker_problems(payload, filed, today)
                if probs:
                    findings.append({"file": str(p.relative_to(repo)), "line": lineno,
                                     "payload": payload.strip()[:120], "problems": probs})
                else:
                    n_valid += 1
    return findings, n_valid


def _self_test() -> int:
    """Plant an annotation against the RE-POINTED universe and prove both calls.

    ⚠️ THE REASON THIS EXISTS (E45, 2026-09-22). This guard was UNSATISFIABLE and
    still printed `OK — 0 annotation(s)`, because the tree happens to carry none.
    A guard that accepts everything is the same defect as one that grades
    nothing, so the bar is both directions: a valid live citation must PASS and a
    bad one must FAIL. Every positive below is paired with its negative control.
    """
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    universe = {"PI-20260101-LIVE-ROW", "FU-20260510-001"}
    future, past = "2099-01-01", "2020-01-01"
    today = "2026-09-22"

    # ── N1 THE CASE THAT COULD NOT BE WRITTEN BEFORE ──────────────────────
    ok(marker_problems(f"PI-20260101-LIVE-ROW until:{future} the fetch degrades "
                       "to an honest n=0", universe, today) == [],
       "N1 a live PIPELINE row id with a future expiry PASSES — before the "
       "re-point this exact annotation failed on `names no backlog id`, because "
       "the shared REF pattern does not match `PI-` at all")

    # ── P1 an id that resolves to nothing OPEN ────────────────────────────
    probs = marker_problems(f"PI-20260101-NOT-FILED until:{future} x", universe, today)
    ok(any("resolve to NOTHING OPEN" in p for p in probs),
       "P1 a PI- id absent from the store FAILS as unresolved — the planted "
       "positive against the re-pointed subject")
    ok(not any("names no tracking id" in p for p in probs),
       "P1b ...and it is reported as UNRESOLVED, not as 'no id named'. Those are "
       "different findings with different fixes, and collapsing them is what "
       "sent a session hunting for a register instead of for a typo")

    # ── P2 a TERMINAL row cannot own an exception ─────────────────────────
    # (`pipeline_ids` only returns OPEN rows, so a closed id is simply absent.)
    ok(any("cannot force the" in p
           for p in marker_problems(f"PI-20260101-CLOSED-ROW until:{future} x",
                                    universe, today)),
       "P2 citing a closed row FAILS, and the message says why: a done/killed "
       "row will never come back, so it cannot force a re-review")

    # ── P3/P4 the original two rules still bite ───────────────────────────
    ok(any("names no tracking id" in p
           for p in marker_problems(f"until:{future} no owner", universe, today)),
       "P3 an annotation naming no id at all still FAILS")
    ok(any("until:" in p for p in
           marker_problems("PI-20260101-LIVE-ROW no expiry", universe, today)),
       "P4 a live id with NO expiry still FAILS — an exception that cannot "
       "expire is the permanent silence this guard exists to prevent")
    ok(any("EXPIRED" in p for p in
           marker_problems(f"PI-20260101-LIVE-ROW until:{past} x", universe, today)),
       "P5 an EXPIRED annotation FAILS even with a perfectly live id")

    # ── N2 the legacy FU- path is not broken by the widening ──────────────
    ok(marker_problems(f"FU-20260510-001 until:{future} x", universe, today) == [],
       "N2 a filed FU- id still passes — the widening ADDS a register, it does "
       "not replace one, and 29 FU- ids were live when this was written")

    # ── P6 an unreadable store is COULD NOT LOOK, never an empty universe ──
    import tempfile
    td = pathlib.Path(tempfile.mkdtemp())
    (td / "docs" / "claude" / "work").mkdir(parents=True)
    try:
        pipeline_ids(td)
        ok(False, "P6 a missing store must raise")
    except CouldNotLook:
        ok(True, "P6 ⚠️ a MISSING pipeline raises CouldNotLook — reading it as an "
                 "empty universe would turn every correctly-owned annotation in "
                 "the tree into a dangling reference at once")
    (td / PIPELINE).write_text("{not json\n")
    try:
        pipeline_ids(td)
        ok(False, "P6b an unparseable store must raise")
    except CouldNotLook:
        ok(True, "P6b ...and so does an unparseable line")

    # ── N3 the real store is readable and non-empty, or this proves nothing ─
    live = pipeline_ids(REPO)
    ok(len(live) > 0,
       "N3 positive control: the live pipeline yields OPEN row ids, so the "
       "universe this guard resolves against is real rather than a fixture")

    print(f"allow-degraded: self-test OK — {fired} planted controls all fire "
          f"({len(live)} open row id(s) citable in the live store)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--self-test", action="store_true",
                    help="plant annotations against the id universe and prove both calls")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD for expiry (default: today UTC)")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    today = args.today or datetime.now(timezone.utc).date().isoformat()
    try:
        findings, n_valid = scan(pathlib.Path(args.repo_root), today)
    except CouldNotLook as exc:
        print(f"::warning::allow-degraded: COULD NOT LOOK — {exc}. This is not "
              f"'every annotation is unowned'; nothing was graded.")
        return 2

    if not findings:
        print(f"OK — {n_valid} `# allow-degraded:` annotation(s), each naming an OPEN "
              f"row id and an unexpired `until:`. Universe: {len(filed_ids(pathlib.Path(args.repo_root)))} "
              f"filed id(s) + {len(pipeline_ids(pathlib.Path(args.repo_root)))} open "
              f"{PIPELINE} row(s).")
        return 0

    print("::error::`# allow-degraded:` annotation(s) missing an owner or an unexpired "
          "expiry. Unenforced, allow-degraded is the silence list KNOWN_VACUOUS was "
          "designed not to be (BL-20260730-ALLOW-DEGRADED-NEEDS-EXPIRY).")
    for f in findings:
        print(f"  {f['file']}:{f['line']}: `{f['payload']}`")
        for prob in f["problems"]:
            print(f"      - {prob}")
    print("")
    print("Fix: annotate as `# allow-degraded: <id> until:YYYY-MM-DD <reason>`, where "
          f"<id> names an OPEN row in {PIPELINE} (mint one with "
          "`python3 scripts/ops/pipeline.py --mint-id <session>`) and the expiry is in "
          "the future, so the degradation is re-reviewed rather than becoming permanent.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
