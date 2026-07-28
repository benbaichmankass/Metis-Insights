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

Runs whole-file (not diff-based) so the invariant is enforced on every PR
regardless of what changed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

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

    print("soak-doctrine guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
