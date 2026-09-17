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

THE BASE IS READ AT THE TIP, AND THAT IS RIGHT BY DESIGN
--------------------------------------------------------
Classified 2026-09-17 (``BL-20260913-CHECK-BACKLOG-REFS-READS-THE-BASE-TIP-AND-IS-THE-ONE-MEMBER-OF-THE-BASE-VS-TIP-CLASS-THE-PER-SCRIPT-AUDIT-NEVER-COVERED``),
the last
of the five tip-readers the base-vs-tip census counts to be graded either way. The reason
lives HERE as well as in the audit in `scripts/ops/check_backlog_criteria.py::_load_at_ref`,
because that row's whole finding was that **a warning in another file does not reach someone
reading this one.**

This module's base read is :func:`_refs_anywhere_at`, a FALLBACK that fires only for a path
ABSENT at the base. It asks *"is this id cited anywhere in the tree I am merging INTO?"* —
and the tree being merged into is the TIP. The tip is the question's own subject, not an
approximation of it, which is the same reason `check_register_ids.py` reads the tip.

⚠️ **THE DIRECTION MATTERS MORE THAN THE COUNT.** Switching to the fork point would make a
guard that runs on EVERY PR blame a diff for a dangling id a CONCURRENT branch introduced —
false blame, and the guard would red PRs for what the base did. The reverse error is real and
is stated rather than hidden: when this diff and a concurrent branch independently cite the
same DANGLING id, the tip read exempts it here. That id is not lost — it lands in
``BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS``, which this docstring already names as the
declared home for pre-existing debt, and the ``--all`` sweep still reports it.

MEASURED on ``origin/main`` 2026-09-17: **1,856** ids cited at the tip, **86** of them
dangling. Of the ids cited ONLY in the last **6 / 20 / 60** commits — the entire population
where tip and fork point can disagree — **0 are dangling, at every window.** So the choice
has changed no finding to date. That is a measurement over a window, not a proof for all
time; ``tests/test_backlog_refs_base_is_the_tip.py`` pins the classification so a later
change to a merge base has to be a decision rather than a tidy-up.

Stdlib-only.

Usage:
  python scripts/ops/check_backlog_refs.py --base origin/main   # diff-scoped (CI)
  python scripts/ops/check_backlog_refs.py --all                # full sweep, report only
