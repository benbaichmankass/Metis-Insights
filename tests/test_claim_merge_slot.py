"""`claim_merge_slot.py` must write the R13 claim WITHOUT reformatting the board.

WHY A WHOLE TEST FILE FOR FOUR LINES OF JSON
--------------------------------------------
`docs/claude/session-board.json` is the one file every session edits, and this
script now edits it on every automation run — dozens a day. Two properties have
to hold together, and each one alone is a trap:

1. the claim must actually satisfy R13 (or every automation PR fails its own
   required guard and can never merge — `MI-208`, measured 2026-09-09 as 46
   automation PRs open at once); and
2. NOTHING ELSE in the file may move (or every concurrent session edit becomes a
   whole-file conflict instead of a four-line one, and the automation lane
   becomes a conflict generator instead of a deadlock).

A test that only checked (1) would pass on a `json.dump` that rewrites all ~139
lines, which is the version of this fix that looks right and is worse than the
bug.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/ops/claim_merge_slot.py"
BOARD = REPO / "docs/claude/session-board.json"


def _load():
    spec = importlib.util.spec_from_file_location("claim_merge_slot", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_exists():
    # `commit-to-main` refuses to open a PR when this file is absent, so its
    # absence is a landing outage rather than a missing nicety.
    assert SCRIPT.is_file(), f"{SCRIPT} is missing — commit-to-main depends on it"


def test_self_test_passes():
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr


def test_claim_satisfies_the_three_fields_r13_grades():
    mod = _load()
    claim = mod.build_claim("automation/work-digest-1-1", "run 1", "why")
    # check_pr_landing.slot_claim_state: `branch` must equal the branch, and
    # `held_by` / `claimed_at` must be non-empty.
    assert claim["branch"] == "automation/work-digest-1-1"
    assert claim["held_by"].strip()
    assert claim["claimed_at"].strip()


@pytest.mark.parametrize("branch,held_by", [("", "x"), ("  ", "x"),
                                            ("b", ""), ("b", "   ")])
def test_an_unattributable_claim_is_refused_not_written(branch, held_by):
    mod = _load()
    with pytest.raises(mod.SpliceError):
        mod.build_claim(branch, held_by, "")


def test_real_board_only_the_slot_changes():
    """The property the whole splice exists for, asserted on the REAL file."""
    mod = _load()
    src = BOARD.read_text(encoding="utf-8")
    out = mod.splice(src, mod.build_claim("automation/probe-1-1", "run 1", "p",
                                          claimed_at="2026-09-09T06:00:00Z"))

    before, after = json.loads(src), json.loads(out)
    assert after["merge_slot"]["branch"] == "automation/probe-1-1"
    assert {k: v for k, v in before.items() if k != "merge_slot"} == \
           {k: v for k, v in after.items() if k != "merge_slot"}

    start, end = mod.find_top_level_value_span(src, "merge_slot")
    assert out.startswith(src[:start]), "bytes before the slot were rewritten"
    assert out.endswith(src[end:]), "bytes after the slot were rewritten"


def test_real_board_diff_is_small_enough_to_conflict_narrowly():
    """A whole-file reformat passes every correctness test above and still
    breaks the thing this change is for, so the SIZE is asserted separately."""
    mod = _load()
    src = BOARD.read_text(encoding="utf-8")
    out = mod.splice(src, mod.build_claim("automation/probe-1-1", "run 1", "p",
                                          claimed_at="2026-09-09T06:00:00Z"))
    a, b = src.split("\n"), out.split("\n")
    changed = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
    assert changed <= 6, (
        f"{changed} lines change; a claim is 4. A whole-file re-serialisation "
        f"turns every concurrent session edit into a conflict.")


def test_the_key_is_found_at_top_level_not_in_prose_or_a_nested_schema():
    """The real board mentions `merge_slot` in `_doc` prose AND defines a
    `schema.merge_slot` DESCRIPTION. A naive `text.find` hits those first."""
    mod = _load()
    src = BOARD.read_text(encoding="utf-8")
    start, _ = mod.find_top_level_value_span(src, "merge_slot")
    # The span must begin at a JSON object, not inside a string.
    assert src[start] == "{", src[start:start + 40]
    # And the nested description must survive untouched.
    out = mod.splice(src, mod.build_claim("automation/probe-1-1", "r", "p"))
    assert json.loads(out)["schema"]["merge_slot"] == \
           json.loads(src)["schema"]["merge_slot"]


def test_a_board_without_a_top_level_slot_is_refused():
    mod = _load()
    with pytest.raises(mod.SpliceError):
        mod.splice('{\n "active_sessions": []\n}\n',
                   mod.build_claim("b", "h", "p"))
