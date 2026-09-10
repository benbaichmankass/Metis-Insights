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


def _budget(mod, src: str, out: str) -> int:
    """The honest ceiling: the lines of the OLD slot plus the NEW one.

    ⚠️ NOT a fixed number. A claim replaces one slot with another, and the
    OUTGOING slot's size is not ours to choose — a session may hand-add keys to
    it (`found_stale`, `concurrent_sibling`, …), which is exactly what broke the
    previous fixed budget of 6. A bound that scales with what is actually being
    replaced still fails loudly on a whole-file reformat, because that touches
    lines belonging to neither slot.
    """
    s0, e0 = mod.find_top_level_value_span(src, "merge_slot")
    s1, e1 = mod.find_top_level_value_span(out, "merge_slot")
    return src[s0:e0].count("\n") + out[s1:e1].count("\n") + 2


def test_real_board_diff_is_small_enough_to_conflict_narrowly():
    """A whole-file reformat passes every correctness test above and still
    breaks the thing this change is for, so the SIZE is asserted separately.

    ⚠️ THIS TEST USED A POSITIONAL METRIC AND FAILED ON CORRECT BEHAVIOUR.
    It was ``sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))``,
    which compares line *i* of the old file with line *i* of the new one — so a
    claim with a DIFFERENT LINE COUNT from the one it replaces shifts every
    following line and reads as a whole-file rewrite. Measured on `main` at
    `4230ef10b` on 2026-09-10: a 6-key outgoing claim replaced by this script's
    4-key claim took the board 94 → 91 lines and the metric reported **87**
    against a budget of 6, while `test_real_board_only_the_slot_changes` proved
    the bytes on BOTH sides of the slot were preserved and the real diff was
    **11 lines**. It failed on `main`, so it failed `pytest-run` on every open
    PR — a guard reddening the repo for doing the right thing.
    """
    mod = _load()
    src = BOARD.read_text(encoding="utf-8")
    out = mod.splice(src, mod.build_claim("automation/probe-1-1", "run 1", "p",
                                          claimed_at="2026-09-09T06:00:00Z"))
    changed = mod._changed_lines(src, out)
    budget = _budget(mod, src, out)
    assert changed <= budget, (
        f"{changed} lines change against a budget of {budget} (the two slots' "
        f"own size). A whole-file re-serialisation turns every concurrent "
        f"session edit into a conflict.")


@pytest.mark.parametrize("extra_keys", [0, 1, 2, 5])
def test_an_outgoing_claim_with_EXTRA_KEYS_is_still_a_narrow_diff(extra_keys):
    """THE REGRESSION, pinned to a SYNTHETIC board rather than the live one.

    ⚠️ This cannot be asserted against `docs/claude/session-board.json`, and that
    is the whole reason it exists: the moment any session claims the slot, the
    live board holds a 4-key claim, the line-count delta is zero, and the bug
    becomes unreproducible from the fixture that caught it. A test whose
    evidence evaporates when someone does ordinary work is not a regression
    test.

    The defect: `claim_merge_slot.py` writes a fixed 4-key claim, but the
    OUTGOING slot may carry any number of hand-added keys — on 2026-09-10 `main`
    carried six (`found_stale`, `concurrent_sibling`). Replacing 6 keys with 4
    SHRINKS the file, and the old positional metric read that shift as a
    whole-file rewrite: 87 changed lines against a budget of 6, on a splice that
    was byte-for-byte correct outside the slot.
    """
    mod = _load()
    members = ['    "held_by": "someone"', '    "branch": "b"',
               '    "claimed_at": "2026-09-09T06:00:00Z"']
    members += [f'    "hand_added_{n}": "a session wrote prose here"'
                for n in range(extra_keys)]
    slot_lines = ['  "merge_slot": {'] + [
        m + ("," if i < len(members) - 1 else "") for i, m in enumerate(members)
    ]
    src = ('{\n' + "\n".join(slot_lines) + '\n  },\n  "active_sessions": [],\n'
           + "".join(f'  "filler_{i}": "line {i}",\n' for i in range(40))
           + '  "updated_at": "2026-09-09T00:00:00Z"\n}\n')
    json.loads(src)  # the fixture must be valid JSON or it proves nothing

    out = mod.splice(src, mod.build_claim("automation/probe-1-1", "run 1", "p",
                                          claimed_at="2026-09-09T06:00:00Z"))
    changed = mod._changed_lines(src, out)
    budget = _budget(mod, src, out)
    assert changed <= budget, (
        f"outgoing slot with {extra_keys} extra key(s): {changed} changed lines "
        f"against a budget of {budget}. This is the 2026-09-10 main-is-red "
        f"regression — the metric is counting a LINE SHIFT as a rewrite.")
    # And the real property still holds: nothing outside the slot moved.
    s0, e0 = mod.find_top_level_value_span(src, "merge_slot")
    assert out.startswith(src[:s0]) and out.endswith(src[e0:])


@pytest.mark.parametrize("indent,sort_keys", [(1, False), (4, False), (2, True)])
def test_a_whole_file_reserialisation_is_still_caught(indent, sort_keys):
    """PLANTED CONTROL — the teeth, kept after the metric was repaired.

    A metric loosened to stop failing on correct behaviour is worthless if it
    also stops failing on the behaviour it exists to prevent. Each of these is a
    genuine reformat of the real board and each must BREACH the budget.

    ⚠️ `indent=2, sort_keys=False` is deliberately NOT in this list, and the
    reason is worth stating rather than hiding: the board is already stored in
    exactly `json.dumps(..., indent=2)` form, so re-serialising it that way
    reproduces the file BYTE-FOR-BYTE outside the slot. That is not a reformat
    this guard misses — it is not a reformat at all, and
    `test_real_board_only_the_slot_changes` confirms the surrounding bytes are
    identical. Adding it as a control would assert a failure that ought not to
    happen.
    """
    mod = _load()
    src = BOARD.read_text(encoding="utf-8")
    good = mod.splice(src, mod.build_claim("automation/probe-1-1", "run 1", "p",
                                           claimed_at="2026-09-09T06:00:00Z"))
    reformatted = json.dumps(json.loads(good), indent=indent,
                             ensure_ascii=False, sort_keys=sort_keys) + "\n"
    changed = mod._changed_lines(src, reformatted)
    budget = _budget(mod, src, reformatted)
    assert changed > budget, (
        f"a whole-file re-serialisation (indent={indent}, sort_keys={sort_keys}) "
        f"changed only {changed} lines against a budget of {budget} — the size "
        f"guard has lost its teeth.")


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
