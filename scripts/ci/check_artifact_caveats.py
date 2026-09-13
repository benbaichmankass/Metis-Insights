#!/usr/bin/env python3
"""A defect filed against a PRODUCER must reach the ARTIFACTS it produced.

WHY THIS EXISTS
---------------
Operator directive 2026-08-27, after an audit found the same root from two
directions: *"we've been working for weeks already on the infra to push active
management forward and keep tripping over ourselves."*

The audit's answer (``docs/research/RESEARCH-INFRA-AUDIT-2026-08-27.md`` § 8.4)
was **not** that defects go unfound. This repo finds and files them well. It is
that a row filed against a research TOOL never travels to the artifacts that tool
already wrote, so the artifact keeps being read as clean evidence while every
caveat sits correctly filed somewhere else.

Measured 2026-08-27 on ``docs/research/exit-refinement-coverage.json`` — the
468-cell matrix every active-management decision is read from: **~50 OPEN backlog
rows name one of its producing tools, and NOT ONE appears in the matrix.** Three
of them condition every verdict in it (power, risk basis, quantization refusal).

⚠️ **THIS GUARD FORCES ADJUDICATION, NOT LISTING.** A row *mentioning* a producer
is not automatically a caveat — 34 of the ~50 name one sweep, and most will not
condition the verdicts. Requiring them all to be listed would produce a wall
nobody reads, which is the failure this exists to fix, one level up. So every
open producer-row must be placed in exactly ONE of three buckets, and the
placement is the decision:

  ``conditions_verdicts``       — a reader of ANY cell must see this.
  ``reviewed_not_conditioning`` — looked at, does not condition the verdicts,
                                  **with the reason stated**.
  ``pending_adjudication``      — not yet judged. Honest, and RATCHETED (below).

THE RATCHET, AND WHY IT IS THE POINT
------------------------------------
``pending_adjudication`` is accepted so this can land without 50 snap judgments
being made blind — a judgement made to get a guard green is worth less than no
judgement. But the artifact records ``pending_baseline``, and **the guard FAILS
if the pending list grows beyond it.** Debt is visible, bounded, and can only
shrink.

This is deliberately one step beyond the precedent it copies.
``check_risk_basis_agreement.KNOWN_DIVERGENCES`` registers accepted debt with an
honest comment (*"each one is a harness whose default answer is about a risk
setting production does not use"*) and has **no ratchet** — which is how the
fleet engine's 0.2 ratio has sat there being reported ``clean``. Registering debt
without a ratchet makes it permanent and quiet.

WHAT THIS GUARD DOES NOT DO
---------------------------
It does not judge whether a caveat is *correct*, and it cannot: that is a
research question. It enforces only that the decision was MADE and is VISIBLE in
the artifact a reader opens. A wrong adjudication is a reviewable line; an absent
one is invisible.

STATUS — REGISTERED (operator decision, 2026-08-27)
---------------------------------------------------
Registered in ``run_guards.py`` as ``artifact-caveat-guard``. It was written and
committed PARKED on 2026-08-27, because the operator's directive on the same audit
was: *"the fix can't just be more guards or another exclamation mark in the
CLAUDE.md — we need to fix how Claude understands context in this repo."* They
then chose to register it, alongside the structural fix (the
MEASURED / INFERRED / DECIDED convention, ``CLAUDE-RULES-CANONICAL``).

⚠️ **It is ONE instrument, not the fix.** No checker can tell a correct inference
from an incorrect one, which is the class that caused the incident — this guard
forces a JUDGEMENT to be made and made visible, and cannot judge.

First real run, on the artifact it was written for: **50 open rows required
adjudication; 40 condition the verdicts, 11 were dismissed with stated reasons,
and it caught one the author had missed** (``BL-20260810-NO-STALL-EXIT-CAPITAL-SITS-IN-DEAD-TRADES``
— the row Path B itself descends from).

Exit codes: 0 clean · 1 finding · 2 could not measure (an ABSENT result, not a
clean one).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: artifact -> the tools whose defects it inherits. An artifact is registered
#: here when a decision is read off it; the producer list is what wrote it.
ARTIFACTS: dict[str, tuple[str, ...]] = {
    "docs/research/exit-refinement-coverage.json": (
        "scripts/backtest_system.py",
        "scripts/research/m20_exit_sweep.py",
        "scripts/research/m20_fleet_exit_sweep.py",
        "scripts/research/e35_bracket_geometry_sweep.py",
        "scripts/research/m20_exit_analysis.py",
        "scripts/capital_efficiency.py",
        "src/research/risk_basis.py",
    ),
}

BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
)

#: A row in any of these states is CLOSED and carries no live caveat.
#:
#: ⚠️ MEASURED 2026-09-13 over all 1826 rows in the three backlogs, this set was
#: wrong in BOTH directions: four of its six entries (`closed`, `duplicate`,
#: `withdrawn`, `wontfix`) occur ZERO times anywhere, while the two terminal
#: statuses that DO occur — `wont_fix` (10 rows, with the underscore) and
#: `invalid` (4) — were absent. `wontfix` vs `wont_fix` is the kind of near-miss
#: that reads as correct.
#:
#: Measured impact TODAY is ZERO: no `wont_fix`/`invalid` row names a producer,
#: and the error direction is fail-SAFE (a closed row was over-demanded, never a
#: live caveat dropped). Stated rather than dressed up as a live bug — it is
#: latent, and latent until the first such row exists.
#:
#: The four dead spellings are KEPT, deliberately: they cost nothing, and
#: removing them would silently narrow what counts as closed if another register
#: ever uses one. Additive is the safe direction here.
_CLOSED = {
    "resolved", "superseded", "wont_fix", "invalid",
    # Dead spellings: zero occurrences across all three backlogs (2026-09-13).
    "closed", "wontfix", "duplicate", "withdrawn",
}

_BUCKETS = ("conditions_verdicts", "reviewed_not_conditioning", "pending_adjudication")


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _open_rows_naming_producers(
    backlog_paths: list[Path], producers: tuple[str, ...],
    artifact_rel: str | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(producer_rows, artifact_only_rows)`` — OPEN rows that qualify a read.

    Matches on the tool's BASENAME inside the row's serialised text: rows cite
    tools inconsistently (full path, bare name, backticked), and a basename is
    the one form all of them share.

    ⚠️ THE SECOND RETURN VALUE IS THIS GUARD'S OWN THESIS WITH THE ARROW
    REVERSED, and it was missing until 2026-09-13. The docstring above says a
    defect filed against a PRODUCER never reaches the artifact it produced — and
    the trigger it shipped with sees producer-rows ONLY, so a defect filed
    against the ARTIFACT ITSELF never reached it either.

    MEASURED on ``docs/research/exit-refinement-coverage.json`` (population: all
    1826 rows in the three backlogs, 2026-09-13): **44 OPEN rows name the
    artifact by its exact path, and 29 of them name no producer tool at all** —
    invisible to this guard, which reported `clean` over them. Three are open
    HIGH rows that plainly condition the matrix: the exit-head lever having no
    consumer in ict_scalp, 33 cells graded on legs whose unit cannot run the
    lever, and the missing target-revision column that M20's own done-condition
    needs. A row about a MATRIX names no tool, which is exactly why the
    producer-only trigger cannot see the rows most specific to it.

    They are returned SEPARATELY rather than merged, because enforcing them is
    an arming decision the artifact makes — see :func:`check`.
    """
    producer_rows: dict[str, list[str]] = {}
    artifact_rows: dict[str, list[str]] = {}
    for bp in backlog_paths:
        if not bp.exists():
            continue
        doc = _load(bp)
        items = doc["items"] if isinstance(doc, dict) else doc
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").strip().lower() in _CLOSED:
                continue
            blob = json.dumps(item)
            hit = [p for p in producers if os.path.basename(p) in blob]
            rid = str(item.get("id"))
            if hit:
                producer_rows[rid] = hit
            elif artifact_rel and artifact_rel in blob:
                artifact_rows[rid] = [artifact_rel]
    return producer_rows, artifact_rows


