"""WC-6 — guard against new non-canonical canonical-store writers in diffs.

Designed for the GitHub Actions guard
(``.github/workflows/writer-conformance-guard.yml``) that runs on every
PR. The script reads a unified diff from stdin (or the path passed as
argv[1]) and exits with status 1 if any **added** line reintroduces one
of the canonical-store write-path defects this effort (WC-1..WC-5) just
fixed:

  1. A NEW raw SQL ``INSERT INTO trades`` / ``UPDATE trades SET`` /
     ``INSERT INTO order_packages`` / ``UPDATE order_packages SET``
     statement OUTSIDE the single canonical DB writer module
     (``src/units/db/database.py``). Every other writer MUST go through
     the canonical helpers — ``Database.insert_trade`` / ``update_trade``
     / ``insert_order_package`` / ``update_order_package`` — so the
     canonical-JSON / ``closed_at`` / direction-vocabulary invariants are
     applied in one place. Hand-rolled raw SQL bypasses them (the WC-1
     class of bug: an out-of-band ``UPDATE trades`` that forgot
     ``closed_at`` / left ``direction='buy'``).

  2. A NEW python string literal assigning a NON-CANONICAL direction
     value to ``trades.direction`` — i.e. ``"buy"`` / ``"sell"`` used as
     a *trades direction*. The canonical vocabulary is
     ``long`` / ``short``; ``buy`` / ``sell`` belong to ``signals.side``
     and exchange order ``side``, never ``trades.direction``. Kept
     deliberately narrow (only flags a line that assigns ``direction`` /
     ``"direction"`` to ``"buy"`` / ``"sell"``) so signal ``side=buy``
     and order ``side="sell"`` are never false-positives.

Known allowlisted exceptions (NOT flagged for rule 1 — they use raw SQL
legitimately, writing canonical JSON + ``closed_at`` + cascades, or are
one-shot operator tooling that runs against the canonical store by hand):

  * ``src/units/db/database.py`` — IS the canonical writer module.
  * ``src/units/ui/processor.py`` — the operator ``/closeall`` path
    (WC-1: raw SQL but writes canonical JSON + ``closed_at`` + cascades).
  * ``scripts/ops/`` — operator backfill / repair tools.
  * ``notebooks/`` — one-shot operator notebooks.
  * ``tests/`` — tests legitimately insert raw / non-conforming rows.

The explicit per-line escape hatch mirrors ``silent-empty-guard`` /
``dry-run-guard``: an inline ``# writer-conformance: allow <reason>``
comment on the offending added line whitelists just that line, recording
the justification in the diff for the audit trail.

This is a guard-rail, not a proof. It is a diff-based scanner — only
ADDED lines are inspected, so every pre-WC-6 writer is grandfathered and
the guard freezes the count of NEW non-canonical writers at zero.

Usage
-----
::

    python scripts/check_writer_conformance.py            # reads stdin
    python scripts/check_writer_conformance.py /tmp/pr.diff

Exit 0 → clean (no new violations). Exit 1 → at least one new violation
(each printed file:line + rule + how to fix/allow).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Allowlisted files / directory prefixes — a forward-slash path match
# against the diff's new-file path. Everything ELSE in the repo is
# protected (the inverse of the silent-empty guard's narrow allowlist of
# protected paths — here the whole tree is in scope except these).
_ALLOWLIST_FILES: Tuple[str, ...] = (
    # The canonical writer module itself — it IS the raw SQL.
    "src/units/db/database.py",
    # WC-1: operator /closeall path. Raw SQL but writes canonical JSON +
    # closed_at + cascades; the canonical helpers don't cover the
    # bulk-close shape, so this is the sanctioned raw-SQL exception.
    "src/units/ui/processor.py",
)
_ALLOWLIST_PREFIXES: Tuple[str, ...] = (
    # Operator backfill / repair tools run against the canonical store by
    # hand (one-shot data migrations); raw SQL is their job.
    "scripts/ops/",
    # One-shot operator notebooks.
    "notebooks/",
    # Tests legitimately seed raw / non-conforming rows into fixtures.
    "tests/",
)

# Paths we always ignore regardless of the protected-tree rule: this
# script + its test (they CONTAIN the very patterns they scan for), and
# docs / markdown (prose that names the bad idiom is fine).
_IGNORE_PATH_RE = re.compile(
    r"(^|/)(tests?|test_)/|/test_[^/]+\.py$|"
    r"^scripts/check_writer_conformance\.py$|"
    r"^docs/|\.md$"
)

# Rule 1 — a raw INSERT/UPDATE against trades / order_packages. Matched
# case-insensitively against the added line. ``INSERT INTO <tbl>`` and
# ``UPDATE <tbl> SET`` are the two mutating shapes; both the trades and
# order_packages tables are guarded. The patterns tolerate the f-string
# wrapping the canonical writer itself uses (``f"INSERT INTO trades …``)
# so a copy of that shape elsewhere is caught.
_RAW_WRITE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r"\bINSERT\s+INTO\s+trades\b", re.IGNORECASE),
        "raw `INSERT INTO trades`",
    ),
    (
        re.compile(r"\bUPDATE\s+trades\s+SET\b", re.IGNORECASE),
        "raw `UPDATE trades SET`",
    ),
    (
        re.compile(r"\bINSERT\s+INTO\s+order_packages\b", re.IGNORECASE),
        "raw `INSERT INTO order_packages`",
    ),
    (
        re.compile(r"\bUPDATE\s+order_packages\s+SET\b", re.IGNORECASE),
        "raw `UPDATE order_packages SET`",
    ),
]

# Rule 2 — a python assignment of a non-canonical direction value. Kept
# narrow: the line must assign ``direction`` / ``"direction"`` / a
# ``direction=`` kwarg / a ``"direction":`` dict key to a ``"buy"`` /
# ``"sell"`` string literal. This catches
#   direction = "buy"
#   trade["direction"] = "sell"
#   direction="buy"
#   {"direction": "sell"}
# but NOT ``side = "buy"`` (signals/orders use side=buy/sell legitimately).
_DIRECTION_VALUE = r"""['"](?:buy|sell)['"]"""
_BAD_DIRECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # direction = "buy"  /  direction="buy"  (bare or attr/kwarg)
    (
        re.compile(
            r"""(?<![\w"'])direction\s*=\s*""" + _DIRECTION_VALUE,
            re.IGNORECASE,
        ),
        "non-canonical trades.direction assignment (buy/sell — use long/short)",
    ),
    # "direction": "buy"   (dict key)
    # trade["direction"] = "sell"   (subscript assignment — optional `]`
    #                                between the quoted key and the `=`)
    (
        re.compile(
            r"""['"]direction['"]\s*\]?\s*[:=]\s*""" + _DIRECTION_VALUE,
            re.IGNORECASE,
        ),
        "non-canonical trades.direction assignment (buy/sell — use long/short)",
    ),
]

