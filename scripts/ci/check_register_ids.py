#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (register-id-guard)
"""Enforce ID UNIQUENESS and ID IDENTITY across the shared JSON registers.

WHY THIS EXISTS
---------------
This is the half of the register-collision problem **git cannot do**.

`.gitattributes` + `scripts/ops/merge_json_register.py` (MI-76) resolve the
*textual* collision: two branches appending rows to the same register merge
row-aware instead of by line. Measured for this guard's PR on `main`
@`855397f6`, two sibling branches each appending one row:

    driver NOT installed   4 of 4 registers CONFLICT
    driver installed       3 of 3 appendable registers CLEAN

So the textual half is solved. What no merge driver and no `git` can see is a
**semantic id collision**:

    main            MI-86 = "the automerge trigger misfires"   (real content)
    a sibling PR    MI-86 = "a brand-new unrelated item"       (filed blind)

To git that is one changed value at one key — indistinguishable from *"we both
edited this row"*. Merging it as written **silently deletes** the item that was
already there. It happened on 2026-09-03 and was caught by a human reading the
three-way diff, which is not a mechanism.

⚠️ **A FINER CLOCK IS NOT THE FIX** and this guard deliberately does not add
one. `session_registry.py` minted `registry_key` from a second-granular
timestamp; going to microseconds narrows the collision window without closing
it. The fix is a **uniqueness CHECK**, which is what this file is. (The live
evidence that the window is not theoretical: measured on `main` @`855397f6`,
`SESSIONS.json` carries 85 rows of which 14 have a `registry_key` and only
**12 are distinct** — `pending-20260902T133456Z` is shared by THREE rows.)

THREE CHECKS, each mapping to one way an id collision reaches `main`
-------------------------------------------------------------------
* **R1 — uniqueness (whole file, every register).** Two rows in one array with
  the same id. This is the *append* spelling of the collision: the branch added
  `MI-86` next to the `MI-86` that was already there. Full coverage: every row
  carries its id field by definition, so there is nothing to grade `unknown`.

* **R2 — identity immutability (diff-scoped against the merge base).** This is
  the *replace* spelling, and R1 is blind to it: the branch overwrote main's
  `MI-86`, so the branch's own file contains no duplicate at all. An id that
  exists in BOTH base and head whose **creation facts** changed is either an id
  being reused for new work (the bug) or a row being rewritten wholesale (rare,
  and worth a human look either way).

* **R3 — new rows must carry a creation-date field (diff-scoped, the past
  grandfathered).** R2's reach is exactly the share of rows that record when
  they were created, and today that share is **not** 100%. R3 is what makes it
  rise instead of staying where it is. Same shape as
  `check_backlog_criteria.py`: the past is grandfathered, the future is not.

⚠️ **R2's COVERAGE IS PARTIAL AND IS PRINTED, NEVER COLLAPSED.** A row with no
creation-date field is graded `unassessable` — *we could not look* — and is
counted and reported separately from `passed`. This guard's own output on
`main` @`855397f6`, over ids present on BOTH sides (run it to re-measure):

    OPEN-ITEMS.json          items[id]          50/50    (100%)
    SESSIONS.json            sessions[session_id]  85/85 (100%)
    SESSIONS.json            sessions[registry_key] 12/12 (100%)
    research-review-backlog  items[id]          12/12    (100%)
    performance-review-...   items[id]         106/113    (94%)
    ml-review-backlog        items[id]         101/107    (94%)
    health-review-backlog    items[id]        1072/1149    (93%)
    MANAGER-CHECKLIST.json   items[id]          26/84      (31%)  <-- weakest,
                                                                   and where the
                                                                   2026-09-03
                                                                   incident was
    OPEN-PRS.json            open_prs[pr]        0/2        (0%)  <-- no row
    OPEN-PRS.json            settled_prs[pr]     0/25       (0%)      carries a
                                                                      creation
                                                                      date yet

Reporting `MANAGER-CHECKLIST 31%` or `OPEN-PRS 0%` as a pass would be the
*"green is not evidence"* shape this repo has a rule about: a verdict computed
over 26 of 84 rows is not a verdict over the register, and one computed over
zero rows is vacuous rather than clean. R1 covers those registers in full
regardless — it needs no creation date — so the honest statement for
MANAGER-CHECKLIST today is *"R1 fully; R2 over 31% of shared ids"*.

R3 is what makes those two numbers climb: every row added from now on must
carry a creation date, so the unassessable share shrinks as the registers turn
over, rather than sitting where it happens to be.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class Register:
    """One register array, and how to identify a row inside it.

    `creation_fields` are the fields a row gets AT FILING TIME and that a later
    edit has no legitimate reason to move. They are listed most-authoritative
    first; the first one present on a row is the one used, so a register that
    changes its spelling over time still grades every row it can.
    """

    def __init__(self, path: str, array: str, id_field: str,
                 creation_fields: Sequence[str],
                 headline_fields: Sequence[str],
                 *, id_required: bool = True):
        self.path = path
        self.array = array
        self.id_field = id_field
        self.creation_fields = tuple(creation_fields)
        self.headline_fields = tuple(headline_fields)
        #: `registry_key` exists on only the rows that were spawned through the
        #: registry, so its absence is normal and is NOT a finding — but where
        #: it IS present it must be unique. Distinguishing these is the whole
        #: reason this flag exists rather than a blanket "every row has an id".
        self.id_required = id_required

    @property
    def label(self) -> str:
        return f"{self.path}::{self.array}[{self.id_field}]"


#: The registers this guard governs. Deliberately an explicit list and not
#: "every JSON file under docs/claude" — a list that grows without argument
#: stops being read (the reasoning `manager_preflight.REGISTERS` records).
REGISTERS: Tuple[Register, ...] = (
    Register("docs/claude/OPEN-ITEMS.json", "items", "id",
             creation_fields=("opened",), headline_fields=("summary", "detail")),
    Register("docs/claude/work/MANAGER-CHECKLIST.json", "items", "id",
             creation_fields=("added", "opened", "opened_at"),
             headline_fields=("title", "note")),
    Register("docs/claude/work/SESSIONS.json", "sessions", "session_id",
             creation_fields=("spawned_at",), headline_fields=("title", "why")),
    # The SECOND id on the same array. This is the one that is actually
    # duplicated on main today, and it is optional-but-unique: absent on the 71
    # rows that were never spawned through the registry.
    Register("docs/claude/work/SESSIONS.json", "sessions", "registry_key",
             creation_fields=("spawned_at",), headline_fields=("title", "why"),
             id_required=False),
    Register("docs/claude/work/OPEN-PRS.json", "open_prs", "pr",
             creation_fields=("opened_at", "as_of"), headline_fields=("title",)),
    Register("docs/claude/work/OPEN-PRS.json", "settled_prs", "pr",
             creation_fields=("opened_at", "settled_at"), headline_fields=("title",)),
    Register("docs/claude/health-review-backlog.json", "items", "id",
             creation_fields=("opened_at", "opened"), headline_fields=("title", "detail")),
    Register("docs/claude/performance-review-backlog.json", "items", "id",
             creation_fields=("opened_at", "opened"), headline_fields=("title", "detail")),
    Register("docs/claude/ml-review-backlog.json", "items", "id",
             creation_fields=("opened_at", "opened"), headline_fields=("title", "detail")),
    Register("docs/claude/research-review-backlog.json", "items", "id",
             creation_fields=("opened_at", "opened"), headline_fields=("title", "detail")),
)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _rows(doc: Any, array: str) -> List[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    got = doc.get(array)
    if not isinstance(got, list):
        return []
    return [r for r in got if isinstance(r, dict)]


def _load(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _load_at_base(path: str, base: str) -> Optional[Any]:
    """The register as it stands on *base*, or None if it is not there/parseable.

    ⚠️ Returning None means **we could not look**, and every caller treats it as
    `unknown` rather than as "nothing changed". A guard that reads a failed
    `git show` as an empty base would grade every row on the branch as new and
    report a confident all-clear — the exact shape this repo files as a
    collapsed state.
    """
    try:
        out = subprocess.run(["git", "show", f"{base}:{path}"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def _first_present(row: Dict[str, Any], fields: Sequence[str]) -> Optional[str]:
    for f in fields:
        if row.get(f) not in (None, ""):
            return f
    return None


#: Below this share of the base row's field VALUES surviving into the head row,
#: a headline change is a wholesale REPLACEMENT rather than an edit.
#:
#: MEASURED, not guessed. Population: 126,278 same-id row-pairs across adjacent
#: commits — OPEN-ITEMS.json (2,074 pairs / 84 commits), MANAGER-CHECKLIST.json
#: (2,528 / 53) and health-review-backlog.json (121,676 / 120), taken from
#: `git log --follow` on each file as of 2026-09-03. Of those, **16 changed the
#: row's headline at all**, and the LOWEST field-retention among those 16 real
#: edits is **0.33**. A row replaced by a different item retains ~0.0 by
#: construction: it shares no field values with what was there.
#:
#: So there is a wide empty band between 0.00 and 0.33 and the exact value is
#: not load-bearing — anything in 0.05–0.30 separates the two populations
#: identically. 0.25 fires on **0 of 16** real edits.
#:
#: ⚠️ This is ONE measured corpus, not a law. If a legitimate rewrite ever trips
#: it, the row to change is this constant plus its measurement — not the guard's
#: reach.
REPLACEMENT_RETENTION = 0.25


def _retention(base_row: Dict[str, Any], head_row: Dict[str, Any],
               id_field: str) -> Optional[float]:
    """Share of *base_row*'s non-id field VALUES still present in *head_row*."""
    before = {(k, json.dumps(v, sort_keys=True, default=str))
              for k, v in base_row.items() if k != id_field}
    if not before:
        return None
    after = {(k, json.dumps(v, sort_keys=True, default=str))
             for k, v in head_row.items() if k != id_field}
    return len(before & after) / len(before)


