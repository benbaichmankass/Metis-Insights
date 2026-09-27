#!/usr/bin/env python3
"""Every NEW research-queue unit pre-registers a `decision_rule` — or it holds.

WHY THIS EXISTS
----------------
Checklist row **C1**, operator note: *"Decide before you measure: the result
IS the decision, removing the interpretation step where everything currently
dies (1 actioned disposition of 117)."* A unit in `research/queue/` asks a
question and fires a run; without a rule stating in advance what each outcome
means, the run lands and then waits for someone to look at it and decide what
it meant — which is exactly the step this repo has measured dying (404
research memos, one ever actioned). `decision_rule` closes that gap the same
way `docs/CLAUDE-RULES-CANONICAL.md`'s promotion ladder closes it for Stage 0:
**a result clears a rule registered BEFORE the run**, not a rule invented to
fit the result.

Three units already write this block by convention
(`RQ-20260922-005/008/009`) — this makes it structural for anything NEW,
without punishing what predates the convention.

WHAT IT CHECKS
--------------
Population is every `research/queue/*.yaml` and `research/queue/blocked/*.yaml`
(the blocked/ directory documents itself as "same schema" — see
`research/queue/README.md` § "blocked/"), excluding README.md.

  * A unit's `decision_rule` is admissible when it is a dict carrying
    non-empty `id`, `registered_at`, `statistic`, `rule`, and
    `registered_before_run` is literally `True` — not merely present, since a
    rule registered AFTER the run is the exact failure this exists to refuse
    and a truthy-but-unchecked flag would let that pass silently.
  * A unit **NEW on this branch** (absent at the merge-base with `--base`)
    with no admissible `decision_rule` FAILS.
  * A unit that already existed at the merge-base is GRANDFATHERED: it is
    counted, never failed. Age excuses not having the convention when it was
    written; it does not excuse a NEW unit skipping it.
  * The population-wide count of units lacking an admissible `decision_rule`
    (new and grandfathered together) is PRINTED ON EVERY RUN — the population
    is the filesystem, not the diff, so a count that quietly changed is
    visible in the log rather than inferable from a diff nobody reads.

STATES, NEVER COLLAPSED (same family as `check_pr_landing.py`)
----------------------------------------------------------------
  ``base_unresolved``   — `--base` was given but the merge-base could not be
                          read. *We did not look*, so every file in the diff
                          is treated as unverifiably-new and graded on the
                          strict branch — the refusing direction, because a
                          guard that grants by default on "could not look" is
                          worse than one that occasionally over-blocks.
  ``no_base``           — no `--base` given (a whole-tree run, e.g. a plain
                          local invocation). Nothing is graded as NEW; the
                          population count still prints. Not a silent pass —
                          printed as its own line so a CI wiring mistake that
                          drops `--base` is visible rather than a quiet no-op.
  ``graded``            — `--base` resolved; each file graded new vs
                          grandfathered against it.

Tier-1: reads committed files plus local git plumbing. No live path, no
network, no VM.

Run:
    python3 scripts/ci/check_research_queue_decision_rule.py --base origin/main
    python3 scripts/ci/check_research_queue_decision_rule.py --self-test
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
QUEUE_DIRS = ("research/queue", "research/queue/blocked")


def _git(root: Path, *args: str) -> Tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args],
                        capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def queue_files(root: Path) -> List[str]:
    """Repo-relative paths of every queue unit, both dirs, excluding README."""
    out: List[str] = []
    for d in QUEUE_DIRS:
        dirpath = root / d
        if not dirpath.is_dir():
            continue
        for p in sorted(dirpath.glob("*.yaml")):
            out.append(p.relative_to(root).as_posix())
    return out


def decision_rule_errors(entry: dict) -> List[str]:
    """Non-empty list means the unit's `decision_rule` is NOT admissible."""
    dr = entry.get("decision_rule")
    if not isinstance(dr, dict):
        return ["`decision_rule` is missing or not a mapping — a unit must "
                "state, before the run, what result means what action"]
    errs = []
    for key in ("id", "registered_at", "statistic", "rule"):
        val = dr.get(key)
        if not (isinstance(val, str) and val.strip()):
            errs.append(f"`decision_rule.{key}` is required and must be a "
                        f"non-empty string, got {val!r}")
    # ⚠️ Checked for being literally True, not merely truthy/present. A rule
    # written up AFTER looking at the result and stamped `registered_before_run`
    # by hand is indistinguishable from a real one by presence alone — the
    # `new-table-wiring-guard` lesson: a guard cheaper to lie to than to
    # satisfy is worse than no guard. This guard cannot prove WHEN the text was
    # written; what it can and does enforce is that the unit affirmatively
    # claims pre-registration rather than leaving the field absent or false.
    if dr.get("registered_before_run") is not True:
        errs.append("`decision_rule.registered_before_run` must be `true` — "
                     "a rule is only a pre-registration if the unit says so")
    return errs


