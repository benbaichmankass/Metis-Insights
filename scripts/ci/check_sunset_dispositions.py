#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::sunset-disposition-guard (--self-test, then the scan)
"""E3's TEETH — a retirement candidate may not be carried forever, and a
`retired` row must name a thing that is actually GONE.

Phase G of the operating-layer build. `scripts/ops/sunset_pass.py` produces
candidates; `docs/claude/SUNSET-DISPOSITIONS.json` records answers; this is what
makes not answering cost something.

WHY A GUARD AND NOT ANOTHER REGISTER
====================================
Because a register alone is the thing being fixed. Measured 2026-09-01:
`scripts/ci/check_unwired_artifacts.py` — a correct, well-built detector —
reports **115 findings and exits 0**. It has reported them for weeks. Nothing
in this repo has ever retired a skill, register, workflow or guard, and there
have been 6 strategy retirements ever, none in five weeks against 45 live legs.
Detection was never the missing half.

THE THREE RULES, and why each is the one that matters
=====================================================

**1 · A `retired` row is VERIFIED, not trusted.** If a row says `retired` and
its ``target`` still exists on disk, this FAILS. The build plan states the rule
in its own words — *"a retirement is done when the old thing is GONE, not when
the replacement ships"* — and names the exact failure mode: the new surface
lands, the old one is left "for now", and a session must now read both. A
register that lets a row claim `retired` while the file sits there would
reproduce that inside the mechanism built to end it.

**2 · A `keep` EXPIRES.** A keep carries a ``reason`` and a ``review_by``; past
that date it fails. A permanent exemption is how a candidate becomes furniture.
This is the same discipline `wip-ceiling-exception.yaml` applies to the WIP cap
— an exception that never expires is a cap raise wearing an exception's clothes.

**3 · An undispositioned candidate ESCALATES BY CARRY COUNT**, rather than
failing on sight. Borrowed from `check_operator_owed.py`, whose insight is that
*carrying an item forward unmoved is itself the measurement*. Failing every
undispositioned candidate on day one would put ~10 red rows on the first PR that
lands the mechanism — a wall, not a forcing function, and a wall gets disabled.
Instead a candidate must survive :data:`CARRY_ESCALATION_PASSES` consecutive
sunset passes with no answer. **It therefore starts green by construction** (one
pass exists as this ships) **and gains teeth as passes accrue.**

⚠️ WHAT THIS GUARD DOES NOT DO. It never decides a retirement, never edits
config, and never removes anything. Retiring a live strategy leg is Tier-3; a
`retire_proposed` row with ``operator_decision: pending`` is a legitimate
terminal state here for as long as the operator has not answered — the guard
requires an ANSWER TO EXIST, not a particular answer. Conflating "nobody has
dispositioned this" with "the operator has not decided yet" would punish the one
state the tier gates require.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "docs" / "claude" / "SUNSET-DISPOSITIONS.json"
SUNSET_ROOT = REPO / "comms" / "sunset"

#: How many CONSECUTIVE most-recent sunset passes a candidate may appear in with
#: no disposition before this fails. CHOSEN, not measured — one pass exists as
#: this ships, so any value >= 2 starts green. 3 is a fortnight at the weekly
#: cadence: long enough that a candidate is not a one-off reading, short enough
#: that it cannot become furniture.
CARRY_ESCALATION_PASSES = 3

VALID = {"retired", "retire_proposed", "keep", "wire"}
OPERATOR_DECISIONS = {"pending", "approved", "refused", "not_required"}


def _load(p: Path) -> Optional[Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- we could not look
        return None


def recent_passes(root: Path, n: int) -> Tuple[List[dict], str]:
    """The `n` newest sunset passes, newest first, plus a read state.

    ⚠️ THREE STATES, NEVER COLLAPSED. ``no_passes`` (none has ever run — which is
    NOT "no candidates"), ``unreadable`` (the directory exists and nothing in it
    parsed), and ``read``. A guard that treated an unrunnable pass as an empty
    one would go permanently green the moment the generator broke, which is the
    "green that checked nothing" this repo has a rule about.
    """
    if not root.is_dir():
        return [], "no_passes"
    days = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    if not days:
        return [], "no_passes"
    out: List[dict] = []
    for d in days[:n]:
        doc = _load(d / "INDEX.json")
        if isinstance(doc, dict) and isinstance(doc.get("rows"), list):
            out.append(doc)
    return (out, "read") if out else ([], "unreadable")


def carried_candidates(passes: List[dict], need: int) -> List[str]:
    """Ids that are a `retire_candidate` in EVERY one of the `need` newest passes.

    Requires `need` passes to exist at all — with fewer, nothing has been
    carried long enough to escalate, and saying so is different from saying
    nothing is carried.
    """
    if len(passes) < need:
        return []
    sets = [{r["id"] for r in p.get("rows", [])
             if isinstance(r, dict) and r.get("verdict") == "retire_candidate"}
            for p in passes[:need]]
    return sorted(set.intersection(*sets)) if sets else []


def _as_date(v: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


def audit(register: Optional[dict], passes: List[dict], pass_state: str,
          *, repo: Path = REPO, today: Optional[date] = None) -> Tuple[List[str], List[str]]:
    """(failures, notes). Never raises: a guard that dies reports nothing."""
    today = today or datetime.now(timezone.utc).date()
    fail: List[str] = []
    notes: List[str] = []
    _REG_REL = "docs/claude/SUNSET-DISPOSITIONS.json"

    if register is None:
        return ([f"{_REG_REL} is missing or unparseable. That is not "
                 f"'no candidates' — it is the register this guard exists to read."], notes)

    rows = register.get("dispositions")
    if not isinstance(rows, list):
        return ([f"{_REG_REL} has no `dispositions` list."], notes)

    by_id: Dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict) or not r.get("id"):
            fail.append(f"a disposition row has no `id`: {json.dumps(r)[:120]}")
            continue
        rid = str(r["id"])
        if rid in by_id:
            fail.append(f"`{rid}` is dispositioned twice — one answer per candidate.")
        by_id[rid] = r

        d = r.get("disposition")
        if d not in VALID:
            fail.append(f"`{rid}` carries disposition `{d}`, which is not one of "
                        f"{sorted(VALID)}.")
            continue
        if not str(r.get("reason") or "").strip():
            fail.append(f"`{rid}` is dispositioned `{d}` with no `reason`. A disposition "
                        f"without a reason is a status, not a decision.")

        od = r.get("operator_decision")
        if od is not None and od not in OPERATOR_DECISIONS:
            fail.append(f"`{rid}` carries operator_decision `{od}`, not one of "
                        f"{sorted(OPERATOR_DECISIONS)}.")

        # RULE 1 — `retired` means GONE, and it is verified.
        if d == "retired":
            tgt = r.get("target")
            if not tgt:
                fail.append(f"`{rid}` claims `retired` but names no `target`, so the "
                            f"claim cannot be checked. A retirement nobody can verify "
                            f"is the state this rule exists to refuse.")
            elif (repo / str(tgt)).exists():
                fail.append(f"`{rid}` claims `retired` but `{tgt}` STILL EXISTS. "
                            f"A retirement is done when the old thing is GONE, not when "
                            f"the replacement ships.")
            else:
                notes.append(f"verified retired: `{tgt}` is absent.")

        # RULE 2 — a `keep` expires.
        if d == "keep":
            rb = _as_date(r.get("review_by"))
            if rb is None:
                fail.append(f"`{rid}` is `keep` with no readable `review_by`. A keep is a "
                            f"decision with an expiry, not a permanent exemption.")
            elif rb < today:
                fail.append(f"`{rid}` is `keep` whose `review_by` ({rb}) has passed. "
                            f"Re-decide it or retire it — do not extend it silently.")

        # A Tier-3 proposal the operator has APPROVED but which has not been
        # enacted is a decision left on the floor, which is its own failure.
        if d == "retire_proposed" and r.get("operator_decision") == "approved":
            notes.append(f"`{rid}`: operator APPROVED the retirement — enact it and flip "
                         f"the row to `retired`.")

    # RULE 3 — carry escalation.
    if pass_state == "unreadable":
        fail.append("`comms/sunset/` exists but no INDEX.json parsed. The pass is broken; "
                    "that is NOT the same as having no candidates.")
    carried = carried_candidates(passes, CARRY_ESCALATION_PASSES)
    undispositioned = [c for c in carried if c not in by_id]
    for c in undispositioned:
        fail.append(f"`{c}` has been a retirement candidate in the last "
                    f"{CARRY_ESCALATION_PASSES} sunset passes with NO disposition. "
                    f"Answer it in {_REG_REL} — `retire_proposed`, "
                    f"`keep` (with a review_by) or `wire` are all valid answers; "
                    f"silence is not.")

    notes.append(f"population: {len(rows)} disposition(s); {len(passes)} sunset pass(es) "
                 f"read (state `{pass_state}`); {len(carried)} candidate(s) carried "
                 f"through {CARRY_ESCALATION_PASSES} passes, {len(undispositioned)} of "
                 f"them unanswered.")
    return fail, notes


# ---------------------------------------------------------------------------
def _self_test() -> int:
    """Every failure path, fired. A guard whose failure branch never runs is
    indistinguishable from one that always passes."""
    import tempfile
    ok, checks = 0, []
    T = date(2026, 9, 1)

    def _p(ids):  # a sunset pass naming `ids` as candidates
        return {"rows": [{"id": i, "verdict": "retire_candidate"} for i in ids]}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts").mkdir()
        (root / "scripts" / "still_here.py").write_text("x\n")

        f, _ = audit({"dispositions": [
            {"id": "tool:x", "target": "scripts/still_here.py", "disposition": "retired",
             "reason": "gone"}]}, [], "no_passes", repo=root, today=T)
        checks.append(("`retired` on a file that STILL EXISTS fails",
                       any("STILL EXISTS" in x for x in f)))

        f, _ = audit({"dispositions": [
            {"id": "tool:y", "target": "scripts/absent.py", "disposition": "retired",
             "reason": "gone"}]}, [], "no_passes", repo=root, today=T)
        checks.append(("`retired` on a file that is genuinely gone passes", not f))

        f, _ = audit({"dispositions": [
            {"id": "a", "disposition": "keep", "reason": "r", "review_by": "2026-08-01"}]},
            [], "no_passes", repo=root, today=T)
        checks.append(("an EXPIRED `keep` fails", any("has passed" in x for x in f)))

        f, _ = audit({"dispositions": [
            {"id": "a", "disposition": "keep", "reason": "r", "review_by": "2026-12-01"}]},
            [], "no_passes", repo=root, today=T)
        checks.append(("an in-date `keep` passes", not f))

        f, _ = audit({"dispositions": [
            {"id": "a", "disposition": "keep", "reason": "r"}]},
            [], "no_passes", repo=root, today=T)
        checks.append(("a `keep` with NO review_by fails", any("no readable" in x for x in f)))

        f, _ = audit({"dispositions": [
            {"id": "a", "disposition": "retire_proposed", "reason": ""}]},
            [], "no_passes", repo=root, today=T)
        checks.append(("a disposition with no REASON fails",
                       any("no `reason`" in x for x in f)))

        # carry escalation
        many = [_p(["strategy:z"])] * CARRY_ESCALATION_PASSES
        f, _ = audit({"dispositions": []}, many, "read", repo=root, today=T)
        checks.append((f"a candidate carried {CARRY_ESCALATION_PASSES} passes unanswered fails",
                       any("NO disposition" in x for x in f)))

        few = [_p(["strategy:z"])] * (CARRY_ESCALATION_PASSES - 1)
        f, _ = audit({"dispositions": []}, few, "read", repo=root, today=T)
        checks.append(("...and fewer passes than the threshold does NOT fail", not f))

        f, _ = audit({"dispositions": [
            {"id": "strategy:z", "disposition": "retire_proposed",
             "operator_decision": "pending", "reason": "tier-3, operator's call"}]},
            many, "read", repo=root, today=T)
        checks.append(("a Tier-3 proposal PENDING with the operator is a valid answer",
                       not f))

        f, _ = audit(None, [], "no_passes", repo=root, today=T)
        checks.append(("an unreadable register fails rather than passing empty",
                       any("missing or unparseable" in x for x in f)))

        f, _ = audit({"dispositions": []}, [], "unreadable", repo=root, today=T)
        checks.append(("a BROKEN sunset pass fails rather than reading as 'no candidates'",
                       any("pass is broken" in x for x in f)))

        f, _ = audit({"dispositions": [
            {"id": "a", "disposition": "keep", "reason": "r", "review_by": "2026-12-01"},
            {"id": "a", "disposition": "wire", "reason": "r"}]},
            [], "no_passes", repo=root, today=T)
        checks.append(("the same id dispositioned twice fails",
                       any("dispositioned twice" in x for x in f)))

    for name, good in checks:
        print(f"  {'ok  ' if good else 'FAIL'}  {name}")
        ok += bool(good)
    print(f"self-test: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    passes, state = recent_passes(SUNSET_ROOT, CARRY_ESCALATION_PASSES)
    fail, notes = audit(_load(REGISTER), passes, state)
    for n in notes:
        print(f"  note: {n}")
    if not fail:
        print("sunset-disposition guard: OK — every carried candidate has a written "
              "answer, every `retired` row names something genuinely gone, and no "
              "`keep` has expired.")
        return 0
    for x in fail:
        print(f"  {x}")
    print(f"\n::error::sunset-disposition guard: {len(fail)} problem(s). E3 is the phase "
          f"that makes the system REMOVE rather than only add; a candidate nobody answers "
          f"is how that stops happening.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
