r"""CI guard: forbid new hand-rolled DB-path resolution in operator wrappers.

The 2026-05-16 orphan-backfill failure (issue #1308 — 14 candidates
processed against a stale DB at `${REPO_DIR}/trade_journal.db`, 0
recoveries) was the proximate trigger for ``scripts/ops/_lib.sh::
runtime_db_path``. The bug: every wrapper that touched the SQLite
journal computed ``DB_PATH="${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}"``
inline. That fallback misses the post-2026-05-12 data-dir externalisation
(``Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db`` in
``deploy/dropins/data-dir.conf``) because operator-action wrappers run
from a fresh shell, not as a child of ict-trader-live.service, so they
don't inherit the systemd drop-in's env.

This guard freezes the count of inline ``trade_journal.db`` resolutions
at zero outside the canonical helper:

* ``scripts/ops/_lib.sh`` — the source of ``load_runtime_env`` and
  ``runtime_db_path``. Allowed to reference the path; it defines the
  fallback chain.
* ``scripts/ops/sync_trainer_data.sh`` — runs on the TRAINER VM and
  SSHes into the live VM to fetch the DB. Its ``LIVE_VM_DB_PATH``
  default is the path on the live VM as seen from the trainer's
  perspective, not a wrapper-local resolution. Tracked as out-of-scope
  for this guard; a separate cross-VM helper is the right answer
  if/when the trainer's path resolution also drifts.

Detection strategy
------------------
Text scan over every ``scripts/ops/*.sh``. A wrapper trips the guard
if it contains either:

  1. A line matching ``trade_journal\.db`` AND not using
     ``runtime_db_path``, OR
  2. A literal ``TRADE_JOURNAL_DB:-`` fallback expression — the exact
     pattern the buggy wrappers used.

Comments (lines starting with ``#``) are skipped — the runbook header
in fix_data_dir.sh references the canonical path in documentation,
which is fine and doesn't introduce a parser.

Usage
-----
::

    python scripts/check_canonical_db_resolver.py        # CI-style scan
    python scripts/check_canonical_db_resolver.py --list # show clean count

Exit code 0 → clean. Exit code 1 → at least one new hand-rolled
resolver found; the script lists each offender's path + line + content.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple


_REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWLIST = frozenset({
    "scripts/ops/_lib.sh",
    # sync_trainer_data.sh runs on the TRAINER VM and SSHes into the
    # live VM. The path it resolves (LIVE_VM_DB_PATH) is the path on
    # the REMOTE host as seen from this script's local perspective,
    # not a wrapper-local resolution. Different concern; tracked as
    # out-of-scope for this guard.
    "scripts/ops/sync_trainer_data.sh",
})

# The buggy idiom: a fallback `${TRADE_JOURNAL_DB:-...}` expression
# that bypasses load_runtime_env's layered resolution. Every existing
# offender used this exact form, and any new wrapper that re-rolls the
# resolver will almost certainly reach for the same shape (it's the
# obvious POSIX-shell idiom). Detecting the syntactic signature
# rather than "mentions trade_journal.db" avoids false positives on:
#   - log/error strings that name the file for diagnostics
#   - trainer-VM scripts that resolve paths under ${DATA_DIR}/
#     (already canonical) and reference the filename as a relative
#     basename
INLINE_FALLBACK_RE = re.compile(r"TRADE_JOURNAL_DB:-")


def _scan_file(path: Path) -> List[Tuple[int, str]]:
    """Return ``[(line_no, content), ...]`` for any offending line."""
    hits: List[Tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#") or not stripped:
            continue
        if INLINE_FALLBACK_RE.search(line):
            hits.append((i, line.strip()))
    return hits


def _gather_offenders() -> List[Tuple[Path, List[Tuple[int, str]]]]:
    offenders: List[Tuple[Path, List[Tuple[int, str]]]] = []
    ops_dir = _REPO_ROOT / "scripts" / "ops"
    for path in sorted(ops_dir.glob("*.sh")):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        hits = _scan_file(path)
        if hits:
            offenders.append((path, hits))
    return offenders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--list", action="store_true",
        help="Print scan summary even on clean runs.",
    )
    args = parser.parse_args()

    offenders = _gather_offenders()
    if not offenders:
        if args.list:
            print(
                "canonical-db-resolver: clean. "
                f"{len(ALLOWLIST)} allowlisted wrapper(s), "
                "0 hand-rolled inline resolvers.",
            )
        return 0

    print(
        "canonical-db-resolver: hand-rolled DB-path resolver(s) found.",
        file=sys.stderr,
    )
    print(
        "Use `DB_PATH=\"$(runtime_db_path)\"` from scripts/ops/_lib.sh instead.\n",
        file=sys.stderr,
    )
    for path, hits in offenders:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for line_no, content in hits:
            print(f"  {rel}:{line_no}: {content}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
