"""S-067 CP-4 — guard against new silent-empty error paths in diffs.

Designed for the GitHub Actions guard
(``.github/workflows/silent-empty-guard.yml``) that runs on every
PR. The script reads a unified diff from stdin (or the path passed
as argv[1]) and exits with status 1 if any **added** line
introduces a broad ``except`` handler inside the protected
read-path dirs without an explicit ``# allow-silent: <reason>``
justification on the same line.

Protected paths (where this pattern has historically caused
trust-corroding outages):

* ``src/web/api/`` — dashboard / diag / pnl / config endpoints.
* ``src/units/db/`` — the DB unit (Database / DataLoader).
* ``src/web/runtime_status.py`` — the runtime-status producer.

This generalises the bug class hardened in S-067 (PRs #642, #643,
#644, #645). The canonical examples it would have caught:

* PR #627: ``/positions`` returned ``[]`` for the endpoint's
  entire lifetime because an ``except Exception`` swallowed an
  ``OperationalError`` on a schema mismatch.
* PR #629: ``/signals`` dropped ``price`` because the writer alias
  fan-out wasn't covered — same root-cause class.

What it flags
-------------
Any **added** line matching::

    ^\s*except\s+(Exception|sqlite3\.Error|BaseException)\b...:
    ^\s*except\s*\([^)]*\b(Exception|sqlite3\.Error|BaseException)\b[^)]*\)\s*:
    ^\s*except\s*:                                # bare except

inside the protected paths, **unless** the line carries an inline
``# allow-silent: <reason>`` comment. That comment is the explicit
override for legitimate fan-out / never-raise contracts.

What it doesn't flag
--------------------
* Existing handlers (this is a diff-based scanner; pre-S-067 sites
  are grandfathered).
* Narrow-typed handlers (``except sqlite3.OperationalError`` etc.
  catch a specific failure mode, which is fine).
* Anything in ``tests/`` (tests legitimately patch / re-raise / wrap
  broad excepts).
* Anything carrying ``# allow-silent: ...``.
* Anything that re-raises (``raise``) inside the handler — we can't
  see body lines reliably from a per-line scan, but the override
  comment is the right path for those cases anyway.

This is a guard-rail, not a proof. False positives are expected and
should be silenced with ``# allow-silent: <reason>`` so the
override is reviewable in code.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Protected paths — a forward-slash substring match against the diff's
# new-file path. Keep narrow; the audit's scope is read-path code.
_PROTECTED_PREFIXES: Tuple[str, ...] = (
    "src/web/api/",
    "src/units/db/",
)
_PROTECTED_FILES: Tuple[str, ...] = (
    "src/web/runtime_status.py",
    # S-067 follow-up #8: hourly_report + boot_audit are reporting-layer
    # surfaces with the same trust contract as the web-api read-path —
    # they MUST NEVER raise (a crash silences the operator's only
    # window into bot state) but the broad-except pattern still risks
    # the silent-empty class. The audit at
    # docs/audits/silent-empty-reporting-2026-05-10.md classifies every
    # site as legitimate (every one logs); this entry pins the lint
    # guard so any *new* broad-except in either file requires either
    # a narrow type or `# allow-silent: <reason>`.
    "src/runtime/hourly_report.py",
    "src/runtime/boot_audit.py",
)

# Files we deliberately ignore. Tests legitimately patch broad-except
# behaviour to exercise the swallow path; the guard targets production
# code only.
_IGNORE_PATH_RE = re.compile(
    r"(^|/)(tests?|test_)/|/test_[^/]+\.py$|^docs/|\.md$|"
    r"^scripts/(check_silent_empty_in_diff|lint/)|\.toml$"
)

# Broad-except patterns. Each entry is (regex, human-readable name).
# All patterns match a single added line.
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # `except Exception:` / `except Exception as exc:` /
    # `except sqlite3.Error:` / `except BaseException:`
    (
        re.compile(
            r"^\s*except\s+(Exception|sqlite3\.Error|BaseException)\b"
            r"[^:]*:",
        ),
        "broad except (Exception / sqlite3.Error / BaseException)",
    ),
    # `except (Exception, ...):` / `except (sqlite3.Error, OSError):` etc.
    (
        re.compile(
            r"^\s*except\s*\([^)]*\b"
            r"(Exception|sqlite3\.Error|BaseException)"
            r"\b[^)]*\)\s*[A-Za-z_ ]*:",
        ),
        "broad except in tuple form (contains Exception / sqlite3.Error / BaseException)",
    ),
    # Bare `except:`
    (
        re.compile(r"^\s*except\s*:"),
        "bare except",
    ),
]

# Inline override marker. Anywhere on the same line is enough.
_ALLOW_RE = re.compile(r"#\s*allow-silent:", re.IGNORECASE)


def _path_is_protected(path: str) -> bool:
    if path in _PROTECTED_FILES:
        return True
    return any(path.startswith(p) for p in _PROTECTED_PREFIXES)


def _iter_added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(file_path, new_lineno, content)`` for every added line.

    Mirrors the parser in ``scripts/check_dry_run_in_diff.py`` so the
    two guards behave consistently.
    """
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
        # Context line — advances the new-file line counter.
        new_line_no += 1


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
        print("silent_empty_in_diff: clean (no offending changes)")
        return 0
    msg_lines = [
        "🚨 SILENT-EMPTY GUARD: a PR adds a broad except handler in a protected read-path file.",
        "Per docs/audits/silent-empty-2026-05-10.md, the protected paths require either",
        "  (a) a narrow exception type with a logged stack trace + re-raise / 503, or",
        "  (b) an inline `# allow-silent: <reason>` justification.",
        "",
        "Findings:",
        "",
    ]
    msg_lines.extend(f"  - {f}" for f in findings)
    print("\n".join(msg_lines), file=sys.stderr)
    # Machine-parseable form for CI to pin in the workflow run summary.
    for f in findings:
        print(f"SILENT_EMPTY_GUARD\t{f}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
