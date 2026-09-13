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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _git_base  # noqa: E402  -- the ONE owner of base resolution

REPO = pathlib.Path(__file__).resolve().parents[2]

# ── the five states, never collapsed ────────────────────────────────────────
CLEAN = "clean"
RESERIALIZED = "reserialized"
UNREADABLE = "unreadable"          # we could not look — NOT a pass
UNTOUCHED = "untouched"
# ⚠️ `NEW_REGISTER` AND `UNREADABLE` ARE THE SAME BYTES AND OPPOSITE FACTS, and
# collapsing them is what this state was added to stop. `git show <ref>:<path>`
# exits non-zero both when the path is absent from an existing ref (a register
# this diff ADDS — there is nothing to have re-serialized, and the honest
# verdict is that the question does not arise) and when the ref itself cannot
# be resolved (*we could not look*). `_git_base.read_at` has always told them
# apart; this guard threw the distinction away one layer up, so a PR that
# legitimately adds a register FAILED — reproduced before the fix, `ok=False`
# with the new file graded `unreadable`.
# It is deliberately NOT folded into CLEAN: `clean` means we compared two
# versions and the comparison held, and there was no base version to compare.
NEW_REGISTER = "new_register"
ALL_STATES = (CLEAN, RESERIALIZED, UNREADABLE, UNTOUCHED, NEW_REGISTER)

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


def grade(base_text: str | None, head_text: str | None, *, path: str,
          base_state: str | None = None) -> dict:
    """Pure decision, so the policy is arguable in tests rather than on a PR.

    *base_state* is `_git_base.read_at`'s verdict for the base side. Omitting it
    means ``we were not told``, which grades UNREADABLE — the fail-safe
    direction, and byte-for-byte the behaviour before the state existed. Only an
    EXPLICIT `ABSENT_AT_BASE` buys the new-register verdict, so a caller cannot
    reach it by forgetting to say anything.
    """
    if base_state == _git_base.ABSENT_AT_BASE and head_text is not None:
        # `lost_lines` is 0 because there were no base lines to lose — a real
        # reading. `lost_fraction` is 0/0, which is UNDEFINED, so it is None and
        # never 0.0: a fraction of a file that does not exist is not "none of
        # it", and a consumer must be able to tell those apart.
        return {"path": path, "state": NEW_REGISTER, "lost_lines": 0,
                "budget": 0, "rows_touched": None, "lost_fraction": None,
                "why": "this register does not exist at the fork point, so this "
                       "diff ADDS it — there is no earlier serialization for it "
                       "to have destroyed. Reported rather than swallowed: a "
                       "register newly bound to the merge driver is a thing a "
                       "reviewer should see named."}
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


#: How many unmarked candidates to NAME before summarising the rest. The list is
#: 23 today; a cap exists so a future tree of 300 JSON files cannot turn a
#: scope note into the page nobody reads. ⚠️ THE CAP TRUNCATES THE LIST, NEVER
#: THE COUNT — a truncated census that also truncated its own total would be the
#: unasserted denominator one level up, which is the defect this renderer exists
#: to fix.
UNMARKED_NAME_CAP = 40


def render_unmarked(paths: list[str], cap: int = UNMARKED_NAME_CAP) -> list[str]:
    """The unmarked candidates, NAMED — pure, so the cap is arguable in a test.

    ⚠️ WHY NAMING THEM IS THE FIX AND COUNTING THEM WAS THE DEFECT. The old
    output said *"23 unmarked candidate(s) NOT checked (so `clean` is a scope
    result, not full coverage)"* — true, honest about its scope, and
    unactionable: a reader could not tell that three of the four review
    backlogs were among the 23. On 2026-09-13 a session hand-resolved a
    conflict in `performance-review-backlog.json`, silently dropped a row that
    had landed on `main` hours earlier, and every guard passed — including this
    one, which knew the file was unbound and said only a number. Filed by the
    MI-278 lane as the row about three of the four review backlogs never being
    bound to the row-aware merge driver, and this is its `next_step` (1).

    ⚠️ THAT ROW IS CITED BY DESCRIPTION AND NOT BY ID, DELIBERATELY. It is filed
    on PR #12148, which is `landing: hold` awaiting a human read, so its id does
    not yet resolve on `main` — and `check_backlog_refs` correctly refused this
    file for naming it. A doc saying "tracked by BL-X" where BL-X was never
    filed reads as tracked while being tracked by nobody. Put the id back once
    that PR lands; do not file a second copy of the row to satisfy the guard.

    This is the repo's unasserted-denominator class (sub-class C) applied to a
    coverage census: the number was correct and told nobody anything.
    """
    if not paths:
        return ["  (none — every register-shaped JSON under docs/claude is bound)"]
    shown, rest = paths[:cap], len(paths) - min(len(paths), cap)
    lines = [f"  {rel}" for rel in shown]
    if rest:
        lines.append(f"  ... and {rest} more (the COUNT above is complete; only "
                     "this list is capped)")
    return lines