def _adjudicated(caveats: dict[str, Any]) -> set[str]:
    """Return every row id placed in ANY of the three buckets."""
    seen: set[str] = set()
    for bucket in _BUCKETS:
        for entry in caveats.get(bucket) or []:
            rid = entry.get("id") if isinstance(entry, dict) else str(entry)
            if rid:
                seen.add(rid)
    return seen


def check(repo_root: Path, artifacts: dict[str, tuple[str, ...]],
          census: dict[str, tuple[str, list[str]]] | None = None) -> list[str]:
    """Errors that FAIL the run. Unenforced debt is collected into *census*.

    The census is an out-parameter rather than a print so that :func:`main`
    can put the count on the SUMMARY line — a "clean" that omitted it would be
    read as "nothing is unadjudicated", which is the collapsed reading this
    guard exists to prevent one level down.
    """
    errors: list[str] = []
    census = {} if census is None else census
    backlogs = [repo_root / b for b in BACKLOGS]
    for art_rel, producers in artifacts.items():
        art = repo_root / art_rel
        if not art.exists():
            errors.append(f"::error::registered artifact missing: {art_rel}")
            continue
        doc = _load(art)
        caveats = doc.get("known_caveats")
        if not isinstance(caveats, dict):
            errors.append(
                f"::error::{art_rel}: no `known_caveats` block. Every decision read "
                f"off this artifact inherits its producers' open defects; without the "
                f"block a reader cannot see them."
            )
            continue

        rows, artifact_only = _open_rows_naming_producers(
            backlogs, producers, artifact_rel=art_rel)
        adjudicated = _adjudicated(caveats)

        # THE ARMING SWITCH, and it is the artifact's to throw.
        #
        # Widening the demanded set from producer-rows to producer-rows PLUS
        # rows naming the artifact adds 29 rows on the live matrix today. Failing
        # on them the moment this lands would red every PR in the repo until
        # someone made 29 research judgements under time pressure — and this
        # guard's own docstring says "a judgement made to get a guard green is
        # worth less than no judgement". It is also how a guard gets switched off
        # instead of fixed (CLAUDE.md, on check_pr_queue_watch, which arms itself
        # the same way).
        #
        # So until `known_caveats.basis` reads "producers_and_artifact", the
        # wider set is CENSUSED — stated loudly, with its ids, and counted — and
        # not failed. The moment the artifact declares the wider basis, it is
        # enforced, and there is no flag to unset.
        #
        # ⚠️ A CENSUS IS NOT A PASS, and the message says so. The number is
        # printed whether or not anything is failing, so "the guard is clean"
        # can never be read as "nothing is unadjudicated".
        basis = str(caveats.get("basis") or "producers_only").strip()
        armed = basis == "producers_and_artifact"
        if armed:
            rows = {**rows, **artifact_only}
            artifact_only = {}

        missing = sorted(set(rows) - adjudicated)
        if missing:
            errors.append(
                f"::error::{art_rel}: {len(missing)} OPEN backlog row(s) name a "
                f"producing tool of this artifact and are adjudicated in NONE of "
                f"{list(_BUCKETS)}. A defect filed against a producer must reach the "
                f"artifact it produced — place each one, with a reason if it does not "
                f"condition the verdicts:"
            )
            for rid in missing[:20]:
                errors.append(f"  - {rid}  (names: {', '.join(rows[rid])})")
            if len(missing) > 20:
                errors.append(f"  … and {len(missing) - 20} more")

        pend_ids = {
            (e.get("id") if isinstance(e, dict) else str(e))
            for e in (caveats.get("pending_adjudication") or [])
        }
        unadjudicated_artifact = sorted(
            set(artifact_only) - adjudicated - pend_ids)
        if unadjudicated_artifact:
            census[art_rel] = (basis, unadjudicated_artifact)

        # Reasons are load-bearing for the dismissal bucket: "reviewed" with no
        # reason is indistinguishable from "listed to silence the guard".
        for entry in caveats.get("reviewed_not_conditioning") or []:
            if isinstance(entry, dict) and not str(entry.get("why") or "").strip():
                errors.append(
                    f"::error::{art_rel}: row {entry.get('id')} is dismissed as "
                    f"not-conditioning with no `why`. State the reason — an unreasoned "
                    f"dismissal is a silenced guard, not a judgement."
                )

        # The ratchet.
        pending = caveats.get("pending_adjudication") or []
        baseline = caveats.get("pending_baseline")
        if baseline is None:
            errors.append(
                f"::error::{art_rel}: `pending_adjudication` needs a "
                f"`pending_baseline` count, or the debt is unbounded."
            )
        elif len(pending) > int(baseline):
            errors.append(
                f"::error::{art_rel}: pending_adjudication GREW "
                f"({len(pending)} > baseline {baseline}). This list may only shrink — "
                f"adjudicate a row, or lower the baseline when you do."
            )
    return errors


