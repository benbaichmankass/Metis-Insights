"""Scan a unified diff for new dry-run / paper-trading switch flips.

Designed for the GitHub Actions guard (`.github/workflows/dry-run-guard.yml`)
that runs on every PR. The script reads a unified diff from stdin (or the
path passed as argv[1]) and exits with status 1 if any **added** line
flips an account out of live mode without operator approval.

Per the operator directive of 2026-05-03, the SINGLE dry/live toggle in
the codebase is per-account ``mode: live | dry_run`` in
``config/accounts.yaml`` (applied via ``RiskManager.dry_run``). This
guard now targets the YAML field directly:

    +    mode: dry_run         (config/accounts.yaml — flips an account out of live)
    +    mode: paper           (alias)
    +mode: dry_run             (legacy unindented; same shape)

The legacy ``DRY_RUN`` / ``ALLOW_LIVE_TRADING`` env-var patterns are
kept for back-compat with operator notebooks and external scripts that
might still reference them — they should not appear in the production
codebase any longer.

When the guard triggers, it prints a Telegram-shaped message naming the
offending file:line and intent. The CI workflow forwards that message
to the operator via the existing pending-pings inbox.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Patterns matched against ADDED diff lines (lines starting with `+ ` but not `+++`).
# Each entry is (regex, human-readable name).
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Primary check (post-2026-05-03 architecture): per-account mode.
    (re.compile(r"^\s*mode\s*:\s*['\"]?(?:dry|dry[_-]run|paper)\b", re.IGNORECASE),
     "account mode flipped to dry_run / paper in accounts.yaml"),
    # Legacy patterns retained as belt-and-braces. Production code no
    # longer reads these, but operator notebooks / external scripts
    # might — flag them so the operator notices.
    #
    # IMPORTANT: these patterns are CASE-SENSITIVE on the env-var name
    # because env vars are uppercase by convention. The lower-case
    # ``dry_run`` is reserved for Python kwargs (``RiskManager(..., dry_run=dry_run)``)
    # and must NOT trip the guard. The previous case-insensitive regex
    # produced four false positives on the `dry_run=dry_run` kwarg
    # introduced by BUG-039.
    (re.compile(r"\bDRY_RUN\s*[:=]\s*['\"]?(?:true|1|yes|on|dry|dry_run|paper)\b"),
     "legacy DRY_RUN env-var set to a truthy value (no longer consulted; remove)"),
    (re.compile(r"\bALLOW_LIVE_TRADING\s*[:=]\s*['\"]?(?:false|0|no|off)\b"),
     "legacy ALLOW_LIVE_TRADING explicitly disabled (no longer consulted; remove)"),
    (re.compile(r"\bpaper_trading\s*[:=]\s*['\"]?(?:true|1|yes|on)\b",
                re.IGNORECASE),
     "paper_trading enabled"),
]

# Files we deliberately ignore (tests are allowed to set mode=dry_run to
# exercise the dry-run path; this guard targets production code & config).
_IGNORE_PATH_RE = re.compile(
    r"(^|/)(tests?|test_)/|/test_[^/]+\.py$|^docs/|\.md$|"
    r"^scripts/check_dry_run_in_diff\.py$"
)

# Explicit per-line override. The guard's purpose is to make a human eyeball
# any line that puts an account in dry/paper mode. Adding a BRAND-NEW account
# that is intentionally dry (e.g. a real-money IB account held safe until it
# is separately promoted) is a legitimate, deliberate case — not the silent
# live→dry flip the guard exists to catch. A line carrying this marker is
# skipped, so the deliberate config lands without weakening the guard for
# every other (unmarked) line. The marker MUST include a reason for the
# audit trail, e.g.:
#     mode: dry_run   # dry-run-guard: allow — new IB real-money acct, held dry
_ALLOW_MARKER_RE = re.compile(r"dry-run-guard:\s*allow", re.IGNORECASE)


def _iter_added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield (file_path, line_no_in_new_file, content) for every added line."""
    current_file: str | None = None
    new_line_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            # `+++ b/path/to/file` or `+++ /dev/null`
            target = raw[4:].strip()
            if target == "/dev/null":
                current_file = None
            else:
                current_file = target[2:] if target.startswith(("a/", "b/")) else target
            new_line_no = 0
            continue
        if raw.startswith("@@"):
            # `@@ -old,len +new,len @@`
            m = re.search(r"\+(\d+)(?:,\d+)?", raw)
            new_line_no = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith("---") or raw.startswith("diff "):
            continue
        if raw.startswith("+") and not raw.startswith("++"):
            new_line_no += 1
            if current_file:
                yield current_file, new_line_no, raw[1:]
            continue
        if raw.startswith("-"):
            continue
        # Context line — counts toward the new-file line counter.
        new_line_no += 1


def scan_diff(diff_text: str) -> List[str]:
    """Return human-readable findings (empty list ⇒ clean)."""
    findings: List[str] = []
    for path, lineno, content in _iter_added_lines(diff_text):
        if _IGNORE_PATH_RE.search(path):
            continue
        # Deliberate, auditable per-line override (see _ALLOW_MARKER_RE).
        if _ALLOW_MARKER_RE.search(content):
            continue
        for pattern, label in _PATTERNS:
            if pattern.search(content):
                findings.append(
                    f"{path}:{lineno} — {label}: {content.strip()[:120]}"
                )
                break
    return findings


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        diff_text = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    else:
        diff_text = sys.stdin.read()
    findings = scan_diff(diff_text)
    if not findings:
        print("dry_run_in_diff: clean (no offending changes)")
        return 0
    msg_lines = [
        "🚨 DRY-RUN GUARD: a PR introduces a trading-mode flag flip.",
        "Per CLAUDE.md, the system default must be LIVE. Review:",
        "",
    ]
    msg_lines.extend(f"  - {f}" for f in findings)
    print("\n".join(msg_lines), file=sys.stderr)
    # Also print to stdout in machine-parseable form for CI to pin in
    # the workflow run summary.
    for f in findings:
        print(f"DRY_RUN_GUARD\t{f}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