# --------------------------------------------------------------------------- #
# the three checks
# --------------------------------------------------------------------------- #
def check_uniqueness(reg: Register, rows: Iterable[Dict[str, Any]]) -> List[str]:
    """R1 — no two rows in one array share an id."""
    seen: Dict[str, int] = {}
    problems: List[str] = []
    for i, row in enumerate(rows):
        raw = row.get(reg.id_field)
        if raw in (None, ""):
            if reg.id_required:
                problems.append(
                    f"{reg.label}: row {i} has no '{reg.id_field}'. A row that "
                    f"cannot be named cannot be merged row-aware, and it cannot "
                    f"be checked for collision either.")
            continue
        rid = str(raw)
        if rid in seen:
            problems.append(
                f"{reg.label}: id {rid!r} appears at rows {seen[rid]} AND {i}. "
                f"Git cannot see this: to it these are two values at two "
                f"positions. Mint a fresh id for the newer row.")
        else:
            seen[rid] = i
    return problems


def check_identity(reg: Register, base_rows: Sequence[Dict[str, Any]],
                   head_rows: Sequence[Dict[str, Any]]) -> Tuple[List[str], Dict[str, int]]:
    """R2 — an id in both base and head must keep its creation facts.

    Returns (problems, coverage) where coverage counts, over the ids present in
    BOTH sides, how many could actually be graded. `unassessable` is reported,
    never folded into `passed`.
    """
    base_by_id = {str(r[reg.id_field]): r for r in base_rows
                  if r.get(reg.id_field) not in (None, "")}
    head_by_id = {str(r[reg.id_field]): r for r in head_rows
                  if r.get(reg.id_field) not in (None, "")}
    shared = sorted(set(base_by_id) & set(head_by_id))

    problems: List[str] = []
    cov = {"shared": len(shared), "assessed": 0, "unassessable": 0, "replaced": 0}
    for rid in shared:
        b, h = base_by_id[rid], head_by_id[rid]

        b_head = _first_present(b, reg.headline_fields)
        h_head = _first_present(h, reg.headline_fields)
        b_txt = str(b.get(b_head)) if b_head else None
        h_txt = str(h.get(h_head)) if h_head else None
        headline_moved = b_txt != h_txt

        # --- R2a: the creation fact moved, and so did the headline -----------
        # Tight, but its reach is only the rows that RECORD a creation date.
        bf = _first_present(b, reg.creation_fields)
        hf = _first_present(h, reg.creation_fields)
        if bf is None or hf is None:
            cov["unassessable"] += 1
        else:
            cov["assessed"] += 1
            if (bf != hf or str(b.get(bf)) != str(h.get(hf))) and headline_moved:
                problems.append(
                    f"{reg.label}: id {rid!r} kept its id but changed BOTH its "
                    f"creation fact ({bf}={b.get(bf)!r} -> {hf}={h.get(hf)!r}) "
                    f"and its headline ({b_txt!r} -> {h_txt!r}). That is the "
                    f"signature of a NEW item filed under an id that was "
                    f"already taken — merging it deletes the row that was "
                    f"there. If you meant to file new work, mint a fresh id.")
                continue

        # --- R2b: the row was REPLACED wholesale -----------------------------
        # ⚠️ THIS IS THE RULE THAT CATCHES THE ACTUAL 2026-09-03 INCIDENT, and
        # R2a alone does NOT: `main`'s MI-86 carries no creation date, so R2a
        # grades it `unassessable` and the collision walks straight through.
        # Measured before this existed — the planted MI-86 replacement was
        # caught in its APPEND spelling by R1 and MISSED in its REPLACE
        # spelling. R2b needs no creation date, so its coverage is every shared
        # id, which is why the weakest register (MANAGER-CHECKLIST, 31% on R2a)
        # is nonetheless fully covered against a replacement.
        if headline_moved:
            ret = _retention(b, h, reg.id_field)
            if ret is not None and ret < REPLACEMENT_RETENTION:
                cov["replaced"] += 1
                problems.append(
                    f"{reg.label}: id {rid!r} was REPLACED, not edited — its "
                    f"headline changed ({b_txt!r} -> {h_txt!r}) and only "
                    f"{ret:.0%} of its fields survived (below the measured "
                    f"{REPLACEMENT_RETENTION:.0%} floor; 0 of 16 real "
                    f"headline-changing edits in 126,278 observed row-pairs "
                    f"retain this little). This is a NEW item wearing an id "
                    f"that was already taken: merging it DELETES "
                    f"{b_txt!r}. Mint a fresh id.")
    return problems, cov