def existed_at_merge_base(root: Path, base: str, rel: str) -> Optional[bool]:
    """True/False, or None meaning the merge-base itself could not be read."""
    rc, mb = _git(root, "merge-base", base, "HEAD")
    if rc != 0 or not mb:
        return None
    rc, _ = _git(root, "cat-file", "-e", f"{mb}:{rel}")
    return rc == 0


def _load_yaml(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment-dependent
        return None, f"pyyaml unavailable: {exc}"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — any parse/read failure is "unreadable"
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(raw, dict):
        return None, "not a YAML mapping"
    return raw, None


def scan(root: Path, base: Optional[str]) -> Tuple[List[str], int, int, str]:
    """Returns (failures, total_units, missing_count, base_state)."""
    files = queue_files(root)
    total = len(files)
    missing = 0
    failures: List[str] = []

    base_state = "no_base"
    merge_base_ok = True
    if base is not None:
        rc, mb = _git(root, "merge-base", base, "HEAD")
        if rc != 0 or not mb:
            base_state = "base_unresolved"
            merge_base_ok = False
        else:
            base_state = "graded"

    for rel in files:
        entry, read_err = _load_yaml(root / rel)
        if read_err is not None:
            # Unreadable is its own finding regardless of new/grandfathered —
            # a file the dispatcher itself cannot load is not "grandfathered
            # clean", it is broken.
            failures.append(f"{rel}: unreadable — {read_err}")
            continue

        errs = decision_rule_errors(entry)
        if not errs:
            continue
        missing += 1

        if base is None:
            continue  # no_base: population is counted, nothing is failed

        if not merge_base_ok:
            # base_unresolved: we cannot tell new from grandfathered, so grade
            # every unit on the strict branch rather than grant by default.
            failures.append(
                f"{rel}: merge-base with `{base}` could not be read, so "
                f"whether this unit predates the branch is UNKNOWN; refusing "
                f"rather than granting on \"could not look\" — " + "; ".join(errs))
            continue

        existed = existed_at_merge_base(root, base, rel)
        if existed:
            continue  # grandfathered: counted above, never failed
        failures.append(f"{rel}: NEW unit with no pre-registered decision_rule — "
                         + "; ".join(errs))

    return failures, total, missing, base_state


# --------------------------------------------------------------------- self-test
_GOOD_RULE = """\
decision_rule:
  id: RULE-TEST-0001
  registered_at: '2026-09-25'
  registered_before_run: true
  statistic: the thing measured
  rule: IF x > 0 THEN do the thing ELSE do the other thing
"""

_BASE_UNIT = """\
id: {id}
title: t
status: queued
cadence: once
kind: deterministic
question: q
why_not_inferential: n
run:
  workflow: w.yml
lands:
  store: docs/research/x.jsonl
"""


def _write_unit(root: Path, unit_id: str, with_rule: bool) -> None:
    (root / "research" / "queue").mkdir(parents=True, exist_ok=True)
    text = _BASE_UNIT.format(id=unit_id)
    if with_rule:
        text += _GOOD_RULE
    (root / "research" / "queue" / f"{unit_id}.yaml").write_text(text, encoding="utf-8")


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                    capture_output=True, text=True)


