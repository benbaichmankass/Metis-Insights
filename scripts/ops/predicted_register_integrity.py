#!/usr/bin/env python3
"""Would merging this branch land a DUPLICATE register id on `main`?

⚠️ **THIS IS NOT THE SAME QUESTION AS "WILL IT CONFLICT", AND THAT DISTINCTION
IS THE WHOLE UNIT.** `github_equivalent_merge.predict` answers the conflict
question correctly. The defect this module exists for is a merge that does
**not** conflict and whose RESULT is corrupt: GitHub's server-side squash cannot
run this repo's row-aware register driver (a custom driver runs a program;
GitHub supports only `union` and the built-ins), so `.gitattributes` falls back
to **union**, which keeps a row that is present on both sides at DIFFERENT
positions rather than recognising it as one row. `+1` authored row lands as
`+2`, with a duplicated id.

MEASURED END TO END on the real 2026-09-17 incident (PR #12374), so none of the
above is inferred:

  | tree                                          | rows | dupes | register-id-guard |
  | un-armed merge of fae00214f into 5979618dc    | 1598 |     0 | exit 0            |
  | un-armed merge of fae00214f into 07f1417e6    | 1600 |     1 | exit 1            |

The second is **byte-identical to `f67899add`**, the commit GitHub's squash
actually produced (9,118,392 bytes) — so an un-armed clone reproduces the
squash exactly, and this class IS predictable before the merge.

⚠️ **WHY CI DID NOT CATCH IT, WHICH IS A SECOND AND SEPARATE FACT.** Both trees
above are correct answers to different questions. `#12374`'s `guards` job
finished at 09:11:38Z against `5979618dc` and was RIGHT to be green; the squash
ran at 09:29:15Z against `07f1417e6`, which did not exist until 09:28:07Z. CI
graded a merge computed against a base **16.5 minutes older** than the one the
squash used. So a PR-time check cannot close this by itself — whatever base it
binds, the base may move before the merge — and the only moment the question
can be asked correctly is **immediately before merging**, which is where this
module is called from.

⚠️ **AND A THIRD FACT, WHICH RULES OUT THE OBVIOUS REMEDY.** The natural reading
of "add a check on main" is *`guards.yml` already runs on push*. Measured over
the complete population of all 29 PR-merge commits on `main` in the window
`guards.yml`'s 100-run push page covers: PRs merged under a user credential have
a push run **5 of 5**; PRs merged under `GITHUB_TOKEN` (which is what auto-merge
uses) have one **0 of 24**. Perfect separation, zero exceptions. The PRs that
land without any post-merge check are exactly the auto-merged ones — i.e. every
PR this repo's landing protocol arms. A post-merge push check is therefore inert
for precisely the population that causes this.

WHAT THIS MODULE IS NOT
-----------------------
It is **not** a second definition of "what is a register" or "what is a
collision". Both are imported from `scripts/ci/check_register_ids.py`, which is
their single owner. A second copy would drift silently and in the dangerous
direction — the way the writer and the guard drifted apart in
`BL-20260912-BACKLOG-APPEND-STAMPS-NO-CREATION-KEY-...` — so a test asserts the
identity of the imported objects rather than their equality.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The three verdicts, never collapsed. `could_not_look` is emphatically not a
#: pass: a register this could not read is the single failure mode most likely
#: to destroy rows, so reading it as clean is the worst available answer.
CLEAN = "clean"
WOULD_DUPLICATE = "would_duplicate"
COULD_NOT_LOOK = "could_not_look"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_owner(repo_root: Optional[Path] = None):
    """Import `check_register_ids` — the single owner of REGISTERS + collision.

    Loaded by path rather than by name because `scripts/ci` is not a package and
    an ops-side caller must not depend on `sys.path` happening to carry it.
    """
    root = Path(repo_root) if repo_root else _REPO_ROOT
    target = root / "scripts" / "ci" / "check_register_ids.py"
    spec = importlib.util.spec_from_file_location("_cri_for_prediction", target)
    if spec is None or spec.loader is None:      # pragma: no cover - defensive
        raise ImportError(f"could not load the register owner at {target}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inspect_tree(worktree: str, *, owner=None) -> Dict[str, Any]:
    """Grade the registers in an already-merged worktree.

    Shaped as a `github_equivalent_merge.predict(inspect=...)` hook: it takes the
    merged worktree's path and returns a verdict dict. It reads the tree it is
    given and nothing else — it does not know or care how that tree was made,
    which is what lets the same function grade a predicted merge, a real
    checkout, or a fixture.
    """
    result: Dict[str, Any] = {"state": COULD_NOT_LOOK, "findings": [],
                              "reason": None, "note": None,
                              "registers_read": 0, "registers_unreadable": []}
    root = Path(worktree)
    if not root.is_dir():
        result["reason"] = f"{worktree!r} is not a directory"
        return result
    try:
        cri = owner or _load_owner()
    except Exception as exc:
        result["reason"] = f"could not import the register owner: {exc!r}"
        return result

    findings: List[str] = []
    unreadable: List[str] = []
    read = 0
    for reg in cri.REGISTERS:
        path = root / reg.path
        if not path.exists():
            # A register absent from the merged tree is not this check's finding
            # — it is not a duplicate — but it is NOT silently a pass either.
            unreadable.append(f"{reg.path} (absent)")
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            unreadable.append(f"{reg.path} ({type(exc).__name__})")
            continue
        rows, array_state, _skipped = cri._rows(doc, reg.array)
        if array_state != "present":
            unreadable.append(f"{reg.path}::{reg.array} ({array_state})")
            continue
        read += 1
        findings.extend(cri.check_uniqueness(reg, rows))

    result["registers_read"] = read
    result["registers_unreadable"] = unreadable
    if read == 0:
        # ⚠️ Zero registers read is NOT "no duplicates". It is the denominator
        # being empty, which is the shape this repo files as a clean negative
        # worn over a population nobody read.
        result["reason"] = ("no register could be read in the merged tree, so "
                            "nothing was graded — this is not a clean result")
        return result
    if findings:
        result["state"] = WOULD_DUPLICATE
        result["findings"] = findings
        return result
    result["state"] = CLEAN
    result["note"] = (f"{read} register(s) read, no duplicate ids"
                      + (f"; {len(unreadable)} unreadable" if unreadable else ""))
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("worktree", help="a merged worktree to grade")
    ns = ap.parse_args(argv)
    r = inspect_tree(ns.worktree)
    if r["state"] == CLEAN:
        print(f"predicted-register-integrity: OK — {r['note']}")
        return 0
    if r["state"] == WOULD_DUPLICATE:
        print("predicted-register-integrity: WOULD DUPLICATE — merging this "
              "would land the following on main:")
        for f in r["findings"]:
            print(f"  - {f}")
        return 1
    print("predicted-register-integrity: COULD NOT LOOK — this is NOT a clean "
          "bill and NOT a finding.")
    print(f"  {r['reason']}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
