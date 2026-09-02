#!/usr/bin/env python3
"""Fail when a change introduces a tracking reference that resolves to nothing.

WHY
---
Operator directive, 2026-07-30: *"make sure the tasks don't fall between the cracks so that
we think they're done, and then it turns out in two weeks that something has been failing
because we incorrectly thought that we finished building something that we hadn't."*

A doc or comment saying "tracked by `BL-X`" where `BL-X` was never filed is **worse than no
reference at all**: it reads as tracked, so nobody re-checks it. A self-audit that day found
four such ids — including **`BL-20260730-M1-PRICE-JOIN-DEAD`**, which is the canonical
example in the binding "Green is not evidence" rule and is cited from four workflows, and
which resolved to nothing.

Resolved rows are **kept** in the backlogs (295 of them), not archived — so a dangling
reference genuinely means *never filed*, never *filed and pruned*.

DIFF-SCOPED, DELIBERATELY
-------------------------
A repo-wide sweep finds ~109 pre-existing dangling refs (21% of all cited ids). Failing on
all of them would produce an alarm that every session walks past — and
`CLAUDE-RULES-CANONICAL.md` names the routinely-ignored alarm as **itself a P1 bug**. So this
guard checks only ids the diff *introduces*, the same scoping `silent-empty-guard` uses. The
pre-existing debt is measured and attributed in `BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS`
rather than hidden behind a suppression.

Stdlib-only.

Usage:
  python scripts/ops/check_backlog_refs.py --base origin/main   # diff-scoped (CI)
  python scripts/ops/check_backlog_refs.py --all                # full sweep, report only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# A tracking id: BL-/MB-/FU- + YYYYMMDD + a SCREAMING-KEBAB slug. The trailing character
# class excludes a bare trailing '-' so a partial match inside prose can't masquerade as an
# id (that artefact produced two false "dangling" hits in the first measurement).
REF = re.compile(r'\b(?:BL|MB|FU)-\d{8}-[A-Z0-9]+(?:-[A-Z0-9]+)*\b')

SEARCH_DIRS = ("docs", "scripts", ".github", "config", "src")
BACKLOG_GLOB = "docs/claude/*backlog*.json"

#: Registers that are NOT ``docs/claude/*backlog*.json`` but do define ids this
#: guard's REF pattern matches, as (path, key-holding-the-list).
#:
#: ``REF`` has always matched three prefixes -- BL, MB and **FU** -- while
#: ``filed_ids`` read only the backlog glob. FU- rows do not live there; they live
#: in ``comms/follow_ups.json``. So **every FU- citation dangled by construction**,
#: however correctly it was filed. MEASURED 2026-09-02: 13 distinct FU- ids are
#: cited across SEARCH_DIRS and **12 of the 13 are genuinely filed** in that
#: register. Adding it removes 12 false findings and keeps the one real one --
#: so this makes the guard MORE accurate, not quieter. That thirteenth id is the
#: positive control, and it is named in ``tests/test_check_backlog_refs.py``
#: rather than here **on purpose**: ``tests/`` is outside SEARCH_DIRS, and writing
#: a deliberately-unresolvable id into a scanned path makes THIS guard report it,
#: which is a real false-positive class of its own (citing an id *as an example of
#: non-resolution* is not claiming it tracks anything). Recorded in
#: BL-20260902-FU-IDS-CAN-NEVER-RESOLVE-IN-CHECK-BACKLOG-REFS.
EXTRA_REGISTERS = (("comms/follow_ups.json", "follow_ups"),)


def filed_ids(repo: pathlib.Path = REPO) -> set[str]:
    out: set[str] = set()
    for f in glob.glob(str(repo / BACKLOG_GLOB)):
        try:
            for it in json.load(open(f, encoding="utf-8")).get("items", []):
                if it.get("id"):
                    out.add(str(it["id"]))
        except Exception:  # noqa: BLE001 — a malformed backlog is another guard's problem
            continue
    for rel, key in EXTRA_REGISTERS:
        try:
            for it in json.load(open(repo / rel, encoding="utf-8")).get(key, []):
                if isinstance(it, dict) and it.get("id"):
                    out.add(str(it["id"]))
        except Exception:  # noqa: BLE001 — same reasoning as above
            continue
    return out


def _git(args: list[str], repo: pathlib.Path) -> str:
    return subprocess.run(["git", "-C", str(repo)] + args,
                          capture_output=True, text=True).stdout


def _refs_in_file_at(ref: str, path: str, repo: pathlib.Path) -> set[str] | None:
    """Every tracking id cited in one file as of `ref`.

    ``None`` means THE FILE DID NOT EXIST at `ref` — deliberately not ``set()``,
    which means *the file existed and cited nothing*. Those are different facts
    and collapsing them is what made a verbatim SPLIT read as an introduction
    (see :func:`_refs_anywhere_at`), so they are kept apart per
    ``CLAUDE-RULES-CANONICAL.md`` § "Collapsed states".
    """
    out = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"],
                         capture_output=True, text=True)
    return set(REF.findall(out.stdout)) if out.returncode == 0 else None


def _refs_anywhere_at(ref: str, repo: pathlib.Path) -> set[str]:
    """Every tracking id cited ANYWHERE under SEARCH_DIRS as of `ref`.

    The fallback for a file that did not exist at `ref`. The "already cited in
    this file" exemption above exists so that re-sorting or reformatting a file
    is not a finding — but it keys on the FILE, so **moving** existing prose into
    a NEW file defeats it: the new file cites nothing at base by construction, so
    every id it carries reads as introduced.

    That is not hypothetical. Splitting the API reference out of ``CLAUDE.md``
    (2026-09-02) moved the rows VERBATIM and this guard reported five dangling
    ids as newly introduced — all five long-standing, and already attributed to
    ``BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS``, the row the module docstring
    names as the home for exactly this pre-existing debt. The guard's own stated
    intent is that *"moving an existing one is not a finding"*; this makes the
    code match it.

    ⚠️ **This is narrower than it looks, and it is not a relaxation of what the
    guard targets.** It applies ONLY to a path absent at `ref`. An id cited
    NOWHERE in the tree at `ref` still fails, in a new file and an existing one
    alike — which is the whole class the guard exists for (a doc saying "tracked
    by BL-X" for a row nobody ever filed). What it stops flagging is debt that
    demonstrably predates the diff, which the diff-scoping already excludes
    everywhere else.
    """
    # `git grep -E` is POSIX ERE: no `\b`, no `\d`, no `(?:...)`. Passing REF.pattern
    # straight in exits 128 ("Invalid preceding regular expression"), which the rc
    # check below would then correctly refuse rather than silently read as "nothing
    # cited at base". So grep with a deliberate SUPERSET and let the real REF regex
    # do the exact matching on the output — the coarse filter may over-select, never
    # under-select, which is the only direction that is safe here.
    out = subprocess.run(
        ["git", "-C", str(repo), "grep", "-h", "-I", "-E",
         "(BL|MB|FU)-[0-9]{8}-", ref, "--"] + list(SEARCH_DIRS),
        capture_output=True, text=True)
    # rc 1 == "no matches", which is a real and clean answer; anything higher is
    # a git failure and must not be read as "nothing was cited at base" — that
    # would silently restore the blindness this function removes.
    if out.returncode > 1:
        raise RuntimeError(
            f"git grep failed against {ref!r} (rc={out.returncode}): "
            f"{out.stderr.strip()[:200]}")
    return set(REF.findall(out.stdout))


def refs_in_added_lines(base: str, repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    """Tracking ids GENUINELY introduced by the diff, mapped id -> {files}.

    "On an added line" is not the same as "introduced". Re-sorting or reformatting a file
    rewrites every line, so long-standing content shows up as added and its pre-existing
    references read as new. That is not hypothetical: union-merging the health-review backlog
    re-ordered it and this guard fired on 12 dangling ids that had been there for weeks —
    precisely the pre-existing debt the diff-scoping exists to EXCLUDE (see the module
    docstring on alarm fatigue). A guard that cries wolf on a reformat teaches sessions to
    suppress it, which is worse than not having it.

    So an id is only "introduced" if it was NOT already cited in that same file at `base`.
    A genuinely new dangling ref is still caught; moving an existing one is not a finding.
    """
    diff = _git(["diff", "-U0", f"{base}...HEAD", "--"] + list(SEARCH_DIRS), repo)
    found: dict[str, set[str]] = {}
    current = "?"
    already: dict[str, set[str]] = {}
    tree_at_base: set[str] | None = None   # computed lazily; only new files need it
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            if current not in already:
                in_file = _refs_in_file_at(base, current, repo)
                if in_file is None:            # the file is NEW at base
                    if tree_at_base is None:
                        tree_at_base = _refs_anywhere_at(base, repo)
                    already[current] = tree_at_base
                else:
                    already[current] = in_file
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for m in REF.findall(line):
            if m in already.get(current, set()):
                continue  # already cited in this file before the change — not introduced
            found.setdefault(m, set()).add(current)
    return found


def refs_everywhere(repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for d in SEARCH_DIRS:
        root = repo / d
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            if p.suffix.lower() not in {".md", ".py", ".yml", ".yaml", ".json", ".sh"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            for m in REF.findall(text):
                found.setdefault(m, set()).add(str(p.relative_to(repo)))
    return found


def dangling(found: dict[str, set[str]], filed: set[str]) -> dict[str, set[str]]:
    # A backlog file citing an id in another row's `refs` is not a definition, so no
    # special-casing here: the id is either filed as a row or it dangles.
    return {k: v for k, v in sorted(found.items()) if k not in filed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--base", default=None,
                    help="git ref to diff against (diff-scoped mode)")
    ap.add_argument("--all", action="store_true",
                    help="full sweep; REPORTS the pre-existing debt, does not fail on it")
    args = ap.parse_args(argv)
    repo = pathlib.Path(args.repo_root)
    filed = filed_ids(repo)

    if args.all:
        bad = dangling(refs_everywhere(repo), filed)
        print(f"{len(filed)} filed ids; {len(bad)} dangling references repo-wide")
        for k, v in bad.items():
            print(f"  {k}  <- {sorted(v)[0]}"
                  + (f" (+{len(v) - 1} more)" if len(v) > 1 else ""))
        # Report-only by design: see the module docstring on alarm fatigue.
        return 0

    if not args.base:
        print("::error::--base <ref> or --all required")
        return 1

    bad = dangling(refs_in_added_lines(args.base, repo), filed)
    if not bad:
        print("OK — every tracking id this change introduces resolves to a filed "
              "backlog row.")
        return 0

    print("::error::this change introduces tracking reference(s) that resolve to NOTHING. "
          "A doc saying 'tracked by BL-X' where BL-X was never filed reads as tracked "
          "while being tracked by nobody — the exact crack the 2026-07-30 operator "
          "directive names.")
    for k, v in bad.items():
        for f in sorted(v):
            print(f"  {f}: {k}")
    print("")
    print("Fix: file the row in the right backlog (docs/claude/*-backlog.json) with honest "
          "severity and enough detail to act on, or correct the id if it is a typo/rename.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # The `--all` report is piped through `head` in artifact-validity-guard.yml, which
        # closes the pipe early and made this exit with a traceback on stderr. Harmless today
        # only because the step's exit status is `head`'s — adding `pipefail` there would have
        # turned a cosmetic wart into a red CI step for no reason.
        try:
            sys.stderr.close()
        finally:
            raise SystemExit(0) from None
