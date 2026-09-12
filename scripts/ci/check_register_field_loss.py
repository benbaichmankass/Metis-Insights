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
import shutil
import subprocess
import tempfile
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
            path: str = "", base_state: str = "merge_base") -> Dict[str, Any]:
    """PURE. Two parsed registers in, one graded verdict out.

    Nothing here reads the filesystem, so the policy is arguable in tests rather
    than against a live merge.

    ``base_state`` changes only what the findings SAY, never which findings are
    produced. ⚠️ It is here because the FIELD_LOSS message used to assert a
    cause this code cannot establish — *"this is the write-back somebody else
    made, reverted silently"* — which is this repo's UNPROVENANCED DIAGNOSTIC
    OUTPUT sub-class A, and is wrong in two different ways. Under
    ``ref_tip_fallback`` nobody reverted anything: a branch that is merely
    BEHIND main shows every key main has gained since as "on the base and gone
    here". And under ``merge_base`` a DELIBERATE removal by this branch produces
    byte-identical evidence — which is precisely what
    ``.github/register-removals/`` exists to declare, so the message was calling
    the declared-removal path a silent revert. What the code measures is
    presence at the base and absence here; who did it and why is not measured,
    and is no longer claimed.
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
                "why": (f"row {rid!r} is on the base and GONE here."
                        + _BEHIND_BASE_CAVEAT.get(base_state, ""))})
            continue
        for key in brow:
            if key not in hrow:
                findings.append({
                    "kind": FIELD_LOSS, "path": path, "id": rid, "key": key,
                    "why": (f"row {rid!r} keeps its id but has LOST the key "
                            f"{key!r}, which is on the base. The id sets match, "
                            f"so a union-by-id proof passes. MEASURED: present "
                            f"on the base, absent here. NOT MEASURED: who "
                            f"removed it or why — a line-level merge reverting "
                            f"somebody's write-back and a deliberate removal "
                            f"leave identical evidence, and the second is what "
                            f".github/register-removals/ is for."
                            + _BEHIND_BASE_CAVEAT.get(base_state, ""))})
    return {"path": path, "state": COMPARED, "findings": findings,
            "why": (f"compared {len(base_rows)} base row(s) against "
                    f"{len(head_rows)} here; {len(findings)} loss(es).")}


IN_DIFF, INHERITED, UNSCOPED = "in_diff", "inherited", "unscoped"

MERGE_BASE, REF_TIP = "merge_base", "ref_tip_fallback"

#: What a finding must ALSO say when the merge-base could not be computed.
#: ⚠️ The verdict SUMMARY already carries this caveat; a finding does not,
#: and the findings are what a reader acts on — `render` prints one
#: `::error::` line per finding, and a session reading a specific row id does
#: not necessarily read the summary line above it. Keyed by base_state so the
#: two can never disagree about when it applies.
_BEHIND_BASE_CAVEAT = {
    REF_TIP: (" ⚠️ NO MERGE-BASE COULD BE COMPUTED, so this is graded against "
              "the ref TIP: a branch that is merely BEHIND main produces this "
              "finding having removed nothing. Run `git merge origin/main` and "
              "re-check before treating it as a loss."),
}


def resolve_base(base: Optional[str], root: Path = REPO_ROOT) -> Tuple[str, Optional[str]]:
    """The commit this change should be compared AGAINST, and how we got it.

    ⚠️ `origin/main`'s TIP IS THE WRONG BASE AND THE ERROR IS NOT SUBTLE.
    A branch that forked an hour ago is missing every row `main` has gained
    since, and a tip comparison reports each one as a ROW LOSS THIS BRANCH
    CAUSED. MEASURED 2026-09-12 on #11897: the guard named a scalp-family row in
    `docs/claude/performance-review-backlog.json` as "on the base and GONE
    here", and that row was introduced by c553c62c at 10:49:56Z — four minutes
    AFTER the branch's last merge. The branch never had it, so it could not have
    dropped it. (The id is deliberately NOT quoted: `artifact-validity-guard`
    resolves every `BL-` token it finds, and a wrapped or elided one reads as a
    reference to a row that was never filed.) Every branch behind `main` produces this, which is
    nearly all of them, and the finding names a row the author has never seen.

    The merge-base is what "did THIS change remove it" actually means. On a
    `pull_request` checkout of the merge ref the two coincide, so CI behaviour
    is unchanged where it was already right; what moves is every head checkout.

    Two states, never collapsed. ``merge_base`` — git answered. ``ref_tip_fallback``
    — **we could not compute one** (shallow clone, unrelated histories, git
    unusable), so the tip is used and the caller SAYS so, because a finding
    graded against a tip may belong to somebody else's merge.
    """
    if not base:
        return REF_TIP, base
    try:
        r = subprocess.run(["git", "-C", str(root), "merge-base", base, "HEAD"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return REF_TIP, base
    sha = r.stdout.strip()
    if r.returncode != 0 or not sha:
        return REF_TIP, base
    return MERGE_BASE, sha



def declaration_scope(base: Optional[str],
                      root: Path = REPO_ROOT) -> Tuple[str, Optional[set]]:
    """Which declaration files THIS diff carries. Three states, never collapsed.

    ``in_diff`` — git answered, and the returned set is the declarations this
    change adds or modifies. ``inherited`` files (on the base, untouched here)
    are deliberately NOT in it.

    ``unscoped`` — **we could not look**. git did not run, or `base` is not
    resolvable. The caller then evaluates EVERY declaration, which is the old
    behaviour, and SAYS so in the summary: under-evaluating a declaration turns
    it into a silencer, and that is the failure this guard exists to prevent.
    Reporting an inherited declaration as a phantom is the milder error, so that
    is the direction the unreadable case resolves toward.

    ⚠️ WHY THIS EXISTS AT ALL. A removal declaration is a statement about ONE
    diff, and it lives in the tree FOREVER. MEASURED 2026-09-12:
    `.github/register-removals/mi279-u5-clear-digest-carrier.json` landed on
    main at 10:54:46Z with #11915 — true about #11915's own diff — and from that
    moment `load_removals`' unconditional glob read it against every LATER PR,
    where the removal it names is absent, and graded it a PHANTOM. That is a
    hard failure, so one merged declaration red-lined every subsequent PR in the
    repo (reproduced on #11897 and #11940, in two different lanes).

    ⚠️ THE ANTI-SILENCER PROPERTY IS UNTOUCHED, which is the whole point of
    scoping rather than of dropping `phantom` from the verdict. A declaration
    still has to be a TRUE statement about the diff that CARRIES it; what it can
    no longer do is make a statement about somebody else's diff.
    """
    if not base:
        return UNSCOPED, None
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", base, "--",
             ".github/register-removals"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return UNSCOPED, None
    if res.returncode != 0:
        return UNSCOPED, None
    names = {Path(line).name for line in res.stdout.split() if line.strip()}
    # ⚠️ `git diff` DOES NOT SEE AN UNTRACKED FILE, and a freshly written
    #    declaration is untracked until it is staged. Leaving it out would make
    #    it inherited-by-omission: the author's own declaration would silently
    #    stop excusing their own removal, and they would be told they had a
    #    loss they had already declared. CI always sees a committed tree, so
    #    this only bites locally — which is exactly where somebody is writing
    #    the declaration and about to conclude the override does not work.
    try:
        un = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
             "--", ".github/register-removals"],
            capture_output=True, text=True, timeout=60)
        if un.returncode == 0:
            names |= {Path(x).name for x in un.stdout.split() if x.strip()}
    except (OSError, subprocess.SubprocessError):
        return UNSCOPED, None
    return IN_DIFF, names


def load_removals(root: Path = REPO_ROOT,
                  only: Optional[set] = None) -> List[Dict[str, Any]]:
    """Every declared removal across `.github/register-removals/*.json`.

    A malformed declaration is IGNORED rather than trusted, so a broken override
    silences nothing — the guard then reports the loss it was meant to excuse.

    ``only`` restricts the read to the declaration FILE NAMES this diff carries
    (see `declaration_scope`). ``None`` means unscoped — read everything.
    """
    out: List[Dict[str, Any]] = []
    d = root / ".github" / "register-removals"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        if only is not None and f.name not in only:
            continue
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
    base_state, base = resolve_base(base, root)
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
        results.append(compare(base_doc, head_doc, array, id_field, path,
                               base_state=base_state))

    scope_state, scoped = declaration_scope(base, root)
    all_decl = {f.name for f in (root / ".github" / "register-removals").glob("*.json")} \
        if (root / ".github" / "register-removals").is_dir() else set()
    return verdict_of(results, load_removals(root, scoped),
                      base_state=base_state,
                      scope_state=scope_state,
                      declarations_read=len(all_decl if scoped is None else (scoped & all_decl)),
                      declarations_inherited=(
                          0 if scoped is None else len(all_decl - scoped)))


def verdict_of(results: Sequence[Dict[str, Any]],
               removals: Sequence[Dict[str, Any]],
               base_state: str = MERGE_BASE,
               scope_state: str = IN_DIFF,
               declarations_read: int = 0,
               declarations_inherited: int = 0) -> Dict[str, Any]:
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
        "base_state": base_state,
        "scope_state": scope_state,
        "declarations_read": declarations_read,
        "declarations_inherited": declarations_inherited,
        # ⚠️ The declaration counts ride in the summary so that `0 phantom`
        # cannot be read as "every declaration checked out". Zero phantoms over
        # zero declarations read is a different fact from zero phantoms over
        # three, and `unscoped` is a third fact again — we did not scope, so
        # inherited declarations ARE being graded against this diff.
        "summary": (f"{sum(1 for r in results if r['state'] == COMPARED)} register(s) "
                    f"compared, {sum(1 for r in results if r['state'] == SKIPPED)} "
                    f"untouched, {len(unreadable)} unreadable; {len(remaining)} "
                    f"loss(es), {len(excused)} declared, {len(phantom)} phantom "
                    f"declaration(s); declarations {scope_state}: "
                    f"{declarations_read} read, {declarations_inherited} inherited"
                    + (" (SCOPE UNKNOWN — inherited declarations are being graded "
                       "against this diff, so a phantom here may not be yours)"
                       if scope_state == UNSCOPED else "")
                    + (" (NO MERGE-BASE — graded against the ref TIP, so a loss "
                       "here may be a row the base gained after this branch "
                       "forked rather than one this change removed)"
                       if base_state == REF_TIP else "") + "."),
    }


#: The first words of the FIX line when there is no merge-base. Module scope so
#: the self-test asserts the string `render` actually emits rather than a copy
#: of it — a control that asserts its own literal cannot catch a reworded fix.
FIX_LEAD_BEHIND_BASE = "Fix: FIRST run `git merge origin/main`"


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
    elif verdict.get("base_state") == REF_TIP:
        # ⚠️ THE REMEDY IS DIFFERENT HERE AND LEADING WITH THE WRONG ONE COSTS
        # A SESSION ITS SEARCH. Without a merge-base these findings are most
        # often "you have not merged main yet", for which resolving a register
        # conflict by field is not the fix and there may be no conflict at all.
        lines.append(FIX_LEAD_BEHIND_BASE + " and re-check — no "
                     "merge-base could be computed, so every row the base "
                     "gained after this branch forked reads as a loss here. If "
                     "a finding SURVIVES that merge it is real: resolve the "
                     "register conflict BY FIELD, never by side, and a genuine "
                     "removal goes in .github/register-removals/<slug>.json.")
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
def _run_git(root: Path, *args: str) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _commit_all(root: Path, message: str) -> bool:
    return (_run_git(root, "add", "-A")
            and _run_git(root, "commit", "-q", "-m", message))


def _tag(root: Path, name: str) -> bool:
    return _run_git(root, "tag", "-f", name)


def _init_repo(root: Path) -> bool:
    """A throwaway repo so the self-test can compare against a REAL base ref.

    Returns False when git is unusable, so the caller can SKIP LOUDLY rather
    than report either a policy failure or a pass it never measured.
    """
    return (_run_git(root, "init", "-q")
            and _run_git(root, "config", "user.email", "selftest@example.invalid")
            and _run_git(root, "config", "user.name", "selftest")
            and _commit_all(root, "registers")
            and _tag(root, "selftest-base"))


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
    with tempfile.TemporaryDirectory() as td:
        v_empty = check("origin/main", root=Path(td))
    ok("a root where NO register exists REFUSES rather than reporting a green "
       "that checked nothing", not v_empty["ok"])
    ok("…and says plainly that it is not 'no register lost a field'",
       "NOT" in v_empty["summary"] and "vacuous" in v_empty["summary"])
    # ── "registers exist, none changed" — over a HERMETIC tree. ────────────
    #
    # ⚠️ THIS USED TO READ `check("origin/main")["ok"]`, AND THAT WAS A DEFECT
    #    IN THE SELF-TEST ITSELF, not a stricter check. It asserted a property
    #    of WHATEVER THE BRANCH HAPPENED TO CONTAIN, so any branch carrying a
    #    real finding reported `the register-field-loss POLICY is broken` — and
    #    an author reading that reasonably concludes the guard is somebody
    #    else's problem and re-runs the job. That is how the phantom-residue
    #    breakage (see `declaration_scope`) sat red across two lanes while the
    #    real message — "a declaration merged by another PR is grading your
    #    diff" — appeared nowhere in the output. A self-test is about the
    #    CHECKER. The live tree is what `--base` is for, and it already runs as
    #    its own guard step beside this one.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, _, _ in REGISTERS:
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = REPO_ROOT / rel
            if src.is_file():
                shutil.copy(src, dst)
            else:
                dst.write_text(json.dumps({"schema_version": 1, "rows": []}),
                               encoding="utf-8")
        if not _init_repo(root):
            # git unusable: SKIP LOUDLY rather than report a policy failure we
            # never measured, or a pass we did not earn.
            fails.append("the hermetic scoping cases could not run — git is "
                         "unusable here, so they were NOT measured")
        else:
            v_same = check("selftest-base", root=root)
            ok("…while a root where the registers EXIST and none CHANGED is "
               "still a real, clean reading", v_same["ok"])
            ok("…and that reading is over a REAL population, not an empty one",
               sum(1 for r in v_same["results"] if r["state"] == SKIPPED)
               == len(REGISTERS))

            # ── DECLARATION SCOPING. The residue defect, pinned. ────────────
            decl_dir = root / ".github" / "register-removals"
            decl_dir.mkdir(parents=True, exist_ok=True)
            phantom_doc = json.dumps({"why": "test", "removals": [
                {"file": REGISTERS[0][0], "id": "no-such-row", "keys": ["x"]}]})
            (decl_dir / "inherited.json").write_text(phantom_doc,
                                                    encoding="utf-8")
            _commit_all(root, "base carries a declaration")
            _tag(root, "selftest-base2")

            v_inh = check("selftest-base2", root=root)
            ok("a declaration INHERITED from the base is not graded against "
               "this diff — the residue defect that red-lined every PR in the "
               "repo on 2026-09-12", v_inh["ok"])
            ok("…and the counts say it was inherited rather than checked",
               v_inh["declarations_inherited"] == 1
               and v_inh["declarations_read"] == 0)

            (decl_dir / "mine.json").write_text(phantom_doc, encoding="utf-8")
            v_mine = check("selftest-base2", root=root)
            ok("…while a declaration THIS diff ADDS is still graded, so the "
               "scoping is not a blanket silencer",
               (not v_mine["ok"]) and len(v_mine["phantom"]) == 1)
            ok("…and exactly the one this diff carries is read",
               v_mine["declarations_read"] == 1
               and v_mine["declarations_inherited"] == 1)

            # ── THE MERGE-BASE. A branch BEHIND the base is not a branch
            #    that removed anything. Both directions, because a base-fix
            #    that also stops catching real removals is worse than the bug.
            reg0, array0, idfield0 = REGISTERS[0]
            (decl_dir / "mine.json").unlink()
            _commit_all(root, "drop the added declaration")
            _run_git(root, "branch", "-f", "selftest-fork")
            live0 = root / reg0
            doc0 = json.loads(live0.read_text(encoding="utf-8"))
            rows0 = doc0.get(array0)
            if isinstance(rows0, list):
                # the BASE gains a row after the fork …
                _run_git(root, "checkout", "-q", "-B", "selftest-basebranch")
                doc0[array0] = list(rows0) + [{idfield0: "row-the-base-gained"}]
                live0.write_text(json.dumps(doc0, indent=2), encoding="utf-8")
                _commit_all(root, "base gains a row")
                _run_git(root, "checkout", "-q", "selftest-fork")

                v_behind = check("selftest-basebranch", root=root)
                ok("a branch BEHIND the base does not report the base's NEW "
                   "rows as losses it caused — measured on #11897, where the "
                   "guard named a row introduced four minutes after the "
                   "branch's last merge", v_behind["ok"])
                ok("…and it says it graded against the merge-base",
                   v_behind["base_state"] == MERGE_BASE)

                # … and a REAL removal on the branch is still caught.
                doc_fork = json.loads(live0.read_text(encoding="utf-8"))
                fork_rows = doc_fork.get(array0) or []
                if fork_rows:
                    doc_fork[array0] = fork_rows[1:]
                    live0.write_text(json.dumps(doc_fork, indent=2),
                                     encoding="utf-8")
                    v_real = check("selftest-basebranch", root=root)
                    ok("…while a row this branch ACTUALLY removed is still a "
                       "finding, so the merge-base fix is not a silencer",
                       not v_real["ok"])
                    live0.write_text(json.dumps(doc_fork | {array0: fork_rows},
                                                indent=2), encoding="utf-8")

            # ── THE LIVE WIRING. `check` must PASS base_state down to
            #    `compare`, and a pure-function control cannot see that: a
            #    planted removal of that argument left the whole self-test
            #    green while no live run would ever carry the caveat.
            #
            #    An UNRELATED HISTORY is the realistic REF_TIP fixture — an
            #    unresolvable ref makes every register read as absent on the
            #    base, so it produces no findings to caveat. Here `merge-base`
            #    genuinely fails while `git show <ref>:<path>` still works,
            #    which is the shape a shallow clone also produces.
            if isinstance(rows0, list):
                _run_git(root, "checkout", "-q", "--orphan", "selftest-unrelated")
                doc_u = {k: v for k, v in doc0.items()}
                doc_u[array0] = list(rows0) + [{idfield0: "row-only-on-the-orphan"}]
                live0.write_text(json.dumps(doc_u, indent=2), encoding="utf-8")
                _commit_all(root, "unrelated history")
                _run_git(root, "checkout", "-q", "-f", "selftest-fork")

                st_u, _ = resolve_base("selftest-unrelated", root)
                v_u = check("selftest-unrelated", root=root)
                ok("an UNRELATED history has no merge-base, so the guard falls "
                   "back to the ref tip", st_u == REF_TIP
                   and v_u.get("base_state") == REF_TIP)
                ok("…and `check` PASSES that down, so every LIVE finding "
                   "carries the behind-base caveat rather than only the summary",
                   bool(v_u["findings"])
                   and all("NO MERGE-BASE" in f["why"] for f in v_u["findings"]))
                ok("…and the rendered FIX line sends the reader to the merge, "
                   "not to a by-field resolution of a conflict that may not exist",
                   FIX_LEAD_BEHIND_BASE in render(v_u))
                _run_git(root, "checkout", "-q", "-f", "selftest-fork")
                live0.write_text(json.dumps(doc0, indent=2), encoding="utf-8")

            st_b, sha_b = resolve_base("no-such-ref-anywhere", root)
            ok("an unresolvable base falls back to the REF TIP and says so, "
               "rather than silently grading against nothing",
               st_b == REF_TIP and sha_b == "no-such-ref-anywhere")
            ok("…and the summary warns that a loss may not be this branch's",
               "NO MERGE-BASE" in verdict_of([], [], base_state=REF_TIP)["summary"])

            # ── THE FINDING ITSELF CARRIES THE CAVEAT, NOT ONLY THE SUMMARY.
            #    `render` prints one ::error:: per finding, and a session
            #    reading the line that names ITS row id does not necessarily
            #    read the summary above it. Both directions, so the caveat is
            #    shown to be CONDITIONAL rather than always-on decoration.
            _base_doc = {"items": [{"id": "X", "a": 1, "b": 2}, {"id": "Y", "a": 1}]}
            _head_doc = {"items": [{"id": "X", "a": 1}]}
            _tip = compare(_base_doc, _head_doc, "items", "id", "p",
                           base_state=REF_TIP)
            _mb = compare(_base_doc, _head_doc, "items", "id", "p",
                          base_state=MERGE_BASE)
            ok("a finding graded against the ref TIP says so in the finding",
               all("NO MERGE-BASE" in f["why"] for f in _tip["findings"]))
            ok("…and a merge-base finding does NOT carry that caveat",
               not any("NO MERGE-BASE" in f["why"] for f in _mb["findings"]))
            ok("base_state changes only the WORDING, never which findings fire",
               [(f["kind"], f["id"], f["key"]) for f in _tip["findings"]]
               == [(f["kind"], f["id"], f["key"]) for f in _mb["findings"]]
               and len(_mb["findings"]) == 2)
            ok("a FIELD_LOSS no longer ASSERTS a cause it did not measure",
               all("reverted silently" not in f["why"] for f in _mb["findings"]))
            ok("…and says what it DID measure, and what it did not",
               any("NOT MEASURED" in f["why"] for f in _mb["findings"]))
            # ⚠️ ASSERT THE FIX LINE, NOT THE STRING ANYWHERE IN THE OUTPUT.
            #    A planted removal of the tip-fallback branch ESCAPED the first
            #    version of these two, because the findings' own caveat text
            #    also contains "git merge origin/main" — so `in render(...)`
            #    was satisfied by the wrong source and the control was vacuous.
            _FIXLEAD = FIX_LEAD_BEHIND_BASE
            ok("the tip-fallback FIX line leads with `git merge origin/main`, "
               "which is the remedy there — resolving by field is not",
               _FIXLEAD in render(verdict_of([_tip], [], base_state=REF_TIP)))
            ok("…and the merge-base FIX line does not send the reader to a "
               "merge that would change nothing",
               _FIXLEAD not in render(verdict_of([_mb], [], base_state=MERGE_BASE))
               and "BY FIELD" in render(verdict_of([_mb], [], base_state=MERGE_BASE)))

            st, names = declaration_scope(None, root)
            ok("no base means UNSCOPED, never an empty set — 'we did not look' "
               "is not 'this diff carries no declaration'",
               st == UNSCOPED and names is None)
            st2, names2 = declaration_scope("no-such-ref-anywhere", root)
            ok("an unresolvable base is UNSCOPED too, not a clean scope",
               st2 == UNSCOPED and names2 is None)
            ok("…and UNSCOPED says in the summary that a phantom may not be "
               "yours",
               "SCOPE UNKNOWN"
               in verdict_of([], [], scope_state=UNSCOPED)["summary"])

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
