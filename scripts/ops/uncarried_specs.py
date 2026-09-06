#!/usr/bin/env python3
"""Count the finished specifications that nothing points at.

WHY
---
On 2026-08-23 `docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md` was written. It
carried the operator's directive verbatim, stated its populations, and ended *"Paste this
whole file as the opening message of a NEW session."* **Nobody did, for 14 days** — while
the same thesis was re-derived at least seven times across `docs/research/` and built zero
times (`BL-20260906-A-RESEARCH-ARTIFACT-THAT-SPECIFIES-WORK-IS-NOT-REGISTERED-AS-WORK`).

`docs/research/` and `docs/design/` are WRITE surfaces with no READ path into planning.
Nothing converts a memo that SPECIFIES WORK into a work object, so a perfect, ready-to-paste
spec is exactly as invisible as a bad one. This probe measures how large that pile is.

It is the artifact-side sibling of `scripts/ops/check_research_index.py`, which enforces the
same property one layer down (every `scripts/research/*.py` is routed or exempted-with-a-
reason). That script covers CODE; this one covers the PROSE that specifies work.

WHAT IT DOES NOT DO
-------------------
It cannot tell whether an un-carried spec's work was ALREADY DONE by some path that never
named the file. A design whose thing shipped is not stranded, and this probe would still
report it un-carried. That is a real over-count and is reported rather than hidden — the
`--triage` output exists so a human can settle those individually.

THE THREE CARRIER STATES ARE NEVER COLLAPSED
--------------------------------------------
  planned    — named by a WORK register (a work object/intent/step, an OPEN-ITEMS row, the
               manager checklist, the due list). Something is meant to act on it.
  mentioned  — named ONLY on a non-carrying surface (a backlog row's prose, ROADMAP, another
               research artifact, a skill). A reference is not a carrier: the motivating
               incident had exactly one such mention and still sat 14 days.
  uncarried  — named nowhere outside itself.
  unreadable — we could not look. NEVER folded into `uncarried`; absence of evidence from a
               failed read is not evidence of absence.

Stdlib-only. Read-only: it writes nothing and mutates no register.

Usage:
  python3 scripts/ops/uncarried_specs.py                 # headline counts + population
  python3 scripts/ops/uncarried_specs.py --self-test     # POSITIVE CONTROL (exit 1 on fail)
  python3 scripts/ops/uncarried_specs.py --triage        # the un-carried list, newest first
  python3 scripts/ops/uncarried_specs.py --json          # machine-readable full result
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ---------------------------------------------------------------- population
ARTIFACT_DIRS = ("docs/research", "docs/design")

# Files that are not specifications of work by construction — an index, a README.
# A reason is mandatory; an unexplained exclusion is how a silence list forms.
POPULATION_EXCLUDE: dict[str, str] = {
    "RESEARCH-CAPABILITY-INDEX.md": "a routing index over scripts/, not a spec of work",
    "README.md": "directory orientation, not a spec of work",
}

# ---------------------------------------------------------------- classifier
# TIER A — the filename itself declares the artifact a specification. Over-inclusive on
# purpose: a DESIGN whose build shipped is a false positive a human can settle, whereas a
# silent exclusion reproduces the very failure being measured.
FILENAME_SIGNALS = (
    "SESSION-PROMPT", "PROPOSAL", "-DESIGN", "DESIGN-", "WORKPLAN",
    "-PLAN", "PLAN-", "-SCOPE", "PROGRAM", "-BRIEF", "PACKET",
    "FEASIBILITY", "-PROCESS", "METHODOLOGY",
)

# TIER B — the artifact's own TEXT directs a future session. These are the phrases the
# backlog row named: "paste this as", "next session should", "not yet built".
TEXT_DIRECTIVE = re.compile(
    r"(paste\s+th(is|e)\s+(whole\s+)?(file|prompt|document)"
    r"|as\s+the\s+opening\s+message"
    r"|next\s+session\s+(should|must|will|picks)"
    r"|a\s+(new|fresh)\s+session\s+should"
    r"|(is|are|remains?|stays?)\s+(still\s+)?(not\s+yet\s+built|unbuilt|not\s+built|unshipped|not\s+shipped|unimplemented|not\s+implemented)"
    r"|has\s+never\s+been\s+(built|started|run|implemented|shipped)"
    r"|not\s+yet\s+(built|implemented|shipped|wired|started)"
    r"|proposed\s+(but|and)\s+(not|never)\s+(built|implemented|shipped|applied)"
    r")",
    re.IGNORECASE,
)

# TIER C — a weaker forward-looking signal. Reported SEPARATELY as borderline and NOT
# counted in the headline, because "recommendation" appears in evidence reports whose job
# was only ever to report.
TEXT_BORDERLINE = re.compile(
    r"(recommend(ation|ed|s)?|next\s+steps?|follow-?ups?|proposed\s+change|to\s+be\s+decided)",
    re.IGNORECASE,
)

# ------------------------------------------------------------------ carriers
# Registers whose PURPOSE is to carry work. A name here means something is meant to act.
WORK_REGISTERS = (
    "docs/claude/work/objects",
    "docs/claude/work/intents",
    "docs/claude/work/steps",
    "docs/claude/OPEN-ITEMS.json",
    "docs/claude/work/MANAGER-CHECKLIST.json",
    "docs/claude/DUE.json",
    "docs/claude/work/WORK-DIGEST.json",
)

# Surfaces where a name is a REFERENCE, not a commitment to act.
MENTION_SURFACES = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
    "ROADMAP.md",
    "ROADMAP_MACRO.md",
    "CLAUDE.md",
    "docs/sprint-logs",
    ".claude/skills",
    "docs/claude",          # everything else under docs/claude not already a work register
    "docs/research",        # an artifact citing another artifact
    "docs/design",
)


def _iter_files(root: pathlib.Path, rel: str):
    p = root / rel
    if p.is_file():
        yield p
        return
    if not p.is_dir():
        return
    for f in sorted(p.rglob("*")):
        if f.is_file():
            yield f


def population(repo: pathlib.Path = REPO) -> list[pathlib.Path]:
    """Every artifact under the scanned dirs, minus the reasoned exclusions."""
    out = []
    for d in ARTIFACT_DIRS:
        for f in _iter_files(repo, d):
            if f.name in POPULATION_EXCLUDE:
                continue
            out.append(f)
    return out


def classify(path: pathlib.Path) -> tuple[str, list[str]]:
    """-> (tier, reasons). tier in {A, B, C, none, unreadable}."""
    name = path.name
    hits = [s for s in FILENAME_SIGNALS if s in name.upper()]
    if hits:
        return "A", [f"filename:{h}" for h in hits]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - defensive
        return "unreadable", [f"read_failed:{exc.__class__.__name__}"]
    m = TEXT_DIRECTIVE.search(text)
    if m:
        return "B", [f"text:{m.group(0)[:60].strip()!r}"]
    if TEXT_BORDERLINE.search(text):
        return "C", ["text:weak_forward_looking"]
    return "none", []


def _needles(path: pathlib.Path) -> list[str]:
    """Names a register could plausibly use. Basename matching is DELIBERATELY permissive:
    it over-counts carriers, which UNDER-counts the un-carried pile — the conservative
    direction for a claim that N artifacts are stranded."""
    stem = path.stem
    return [path.name, stem]


def carriers(repo: pathlib.Path, path: pathlib.Path, corpus: dict[str, str]) -> dict:
    """Which surfaces name this artifact, excluding the artifact itself."""
    self_rel = str(path.relative_to(repo))
    needles = _needles(path)
    planned, mentioned = [], []
    for rel, text in corpus.items():
        if rel == self_rel:
            continue
        if not any(n in text for n in needles):
            continue
        if any(rel == w or rel.startswith(w.rstrip("/") + "/") for w in WORK_REGISTERS):
            planned.append(rel)
        else:
            mentioned.append(rel)
    return {"planned": sorted(planned), "mentioned": sorted(mentioned)}


def build_corpus(repo: pathlib.Path) -> dict[str, str]:
    """Read every surface once. Returns {repo-relative path: text}."""
    corpus: dict[str, str] = {}
    seen: set[pathlib.Path] = set()
    for rel in list(WORK_REGISTERS) + list(MENTION_SURFACES):
        for f in _iter_files(repo, rel):
            if f in seen:
                continue
            seen.add(f)
            if f.suffix.lower() not in (".json", ".yaml", ".yml", ".md", ".txt"):
                continue
            try:
                corpus[str(f.relative_to(repo))] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
    return corpus


def state_for(c: dict) -> str:
    if c["planned"]:
        return "planned"
    if c["mentioned"]:
        return "mentioned"
    return "uncarried"


def analyse(repo: pathlib.Path = REPO) -> dict:
    corpus = build_corpus(repo)
    rows = []
    for p in population(repo):
        tier, reasons = classify(p)
        if tier == "unreadable":
            rows.append({"path": str(p.relative_to(repo)), "tier": tier,
                         "reasons": reasons, "state": "unreadable",
                         "planned_by": [], "mentioned_by": []})
            continue
        c = carriers(repo, p, corpus)
        rows.append({
            "path": str(p.relative_to(repo)),
            "tier": tier,
            "reasons": reasons,
            "state": state_for(c),
            "planned_by": c["planned"],
            "mentioned_by": c["mentioned"],
        })
    return {"population_scanned": len(rows),
            "corpus_surfaces_read": len(corpus),
            "excluded_from_population": POPULATION_EXCLUDE,
            "rows": rows}


# ------------------------------------------------------------- positive control
# A known POSITIVE (a spec with real carriers) and a known NEGATIVE (the motivating
# incident). The probe's silence on an un-carried artifact means nothing until it is shown
# to find the carried one. Both calibration points are named in the work object.
CONTROL_POSITIVE = "docs/design/operating-layer-build-plan-DESIGN.md"
CONTROL_NEGATIVE = "docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md"


def self_test(repo: pathlib.Path = REPO) -> tuple[bool, list[str]]:
    res = analyse(repo)
    by_path = {r["path"]: r for r in res["rows"]}
    problems = []
    pos = by_path.get(CONTROL_POSITIVE)
    if pos is None:
        problems.append(f"POSITIVE CONTROL MISSING from population: {CONTROL_POSITIVE}")
    else:
        if pos["tier"] != "A":
            problems.append(f"positive control classified {pos['tier']}, expected A")
        if pos["state"] != "planned":
            problems.append(
                f"POSITIVE CONTROL FAILED: {CONTROL_POSITIVE} graded {pos['state']!r}, "
                f"expected 'planned'. The probe cannot find a carried spec, so its silence "
                f"on an un-carried one proves nothing.")
        elif len(pos["planned_by"]) < 6:
            problems.append(
                f"positive control found only {len(pos['planned_by'])} work-register "
                f"carriers; the work object states SIX exist")
    neg = by_path.get(CONTROL_NEGATIVE)
    if neg is None:
        problems.append(f"NEGATIVE CONTROL MISSING from population: {CONTROL_NEGATIVE}")
    elif neg["tier"] not in ("A", "B"):
        problems.append(f"negative control classified {neg['tier']}, expected A or B")
    return (not problems), problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="run the positive/negative control; exit 1 if the probe is not calibrated")
    ap.add_argument("--triage", action="store_true", help="list the un-carried specs")
    ap.add_argument("--json", action="store_true", help="full machine-readable result")
    args = ap.parse_args(argv)

    if args.self_test:
        ok, problems = self_test()
        if ok:
            print("uncarried-specs self-test: OK "
                  f"(positive control {CONTROL_POSITIVE} reads 'planned'; "
                  f"negative control {CONTROL_NEGATIVE} classifies as a spec)")
            return 0
        for p in problems:
            print(f"uncarried-specs self-test: FAIL — {p}")
        return 1

    res = analyse()
    rows = res["rows"]
    if args.json:
        print(json.dumps(res, indent=2))
        return 0

    specs = [r for r in rows if r["tier"] in ("A", "B")]
    border = [r for r in rows if r["tier"] == "C"]
    unread = [r for r in rows if r["tier"] == "unreadable"]
    unc = [r for r in specs if r["state"] == "uncarried"]
    men = [r for r in specs if r["state"] == "mentioned"]
    pla = [r for r in specs if r["state"] == "planned"]

    print(f"POPULATION: {res['population_scanned']} artifacts under {', '.join(ARTIFACT_DIRS)} "
          f"(excluded {len(POPULATION_EXCLUDE)} by reasoned rule); "
          f"{res['corpus_surfaces_read']} register/doc surfaces read for the join.")
    print(f"CLASSIFIED AS SPECS (tier A filename + tier B self-declared): {len(specs)}"
          f"  [A={sum(1 for r in specs if r['tier']=='A')}, "
          f"B={sum(1 for r in specs if r['tier']=='B')}]")
    print(f"BORDERLINE (tier C, reported separately, NOT in the headline): {len(border)}")
    if unread:
        print(f"UNREADABLE (we could not look — never counted as un-carried): {len(unread)}")
    print()
    print(f"  planned   (named by a work register): {len(pla)}")
    print(f"  mentioned (named only on a non-carrying surface): {len(men)}")
    print(f"  UNCARRIED (named nowhere outside itself): {len(unc)}")

    if args.triage:
        print("\n--- UNCARRIED specs, newest filename first ---")
        for r in sorted(unc, key=lambda r: r["path"], reverse=True):
            print(f"  {r['path']}  [{r['tier']}] {' '.join(r['reasons'])}")
        print("\n--- MENTIONED-ONLY specs (a reference is not a carrier) ---")
        for r in sorted(men, key=lambda r: r["path"], reverse=True):
            print(f"  {r['path']}  [{r['tier']}] mentioned_by={r['mentioned_by'][:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
