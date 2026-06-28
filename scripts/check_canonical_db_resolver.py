r"""CI guard: forbid new hand-rolled DB-path resolution in operator wrappers.

The 2026-05-16 orphan-backfill failure (issue #1308 — 14 candidates
processed against a stale DB at `${REPO_DIR}/trade_journal.db`, 0
recoveries) was the proximate trigger for ``scripts/ops/_lib.sh::
runtime_db_path``. The bug: every wrapper that touched the SQLite
journal computed ``DB_PATH="${TRADE_JOURNAL_DB:-${REPO_DIR}/trade_journal.db}"``
inline. That fallback misses the post-2026-05-12 data-dir externalisation
(``Environment=TRADE_JOURNAL_DB=/data/bot-data/trade_journal.db`` in
``deploy/dropins/data-dir.conf``) because system-action wrappers run
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

# ---------------------------------------------------------------------------
# Python side (added 2026-05-23, S-PERSIST-CANON).
#
# The shell scan above froze inline DB-path resolution in operator
# wrappers. The same class of bug existed — and actually produced the
# stray duplicate journals on the live VM — on the PYTHON side:
#
#   * ``Database(db_path="trade_journal.db")`` default, and
#   * ``os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"``
#
# both resolve relative to the process CWD, so any Python process that
# started without the systemd ``TRADE_JOURNAL_DB`` env wrote a fresh DB
# under its working directory (``/home/ubuntu/ict-trading-bot/`` for the
# trader, ``src/bot/`` historically). The single canonical resolver is
# ``src.utils.paths.trade_journal_db_path()``; this scan forbids both the
# CWD-relative fallback AND any new inline ``TRADE_JOURNAL_DB`` env-read
# outside that resolver, so every caller routes through one place.
# ---------------------------------------------------------------------------

# Directories scanned for Python offenders (runtime code only — tests
# legitimately set the env var). The shell scan covers scripts/*.sh, but the
# operational *.py under scripts/ops were a blind spot: 13 DB-mutating scripts
# used the `args.db or os.environ.get("TRADE_JOURNAL_DB", "trade_journal.db")`
# CWD-relative fallback the guard exists to forbid (S-AUDIT-G Finding 1). They
# were masked in production only because the *_action.sh wrappers export
# TRADE_JOURNAL_DB from runtime_db_path() first — a direct run would write a
# stray journal. scripts/ops/*.py now routes through trade_journal_db_path(),
# and is scanned here so it can't regress. Widened to ALL of scripts/ in
# S-AUDIT-H (H-2/H-3): scripts/init_db.py + scripts/daily_heartbeat.py carried
# the same non-canonical CWD-relative resolution outside scripts/ops, also
# unseen by this guard. Both are fixed; the full scripts/ scan keeps any new
# script honest (genuinely self-contained stdlib scripts that cannot import
# the resolver are allowlisted below).
_PY_SCAN_DIRS = ("src", "ml", "scripts")

# Only the canonical resolver module may read the env var / name the
# basename directly — it IS the single resolver.
_PY_ALLOWLIST = frozenset({
    "src/utils/paths.py",
    # risk_counters reads TRADE_JOURNAL_DB to detect whether a journal is
    # EXPLICITLY configured (env/settings) — a different semantic from
    # resolving the canonical default. "No journal configured → leave
    # settings unchanged" is a load-bearing contract (tests:
    # test_runtime_risk_injection / test_per_strategy_risk), so this file
    # legitimately reads the env directly rather than the always-resolving
    # trade_journal_db_path(). It uses no CWD-relative fallback.
    "src/runtime/risk_counters.py",
    # daily_heartbeat is a stdlib-only digest that must run even when the
    # venv/src is wedged, so it deliberately does NOT import src.utils.paths.
    # Its _db_path() mirrors the canonical env -> $DATA_DIR -> repo-root chain
    # in stdlib (no CWD-relative bare-basename fallback). Same carve-out
    # rationale as risk_counters.py (S-AUDIT-H H-3).
    "scripts/daily_heartbeat.py",
})

# 1. CWD-relative bare basename used as a path value — the proven bug.
#    (a) the ``... or "trade_journal.db"`` fallback idiom, and
#    (b) a ``db_path=`` default of the bare basename (the old
#        ``Database(db_path="trade_journal.db")`` signature).
#    Deliberately does NOT match repo-anchored forms
#    (``_REPO_ROOT / "trade_journal.db"``, ``os.path.join(root, …)``,
#    which are absolute) nor unrelated kwargs like a Telegram upload's
#    ``filename="trade_journal.db"``.
_PY_CWD_FALLBACK_RES = (
    re.compile(r"""\bor\s+['"]trade_journal\.db['"]"""),
    re.compile(r"""db_path\s*=\s*['"]trade_journal\.db['"]""", re.IGNORECASE),
)

# 2. Inline TRADE_JOURNAL_DB env-read outside the canonical resolver —
#    forces consolidation onto trade_journal_db_path().
_PY_ENV_READ_RE = re.compile(
    r"""(?:environ\.get|getenv)\(\s*['"]TRADE_JOURNAL_DB['"]"""
)


def _scan_python_file(path: Path) -> List[Tuple[int, str]]:
    """Return ``[(line_no, content), ...]`` for offending Python lines.

    Skips ``#`` comment lines. Docstring prose that merely *names* the
    historical idiom doesn't match (the regexes require the live call
    syntax, not prose), so the allowlisted resolver's own docstring is
    safe — but it's allowlisted anyway.
    """
    hits: List[Tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#") or not stripped:
            continue
        if _PY_ENV_READ_RE.search(line) or any(
            rx.search(line) for rx in _PY_CWD_FALLBACK_RES
        ):
            hits.append((i, line.strip()))
    return hits


def _gather_python_offenders() -> List[Tuple[Path, List[Tuple[int, str]]]]:
    offenders: List[Tuple[Path, List[Tuple[int, str]]]] = []
    for sub in _PY_SCAN_DIRS:
        root = _REPO_ROOT / sub
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in _PY_ALLOWLIST:
                continue
            hits = _scan_python_file(path)
            if hits:
                offenders.append((path, hits))
    return offenders


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
    py_offenders = _gather_python_offenders()
    if not offenders and not py_offenders:
        if args.list:
            print(
                "canonical-db-resolver: clean. "
                f"{len(ALLOWLIST)} allowlisted shell wrapper(s), "
                f"{len(_PY_ALLOWLIST)} allowlisted python module(s), "
                "0 hand-rolled inline resolvers (shell + python).",
            )
        return 0

    if offenders:
        print(
            "canonical-db-resolver: hand-rolled DB-path resolver(s) found "
            "in shell wrappers.",
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

    if py_offenders:
        print(
            "canonical-db-resolver: CWD-relative fallback or inline "
            "TRADE_JOURNAL_DB env-read found in Python.",
            file=sys.stderr,
        )
        print(
            "Use `from src.utils.paths import trade_journal_db_path` and "
            "call `trade_journal_db_path()` (or `Database()` with no path) "
            "instead.\n",
            file=sys.stderr,
        )
        for path, hits in py_offenders:
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for line_no, content in hits:
                print(f"  {rel}:{line_no}: {content}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
