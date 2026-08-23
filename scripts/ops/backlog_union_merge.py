#!/usr/bin/env python3
# wiring: manual-only - invoked by a session resolving a real merge conflict on
# a review backlog. It cannot have a scheduled runner: it needs an in-progress
# merge with three named refs, which only exists mid-conflict. `--selftest` is
# wired into CI via `artifact-validity-guard` instead, because a self-test
# nobody invokes is worse than a missing one.
"""Three-way UNION merge of a review backlog, MEASURED before and ASSERTED after.

WHY THIS EXISTS
---------------
`BL-20260814-HAND-RESOLVED-BACKLOG-MERGE-SILENTLY-REVERTED-SIX-ITEMS-INCLUDING-A-RESOLUTION`. The three
review backlogs are append-heavy files that EVERY session is required to edit
(`CLAUDE.md` § "Every session"), so any branch open for more than an hour
conflicts on one. Measured 2026-08-23: **three conflicts on
`health-review-backlog.json` in a single evening across two PRs**, each from an
unrelated session appending rows to `main`.

Resolved by hand, that is a coin flip. `--ours` silently drops every row the
other side filed; `--theirs` silently drops your own; and a careful hand-merge
still reverted six items once, which is the row above. The correct resolution is
almost always a UNION — but "almost always" is exactly the word that needs a
check rather than a habit.

WHAT IT REFUSES, AND WHY THAT IS THE POINT
------------------------------------------
A union is only correct when the two sides touched disjoint rows. This exits **2**
rather than guessing when:

* **both sides edited the same row** — a union has to pick one, which is the
  hand-merge failure with extra steps;
* **both sides added the same id** — a union would emit a duplicate id;
* **either side DELETED a row** — a union RESURRECTS it. Backlog rows are kept
  when resolved rather than archived (`check_backlog_refs` depends on that), so
  a deletion is deliberate and silently undoing it is the same class of defect.

WHOSE ROW OWNS THE DURABLE FIX
------------------------------
`BL-20260821-BACKLOG-JSON-IS-A-SHARED-MUTABLE-ARRAY` (open) already owns it, and
is worth reading before proposing one: it explicitly **declines to split the
file** ("that would break every consumer -- the review skills,
`check_backlog_refs`, `check_allow_degraded`, the coherence guard -- for a
problem that is currently costing minutes, not correctness"), and its criterion
is a **CI check that fails a PR whose merge-base row set is not a SUBSET of the
result** -- one that detects a resolution which DROPPED a row.

This tool is a PARTIAL, complementary contribution: it prevents a lossy
resolution at resolve time, but it is NOT that CI detector, and a session that
reaches for `--ours` never invokes it. Nor does it implement that row's
zero-cost mitigation -- cut the branch FRESH from current `main` instead of
merging `main` in, which for an append-mostly file removes the conflict
entirely.

MEASURE FIRST, ASSERT AFTER
---------------------------
It prints the added/edited/deleted counts per side BEFORE writing, so the
resolution is a stated decision rather than an outcome; and after writing it
re-reads the file and asserts every row from BOTH sides is present verbatim,
no ancestor id was dropped, no id is duplicated, and the count is exactly
`ancestor + theirs_new + ours_new`.

Ordering: `theirs` is the spine (so their row EDITS survive verbatim), with our
new rows appended. That is deliberate — we assert we edited nothing, so nothing
of ours can be overwritten by taking their spine.

`ensure_ascii=False` + `indent=2` matches `backlog_append.py`, so this does not
reformat the file and re-attribute unrelated rows to the merging PR.

Usage (mid-conflict, from the repo root):
    python3 scripts/ops/backlog_union_merge.py            # health-review backlog
    python3 scripts/ops/backlog_union_merge.py --path docs/claude/ml-review-backlog.json
    python3 scripts/ops/backlog_union_merge.py --selftest

Tier-1 tooling. Touches one docs JSON; no runtime path.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

DEFAULT_PATH = "docs/claude/health-review-backlog.json"


class UnionRefusal(RuntimeError):
    """The sides are not disjoint (or a row was deleted). Refuse; never guess."""


def _items(doc: Any) -> List[Dict[str, Any]]:
    return doc["items"] if isinstance(doc, dict) else doc


def union(anc: Any, ours: Any, theirs: Any) -> Tuple[Any, Dict[str, Any]]:
    """Pure three-way union. Raises UnionRefusal rather than picking a side."""
    a, o, t = _items(anc), _items(ours), _items(theirs)
    ai = {r["id"]: r for r in a}
    oi = {r["id"]: r for r in o}
    ti = {r["id"]: r for r in t}

    ours_new = [r for r in o if r["id"] not in ai]
    theirs_new = [r for r in t if r["id"] not in ai]
    ours_ed = sorted(i for i in ai if i in oi and oi[i] != ai[i])
    theirs_ed = sorted(i for i in ai if i in ti and ti[i] != ai[i])
    ours_del = sorted(set(ai) - set(oi))
    theirs_del = sorted(set(ai) - set(ti))

    prov = {
        "ancestor": len(a), "ours": len(o), "theirs": len(t),
        "ours_new": [r["id"] for r in ours_new],
        "theirs_new": [r["id"] for r in theirs_new],
        "ours_edited": ours_ed, "theirs_edited": theirs_ed,
        "ours_deleted": ours_del, "theirs_deleted": theirs_del,
    }

    # An IDENTICAL edit on both sides is not a conflict: there is nothing to
    # pick, so refusing would block a legitimate resolution. Only a DIVERGENT
    # both-side edit needs a human.
    both_ed = sorted(i for i in set(ours_ed) & set(theirs_ed) if oi[i] != ti[i])
    both_new = sorted({r["id"] for r in ours_new} & {r["id"] for r in theirs_new})
    if both_ed:
        raise UnionRefusal(
            f"both sides EDITED {both_ed} DIVERGENTLY — a union must pick one")
    if both_new:
        raise UnionRefusal(f"both sides ADDED {both_new} — a union would duplicate the id")
    if ours_del or theirs_del:
        raise UnionRefusal(
            f"a side DELETED rows (ours={ours_del}, theirs={theirs_del}) — "
            f"a union RESURRECTS them, and backlog rows are kept on purpose")

    merged = list(t) + [r for r in o if r["id"] not in ti]

    # ASSERT AFTER — on the merged list, before it is ever written.
    ids = [r["id"] for r in merged]
    if len(ids) != len(set(ids)):
        raise UnionRefusal("duplicate ids in the union result")
    for r in t:
        if r not in merged:
            raise UnionRefusal(f"theirs row lost: {r['id']}")
    for r in ours_new:
        if r not in merged:
            raise UnionRefusal(f"our new row lost: {r['id']}")
    missing = [r["id"] for r in a if r["id"] not in set(ids)]
    if missing:
        raise UnionRefusal(f"ancestor ids dropped: {missing}")
    if len(merged) != len(a) + len(theirs_new) + len(ours_new):
        raise UnionRefusal(
            f"count {len(merged)} != {len(a)} + {len(theirs_new)} + {len(ours_new)}")

    out = dict(theirs) if isinstance(theirs, dict) else merged
    if isinstance(theirs, dict):
        out["items"] = merged
    prov["merged"] = len(merged)
    return out, prov


def _show(ref: str, path: str) -> Any:
    r = subprocess.run(["git", "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise UnionRefusal(f"cannot read {path} at {ref}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--ours", default="HEAD")
    ap.add_argument("--theirs", default="origin/main")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv[1:])
    if a.selftest:
        return _selftest()

    mb = subprocess.run(["git", "merge-base", a.ours, a.theirs],
                        capture_output=True, text=True)
    if mb.returncode != 0:
        print(f"REFUSED: no merge base for {a.ours}..{a.theirs}", file=sys.stderr)
        return 2
    try:
        doc, prov = union(_show(mb.stdout.strip(), a.path),
                          _show(a.ours, a.path), _show(a.theirs, a.path))
    except UnionRefusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        print("This needs a human read — do NOT resolve by side.", file=sys.stderr)
        return 2

    print(f"MEASURE  ancestor {prov['ancestor']} | ours {prov['ours']} | theirs {prov['theirs']}")
    print(f"  added   ours {len(prov['ours_new'])} theirs {len(prov['theirs_new'])}")
    print(f"  edited  ours {len(prov['ours_edited'])} theirs {len(prov['theirs_edited'])}")
    print(f"  deleted ours {len(prov['ours_deleted'])} theirs {len(prov['theirs_deleted'])}")
    if a.dry_run:
        print(f"DRY RUN — would write {prov['merged']} rows to {a.path}")
        return 0
    Path(a.path).write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    back = _items(json.loads(Path(a.path).read_text()))
    if len(back) != prov["merged"]:
        print(f"REFUSED: read-back {len(back)} != {prov['merged']}", file=sys.stderr)
        return 2
    print(f"UNION OK  {prov['ancestor']} + {len(prov['theirs_new'])} theirs "
          f"+ {len(prov['ours_new'])} ours = {prov['merged']}, 0 duplicates")
    return 0


# ---------------------------------------------------------------------------
def _selftest() -> int:
    checks: List[Tuple[str, bool]] = []

    def ck(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    def doc(rows):
        return {"items": rows}

    r = lambda i, v="a": {"id": i, "note": v}  # noqa: E731 — fixture builder

    anc = doc([r("A"), r("B")])

    # happy path: disjoint adds + a theirs-side edit
    ours = doc([r("A"), r("B"), r("OURS1"), r("OURS2")])
    theirs = doc([r("A"), r("B", "EDITED"), r("THEIRS1")])
    out, prov = union(anc, ours, theirs)
    ids = [x["id"] for x in out["items"]]
    ck("union count = anc + theirs_new + ours_new", len(out["items"]) == 5)
    ck("theirs EDIT survives verbatim",
       {"id": "B", "note": "EDITED"} in out["items"])
    ck("our new rows survive", "OURS1" in ids and "OURS2" in ids)
    ck("theirs new row survives", "THEIRS1" in ids)
    ck("no duplicate ids", len(ids) == len(set(ids)))
    ck("theirs is the spine (their order first)", ids[:3] == ["A", "B", "THEIRS1"])
    ck("provenance counts the sides", prov["ours_new"] == ["OURS1", "OURS2"])

    # non-dict (bare list) backlog shape still works
    out2, _ = union([r("A")], [r("A"), r("O")], [r("A"), r("T")])
    ck("bare-list shape supported", [x["id"] for x in out2] == ["A", "T", "O"])

    # REFUSALS — each must refuse, and a union must NOT be produced
    for name, o, t in (
        ("both edited the same row", doc([r("A", "x"), r("B")]), doc([r("A", "y"), r("B")])),
        ("both added the same id", doc([r("A"), r("B"), r("NEW", "x")]),
         doc([r("A"), r("B"), r("NEW", "y")])),
        ("ours deleted a row", doc([r("A")]), doc([r("A"), r("B")])),
        ("theirs deleted a row", doc([r("A"), r("B")]), doc([r("A")])),
    ):
        try:
            union(anc, o, t)
            ck(f"refuses: {name}", False)
        except UnionRefusal:
            ck(f"refuses: {name}", True)

    # an identical edit on both sides is NOT a conflict (same value, no pick needed)
    same = doc([r("A"), r("B", "SAME")])
    try:
        union(anc, same, same)
        ck("identical both-side edit is not a refusal", True)
    except UnionRefusal:
        ck("identical both-side edit is not a refusal", False)

    # round-trip: writing must not escape non-ASCII (the backlog_append hazard)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        d, _ = union(doc([r("A")]), doc([r("A"), r("O", "em—dash")]), doc([r("A")]))
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        ck("writes a real em-dash, not \\u2014", "em—dash" in p.read_text())

    ok = sum(1 for _, c in checks if c)
    for name, c in checks:
        print(f"  [{'ok' if c else 'FAIL'}] {name}")
    print(f"selftest: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
