#!/usr/bin/env python3
"""WHO resolves a `--base` to the fork point, and who reads it wherever it points?

WHY A CENSUS AND NOT ANOTHER SENTENCE. `scripts/ci/_git_base.py`'s docstring
carried this as a frozen prose number — *"of 10 scripts that take `--base` and
read file content there, 7 read the tip and 3 resolve the merge base"* — and by
2026-09-13 every term of it was wrong: the population was 12 and the split was
7 resolving to 5 reading the tip. Nothing had gone wrong; four fixes had landed
and the sentence could not know. A measurement that must be retyped to stay true
goes stale silently, which is the failure this repo keeps paying for. This
re-derives it.

⚠️ **IT COUNTS A PATTERN. IT DOES NOT GRADE HARM, AND MUST NOT BE READ AS IF IT
DID.** Whether reading at the tip is a DEFECT depends on what the caller then
compares, which this cannot see:

  * A **content or count** comparison is contaminated outright. Measured on
    `check_register_reserialization` 2026-09-12: an honest one-row append
    reported *"421 base line(s) lost"* against 0 at the fork point, because rows
    other sessions added after the fork read as this diff's losses.
  * An **id set-difference** — the `if rid in base_ids: continue` shape shared by
    `check_claim_basis` and `check_register_ids` — is **benign in the ordinary
    case**, and that was established by running it, not by reading it. Ids the
    base GAINS are ids the branch does not have, so the graded set is unchanged.
    Measured 2026-09-13 on `check_claim_basis.check_new_rows`: fork point and
    tip both returned exactly 1 finding, identical.
  * That same shape DOES break in one narrow case: when the base **removes** a
    row the branch still carries, a tip read grades that row as NEW. Measured on
    the same function: 2 findings against 1. This repo does land removal
    declarations, so the case is real rather than theoretical — it is simply
    rare, and it is a FALSE POSITIVE, the direction that reddens somebody's PR.

So a session draining this list must ask, per script, what is being compared.
"Reads at the tip" is the population, not the verdict. Ranking the list by
severity from here would be exactly the unprovenanced-diagnostic-output class
this repo names: a confident label over a quantity nobody measured.

⚠️ **WHAT THE DENOMINATOR INCLUDES, because the first version of this probe got
it wrong.** A script is in the population when it accepts `--base`/`--base-ref`
AND reads file content at a ref — either directly via `git show` or by
DELEGATING to `_git_base.read_at`. The first draft required a literal `git
show`, which silently dropped every delegating caller — including the two
scripts most recently FIXED to delegate. It reported 10/5/5 where the truth was
12/7/5, and it reported it confidently. A probe whose negative is uninformative
is the thing this whole family of rows is about, so the self-test below plants a
delegating reader specifically.

NOT WIRED INTO `run_guards.py`, DELIBERATELY. This is a census, not a guard: it
has no failing condition, because "reads at the tip" is not by itself a defect
(see above). A guard that failed on the pattern would fire on the two scripts
measured NOT to be harmed by it, and the cheapest way to satisfy it would be to
add a resolver call nobody uses — decoration that makes the count look better.

Run:  python3 scripts/ops/base_resolution_census.py
      python3 scripts/ops/base_resolution_census.py --self-test
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Two exclusions, both for the same reason: these files CONTAIN the patterns
#: rather than USING them.
#:
#: ⚠️ THE SECOND ONE IS THIS FILE, AND IT WAS FOUND BY RUNNING THE PROBE, NOT BY
#: FORESEEING IT. The first run reported a population of 13 against a
#: hand-measured 12, because the census's own regex literals spell `"--base"`,
#: `"show"` and `_git_base.read_at`, so it matched itself and filed itself as a
#: tip-reader. A probe inside its own denominator is a small instance of exactly
#: what this module exists to count, and it is recorded here rather than quietly
#: deleted: the number was wrong by one, in the direction that makes the problem
#: look bigger, and only a comparison against a separately-derived count caught
#: it.
EXCLUDE = {
    "scripts/ci/_git_base.py",          # IS the thing being resolved through
    "scripts/ops/base_resolution_census.py",   # spells the patterns, uses none
}

BASE_OPT = re.compile(r"""["'](?:--base|--base-ref)["']""")
DIRECT_READ = re.compile(r"""["']show["']""")
DELEGATED_READ = re.compile(r"_git_base\.read_at")

SHARED = "_git_base.resolve_base"
OWN = "its own resolve_base()"
RAW = "a raw `merge-base` call"


def classify(src: str) -> tuple[bool, str | None, str]:
    """``(in_population, how_it_resolves_or_None, how_it_reads)``.

    Pure, so the policy is arguable in the self-test rather than against the
    tree — the tree is the one input that changes under you.
    """
    if not BASE_OPT.search(src):
        return False, None, ""
    direct, delegated = bool(DIRECT_READ.search(src)), bool(DELEGATED_READ.search(src))
    if not (direct or delegated):
        return False, None, ""
    how = None
    if "_git_base" in src and "resolve_base" in src:
        how = SHARED
    elif re.search(r"def resolve_base\b", src):
        how = OWN
    elif '"merge-base"' in src or "'merge-base'" in src:
        how = RAW
    return True, how, "delegates to read_at" if delegated else "direct `git show`"


def census(root: pathlib.Path = REPO) -> dict:
    resolved: list[tuple[str, str, str]] = []
    at_tip: list[tuple[str, str]] = []
    for p in sorted(root.glob("scripts/**/*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        if rel in EXCLUDE:
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        inpop, how, reads = classify(src)
        if not inpop:
            continue
        (resolved.append((rel, how, reads)) if how else at_tip.append((rel, reads)))
    return {"population": len(resolved) + len(at_tip),
            "resolved": resolved, "at_tip": at_tip}


def _selftest(quiet: bool = False) -> tuple[bool, list[str]]:
    fails: list[str] = []

    def say(m: str) -> None:
        if not quiet:
            print(m)

    def check(label: str, src: str, want_in: bool, want_how: str | None) -> None:
        inpop, how, _reads = classify(src)
        ok = inpop == want_in and (not want_in or how == want_how)
        if not ok:
            fails.append(f"{label}: in_population={inpop} how={how!r} — wanted "
                         f"{want_in} / {want_how!r}")
        say(f"  {'ok ' if ok else 'FAIL'} {label}")

    # POSITIVE CONTROL FIRST. Without one, a classifier that answered "no" to
    # everything would pass every plant below and report a population of zero —
    # the emptiest possible vacuous pass.
    check("a plain tip-reader is IN, and unresolved",
          'ap.add_argument("--base")\nsubprocess.run(["git", "show", f"{base}:{p}"])',
          True, None)

    check("a caller using the shared resolver is IN, and resolved",
          'ap.add_argument("--base")\nimport _git_base\n'
          'r, s = _git_base.resolve_base(base)\nsubprocess.run(["git","show",r])',
          True, SHARED)

    # THE ONE THE FIRST DRAFT MISSED: content read DELEGATED, no `git show` in
    # the file at all. This plant is why the denominator is now right.
    check("a DELEGATING reader is IN the population (no `git show` anywhere)",
          'ap.add_argument("--base")\nimport _git_base\n'
          'st, txt = _git_base.read_at(ref, rel)\n'
          'b, s = _git_base.resolve_base(base)',
          True, SHARED)

    check("a script with its OWN resolve_base is resolved",
          'ap.add_argument("--base")\ndef resolve_base(ref):\n    pass\n'
          'subprocess.run(["git", "show", f"{base}:{p}"])', True, OWN)

    check("a raw merge-base call counts as resolving",
          'ap.add_argument("--base")\nsubprocess.run(["git","merge-base","HEAD",b])\n'
          'subprocess.run(["git", "show", f"{base}:{p}"])', True, RAW)

    # THE DENOMINATOR MUST NOT INFLATE. These two are the reason the count means
    # anything: a script that takes --base but never reads content at it has
    # nothing to read at the wrong ref, and a content reader with no --base is
    # not being pointed anywhere by a caller.
    check("--base but NO content read is OUT of the population",
          'ap.add_argument("--base")\nsubprocess.run(["git","diff","--name-only",b])',
          False, None)
    check("a content read with NO --base is OUT of the population",
          'subprocess.run(["git", "show", f"main:{p}"])', False, None)

    # AND THE PROBE MUST NOT COUNT ITSELF. It did, on its first run: the regex
    # literals below spell every pattern it looks for, so it filed itself as a
    # tip-reader and reported 13 where a separately-derived count said 12.
    c0 = census()
    listed = [r for r, _h, _x in c0["resolved"]] + [r for r, _x in c0["at_tip"]]
    self_counted = "scripts/ops/base_resolution_census.py" in listed
    if self_counted:
        fails.append("the census counts ITSELF — its own regex literals match, "
                     "so the population is inflated by one")
    say(f"  {'ok ' if not self_counted else 'FAIL'} the census is not in its own "
        "denominator")

    # ...and the exclusion must be doing real work, or it is decoration. Read
    # this file's own source through the classifier: it MUST match, which is
    # why it has to be excluded by path rather than by hoping it does not.
    _in, _how, _r = classify(pathlib.Path(__file__).read_text(encoding="utf-8"))
    if not _in:
        fails.append("this file no longer matches the classifier, so its "
                     "EXCLUDE entry is silently doing nothing — either the "
                     "entry is stale or the patterns stopped matching")
    say(f"  {'ok ' if _in else 'FAIL'} the self-exclusion is load-bearing (this "
        "file does match the classifier, and is excluded by path)")

    # THE LIVE TREE MUST NOT BE EMPTY. An empty census reads exactly like a
    # clean one, and it is the shape every vacuous pass in this family took.
    c = census()
    if c["population"] < 5:
        fails.append(f"live census found only {c['population']} script(s) — a "
                     "population this small means the probe stopped matching, "
                     "not that the repo stopped using --base")
    say(f"  {'ok ' if c['population'] >= 5 else 'FAIL'} the live tree yields a "
        f"non-trivial population ({c['population']})")
    return not fails, fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        print("base-resolution census self-test")
        ok, fails = _selftest()
        if not ok:
            for f in fails:
                print(f"::error::{f}")
            return 1
        print("self-test OK")
        return 0

    c = census()
    print(f"POPULATION: {c['population']} script(s) under scripts/ that take a "
          "--base/--base-ref AND read file content at a ref")
    print("            (directly via `git show`, or by delegating to "
          "_git_base.read_at — the delegating")
    print("             callers are in the denominator, and an earlier probe "
          "that missed them under-counted)\n")
    print(f"RESOLVE THE FORK POINT: {len(c['resolved'])} of {c['population']}")
    for rel, how, reads in c["resolved"]:
        print(f"  ok    {rel:48} via {how} ({reads})")
    print(f"\nREAD AT WHATEVER REF WAS PASSED: {len(c['at_tip'])} of "
          f"{c['population']}")
    for rel, reads in c["at_tip"]:
        print(f"  tip   {rel:48} ({reads})")
    print("\n⚠️ THIS IS A COUNT OF A PATTERN, NOT A LIST OF DEFECTS. Reading at "
          "the tip contaminates a CONTENT or COUNT comparison outright, and is "
          "benign for an id set-difference except when the base REMOVES a row "
          "the branch still carries. Ask, per script, what is compared — see "
          "this module's docstring for the measurements behind that sentence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
