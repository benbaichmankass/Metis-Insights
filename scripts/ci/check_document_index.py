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
from typing import Dict, List, Optional, Set

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
             scope: Optional[Set[str]] = None) -> List[str]:
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
    drift_state, drifted = row_status_drift(rows, computed)
    if scope is not None and drift_state == DRIFT_COMPUTED:
        in_scope = {p for p, _c, _f in drifted} & scope
        for p, committed, fresh in drifted:
            if p not in in_scope:
                continue
            findings.append(
                f"R6 ROW DRIFT: {INDEX_REL} records status '{committed}' for {p}, "
                f"but a fresh build_rows() computes '{fresh}'. This diff touches "
                f"that document, so it is yours to reconcile. R3 cannot see this: "
                f"the header and the row were written by the same run and agree "
                f"with each other. Run: python3 scripts/ops/document_index.py "
                f"--write, then READ what it changed before committing.")

    # R1 — a document exists and is not registered.
    for p in sorted(population - registered):
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

    # The rule engine is only half the guard. Grade the POPULATION BUILDER too —
    # the half that was defective while every case above passed.
    pop_failures = population_control()
    failures += pop_failures

    if failures:
        print(f"document-index self-test: FAIL — {failures} check(s) did not "
              f"behave as declared. The guard cannot be trusted.")
        return 1
    print(f"document-index self-test: OK — {len(cases) + len(r6) + 3} planted rule cases, "
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
    ap.add_argument("--base", default=None,
                    help="diff base for R6 scoping (e.g. origin/main). Without "
                         "it R6 produces no findings and only the census is "
                         "printed — that is 'we could not look', not a pass.")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    b = _builder()
    index = REPO / INDEX_REL
    if not index.exists():
        print(f"document-index: FAIL — {INDEX_REL} does not exist. "
              f"Run: python3 scripts/ops/document_index.py --write")
        return 1

    population = set(b.population())
    rows = parse_rows(index.read_text(encoding="utf-8"))
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

    findings = evaluate(population, rows, headers, generated,
                        set(b.CATEGORIES) | {"unknown"}, set(b.STATUSES),
                        computed=computed, scope=scope)

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
        scope_note = ("unscoped (no --base): R6 reports nothing"
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
        print(f"document-index: FAIL — {len(findings)} finding(s).")
        return 1

    print("document-index: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
