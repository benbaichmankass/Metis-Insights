#!/usr/bin/env python3
"""DB-integrity checker — Phase-4 guardrail for the dashboard-truth effort.

> Read-only, Tier-1. Detects when the canonical store's WRITE-PATH is
> producing bad data, so the operator gets alerted. It distinguishes a
> RECENT write-path regression (alert-worthy — a row that just closed
> without a canonical field = a live bug) from the LEGACY historical
> backlog (informational — un-backfilled old rows the P1-E pass clears,
> NOT an alert).

This implements the INV-1..5 integrity invariants from
``docs/audits/dashboard-truth-and-persistence-2026-06-16.md`` § "Integrity
invariants (Phase-4 guardrail)". Each invariant is computed over a
configurable RECENT window (default 48h, keyed on
``COALESCE(closed_at, created_at, timestamp)``) AND as a separate
total/legacy count, so a fresh regression surfaces immediately while the
pre-backfill backlog stays a silent informational number.

**alert = true only when ``recent_count > 0``** — the legacy backlog never
alerts (the P1-E backfill clears it; alerting on it would be permanent
noise that trains the operator to ignore the channel).

Invariants
----------
- **INV-1**  ``status='closed'`` AND ``closed_at IS NULL`` (non-backtest).
  Recent ⇒ a close path didn't stamp ``closed_at`` (regression, P1-B
  should prevent). Legacy ⇒ pre-P1-B backlog.
- **INV-2**  ``status='closed'`` AND ``pnl IS NULL`` (non-backtest), EXCLUDING
  the legitimately-bounded broker-sweep window: a just-closed Bybit row is
  allowed to carry ``pnl NULL`` until the deferred broker-pnl sweep fills
  the fee-accurate number. So we flag only closed rows whose close is OLDER
  than ``--pnl-grace-hours`` (default 6h) — those should have converged.
  INV-2 is a *convergence* guarantee, not "never NULL at the instant of
  close" (see the audit's "P1-B refinement forced by the audit").
- **INV-3**  ``status IN ('open','closed')`` (non-backtest) with NO resolvable
  order-package link by EITHER direction — ``order_package_id IS NULL`` AND
  the row is not referenced by any ``order_packages.linked_trade_id``.
- **INV-4**  ``account_class IS NULL`` (non-backtest). Recent ⇒ an insert
  didn't stamp it (regression — WC-2 should prevent). Legacy ⇒ pre-backfill.
- **INV-5**  an ``order_packages`` row in a terminal state whose
  ``linked_trade_id`` disagrees with the ``trades.order_package_id`` back-ref
  for the same fill (best-effort, conservative): the package points at a
  trade (``linked_trade_id`` set) that does NOT point back at the package
  (``trades.order_package_id`` is a different package id, i.e. a concrete
  mismatch — NULL back-refs are the documented many-to-one design and are
  NOT flagged).
- **INV-6**  ``trades.notes`` present but ``json_valid(notes)=0`` — invalid
  JSON (BL-20260618). Recent ⇒ a write path emitted a malformed blob (should
  be impossible now the char-slice footgun is gone + the json-notes-cap guard
  is a merge gate). Legacy ⇒ pre-fix backlog, cleared by
  ``scripts/ops/repair_malformed_notes.py``.

Output
------
A JSON report to **stdout**::

    {generated_at, window_hours, pnl_grace_hours, db_path,
     checks: [{id, title, recent_count, total_count, sample_ids:[...], alert}],
     any_alert: bool}

and a short human summary to **stderr**.

Exit code: **0 always** (this is a reporter, not a gate) — UNLESS
``--fail-on-alert`` is passed, in which case exit 1 when ``any_alert``.
``2`` on a DB-open/read error.

It is the single source the cron alert + dashboard Health tab both consume:
``--json`` (the default) prints the machine report; the health-snapshot path
folds the same report in via ``run_checks()``.

Read-only: opens the canonical ``trade_journal.db`` with a SQLite
``mode=ro`` URI connection (resolved through
``src.utils.paths.trade_journal_db_path()``; ``--db`` overrides for tests).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the repo root importable when run as a standalone script
# (systemd ExecStart=.../python scripts/check_db_integrity.py).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_WINDOW_HOURS = 48.0
DEFAULT_PNL_GRACE_HOURS = 6.0

# How many offending ids to carry in each check's `sample_ids` — enough for
# the operator to spot-check via the Data Explorer, small enough to keep the
# JSON + Telegram ping compact.
_SAMPLE_LIMIT = 10

# The window basis: the most-specific timestamp available on a trades row.
# closed_at is the canonical close time (P1-A column); created_at is the
# row-insert time; timestamp is the open time. COALESCE prefers the
# strongest signal so "recent" tracks when the row actually became its
# current state.
_WINDOW_TS = "COALESCE(t.closed_at, t.created_at, t.timestamp)"

# Non-backtest filter — every INV is about live/paper rows, never the
# is_backtest=1 research rows (which legitimately omit closed_at/pnl/etc).
_NON_BACKTEST = "COALESCE(t.is_backtest, 0) = 0"


def _resolve_db_path(explicit: Optional[str]) -> str:
    """Resolve the canonical DB path (``--db`` wins, else the resolver)."""
    if explicit:
        return str(Path(explicit).expanduser())
    from src.utils.paths import trade_journal_db_path

    return trade_journal_db_path()


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """Open a strictly read-only connection (``mode=ro`` URI).

    ``mode=ro`` makes SQLite refuse any write and refuse to CREATE the file
    if it's missing — so a typo'd path errors loudly instead of seeding a
    stray empty journal (the exact failure the canonical-db-resolver guard
    exists to prevent).
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _count_and_sample(
    conn: sqlite3.Connection, where: str, params: List[Any]
) -> tuple[int, List[Any]]:
    """Return ``(count, sample_ids)`` for ``SELECT t.id ... WHERE <where>``.

    Counts the full matching set but only materialises up to ``_SAMPLE_LIMIT``
    ids (newest-first by the window basis) for the report.
    """
    count_sql = f"SELECT COUNT(*) AS n FROM trades t WHERE {where}"  # noqa: S608 — where is built from module constants, params are bound
    n = int(conn.execute(count_sql, params).fetchone()["n"])
    sample: List[Any] = []
    if n:
        sample_sql = (  # noqa: S608 — same: static where + bound params
            f"SELECT t.id AS id FROM trades t WHERE {where} "
            f"ORDER BY {_WINDOW_TS} DESC LIMIT {_SAMPLE_LIMIT}"
        )
        sample = [r["id"] for r in conn.execute(sample_sql, params).fetchall()]
    return n, sample


