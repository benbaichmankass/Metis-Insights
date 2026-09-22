"""claim-basis-guard: a NEW backlog row asserting quantitative evidence must
carry a parseable denominator (P2.5 of the 2026-07-31 full-system-audit plan;
`BL-20260731-CLAIM-SURFACE-UNGUARDED` preventer P2).

The gap this closes: every CI guard in this repo checks CODE shape; the
NUMBERS that Tier-3 decisions are made from (register rows, research claims)
had no preventer — five claim-defects shipped in one session, all self-caught,
none by a repo mechanism. The full rule is
`docs/CLAUDE-RULES-CANONICAL.md` § "Always state the population"; this guard
is its mechanical FLOOR, deliberately scoped to the surfaces that are
structured enough to check without crying wolf: **register rows**.

⚠️ RE-POINTED 2026-09-22 (E45) — IT WAS GRADING NOTHING, AND THE #1 DOC SAID
OTHERWISE. This guard read the four review backlogs, all of which the
2026-09-21 operating reset ARCHIVED. MEASURED on `main` 2026-09-22 before the
re-point: `0 backlog file(s) scanned, 0 finding(s)` — it could not open a single
subject, and while that exits 1 rather than green, `claim-basis-guard` had ALSO
been dropped from `scripts/ci/run_guards.py` at the reset, so nothing ran it at
all. Meanwhile `docs/CLAUDE-RULES-CANONICAL.md` § "Backlog governance" still
said the `status` enum was *"enforced whole-file over every review backlog by
`claim-basis-guard`"*. A canonical doc naming a mechanism that cannot run is
the folklore failure that document has its own section about.

The post-reset taxonomy is exactly two intakes plus one pipeline, and no fourth
register was invented here: **`docs/claude/work/MANAGER-CHECKLIST.json`** (the
builds — the one register, served to the operator as the Workflow page) and
**`docs/claude/work/PIPELINE.jsonl`** (anything needing pick-up later). Both
carry prose rows that later sessions read as established fact, which is the
whole reason this guard exists.

Contract (diff-scoped — rows whose id is NEW versus the base ref):
  - A new row in a watched register whose prose asserts evidence-grade
    figures — a percentage, an R-figure (e.g. `+32.66R`), or a $-total
    >= 1000 — must ALSO contain, in the same row, at least one parseable
    denominator/basis:
      * "N of M" / "N/M" with integer M,
      * "n=N" / "n = N",
      * an explicit row/count basis ("829 rows", "118 closes", "38 dirs"),
      * or a date-window (two ISO dates, or an ISO date + "since"/"→"/"..").
  - The basis must PARSE (integers are integers) — the new-table-wiring-guard
    lesson: a presence-only marker is cheaper to lie to than to satisfy.
  - Verified failure path: this file ships with tests that feed it a
    basis-less claim row and require exit 1.

Rows with no quantitative assertion are untouched. False-positive escape
hatch: none by design at V1 — a genuinely unquantifiable claim should not be
phrased as a number. Widening to research docs is a follow-up once the
false-positive rate here is observed at ~0.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

#: ⚠️ RE-POINTED 2026-09-22 (E45). The four names below are the ARCHIVED review
#: backlogs this guard used to read; they are kept in the comment and nowhere
#: else, so a reader can see what moved:
#:   docs/claude/{health,performance,ml,research}-review-backlog.json
#: All four are under `docs/archive/2026-09-21-operating-reset/`. The live
#: registers are these two.
BACKLOGS = (
    "docs/claude/work/MANAGER-CHECKLIST.json",
    "docs/claude/work/PIPELINE.jsonl",
)

# Evidence-grade figures: percentages, R-figures, $-totals >= 1,000.
_CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*%"                      # 65.3%
    r"|[+\-−]\d+(?:\.\d+)?\s*R\b"        # +32.66R / -4R
    r"|\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?"     # $36,018.60
    r"|\$\s?\d{4,}(?:\.\d+)?"                 # $36018
)

_BASIS_RES = (
    re.compile(r"\b\d+\s+of\s+\d+\b", re.IGNORECASE),          # 206 of 829
    re.compile(r"\b\d+\s*/\s*\d+\b"),                            # 24/327
    re.compile(r"\bn\s*=\s*\d+\b", re.IGNORECASE),               # n=979
    re.compile(r"\b\d+\s+(?:rows?|closes?|trades?|dirs?|files?|"
               r"manifests?|records?|fills?|samples?|folds?)\b",
               re.IGNORECASE),                                    # 829 rows
    re.compile(r"\b20\d\d-\d\d-\d\d\b.*\b20\d\d-\d\d-\d\d\b",
               re.DOTALL),                                        # two dates
    re.compile(r"\b(?:since|from|window|through)\b[^.\n]{0,40}"
               r"\b20\d\d-\d\d-\d\d\b", re.IGNORECASE),           # since <date>
)


def _rows(text: str) -> dict[str, dict]:
    """`{id: row}` from either watched shape.

    ⚠️ TWO SHAPES, AND THE JSONL ONE IS LAST-WINS ON PURPOSE.
    `MANAGER-CHECKLIST.json` is a JSON object with an `items` list.
    `PIPELINE.jsonl` is APPEND-ONLY JSONL with a `//` header block, so a row is
    UPDATED by appending it again under the same id — last occurrence is the
    current record, which is what `scripts/ops/pipeline.py::load` does. Reading
    it any other way would grade a superseded version of the row.
    """
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return {}
        items = (data.get("items") or data.get("backlog") or []) \
            if isinstance(data, dict) else data
        return {r["id"]: r for r in items
                if isinstance(r, dict) and isinstance(r.get("id"), str)}
    out: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            out[r["id"]] = r  # last wins
    return out


# Fields whose prose can carry a quantitative claim OR its basis.
#
# ⚠️ THIS LIST IS THE GUARD'S ENTIRE FIELD OF VIEW, IN BOTH DIRECTIONS.
# A field missing here produces a FALSE NEGATIVE when the claim lives there
# (the guard sees no claim and passes silently) and a FALSE POSITIVE when the
# claim is in `title` and the basis is there (a correct row is failed).
#
# `detail` and `evidence` were absent until 2026-08-20 while being the two
# richest prose fields these backlogs actually use. MEASURED across all three
# backlogs at that date (940 rows): 198 rows carried a quantitative claim in a
# then-scanned field, and **65 more carried one ONLY in an unscanned field** —
# 24.7% of the 263 claim-bearing rows, never checked at all. Sixteen of those
# 65 had no parseable basis anywhere in that text, i.e. they would have FAILED
# had the guard been able to read them; one of them cites `$247,683.78`, the
# very figure CLAUDE.md flags as an ALL-STATUS population number whose sign
# flips on the filter. A guard blind to a quarter of its own population reports
# a clean negative it never earned.
#
# The guard is DIFF-SCOPED to rows absent from the base (`rid in base_ids`
# continues), so widening this tuple does not retro-fail the 16 — it only holds
# NEW rows to the standard. When adding a field here, prefer prose fields;
# adding an id/date field would match dates as "basis" and weaken the check.
#
# ⚠️ WIDENED 2026-09-22 (E45) FOR THE POST-RESET SHAPES. `note` is the
# checklist's only prose field and carries everything a row asserts (its
# `item_schema` says so: *"what is true about this row"*); `what` and
# `terminal_reason` are the pipeline's. MEASURED over the two live registers
# at the re-point — MANAGER-CHECKLIST.json 75 rows, PIPELINE.jsonl 1,253 lines:
# with the pre-existing tuple alone the guard's field of view over the
# checklist was `title` only, and over the pipeline it was EMPTY, so it would
# have graded every pipeline row as claim-free. That is the 24.7%-blind failure
# recorded above, restated at 100%.
_ROW_TEXT_FIELDS = (
    "title", "description", "source", "action",
    "resolution", "resolution_criteria",
    "detail", "evidence", "why_it_matters", "summary", "impact",
    "note", "what", "terminal_reason",
)


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k, "")) for k in _ROW_TEXT_FIELDS)


def check_new_rows(base_text: str, head_text: str, path: str) -> list[str]:
    base_ids = set(_rows(base_text))
    failures = []
    for rid, row in _rows(head_text).items():
        if rid in base_ids:
            continue  # diff-scoped: only NEW rows are held to the guard
        text = _row_text(row)
        claims = _CLAIM_RE.findall(text)
        if not claims:
            continue
        if any(rx.search(text) for rx in _BASIS_RES):
            continue
        failures.append(
            f"{path}: NEW row '{rid}' asserts quantitative evidence "
            f"({', '.join(claims[:3])}) with NO parseable basis — state the "
            f"population/denominator/window in the row (canonical rule: "
            f"'Always state the population'). A number without its basis "
            f"is not a finding."
        )
    return failures


def _git_show(ref: str, path: str) -> str:
    r = subprocess.run(["git", "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else "{}"


# ---------------------------------------------------------------------------
# STATUS ENUM (workplan 0.1 Step 0, 2026-08-22; RE-POINTED 2026-09-22, E45)
#
# THE ORIGINAL HARM. `status` was uncontrolled free text across the four review
# backlogs: 41 distinct values, several of them whole sentences carrying state a
# two-state field could not hold. The cost was that THE OPEN SET WAS NOT
# COMPUTABLE -- 333, 378 and 121 were all quoted in one month and each was right
# under some filter, so no session could defend a count row by row.
#
# ⚠️ THE SUBJECT MOVED, THE HARM DID NOT. The 2026-09-21 reset archived all four
# backlogs, so this check graded NOTHING from that date until 2026-09-22 while
# `docs/CLAUDE-RULES-CANONICAL.md` § "Backlog governance" went on saying the
# enum was "enforced whole-file over every review backlog by claim-basis-guard".
# The post-reset register whose open count the operator reads is
# `docs/claude/work/MANAGER-CHECKLIST.json` -- it IS the Workflow page
# (`GET /api/bot/work/checklist`) -- and its state field is `state`. So that is
# what is graded now, with the same whole-file rule and for the same reason.
#
# ⚠️ `PIPELINE.jsonl` IS DELIBERATELY NOT RE-GRADED HERE. Its state enum has ONE
# home already -- `scripts/ops/pipeline.py::STATES`, enforced by `pipeline-guard`
# on every run via `--check`. Grading it a second time here would give one enum
# two owners, which is the drift this section exists to prevent, one level up.
#
# This still lives in the claim-basis guard rather than in a guard of its own,
# for the original reason: the pass that motivated it is a RETIREMENT pass, and
# a new guard about the register is precisely the failure it warns against. This
# file already opens both registers, so the check is ~20 lines here and zero new
# CI surface.
#
# A qualifier belongs in `note`, never in `state`.
# ---------------------------------------------------------------------------
STATUS_ENUM = frozenset({
    "queued", "in_flight", "blocked", "landed_unproven", "done", "dropped",
})

#: The one file whose rows carry `state`. Kept separate from BACKLOGS because
#: the claim-basis half watches both registers and this half watches one.
_STATE_REGISTER = "docs/claude/work/MANAGER-CHECKLIST.json"

#: The field that holds it. (Pre-2026-09-22 this was `status`, on the backlogs.)
_STATE_FIELD = "state"


def check_status_enum(head_text: str, path: str) -> list[str]:
    """Every row's `state` must be in STATUS_ENUM. Whole file, not diff.

    Deliberately NOT diff-scoped: the point is that the count is computable
    from the file as it stands, which a diff-scoped check cannot establish.
    An unreadable file returns no findings rather than a false clean -- the
    caller's `scanned` counter is what catches "we read nothing".

    ⚠️ IT ALSO GRADES THE ENUM AGAINST THE FILE'S OWN `states` BLOCK.
    MANAGER-CHECKLIST.json declares its vocabulary inline, with a gloss per
    value. If that block and STATUS_ENUM disagree, one of them is wrong and a
    session reading either would be misled -- *field beats comment*. Checking
    only the rows would let the two drift silently, which is the exact shape of
    the defect that made this re-point necessary.
    """
    if path != _STATE_REGISTER:
        return []
    try:
        doc = json.loads(head_text)
    except Exception:  # noqa: BLE001
        return []
    bad: list[str] = []

    declared = doc.get("states") if isinstance(doc, dict) else None
    if isinstance(declared, dict):
        declared_set = set(declared)
        if declared_set != set(STATUS_ENUM):
            bad.append(
                "%s: the file's own `states` block declares %s but "
                "check_claim_basis.STATUS_ENUM accepts %s. One of them is "
                "wrong; the field is the truth and the enum follows it. "
                "(`canonical-doc-coherence` mirrors STATUS_ENUM into "
                "docs/CLAUDE-RULES-CANONICAL.md, so a drift here reaches the "
                "#1 doc.)" % (path, sorted(declared_set), sorted(STATUS_ENUM)))

    rows = doc if isinstance(doc, list) else (
        doc.get("items") or doc.get("rows") or doc.get("backlog") or [])
    for r in rows:
        if not isinstance(r, dict):
            continue
        st = str(r.get(_STATE_FIELD, "")).strip()
        if st not in STATUS_ENUM:
            bad.append(
                "%s: row %s has %s %r, which is not in the enum %s. A "
                "qualifier belongs in `note` -- a free-text state makes the "
                "open count uncomputable."
                % (path, r.get("id") or r.get("item_id") or "<no id>",
                   _STATE_FIELD, st, sorted(STATUS_ENUM)))
    return bad


def _self_test() -> int:
    """Plant a violation against the RE-POINTED subject and prove it FAILS.

    ⚠️ THE REASON THIS EXISTS (E45, 2026-09-22). Between the 2026-09-21 reset
    and this change the guard read four archived files, scanned ZERO of them,
    and the #1 canonical doc still named it as what enforces the state enum.
    A green re-point proves nothing: the only evidence that it grades now is a
    planted violation against `docs/claude/work/MANAGER-CHECKLIST.json` that it
    refuses. Each positive below is paired with its negative control.
    """
    import tempfile
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    def checklist(states, rows):
        return json.dumps({"states": {k: "gloss" for k in states},
                           "items": rows})

    live = sorted(STATUS_ENUM)

    # ── P1 the planted violation: an off-enum `state` on the live register ──
    f = check_status_enum(
        checklist(live, [{"id": "E1", "state": "in_flight"},
                         {"id": "E2", "state": "done, but only on paper"}]),
        _STATE_REGISTER)
    ok(len(f) == 1 and "E2" in f[0],
       "P1 a free-text `state` on MANAGER-CHECKLIST.json is a FINDING — the "
       "planted positive against the re-pointed subject")
    ok("note" in f[0],
       "P1b and it names where the qualifier belongs, so the fix is obvious")

    # ── N1 the negative control: every legal value passes ──────────────────
    f = check_status_enum(
        checklist(live, [{"id": f"E{i}", "state": v}
                         for i, v in enumerate(live)]), _STATE_REGISTER)
    ok(f == [], f"N1 all six declared states pass: {live}")

    # ── P2 the file's own `states` block drifting from the enum ────────────
    f = check_status_enum(
        checklist([*live, "snoozed"], [{"id": "E1", "state": "queued"}]),
        _STATE_REGISTER)
    ok(len(f) == 1 and "snoozed" in f[0],
       "P2 a `states` block the enum does not accept is a FINDING — the two "
       "surfaces may not drift, because canonical-doc-coherence mirrors the "
       "enum into the #1 doc")

    # ── P3 a row with NO state at all is not silently fine ─────────────────
    f = check_status_enum(checklist(live, [{"id": "E1"}]), _STATE_REGISTER)
    ok(len(f) == 1 and "E1" in f[0],
       "P3 a MISSING state reads as off-enum, not as 'nothing to grade' — an "
       "absent value and a legal one must not collapse")

    # ── N2 the OTHER register is not graded here, and that is deliberate ───
    ok(check_status_enum('{"items":[{"id":"x","state":"routed"}]}',
                         "docs/claude/work/PIPELINE.jsonl") == [],
       "N2 PIPELINE.jsonl states are owned by scripts/ops/pipeline.py::STATES "
       "and enforced by pipeline-guard — one enum, one home")

    # ── P4/N3 the claim-basis half, on both post-reset row shapes ──────────
    base_cl = checklist(live, [])
    head_cl = checklist(live, [
        {"id": "NEW1", "state": "queued", "title": "roster drift",
         "note": "the bad cells are 42.9% of the fleet"}])
    f = check_new_rows(base_cl, head_cl, _STATE_REGISTER)
    ok(len(f) == 1 and "NEW1" in f[0],
       "P4 a NEW checklist row asserting 42.9% with no denominator FAILS — "
       "`note` is the checklist's only prose field, so a guard blind to it "
       "would grade every row claim-free")

    head_ok = checklist(live, [
        {"id": "NEW1", "state": "queued", "title": "roster drift",
         "note": "the bad cells are 42.9% of the fleet (12 of 28 legs)"}])
    ok(check_new_rows(base_cl, head_ok, _STATE_REGISTER) == [],
       "N3 ...and the same claim WITH its denominator passes — the fix works")

    jsonl_base = "// header\n"
    jsonl_head = ('// header\n'
                  '{"id": "PI-1", "what": "slippage is 3.4% of gross"}\n')
    f = check_new_rows(jsonl_base, jsonl_head, "docs/claude/work/PIPELINE.jsonl")
    ok(len(f) == 1 and "PI-1" in f[0],
       "P5 the APPEND-ONLY JSONL shape is parsed and graded too — the `//` "
       "header is skipped rather than read as a row")

    # last-wins: an id appended again is an UPDATE, not a new row
    jsonl_two = jsonl_head + '{"id": "PI-1", "what": "slippage is 3.4% of gross (n=811)"}\n'
    ok(check_new_rows(jsonl_base, jsonl_two,
                      "docs/claude/work/PIPELINE.jsonl") == [],
       "N4 ...and a later append under the same id SUPERSEDES the earlier one, "
       "as scripts/ops/pipeline.py::load reads it — grading the stale copy "
       "would fail a row that was already fixed")

    # ── N5 the shipped enum must match the REAL register, not a fixture ────
    try:
        real = json.loads(open(_STATE_REGISTER, encoding="utf-8").read())
    except OSError:
        real = None
    if isinstance(real, dict) and isinstance(real.get("states"), dict):
        ok(set(real["states"]) == set(STATUS_ENUM),
           "N5 the shipped STATUS_ENUM equals the live register's own `states` "
           "block — measured against the real file, not a fixture")

    with tempfile.TemporaryDirectory():
        pass
    print(f"claim-basis: self-test OK — {fired} planted controls all fire")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=None,
                    help="base git ref (e.g. origin/main)")
    ap.add_argument("--self-test", action="store_true",
                    help="plant a violation against each subject and prove it fails")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    if not args.base:
        ap.error("--base is required unless --self-test is given")
    # The verdict below is about the COMMITTED tree. Say so when that is
    # not the tree you edited. See
    # BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY
    import pathlib  # noqa: PLC0415 — local, so importing this module stays free
    import sys as _sys  # noqa: PLC0415
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "ci"))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()


    failures: list[str] = []
    scanned = 0
    for path in BACKLOGS:
        try:
            head = open(path, encoding="utf-8").read()
        except OSError:
            continue
        scanned += 1
        failures.extend(check_new_rows(_git_show(args.base, path), head, path))
        failures.extend(check_status_enum(head, path))

    for f in failures:
        print(f"::error::{f}")
    print(f"claim-basis-guard: {scanned} register file(s) scanned against "
          f"{args.base}, {len(failures)} finding(s) "
          f"(basis-less new claim rows + off-enum statuses).")
    if scanned == 0:
        print("::error::scanned NOTHING — no register file readable (an "
              "absent result, not a clean one; wrong cwd?). This is the exact "
              "state this guard sat in from 2026-09-21 until the E45 re-point.")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