def _base_read(ref: str, path: str,
               cwd: pathlib.Path = REPO) -> tuple[str, str | None]:
    """`read_at`'s verdict for the base side, forwarded WITHOUT collapsing it.

    ⚠️ THIS USED TO COLLAPSE TWO OF `read_at`'s THREE STATES, and the collapse
    was the defect. `_base_text` returned None for both `ABSENT_AT_BASE` (the
    register is NEW in this diff) and `UNREADABLE` (*we could not look*), which
    was exactly what the pre-#12138 `_show` did — deliberate at the time, so
    that the fork-point change altered WHICH REF is read and nothing else, and
    filed rather than silently folded in. That row is what this repays: a PR
    that legitimately adds a register graded `unreadable` and FAILED.

    Nothing is decided here. The two states are handed to `grade`, which is the
    one place the policy lives and the one place it is argued in tests.
    """
    return _git_base.read_at(ref, path, repo=cwd)


def check(base: str, *, root: pathlib.Path = REPO) -> dict:
    regs = registers_from_gitattributes(root)
    unmarked = unmarked_registers(root)
    # THE FORK POINT, NOT THE TIP. `git diff {base}...HEAD` is already
    # three-dot (merge-base semantics), so the FILE LIST was always scoped
    # correctly -- but the base CONTENT was read at `base` itself, i.e. the
    # tip. Mixed basis: files graded at the fork point, bytes compared against
    # a ref that has moved. Measured 2026-09-12 on a branch 25 register-commits
    # behind: an honest ONE-ROW append reported "421 base line(s) lost" against
    # 0 at the merge base, because rows OTHER SESSIONS added after the fork read
    # as this diff's losses. The verdict happened to survive (a 75% floor, and a
    # budget that inflates with the same contaminated input), so what was wrong
    # was the number a human reads and acts on.
    resolved_base, base_state = _git_base.resolve_base(base, repo=root)

    # ⚠️ AN UNRESOLVABLE BASE IS REFUSED, AND UNTIL NOW IT GRADED VACUOUSLY
    # CLEAN. MEASURED 2026-09-13: `check("origin/does-not-exist")` returned
    # ok=True with registers_in_diff=0 and EVERY register recorded UNTOUCHED —
    # a verdict byte-identical to a run that compared everything and found it
    # clean. Both `git diff` calls below fail, `out` is empty, `touched` is
    # empty, and the `rel not in touched` branch then marks each register
    # UNTOUCHED, which is a clean state.
    #
    # The information to refuse was already computed and thrown away: this line
    # bound `resolve_base`'s verdict to `_base_state`, underscore-prefixed and
    # unused, one line above the diff call — while this guard already owns an
    # UNREADABLE state whose whole purpose is *we could not look*.
    #
    # ⚠️ `registers_in_diff == 0` NOW MEANS TWO DIFFERENT THINGS AND THEY ARE
    # KEPT APART: on a resolvable base it is coverage (this diff touched no
    # register), and on an unresolvable one it is the absence of any reading.
    # Reporting the second as the first is what made this vacuous.
    #
    # ⚠️ THE TEST IS THE REF, NOT THE DIFF BEING EMPTY. An empty diff against a
    # REAL base is the ordinary case on nearly every PR in this repo and must
    # stay clean; refusing on an empty `touched` would fail all of them.
    if base and base_state == _git_base.TIP_UNRESOLVABLE and \
            not _git_base.ref_exists(base, repo=root):
        return {
            "ok": False,
            "rows": [],
            "registers_checked": len(regs),
            "registers_in_diff": None,   # None, never 0 — nothing was read
            "unmarked_candidates": len(unmarked),
            "unmarked_paths": unmarked,
            "reserialized": [],
            "unreadable": [],
            "new_registers": [],
            "base_state": _git_base.TIP_UNRESOLVABLE,
            "why": (f"the base ref {base!r} could not be resolved, so NO "
                    "register was compared. That is 'we could not look', and "
                    "it is not the same fact as 'this diff touched no "
                    "register' — which is what a count of 0 would say."),
        }

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
        base_state, base_text = _base_read(resolved_base, rel, root)
        rows.append(grade(base_text,
                          (root / rel).read_text(encoding="utf-8")
                          if (root / rel).is_file() else None,
                          path=rel, base_state=base_state))
    bad = [r for r in rows if r["state"] == RESERIALIZED]
    unread = [r for r in rows if r["state"] == UNREADABLE]
    fresh = [r for r in rows if r["state"] == NEW_REGISTER]
    return {
        "ok": not bad and not unread,
        "rows": rows,
        "registers_checked": len(regs),
        "registers_in_diff": sum(1 for r in rows if r["state"] != UNTOUCHED),
        "unmarked_candidates": len(unmarked),
        # ⚠️ THE LIST, not only its length. A census that counts what it did not
        # check, without saying WHAT, cannot be acted on — see render_unmarked.
        "unmarked_paths": unmarked,
        "reserialized": [r["path"] for r in bad],
        "unreadable": [r["path"] for r in unread],
        "new_registers": [r["path"] for r in fresh],
        "base_state": base_state,
        "why": None,
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

    # ── THE SPLIT: `new register` vs `we could not look` ────────────────────
    # These two are the SAME `git show` failure and OPPOSITE facts, and until
    # this state existed a PR that legitimately ADDED a register failed. Both
    # directions are asserted here, because fixing only the first would turn
    # every unreadable base into a pass — strictly worse than the bug.
    v = grade(None, base, path="p", base_state=_git_base.ABSENT_AT_BASE)
    if v["state"] != NEW_REGISTER:
        fails.append(f"a register ABSENT at the fork point graded {v['state']},"
                     f" not {NEW_REGISTER} — a PR that adds a register fails")
    say(f"  {'ok ' if v['state'] == NEW_REGISTER else 'FAIL'} a register absent "
        f"at the fork point is {NEW_REGISTER}, not a failure")

    # THE OTHER DIRECTION, and it is the one that must not be lost.
    v = grade(None, base, path="p", base_state=_git_base.UNREADABLE)
    if v["state"] != UNREADABLE:
        fails.append(f"an UNREADABLE base graded {v['state']} — the split "
                     "turned 'we could not look' into a pass")
    say(f"  {'ok ' if v['state'] == UNREADABLE else 'FAIL'} an unreadable base "
        f"is still {UNREADABLE}")

    # AND THE DEFAULT IS FAIL-SAFE: a caller that says nothing about the base
    # gets the strict verdict, so the permissive one cannot be reached by
    # omission — only by an explicit ABSENT_AT_BASE.
    v = grade(None, base, path="p")
    if v["state"] != UNREADABLE:
        fails.append(f"an unstated base_state graded {v['state']} — the "
                     "permissive verdict is reachable by forgetting to say")
    say(f"  {'ok ' if v['state'] == UNREADABLE else 'FAIL'} an UNSTATED base "
        f"state is {UNREADABLE} (the permissive verdict needs an explicit say)")

    # A head that cannot be read is unreadable EVEN WHEN the base is absent —
    # both sides gone is not a register being added, it is nothing to grade.
    v = grade(None, None, path="p", base_state=_git_base.ABSENT_AT_BASE)
    if v["state"] != UNREADABLE:
        fails.append(f"absent base AND unreadable head graded {v['state']}")
    say(f"  {'ok ' if v['state'] == UNREADABLE else 'FAIL'} absent base with an "
        f"unreadable head is {UNREADABLE}, not {NEW_REGISTER}")

    v = grade(base, base, path="p")
    if v["state"] != UNTOUCHED:
        fails.append(f"identical bytes graded {v['state']}")
    say(f"  {'ok ' if v['state'] == UNTOUCHED else 'FAIL'} identical bytes are "
        f"{UNTOUCHED}")

    reached = {grade(base, base, path="p")["state"],
               grade(base, honest, path="p")["state"],
               grade(base, reser, path="p")["state"],
               grade(None, base, path="p")["state"],
               grade(None, base, path="p",
                     base_state=_git_base.ABSENT_AT_BASE)["state"]}
    if reached != set(ALL_STATES):
        fails.append(f"not every state is reachable: {sorted(reached)}")
    say(f"  {'ok ' if reached == set(ALL_STATES) else 'FAIL'} all "
        f"{len(ALL_STATES)} states are reachable, so none is decorative")

    # ── THE CENSUS MUST NAME WHAT IT DID NOT CHECK ──────────────────────────
    # A count is true and unactionable. On 2026-09-13 this guard printed "23
    # unmarked candidate(s) NOT checked" while three of the four review backlogs
    # sat in that 23, and a hand-resolved conflict in one of them dropped a filed
    # row with every guard green.
    sample = ["docs/claude/performance-review-backlog.json",
              "docs/claude/ml-review-backlog.json"]
    out = "\n".join(render_unmarked(sample))
    named = all(x in out for x in sample)
    if not named:
        fails.append("render_unmarked did not NAME the candidates it was given — "
                     "a count is what this replaced")
    say(f"  {'ok ' if named else 'FAIL'} the unmarked candidates are NAMED, not "
        "counted")

    # THE CAP TRUNCATES THE LIST AND NEVER THE COUNT. A census that capped its
    # own total would be the unasserted denominator one level up — the exact
    # defect this renderer exists to fix, reintroduced by the fix.
    many = [f"docs/claude/f{i:03d}.json" for i in range(100)]
    capped = render_unmarked(many, cap=10)
    listed = [ln for ln in capped if ln.strip().startswith("docs/")]
    says_rest = any("90 more" in ln for ln in capped)
    cap_ok = len(listed) == 10 and says_rest
    if not cap_ok:
        fails.append(f"cap misbehaved: listed {len(listed)} line(s), "
                     f"says_rest={says_rest} — it must show exactly `cap` and "
                     "state how many it withheld")
    say(f"  {'ok ' if cap_ok else 'FAIL'} the cap truncates the LIST and says "
        "how many it withheld")

    # AND AN EMPTY LIST MUST SAY SO IN WORDS. Rendering nothing would make "every
    # register is bound" and "the probe stopped matching" the same output.
    empty = render_unmarked([])
    empty_ok = bool(empty) and "none" in empty[0]
    if not empty_ok:
        fails.append("an empty unmarked list rendered as nothing — silence "
                     "cannot distinguish full coverage from a broken probe")
    say(f"  {'ok ' if empty_ok else 'FAIL'} an empty list is stated in words, "
        "not rendered as silence")

    # THE LIST MUST REACH THE CALLER, not just exist. check() surfacing only a
    # length is what main() could print nothing useful from.
    v_chk = check("HEAD")
    has_paths = isinstance(v_chk.get("unmarked_paths"), list) and \
        len(v_chk["unmarked_paths"]) == v_chk["unmarked_candidates"]
    if not has_paths:
        fails.append("check() does not return unmarked_paths, or its length "
                     "disagrees with unmarked_candidates — the count and the "
                     "list must be the same population")
    say(f"  {'ok ' if has_paths else 'FAIL'} check() returns the LIST and it "
        f"agrees with the count ({v_chk.get('unmarked_candidates')})")

    # AND THE LIST MUST EXCLUDE WHAT IS BOUND, or it is not a list of gaps.
    bound = set(registers_from_gitattributes())
    overlap = bound & set(v_chk.get("unmarked_paths") or [])
    if overlap:
        fails.append(f"bound register(s) appear as unmarked: {sorted(overlap)}")
    say(f"  {'ok ' if not overlap else 'FAIL'} no bound register appears in the "
        "unmarked list")

    # ── AN UNRESOLVABLE BASE IS REFUSED, NOT GRADED CLEAN ───────────────────
    # Measured before the fix: check("origin/does-not-exist") returned ok=True
    # with registers_in_diff=0 and every register UNTOUCHED — indistinguishable
    # from a run that compared everything.
    v_bad = check("no-such-ref-anywhere-000-selftest")
    bad_ok = (v_bad["ok"] is False
              and v_bad["base_state"] == _git_base.TIP_UNRESOLVABLE
              and v_bad["registers_in_diff"] is None
              and not v_bad["rows"])
    if not bad_ok:
        fails.append(
            f"an unresolvable base graded ok={v_bad['ok']} "
            f"registers_in_diff={v_bad['registers_in_diff']!r} with "
            f"{len(v_bad['rows'])} row(s) — it must REFUSE, and its count must "
            "be None rather than 0, because 0 is a real reading")
    say(f"  {'ok ' if bad_ok else 'FAIL'} an unresolvable base is REFUSED and "
        "its count is None, never 0")

    # POSITIVE CONTROL. Without it a guard that refused every base would pass
    # the assertion above and be strictly worse than the defect.
    v_good = check("HEAD")
    good_ok = (v_good["base_state"] != _git_base.TIP_UNRESOLVABLE
               and isinstance(v_good["registers_in_diff"], int))
    if not good_ok:
        fails.append("a REAL base was refused, or reported no count — the "
                     "refusal is not keyed on the ref being unresolvable")
    say(f"  {'ok ' if good_ok else 'FAIL'} a REAL base still grades "
        f"(registers_in_diff={v_good['registers_in_diff']})")

    # ⚠️ THE DISCRIMINATOR, and it is the control that decides the
    # implementation. A defect keying the refusal on the DIFF being empty
    # behaves identically to the correct one on both inputs above — a bogus ref
    # yields an empty diff, and HEAD here happens to yield a non-empty one. An
    # empty diff against a REAL base is the ordinary case on most PRs in this
    # repo, and refusing it would fail all of them. So: a real ref whose diff
    # against HEAD is EMPTY must still be GRADED.
    import subprocess as _sp
    head_sha = _sp.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
    if not head_sha:
        fails.append("could not resolve HEAD, so the empty-diff control is "
                     "UNTESTED — not passed")
        say("  FAIL could not build the empty-diff control")
    else:
        v_self = check(head_sha)          # HEAD vs HEAD: a real ref, no diff
        self_ok = (v_self["base_state"] != _git_base.TIP_UNRESOLVABLE
                   and v_self["registers_in_diff"] == 0
                   and v_self["ok"] is True)
        if not self_ok:
            fails.append(
                "a REAL ref with an EMPTY diff was refused or graded not-ok "
                f"(base_state={v_self['base_state']!r} "
                f"registers_in_diff={v_self['registers_in_diff']!r}) — the "
                "refusal is keyed on the diff being empty, which would fail "
                "every PR that touches no register")
        say(f"  {'ok ' if self_ok else 'FAIL'} a REAL ref with an EMPTY diff is "
            "GRADED (count 0 = coverage), not refused")

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
    # THE REFUSAL IS REPORTED BEFORE THE COVERAGE LINE, NEVER THROUGH IT. That
    # line's shape ("N register(s) bound ... M in this diff") reads as a
    # measurement, and printing `None` into it would dress the absence of any
    # reading as a reading.
    if v.get("base_state") == _git_base.TIP_UNRESOLVABLE:
        print(f"::error::register-reserialization: {v['why']}")
        return 1
    print(f"register-reserialization: {v['registers_checked']} register(s) bound "
          f"to the merge driver, {v['registers_in_diff']} in this diff; "
          f"{v['unmarked_candidates']} unmarked candidate(s) NOT checked "
          "(so `clean` is a scope result, not full coverage)")
    # NAMED, not counted. The count alone was true and unactionable: it could
    # not tell a reader that three of the four review backlogs were in it.
    print("  not bound to the merge driver, so NOT graded by this run — this is "
          "the scope of `clean`, not a list of faults:")
    for line in render_unmarked(v["unmarked_paths"]):
        print(line)
    for r in v["rows"]:
        if r["state"] != UNTOUCHED:
            print(f"  {r['state']:14} {r['path']} — {r['why']}")
    if v["new_registers"]:
        # A NOTICE, not an error: adding a register is legitimate. It is said
        # out loud anyway, because the alternative to failing on it is not
        # silence — a file newly bound to the merge driver changes how every
        # other branch merges, and a reviewer should be told it happened.
        print("::notice::this PR ADDS register(s) that do not exist at the fork "
              "point, so there is no earlier serialization to compare against:")
        for p_ in v["new_registers"]:
            print(f"  - {p_}")
    if v["unreadable"]:
        print("::error::a register could not be READ, which is not the same as "
              "a register that is fine, and not the same as one this diff ADDS "
              "(that is `new_register`):")
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
