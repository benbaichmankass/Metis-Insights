#!/usr/bin/env python3
"""document-index-guard — the register cannot silently stop being true.

WHY. On 2026-09-07 a manager reported roadmap status to the operator off
`docs/research/WORKPLAN-2026-08-14.md`, which declares "Status: ACTIVE" in its
own header while being three generations superseded. The operator's diagnosis
was that *"a lot of things probably look fine if you just open them on their
own"*, and asked for one centralized table.

A table alone does not fix that, for two reasons that this guard is the answer
to. A table nobody updates decays into another stale surface; and a table that
disagrees with the file a reader actually opens is worse than no table, because
now two surfaces confidently contradict each other. So:

    R1  a document exists in the population and is NOT registered
    R2  a registered row names a file that NO LONGER EXISTS
    R3  a file's own header status DISAGREES with its index row
    R6  a committed row's status is NOT what a fresh build_rows() computes
    R4  a row is `superseded` without naming a `superseded_by`
    R5  a row uses a category/status outside the closed sets

PLANTED-POSITIVE CONTROL. `--self-test` feeds each rule a known-bad input and
FAILS unless the rule fires, plus a clean input that must produce no findings
(so a rule that always fires is caught too). It runs on every invocation of the
guard in CI. `CLAUDE.md` is explicit that a finding without a permanent
detector recurs and that a guard nobody has seen fail is not evidence -- and
this repo has already been bitten by presence-only markers that were cheaper to
lie to than to satisfy (`new-table-wiring-guard`). On a clean tree this guard is
otherwise only ever observed PASSING, which is the state a guard is least
useful in.

⚠️ THE RULE ENGINE IS ONLY HALF THE GUARD, AND THE OTHER HALF WENT UNGRADED
UNTIL MI-162. `--self-test` also runs `population_control()`, which plants REAL
files on disk and asserts `document_index.population()` sees them. The rule
engine is a pure function over a population it is HANDED, so it cannot detect a
population that was filtered before it ran — and on 2026-09-07 that is exactly
what happened: `POPULATION_GLOBS` passed `docs/**/*.md` to `git ls-files` as a
PATHSPEC, where `**/` needs a literal intervening slash, silently excluding all
28 `.md` files sitting directly in `docs/` (`CLAUDE-RULES-CANONICAL.md`,
`ARCHITECTURE-CANONICAL.md`, `api-tier-policy.md`, `workplan.md` …). Every rule
case above passed, correctly, while the guard printed `population=971
registered=971` / `document-index: OK` — 100% coverage of a population that
excluded its own most important members
(`BL-20260907-DOCUMENT-INDEX-POPULATION-GLOB-EXCLUDES-EVERY-TOP-LEVEL-DOCS-FILE`).
Verified by re-running this self-test against the old pathspec: 13/13 rule cases
PASS and the population control is the only thing that FAILS.

The rule engine is a PURE FUNCTION over (population, rows, headers, generated),
so the policy is arguable in tests rather than against the live tree.

Run:
    python3 scripts/ci/check_document_index.py
    python3 scripts/ci/check_document_index.py --self-test
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parents[2]
INDEX_REL = "docs/DOCUMENT-INDEX.md"

ROW_RE = re.compile(
    r"^\|\s*`(?P<path>[^`]+)`\s*\|\s*(?P<category>[a-z_]+)\s*\|\s*(?P<status>[a-z_]+)\s*\|"
    r"\s*(?P<superseded_by>[^|]*?)\s*\|\s*(?P<last_verified>[^|]*?)\s*\|"
    r"\s*(?P<basis>[^|]*?)\s*\|\s*(?P<note>[^|]*?)\s*\|\s*$"
)
BEGIN = "<!-- DOCUMENT-INDEX-ROWS-BEGIN -->"
END = "<!-- DOCUMENT-INDEX-ROWS-END -->"


def _builder():
    spec = importlib.util.spec_from_file_location(
        "_di", REPO / "scripts" / "ops" / "document_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# R6 — the row itself, against the computation that is supposed to produce it
# ---------------------------------------------------------------------------
DRIFT_COMPUTED, DRIFT_UNCOMPUTABLE = "computed", "uncomputable"


def row_status_drift(rows: List[Dict[str, str]],
                     computed: Optional[Dict[str, str]]) -> tuple:
    """`[(path, committed_status, computed_status)]` where the two disagree.

    ⚠️ WHY R3 CANNOT SEE THIS, which is the whole reason R6 exists. R3 compares
    a file's HEADER against its ROW IN THE COMMITTED INDEX, and both surfaces
    are written by the same run of `--write`. So they agree with each other and
    can BOTH disagree with `build_rows()`, which is the computation that is
    supposed to produce them. Measured on a clean `origin/main`, 2026-09-12:
    **47 rows** (39 when the finding was filed hours earlier — the population
    GREW while the row sat open) say `live` where a fresh build computes
    `unknown`, and `document-index: OK` printed on that same tree. `unknown` is
    `status_for`'s rung 7, *"nobody has checked"*, and it carries a rendered
    warning telling a reader not to act on the document as current — so those
    documents present as `live` while the register's own computation says
    nobody established that.

    Returns `(state, rows)` where state is `computed` or `uncomputable`.

    ⚠️ THE STATE IS A RETURN VALUE AND NOT A BARE EMPTY LIST, AND A PLANTED
    DEFECT IS WHY. The first version returned `[]` for BOTH "nothing drifted"
    and "the fresh row set could not be built", and a mutation replacing
    `computed is None` with `computed = {}` passed the whole self-test —
    because at that point the two are behaviourally identical. That is this
    repo's own collapsed-states defect, committed inside the check written to
    catch a different one. `uncomputable` is *we could not look*; it is never
    "no rows drifted", and `evaluate` must not raise a finding from it.
    """
    if computed is None:
        return (DRIFT_UNCOMPUTABLE, [])
    out = []
    for r in rows:
        p = r["path"]
        if p in computed and r["status"] != computed[p]:
            out.append((p, r["status"], computed[p]))
    return (DRIFT_COMPUTED, sorted(out))


# ---------------------------------------------------------------------------
# THE RULE ENGINE — pure
# ---------------------------------------------------------------------------
def evaluate(population: Set[str],
             rows: List[Dict[str, str]],
             headers: Dict[str, Optional[str]],
             generated: Set[str],
             categories: Set[str],
             statuses: Set[str],
             computed: Optional[Dict[str, str]] = None,
             scope: Optional[Set[str]] = None,
             enforce_all: bool = False,
             stubs: Optional[Dict[str, str]] = None) -> List[str]:
    """Return a list of findings. Empty list = the register is true.

    `headers[path]` is the status token read from that file's own stamp, or
    None when the file carries no stamp. A path absent from `headers` was not
    readable and is reported rather than assumed clean — "we could not look" is
    never folded into "we looked and it agreed".
    """
    findings: List[str] = []
    registered = {r["path"] for r in rows}

    # R6 — DIFF-SCOPED, and the wider number is CENSUSED by the caller.
    #
    # ⚠️ THE SCOPING IS NOT TIMIDITY, IT IS THE ONLY SHIPPABLE SHAPE. There are
    # 47 standing disagreements on `main` today; an unscoped rule would fail
    # every PR in the repo from the moment it merged, which is not a guard, it
    # is an outage. `session-registry-guard` is the precedent named in
    # CLAUDE.md: enforce narrowly, CENSUS everything else so the narrow
    # enforcement cannot hide the wider number.
    #
    # ⚠️ `scope is None` means *we could not establish what this diff touched*
    # — no `--base` — and it produces NO findings. That is deliberately not the
    # same as an empty scope (a diff that touched nothing relevant), and the
    # caller says which it had.
    #
    # ⚠️ THE MESSAGE STATES WHAT WAS MEASURED AND NAMES NO CAUSE. Why the
    # committed row says what it says is not established here — a row written
    # when the document was in ACTIVE_DOCS, a hand-edit, and a `--write` that
    # predates a change to `status_for` are all consistent with the same
    # evidence, and asserting one would be the unprovenanced-diagnostic class.
    #
    # ⚠️ `enforce_all=True` DROPS THE SCOPING, and it is shippable ONLY because
    # the residue is gone. R6's comment above recorded 47 standing
    # disagreements as the reason it could not be unscoped; MEASURED
    # 2026-09-13 against a fresh `build_rows()` the count is **46 at
    # b209768c2 and 0 at `origin/main`** — drained by the
    # `document_index.py --write` that rode PR #12122. This is the
    # `diagnostic-provenance-guard` shape exactly: a diff-scoped step plus an
    # ungated one, the ungated one added only once the residue reads zero, so
    # nothing is grandfathered and there is no standing audit for anyone to
    # forget to run.
    #
    # ⚠️ UNDER `enforce_all` AN UNCOMPUTABLE ROW SET IS A FINDING, and under
    # the scoped rule it is not. That asymmetry is deliberate: the scoped step
    # is one check among many and can afford *we could not look*, but an
    # UNGATED assertion that silently reports nothing when its own input could
    # not be built reads byte-identically to a clean tree — which is the
    # unasserted-denominator defect this repo files as sub-class C.
    drift_state, drifted = row_status_drift(rows, computed)
    if enforce_all and drift_state == DRIFT_UNCOMPUTABLE:
        findings.append(
            "R6 UNCOMPUTABLE (--all): a fresh build_rows() could not be "
            "computed, so the ungated drift assertion could not run. This is "
            "*we could not look*, NOT a clean register, and the ungated step "
            "fails rather than printing a reassuring nothing.")
    if (enforce_all or scope is not None) and drift_state == DRIFT_COMPUTED:
        in_scope = ({p for p, _c, _f in drifted}
                    if enforce_all else {p for p, _c, _f in drifted} & (scope or set()))
        for p, committed, fresh in drifted:
            if p not in in_scope:
                continue
            findings.append(
                f"R6 ROW DRIFT: {INDEX_REL} records status '{committed}' for {p}, "
                f"but a fresh build_rows() computes '{fresh}'. "
                + ("The ungated step reports every drifted row, so this one is "
                   "not necessarily yours — but the register is not coherent "
                   "until it is gone. "
                   if enforce_all and p not in (scope or set())
                   else "This diff touches that document, so it is yours to "
                        "reconcile. ")
                + "R3 cannot see this: "
                "the header and the row were written by the same run and agree "
                "with each other. Run: python3 scripts/ops/document_index.py "
                "--write, then READ what it changed before committing.")

    # R1 — a document exists and is not registered.
    # ⚠️ THE REMEDY NAMES THE ROW, because the old one named a COMMAND whose
    # diff is two orders of magnitude larger than the finding. MEASURED
    # 2026-09-13: on a tree one document ahead of the last `--write`,
    # `document_index.py --write` changed 65 files — 3 real and 62 one-line
    # `Doc status:` header rewrites in documents the change never touched —
    # and one of those runs erased the hand-written basis on 35 rows. This is
    # the same trap `document_index.py::carry_last_verified` already names for
    # the DATE ("the guard prescribed it, which made it a trap rather than a
    # footgun"); that fix carried the date and left the status surface moving.
    # The stub below is the generator's OWN output for this path, so pasting
    # it cannot drift from what `--write` would produce, and R6 checks that.
    for p in sorted(population - registered):
        stub = (stubs or {}).get(p)
        if stub:
            findings.append(
                f"R1 UNREGISTERED: {p} is in the document population but has "
                f"no row in {INDEX_REL}. Add THIS ONE LINE, in sorted position "
                f"(it is the generator's own output for this path, so R6 will "
                f"agree with it):\n      {stub}\n      Running "
                f"`document_index.py --write` also works and is what you want "
                f"if several documents are unregistered — but on a tree behind "
                f"the last write it re-decides every other document's status "
                f"too, so READ its diff before committing.")
        else:
            findings.append(
                f"R1 UNREGISTERED: {p} is in the document population but has no row "
                f"in {INDEX_REL}. Run: python3 scripts/ops/document_index.py --write")

    # R2 — a row names a file that no longer exists.
    for p in sorted(registered - population):
        findings.append(
            f"R2 GHOST ROW: {INDEX_REL} registers {p}, which is not in the "
            f"document population (deleted, renamed, or moved out of scope).")

    for r in sorted(rows, key=lambda x: x["path"]):
        p = r["path"]

        # R5 — closed vocabularies.
        if r["category"] not in categories:
            findings.append(
                f"R5 BAD CATEGORY: {p} has category '{r['category']}', which is "
                f"outside the closed set {sorted(categories)}.")
        if r["status"] not in statuses:
            findings.append(
                f"R5 BAD STATUS: {p} has status '{r['status']}', which is "
                f"outside the closed set {sorted(statuses)}.")

        # R4 — superseded must name its successor. `superseded` and
        # `closed_unfinished` are different facts; a superseded row that cannot
        # say BY WHAT has not recorded the fact it claims to record.
        if r["status"] == "superseded" and not r.get("superseded_by", "").strip(" —-"):
            findings.append(
                f"R4 SUPERSEDED WITHOUT SUCCESSOR: {p} is marked superseded but "
                f"names no superseded_by. Overtaken BY WHAT?")

        # R3 — the header a reader actually sees must agree with the row.
        if p in generated:
            continue  # waived; see the index's generated-documents section
        if p not in headers:
            findings.append(
                f"R3 UNREADABLE: {p} could not be read to check its header "
                f"status. Not assumed to agree.")
            continue
        h = headers[p]
        if h is None:
            findings.append(
                f"R3 UNSTAMPED: {p} carries no status header. A reader opening "
                f"it directly is told nothing — which is how "
                f"WORKPLAN-2026-08-14.md misled a manager on 2026-09-07.")
        elif h != r["status"]:
            findings.append(
                f"R3 DISAGREEMENT: {p} header says '{h}' but {INDEX_REL} says "
                f"'{r['status']}'. Two surfaces, two answers.")

    return findings


# ---------------------------------------------------------------------------
# Reading the live tree
# ---------------------------------------------------------------------------
# ── R7: the FILE must be well-formed before any rule about its CONTENT ──────
# Three states, never collapsed.
INDEX_CLEAN = "clean"
INDEX_CONFLICTED = "conflicted"
INDEX_UNREADABLE = "unreadable"          # we could not look — NOT a pass

#: git writes exactly seven characters, at the start of a line, followed by a
#: space and a label or nothing at all. Anchoring on that shape is what lets a
#: TABLE ROW that merely mentions the markers pass: every row in this document
#: begins with `|`, so a note quoting a conflict marker can never match.
_CONFLICT_OPEN = re.compile(r"^<<<<<<<(?: .*)?$")
_CONFLICT_CLOSE = re.compile(r"^>>>>>>>(?: .*)?$")
#: Corroboration only, NEVER a trigger. A line of bare `=` is also a valid
#: setext heading underline in markdown, so firing on it would invent findings
#: in a document nobody conflicted.
_CONFLICT_MID = re.compile(r"^=======$")


def index_integrity(text: Optional[str]) -> Tuple[str, List[Tuple[int, str]]]:
    """`(state, [(line_no, line), ...])` — is the index file itself intact?

    ⚠️ WHY THIS IS A RULE ABOUT THE FILE AND NOT ABOUT ITS ROWS. Every other
    rule here reads rows, and `parse_rows` skips anything that is not a row —
    so the three marker lines of an unresolved conflict are NOISE to it, BOTH
    conflicting copies of a row parse as ordinary registrations, and every rule
    is satisfied. MEASURED 2026-09-13 by planting it: `--all` printed
    `document-index: OK` and exited 0 on a file whose line 732 was
    `<<<<<<< HEAD`. No rule was misbehaving; there was no rule about the file
    being well-formed.

    It matters more since R6 became ungated: that backstop now runs on every PR,
    so a green document-index step reads as evidence the register is intact.

    ⚠️ THE REPO ALREADY DECIDED THIS CLASS FOR THE JSON HALF.
    `check_register_reserialization` carries an explicit conflict-markers case
    and grades such a file UNREADABLE — *we could not look* — rather than clean.
    It walks `docs/claude/**/*.json`, so this markdown register was outside it
    entirely. This is the same verdict for the same condition on the other half.

    ⚠️ DELIBERATELY SCOPED TO THIS ONE FILE. A repo-wide conflict-marker guard
    is a bigger decision — which paths, and whether a marker inside a test
    fixture or a doc ABOUT merge conflicts is a false positive — and the row
    that asked for this says in terms that it must not be smuggled in as a
    one-line fix. This module already scans exactly one file.
    """
    if text is None:
        return INDEX_UNREADABLE, []
    hits: List[Tuple[int, str]] = []
    triggered = False
    for i, line in enumerate(text.splitlines(), start=1):
        if _CONFLICT_OPEN.match(line) or _CONFLICT_CLOSE.match(line):
            triggered = True
            hits.append((i, line))
        elif _CONFLICT_MID.match(line) and triggered:
            # Corroboration, NEVER a trigger — it is reported only once an
            # unambiguous marker has already been seen, so a setext heading
            # underline in an otherwise clean file can neither fire this nor
            # appear in the output.
            hits.append((i, line))
    return (INDEX_CONFLICTED if triggered else INDEX_CLEAN), hits


def parse_rows(text: str) -> List[Dict[str, str]]:
    if BEGIN not in text or END not in text:
        raise SystemExit(
            f"document-index: FAIL — {INDEX_REL} is missing its "
            f"{BEGIN} / {END} row markers; the table cannot be parsed.")
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    rows = []
    for line in body.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        d = m.groupdict()
        d["path"] = d["path"].replace("\\|", "|")
        d["superseded_by"] = d["superseded_by"].strip("`").strip()
        rows.append(d)
    return rows


def read_headers(paths: Set[str], stamp_re: re.Pattern) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for p in paths:
        f = REPO / p
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # deliberately absent -> R3 UNREADABLE, never a silent pass
        m = stamp_re.search(text)
        out[p] = m.group("status") if m else None
    return out


# ---------------------------------------------------------------------------
# The planted-positive control
# ---------------------------------------------------------------------------
def population_control() -> int:
    """Plant real files on disk and assert the POPULATION BUILDER sees them.

    ⚠️ THIS IS THE CONTROL THAT WAS MISSING, AND ITS ABSENCE COST A CLEAN
    NEGATIVE. Every planted case below `self_test` feeds the RULE ENGINE known-bad
    input — and the rule engine has never been the defect. It passed throughout
    `BL-20260907-DOCUMENT-INDEX-POPULATION-GLOB-EXCLUDES-EVERY-TOP-LEVEL-DOCS-FILE`,
    correctly, because a rule engine handed a population of 971 cannot know that
    28 documents were filtered out before it ran. The guard duly printed
    `population=971 registered=971` and `document-index: OK` while
    `docs/CLAUDE-RULES-CANONICAL.md` (instruction hierarchy level 1) and
    `docs/ARCHITECTURE-CANONICAL.md` (level 2) had no row at all.

    So this control grades `document_index.population()` itself, against the
    filesystem, with the EXACT case that escaped: a `.md` sitting directly in
    `docs/`. It is the second defect of this class in that one function (see
    `population()`'s own docstring for the first), and both were found by
    running the thing against a real file rather than reasoning about it —
    which is what this control now does automatically, every CI run.

    A planted file that cannot be created FAILS the control. It never skips:
    a control that quietly opts out is the unasserted denominator again.
    """
    mod = _builder()
    tag = f"zz-document-index-population-control-{os.getpid()}"
    planted = {
        # The case that escaped: directly inside `docs/`. MUST be seen.
        "top-level .md in docs/": (REPO / "docs" / f"{tag}.md", True),
        # A nested one, so "sees the top level" cannot pass by the builder
        # having quietly stopped recursing.
        "nested .md under docs/": (REPO / "docs" / "claude" / f"{tag}.md", True),
        # A NEGATIVE: the population must not be "every file under docs/".
        # Without this, a pathspec of `docs/*` would pass the two above.
        "non-markdown file in docs/": (REPO / "docs" / f"{tag}.txt", False),
    }

    failures = 0
    try:
        for _, (path, _) in planted.items():
            path.write_text(f"# {tag}\n\nplanted by the document-index "
                            f"population control; deleted in the same run.\n",
                            encoding="utf-8")

        pop = set(mod.population())

        for label, (path, must_be_seen) in planted.items():
            rel = path.relative_to(REPO).as_posix()
            seen = rel in pop
            ok = seen == must_be_seen
            print(f"  {'PASS' if ok else 'FAIL'}  population sees {label}"
                  f" ({'expected' if must_be_seen else 'expected ABSENT'})")
            if not ok:
                failures += 1
                if must_be_seen:
                    print(f"        {rel} was planted on disk and is ABSENT from "
                          f"population() ({len(pop)} paths). The population "
                          f"builder is filtering out real documents — this is "
                          f"the clean negative, not a missing index row. Check "
                          f"POPULATION_GLOBS: these are git PATHSPECS, so "
                          f"`docs/**/*.md` needs `:(glob)` to mean what it looks "
                          f"like it means.")
                else:
                    print(f"        {rel} was planted and population() INCLUDED "
                          f"it. The population is wider than markdown documents.")
    except OSError as e:
        print(f"  FAIL  population control could not plant its files: {e}")
        print("        Not skipped: an unrunnable control is not a passing one.")
        failures += 1
    finally:
        for _, (path, _) in planted.items():
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    return failures


# ---------------------------------------------------------------------------
# THE VERDICT — a graded state, not a bare exit code
# ---------------------------------------------------------------------------
# `OK` and `OK` are the same two bytes whether R6 looked or not, and until
# 2026-09-13 that is what the diff-scoped and unscoped runs printed when
# `build_rows()` RAISED. OBSERVED 2026-09-12 by the author of
# BL-20260912-DOCUMENT-INDEX-GUARD-REPORTS-OK-WHILE-ITS-OWN-ROW-DRIFT-CENSUS-IS-UNAVAILABLE,
# on their own typo and by accident: the census line said
# "UNAVAILABLE ... NOT 'no rows drifted'" -- exactly right -- and the verdict
# line under it said OK anyway. A reader comparing verdicts could not tell
# that R6 had gone blind.
#
# ⚠️ THE UNGATED HALF IS ALREADY FIXED AND THIS DOES NOT RE-FIX IT. #12136
# made an uncomputable census an `R6 UNCOMPUTABLE (--all)` FINDING, so the
# `--all` step -- the one CI runs -- exits 1. MEASURED 2026-09-13 with a
# confirmed-live plant (`build_rows` raising): `--all` rc=1 FAIL, `--base` rc=0
# OK, unscoped rc=0 OK. What was left was the two modes a human runs locally,
# which is precisely where the row's author was standing.
#
# ⚠️ AND THE SCOPED ASYMMETRY IS DELIBERATE AND IS PRESERVED. #12136's own
# comment argues it: the scoped step is one check among many and can afford
# *we could not look*, while an UNGATED assertion that silently reports nothing
# reads byte-identically to a clean tree. That reasoning postdates the row and
# is not overridden here -- the exit code for the scoped modes is UNCHANGED.
# What changes is that the verdict LINE stops claiming a clean R6 reading it
# did not get.
VERDICT_FAIL = "fail"
VERDICT_OK = "ok"
VERDICT_OK_R6_BLIND = "ok_r6_blind"   # nothing failed, but R6 could not look


def verdict(n_findings: int, drift_state: str) -> tuple:
    """`(state, exit_code, line)` — PURE, so what the guard CLAIMS is arguable
    in a test rather than only against a tree in which the builder is broken.

    The row that asked for this says in terms that asserting on the MESSAGE is
    not sufficient, because the message was already correct while the verdict
    was already wrong. So the thing under test is this triple.
    """
    if n_findings:
        return (VERDICT_FAIL, 1,
                f"document-index: FAIL — {n_findings} finding(s).")
    if drift_state == DRIFT_UNCOMPUTABLE:
        return (VERDICT_OK_R6_BLIND, 0,
                "document-index: OK — but the R6 row-drift census was "
                "UNAVAILABLE, so this is NOT a clean R6 reading. A tree where "
                "build_rows() raises is one where `--write` is broken too; the "
                "ungated `--all` step fails on it.")
    return (VERDICT_OK, 0, "document-index: OK")


def self_test() -> int:
    CATS = {"instruction", "evidence", "history", "unknown"}
    STS = {"live", "superseded", "historical", "unknown"}

    def row(p, c="history", s="historical", sb="", **kw):
        d = {"path": p, "category": c, "status": s, "superseded_by": sb,
             "last_verified": "2026-09-07", "basis": "t", "note": ""}
        d.update(kw)
        return d

    cases = [
        # (label, population, rows, headers, generated, must_contain)
        ("R0 clean input produces NO findings",
         {"a.md"}, [row("a.md")], {"a.md": "historical"}, set(), None),
        ("R1 fires on an unregistered document",
         {"a.md", "b.md"}, [row("a.md")], {"a.md": "historical", "b.md": None},
         set(), "R1 UNREGISTERED"),
        ("R2 fires on a row whose file is gone",
         {"a.md"}, [row("a.md"), row("gone.md")],
         {"a.md": "historical"}, set(), "R2 GHOST ROW"),
        ("R3 fires when header and index disagree",
         {"a.md"}, [row("a.md", s="historical")], {"a.md": "live"},
         set(), "R3 DISAGREEMENT"),
        ("R3 fires when a document carries no stamp at all",
         {"a.md"}, [row("a.md")], {"a.md": None}, set(), "R3 UNSTAMPED"),
        ("R3 fires when a document could not be read",
         {"a.md"}, [row("a.md")], {}, set(), "R3 UNREADABLE"),
        ("R3 is WAIVED for a generated document",
         {"a.md"}, [row("a.md")], {"a.md": None}, {"a.md"}, None),
        ("R4 fires on superseded with no successor",
         {"a.md"}, [row("a.md", s="superseded", sb="—")],
         {"a.md": "superseded"}, set(), "R4 SUPERSEDED WITHOUT SUCCESSOR"),
        ("R4 does NOT fire when a successor is named",
         {"a.md"}, [row("a.md", s="superseded", sb="b.md")],
         {"a.md": "superseded"}, set(), None),
        ("R5 fires on a category outside the closed set",
         {"a.md"}, [row("a.md", c="vibes")], {"a.md": "historical"},
         set(), "R5 BAD CATEGORY"),
        ("R5 fires on a status outside the closed set",
         {"a.md"}, [row("a.md", s="probably_fine")], {"a.md": "probably_fine"},
         set(), "R5 BAD STATUS"),
        # `unknown` is the REQUIRED honest state, in both columns. If R5
        # rejected it, the builder would be pushed to invent a value to make
        # CI green — manufacturing exactly the false confidence this register
        # exists to remove. These two cases pin that open.
        ("R5 does NOT fire on the required `unknown` category",
         {"a.md"}, [row("a.md", c="unknown")], {"a.md": "historical"},
         set(), None),
        ("R5 does NOT fire on the required `unknown` status",
         {"a.md"}, [row("a.md", s="unknown")], {"a.md": "unknown"},
         set(), None),
    ]

    failures = 0
    for label, pop, rows, headers, gen, expect in cases:
        found = evaluate(pop, rows, headers, gen, CATS, STS)
        if expect is None:
            ok = not found
            detail = f"expected NO findings, got {found}"
        else:
            ok = any(expect in f for f in found)
            detail = f"expected a finding containing {expect!r}, got {found}"
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"        {detail}")
            failures += 1

    # ── R6 — its own block, because it needs `computed` and `scope`. ───────
    #
    # ⚠️ THE SECOND CASE IS THE LOAD-BEARING ONE. R6 is diff-scoped over 47
    # standing disagreements, so a rule that quietly fired on all of them would
    # break every PR in the repo, and one that quietly fired on none would be
    # decoration. Both directions are pinned.
    r6 = [
        ("R6 fires on a drifted row THIS DIFF touches",
         [row("a.md", s="live")], {"a.md": "unknown"}, {"a.md"}, "R6 ROW DRIFT"),
        ("R6 is SILENT on a drifted row the diff does NOT touch (census only)",
         [row("a.md", s="live")], {"a.md": "unknown"}, {"b.md"}, None),
        ("R6 is SILENT with no --base — 'we could not look', not a pass",
         [row("a.md", s="live")], {"a.md": "unknown"}, None, None),
        ("R6 is SILENT when the fresh row set could not be computed",
         [row("a.md", s="live")], None, {"a.md"}, None),
        ("R6 does NOT fire when the row and the computation agree",
         [row("a.md", s="live")], {"a.md": "live"}, {"a.md"}, None),
    ]
    for label, rws, computed, scope, expect in r6:
        found = [f for f in evaluate({"a.md"}, rws, {"a.md": rws[0]["status"]},
                                     set(), CATS, STS,
                                     computed=computed, scope=scope)
                 if f.startswith("R6")]
        ok = (any(expect in f for f in found) if expect else not found)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"        got {found}")
            failures += 1

    # ── R6 UNGATED (--all) ────────────────────────────────────────────────
    # The whole value of the ungated step is that it fires where the scoped one
    # is silent, so every case below is one the scoped rule already passes.
    r6_all = [
        ("R6 --all fires on a drifted row the diff does NOT touch",
         [row("a.md", s="live")], {"a.md": "unknown"}, {"b.md"}, "R6 ROW DRIFT"),
        ("R6 --all fires with NO --base at all (scope is None)",
         [row("a.md", s="live")], {"a.md": "unknown"}, None, "R6 ROW DRIFT"),
        ("R6 --all is SILENT when the row and the computation agree",
         [row("a.md", s="live")], {"a.md": "live"}, None, None),
        # ⚠️ THE ASYMMETRY WITH THE SCOPED RULE, ASSERTED. An ungated assertion
        # that reports nothing when its own input could not be built reads
        # byte-identically to a clean tree.
        ("R6 --all FAILS on an uncomputable row set (the scoped rule does not)",
         [row("a.md", s="live")], None, None, "R6 UNCOMPUTABLE"),
    ]
    for label, rws, computed, scope, expect in r6_all:
        found = [f for f in evaluate({"a.md"}, rws, {"a.md": rws[0]["status"]},
                                     set(), CATS, STS, computed=computed,
                                     scope=scope, enforce_all=True)
                 if f.startswith("R6")]
        ok = (any(expect in f for f in found) if expect else not found)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"        got {found}")
            failures += 1

    # And the scoped rule must NOT have changed: the same uncomputable input
    # that fails --all stays silent without it.
    scoped_unc = [f for f in evaluate({"a.md"}, [row("a.md", s="live")],
                                      {"a.md": "live"}, set(), CATS, STS,
                                      computed=None, scope={"a.md"})
                  if f.startswith("R6")]
    if scoped_unc:
        print(f"  FAIL  the SCOPED rule must stay silent on an uncomputable row "
              f"set; got {scoped_unc}")
        failures += 1
    else:
        print("  PASS  the scoped rule is unchanged — uncomputable is still "
              "'we could not look' there, and only --all treats it as a finding")

    # ── R1 names the ONE row when a stub is available ─────────────────────
    stub = "| `b.md` | evidence | unknown | — | never | `x / y` | — |"
    with_stub = [f for f in evaluate({"a.md", "b.md"}, [row("a.md")],
                                     {"a.md": "historical", "b.md": None},
                                     set(), CATS, STS, stubs={"b.md": stub})
                 if f.startswith("R1")]
    if not (with_stub and stub in with_stub[0]):
        print(f"  FAIL  R1 must quote the generator's own stub verbatim; got {with_stub}")
        failures += 1
    else:
        print("  PASS  R1 quotes the generator's own stub verbatim")
    # ⚠️ AND IT MUST STILL NAME --write, because several unregistered documents
    # at once is exactly the case the one-line remedy does NOT serve. Dropping
    # it would trade one trap for another.
    if not (with_stub and "--write" in with_stub[0]):
        print("  FAIL  R1 must still name --write for the several-documents case")
        failures += 1
    else:
        print("  PASS  R1 still names --write, with its blast radius stated")
    # No stub available -> the old remedy, never a half-written one.
    no_stub = [f for f in evaluate({"a.md", "b.md"}, [row("a.md")],
                                   {"a.md": "historical", "b.md": None},
                                   set(), CATS, STS, stubs={})
               if f.startswith("R1")]
    if not (no_stub and "--write" in no_stub[0] and "THIS ONE LINE" not in no_stub[0]):
        print(f"  FAIL  with no stub R1 must fall back to the command, cleanly; got {no_stub}")
        failures += 1
    else:
        print("  PASS  with no stub R1 falls back to the command rather than "
              "promising a line it does not have")

    # The CENSUS must see what the scoped rule deliberately does not. Without
    # this, narrowing the rule to nothing would still read as a clean guard.
    _st, census = row_status_drift([row("a.md", s="live"), row("b.md", s="live")],
                                   {"a.md": "unknown", "b.md": "live"})
    if census != [("a.md", "live", "unknown")]:
        print(f"  FAIL  the census must find a drift the scoped rule skips; got {census}")
        failures += 1
    else:
        print("  PASS  the census finds a drift the scoped rule skips")
    # ⚠️ ASSERT THE STATE, NOT THE EMPTINESS. A planted `computed = {}` in place
    # of the None guard passed this check while it only tested for `[]`, because
    # "we could not look" and "nothing drifted" were the same value. That is the
    # collapsed state this guard's own repo has a rule about, committed here.
    if row_status_drift([row("a.md", s="live")], None) != (DRIFT_UNCOMPUTABLE, []):
        print("  FAIL  an uncomputable row set must be NAMED uncomputable, not "
              "returned as an empty census")
        failures += 1
    else:
        print("  PASS  an uncomputable row set is NAMED, so it cannot read as "
              "'nothing drifted'")
    if row_status_drift([row("a.md", s="live")], {"a.md": "live"}) != (DRIFT_COMPUTED, []):
        print("  FAIL  an agreeing row set must grade `computed` with no rows")
        failures += 1
    else:
        print("  PASS  an agreeing row set grades `computed`, which is a "
              "different fact from `uncomputable`")

    # ── THE VERDICT ─────────────────────────────────────────────────────────
    # ⚠️ THE ROW THAT ASKED FOR THIS RULES OUT THE OBVIOUS TEST. The census
    # MESSAGE was already correct -- "UNAVAILABLE ... NOT 'no rows drifted'" --
    # and the verdict under it was already wrong, so asserting on the message
    # would have passed against the defect. The assertion is on the graded
    # VERDICT, which is the triple `verdict()` returns.
    clean = verdict(0, DRIFT_COMPUTED)
    blind = verdict(0, DRIFT_UNCOMPUTABLE)
    if clean == blind:
        print("  FAIL  [verdict] a run whose R6 census could not be computed "
              "returns the SAME verdict as a clean one — which is the defect, "
              "not a style point")
        failures += 1
    else:
        print("  PASS  [verdict] an uncomputable census does not return the "
              "clean verdict")
    if blind[0] != VERDICT_OK_R6_BLIND:
        print(f"  FAIL  [verdict] an uncomputable census graded {blind[0]!r}; "
              f"it must be its own state, never folded into `ok`")
        failures += 1
    else:
        print("  PASS  [verdict] *we could not look* is its own graded state")
    # The exit code for the scoped modes is DELIBERATELY unchanged (#12136's
    # reasoning, preserved). Pinned, so a later "tighten it" has to argue with
    # the comment on `verdict()` rather than silently red every local run.
    if blind[1] != 0:
        print("  FAIL  [verdict] the scoped verdict's EXIT CODE changed; the "
              "ungated --all step is what fails on this, by design")
        failures += 1
    else:
        print("  PASS  [verdict] the scoped exit code is unchanged, as designed")
    if verdict(3, DRIFT_COMPUTED)[1] != 1 or verdict(3, DRIFT_UNCOMPUTABLE)[1] != 1:
        print("  FAIL  [verdict] findings must fail whatever the census did")
        failures += 1
    else:
        print("  PASS  [verdict] findings fail regardless of the census state")
    # END-TO-END, through the real rule: an uncomputable census under --all is
    # a FINDING, so the step CI runs exits 1. This is the half #12136 shipped.
    #
    # ⚠️ IT IS NOT UNCONTROLLED TODAY AND THIS DOES NOT CLAIM TO BE THE FIRST.
    # The `r6_all` case list already asserts `row_status_drift`'s side of it;
    # discovered by planting `enforce_all and ...` away and watching BOTH that
    # control and this one go red. What this adds is the layer above: that the
    # finding actually reaches the VERDICT, which is the join the row is about
    # -- a correct finding under a verdict that ignored it was the whole
    # defect.
    _blind_findings = evaluate(set(), [], {}, set(), CATS, STS,
                               computed=None, scope=None, enforce_all=True)
    if not any("R6 UNCOMPUTABLE" in f for f in _blind_findings):
        print("  FAIL  [verdict] --all did not raise a finding on an "
              "uncomputable census, so the step CI runs would exit 0 on a "
              "broken build_rows()")
        failures += 1
    elif verdict(len(_blind_findings), DRIFT_UNCOMPUTABLE)[1] != 1:
        print("  FAIL  [verdict] --all raised the finding and the verdict "
              "still did not fail")
        failures += 1
    else:
        print("  PASS  [verdict] --all FAILS on an uncomputable census, "
              "end-to-end through evaluate()")
    # The control that keeps the four above from passing vacuously against an
    # `evaluate()` that finds something in everything.
    if evaluate(set(), [], {}, set(), CATS, STS,
                computed={}, scope=None, enforce_all=True):
        print("  FAIL  [verdict] evaluate() reports findings on an EMPTY, "
              "computable register — the controls above prove nothing")
        failures += 1
    else:
        print("  PASS  [verdict] a computable empty register is silent, so the "
              "controls above are not vacuous")

    # ── R7: THE FILE ITSELF ─────────────────────────────────────────────────
    # The measured incident is the first case: a HALF-resolved conflict, with
    # only the opener left behind. A control requiring the full three-marker
    # block would have passed against the very file that produced this row.
    real_row = ("| `docs/x.md` | instruction | live | — | 2026-09-13 | "
                "`stamp` | — |\n")
    r7 = [
        ("a half-resolved conflict — ONLY the opener, the measured case",
         "# Index\n<<<<<<< HEAD\n" + real_row, INDEX_CONFLICTED),
        ("a full conflict block", "# Index\n<<<<<<< HEAD\n" + real_row
         + "=======\n" + real_row + ">>>>>>> origin/main\n", INDEX_CONFLICTED),
        ("only the CLOSER left behind", "# Index\n>>>>>>> origin/main\n",
         INDEX_CONFLICTED),
        # THE FALSE-POSITIVE CASE THE ROW ITSELF WARNS ABOUT. Every row in this
        # document starts with `|`, so a note QUOTING the markers is not a
        # conflict. Without this control the cheapest passing implementation is
        # a substring search, which would fail the register for describing it.
        ("a table row whose NOTE quotes the markers is NOT a conflict",
         "# Index\n| `docs/y.md` | instruction | live | — | 2026-09-13 | "
         "`stamp` | mentions <<<<<<< HEAD and >>>>>>> origin/main |\n",
         INDEX_CLEAN),
        # A bare `=======` is a valid setext heading underline. Firing on it
        # would invent findings in a document nobody conflicted.
        ("a setext heading underline is NOT a conflict",
         "Some Heading\n=======\n" + real_row, INDEX_CLEAN),
        ("an ordinary clean index", "# Index\n" + real_row, INDEX_CLEAN),
        # `we could not look` is its own state and must never read as clean.
        ("an unreadable file", None, INDEX_UNREADABLE),
    ]
    for label, text, want in r7:
        got, _hits = index_integrity(text)
        if got != want:
            print(f"  FAIL  [R7] {label}: graded {got!r}, wanted {want!r}")
            failures += 1
        else:
            print(f"  PASS  [R7] {label} -> {got}")

    # AND THE MARKER LINES MUST BE NAMED, not merely counted — a guard that
    # says "there is a conflict somewhere in a 1076-row file" is not actionable.
    _st, hits = index_integrity("# Index\n<<<<<<< HEAD\n" + real_row)
    if not (hits and hits[0][0] == 2 and "<<<<<<<" in hits[0][1]):
        print("  FAIL  [R7] the marker line is not reported with its line number")
        failures += 1
    else:
        print(f"  PASS  [R7] the marker is named with its line number "
              f"(line {hits[0][0]})")

    # The rule engine is only half the guard. Grade the POPULATION BUILDER too —
    # the half that was defective while every case above passed.
    pop_failures = population_control()
    failures += pop_failures

    if failures:
        print(f"document-index self-test: FAIL — {failures} check(s) did not "
              f"behave as declared. The guard cannot be trusted.")
        return 1
    # ⚠️ `len(r7)` IS IN THIS SUM ON PURPOSE. The count is the self-test's own
    # denominator, and cases that ran but were not counted would be the
    # unasserted-denominator class inside the thing that exists to prevent it.
    #
    # ⚠️ THE TRAILING TERM IS THE HAND-WRITTEN CHECKS -- the ones not driven by
    # a `cases`-style list -- and it MUST be bumped when one is added. It went
    # 8 -> 14 on 2026-09-13 with the six `[verdict]` controls. Nothing computes
    # it, which is a real weakness of this line and is stated rather than
    # hidden: the six were counted by hand, and the original eight were taken
    # on trust rather than re-audited.
    _HANDWRITTEN = 14
    print(f"document-index self-test: OK — "
          f"{len(cases) + len(r6) + len(r6_all) + len(r7) + _HANDWRITTEN} planted rule cases, "
          f"every rule observed FIRING and every clean case observed SILENT; "
          f"plus 3 planted FILES proving the population builder sees a "
          f"top-level `docs/*.md`, a nested one, and no non-markdown file.")
    return 0


def _diff_scope(base: Optional[str]) -> Optional[Set[str]]:
    """Repo-relative paths this diff touches, or None — *we could not look*.

    None and an EMPTY set are different facts and are never collapsed: the
    first is "no --base was given, so scoping is impossible", the second is
    "this diff touches no registered document". R6 fires on neither, but only
    one of them is a clean reading, and `main` prints which it had.
    """
    if not base:
        return None
    try:
        r = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                           cwd=REPO, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--all", dest="all_", action="store_true",
                    help="UNGATED R6: report every drifted row, not only the "
                         "ones this diff touches. Shippable because the "
                         "residue is 0 (measured 2026-09-13; it was 46 at "
                         "b209768c2). An uncomputable row set FAILS this step "
                         "rather than printing nothing.")
    ap.add_argument("--base", default=None,
                    help="diff base for R6 scoping (e.g. origin/main). Without "
                         "it R6 produces no findings and only the census is "
                         "printed — that is 'we could not look', not a pass.")
    a = ap.parse_args()
    # The verdict below is about the COMMITTED tree. Say so when that is
    # not the tree you edited — BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS.
    import pathlib  # noqa: PLC0415 — local, so importing this module stays free
    import sys as _sys  # noqa: PLC0415
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()


    if a.self_test:
        return self_test()

    b = _builder()
    index = REPO / INDEX_REL
    if not index.exists():
        print(f"document-index: FAIL — {INDEX_REL} does not exist. "
              f"Run: python3 scripts/ops/document_index.py --write")
        return 1

    # R7 FIRST, and it RETURNS rather than adding a finding. Grading the rows
    # of a file carrying an unresolved conflict would emit confident verdicts
    # about a corrupt register — both copies of a conflicted row parse as real
    # registrations, so every other rule would be satisfied and say so.
    try:
        index_text: Optional[str] = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        index_text = None
        print(f"document-index: FAIL — {INDEX_REL} could not be READ "
              f"({type(exc).__name__}: {exc}). That is 'we could not look', "
              "never 'the register is fine'.")
    state, hits = index_integrity(index_text)
    if state is not INDEX_CLEAN:
        if state == INDEX_CONFLICTED:
            print(f"::error::{INDEX_REL} carries an UNRESOLVED MERGE CONFLICT. "
                  "Every rule below reads ROWS, and both copies of a conflicted "
                  "row parse as ordinary registrations — so a corrupted register "
                  "would otherwise read OK. Resolve it, then re-run.")
            for n, line in hits[:20]:
                print(f"    {INDEX_REL}:{n}: {line}")
            if len(hits) > 20:
                print(f"    ... and {len(hits) - 20} more marker line(s)")
        return 1

    population = set(b.population())
    rows = parse_rows(index_text)
    generated = set(b.verify_generated())
    headers = read_headers(population & {r["path"] for r in rows}, b.STAMP_RE)

    # `unknown` is a REQUIRED state for category as well as status — it is the
    # absence of a category, not a category, and the brief is explicit that it
    # must be markable rather than guessed. Rejecting it here would push the
    # builder to invent a category to satisfy the guard, which is precisely the
    # "an invented status reads as checked" failure this whole register exists
    # to stop. It is admitted deliberately, not overlooked.
    # A fresh computation of every row's status — what `--write` WOULD record.
    # Cheap (measured 0.12s over 1063 documents), and it is the only thing that
    # can see a row and its own header drift together.
    try:
        computed = {r["path"]: r["status"]
                    for r in b.build_rows(datetime.date.today().isoformat())}
    except Exception as exc:  # noqa: BLE001
        computed = None
        print(f"document-index: WARNING — could not compute a fresh row set "
              f"({type(exc).__name__}: {exc}); R6 is reporting NOTHING, which "
              f"is 'we could not look' and not a clean reading.")
    scope = _diff_scope(a.base)

    # The generator's OWN stub for each unregistered path, so R1 can name the
    # one line to add instead of a command that rewrites the tree. Built only
    # for the unregistered set (never for all 1073 rows) and best-effort: a
    # builder that cannot produce one falls back to the old remedy text rather
    # than failing the guard over its own help string.
    stubs: Dict[str, str] = {}
    for pth in sorted(population - {r["path"] for r in rows}):
        try:
            r = b.assess(pth, b._canonical_active_docs(), b._mi159_states(),
                         datetime.date.today().isoformat())
            stubs[pth] = (f"| `{pth}` | {r['category']} | {r['status']} | "
                          f"{r['superseded_by'] or '—'} | {r['last_verified']} | "
                          f"`{r['basis']}` | {r['note'] or '—'} |")
        except Exception:  # noqa: BLE001
            pass

    findings = evaluate(population, rows, headers, generated,
                        set(b.CATEGORIES) | {"unknown"}, set(b.STATUSES),
                        computed=computed, scope=scope,
                        enforce_all=a.all_, stubs=stubs)

    # ALWAYS STATE THE POPULATION — a guard reporting "no findings" without a
    # denominator is the clean-negative this repo has a rule about.
    print(f"document-index: population={len(population)} registered={len(rows)} "
          f"headers_read={len(headers)} generated_waived={len(generated)}")

    # THE CENSUS. Printed on every run, including a clean one, so the narrow
    # (diff-scoped) enforcement can never hide the standing number — the
    # `session-registry-guard` discipline. A guard that enforced on 1 row and
    # said nothing about the other 46 would read as "the register is coherent".
    drift_state, drift = row_status_drift(rows, computed)
    if drift_state == DRIFT_UNCOMPUTABLE:
        print("document-index: row-drift census UNAVAILABLE — the fresh row set "
              "could not be computed. NOT 'no rows drifted'.")
    else:
        scope_note = ("UNGATED (--all): every drifted row is a finding"
                      if a.all_ else
                      "unscoped (no --base): R6 reports nothing"
                      if scope is None else f"diff touches {len(scope)} path(s)")
        print(f"document-index: row-drift census = {len(drift)} committed row(s) "
              f"whose status a fresh build_rows() does not reproduce; {scope_note}.")
        for pth, committed_st, fresh_st in drift[:5]:
            print(f"    census(not a finding): {pth} row='{committed_st}' "
                  f"computed='{fresh_st}'")
        if len(drift) > 5:
            print(f"    ... and {len(drift) - 5} more (census only)")

    if findings:
        for f in findings[:60]:
            print(f"  {f}")
        if len(findings) > 60:
            print(f"  ... and {len(findings) - 60} more")

    _state, rc, line = verdict(len(findings), drift_state)
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