def _windowed(
    conn: sqlite3.Connection, base_where: str, since_iso: str
) -> tuple[int, List[Any]]:
    """``base_where`` restricted to rows whose window basis ≥ ``since_iso``."""
    where = f"({base_where}) AND {_WINDOW_TS} >= ?"
    return _count_and_sample(conn, where, [since_iso])


def _total(
    conn: sqlite3.Connection, base_where: str
) -> tuple[int, List[Any]]:
    """``base_where`` over the whole table (legacy + recent)."""
    return _count_and_sample(conn, base_where, [])


def _check(
    conn: sqlite3.Connection,
    *,
    check_id: str,
    title: str,
    base_where: str,
    since_iso: str,
) -> Dict[str, Any]:
    """Build one check dict: recent (windowed) + total counts + sample + alert.

    The sample carried in the report is the RECENT sample when there is a
    recent hit (that's the alert-worthy set the operator wants ids for),
    otherwise the total sample (so a purely-legacy finding still shows
    example ids for context).
    """
    recent_count, recent_sample = _windowed(conn, base_where, since_iso)
    total_count, total_sample = _total(conn, base_where)
    return {
        "id": check_id,
        "title": title,
        "recent_count": recent_count,
        "total_count": total_count,
        "sample_ids": recent_sample if recent_count else total_sample,
        "alert": recent_count > 0,
    }