def check_new_rows_dateable(reg: Register, base_rows: Sequence[Dict[str, Any]],
                            head_rows: Sequence[Dict[str, Any]]) -> List[str]:
    """R3 — a row ADDED by this diff must record when it was created.

    Diff-scoped on purpose: the past is grandfathered (880 of 1149 backlog rows
    and 26 of 84 checklist rows predate this rule), the future is not. Without
    this, R2's coverage is frozen at whatever it happens to be today.
    """
    base_ids = {str(r[reg.id_field]) for r in base_rows
                if r.get(reg.id_field) not in (None, "")}
    problems: List[str] = []
    for row in head_rows:
        raw = row.get(reg.id_field)
        if raw in (None, ""):
            continue
        rid = str(raw)
        if rid in base_ids:
            continue
        if _first_present(row, reg.creation_fields) is None:
            problems.append(
                f"{reg.label}: NEW row {rid!r} records no creation date "
                f"(expected one of {list(reg.creation_fields)}). Without it "
                f"nothing can later tell this row apart from a different item "
                f"filed under the same id.")
    return problems


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def check(root: Path, base: Optional[str] = None,
          registers: Sequence[Register] = REGISTERS
          ) -> Tuple[List[str], List[str]]:
    """Return (problems, report_lines)."""
    problems: List[str] = []
    report: List[str] = []

    for reg in registers:
        path = root / reg.path
        doc = _load(path)
        if doc is None:
            # A register that is absent or unparseable is not this guard's
            # finding to make (open-items-guard and the JSON syntax check own
            # that), but it must be SAID rather than skipped silently.
            report.append(f"  {reg.label}: not present or not parseable — not checked")
            continue
        head_rows = _rows(doc, reg.array)

        problems += check_uniqueness(reg, head_rows)

        if base is None:
            report.append(f"  {reg.label}: {len(head_rows)} rows, unique "
                          f"(R2/R3 skipped — no base given)")
            continue

        base_doc = _load_at_base(reg.path, base)
        if base_doc is None:
            report.append(f"  {reg.label}: {len(head_rows)} rows, unique · "
                          f"R2 UNKNOWN (no readable base at {base})")
            continue
        base_rows = _rows(base_doc, reg.array)

        idp, cov = check_identity(reg, base_rows, head_rows)
        problems += idp
        problems += check_new_rows_dateable(reg, base_rows, head_rows)

        pct = (100.0 * cov["assessed"] / cov["shared"]) if cov["shared"] else 100.0
        report.append(
            f"  {reg.label}: {len(head_rows)} rows, unique · "
            f"R2a (creation-fact) assessed {cov['assessed']}/{cov['shared']} "
            f"shared ids ({pct:.0f}%), {cov['unassessable']} unassessable · "
            f"R2b (replacement) {cov['shared']}/{cov['shared']} (100%), "
            f"{cov['replaced']} replaced")
    return problems, report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=None,
                    help="git ref to diff identity against (e.g. origin/main). "
                         "Without it only R1 (uniqueness) runs, and the report "
                         "says so rather than implying R2 passed.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    problems, report = check(Path(args.root), args.base)
    print("register-id-guard: id uniqueness + identity across "
          f"{len(REGISTERS)} register arrays")
    for line in report:
        print(line)
    if problems:
        print("::error::A register id collided. Git cannot see this class: to "
              "git a reused id is a changed value at the same key, so the merge "
              "silently deletes the row that was already there.")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("register-id-guard: OK")
    return 0


