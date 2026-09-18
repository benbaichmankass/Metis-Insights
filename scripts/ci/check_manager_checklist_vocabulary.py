#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::manager-checklist-vocabulary-guard (--self-test, then the scan)
"""MI-257's TEETH — the checklist the operator reads may not carry two
competing status fields that disagree, or a value outside its own declared
vocabulary.

`GET /api/bot/work/checklist` (`src/web/api/routers/work.py`) renders
`docs/claude/work/MANAGER-CHECKLIST.json` through the ONE owner of the
`state`/`status` merge -- `src.runtime.manager_status.effective_state`. That
function has always been correct: it reports `disagree` when the two fields
contradict each other and flags a value the file's own `states` block does not
declare, rather than silently guessing. What was missing was anything that
made either condition COST something -- measured 2026-09-10 at 12 disagreeing
items and 38 off-vocabulary values, and again 2026-09-18 at 14 and 45, drifting
upward between the two measurements with nothing failing on it in between.

THIS GUARD ADDS NO NEW LOGIC. It imports `effective_state` and the file's own
declared vocabulary -- the same two things the live route already reads -- and
fails the moment either count is non-zero. A drifted row is therefore caught
on the PR that drifted it, not discovered 167 hours later on a due-list scan.

UNGATED (`when: None` in run_guards.py) on purpose: the checklist is a shared
register that many sessions write without touching this guard's own `when`
globs, and a row can drift on a commit that only edits `MANAGER-CHECKLIST.json`
-- the exact shape this guard exists to catch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime.manager_status import (  # noqa: E402
    _declared_vocabulary,
    effective_state,
)

CHECKLIST = Path("docs/claude/work/MANAGER-CHECKLIST.json")


def _grade(data: dict) -> tuple[list[str], list[str]]:
    """Return (disagree_ids, offvocab_ids) for one checklist payload."""
    vocab = _declared_vocabulary(data)
    items = data.get("items")
    disagree, offvocab = [], []
    if not isinstance(items, list):
        return disagree, offvocab
    for it in items:
        if not isinstance(it, dict):
            continue
        eff = effective_state(it, vocabulary=vocab)
        item_id = str(it.get("id", "<no id>"))
        if eff.disagrees:
            disagree.append(item_id)
        if eff.value is not None and not eff.in_declared_vocabulary:
            offvocab.append(item_id)
    return disagree, offvocab


def _self_test() -> int:
    """Plant a real disagreement and a real off-vocabulary value, prove the
    guard fires on each, then prove a clean file passes. A guard whose failure
    branch never runs is indistinguishable from one that always passes."""
    ok, checks = 0, []

    clean = {
        "states": {"done": "x", "queued": "y"},
        "items": [
            {"id": "A", "state": "done"},
            {"id": "B", "state": "queued", "status": "queued"},
        ],
    }
    d, o = _grade(clean)
    checks.append(("a clean file has no disagreement or off-vocab rows",
                    not d and not o))

    disagreeing = {
        "states": {"done": "x", "queued": "y"},
        "items": [{"id": "C", "state": "done", "status": "queued"}],
    }
    d, o = _grade(disagreeing)
    checks.append(("a planted state/status disagreement is caught",
                    d == ["C"]))

    offvocab = {
        "states": {"done": "x", "queued": "y"},
        "items": [{"id": "D", "state": "ready"}],
    }
    d, o = _grade(offvocab)
    checks.append(("a planted off-vocabulary value is caught",
                    o == ["D"]))

    widened = {
        "states": {"done": "x", "queued": "y", "ready": "next up"},
        "items": [{"id": "E", "state": "ready"}],
    }
    d, o = _grade(widened)
    checks.append(("a value declared in the file's own `states` block "
                    "is NOT flagged", not o))

    non_mapping_item = {
        "states": {"done": "x"},
        "items": [{"id": "F", "state": "done"}, "not-a-dict"],
    }
    d, o = _grade(non_mapping_item)
    checks.append(("a non-mapping row is skipped, not a crash",
                    d == [] and o == []))

    for label, passed in checks:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {label}")
        ok += int(passed)
    print(f"self-test: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    if not CHECKLIST.exists():
        print(f"manager-checklist-vocabulary guard: OK — {CHECKLIST} does not "
              "exist (nothing to grade).")
        return 0
    try:
        data = json.loads(CHECKLIST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"manager-checklist-vocabulary guard: FAIL — could not read/parse "
              f"{CHECKLIST}: {exc}")
        return 1
    if not isinstance(data, dict):
        print(f"manager-checklist-vocabulary guard: FAIL — {CHECKLIST} top "
              "level is not an object.")
        return 1

    disagree, offvocab = _grade(data)
    if not disagree and not offvocab:
        print("manager-checklist-vocabulary guard: OK — every item's "
              "effective state is unambiguous and every declared value is "
              "in the file's own vocabulary.")
        return 0

    if disagree:
        print(f"  {len(disagree)} item(s) with `state` disagreeing with "
              f"`status`: {disagree}")
    if offvocab:
        print(f"  {len(offvocab)} item(s) with a value outside the file's "
              f"declared `states` vocabulary: {offvocab}")
    print("  Resolve each against evidence (see MI-257 / "
          "docs/claude/work/MANAGER-CHECKLIST.json), or widen the `states` "
          "block if the value is a real, repeatedly-used distinction. Do "
          "not force a value to make this count go to zero.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
