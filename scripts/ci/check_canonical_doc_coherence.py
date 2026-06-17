#!/usr/bin/env python3
"""canonical-doc-coherence — mechanical guard against governance-doc drift.

This is the "teeth" behind the doc-freshness skill. It catches the exact
classes of drift that accumulated silently and produced the recurring
operator pain (stale VM topology, removed gates described as live, the
7-stage ML ladder, and the two hierarchy lists falling out of sync).

It is intentionally simple and stdlib-only so it can run in CI and locally
over the working tree. Each check prints PASS/FAIL lines; the process exits
non-zero if any check fails.

Run:  python scripts/ci/check_canonical_doc_coherence.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Files a live session follows day-to-day. Drift here is what misleads Claude.
ACTIVE_DOCS = [
    "CLAUDE.md",
    "docs/CLAUDE-RULES-CANONICAL.md",
    "docs/ARCHITECTURE-CANONICAL.md",
    "docs/github-actions-workflows.md",
    "docs/claude/system-actions.md",
    "docs/claude/vm-operator-mode.md",
    "docs/claude/trainer-vm-mode.md",
    "docs/claude/diag-relay.md",
    "docs/claude/deployment-ops.md",
]


def _active_files() -> list[Path]:
    files = [ROOT / p for p in ACTIVE_DOCS]
    files += sorted((ROOT / ".claude" / "skills").rglob("SKILL.md"))
    files += sorted((ROOT / ".claude" / "commands").glob("*.md"))
    return [f for f in files if f.exists()]


def _iter_windows(files: list[Path], radius: int = 2):
    """Yield (rel, lineno, line, context) where context is the line plus
    `radius` neighbours on each side joined — so a historical/removal marker
    on an adjacent wrapped line still suppresses a false positive."""
    for f in files:
        rel = f.relative_to(ROOT)
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            lo = max(0, i - 1 - radius)
            hi = min(len(lines), i + radius)
            context = " ".join(lines[lo:hi])
            yield rel, i, line, context


def check_dead_vm_ip() -> list[str]:
    """The terminated x86 micro must never appear as the *current* live VM.

    Allowed only on lines explicitly framing it as past/historical.
    """
    DEAD_IP = "158.178.210.252"
    OLD_IP = "129.159.83.68"  # an even-older pre-micro live IP
    HIST = re.compile(
        r"terminat|retir|histor|pre-2026-06-14|migration source|decommiss|"
        r"supersed|old x86|former|was the|no longer|micro\b",
        re.I,
    )
    fails = []
    for rel, i, line, context in _iter_windows(_active_files()):
        if DEAD_IP in line or OLD_IP in line:
            if not HIST.search(context):
                fails.append(f"{rel}:{i}: dead VM IP without historical marker -> {line.strip()}")
    return fails


def check_removed_gates() -> list[str]:
    """Removed feature gates must only appear flagged as removed/historical."""
    GATES = re.compile(
        r"MULTI_SYMBOL_ENABLED|NEWS_ENABLED|NAKED_POSITION_AUTOPROTECT|"
        r"MONITOR_RECONCILE_ENABLED|POSITION_NETTING_GUARD_ENABLED|"
        r"POSITION_NETTING_GUARD_ACCOUNTS",
    )
    OK = re.compile(
        r"remov|retir|supersed|histor|ignored|baseline|no longer|legacy|"
        r"deprecat|example|stranded|unconditional|purge",
        re.I,
    )
    fails = []
    for rel, i, line, context in _iter_windows(_active_files()):
        if GATES.search(line) and not OK.search(context):
            fails.append(f"{rel}:{i}: removed gate described as live -> {line.strip()}")
    return fails


def check_seven_stage_ladder() -> list[str]:
    """No 7-stage ML ladder in the skill/command catalog."""
    SEVEN = re.compile(r"7[- ]stage|seven[- ]stage", re.I)
    # Allowed when the mention is a legacy-alias note or meta text (e.g. this
    # guard's own description, or "the legacy 7-stage names alias to ...").
    OK = re.compile(
        r"legacy|alias|collaps|former|\bold\b|should be empty|stale 7-stage|"
        r"detect|guard|aliases to",
        re.I,
    )
    fails = []
    cat = sorted((ROOT / ".claude").rglob("*.md"))
    for f in cat:
        rel = f.relative_to(ROOT)
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if SEVEN.search(line) and not OK.search(line):
                fails.append(f"{rel}:{i}: stale 7-stage ladder -> {line.strip()}")
    return fails


_HIER_KEYS = [
    ("rules", re.compile(r"CLAUDE-RULES-CANONICAL", re.I)),
    ("architecture", re.compile(r"ARCHITECTURE-CANONICAL", re.I)),
    ("roadmap", re.compile(r"ROADMAP", re.I)),
    ("sprintlog", re.compile(r"sprint log|sprint-logs", re.I)),
    ("skills", re.compile(r"\.claude/skills|^.*\bSkills\b", re.I)),
    ("claudemd", re.compile(r"this file|root .?CLAUDE\.md|\bCLAUDE\.md\b", re.I)),
    ("implspecs", re.compile(r"implementation spec", re.I)),
    ("historical", re.compile(r"docs/claude|historical", re.I)),
]


def _normalize_item(text: str) -> str | None:
    for key, pat in _HIER_KEYS:
        if pat.search(text):
            return key
    return None


def _extract_hierarchy(path: Path, heading_substr: str) -> list[str] | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and heading_substr.lower() in line.lower():
            start = i
            break
    if start is None:
        return None
    seq: list[str] = []
    for line in lines[start + 1:]:
        m = re.match(r"\s*\d+\.\s+(.*)", line)
        if m:
            key = _normalize_item(m.group(1))
            if key:
                seq.append(key)
            continue
        # A numbered list item may wrap onto indented continuation lines —
        # those start with whitespace and must NOT end the list. The list
        # ends at the first non-indented, non-numbered prose line (or heading)
        # once we have started collecting items.
        if seq and line and not line[0].isspace():
            break
    return seq


def check_hierarchy_mirror() -> list[str]:
    """CLAUDE.md Instruction hierarchy must mirror canonical Document Priority."""
    claude = _extract_hierarchy(ROOT / "CLAUDE.md", "Instruction hierarchy")
    canon = _extract_hierarchy(ROOT / "docs/CLAUDE-RULES-CANONICAL.md", "Document Priority")
    fails = []
    if not claude:
        fails.append("CLAUDE.md: could not parse § Instruction hierarchy")
    if not canon:
        fails.append("docs/CLAUDE-RULES-CANONICAL.md: could not parse § Document Priority")
    if claude and canon and claude != canon:
        fails.append(
            "hierarchy mismatch:\n"
            f"    CLAUDE.md           -> {claude}\n"
            f"    CLAUDE-RULES-CANON  -> {canon}"
        )
    return fails


CHECKS = [
    ("dead VM IP single-source", check_dead_vm_ip),
    ("removed gates not described as live", check_removed_gates),
    ("no 7-stage ML ladder in catalog", check_seven_stage_ladder),
    ("instruction-hierarchy mirror", check_hierarchy_mirror),
]


def main() -> int:
    total = 0
    for name, fn in CHECKS:
        fails = fn()
        if fails:
            total += len(fails)
            print(f"FAIL  {name}  ({len(fails)})")
            for f in fails:
                print(f"      {f}")
        else:
            print(f"PASS  {name}")
    if total:
        print(f"\ncanonical-doc-coherence: {total} issue(s). See docs/CLAUDE-RULES-CANONICAL.md.")
        return 1
    print("\ncanonical-doc-coherence: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