# Inline override marker — anywhere on the same line.
_ALLOW_RE = re.compile(r"#\s*writer-conformance:\s*allow\b", re.IGNORECASE)


def _path_is_protected(path: str) -> bool:
    """True if *path* is in scope (i.e. NOT allowlisted)."""
    if path in _ALLOWLIST_FILES:
        return False
    if any(path.startswith(p) for p in _ALLOWLIST_PREFIXES):
        return False
    return True


def _iter_added_lines(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(file_path, new_lineno, content)`` for every added line.

    Mirrors the parser in ``scripts/check_silent_empty_in_diff.py`` so
    the guards behave consistently.
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
        if _IGNORE_PATH_RE.search(path):
            continue
        if _ALLOW_RE.search(content):
            continue
        # Rule 1 — raw writers, scoped to the protected (non-allowlisted)
        # tree. Only python sources can hold the offending SQL in a way
        # that bypasses the canonical writer; restrict to .py so a YAML /
        # SQL-schema file that legitimately names the table isn't caught.
        if path.endswith(".py") and _path_is_protected(path):
            for pattern, label in _RAW_WRITE_PATTERNS:
                if pattern.search(content):
                    findings.append(
                        f"{path}:{lineno} — {label} outside the canonical "
                        f"writer (src/units/db/database.py): "
                        f"{content.strip()[:120]}"
                    )
                    break
        # Rule 2 — non-canonical direction value. Applies repo-wide on
        # python sources (the canonical writer module is NOT exempt — it
        # must never hard-code a buy/sell direction either), but skips the
        # allowlisted operator tooling that may massage legacy rows.
        if path.endswith(".py") and (
            _path_is_protected(path) or path in _ALLOWLIST_FILES
        ):
            for pattern, label in _BAD_DIRECTION_PATTERNS:
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
        print("writer_conformance: clean (no new non-canonical writers)")
        return 0
    msg_lines = [
        "🚨 WRITER-CONFORMANCE GUARD: a PR adds a non-canonical write to the "
        "canonical store.",
        "",
        "Every writer to trades / order_packages must go through the canonical",
        "helpers in src/units/db/database.py — insert_trade / update_trade /",
        "insert_order_package / update_order_package — so the canonical-JSON,",
        "closed_at, and direction-vocabulary (long/short, NOT buy/sell)",
        "invariants are applied in one place.",
        "",
        "To fix: route the write through the canonical Database helper, or use",
        "long/short for trades.direction. If the raw write is genuinely",
        "warranted (operator tooling, a bulk path the helpers don't cover), add",
        "an inline `# writer-conformance: allow <reason>` comment on that line.",
        "",
        "Findings:",
        "",
    ]
    msg_lines.extend(f"  - {f}" for f in findings)
    print("\n".join(msg_lines), file=sys.stderr)
    # Machine-parseable form for CI to pin in the workflow run summary.
    for f in findings:
        print(f"WRITER_CONFORMANCE_GUARD\t{f}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