def _self_test() -> int:
    """Prove the guard finds a positive before its silence is trusted.

    Planted controls run in BOTH directions for every rule — a guard that only
    ever demonstrates its failures cannot show it is not simply always-red.
    """
    ok = True
    reg = Register("r.json", "items", "id",
                   creation_fields=("opened",), headline_fields=("summary",))
    a = {"id": "MI-86", "opened": "2026-09-01", "summary": "the automerge trigger misfires"}
    b = {"id": "MI-87", "opened": "2026-09-01", "summary": "something else"}

    cases: List[Tuple[str, bool, bool]] = []

    def r1(rows, label, want):
        cases.append((label, bool(check_uniqueness(reg, rows)), want))

    def r2(base, head, label, want):
        probs, _ = check_identity(reg, base, head)
        cases.append((label, bool(probs), want))

    def r3(base, head, label, want):
        cases.append((label, bool(check_new_rows_dateable(reg, base, head)), want))

    # R1
    r1([a, b], "R1: distinct ids pass", False)
    r1([a, dict(a)], "R1: a duplicated id is a finding", True)
    r1([a, {**b, "id": "MI-86"}], "R1: the MI-86 APPEND spelling is a finding", True)
    r1([a, {"opened": "x", "summary": "y"}], "R1: a row with no id is a finding", True)

    # R1 on an optional id — absence is normal, collision is not.
    optreg = Register("r.json", "items", "registry_key", creation_fields=("opened",),
                      headline_fields=("summary",), id_required=False)
    cases.append(("R1: an ABSENT optional id is not a finding",
                  bool(check_uniqueness(optreg, [{"id": "x"}, {"id": "y"}])), False))
    cases.append(("R1: a DUPLICATED optional id is a finding",
                  bool(check_uniqueness(optreg, [{"registry_key": "k"},
                                                 {"registry_key": "k"}])), True))

    # R2 — the replace spelling.
    r2([a], [a], "R2: an untouched row passes", False)
    r2([a], [{**a, "summary": "the automerge trigger misfires (typo fixed)"}],
       "R2: editing only the headline passes (an ordinary edit)", False)
    r2([a], [{**a, "opened": "2026-09-03"}],
       "R2: correcting only the date passes (a typo fix, not a re-file)", False)
    r2([a], [{"id": "MI-86", "opened": "2026-09-03", "summary": "a brand-new item"}],
       "R2: the MI-86 REPLACE spelling is a finding", True)
    r2([a], [a, b], "R2: adding a new row alongside passes", False)
    # An UNDATED row is not thereby a finding — R2a declines to grade it and
    # R2b only fires if the row was actually replaced. ⚠️ This control used to
    # use a one-field row (`{id, summary}`) and asserted a pass; R2b correctly
    # turns that into a finding, because a row whose ONLY field is swapped has
    # retained nothing and IS a replacement. The control was encoding R2a's
    # blind spot as though it were desired behaviour, so it was rewritten
    # rather than the rule being relaxed to keep it green.
    undated_ordinary = {"id": "MI-86", "state": "in_flight", "owner": "session_01AAA",
                        "priority": "P1", "summary": "no date here"}
    r2([undated_ordinary], [{**undated_ordinary, "summary": "no date here, reworded"}],
       "R2: an UNDATED row given an ordinary edit still passes", False)

    # that unassessable row must be COUNTED, not silently passed
    _, cov = check_identity(reg, [{"id": "MI-86", "summary": "s"}],
                            [{"id": "MI-86", "summary": "t"}])
    cases.append(("R2a: unassessable rows are counted, not collapsed into passed",
                  cov["unassessable"] == 1 and cov["assessed"] == 0, True))

    # --- R2b: the wholesale-replacement detector -----------------------------
    # THE REGRESSION CONTROL. Before R2b existed this exact pair passed, because
    # the base row carries no creation date and R2a therefore never looked. It
    # is the real shape of the 2026-09-03 MI-86 incident.
    undated_live = {"id": "MI-86", "state": "in_flight", "owner": "session_01AAA",
                    "pr": 10903, "priority": "P1", "note": "night shift handoff",
                    "summary": "Night shift — handoff executed, lease released"}
    replaced = {"id": "MI-86", "opened": "2026-09-03", "state": "in_flight",
                "summary": "a brand-new unrelated item filed blind"}
    r2([undated_live], [replaced],
       "R2b: an UNDATED row replaced wholesale is caught (the real MI-86 case)", True)
    # ...and the ordinary edits it must not fire on.
    r2([undated_live], [{**undated_live, "state": "done"}],
       "R2b: an ordinary state transition passes", False)
    r2([undated_live],
       [{**undated_live, "summary": "Night shift — handoff executed, lease "
                                    "released, brief delivered"}],
       "R2b: extending the headline while keeping the row passes", False)
    r2([undated_live], [{**undated_live, "note": "x", "priority": "P2",
                         "summary": "reworded headline"}],
       "R2b: a reworded headline that KEEPS most fields passes "
       "(retention above the measured floor)", False)
    _, cov_b = check_identity(reg, [undated_live], [replaced])
    cases.append(("R2b: a replacement is counted in the report",
                  cov_b["replaced"] == 1, True))

    # R3
    r3([a], [a], "R3: no new row, nothing to require", False)
    r3([a], [a, b], "R3: a new row WITH a creation date passes", False)
    r3([a], [a, {"id": "MI-99", "summary": "undated"}],
       "R3: a new row with NO creation date is a finding", True)
    r3([{"id": "MI-99", "summary": "grandfathered, undated"}],
       [{"id": "MI-99", "summary": "grandfathered, undated"}],
       "R3: an existing undated row is grandfathered", False)

    for label, got, want in cases:
        status = "PASS" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  self-test ({label}): {status}")
    print("register-id-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
