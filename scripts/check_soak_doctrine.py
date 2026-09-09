#!/usr/bin/env python3
"""Guard: the soak doctrine (offline edge, live mechanics) must stay intact.

Enforces ``docs/CLAUDE-RULES-CANONICAL.md`` § "Promotion evidence — offline
edge, live mechanics": a paper/shadow soak validates **MECHANICS ONLY** (a
couple of live executions, accruing in **hours**), never gathers
performance/edge over **days–weeks**. The edge is proven OFFLINE (a
live-faithful backtest + the backfill / live-simulator over deep history)
BEFORE anything is wired to a soak account.

This guard exists because the rule kept getting reverted: prior Claude
sessions repeatedly rewrote the ``new-strategy`` skill (the operational
touchpoint a session actually follows for a strategy soak) back to a
"days–weeks soak to confirm the edge" framing — the exact drift that made the
operator watch alt legs "soak for 2–3 weeks" when they should have needed only
1–2 mechanics-confirming executions. Prose fixes alone did not hold; this is
the mechanical backstop.

Fails (exit 1) if:
  A. The canonical binding rule marker is missing from CLAUDE-RULES-CANONICAL.md,
     or the rule no longer states it applies to STRATEGY legs (not just ML).
  B. The new-strategy skill stops framing the soak as a MECHANICS check, or
     drops the cross-reference to the canonical rule.
  C. Any soak-doctrine skill (new-strategy, backtesting) reintroduces the
     banned "days–weeks soak-to-prove-edge" idiom.
  D. A DECISION-GATE SURFACE grades a verdict off LIVE-ACCRUAL evidence
     (see below) beyond the count recorded in the exception ledger.

Runs whole-file (not diff-based) so the invariant is enforced on every PR
regardless of what changed.

CHECK D — WHY IT EXISTS, AND WHY A–C COULD NOT HAVE CAUGHT IT
-------------------------------------------------------------
Checks A–C read exactly three files: ``docs/CLAUDE-RULES-CANONICAL.md``,
``.claude/skills/new-strategy/SKILL.md`` and ``.claude/skills/backtesting/
SKILL.md``. That is the guard's ENTIRE reach, and it is a measured fact about
code paths, not merely about which names appear in the source: no other path
opens any other file. So the guard enforced the doctrine's PROSE and reached
none of the CODE that decides anything.

Measured 2026-09-09: ``scripts/ml/strategy_review_packet.py::decide`` — the M7
daily strategy-review gate — graded **52 of 52 legs ``hold`` on every day it
has a committed record for** because it withholds every verdict for want of
live in-window closes. Population, stated: the **7** indexes committed under
``comms/strategy_reviews/`` at ``a77afb8e`` (2026-09-01..09-07), each reading
``graded 52 / actionable 0 / by_action {hold: 52}``, and ``below_evidence_floor
52`` on the six that carry the field. ⚠️ The brief that commissioned this check
said EIGHT days through 09-08; only seven are in the repo, and this docstring
says seven because that is what was counted here. The finding is identical
either way, and the smaller number is the measured one. Its ``Override 4`` is a literal
``shadow_soak_days < 14`` promotion gate — the exact "N days at stage"
construct the canonical rule's clause 3 names as "a policy artifact, not
evidence". The rule has been binding since 2026-07-26 and was contradicted for
those days **unseen**, because the guard that enforces it did not reach the
surface. Operator, 2026-09-09: *"we do NOT rely on live soaking for validating
strategies, only mechanics ... I don't know how many more times I can explain
this to various claudes"*. A further prose reminder is a NON-FIX; this check is
the mechanism.

WHAT IT DETECTS. Not a keyword. For each registered gate function it builds the
AST, resolves LOCAL ALIASES of the banned evidence names to a fixpoint (so
``n = headline.n_closed`` makes ``n`` banned too — a rename cannot evade it),
and reports every ``if``/``elif`` whose TEST reads a live-accrual name and
whose BODY assigns a verdict. That is the canonical rule stated mechanically:
**a verdict must not be control-dependent on calendar/live accrual.**

⚠️ IT DELIBERATELY FLAGS FORCED ``hold`` TOO, not only KILL/PROMOTE. Withholding
a verdict for want of accrual IS the prohibited gate — it is the precise shape
of the 52/52 hold — so a check that only watched the loud verdicts would have
stayed green through every one of those days.

WHY A RATCHET AND NOT A HARD FAIL. The seven live branches are Tier-3 (they
decide what a KILL/DEMOTE rests on) and cannot be changed by a session; a guard
that failed CI on day one would be disabled rather than fixed — the
``check_pr_queue_watch.py`` lesson. So known branches are declared in
``docs/claude/soak-doctrine-exceptions.json`` with a per-surface COUNT, and the
check is an EXACT match: one more than declared FAILS (a new violation), one
fewer FAILS as a stale ledger (so the number can only be walked DOWN as the
Tier-3 fix lands, never silently back up). Raising a count is a visible diff
that must name a reason and an EXISTING backlog row — the entry is VERIFIED,
not presence-only, because a ledger cheaper to lie to than to satisfy is worse
than no ledger (the ``new-table-wiring-guard`` lesson).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]

# The canonical markers that MUST persist (deleting the rule fails CI).
CANON = ROOT / "docs" / "CLAUDE-RULES-CANONICAL.md"
CANON_MECHANICS_MARKER = "A live soak may only prove serving-MECHANICS"
# The generalization to strategy legs (added 2026-07-28) must stay put.
CANON_STRATEGY_MARKER = "applies to STRATEGY legs"

# The skills that govern how a session runs a soak.
SOAK_SKILLS = ["new-strategy", "backtesting"]
# The new-strategy skill owns the lifecycle text, so it must carry the doctrine.
SKILL_MECHANICS_MARKER = "MECHANICS"
SKILL_CANON_REF = "offline edge, live mechanics"

# Banned "soak proves the edge over days–weeks" idioms (case-insensitive).
BANNED = [
    re.compile(r"confirm the backtest\s*\(days", re.I),
    re.compile(r"shadow data mature\s*\(days", re.I),
    re.compile(
        r"(soak|paper data|shadow data|live paper|paper[- ]?soak|live data)"
        r"[^.\n]{0,80}days[^.\n]{0,8}weeks",
        re.I,
    ),
    re.compile(
        r"days[^.\n]{0,8}weeks[^.\n]{0,80}"
        r"(confirm|prove|mature|edge|performance|track record)",
        re.I,
    ),
]


# ---------------------------------------------------------------------------
# Check D — a verdict must not be control-dependent on live/calendar accrual.
# ---------------------------------------------------------------------------

#: The exception ledger. Per-surface COUNTS of already-known banned branches.
EXCEPTIONS = ROOT / "docs" / "claude" / "soak-doctrine-exceptions.json"

#: Backlog files a ledger entry's ``backlog_row`` may be VERIFIED against. The
#: entry is checked, not merely present — see the module docstring.
BACKLOGS = [
    ROOT / "docs" / "claude" / "health-review-backlog.json",
    ROOT / "docs" / "claude" / "performance-review-backlog.json",
    ROOT / "docs" / "claude" / "ml-review-backlog.json",
    ROOT / "docs" / "claude" / "research-review-backlog.json",
]

#: Gate surfaces this check reaches: module path -> the functions that emit a
#: verdict. ⚠️ ADDING A NEW DECISION GATE MEANS ADDING IT HERE. A gate absent
#: from this map is UNREACHED, which is exactly the hole check D was written to
#: close — do not read a clean run as covering a surface not listed.
GATE_SURFACES: Dict[str, Tuple[str, ...]] = {
    "scripts/ml/strategy_review_packet.py": ("decide",),
}

#: Seed names denoting LIVE-OUTCOME or CALENDAR accrual. Local aliases are
#: resolved from these to a fixpoint, so renaming the variable does not evade
#: the check.
ACCRUAL_SEEDS: Set[str] = {
    "n_closed",                    # live closes in the review window
    "MIN_CLOSED_FOR_ACTION",       # the floor those closes are compared to
    "below_evidence_floor",
    "shadow_soak_days",            # "N days at stage" — clause 3, verbatim
    "window_days",
    "observed_close_rate_per_day",  # evidence-horizon outputs: a projection of
    "days_to_floor_point",          # WHEN accrual would suffice is the same
    "days_to_floor_optimistic",     # instrument one level up, and must never
    "days_to_floor_conservative",   # become an input to a verdict.
}

#: The attribute a gate assigns to record its verdict.
VERDICT_ATTR = "action"


def _rel(path: Path) -> str:
    """Repo-relative display path, tolerating a path outside the repo.

    ``Path.relative_to`` RAISES off-root, and this is used inside the guard's
    own error messages — so the failure path would crash instead of reporting,
    turning a finding into a traceback. Found by
    ``tests/test_soak_doctrine_guard.py``.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _names_in(node: ast.AST) -> Set[str]:
    """Every identifier and attribute name read anywhere under *node*."""
    out: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _accrual_closure(fn: ast.FunctionDef) -> Set[str]:
    """ACCRUAL_SEEDS plus every local alias of one, to a fixpoint."""
    banned = set(ACCRUAL_SEEDS)
    for _ in range(16):  # bounded; converges far sooner in practice
        grew = False
        for node in ast.walk(fn):
            targets: List[ast.expr] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            if value is None:
                continue
            if not (_names_in(value) & banned):
                continue
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in banned:
                    banned.add(t.id)
                    grew = True
        if not grew:
            break
    return banned