def run_checks(
    db_path: str,
    *,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    pnl_grace_hours: float = DEFAULT_PNL_GRACE_HOURS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run all five integrity invariants and assemble the report dict.

    Pure read path. ``now`` is injectable for deterministic tests.
    """
    now = now or datetime.now(timezone.utc)
    since_iso = _iso(now - timedelta(hours=window_hours))
    pnl_cutoff_iso = _iso(now - timedelta(hours=pnl_grace_hours))

    conn = _connect_ro(db_path)
    try:
        checks: List[Dict[str, Any]] = []

        # INV-1 — closed row with no canonical close timestamp.
        checks.append(
            _check(
                conn,
                check_id="INV-1",
                title="closed trade with closed_at IS NULL",
                base_where=(
                    f"{_NON_BACKTEST} AND t.status = 'closed' "
                    "AND t.closed_at IS NULL"
                ),
                since_iso=since_iso,
            )
        )

        # INV-2 — closed row with NULL pnl PAST the broker-sweep grace window.
        # A row whose close (window basis) is OLDER than pnl_grace_hours
        # should have been filled (broker sweep for Bybit, local sweep for
        # non-broker accounts). Recent-but-within-grace is the legitimate
        # bounded NULL window and is NOT flagged. Note: the grace cutoff is
        # baked into base_where so BOTH the recent and the total counts
        # already exclude the in-grace window — the "recent" count is the
        # alertable set (past-grace AND inside the review window).
        checks.append(
            _check(
                conn,
                check_id="INV-2",
                title=(
                    "closed trade with pnl IS NULL past the "
                    f"{pnl_grace_hours:g}h broker-sweep grace"
                ),
                base_where=(
                    f"{_NON_BACKTEST} AND t.status = 'closed' "
                    "AND t.pnl IS NULL "
                    f"AND {_WINDOW_TS} < '{pnl_cutoff_iso}'"
                ),
                since_iso=since_iso,
            )
        )

        # INV-3 — open/closed real trade with NO resolvable package link by
        # EITHER direction. order_package_id NULL (forward link absent) AND
        # not referenced by any order_packages.linked_trade_id (reverse link
        # absent). A row linked by either direction is fine.
        checks.append(
            _check(
                conn,
                check_id="INV-3",
                title="open/closed trade with no order-package link (either direction)",
                base_where=(
                    f"{_NON_BACKTEST} AND t.status IN ('open', 'closed') "
                    "AND t.order_package_id IS NULL "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM order_packages op "
                    "  WHERE op.linked_trade_id = t.id"
                    ")"
                ),
                since_iso=since_iso,
            )
        )

        # INV-4 — row missing account_class (the funding-category axis).
        checks.append(
            _check(
                conn,
                check_id="INV-4",
                title="trade with account_class IS NULL",
                base_where=f"{_NON_BACKTEST} AND t.account_class IS NULL",
                since_iso=since_iso,
            )
        )

        # INV-5 — terminal order_packages row whose linked_trade_id disagrees
        # with the trade's order_package_id back-ref for the SAME fill. This
        # is computed over order_packages (not trades), so its window basis is
        # the package's updated_at and its sample ids are package ids. We only
        # flag a CONCRETE mismatch: the package links a trade that exists and
        # points back at a DIFFERENT package — never the documented NULL
        # back-ref (the many-to-one convenience design).
        checks.append(
            _inv5_check(
                conn,
                since_iso=since_iso,
            )
        )

        # INV-6 — trade row whose ``notes`` blob is INVALID JSON
        # (BL-20260618-CLOSEDFLAT-MALFORMED-JSON). The retired
        # ``json.dumps(payload)[:N]`` char-slice footgun cut mid-token and
        # persisted invalid JSON, which made ``closed_flat_invariant``'s
        # ``json_extract(notes,'$.closed_at')`` raise "malformed JSON" and abort
        # the whole safety-invariant query. The write-side is now
        # ``dump_capped`` (always-valid) + guarded by the json-notes-cap CI
        # check, so recent > 0 is a genuine regression; the legacy backlog
        # (total, pre-fix rows) is cleared by ``scripts/ops/repair_malformed_notes.py``.
        checks.append(
            _check(
                conn,
                check_id="INV-6",
                title="trade with malformed-JSON notes (invalid json_valid)",
                base_where=(
                    f"{_NON_BACKTEST} AND t.notes IS NOT NULL "
                    "AND t.notes != '' AND json_valid(t.notes) = 0"
                ),
                since_iso=since_iso,
            )
        )

        any_alert = any(c["alert"] for c in checks)
        return {
            "generated_at": _iso(now),
            "window_hours": window_hours,
            "pnl_grace_hours": pnl_grace_hours,
            "db_path": db_path,
            "checks": checks,
            "any_alert": any_alert,
        }
    finally:
        conn.close()


# Terminal order_packages states: a package that reached a terminal state has
# a settled link; an open/pending package is mid-flight and its link is
# legitimately still being written, so we don't flag it.
_TERMINAL_OP_STATES = ("closed", "filled", "cancelled", "rejected", "expired")


def _inv5_check(conn: sqlite3.Connection, *, since_iso: str) -> Dict[str, Any]:
    """INV-5: terminal package whose linked_trade_id disagrees with the
    trade's order_package_id back-ref.

    Conservative concrete-mismatch definition:
      - the package is in a terminal state, AND
      - it carries a non-NULL linked_trade_id pointing at an existing trade,
        AND
      - that trade's order_package_id is non-NULL but != this package id
        (a real cross-link, not the documented NULL back-ref).

    Window basis is the package's updated_at; sample ids are package ids.
    """
    placeholders = ", ".join("?" for _ in _TERMINAL_OP_STATES)
    base_where = (
        "op.status IN (" + placeholders + ") "
        "AND op.linked_trade_id IS NOT NULL "
        "AND EXISTS ("
        "  SELECT 1 FROM trades t "
        "  WHERE t.id = op.linked_trade_id "
        "    AND t.order_package_id IS NOT NULL "
        "    AND t.order_package_id != op.order_package_id"
        ")"
    )
    params = list(_TERMINAL_OP_STATES)

    total_sql = f"SELECT COUNT(*) AS n FROM order_packages op WHERE {base_where}"  # noqa: S608
    total_count = int(conn.execute(total_sql, params).fetchone()["n"])
    total_sample = (
        [
            r["order_package_id"]
            for r in conn.execute(
                f"SELECT op.order_package_id FROM order_packages op WHERE {base_where} "  # noqa: S608
                f"ORDER BY op.updated_at DESC LIMIT {_SAMPLE_LIMIT}",
                params,
            ).fetchall()
        ]
        if total_count
        else []
    )

    recent_where = f"({base_where}) AND op.updated_at >= ?"
    recent_params = params + [since_iso]
    recent_count = int(
        conn.execute(
            f"SELECT COUNT(*) AS n FROM order_packages op WHERE {recent_where}",  # noqa: S608
            recent_params,
        ).fetchone()["n"]
    )
    recent_sample = (
        [
            r["order_package_id"]
            for r in conn.execute(
                f"SELECT op.order_package_id FROM order_packages op WHERE {recent_where} "  # noqa: S608
                f"ORDER BY op.updated_at DESC LIMIT {_SAMPLE_LIMIT}",
                recent_params,
            ).fetchall()
        ]
        if recent_count
        else []
    )

    return {
        "id": "INV-5",
        "title": "terminal order_package whose linked_trade_id disagrees with the trade back-ref",
        "recent_count": recent_count,
        "total_count": total_count,
        "sample_ids": recent_sample if recent_count else total_sample,
        "alert": recent_count > 0,
    }


def render_summary(report: Dict[str, Any]) -> str:
    """Human one-block summary for stderr / the Telegram ping body."""
    lines = [
        f"DB integrity @ {report['generated_at']}  "
        f"window={report['window_hours']:g}h  "
        f"pnl_grace={report['pnl_grace_hours']:g}h",
        f"db: {report['db_path']}",
    ]
    for c in report["checks"]:
        flag = "ALERT" if c["alert"] else "ok   "
        sample = (
            f"  e.g. {c['sample_ids']}" if c["sample_ids"] else ""
        )
        lines.append(
            f"[{flag}] {c['id']}: recent={c['recent_count']} "
            f"total={c['total_count']}  {c['title']}{sample}"
        )
    verdict = "ANY_ALERT" if report["any_alert"] else "clean"
    lines.append(f"=> {verdict}")
    return "\n".join(lines)


def build_alert_message(report: Dict[str, Any]) -> str:
    """``[WARN] DB integrity: …`` Telegram body for the alerting wire.

    Only the alerting checks (recent_count > 0) are enumerated, since the
    legacy backlog isn't the alert. Matches the repo's bracketed-severity
    Telegram idiom (``[CRITICAL] …`` / ``[WARN] …`` / ``[OK] …``).
    """
    alerts = [c for c in report["checks"] if c["alert"]]
    head = (
        f"[WARN] DB integrity: {len(alerts)} write-path regression(s) "
        f"in the last {report['window_hours']:g}h"
    )
    body = [head]
    for c in alerts:
        body.append(
            f"• {c['id']}: {c['recent_count']} recent "
            f"({c['total_count']} total) — {c['title']}; ids {c['sample_ids']}"
        )
    body.append(
        "Recent = a row that just hit this state without its canonical "
        "field (a live write-path bug); the legacy backlog is excluded."
    )
    return "\n".join(body)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default=None,
        help="DB path override (default: canonical trade_journal_db_path()).",
    )
    p.add_argument(
        "--window-hours",
        type=float,
        default=DEFAULT_WINDOW_HOURS,
        help="RECENT window in hours, keyed on COALESCE(closed_at, created_at, "
        "timestamp). recent_count > 0 in this window is what alerts. "
        "Default: %(default)s.",
    )
    p.add_argument(
        "--pnl-grace-hours",
        type=float,
        default=DEFAULT_PNL_GRACE_HOURS,
        help="INV-2 broker-sweep grace: a closed row may carry pnl NULL until "
        "its close is this many hours old (the deferred Bybit broker-pnl / "
        "local sweep window). Default: %(default)s.",
    )
    p.add_argument(
        "--fail-on-alert",
        action="store_true",
        help="Exit 1 (instead of 0) when any_alert. Default off — this is a "
        "reporter, not a gate.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human summary on stderr (JSON to stdout only).",
    )
    args = p.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    try:
        report = run_checks(
            db_path,
            window_hours=args.window_hours,
            pnl_grace_hours=args.pnl_grace_hours,
        )
    except sqlite3.Error as exc:
        print(
            json.dumps(
                {
                    "generated_at": _iso(datetime.now(timezone.utc)),
                    "db_path": db_path,
                    "error": f"{type(exc).__name__}: {exc}",
                    "checks": [],
                    "any_alert": False,
                }
            )
        )
        print(f"check_db_integrity: DB error ({db_path}): {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, default=str))
    if not args.quiet:
        print(render_summary(report), file=sys.stderr)

    if args.fail_on_alert and report["any_alert"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
