#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (register-field-loss-guard)
"""Did this change silently DELETE a field from a shared register row?

THE DEFECT, AND WHY THE OBVIOUS PROOF IS BLIND TO IT
---------------------------------------------------
The shared registers (`SESSIONS.json`, the three backlogs, `OPEN-ITEMS.json`,
`MANAGER-CHECKLIST.json`) are edited concurrently by every lane, so they conflict
constantly, and the remedy everyone reaches for is a **union by id set**: prove
the ids on both sides all survive and the merge is clean.

**A union-by-id proof cannot see a merge that drops a FIELD.** The id is present
on both sides, so the sets match, so the proof passes — while a key that one side
added has quietly gone.

OBSERVED LIVE, 2026-09-11/12: MI-277's branch carried a pre-tick `SESSIONS.json`,
and resolving only the marked conflicts would have reverted two manager
observation write-backs **through lines git merges without complaint**. The
sibling `BL-20260911-A-SHARED-REGISTER-LOSES-ITS-TOP-LEVEL-KEYS-TO-A-LINE-LEVEL-MERGE-WHILE-ITS-ROWS-SURVIVE`
records the same mechanism one level up, on the TOP-LEVEL keys, with the named
consequence that `check_manager_scope` R6 reads `updated_at`.

WHY A GUARD RATHER THAN THE MERGE DRIVER THAT ALREADY EXISTS
------------------------------------------------------------
`scripts/ops/merge_json_register.py` is correct and is NOT what this duplicates.
It resolves the merge row-aware and **REFUSES** (`both sides EDITED <id>
differently`) on exactly this case — when it runs.

⚠️ **IT IS A CLIENT-SIDE MERGE DRIVER, AND ITS ABSENCE IS SILENT.** Its own
`.gitattributes` header says so: git runs it locally and only after
`scripts/ops/install_merge_driver.sh` has registered it in *that clone's* git
config, and GitHub's servers never run it at all. MEASURED in the container that
wrote this file: ``git config --get merge.jsonregister.driver`` returns nothing,
so every register merge here is a plain line-level merge — the failing case — and
nothing announced that.

So this validates the **ARTIFACT**, not the producer. `docs/CLAUDE-RULES-CANONICAL.md`
§ "What enforces this rule" puts that first for this exact reason: *"Validate the
artifact, not the script that produced it — then any bad producer is caught
regardless of author."* A driver-less clone, a hand-resolved conflict, a
`git checkout --theirs`, and a careless scripted rewrite all produce the same
artifact, and this sees all four.

WHAT IT CHECKS — three losses, graded separately
------------------------------------------------
``field_loss``      a row id present on BOTH sides has a key on `main` that is
                    GONE in this change. **FAILS.** This is the one a union-by-id
                    proof cannot see, and no legitimate case for it has been
                    found — a row's history is written by additive keys
                    (`observed_*`, `resolution`, `confirmed_at`).
``toplevel_loss``   a top-level key present on `main` is GONE. **FAILS.** Same
                    mechanism, wider blast radius.
``row_loss``        a row id present on `main` is GONE. **FAILS**, but it is
                    called out separately because unlike the other two it DOES
                    have legitimate cases — `OPEN-ITEMS.json` rows are pruned at
                    review — so it is the one most likely to need the override.

⚠️ A CHANGED VALUE IS NOT A LOSS AND IS NOT GRADED. Editing a field is ordinary
work. Only DISAPPEARANCE is the signature of a merge that dropped somebody's
write-back, and widening this to value changes would fire on every normal edit —
the desensitised alarm this repo has measured the cost of.

THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY
-------------------------------------------
A genuine removal declares itself in ``.github/register-removals/<slug>.json``:

    {"why": "...", "removals": [{"file": "...", "id": "...", "keys": ["..."]}]}

and the guard **checks the declaration against what actually happened**. A
declared removal that did NOT occur is itself a FAILURE, so the file cannot be a
blanket silencer — it is a statement about this diff that has to be true. That is
the `new-table-wiring-guard` lesson stated in `CLAUDE.md`: *a guard cheaper to
lie to than to satisfy is worse than no guard.*

⚠️ THREE READ STATES, NEVER COLLAPSED. ``compared`` (both sides parsed and
graded) · ``skipped`` (this change does not touch the register — a real
observation, and NOT a pass for it) · ``unreadable`` (**we could not look** — the
base blob or the working copy would not parse). `unreadable` FAILS: a register
that stopped parsing is a defect in its own right, and passing quietly on it
would make a broken guard indistinguishable from a clean diff.

Self-test:  python3 scripts/ci/check_register_field_loss.py --self-test
Live:       python3 scripts/ci/check_register_field_loss.py --base origin/main
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOVALS_DIR = REPO_ROOT / ".github" / "register-removals"

COMPARED, SKIPPED, UNREADABLE = "compared", "skipped", "unreadable"
FIELD_LOSS, TOPLEVEL_LOSS, ROW_LOSS = "field_loss", "toplevel_loss", "row_loss"

#: (path, array key, id field). Only these are graded — a register not listed
#: here is not silently covered, and adding one is a deliberate act.
#:
#: ⚠️ `docs/claude/session-board.json` is deliberately ABSENT. It has no row
#: array, and its `merge_slot` key is REWRITTEN by every armed branch by design
#: (`BL-20260910-R13-MAKES-EVERY-ARMED-SELF-LANDING-PR-WRITE-THE-SAME-MERGE-SLOT-LINE-SO-CONCURRENT-ARMED-PRS-CONFLICT-BY-CONSTRUCTION`),
#: so a top-level-key check there would fire on the normal case.
REGISTERS: Tuple[Tuple[str, str, str], ...] = (
    ("docs/claude/work/SESSIONS.json", "sessions", "session_id"),
    ("docs/claude/OPEN-ITEMS.json", "items", "id"),
    ("docs/claude/health-review-backlog.json", "items", "id"),
    ("docs/claude/performance-review-backlog.json", "items", "id"),
    ("docs/claude/ml-review-backlog.json", "items", "id"),
    ("docs/claude/research-review-backlog.json", "items", "id"),
)


def _git_show(ref: str, path: str, root: Path = REPO_ROOT) -> Optional[str]:
    """The blob at *ref*, or ``None`` — meaning ABSENT **or** unreadable.

    Deliberately conflated HERE and separated by the caller: a file that does not
    exist on the base is a new file, which this guard has nothing to say about.
    """
    try:
        res = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def _rows_by_id(doc: Any, array: str, id_field: str) -> Optional[Dict[str, Dict[str, Any]]]:
    if not isinstance(doc, dict):
        return None
    arr = doc.get(array)
    if not isinstance(arr, list):
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for row in arr:
        if isinstance(row, dict) and isinstance(row.get(id_field), str):
            out[row[id_field]] = row
    return out


def compare(base_doc: Any, head_doc: Any, array: str, id_field: str,
            path: str = "") -> Dict[str, Any]:
    """PURE. Two parsed registers in, one graded verdict out.

    Nothing here reads the filesystem, so the policy is arguable in tests rather
    than against a live merge.
    """
    base_rows = _rows_by_id(base_doc, array, id_field)
    head_rows = _rows_by_id(head_doc, array, id_field)
    if base_rows is None or head_rows is None:
        return {"path": path, "state": UNREADABLE, "findings": [],
                "why": ("one side did not parse as a register (expected a dict "
                        f"with a list under {array!r}). WE COULD NOT LOOK — this "
                        "is not a clean comparison and is not graded as one.")}

    findings: List[Dict[str, Any]] = []

    for key in base_doc:
        if key not in head_doc:
            findings.append({
                "kind": TOPLEVEL_LOSS, "path": path, "id": None, "key": key,
                "why": (f"top-level key {key!r} is on the base and GONE here. A "
                        f"line-level merge drops these while every ROW survives, "
                        f"which is why a union-by-id proof reads clean.")})

    for rid, brow in base_rows.items():
        hrow = head_rows.get(rid)
        if hrow is None:
            findings.append({
                "kind": ROW_LOSS, "path": path, "id": rid, "key": None,
                "why": f"row {rid!r} is on the base and GONE here."})
            continue
        for key in brow:
            if key not in hrow:
                findings.append({
                    "kind": FIELD_LOSS, "path": path, "id": rid, "key": key,
                    "why": (f"row {rid!r} keeps its id but has LOST the key "
                            f"{key!r}, which is on the base. The id sets match, "
                            f"so a union-by-id proof passes — this is the write-"
                            f"back somebody else made, reverted silently.")})
    return {"path": path, "state": COMPARED, "findings": findings,
            "why": (f"compared {len(base_rows)} base row(s) against "
                    f"{len(head_rows)} here; {len(findings)} loss(es).")}


def load_removals(root: Path = REPO_ROOT) -> List[Dict[str, Any]]:
    """Every declared removal across `.github/register-removals/*.json`.

    A malformed declaration is IGNORED rather than trusted, so a broken override
    silences nothing — the guard then reports the loss it was meant to excuse.
    """
    out: List[Dict[str, Any]] = []
    d = root / ".github" / "register-removals"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for r in (doc.get("removals") or []):
            if isinstance(r, dict) and isinstance(r.get("file"), str):
                out.append({"file": r["file"], "id": r.get("id"),
                            "keys": list(r.get("keys") or []),
                            "declared_in": f.name})
    return out


def _covers(decl: Dict[str, Any], finding: Dict[str, Any]) -> bool:
    if decl["file"] != finding["path"]:
        return False
    if finding["kind"] == TOPLEVEL_LOSS:
        return decl.get("id") is None and finding["key"] in decl["keys"]
    if decl.get("id") != finding["id"]:
        return False
    if finding["kind"] == ROW_LOSS:
        return not decl["keys"]          # the WHOLE row was declared gone
    return finding["key"] in decl["keys"]


def apply_removals(findings: Sequence[Dict[str, Any]],
                   removals: Sequence[Dict[str, Any]]
                   ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                              List[Dict[str, Any]]]:
    """(still failing, excused, PHANTOM declarations).

    ⚠️ THE THIRD RETURN IS WHAT MAKES THE OVERRIDE VERIFIED. A declaration that
    matches no actual loss is a claim about this diff that is FALSE, and it fails
    — so the file cannot be used as a blanket silencer, only as an accurate
    statement of what was deliberately removed.
    """
    excused, remaining = [], []
    used = set()
    for f in findings:
        hit = next((i for i, d in enumerate(removals) if _covers(d, f)), None)
        if hit is None:
            remaining.append(f)
        else:
            used.add(hit)
            excused.append(f)
    phantom = [d for i, d in enumerate(removals) if i not in used]
    return remaining, excused, phantom


def check(base: Optional[str], root: Path = REPO_ROOT,
          registers: Sequence[Tuple[str, str, str]] = REGISTERS) -> Dict[str, Any]:
    """Live. Reads the base blobs and the working copies."""
    if not base:
        return {"ok": True, "results": [],
                "summary": ("no --base given, so NOTHING was compared. This is "
                            "not a pass: run it with --base origin/main.")}
    # ⚠️ ASSERT THE DENOMINATOR BEFORE GRADING ANYTHING. Found by accident while
    # testing this guard: running a COPY of it from /tmp made `REPO_ROOT`
    # resolve to `/`, every register read as absent, and the verdict was a
    # confident `OK — no shared register lost a field` over a population of
    # ZERO. That is `CLAUDE-RULES-CANONICAL.md` § "Green is not evidence" in
    # this guard's own output: a verdict computed from zero inputs is VACUOUS,
    # not clean, and the two are indistinguishable from outside unless the
    # inputs are asserted. A wrong `--base`, a wrong cwd, or a rename of every
    # register all produce it.
    #
    # "No register CHANGED" is a real and common reading and stays OK. "No
    # register EXISTS" is not a reading at all.
    present = [p for p, _, _ in registers if (root / p).is_file()]
    if not present:
        return {"ok": False, "results": [], "findings": [], "excused": [],
                "phantom": [],
                "unreadable": [{"path": str(root), "state": UNREADABLE,
                                "findings": [],
                                "why": ("no declared register exists here, so "
                                        "nothing could be read")}],
                "summary": (f"NOTHING WAS CHECKED — none of the {len(registers)} "
                            f"declared registers exists under {root}. This is NOT "
                            f"'no register lost a field': it is a vacuous verdict "
                            f"over an empty population, and the usual cause is "
                            f"being run from the wrong root. Refusing rather than "
                            f"reporting a green that checked nothing.")}

    results = []
    for path, array, id_field in registers:
        base_text = _git_show(base, path, root)
        live = root / path
        if base_text is None or not live.is_file():
            results.append({"path": path, "state": SKIPPED, "findings": [],
                            "why": "absent on the base or here — nothing to compare."})
            continue
        try:
            base_doc = json.loads(base_text)
        except json.JSONDecodeError as exc:
            results.append({"path": path, "state": UNREADABLE, "findings": [],
                            "why": f"the BASE blob did not parse: {exc}"})
            continue
        try:
            head_doc = json.loads(live.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append({"path": path, "state": UNREADABLE, "findings": [],
                            "why": f"the working copy did not parse: {exc}"})
            continue
        if base_text == live.read_text(encoding="utf-8"):
            results.append({"path": path, "state": SKIPPED, "findings": [],
                            "why": "unchanged by this diff."})
            continue
        results.append(compare(base_doc, head_doc, array, id_field, path))

    return verdict_of(results, load_removals(root))


def verdict_of(results: Sequence[Dict[str, Any]],
               removals: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """PURE. The per-register comparisons plus the declarations, one verdict out.

    ⚠️ THIS IS A SEPARATE FUNCTION BECAUSE A MUTATION RUN SHOWED IT HAD TO BE.
    While it was inlined in `check`, the only way to reach the `ok` computation
    was the live filesystem path, so deleting `phantom` from the failure
    condition — which turns the verified override back into a blanket silencer —
    passed the entire suite. Three of the four terms below are load-bearing in a
    direction nothing else pins.
    """
    all_findings = [f for r in results for f in r["findings"]]
    remaining, excused, phantom = apply_removals(all_findings, removals)
    unreadable = [r for r in results if r["state"] == UNREADABLE]
    return {
        # ⚠️ ALL THREE TERMS. `phantom` is here because a declaration that names
        # no real removal is a FALSE statement about the diff; dropping it would
        # let one inaccurate override file sit in the tree forever, and the next
        # author would inherit a silencer rather than a record. `unreadable` is
        # here because a register that stopped parsing is a defect in its own
        # right, and passing quietly on it makes a broken guard look like a
        # clean diff.
        "ok": not remaining and not phantom and not unreadable,
        "results": list(results), "findings": remaining, "excused": excused,
        "phantom": phantom, "unreadable": unreadable,
        "summary": (f"{sum(1 for r in results if r['state'] == COMPARED)} register(s) "
                    f"compared, {sum(1 for r in results if r['state'] == SKIPPED)} "
                    f"untouched, {len(unreadable)} unreadable; {len(remaining)} "
                    f"loss(es), {len(excused)} declared, {len(phantom)} phantom "
                    f"declaration(s)."),
    }


def render(verdict: Dict[str, Any]) -> str:
    lines = [f"register-field-loss: {verdict['summary']}"]
    for r in verdict.get("unreadable", []):
        lines.append(f"::error::register-field-loss: {r['path']} — {r['why']}")
    for f in verdict.get("findings", []):
        lines.append(f"::error::register-field-loss: {f['kind'].upper()} in "
                     f"{f['path']} — {f['why']}")
    for d in verdict.get("phantom", []):
        lines.append(f"::error::register-field-loss: PHANTOM declaration in "
                     f"{d['declared_in']} — it says {d['file']} row "
                     f"{d.get('id')!r} keys {d['keys']} were removed, and NO "
                     f"such removal is in this diff. An override must be a TRUE "
                     f"statement about the change, or it is a silencer.")
    for f in verdict.get("excused", []):
        lines.append(f"  declared removal (allowed): {f['kind']} {f['path']} "
                     f"{f.get('id')} {f.get('key')}")
    if verdict["ok"]:
        lines.append("register-field-loss: OK — no shared register lost a field, "
                     "a row or a top-level key that the base had.")
    else:
        lines.append("Fix: resolve the register conflict BY FIELD, never by side "
                     "— take the base's version and re-apply only the keys this "
                     "branch owns. `scripts/ops/install_merge_driver.sh` makes "
                     "git do it for you, and it is NOT installed by default in a "
                     "fresh clone. A genuine removal goes in "
                     ".github/register-removals/<slug>.json and must be true.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SELF-TEST — every case asserts in BOTH directions: the planted loss FIRES and
# the clean input stays quiet. One direction proves a check runs, never that it
# discriminates.
# ---------------------------------------------------------------------------
def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []

    def ok(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    def doc(rows, **top):
        d = {"schema_version": 1, "updated_at": "t", "sessions": rows}
        d.update(top)
        return d

    ROW = {"session_id": "s1", "title": "lane", "confirmed_at": "c",
           "observed_2026_09_12T0152Z": "the manager's write-back"}

    # ── THE MI-277 CASE: the id survives, the observation does not ──────────
    base = doc([dict(ROW)])
    head = doc([{k: v for k, v in ROW.items() if k != "observed_2026_09_12T0152Z"}])
    v = compare(base, head, "sessions", "session_id", "R.json")
    ok("a row that keeps its id but LOSES a key is a field_loss",
       [f["kind"] for f in v["findings"]] == [FIELD_LOSS])
    ok("…and the finding names the row AND the key",
       v["findings"][0]["id"] == "s1"
       and v["findings"][0]["key"] == "observed_2026_09_12T0152Z")
    ok("…and it says why a union-by-id proof is blind to it",
       "union-by-id" in v["findings"][0]["why"])

    # THE CONTROL, and the reason the guard is not simply 'rows must not change'.
    edited = doc([dict(ROW, observed_2026_09_12T0152Z="a NEWER observation")])
    ok("a CHANGED value is ordinary work and is NOT a finding",
       compare(base, edited, "sessions", "session_id", "R.json")["findings"] == [])
    added = doc([dict(ROW, lane_note="new")])
    ok("an ADDED key is not a finding either",
       compare(base, added, "sessions", "session_id", "R.json")["findings"] == [])
    ok("an identical document is quiet",
       compare(base, doc([dict(ROW)]), "sessions", "session_id", "R.json")["findings"] == [])

    # ── the sibling defect: top-level keys ─────────────────────────────────
    v_top = compare(doc([dict(ROW)], extra="x"), doc([dict(ROW)]),
                    "sessions", "session_id", "R.json")
    ok("a lost TOP-LEVEL key is its own finding",
       [f["kind"] for f in v_top["findings"]] == [TOPLEVEL_LOSS])
    ok("…and it is NOT reported as a field_loss (different blast radius)",
       v_top["findings"][0]["id"] is None)

    # ── a lost row is graded, and graded SEPARATELY ────────────────────────
    v_row = compare(base, doc([]), "sessions", "session_id", "R.json")
    ok("a lost ROW is a row_loss, not a pile of field_losses",
       [f["kind"] for f in v_row["findings"]] == [ROW_LOSS])

    # ── three read states, never collapsed ─────────────────────────────────
    ok("a non-register document grades `unreadable`, never clean",
       compare({"sessions": "not a list"}, doc([]), "sessions", "session_id")["state"]
       == UNREADABLE)
    ok("…and `unreadable` reports NO findings rather than inventing them",
       compare({"x": 1}, doc([]), "sessions", "session_id")["findings"] == [])
    ok("a row with no id is skipped rather than crashing the grader",
       compare(doc([{"title": "no id"}]), doc([]), "sessions", "session_id")["findings"] == [])

    # ── THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY ────────────────────────
    finding = v["findings"]
    decl = [{"file": "R.json", "id": "s1",
             "keys": ["observed_2026_09_12T0152Z"], "declared_in": "x.json"}]
    rem, exc, ph = apply_removals(finding, decl)
    ok("a TRUE declaration excuses its own loss", not rem and len(exc) == 1 and not ph)

    wrong_key = [{"file": "R.json", "id": "s1", "keys": ["something_else"],
                  "declared_in": "x.json"}]
    rem2, exc2, ph2 = apply_removals(finding, wrong_key)
    ok("a declaration naming the WRONG key excuses nothing", len(rem2) == 1 and not exc2)
    ok("…AND is reported as a PHANTOM — a false statement about the diff, "
       "which is what stops the file being a blanket silencer", len(ph2) == 1)

    rem3, _, ph3 = apply_removals([], decl)
    ok("a declaration with NO matching loss at all is a phantom",
       not rem3 and len(ph3) == 1)

    wrong_file = [{"file": "OTHER.json", "id": "s1",
                   "keys": ["observed_2026_09_12T0152Z"], "declared_in": "x.json"}]
    ok("a declaration for a DIFFERENT file excuses nothing",
       len(apply_removals(finding, wrong_file)[0]) == 1)

    ok("a whole-row removal is declared with an EMPTY key list",
       not apply_removals(v_row["findings"],
                          [{"file": "R.json", "id": "s1", "keys": [],
                            "declared_in": "x.json"}])[0])
    ok("…and a keyed declaration does NOT excuse a whole-row loss",
       len(apply_removals(v_row["findings"],
                          [{"file": "R.json", "id": "s1", "keys": ["title"],
                            "declared_in": "x.json"}])[0]) == 1)

    # ── THE VERDICT ASSEMBLY, which a mutation run proved was unpinned ──────
    clean_result = [{"path": "R.json", "state": COMPARED, "findings": [], "why": ""}]
    loss_result = [{"path": "R.json", "state": COMPARED,
                    "findings": list(finding), "why": ""}]
    unread_result = [{"path": "R.json", "state": UNREADABLE, "findings": [], "why": ""}]
    ok("a clean comparison with no declarations is ok",
       verdict_of(clean_result, [])["ok"])
    ok("a real loss is NOT ok", not verdict_of(loss_result, [])["ok"])
    ok("a real loss WITH its true declaration IS ok",
       verdict_of(loss_result, decl)["ok"])
    ok("a PHANTOM declaration alone FAILS the run, even over a clean tree — "
       "without this the verified override degrades to a blanket silencer",
       not verdict_of(clean_result, decl)["ok"])
    ok("an UNREADABLE register FAILS rather than passing quietly",
       not verdict_of(unread_result, [])["ok"])

    # ── THE EMPTY POPULATION. Found by accident: a copy of this script run from
    #    /tmp resolved its root to `/`, every register read as absent, and the
    #    verdict was a confident OK over ZERO inputs.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        v_empty = check("origin/main", root=Path(td))
    ok("a root where NO register exists REFUSES rather than reporting a green "
       "that checked nothing", not v_empty["ok"])
    ok("…and says plainly that it is not 'no register lost a field'",
       "NOT" in v_empty["summary"] and "vacuous" in v_empty["summary"])
    ok("…while a root where the registers EXIST and none CHANGED is still a "
       "real, clean reading", check("origin/main")["ok"])

    ok("with no --base NOTHING is compared, and it says so rather than passing "
       "quietly", "not a pass" in check(None)["summary"])

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: "
              f"{len(fails)} failure(s) in the register-field-loss policy")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--base", default=None)
    args = ap.parse_args(argv)
    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1
    verdict = check(args.base)
    print(render(verdict))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
