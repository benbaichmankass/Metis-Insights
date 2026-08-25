#!/usr/bin/env python3
"""CI guard: the workflow catalog must be complete, and must not invent files.

WHY (the defect class this guard exists to prevent recurring)
-------------------------------------------------------------
``docs/github-actions-workflows.md`` § "Complete workflow index" states its own
invariant: *"this index lists the remaining workflow files so every
``.github/workflows/*.yml`` is named in this doc"* (BL-20260602-003).

Measured 2026-08-21, that claim was false for **51 of 111 workflow files
(45.9%)** — including ``guards.yml``, which runs every static guard in the repo
and is one of only three REQUIRED status checks. The absentees also included
``claude-run-failure-alert.yml``, ``board-post.yml``, ``pr-close.yml``,
``oci-inventory.yml``, ``merge-claim-audit.yml`` and the whole m28/m31/m32/m33/
m34 grade family.

**An incomplete index is worse than an undocumented set, because this one
claims to be exhaustive.** A session asking "does a workflow for X already
exist?" searches the doc, finds nothing, and concludes none exists — so it
builds a second one, or reports the capability missing. That is the same shape
as `docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence": an artifact
reporting success that is true relative to its own scope, while the scope is
wrong. A negative read off this doc had no denominator behind it.

The cause was structural, not neglect. Every sibling inventory in this repo
that STAYS correct has a CI check behind it — ``api-tier-policy`` (whose own
doc was 60% incomplete for the identical reason), ``canonical-doc-coherence``,
``provenance-consumer-guard``, ``new-table-wiring-guard``. This one never did,
so every workflow added since 2026-06-02 could land unnamed and **none of them
announced itself**. The invariant has now been asserted-and-false at least
once, which is the argument for a detector over another manual sweep.

WHAT IT CHECKS — two directions, because the gap ran both ways
--------------------------------------------------------------
**A. COMPLETENESS.** Every ``.github/workflows/*.yml`` / ``*.yaml`` must be
named somewhere in the doc.

**B. NO PHANTOMS.** Every backticked ``*.yml`` / ``*.yaml`` token in the doc
must resolve to a file that actually exists in the repo.

Direction B is not hypothetical either: the same measurement found **12 names
carrying a ``.yml`` suffix that are not workflow files at all** —
``env-gate-guard.yml``, ``canonical-db-resolver.yml``, ``secret-scan.yml``,
``ruff-lint.yml``, ``account-class-guard.yml`` and 7 more. Each is a guard
**id** in ``scripts/ci/run_guards.py``'s registry, consolidated into the single
``guards.yml`` job by BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES. So the
doc named a file that does not exist *and* omitted the workflow that actually
runs it. A reader looking for the file that runs ``env-gate-guard`` found a
plausible name, no file on disk, and no pointer to the real one.

Both directions must be enforced together. A guard that only checked
completeness would be satisfied by adding a row for a workflow that does not
exist — which is how an index drifts into fiction while passing its own test.

WHY BASENAME-RESOLUTION RATHER THAN AN EXCLUSION LIST
------------------------------------------------------
Direction B cannot simply flag every backticked ``*.yaml`` token: the doc
legitimately mentions ``accounts.yaml``, which is a real file at
``config/accounts.yaml``. The obvious fix — a curated list of "things that are
not workflows" — is exactly the kind of hand-maintained set that goes stale and
recreates the problem one level down.

Instead a token is a phantom only if **no tracked file anywhere in the repo has
that basename**. That is self-maintaining: ``accounts.yaml`` resolves and is
never flagged; a retired guard workflow resolves to nothing and is. Adding a
real file makes its mentions legal automatically, with no edit here.

GRANDFATHERING
--------------
``_EXEMPT_WORKFLOWS`` and ``_EXEMPT_TOKENS`` carry deliberate exemptions, so
shipping this guard could never block an open PR on a gap that predated it.
**Both are empty**, because the PR that added this guard backfilled all 51
missing rows and corrected all 12 phantom names. An entry is now an explicit,
reviewable decision rather than a backlog — the whole failure mode here is an
absence nobody had to justify.

Exit 0 = clean. Exit 1 = at least one unnamed workflow or phantom reference.

Usage:
    python3 scripts/ci/check_workflow_catalog.py           # the standing audit
    python3 scripts/ci/check_workflow_catalog.py --all     # identical (explicit)
    python3 scripts/ci/check_workflow_catalog.py --list    # measured coverage
    python3 scripts/ci/check_workflow_catalog.py --self-test
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence, Set

REPO = Path(__file__).resolve().parents[2]

_WORKFLOW_DIR = ".github/workflows"
_CATALOG_DOC = "docs/github-actions-workflows.md"

# Deliberate exemptions. EMPTY BY DESIGN — see the module docstring. An entry
# needs a comment saying why.
_EXEMPT_WORKFLOWS: Set[str] = set()
_EXEMPT_TOKENS: Set[str] = set()

# Direction A requires a DELIBERATE CATALOG ENTRY, not a mention.
#
# ⚠️ IT USED TO MATCH A BARE FILENAME ANYWHERE IN THE DOC, and that tolerance
# was paid for on the worst possible file
# (BL-20260825-WORKFLOW-CATALOG-COUNTS-AN-INCIDENTAL-MENTION-AS-A-CATALOG-ROW).
# `get-diag-token.yml` — the repo's only deliberate secret-emitter — had NO row
# in the index table for months while this guard printed "118/118 named,
# 100.0%". Its single mention was an artifact-RETENTION line ("`get-diag-token.yml`
# at 1"), which the old regex counted. That is CLAUDE.md's diagnostic-provenance
# sub-class C: a clean denominator over a population never checked for the
# property the label implies. The looseness was DECLARED in a comment here, which
# is why this is a cost that was accepted rather than a bug that was hidden — but
# the cost came due, so the tolerance is withdrawn.
#
# Same lesson as new-table-wiring-guard's presence-only `# data-wiring:` marker,
# which CLAUDE.md records: a guard cheaper to satisfy INCIDENTALLY than
# DELIBERATELY drifts toward being satisfied incidentally.
#
# TWO shapes count, because both are deliberate acts of cataloguing:
#   1. a backticked filename in the FIRST CELL of an index-table row, and
#   2. a backticked filename as its own `####`-style SECTION HEADING — which is
#      MORE documentation than a table row, not less. Two workflows
#      (`sync-vm-secrets.yml`, `init-actions-secrets.yml`) are catalogued that
#      way and requiring a table row would have flagged the best-documented
#      files in the doc.
# A filename in running prose counts as neither.
_TABLE_ROW_YML = re.compile(r"^\|\s*`([A-Za-z0-9._-]+\.ya?ml)`", re.M)
_SECTION_YML = re.compile(r"^#{2,6}\s+`([A-Za-z0-9._-]+\.ya?ml)`", re.M)

# Direction B is deliberately NARROWER: only *backticked* tokens are read as a
# claim that a file exists. Un-backticked prose is not a file reference, and
# treating it as one would flag ordinary sentences.
_BACKTICKED_YML = re.compile(r"`([A-Za-z0-9._-]+\.ya?ml)`")


def workflow_files(root: Path | None = None) -> List[str]:
    """Every workflow file on disk, sorted. Basenames, not paths."""
    wf_dir = (root or REPO) / _WORKFLOW_DIR
    if not wf_dir.is_dir():
        return []
    return sorted(
        p.name for p in wf_dir.iterdir()
        if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def tracked_basenames(root: Path | None = None) -> Set[str]:
    """Basenames of every tracked file in the repo.

    ``git ls-files`` is the source of truth (it is what "in the repo" means for
    a reviewer). The filesystem walk is a fallback for a non-git checkout, and
    is NOT silently equivalent: it can see untracked litter. It exists so the
    guard degrades to running rather than crashing, never to make a failure
    look like a pass.
    """
    root = root or REPO
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout.split("\n")
        names = {os.path.basename(p) for p in out if p.strip()}
        if names:
            return names
    except (subprocess.SubprocessError, OSError):
        pass
    names = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        names.update(filenames)
    return names


def documented_names(doc_text: str) -> Set[str]:
    """Workflows the doc CATALOGUES — a table row or its own section heading.

    Deliberately NOT "every filename that appears in the text"; see the
    _TABLE_ROW_YML comment for the incident that narrowed it.
    """
    return set(_TABLE_ROW_YML.findall(doc_text)) | set(_SECTION_YML.findall(doc_text))


def referenced_tokens(doc_text: str) -> Set[str]:
    return set(_BACKTICKED_YML.findall(doc_text))


def undocumented_workflows(files: Sequence[str], documented: Set[str]) -> List[str]:
    return [f for f in files if f not in documented and f not in _EXEMPT_WORKFLOWS]


def phantom_references(tokens: Set[str], tracked: Set[str]) -> List[str]:
    return sorted(t for t in tokens if t not in tracked and t not in _EXEMPT_TOKENS)


_EXPLAINER = """
HOW TO FIX
  A. An unnamed workflow -> add a row to the "Complete workflow index" table in
     docs/github-actions-workflows.md naming the file, its category, autonomy
     level, trigger and purpose. The doc claims to name every workflow; a new
     one that is not named makes that claim false for every reader after you.

  B. A phantom reference -> the doc names `X.yml` and no such file exists.
     Usually the workflow was retired or consolidated (the static guards now
     all run inside `guards.yml` as ids in scripts/ci/run_guards.py). Point the
     row at the file that really runs it, and drop the `.yml` from the id.

  Genuinely-exempt cases go in _EXEMPT_WORKFLOWS / _EXEMPT_TOKENS in this
  script WITH A REASON. Both are empty today, deliberately.