"""
from __future__ import annotations

import argparse
import difflib
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


def filed_ids_with_state(repo: pathlib.Path = REPO) -> tuple[set[str], list[str]]:
    """`(every filed id, the registers we could NOT read)`.

    ⚠️ **THE SECOND HALF EXISTS BECAUSE THE UNIVERSE SHRINKING IS INVISIBLE.**
    A register that does not parse was skipped with `continue` and the comment
    *"a malformed backlog is another guard's problem"* — defensible about whose
    job the CORRUPTION is, and silent about what it does HERE: every id that
    register defines drops out of `filed`, so every citation of one reads as a
    reference resolving to NOTHING.

    MEASURED on `origin/main` @`eb606713d` with one `THIS IS NOT JSON` line
    inserted into `docs/claude/health-review-backlog.json`: `--all` went from
    **86 dangling references to 1660** — exactly **+1574**, the row count of
    that file. Every one of the added 1574 is a confident claim that a row which
    exists does not, and 1660 lines of it is the desensitised alarm this repo
    calls its own worst failure mode, with the 86 real findings buried inside.

    ⚠️ **THE GATING `--base` MODE WAS MEASURED AND IS NOT AFFECTED — do not
    "fix" it.** An id already cited at the base is exempted by
    `_refs_anywhere_at`, and every real row id is cited at base in its own
    register row, so the gate cannot false-fail on one. Verified with a positive
    control that came back OK, and then by establishing WHY rather than reading
    the OK as evidence. The one reachable false-PASS needs a single diff to FILE
    a row and CORRUPT the register, which `register-id-guard` reds anyway. So
    the gate gets a WARNING, not a refusal: a refusal nothing can reach is the
    decorative branch `collapsed-state-guard` exists to refuse.
    """
    out: set[str] = set()
    unreadable: list[str] = []
    for f in sorted(glob.glob(str(repo / BACKLOG_GLOB))):
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001 — whose job the CORRUPTION is, is another guard's
            unreadable.append(str(pathlib.Path(f).relative_to(repo)))
            continue
        for it in (doc.get("items") or []):
            if isinstance(it, dict) and it.get("id"):
                out.add(str(it["id"]))
    for rel, key in EXTRA_REGISTERS:
        try:
            doc = json.load(open(repo / rel, encoding="utf-8"))
        except FileNotFoundError:
            # ABSENT, not unreadable. `comms/follow_ups.json` is optional and a
            # tree without it is not a tree whose universe is incomplete.
            continue
        except Exception:  # noqa: BLE001
            unreadable.append(rel)
            continue
        for it in (doc.get(key) or []):
            if isinstance(it, dict) and it.get("id"):
                out.add(str(it["id"]))
    return out, unreadable


def filed_ids(repo: pathlib.Path = REPO) -> set[str]:
    """The ids only. Callers that report a COUNT must use the pair above."""
    return filed_ids_with_state(repo)[0]


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


# Three states, never collapsed. "We found a likely id", "we found nothing
# close" and "several are equally likely" are different answers, and the third
# is the one a single-suggestion design silently turns into the first.
SUGGEST_PREFIX = "prefix"
SUGGEST_AMBIGUOUS = "ambiguous"    # too many start with it to name one
SUGGEST_FUZZY = "fuzzy"
SUGGEST_NONE = "none"

#: How many candidates to show. More than this is not a hint, it is a second
#: problem to read.
_MAX_SUGGESTIONS = 3

#: Below this length a "prefix" is not evidence of anything -- `BL-2026` would
#: match hundreds of rows and the hint would be noise dressed as help.
_MIN_PREFIX_LEN = 12


def suggest_for(ref: str, filed: set[str]) -> tuple[str, list[str]]:
    """`(kind, candidates)` -- which filed id did the author probably mean?

    ⚠️ PREFIX FIRST, AND NOT BECAUSE IT IS EASIER. MEASURED 2026-09-13 over
    every dangling reference this guard raised against one session's branches
    (n=3, one session, one author -- state the population, it is small):
    ALL THREE were exact PREFIXES of a real filed row.

        BL-...-A-DUPLICATE-ROW-ID-REACHED-MAIN          elided in prose
        BL-...-GRADES-THE-WRONG-TREE                    elided in prose
        BL-...-GRADES-THE-WRONG                         WRAPPED across a
                                                        docstring line

    None was a typo. The failure mode these ids actually have is REFORMATTING
    -- an id shortened or line-broken for readability stops being an id -- and
    that always truncates, never garbles. `difflib` is the obvious
    implementation and is NOT the one the evidence asks for, so it is the
    fallback rather than the rule.

    ⚠️ AMBIGUITY IS REPORTED, NEVER RESOLVED. A prefix shared by several filed
    rows returns all of them (capped). Picking the first would be the
    implicit-input-selection shape `check_diagnostic_provenance.py` exists to
    catch: a confident single answer computed from an arbitrary tiebreak.

    ⚠️ IT SUGGESTS AND NEVER REWRITES. The author may genuinely have meant an
    id nobody has filed yet, and the fix for that is to file the row -- which
    the existing message already says. This only removes the lookup.
    """
    if len(ref) >= _MIN_PREFIX_LEN:
        pre = sorted(f for f in filed if f.startswith(ref) and f != ref)
        if len(pre) > _MAX_SUGGESTIONS:
            # ⚠️ NOT `prefix` WITH THE FIRST THREE. MEASURED on the live
            # register (1876 filed ids): a bare date prefix matched 9 rows and
            # a date-plus-common-word prefix matched 29, so showing three of
            # them would present an arbitrary alphabetical tiebreak as a hint.
            #
            # ⚠️ The illustrations are DESCRIBED rather than QUOTED for a
            # reason that is this function's own subject: writing a truncated
            # id here made THIS FILE carry a dangling reference, and the guard
            # refused its own source. A comment about ids is still text an id
            # extractor reads.
            #
            # Saying HOW MANY is the useful answer: it tells the author their
            # reference is too truncated to identify anything, which is a
            # different problem from having mistyped one.
            return SUGGEST_AMBIGUOUS, pre
        if pre:
            return SUGGEST_PREFIX, pre
    close = difflib.get_close_matches(ref, sorted(filed), n=_MAX_SUGGESTIONS,
                                      cutoff=0.85)
    if close:
        return SUGGEST_FUZZY, list(close)
    return SUGGEST_NONE, []


def suggestion_lines(ref: str, filed: set[str]) -> list[str]:
    """The hint, as lines. Empty when there is nothing to say -- a hint printed
    on every finding whether or not it has content is decoration, and this repo
    walks past decoration."""
    kind, cands = suggest_for(ref, filed)
    if kind == SUGGEST_NONE:
        return []
    if kind == SUGGEST_AMBIGUOUS:
        return [f"      ↳ {len(cands)} filed ids START WITH this, so it names "
                f"none of them — the reference is truncated too far to "
                f"identify a row. Cite the id in full."]
    if kind == SUGGEST_PREFIX:
        head = ("      ↳ a filed id STARTS WITH this. An id shortened or "
                "line-wrapped for readability stops being an id:")
    else:
        head = "      ↳ did you mean:"
    return [head] + [f"          {c}" for c in cands]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--base", default=None,
                    help="git ref to diff against (diff-scoped mode)")
    ap.add_argument("--all", action="store_true",
                    help="full sweep; REPORTS the pre-existing debt, does not fail on it")
    args = ap.parse_args(argv)
    # The verdict below is about the COMMITTED tree. Say so when that is
    # not the tree you edited — BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS.
    import pathlib  # noqa: PLC0415 — local, so importing this module stays free
    import sys as _sys  # noqa: PLC0415
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ci"))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()

    repo = pathlib.Path(args.repo_root)
    filed, unreadable = filed_ids_with_state(repo)

    if unreadable:
        # ⚠️ BEFORE THE LIST, NEVER AFTER IT. The list is the thing that becomes
        # untrustworthy, and a reader who has already read 1660 "resolves to
        # NOTHING" lines has drawn the conclusion by the time a footnote lands.
        print("::error::the universe of FILED ids is INCOMPLETE — "
              f"{len(unreadable)} register(s) are present and do NOT parse, so "
              "every id they define is missing from it and every citation of "
              "one reads as a reference resolving to NOTHING. Measured: one "
              "corrupt 1574-row backlog turns 86 dangling references into 1660. "
              "Treat the report below as OVER-COUNTING by an unknown amount.")
        for rel in unreadable:
            print(f"  - {rel}")
        print("Fix the register first (git checkout the last parseable copy, or "
              "resolve the conflict row-aware via scripts/ops/merge_json_register.py), "
              "then re-run. register-id-guard fails on this too, and that is the "
              "blocking half.")

    if args.all:
        bad = dangling(refs_everywhere(repo), filed)
        qualifier = (" — OVER-COUNTED, see the incomplete universe above"
                     if unreadable else "")
        print(f"{len(filed)} filed ids; {len(bad)} dangling references "
              f"repo-wide{qualifier}")
        for k, v in bad.items():
            print(f"  {k}  <- {sorted(v)[0]}"
                  + (f" (+{len(v) - 1} more)" if len(v) > 1 else ""))
        # Report-only on the DEBT by design (see the module docstring on alarm
        # fatigue) — but an unreadable register is not debt, it is a broken
        # input, and a sweep that cannot state its own denominator should not
        # exit 0. This mode is `allow_fail` in run_guards.py, so it still gates
        # nothing; what changes is that it stops CLAIMING a number it cannot
        # stand behind.
        return 2 if unreadable else 0

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
        # ⚠️ DIFF-SCOPED MODE ONLY, deliberately. `--all` reports a standing
        # debt of dozens of references (86 when this was written) and is
        # report-only by design for exactly the alarm-fatigue reason this
        # module's docstring gives; four extra lines each would turn a long
        # report into an unreadable one. This mode is a human trying to fix
        # ONE thing right now.
        for line in suggestion_lines(k, filed):
            print(line)
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