def _assigns_verdict(body: List[ast.stmt]) -> bool:
    """True if *body* assigns the verdict attribute (e.g. ``decision.action``)."""
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == VERDICT_ATTR:
                        return True
            elif isinstance(node, ast.AnnAssign):
                t = node.target
                if isinstance(t, ast.Attribute) and t.attr == VERDICT_ATTR:
                    return True
    return False


def find_accrual_gated_verdicts(
    source: str, func_names: Tuple[str, ...]
) -> List[Tuple[int, str, str]]:
    """Return (lineno, function, evidence-names) for each banned branch.

    A branch is banned when its TEST reads a live/calendar-accrual name and its
    BODY assigns a verdict — i.e. the verdict is control-dependent on accrual.
    """
    tree = ast.parse(source)
    wanted = set(func_names)
    hits: List[Tuple[int, str, str]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in wanted:
            continue
        banned = _accrual_closure(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            used = _names_in(node.test) & banned
            if used and _assigns_verdict(node.body):
                hits.append((node.lineno, fn.name, ", ".join(sorted(used))))
    return sorted(set(hits))


def _load_backlog_ids() -> Set[str]:
    ids: Set[str] = set()
    for path in BACKLOGS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = data.get("items") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                ids.add(row["id"])
    return ids


def check_gate_surfaces() -> Tuple[List[str], List[str]]:
    """Check D. Returns (errors, notices)."""
    errors: List[str] = []
    notices: List[str] = []

    ledger: Dict[str, dict] = {}
    if EXCEPTIONS.exists():
        try:
            raw = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
            ledger = {
                k: v for k, v in (raw.get("surfaces") or {}).items()
                if isinstance(v, dict)
            }
        except (OSError, ValueError) as exc:
            errors.append(
                f"{_rel(EXCEPTIONS)} is unreadable ({exc}). An "
                "unreadable ledger is 'we could not look', never 'nothing is "
                "declared' — refusing rather than passing."
            )
            return errors, notices

    backlog_ids = _load_backlog_ids()

    for rel, func_names in GATE_SURFACES.items():
        path = ROOT / rel
        if not path.exists():
            errors.append(
                f"{rel} is registered in GATE_SURFACES but does not exist. A "
                "gate surface that vanished must be de-registered deliberately, "
                "not skipped silently."
            )
            continue
        try:
            hits = find_accrual_gated_verdicts(
                path.read_text(encoding="utf-8"), func_names
            )
        except SyntaxError as exc:
            errors.append(f"{rel} could not be parsed for check D: {exc}")
            continue

        entry = ledger.get(rel, {})
        allowed = entry.get("known_accrual_gated_branches")
        if not isinstance(allowed, int):
            allowed = 0
        found = len(hits)

        if found > allowed:
            detail = "; ".join(f"line {ln} ({fn}, reads: {ev})" for ln, fn, ev in hits)
            errors.append(
                f"{rel}: {found} verdict branch(es) are control-dependent on "
                f"live/calendar accrual, but the ledger declares {allowed}. "
                f"Branches: {detail}. A verdict must not be gated on accrual "
                "(CLAUDE-RULES-CANONICAL.md § 'Promotion evidence — offline "
                "edge, live mechanics', clause 3: 'No gate may require "
                "calendar-time accrual to prove edge'). Prove the edge OFFLINE. "
                f"If a branch here is genuinely new and Tier-3-blocked, raising "
                f"{_rel(EXCEPTIONS)} needs a reason AND an existing "
                "backlog row — it is not a way to silence the check."
            )
        elif found < allowed:
            errors.append(
                f"{rel}: the ledger declares {allowed} accrual-gated verdict "
                f"branch(es) but only {found} remain. The ledger is STALE — "
                f"lower it to {found}. It ratchets DOWN only, so a repaired "
                "surface can never leave headroom for the violation to return "
                "unseen."
            )
        elif found:
            row = entry.get("backlog_row")
            if not isinstance(row, str) or row not in backlog_ids:
                errors.append(
                    f"{rel}: the ledger declares {found} known accrual-gated "
                    f"branch(es) but its backlog_row {row!r} is not a row in any "
                    "review backlog. The exception is VERIFIED, not "
                    "presence-only: an entry naming a row that does not exist "
                    "is how a real finding gets silenced."
                )
            else:
                detail = "; ".join(f"L{ln} ({ev})" for ln, fn, ev in hits)
                notices.append(
                    f"{rel}: {found} KNOWN accrual-gated verdict branch(es) "
                    f"[{detail}] — declared in "
                    f"{_rel(EXCEPTIONS)}, tracked by {row}. This is "
                    "a live contradiction of the canonical rule, held open "
                    "because the repair is Tier-3. It is NOT compliance."
                )

    return errors, notices


def main() -> int:
    errors: list[str] = []

    canon = CANON.read_text(encoding="utf-8") if CANON.exists() else ""
    if CANON_MECHANICS_MARKER not in canon:
        errors.append(
            "CLAUDE-RULES-CANONICAL.md is missing the binding rule marker "
            f"'{CANON_MECHANICS_MARKER}' (§ 'Promotion evidence — offline edge, "
            "live mechanics'). The soak-is-mechanics-only rule must not be removed."
        )
    if CANON_STRATEGY_MARKER not in canon:
        errors.append(
            "CLAUDE-RULES-CANONICAL.md § 'Promotion evidence' must state that the "
            f"rule '{CANON_STRATEGY_MARKER}' (not just ML models) — a strategy "
            "paper-soak is a mechanics check, not a performance test."
        )

    for name in SOAK_SKILLS:
        p = ROOT / ".claude" / "skills" / name / "SKILL.md"
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        for rx in BANNED:
            m = rx.search(txt)
            if m:
                errors.append(
                    f".claude/skills/{name}/SKILL.md reintroduces the banned "
                    f"soak-as-performance idiom: ...{m.group(0)[:90].strip()}... — "
                    "a soak proves MECHANICS (1–2 live executions, hours), not edge "
                    "over days–weeks. The edge is decided OFFLINE before the soak."
                )
        if name == "new-strategy":
            if SKILL_MECHANICS_MARKER not in txt:
                errors.append(
                    ".claude/skills/new-strategy/SKILL.md must frame the paper/shadow "
                    "soak as a MECHANICS check (the word 'MECHANICS' is missing from "
                    "the lifecycle)."
                )
            if SKILL_CANON_REF not in txt:
                errors.append(
                    ".claude/skills/new-strategy/SKILL.md must cross-reference the "
                    f"canonical rule ('{SKILL_CANON_REF}')."
                )

    d_errors, d_notices = check_gate_surfaces()
    errors.extend(d_errors)

    if errors:
        print("SOAK-DOCTRINE GUARD FAILED:\n")
        for e in errors:
            print("  - " + e)
        print(
            "\nDoctrine (CLAUDE-RULES-CANONICAL.md § 'Promotion evidence — offline "
            "edge, live mechanics'): performance/edge is proven OFFLINE (a "
            "live-faithful backtest + the backfill/live-simulator over deep history) "
            "BEFORE any soak. A soak validates MECHANICS only — that the live "
            "executions match the simulator — needing 1–2 executions (hours). Never "
            "frame a soak as gathering performance over days–weeks; if a leg reaches "
            "soak without an adequate offline edge proof, the gap is the missing "
            "backtest, not more soak time."
        )
        return 1

    for n in d_notices:
        print("soak-doctrine: KNOWN OPEN VIOLATION — " + n)
    print("soak-doctrine guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