""".rstrip()


def _print_coverage(files: Sequence[str], documented: Set[str],
                    tokens: Set[str], tracked: Set[str]) -> None:
    missing = undocumented_workflows(files, documented)
    phantoms = phantom_references(tokens, tracked)
    total = len(files)
    named = total - len(missing)
    pct = (named / total * 100) if total else 100.0
    print(f"workflow-catalog coverage: {named}/{total} catalogued ({pct:.1f}%)")
    print(f"  uncatalogued workflows : {len(missing)}")
    print(f"  phantom refs      : {len(phantoms)}")
    for f in missing:
        print(f"    uncatalogued  {f}")
    for t in phantoms:
        print(f"    phantom  {t}")


def _self_test() -> int:
    """Feed the checker a known-bad catalog and require it to fail.

    A guard whose failure path is never exercised is indistinguishable from one
    that always passes (`docs/CLAUDE-RULES-CANONICAL.md` § "Green is not
    evidence"). Both directions are exercised, because they fail independently
    and a regression in one would otherwise hide behind the other.
    """
    files = ["real-one.yml", "unnamed-one.yml"]
    tracked = {"real-one.yml", "accounts.yaml"}

    # Direction A: a workflow the doc does not CATALOGUE. Note the fixture is a
    # table row, not prose — since 2026-08-25 prose does not count (see below).
    doc_a = "| `real-one.yml` | CI guard | AUTO | PR | Does a thing. |\n"
    missing = undocumented_workflows(files, documented_names(doc_a))
    if missing != ["unnamed-one.yml"]:
        print("::error::SELF-TEST FAILED — the completeness direction did not "
              f"flag an unnamed workflow (got {missing!r}, expected "
              "['unnamed-one.yml']). Its failure path is broken, so a green "
              "from it means nothing.", file=sys.stderr)
        return 1

    # Direction B: a doc naming a file that does not exist.
    doc_b = "Guards: `retired-guard.yml` and config `accounts.yaml`.\n"
    phantoms = phantom_references(referenced_tokens(doc_b), tracked)
    if phantoms != ["retired-guard.yml"]:
        print("::error::SELF-TEST FAILED — the phantom direction did not flag "
              f"a name with no file (got {phantoms!r}, expected "
              "['retired-guard.yml']).", file=sys.stderr)
        return 1

    # The negative control that matters: a real non-workflow file must NEVER be
    # flagged. Without this, the obvious over-broad implementation of direction
    # B (flag every backticked *.yaml) passes both checks above.
    if "accounts.yaml" in phantoms:
        print("::error::SELF-TEST FAILED — `accounts.yaml` resolves to a real "
              "tracked file and must not be reported as a phantom.",
              file=sys.stderr)
        return 1

    # ⚠️ THE CASE THIS GUARD EXISTS FOR AS OF 2026-08-25
    # (BL-20260825-WORKFLOW-CATALOG-COUNTS-AN-INCIDENTAL-MENTION-AS-A-CATALOG-ROW).
    # A filename mentioned only in RUNNING PROSE is not a catalog entry. The real
    # instance was an artifact-retention line — "`get-diag-token.yml` at 1" —
    # which the old anywhere-in-the-doc regex counted, so the repo's only
    # deliberate secret-emitter sat uncatalogued while this guard printed
    # "118/118 named (100.0%)". Without this case the narrowing silently reverts.
    doc_prose = ("Retention: `real-one.yml` at 3; `unnamed-one.yml` at 1. "
                 "Run logs persist 90 days.\n")
    prose_missing = undocumented_workflows(files, documented_names(doc_prose))
    if sorted(prose_missing) != ["real-one.yml", "unnamed-one.yml"]:
        print("::error::SELF-TEST FAILED — a filename mentioned only in PROSE "
              f"was accepted as catalogued (got {prose_missing!r}, expected "
              "both files flagged). This is the exact tolerance that let "
              "get-diag-token.yml go uncatalogued for months.", file=sys.stderr)
        return 1

    # A SECTION HEADING is a catalog entry too — more documentation than a table
    # row, not less. Two real workflows (sync-vm-secrets.yml,
    # init-actions-secrets.yml) are catalogued this way, and requiring a table
    # row would have flagged the best-documented files in the doc.
    doc_section = ("#### `real-one.yml`\n\nWhat it does.\n\n"
                   "| `unnamed-one.yml` | Ops | AUTO | dispatch | Row form. |\n")
    if undocumented_workflows(files, documented_names(doc_section)):
        print("::error::SELF-TEST FAILED — a workflow documented under its own "
              "section heading was reported uncatalogued.", file=sys.stderr)
        return 1

    # A clean catalog must come back clean, or the guard is a permanent red.
    doc_ok = ("| `real-one.yml` | CI | AUTO | PR | x |\n"
              "| `unnamed-one.yml` | CI | AUTO | PR | y |\n")
    if undocumented_workflows(files, documented_names(doc_ok)):
        print("::error::SELF-TEST FAILED — a complete catalog was reported "
              "incomplete.", file=sys.stderr)
        return 1

    print("self-test OK — both directions fail closed, and a real "
          "non-workflow file is not mistaken for a phantom.")
    return 0


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description="workflow-catalog guard")
    ap.add_argument("--all", action="store_true",
                    help="check every workflow (the default; explicit for parity "
                         "with sibling guards)")
    ap.add_argument("--list", action="store_true",
                    help="print measured coverage and exit 0")
    ap.add_argument("--self-test", action="store_true",
                    help="exercise the failure paths and exit non-zero if broken")
    args = ap.parse_args(argv[1:])

    if args.self_test:
        return _self_test()

    doc_path = REPO / _CATALOG_DOC
    if not doc_path.is_file():
        print(f"::error::{_CATALOG_DOC} is missing — the workflow index this "
              f"guard enforces does not exist.", file=sys.stderr)
        return 1
    doc = doc_path.read_text(encoding="utf-8", errors="replace")

    files = workflow_files()
    if not files:
        print(f"::error::no workflow files found under {_WORKFLOW_DIR} — the "
              f"guard cannot have measured anything. Refusing to report a "
              f"clean catalog over an empty scan.", file=sys.stderr)
        return 1

    documented = documented_names(doc)
    tracked = tracked_basenames()

    if args.list:
        _print_coverage(files, documented, referenced_tokens(doc), tracked)
        return 0

    missing = undocumented_workflows(files, documented)
    phantoms = phantom_references(referenced_tokens(doc), tracked)

    if not missing and not phantoms:
        print(f"workflow-catalog: OK — all {len(files)} workflow file(s) are "
              f"catalogued in {_CATALOG_DOC} (a table row or its own section heading — a prose mention does NOT count), and every file it names exists.")
        return 0

    print("workflow-catalog guard: FAIL\n", file=sys.stderr)
    for f in missing:
        print(f"  - {_WORKFLOW_DIR}/{f} is not named in {_CATALOG_DOC}",
              file=sys.stderr)
    for t in phantoms:
        print(f"  - {_CATALOG_DOC} names `{t}`, which is not a file in this repo",
              file=sys.stderr)
    print(_EXPLAINER, file=sys.stderr)
    for f in missing:
        print(f"WORKFLOW_CATALOG_GUARD\t{_WORKFLOW_DIR}/{f}\tunnamed")
    for t in phantoms:
        print(f"WORKFLOW_CATALOG_GUARD\t{_CATALOG_DOC}\tphantom {t}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