def _self_test() -> int:
    failures = 0

    def check(label: str, cond: bool) -> None:
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            failures += 1

    # unit-level admissibility, no git involved.
    entry_good = {"decision_rule": {
        "id": "R1", "registered_at": "2026-09-25", "statistic": "s",
        "rule": "r", "registered_before_run": True}}
    check("positive control: a well-formed decision_rule is admissible",
          decision_rule_errors(entry_good) == [])

    entry_missing = {}
    check("negative: a missing decision_rule is caught",
          bool(decision_rule_errors(entry_missing)))

    entry_after = dict(entry_good, decision_rule=dict(
        entry_good["decision_rule"], registered_before_run=False))
    check("negative: registered_before_run=false is caught",
          bool(decision_rule_errors(entry_after)))

    entry_blank_rule = dict(entry_good, decision_rule=dict(
        entry_good["decision_rule"], rule="   "))
    check("negative: a blank `rule` string is caught",
          bool(decision_rule_errors(entry_blank_rule)))

    # git-level grading: a throwaway repo with a real merge-base.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _run_git(root, "init", "-q", "-b", "trunk")
        _run_git(root, "config", "user.email", "t@example.com")
        _run_git(root, "config", "user.name", "t")

        # trunk: one grandfathered unit with NO decision_rule.
        _write_unit(root, "RQ-OLD-001", with_rule=False)
        _run_git(root, "add", "-A")
        _run_git(root, "commit", "-q", "-m", "base")

        _run_git(root, "checkout", "-q", "-b", "feature")
        # feature branch: a NEW unit with no decision_rule (must fail),
        # and a NEW unit WITH one (must pass).
        _write_unit(root, "RQ-NEW-BAD", with_rule=False)
        _write_unit(root, "RQ-NEW-GOOD", with_rule=True)
        _run_git(root, "add", "-A")
        _run_git(root, "commit", "-q", "-m", "feature")

        failures_found, total, missing, state = scan(root, base="trunk")
        check("graded: base resolves", state == "graded")
        check("graded: total is 3 (1 old + 2 new)", total == 3)
        check("graded: missing count is 2 (old ungated + new-bad)", missing == 2)
        check("negative control: NEW unit with no decision_rule FAILS",
              any("RQ-NEW-BAD" in f and "NEW unit" in f for f in failures_found))
        check("positive control: NEW unit WITH decision_rule does NOT fail",
              not any("RQ-NEW-GOOD" in f for f in failures_found))
        check("grandfather: OLD unit with no decision_rule does NOT fail",
              not any("RQ-OLD-001" in f for f in failures_found))

        # no_base: nothing is failed, population still counted.
        failures_nobase, total_nobase, missing_nobase, state_nobase = scan(root, base=None)
        check("no_base: nothing fails even though 2 units lack a rule",
              failures_nobase == [])
        check("no_base: population count is still 2", missing_nobase == 2)
        check("no_base: state is reported as no_base", state_nobase == "no_base")

        # base_unresolved: an unreachable base fails EVERYTHING lacking a rule,
        # not just what we could prove is new — the refusing direction.
        failures_badbase, _, _, state_badbase = scan(root, base="does-not-exist")
        check("base_unresolved: state is reported, not silently graded",
              state_badbase == "base_unresolved")
        check("base_unresolved: refuses rather than grants (both bad units caught)",
              any("RQ-OLD-001" in f for f in failures_badbase)
              and any("RQ-NEW-BAD" in f for f in failures_badbase))

        # An unreadable YAML file is its own finding, new or not.
        (root / "research" / "queue" / "RQ-BROKEN.yaml").write_text(
            "id: [this is not\n  a mapping", encoding="utf-8")
        failures_broken, _, _, _ = scan(root, base="trunk")
        check("an unreadable/malformed unit is caught regardless of newness",
              any("RQ-BROKEN" in f for f in failures_broken))

    print(f"research-queue-decision-rule-guard --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=None,
                     help="e.g. origin/main — enables new-vs-grandfathered grading "
                          "via merge-base. Omit for a whole-tree report only.")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    # The verdict below is about the COMMITTED tree. Say so when that is
    # not the tree you edited. See
    # BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY
    import pathlib  # noqa: PLC0415 — local, so importing this module stays free
    import sys as _sys  # noqa: PLC0415
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()

    if args.self_test:
        return _self_test()

    failures, total, missing, base_state = scan(REPO, args.base)
    print(f"research-queue-decision-rule-guard: base_state={base_state} "
          f"units={total} missing_decision_rule={missing} failures={len(failures)}")
    for f in failures:
        print(f"::error::research-queue-decision-rule-guard: {f}")
    if failures:
        return 1
    print("research-queue-decision-rule-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
