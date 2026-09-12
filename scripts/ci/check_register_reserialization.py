#!/usr/bin/env python3
"""Did this PR RE-SERIALIZE a shared register, rather than edit rows in it?

WHY THIS EXISTS, AND WHY IT IS NOT THE DETECTOR THAT ALREADY FIRED.
On 2026-09-12T05:44:18Z a session merged `origin/main` into a live branch from a
clone where the register merge driver was NOT armed, hit conflicts in exactly
four register files, and hand-resolved them. The `OPEN-ITEMS.json` resolution
re-serialized the whole register into ``json.dumps(indent=2, ensure_ascii=False)``
— a file `merge_json_register.py`'s own docstring names as the one that is NOT
byte-reproducible, because it mixes a literal em-dash with an escaped one. The
PR's diff on that file went from 23 added / 0 removed to a whole-file rewrite.

STATE THE POPULATION: the resolution was semantically CLEAN — 84 items in, 84
out, zero rows lost, zero pre-existing values changed. So this is a PROVENANCE
defect, not a data-loss one, and this guard must not be sold as catching loss.
`check_register_ids.py` and `check_register_field_loss.py` own loss.

⚠️ THE ONLY THING THAT NOTICED WAS A CANARY IN AN UNRELATED TOOL, AND IT NAMED
THE WRONG CONDITION. What failed CI was `manager_preflight.py --self-test`:
``OPEN-ITEMS.json does NOT round-trip ... FAIL got=True want=False``. That
self-test hardcodes a live property of a DATA FILE as its expectation, so it
fires on churn only as a SIDE EFFECT and reads as a broken self-test rather than
as a corrupted register — this repo's UNPROVENANCED DIAGNOSTIC OUTPUT sub-class
A. Two consequences it cannot escape: a session that legitimately canonicalized
that file would see an identical failure and could "fix" it by editing the
self-test, destroying the only detector; and if the file ever becomes
round-trippable for any reason the canary dies SILENTLY and re-serialization
becomes undetectable. This guard names the condition instead, on the diff, and
depends on no data file's current formatting.

THE MEASURE IS NOT A TUNED CONSTANT. A row that is semantically IDENTICAL in
base and head but whose BYTES moved has been re-serialized, by definition; an
honest append or edit leaves every other row's bytes alone. So the budget for
how many base lines may legitimately disappear is derived from the rows that
actually changed — their own size — not from a factor chosen until the tree
passed. Measured on the motivating incident the two quantities are not close:
a whole-file rewrite loses tens of thousands of lines against a budget of a few
hundred, so the verdict does not sit near its threshold.

⚠️ WHAT THIS DOES NOT COVER, STATED RATHER THAN IMPLIED. The register set is
READ FROM `.gitattributes` (`merge=jsonregister`), so it cannot drift from the
driver it protects — but that also means a JSON register nobody marked there is
outside the denominator. The count of unmarked candidates is REPORTED on every
run, so "clean" can never be read as "every register was checked".

Run:  python3 scripts/ci/check_register_reserialization.py --base origin/main
      python3 scripts/ci/check_register_reserialization.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import subprocess
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[2]

# ── the four states, never collapsed ────────────────────────────────────────
CLEAN = "clean"
RESERIALIZED = "reserialized"
UNREADABLE = "unreadable"          # we could not look — NOT a pass
UNTOUCHED = "untouched"
ALL_STATES = (CLEAN, RESERIALIZED, UNREADABLE, UNTOUCHED)

ID_FIELDS = ("id", "session_id", "pr", "item_id", "key")

# Slack for lines that legitimately move without any row changing: the header
# scalars (`updated_at`, `generated_at`, ...), the trailing comma the previously
# last row gains when a row is appended, and a container line or two. Chosen as
# a small absolute allowance rather than a proportion — a proportion of a 7.8 MB
# file would be thousands of lines and would swallow the thing being measured.
HEADER_SLACK = 24
# How much bigger a row may serialize in the base than a canonical dump of it.
# Generous on purpose: undershooting here makes the guard cry wolf on an honest
# edit, and the real signal clears any plausible budget by two orders of
# magnitude, so buying safety costs nothing.
ROW_SIZE_FACTOR = 3
# ⚠️ THE BUDGET ALONE WAS NOT ENOUGH, AND HISTORY IS HOW THAT WAS ESTABLISHED
# RATHER THAN ARGUED. Graded over EVERY commit-vs-first-parent pair that changed
# one of the five driver-bound registers in the last 400 commits touching each
# (POPULATION: 1768 pairs), `lost > budget` alone returned 13 findings — and
# four of them were ordinary merges, at ratios of 1.02x to 2.79x.
#
# Re-serialization is a WHOLE-FILE property, so the second condition is the one
# that actually names it: what share of the base file's lines stopped existing.
# The measured separation is not close, and it is why a threshold can be chosen
# here without tuning it until something passed:
#
#     0.975 0.975 0.975 0.975 0.975 0.969 0.969 0.961 0.960   <- the nine real ones
#     ................ a gap of 0.58 with nothing in it ................
#     0.378                                                   <- a merge that removed rows
#     0.031 0.016 0.016                                       <- ordinary edits
#
# 0.75 sits in the empty middle: 2x above the highest non-member and 0.21 below
# the lowest member. It is a CHOSEN value with a measured basis, not a tuned one,
# and moving it anywhere in [0.40, 0.95] changes no verdict in that population.
LOST_FRACTION_FLOOR = 0.75


def _git(*args: str, cwd: pathlib.Path = REPO) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def registers_from_gitattributes(root: pathlib.Path = REPO) -> list[str]:
    """The register set, read from the driver's own registration.

    Hardcoding a second list here is how a guard and the thing it guards drift
    apart; `.gitattributes` is where the driver is actually bound.
    """
    ga = root / ".gitattributes"
    out: list[str] = []
    if not ga.is_file():
        return out
    for line in ga.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "merge=jsonregister" not in line:
            continue
        out.append(line.split()[0])
    return sorted(set(out))


def unmarked_registers(root: pathlib.Path = REPO) -> list[str]:
    """JSON files that LOOK like registers and are not bound to the driver.

    Reported so a clean verdict is never read as full coverage. Deliberately a
    COUNT of candidates, not an accusation: some of these are legitimately not
    merge-critical, and deciding that is a human call.
    """
    marked = set(registers_from_gitattributes(root))
    found = []
    for p in sorted((root / "docs" / "claude").rglob("*.json")):
        rel = p.relative_to(root).as_posix()
        if rel in marked:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict) and _row_array(doc)[0] is not None:
            found.append(rel)
    return found


def _row_array(doc: Any) -> tuple[str | None, list]:
    """The first top-level list of dicts, and its key. ``(None, [])`` if none."""
    if isinstance(doc, list) and doc and isinstance(doc[0], dict):
        return "<root>", doc
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return k, v
    return None, []


def _row_id(row: dict) -> str | None:
    for f in ID_FIELDS:
        v = row.get(f)
        if isinstance(v, (str, int)) and str(v).strip():
            return f"{f}={v}"
    return None


def _dump_lines(row: Any) -> int:
    return len(json.dumps(row, indent=2, ensure_ascii=False).splitlines())


def grade(base_text: str | None, head_text: str | None, *, path: str) -> dict:
    """Pure decision, so the policy is arguable in tests rather than on a PR."""
    if base_text is None or head_text is None:
        return {"path": path, "state": UNREADABLE, "lost_lines": None,
                "budget": None, "rows_touched": None, "lost_fraction": None,
                
                "why": "base or head could not be read — that is 'we did not "
                       "look', never 'the file is fine'"}
    if base_text == head_text:
        return {"path": path, "state": UNTOUCHED, "lost_lines": 0, "budget": 0,
                "rows_touched": 0, "lost_fraction": 0.0, "why": "identical bytes"}
    try:
        base_doc = json.loads(base_text)
        head_doc = json.loads(head_text)
    except Exception as e:  # noqa: BLE001
        return {"path": path, "state": UNREADABLE, "lost_lines": None,
                "budget": None, "rows_touched": None, "lost_fraction": None,
                
                "why": f"not parseable as JSON ({type(e).__name__}) — a "
                       "conflict-markered or truncated file lands here, and it "
                       "must not read as clean"}
    bk, brows = _row_array(base_doc)
    hk, hrows = _row_array(head_doc)
    if bk is None or hk is None:
        return {"path": path, "state": UNREADABLE, "lost_lines": None,
                "budget": None, "rows_touched": None, "lost_fraction": None,
                
                "why": "no top-level array of rows found on one side"}

    bidx = {i: r for r in brows if (i := _row_id(r))}
    hidx = {i: r for r in hrows if (i := _row_id(r))}
    added = set(hidx) - set(bidx)
    removed = set(bidx) - set(hidx)
    changed = {i for i in set(bidx) & set(hidx) if bidx[i] != hidx[i]}

    # Only rows that LEFT the base in some way can legitimately take base lines
    # with them. An ADDED row consumes no base line, so it is not in the budget.
    budget = HEADER_SLACK + ROW_SIZE_FACTOR * sum(
        _dump_lines(bidx[i]) for i in (removed | changed))

    base_lines = collections.Counter(base_text.splitlines())
    head_lines = collections.Counter(head_text.splitlines())
    lost = sum((base_lines - head_lines).values())

    rows_touched = len(added) + len(removed) + len(changed)
    base_n = max(len(base_text.splitlines()), 1)
    lost_fraction = lost / base_n
    if lost > budget and lost_fraction >= LOST_FRACTION_FLOOR:
        return {
            "path": path, "state": RESERIALIZED, "lost_lines": lost,
            "budget": budget, "rows_touched": rows_touched,
            "lost_fraction": round(lost_fraction, 4),
            "why": (f"{lost} of this file's {base_n} base line(s) — "
                    f"{lost_fraction:.1%} of it — are gone from the head, "
                    f"against a budget of {budget} derived from the "
                    f"{len(removed)} removed + {len(changed)} changed row(s). "
                    "A row that is semantically unchanged but whose bytes moved "
                    "has been RE-SERIALIZED, and an honest edit leaves the other "
                    "rows' bytes alone. Run scripts/ops/install_merge_driver.sh "
                    "and re-merge, or re-splice the base bytes by hand."),
        }
    return {"path": path, "state": CLEAN, "lost_lines": lost, "budget": budget,
            "rows_touched": rows_touched, "lost_fraction": round(lost_fraction, 4),
            "why": (f"{lost} base line(s) lost ({lost_fraction:.1%} of the file), "
                    f"budget {budget}, floor {LOST_FRACTION_FLOOR:.0%} — BOTH "
                    "conditions must hold, so this is clean")}


def _show(ref: str, path: str, cwd: pathlib.Path = REPO) -> str | None:
    rc, out = _git("show", f"{ref}:{path}", cwd=cwd)
    return out if rc == 0 else None


def check(base: str, *, root: pathlib.Path = REPO) -> dict:
    regs = registers_from_gitattributes(root)
    rc, out = _git("diff", "--name-only", f"{base}...HEAD", cwd=root)
    if rc != 0:
        rc, out = _git("diff", "--name-only", base, cwd=root)
    touched = set(out.split())
    rows = []
    for rel in regs:
        if rel not in touched:
            rows.append({"path": rel, "state": UNTOUCHED, "lost_lines": 0,
                         "budget": 0, "rows_touched": 0,
                         "lost_fraction": 0.0,
                         "why": "not changed by this diff"})
            continue
        rows.append(grade(_show(base, rel, root),
                          (root / rel).read_text(encoding="utf-8")
                          if (root / rel).is_file() else None,
                          path=rel))
    bad = [r for r in rows if r["state"] == RESERIALIZED]
    unread = [r for r in rows if r["state"] == UNREADABLE]
    return {
        "ok": not bad and not unread,
        "rows": rows,
        "registers_checked": len(regs),
        "registers_in_diff": sum(1 for r in rows if r["state"] != UNTOUCHED),
        "unmarked_candidates": len(unmarked_registers(root)),
        "reserialized": [r["path"] for r in bad],
        "unreadable": [r["path"] for r in unread],
    }


# ── self-test: a planted defect must FAIL, an honest edit must stay quiet ────

def _selftest(quiet: bool = False) -> tuple[bool, list[str]]:
    fails: list[str] = []

    def say(m: str) -> None:
        if not quiet:
            print(m)

    # A register whose serialization is deliberately NOT reproducible by a
    # naive dump — the motivating file's own property, reproduced here so the
    # controls are about the real shape rather than a tidy one.
    rows = [{"id": f"BL-{i:04d}", "title": f"row {i} — with an em-dash",
             "detail": "x" * 40} for i in range(40)]
    base = ('{\n  "schema_version": 1,\n  "updated_at": "2026-09-01",\n'
            '  "items": [\n'
            + ",\n".join(
                "    " + json.dumps(r, ensure_ascii=False) for r in rows)
            + "\n  ]\n}\n")

    # POSITIVE CONTROL FIRST. Without it a guard that fails everything passes
    # every planted defect below and is worse than no guard.
    appended = rows + [{"id": "BL-9999", "title": "new", "detail": "y"}]
    honest = base.replace(
        "\n  ]\n}\n",
        ",\n    " + json.dumps(appended[-1], ensure_ascii=False) + "\n  ]\n}\n")
    v = grade(base, honest, path="p")
    if v["state"] != CLEAN:
        fails.append(f"positive control (honest append) graded {v['state']}")
    say(f"  {'ok ' if v['state'] == CLEAN else 'FAIL'} honest append is clean "
        f"(lost={v['lost_lines']} budget={v['budget']})")

    # POSITIVE CONTROL 2: an honest EDIT of one row.
    edited = json.loads(base)
    edited["items"][3]["detail"] = "z" * 40
    honest_edit = base.replace(
        json.dumps(rows[3], ensure_ascii=False),
        json.dumps(edited["items"][3], ensure_ascii=False))
    v = grade(base, honest_edit, path="p")
    if v["state"] != CLEAN:
        fails.append(f"positive control (honest edit) graded {v['state']}")
    say(f"  {'ok ' if v['state'] == CLEAN else 'FAIL'} honest edit is clean "
        f"(lost={v['lost_lines']} budget={v['budget']})")

    # THE PLANT: canonicalize the whole file, changing NO row semantically.
    reser = json.dumps(json.loads(base), indent=2, ensure_ascii=False) + "\n"
    v = grade(base, reser, path="p")
    if v["state"] != RESERIALIZED:
        fails.append(f"a whole-file re-serialization graded {v['state']}")
    if v["rows_touched"] not in (0, None):
        fails.append("the re-serialization changed a row — the fixture is wrong,"
                     " this control must isolate FORMATTING")
    say(f"  {'ok ' if v['state'] == RESERIALIZED else 'FAIL'} pure "
        f"re-serialization is caught (lost={v['lost_lines']} "
        f"budget={v['budget']} rows_touched={v['rows_touched']})")

    # AND THE HARDER HALF: re-serialized AND a row legitimately appended, which
    # is what the real incident looked like. An edit-sized budget must not
    # excuse a whole-file rewrite.
    both = json.dumps(json.loads(honest), indent=2, ensure_ascii=False) + "\n"
    v = grade(base, both, path="p")
    if v["state"] != RESERIALIZED:
        fails.append(f"re-serialization WITH an honest append graded {v['state']}")
    say(f"  {'ok ' if v['state'] == RESERIALIZED else 'FAIL'} re-serialization "
        f"hiding behind a real append is caught")

    # POSITIVE CONTROL 3 — THE ONE THE MEASURED HISTORY FORCED, and it took two
    # attempts to write, which is worth recording. `lost > budget` ALONE called
    # four ordinary commits re-serialized (ratios 1.02x-2.79x, fractions
    # 0.016-0.378), so the fraction floor exists to exclude them. The FIRST
    # fixture written for it did not reach that branch at all — it came in UNDER
    # the budget, so it proved the budget and said nothing about the floor,
    # which is the same "a control that cannot reach the branch it targets"
    # shape this lane has now hit four times. This one reproduces `e5eeb42d2`:
    # rows are only ADDED (so the budget is HEADER_SLACK alone) while a merge
    # churns the bytes of rows nobody touched, in a file large enough that the
    # churn is a small fraction of it.
    big = [{"id": f"BL-{i:05d}", "title": f"row {i} — em-dash", "detail": "x" * 30}
           for i in range(400)]

    def _ser(rs: list, stamp: str) -> str:
        return ('{\n  "schema_version": 1,\n  "updated_at": "%s",\n  "items": [\n'
                % stamp
                + ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in rs)
                + "\n  ]\n}\n")

    base_big = _ser(big, "2026-09-01")
    churned = base_big.splitlines(keepends=True)
    # 30 untouched rows lose their exact bytes (a cosmetic space), which is what
    # a hand-resolved merge does to the lines around a conflict.
    for i in range(4, 34):
        churned[i] = churned[i].replace('", "', '",  "')
    head_big = "".join(churned).replace(
        "\n  ]\n}\n",
        ",\n    " + json.dumps({"id": "BL-99999", "title": "new", "detail": "y"},
                               ensure_ascii=False) + "\n  ]\n}\n")
    v = grade(base_big, head_big, path="p")
    reached_the_floor = (v["lost_lines"] or 0) > (v["budget"] or 0)
    if not reached_the_floor:
        fails.append("control 3 does not reach the fraction floor — it comes in "
                     "under the budget and therefore tests the wrong branch")
    if v["state"] != CLEAN:
        fails.append(f"a budget-busting but NOT whole-file diff graded {v['state']}")
    say(f"  {'ok ' if v['state'] == CLEAN and reached_the_floor else 'FAIL'} "
        f"over budget but not a rewrite is clean (lost={v['lost_lines']} > "
        f"budget={v['budget']}, frac={v['lost_fraction']} < "
        f"{LOST_FRACTION_FLOOR}) — and it REACHES that branch")

    # "we could not look" is its own state and must never read as clean.
    for label, b, h in (("base missing", None, base),
                        ("head missing", base, None),
                        ("conflict markers", base, "<<<<<<< HEAD\n" + base)):
        v = grade(b, h, path="p")
        if v["state"] != UNREADABLE:
            fails.append(f"{label} graded {v['state']}, not {UNREADABLE}")
        say(f"  {'ok ' if v['state'] == UNREADABLE else 'FAIL'} {label} is "
            f"{UNREADABLE}")

    v = grade(base, base, path="p")
    if v["state"] != UNTOUCHED:
        fails.append(f"identical bytes graded {v['state']}")
    say(f"  {'ok ' if v['state'] == UNTOUCHED else 'FAIL'} identical bytes are "
        f"{UNTOUCHED}")

    reached = {grade(base, base, path="p")["state"],
               grade(base, honest, path="p")["state"],
               grade(base, reser, path="p")["state"],
               grade(None, base, path="p")["state"]}
    if reached != set(ALL_STATES):
        fails.append(f"not every state is reachable: {sorted(reached)}")
    say(f"  {'ok ' if reached == set(ALL_STATES) else 'FAIL'} all four states "
        "are reachable, so none is decorative")

    # The register set must come from .gitattributes and must not be empty —
    # an empty list would make every run vacuously green.
    regs = registers_from_gitattributes()
    if len(regs) < 3:
        fails.append(f"only {len(regs)} register(s) resolved from .gitattributes")
    say(f"  {'ok ' if len(regs) >= 3 else 'FAIL'} {len(regs)} register(s) read "
        "from .gitattributes (a hardcoded list is how a guard drifts from the "
        "driver)")
    return not fails, fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        print("register-reserialization self-test")
        ok, fails = _selftest()
        if not ok:
            for f in fails:
                print(f"::error::{f}")
            return 1
        print("self-test OK")
        return 0

    v = check(a.base)
    print(f"register-reserialization: {v['registers_checked']} register(s) bound "
          f"to the merge driver, {v['registers_in_diff']} in this diff; "
          f"{v['unmarked_candidates']} unmarked candidate(s) NOT checked "
          "(so `clean` is a scope result, not full coverage)")
    for r in v["rows"]:
        if r["state"] != UNTOUCHED:
            print(f"  {r['state']:14} {r['path']} — {r['why']}")
    if v["unreadable"]:
        print("::error::a register could not be READ, which is not the same as "
              "a register that is fine:")
        for p in v["unreadable"]:
            print(f"  - {p}")
    if v["reserialized"]:
        print("::error::this PR RE-SERIALIZED a shared register. Nothing is "
              "necessarily lost — but the whole file is now attributed to this "
              "PR, every other branch touching it will conflict, and the row-"
              "level history is gone.")
        for p in v["reserialized"]:
            print(f"  - {p}")
        print("Fix: scripts/ops/install_merge_driver.sh, then redo the merge. "
              "The driver splices rows as their ORIGINAL BYTES.")
    if not v["ok"]:
        return 1
    print("register-reserialization: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
