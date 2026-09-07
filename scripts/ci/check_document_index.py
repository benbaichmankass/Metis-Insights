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

The rule engine is a PURE FUNCTION over (population, rows, headers, generated),
so the policy is arguable in tests rather than against the live tree.

Run:
    python3 scripts/ci/check_document_index.py
    python3 scripts/ci/check_document_index.py --self-test
"""
from __future__ import annotations

import argparse
import importlib.util
import re
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
# THE RULE ENGINE — pure
# ---------------------------------------------------------------------------
def evaluate(population: Set[str],
             rows: List[Dict[str, str]],
             headers: Dict[str, Optional[str]],
             generated: Set[str],
             categories: Set[str],
             statuses: Set[str]) -> List[str]:
    """Return a list of findings. Empty list = the register is true.

    `headers[path]` is the status token read from that file's own stamp, or
    None when the file carries no stamp. A path absent from `headers` was not
    readable and is reported rather than assumed clean — "we could not look" is
    never folded into "we looked and it agreed".
    """
    findings: List[str] = []
    registered = {r["path"] for r in rows}

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

    if failures:
        print(f"document-index self-test: FAIL — {failures} rule(s) did not "
              f"behave as declared. The guard cannot be trusted.")
        return 1
    print(f"document-index self-test: OK — {len(cases)} planted cases, "
          f"every rule observed FIRING and every clean case observed SILENT.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
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
    findings = evaluate(population, rows, headers, generated,
                        set(b.CATEGORIES) | {"unknown"}, set(b.STATUSES))

    # ALWAYS STATE THE POPULATION — a guard reporting "no findings" without a
    # denominator is the clean-negative this repo has a rule about.
    print(f"document-index: population={len(population)} registered={len(rows)} "
          f"headers_read={len(headers)} generated_waived={len(generated)}")

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
