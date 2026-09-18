#!/usr/bin/env python3
# wiring: manual-only - a one-off normalization a session runs by hand against
# MANAGER-CHECKLIST.json (idempotent: a re-run finds nothing left to fill), not
# a recurring pipeline step; not added to run_guards.py to avoid touching that
# heavily-contended shared file
"""FILL A MISSING `state` FROM AN UNAMBIGUOUS `status` — MI-237.

WHAT THIS IS NOT
-----------------
`src.runtime.manager_status.effective_state` already resolves an item that
carries ONLY `status` (no `state`) correctly and unambiguously — that is the
`STATUS_BASIS_STATUS_ONLY` basis, a legitimate, non-error resolution, and
`scripts/ci/check_manager_checklist_vocabulary.py` already verifies its
resolved value sits in the file's own declared vocabulary. So this is **not**
a correctness fix — a status-only row already renders and grades correctly
on `GET /api/bot/work/checklist` today. Measured 2026-09-18 (MI-316/MI-237):
0 disagreements, 0 off-vocabulary values, on all 294 items, including the 52
that carry `status` alone.

WHAT THIS IS
-------------
Schema uniformity, so a reader scanning `docs/claude/work/MANAGER-CHECKLIST.json`
does not have to know `effective_state`'s fallback rule to find an item's own
state — and so a FUTURE hand-edit that adds a `status` without a `state` is
one item smaller a population to reason about. Mirrors `status` into `state`
for exactly the items where doing so is a no-op on the RESOLVED value:

  * `state` is absent
  * `status` is present AND already inside the file's own declared vocabulary

Nothing is invented. An item whose `status` is itself off-vocabulary is left
untouched — mirroring an undeclared value into a second field would not fix
it, and `check_manager_checklist_vocabulary.py` already catches that case on
its own. (Measured 2026-09-18: 0 such items exist, so this script's `--dry-run`
output should show `skipped_offvocab: 0` on a clean run — a non-zero count
here means the population has changed since and needs re-reading, not blind
application.)

WHY A SCRIPT, NOT A HAND EDIT
--------------------------------
`docs/claude/work/MANAGER-CHECKLIST.json` round-trips byte-for-byte through
`json.dumps(doc, indent=2, ensure_ascii=False)` (verified before this file was
written) — this script relies on that so ONLY the touched items change in the
diff, the same discipline `scripts/ops/backlog_append.py` applies to the
review backlogs. A naive read-modify-write through a different serializer
would reformat the whole 1.1MB file and bury a 52-line change in noise.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST_PATH = REPO_ROOT / "docs" / "claude" / "work" / "MANAGER-CHECKLIST.json"


def _declared_vocabulary(doc: Dict[str, Any]) -> Tuple[str, ...]:
    states = doc.get("states")
    if isinstance(states, dict) and states:
        return tuple(str(k) for k in states)
    return ()


def plan(doc: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    # collapsed-state: status_only — this script's ENTIRE job is the
    # STATUS_BASIS_STATUS_ONLY case (state absent, status present); an item
    # already carrying `state` (agree/disagree/state_only) is skipped by the
    # `"state" in it` check above, unconditionally, with nothing left for this
    # function to decide about it, and an item with neither field
    # (undeclared) has no status to mirror. Reconciling agree/disagree pairs
    # is `effective_state`'s job (invoked by check_manager_checklist_vocabulary.py),
    # not this one-purpose normalization script's.
    """Pure. Returns (fillable_ids, skipped_offvocab_ids) — never mutates."""
    vocab = set(_declared_vocabulary(doc))
    items = doc.get("items")
    fillable: List[str] = []
    skipped: List[str] = []
    if not isinstance(items, list):
        return fillable, skipped
    for it in items:
        if not isinstance(it, dict) or "state" in it:
            continue
        status = it.get("status")
        item_id = str(it.get("id", "<no id>"))
        if isinstance(status, str) and status.strip() and status.strip() in vocab:
            fillable.append(item_id)
        elif isinstance(status, str) and status.strip():
            skipped.append(item_id)
    return fillable, skipped


def apply(doc: Dict[str, Any], fillable_ids: List[str]) -> int:
    """Mutates `doc` in place. Returns the number of items actually touched."""
    ids = set(fillable_ids)
    touched = 0
    for it in doc.get("items") or []:
        if isinstance(it, dict) and str(it.get("id")) in ids and "state" not in it:
            it["state"] = it["status"]
            touched += 1
    return touched


def _self_test() -> int:
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    doc = {
        "states": {"done": "x", "queued": "y"},
        "items": [
            {"id": "A", "status": "done"},          # fillable
            {"id": "B", "state": "done"},            # already has state, untouched
            {"id": "C", "state": "queued", "status": "queued"},  # already has state
            {"id": "D", "status": "mystery_value"},  # off-vocab, skipped
            {"id": "E"},                              # neither field, skipped (not fillable, not offvocab)
        ],
    }
    fillable, skipped = plan(doc)
    check("exactly the status-only, in-vocabulary item is fillable",
          fillable, ["A"])
    check("an off-vocabulary status-only item is skipped, not guessed",
          skipped, ["D"])

    touched = apply(doc, fillable)
    check("apply() touches exactly one item", touched, 1)
    check("the touched item now carries state == its own status",
          [it for it in doc["items"] if it["id"] == "A"][0]["state"], "done")
    check("an item with no status and no state is untouched by apply()",
          "state" in [it for it in doc["items"] if it["id"] == "E"][0], False)
    check("re-running plan() on the mutated doc finds nothing left to fill "
          "(idempotent)", plan(doc)[0], [])

    print("checklist-fill-missing-state self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change; write nothing")
    ap.add_argument("--path", default=str(CHECKLIST_PATH))
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    path = Path(a.path)
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    fillable, skipped = plan(doc)
    print(f"checklist-fill-missing-state: {len(fillable)} item(s) fillable "
          f"(status present, in-vocabulary, state absent).")
    print(f"checklist-fill-missing-state: {len(skipped)} item(s) skipped "
          f"(status present but NOT in the file's own vocabulary — see "
          f"check_manager_checklist_vocabulary.py, not this script).")
    if skipped:
        for sid in skipped:
            print(f"  skipped (offvocab): {sid}")
    if a.dry_run:
        print("checklist-fill-missing-state: --dry-run, nothing written.")
        return 0
    if not fillable:
        print("checklist-fill-missing-state: nothing to do.")
        return 0
    touched = apply(doc, fillable)
    dumped = json.dumps(doc, indent=2, ensure_ascii=False)
    path.write_text(dumped + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    print(f"checklist-fill-missing-state: wrote {touched} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