# --------------------------------------------------------------------------
# Self-test: planted controls. A guard whose controls no longer fire must not
# report a clean scan (the collapsed-state-guard lesson).
# --------------------------------------------------------------------------
def _self_test() -> int:
    import tempfile

    fails = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs/research").mkdir(parents=True)
        (root / "docs/claude").mkdir(parents=True)
        art = root / "docs/research/a.json"
        bl = root / "docs/claude/health-review-backlog.json"
        for other in BACKLOGS[1:]:
            (root / other).write_text(json.dumps({"items": []}))

        def write(caveats, status="open"):
            art.write_text(json.dumps({"rows": [], "known_caveats": caveats}))
            bl.write_text(json.dumps({"items": [
                {"id": "BL-X", "status": status,
                 "detail": "something about scripts/backtest_system.py"}]}))

        reg = {"docs/research/a.json": ("scripts/backtest_system.py",)}

        # 1. an unadjudicated open row must FAIL
        write({"pending_adjudication": [], "pending_baseline": 0})
        if not check(root, reg):
            fails.append("control 1: an unadjudicated open producer-row did not fire")

        # 2. adjudicating it clears
        write({"conditions_verdicts": [{"id": "BL-X", "why": "it does"}],
               "pending_adjudication": [], "pending_baseline": 0})
        if check(root, reg):
            fails.append("control 2: an adjudicated row still fires")

        # 3. a CLOSED row is not a live caveat
        write({"pending_adjudication": [], "pending_baseline": 0}, status="resolved")
        if check(root, reg):
            fails.append("control 3: a resolved row is being treated as a live caveat")

        # 4. dismissal without a reason must FAIL
        write({"reviewed_not_conditioning": [{"id": "BL-X"}],
               "pending_adjudication": [], "pending_baseline": 0})
        if not check(root, reg):
            fails.append("control 4: an unreasoned dismissal did not fire")

        # 5. the ratchet: pending above baseline must FAIL
        write({"pending_adjudication": [{"id": "BL-X"}], "pending_baseline": 0})
        if not any("GREW" in e for e in check(root, reg)):
            fails.append("control 5: the ratchet did not fire when pending grew")

        # 6. a missing known_caveats block must FAIL
        art.write_text(json.dumps({"rows": []}))
        if not check(root, reg):
            fails.append("control 6: a missing known_caveats block did not fire")

    # ── controls for the ARTIFACT-NAMING trigger (2026-09-13) ──────────────
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs/research").mkdir(parents=True)
        (root / "docs/claude").mkdir(parents=True)
        art = root / "docs/research/a.json"
        bl = root / "docs/claude/health-review-backlog.json"
        for other in BACKLOGS[1:]:
            (root / other).write_text(json.dumps({"items": []}))
        reg = {"docs/research/a.json": ("scripts/backtest_system.py",)}
        base = {"pending_adjudication": [], "pending_baseline": 0}

        def write2(caveats, status="open"):
            art.write_text(json.dumps({"rows": [], "known_caveats": caveats}))
            # names the ARTIFACT and NO producing tool — the blind spot.
            bl.write_text(json.dumps({"items": [
                {"id": "BL-ART", "status": status,
                 "detail": "the matrix docs/research/a.json has no column for X"}]}))

        # 7. UNARMED: censused, never failed.
        write2(dict(base))
        cen: dict = {}
        if check(root, reg, cen):
            fails.append("control 7: an artifact-naming row FAILED while unarmed — "
                         "it must be censused, not enforced")
        if cen.get("docs/research/a.json", ("", []))[1] != ["BL-ART"]:
            fails.append("control 7b: the artifact-naming row was not censused")

        # 8. ARMED: the same row now FAILS. THE MUTATION CONTROL — it is what
        #    proves the arming switch is real rather than decorative.
        write2({**base, "basis": "producers_and_artifact"})
        if not check(root, reg, {}):
            fails.append("control 8: basis=producers_and_artifact did not ENFORCE "
                         "the artifact-naming row")

        # 9. ARMED + adjudicated clears.
        art.write_text(json.dumps({"rows": [], "known_caveats": {
            **base, "basis": "producers_and_artifact",
            "conditions_verdicts": [{"id": "BL-ART", "why": "it does"}]}}))
        if check(root, reg, {}):
            fails.append("control 9: an adjudicated artifact-naming row still fires "
                         "when armed")

        # 10. NEGATIVE CONTROL — a CLOSED artifact-naming row is neither censused
        #     nor enforced. `wont_fix` is used deliberately: the underscore
        #     spelling was ABSENT from _CLOSED until 2026-09-13, while the
        #     underscore-less `wontfix` (which occurs nowhere in any backlog)
        #     was present. A near-miss that reads as correct.
        write2(dict(base), status="wont_fix")
        cen = {}
        if check(root, reg, cen) or cen:
            fails.append("control 10: a wont_fix row is being treated as a live "
                         "caveat — _CLOSED has lost the underscore spelling again")

    if fails:
        for f in fails:
            print(f"::error::self-test: {f}")
        return 1
    print("artifact-caveat-guard: self-test OK — 10 planted controls all fire")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument(
        "--census", action="store_true",
        help="list the unenforced artifact-naming rows by id. The COUNT is always "
             "printed; the LIST is behind this flag because a 27-row block on every "
             "PR is a second backlog, not a signal — the same reason "
             "checklist_routing_age reports the crossing and not the stock.")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    census: dict[str, tuple[str, list[str]]] = {}
    try:
        errors = check(_REPO_ROOT, ARTIFACTS, census)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::artifact-caveat-guard: could not measure ({exc})")
        return 2

    # ⚠️ PRINTED BEFORE THE VERDICT, AND ON BOTH PATHS. A summary reading
    # "clean" with unenforced debt sitting beside it unmentioned is exactly the
    # unasserted-denominator failure this guard is about.
    for art_rel, (basis, ids) in sorted(census.items()):
        print(f"artifact-caveat-guard: {len(ids)} OPEN row(s) name {art_rel} by "
              f"path, name NO producing tool, and are adjudicated NOWHERE — "
              f"NOT ENFORCED (basis={basis!r}). A row about the ARTIFACT names no "
              f'tool, which is why a producer-only trigger cannot see it. Set '
              f'`known_caveats.basis` to "producers_and_artifact" to enforce them; '
              f"run with --census to list them.")
        if args.census:
            for rid in ids:
                print(f"  - {rid}")

    if errors:
        for e in errors:
            print(e)
        return 1
    total = sum(len(v) for v in ARTIFACTS.values())
    pending = sum(len(ids) for _, ids in census.values())
    tail = (f"; {pending} artifact-naming row(s) unadjudicated and NOT enforced "
            f"(see above)" if pending else "")
    print(f"artifact-caveat-guard: clean — {len(ARTIFACTS)} artifact(s), "
          f"{total} registered producer(s); every open row naming one is "
          f"adjudicated{tail}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
