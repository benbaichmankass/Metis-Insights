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


# --------------------------------------------------------------------------- #
# declared values — does the prose match the file that actually sets it?
# --------------------------------------------------------------------------- #
#
# WHY (2026-08-10). The four checks above passed 4/4 while FIVE canonical docs
# described branch protection incorrectly, because none of them compares a
# claim about a live setting against the file that sets it. The same session
# also found `CLAUDE.md` describing `POST /api/bot/prop/report` as PERMISSIVELY
# token-gated ("when set") when `_require_write_token` is fail-CLOSED (503 when
# the token is unset). That second one is the shape that reaches the trader: a
# session reasoning from it would conclude an unauthenticated write path exists
# where it does not, or vice versa.
#
# Same idiom as `check_removed_gates`: a phrase asserting the wrong value is a
# finding unless the surrounding context marks it historical/corrected.
#
# TWO RULES FOR ADDING A CONTRACT — both are the point, not ceremony:
#
#  1. THE SOURCE MUST BE IN-REPO. A value that lives only on the VM (e.g. the
#     live `BYBIT_TPSL_MODE`) is deliberately EXCLUDED: this guard would be
#     asserting a value it cannot read, which is precisely the defect it
#     exists to catch. Verify those by diag, not here.
#  2. AN UNREADABLE SOURCE IS A FAILURE, NOT A PASS. If the extractor stops
#     matching (someone renames `STRICT=`), the check reports that loudly. A
#     silently-disabled check is the "green that checked nothing" this repo
#     already treats as worse than a red.
#
# WHAT THIS DOES **NOT** PROVE — stated so the PASS line is not read as more
# than it is (the same defect one level up):
#
#  * It matches KNOWN STALE PHRASINGS, not meaning. A doc can assert the wrong
#    value in words no pattern here anticipates and this check will pass. It
#    is a ratchet against recurrence of drift that actually happened, not a
#    general prover.
#  * Coverage is deliberately ASYMMETRIC. Each contract lists patterns only
#    for the value(s) the source does NOT currently hold; the pattern list for
#    the current value is empty. Flipping a source value therefore does not
#    immediately start flagging the now-stale prose — the phrases that would
#    catch it ("unticked", "off since") are the same ones `_HISTORICAL` uses to
#    suppress corrected text, and conflating those would make the guard fire on
#    its own retrospective notes. **When you flip a value, sweep its docs by
#    hand and move the patterns across.**
#  * It reads REPO state. Anything whose truth lives on the VM is out of scope
#    by rule 1 above.

_HISTORICAL = re.compile(
    r"remov|retir|supersed|histor|no longer|was |used to|until |previously|"
    r"correct|~~|deprecat|before 20|off since|unticked|past tense|RESOLVED",
    re.I,
)

# (id, source_file, extractor, {value: [patterns asserting THAT value]})
VALUE_CONTRACTS = [
    {
        "id": "branch-protection require-up-to-date",
        "source": ".github/workflows/branch-protection-sync.yml",
        "extract": re.compile(r"^\s*STRICT=(true|false)\s*$", re.M),
        "asserts": {
            "true": [
                re.compile(r"safety net is .{0,40}branch.protection \(require-up-to-date\)", re.I),
                re.compile(r'"?Require branches to be up to date[^\n]{0,60}\b(is ON|ticked|enabled)', re.I),
                re.compile(r"sync to `?main`? LAST, right before merging", re.I),
            ],
            "false": [],
        },
    },
    {
        "id": "POST /api/bot/prop/report write gate",
        "source": "src/web/api/routers/prop.py",
        # fail-closed iff the token-unset branch raises 503.
        "extract": lambda t: "fail_closed" if re.search(
            r"def _require_write_token.*?status_code=503", t, re.S) else "permissive",
        "asserts": {
            "permissive": [
                re.compile(r"prop/report[^\n]{0,200}token-gated[^\n]{0,60}\bwhen set\b", re.I),
            ],
            "fail_closed": [],
        },
    },
    {
        "id": "/api/bot/devices admin-token gate",
        "source": "src/web/api/routers/devices.py",
        # permissive iff the token-unset branch returns instead of raising.
        "extract": lambda t: "permissive" if re.search(
            r"def _check_admin_token.*?if not expected:\s*\n\s*return", t, re.S) else "fail_closed",
        "asserts": {
            "fail_closed": [
                re.compile(r"/devices[^\n]{0,120}\bfail-?closed\b", re.I),
                re.compile(r"/devices[^\n]{0,120}\b503\b", re.I),
            ],
            "permissive": [],
        },
    },
]

# Docs where a claim about a live gate misleads a session. Broader than
# ACTIVE_DOCS: the 2026-08-10 drift was in the runbook and the board JSON,
# neither of which the other checks read.
_VALUE_DOC_EXTRAS = [
    "docs/runbooks/merge-queue.md",
    "docs/claude/coordination-board.md",
    "docs/claude/session-board.json",
]


def check_declared_values() -> list[str]:
    """A doc must not assert a live setting's value that the source contradicts."""
    fails: list[str] = []
    files = _active_files()
    files += [ROOT / p for p in _VALUE_DOC_EXTRAS if (ROOT / p).exists()]

    for c in VALUE_CONTRACTS:
        src = ROOT / c["source"]
        if not src.exists():
            fails.append(f"{c['source']}: source for '{c['id']}' is missing — "
                         f"this check cannot run; fix the path or drop the contract")
            continue
        text = src.read_text(encoding="utf-8")
        ex = c["extract"]
        if callable(ex) and not hasattr(ex, "search"):
            actual = ex(text)
        else:
            m = ex.search(text)
            actual = m.group(1) if m else None
        if actual is None:
            fails.append(f"{c['source']}: could not read the current value for "
                         f"'{c['id']}' — the extractor no longer matches, so this "
                         f"check is silently disabled. Fix it, do not ignore it")
            continue

        wrong = [(v, pats) for v, pats in c["asserts"].items() if v != actual]
        for claimed, patterns in wrong:
            for rel, i, line, context in _iter_windows(files):
                for pat in patterns:
                    if pat.search(line) and not _HISTORICAL.search(context):
                        fails.append(
                            f"{rel}:{i}: says '{c['id']}' is {claimed!r}, but "
                            f"{c['source']} sets it to {actual!r} -> {line.strip()[:110]}"
                        )
                        break
    return fails


CHECKS = [
    ("dead VM IP single-source", check_dead_vm_ip),
    ("removed gates not described as live", check_removed_gates),
    ("no 7-stage ML ladder in catalog", check_seven_stage_ladder),
    ("instruction-hierarchy mirror", check_hierarchy_mirror),
    ("declared values match their source", check_declared_values),
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
