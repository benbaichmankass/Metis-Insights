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
_CLOSED = {"resolved", "closed", "wontfix", "duplicate", "superseded", "withdrawn"}

_BUCKETS = ("conditions_verdicts", "reviewed_not_conditioning", "pending_adjudication")


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _open_rows_naming_producers(
    backlog_paths: list[Path], producers: tuple[str, ...]
) -> dict[str, list[str]]:
    """Return {row_id: [producer, ...]} for OPEN rows naming any producer.

    Matches on the tool's BASENAME inside the row's serialised text: rows cite
    tools inconsistently (full path, bare name, backticked), and a basename is
    the one form all of them share.
    """
    found: dict[str, list[str]] = {}
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
            if hit:
                found[str(item.get("id"))] = hit
    return found


def _adjudicated(caveats: dict[str, Any]) -> set[str]:
    """Return every row id placed in ANY of the three buckets."""
    seen: set[str] = set()
    for bucket in _BUCKETS:
        for entry in caveats.get(bucket) or []:
            rid = entry.get("id") if isinstance(entry, dict) else str(entry)
            if rid:
                seen.add(rid)
    return seen


def check(repo_root: Path, artifacts: dict[str, tuple[str, ...]]) -> list[str]:
    errors: list[str] = []
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

        rows = _open_rows_naming_producers(backlogs, producers)
        adjudicated = _adjudicated(caveats)

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

    if fails:
        for f in fails:
            print(f"::error::self-test: {f}")
        return 1
    print("artifact-caveat-guard: self-test OK — 6 planted controls all fire")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    try:
        errors = check(_REPO_ROOT, ARTIFACTS)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::artifact-caveat-guard: could not measure ({exc})")
        return 2

    if errors:
        for e in errors:
            print(e)
        return 1
    total = sum(len(v) for v in ARTIFACTS.values())
    print(f"artifact-caveat-guard: clean — {len(ARTIFACTS)} artifact(s), "
          f"{total} registered producer(s); every open row naming one is adjudicated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
