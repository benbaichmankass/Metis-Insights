"""S-067 follow-up #4 — env-gate guard for the diff-scan layer.

Designed for the GitHub Actions guard
(``.github/workflows/env-gate-guard.yml``) that runs on every PR.
Reads a unified diff from stdin (or the path passed as argv[1]) and
exits with status 1 if any **added** line introduces an
``os.environ.get("…")`` / ``os.getenv("…")`` call inside the
protected paths whose env-var name matches the suspect patterns
documented in ``docs/audits/env-gate-purge-2026-05-10.md``.

Suspect patterns (any of):

  * ``MULTI_ACCOUNT_*``
  * ``MONITOR_*``
  * ``DISPATCH_*``
  * ``*_APPLY_TO_*``
  * ``*_DRY_*``
  * ``*_ENABLED`` / ``*_DISABLED``

Protected paths:

  * ``src/runtime/``
  * ``src/units/``
  * ``src/web/``

Override mechanism: inline ``# allow-silent: <reason>`` on the same
line. Mirrors the silent-empty-guard's shape so operators don't have
to learn two override syntaxes.

Why this exists
---------------
The 2026-05-03 directive (BUG-039) said per-account
``RiskManager.dry_run`` is the only live/dry switch. PR #630 deleted
``MONITOR_APPLY_TO_EXCHANGE`` after it lingered as a silent live →
dry escape hatch. This guard catches the same class of regression
at PR time rather than at audit time.

What it doesn't flag
--------------------
* Existing env-var reads (the scanner is diff-based; pre-S-067
  sites are grandfathered, documented in the audit doc).
* Reads of env vars whose names DON'T match the suspect patterns —
  business-as-usual flag flips for unrelated features.
* Anything in ``tests/`` or ``docs/`` (tests legitimately set env
  vars for fixtures).
* Anything carrying ``# allow-silent: <reason>``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

_PROTECTED_PREFIXES: Tuple[str, ...] = (
    "src/runtime/",
    "src/units/",
    "src/web/",
)

# Names that look like live/dry switches. An env var matching any of
# these on an *added* line under a protected path needs explicit
# justification.
_SUSPECT_NAME_RE = re.compile(
    r"\b("
    r"MULTI_ACCOUNT_[A-Z0-9_]+|"
    r"MONITOR_[A-Z0-9_]+|"
    r"DISPATCH_[A-Z0-9_]+|"
    r"[A-Z0-9_]+_APPLY_TO_[A-Z0-9_]+|"
    r"[A-Z0-9_]+_DRY_[A-Z0-9_]+|"
    r"[A-Z0-9_]+_ENABLED|"
    r"[A-Z0-9_]+_DISABLED"
    r")\b"
)

# An env-var read in code: os.environ.get("…") / os.getenv("…").
# Bracket subscripting (os.environ["…"]) is also caught. The name
# is captured for cross-checking against _SUSPECT_NAME_RE.
_ENV_READ_RE = re.compile(
    r"os\.(?:environ\.get|getenv)\s*\(\s*['\"]([A-Z0-9_]+)['\"]"
    r"|os\.environ\s*\[\s*['\"]([A-Z0-9_]+)['\"]"
)

_IGNORE_PATH_RE = re.compile(
    r"(^|/)(tests?|test_)/|/test_[^/]+\.py$|^docs/|\.md$|"
    r"^scripts/(check_env_gate_in_diff|check_silent_empty_in_diff|lint/)|\.toml$"
)

_ALLOW_RE = re.compile(r"#\s*allow-silent:", re.IGNORECASE)


def _path_is_protected(path: str) -> bool:
    return any(path.startswith(p) for p in _PROTECTED_PREFIXES)


def _iter_added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(file_path, new_lineno, content)`` for every added line.
    Mirrors the parser in ``scripts/check_silent_empty_in_diff.py``."""
    current_file: str | None = None
    new_line_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current_file = None
            else:
                current_file = (
                    target[2:] if target.startswith(("a/", "b/")) else target
                )
            new_line_no = 0
            continue
        if raw.startswith("@@"):
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
        new_line_no += 1


def _extract_env_name(content: str) -> str | None:
    m = _ENV_READ_RE.search(content)
    if not m:
        return None
    return m.group(1) or m.group(2)


def scan_diff(diff_text: str) -> List[str]:
    """Return human-readable findings (empty list ⇒ clean)."""
    findings: List[str] = []
    for path, lineno, content in _iter_added_lines(diff_text):
        if not _path_is_protected(path):
            continue
        if _IGNORE_PATH_RE.search(path):
            continue
        if _ALLOW_RE.search(content):
            continue
        env_name = _extract_env_name(content)
        if env_name is None:
            continue
        if not _SUSPECT_NAME_RE.search(env_name):
            continue
        findings.append(
            f"{path}:{lineno} — env-gate {env_name!r}: {content.strip()[:120]}"
        )
    return findings


def main(argv: List[str]) -> int:
    if len(argv) > 1:
        diff_text = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    else:
        diff_text = sys.stdin.read()
    findings = scan_diff(diff_text)
    if not findings:
        print("env_gate_in_diff: clean (no offending changes)")
        return 0
    msg_lines = [
        "🚨 ENV-GATE GUARD: a PR adds an env-var gate matching the BUG-039 suspect patterns in a protected path.",
        "Per docs/audits/env-gate-purge-2026-05-10.md, the live/dry contract is RiskManager.dry_run only.",
        "If this gate is genuinely required, add an inline `# allow-silent: <reason>` justification.",
        "",
        "Findings:",
        "",
    ]
    msg_lines.extend(f"  - {f}" for f in findings)
    print("\n".join(msg_lines), file=sys.stderr)
    for f in findings:
        print(f"ENV_GATE_GUARD\t{f}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
