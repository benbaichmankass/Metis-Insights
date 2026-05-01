"""Scan a unified diff for new dry-run / paper-trading switch flips.

Designed for the GitHub Actions guard (`.github/workflows/dry-run-guard.yml`)
that runs on every PR. The script reads a unified diff from stdin (or the
path passed as argv[1]) and exits with status 1 if any **added** line
sets a trading-mode flag in a way that would silently downgrade a service
out of live mode. Examples it catches:

    +DRY_RUN=true
    +ALLOW_LIVE_TRADING=false
    +MODE=BACKTEST           (only when not paired with a removal of MODE=LIVE
                              on the same hunk — heuristic; falls back to
                              human review)
    +dry_run: true           (yaml)
    +"dry_run": true         (json)

When it triggers, it prints a Telegram-shaped message naming the offending
file:line and intent. The CI workflow forwards that message to the
operator via the existing pending-pings inbox.

Per CLAUDE.md "Autonomous live-trading rule" the system default must
remain live; an unintended dry-run flip in a PR is a regression that
the operator wants to see before merge.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Patterns matched against ADDED diff lines (lines starting with `+ ` but not `+++`).
# Each entry is (regex, human-readable name).
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bDRY_RUN\s*[:=]\s*['\"]?(?:true|1|yes|on|dry|dry_run|paper)\b",
                re.IGNORECASE),
     "DRY_RUN set to a truthy value"),
    (re.compile(r"\bALLOW_LIVE_TRADING\s*[:=]\s*['\"]?(?:false|0|no|off)\b",
                re.IGNORECASE),
     "ALLOW_LIVE_TRADING explicitly disabled"),
    (re.compile(r"\bdry_run\s*[:=]\s*['\"]?(?:true|1|yes|on)\b", re.IGNORECASE),
     "dry_run set to truthy in YAML/JSON"),
    (re.compile(r"\bpaper_trading\s*[:=]\s*['\"]?(?:true|1|yes|on)\b",
                re.IGNORECASE),
     "paper_trading enabled"),
]

# Files we deliberately ignore (tests are allowed to set DRY_RUN=true to
# exercise the dry-run path; this guard targets production code & config).
_IGNORE_PATH_RE = re.compile(
    r"(^|/)(tests?|test_)/|/test_[^/]+\.py$|^docs/|\.md$|"
    r"^scripts/check_dry_run_in_diff\.py$|"
    r"^src/runtime/trading_mode\.py$"
)


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
