#!/usr/bin/env python3
"""Find LEGS THAT SIGNAL BUT NEVER PLACE — the gap no existing check can see.

WHY THIS EXISTS (2026-08-14, docs/research/WORKPLAN-2026-08-14.md Lane 0).

Two checks already watch for a dead strategy, and BOTH measure a different thing
than "can this leg actually get a position on the exchange":

  * `/health-review`'s **strategy silence** check asks whether an enabled
    strategy emits per-tick ``*_eval`` events. A leg that evaluates fine,
    produces signals, and then has every single order bounced by the venue is
    NOT silent — it is loudly failing at the last step, and grades `ok`.
  * `account_reachability_alert` probes ``account_open_positions``. An account
    whose ``positions()`` answers while ``balance()`` returns None reads UP
    while refusing every signal it is routed
    (``BL-20260814-REACHABILITY-PROBES-POSITIONS-NOT-BALANCE``).

So the failure mode "declared live, evaluates, signals, places NOTHING" sits in
the gap between them. It is not hypothetical — it is the shape of at least three
filed rows:

  * ``BL-20260810-ICTSCALP-AVAX-QTY-EXCEEDS-VENUE-MAX`` — every order
    EXCHANGE_REJECTED over Bybit's max_qty; the leg had been promoted to
    ``execution: live`` by M27 and was contributing zero fills.
  * ``BL-20260813-ALPACA-BALANCE-NONE-WHILE-ACCOUNT-READS-ACTIVE`` — two
    accounts (33 routed strategies) refusing every signal. That row records it
    was "found incidentally while verifying an unrelated fix — which is the only
    reason it was found at all; nothing alerted."
  * ``BL-20260814-VENUE-MAX-NONE-CANNOT-SAY-WE-COULD-NOT-LOOK`` — the clamp that
    was supposed to fix the first one, silently not engaging.

Each was found by a human reading a backlog months later. This makes the class
queryable in one command.

WHAT IT DOES NOT DO. It reports; it decides nothing and changes nothing. A leg
flagged `signalled_never_placed` may be correct (a strategy whose gate is doing
its job), so the verdicts are descriptive and the operator judges. Read-only:
opens SQLite ``mode=ro`` and issues SELECTs only.

STATES ARE NEVER COLLAPSED (``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed
states"). "This leg produced no rows at all" and "this leg produced rows and
every one was refused" are opposite findings — the first may be a quiet market,
the second is a broken leg — so they are separate verdicts and the denominator
is always printed beside the verdict.

    python3 scripts/ops/dead_leg_audit.py --days 7
    python3 scripts/ops/dead_leg_audit.py --days 30 --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any, Dict, List, Optional

# The status vocabulary + the verdict rule live in `src/runtime/dead_leg.py`,
# because the LIVE latched alert (`src/runtime/silent_refusal_alert.py`) grades
# the same column and two copies would drift — this report calling a leg healthy
# while the alert calls it dead. The status tuples themselves are deliberately
# NOT re-exported here: nothing imports them from this module, and a re-export
# kept "for compatibility" with no importer is just a second name for the same
# constant, which is the drift this move exists to remove.
from src.runtime.dead_leg import bucket_for, verdict_for  # noqa: E402


def _resolve_db(explicit: Optional[str]) -> str:
    """``--db`` if given, else the ONE canonical resolver. No third path.

    The first draft of this function had a "helpful" fallback chain that read
    ``TRADE_JOURNAL_DB`` and then ``DATA_DIR`` inline when the import failed.
    `canonical-db-resolver` rejected it on the first run, correctly: an inline
    env-read is a SECOND definition of where the journal lives, free to drift
    from `src.utils.paths.trade_journal_db_path()`, and that drift is what
    seeded the stray duplicate journals the guard exists to prevent. A resolver
    that is right 99% of the time is worse than no fallback, because the 1%
    writes a report about a database nobody else is reading.

    So an unresolvable path is a hard stop that tells the caller to be explicit,
    rather than a guess.
    """
    if explicit:
        return explicit
    try:
        from src.utils.paths import trade_journal_db_path
    except Exception as exc:  # noqa: BLE001 — the resolver needs the repo importable
        raise SystemExit(
            f"could not import the canonical journal resolver ({exc}) — run from "
            "the repo root or pass --db explicitly. Deliberately NOT falling back "
            "to an env-read: see canonical-db-resolver."
        )
    return str(trade_journal_db_path())


def audit(db: str, days: int) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # DENOMINATOR FIRST — measured 2026-08-14, and this is not hypothetical.
        # The trainer VM carries TWO journals: the db-puller writes the real one
        # to `<repo>/data/trade_journal.db` (837 MB, 4649 non-backtest rows,
        # 12 accounts, current), while `src.utils.paths.trade_journal_db_path()`
        # on that box resolves to `<repo>/trade_journal.db` — 8.5 MB, **ZERO**
        # rows, untouched since 2026-08-02. So the canonical resolver this
        # script deliberately trusts points at an EMPTY database there.
        #
        # Without this check the report renders "legs graded: 0 / No
        # signalled-never-placed legs in this window" — which is exactly the
        # UNPROVENANCED-DIAGNOSTIC sub-class C shape (an empty result read as a
        # clean negative), and it would be read as an all-clear by the reviewer
        # this tool exists to inform. A dead-leg audit that reports "nothing
        # wrong" because it queried the wrong file is worse than no audit.
        #
        # So: prove the table has rows AT ALL before believing anything about
        # the window. A zero-row journal is a HARD STOP naming both the path and
        # the likely sibling, never a clean report.
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE is_backtest = 0"
        ).fetchone()[0]
        if not total_rows:
            raise SystemExit(
                f"REFUSING TO REPORT: {db} contains ZERO non-backtest `trades` "
                "rows, so an empty result would say nothing about the system. "
                "This is the stray-journal trap (see canonical-db-resolver) — "
                "on the trainer VM the canonical resolver points at an empty "
                "repo-root journal while the real one is under `data/`. Pass "
                "--db explicitly with the populated journal."
            )

        cur = conn.execute(
            """
            SELECT account_id, strategy_name, status, COUNT(*) AS n
              FROM trades
             WHERE is_backtest = 0
               AND COALESCE(created_at, timestamp) >= datetime('now', ?)
             GROUP BY account_id, strategy_name, status
            """,
            (f"-{int(days)} days",),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    legs: Dict[tuple, Dict[str, Any]] = {}
    for account_id, strategy, status, n in rows:
        key = (account_id or "?", strategy or "?")
        leg = legs.setdefault(key, {
            "account_id": key[0], "strategy": key[1],
            "placed": 0, "refused": 0, "other": 0,
            "by_status": {},
        })
        leg["by_status"][status or "?"] = n
        # An unrecognised status is NOT silently folded into either bucket — a
        # new status the venue or the reconciler starts writing would otherwise
        # change every leg's verdict invisibly. `bucket_for` owns that rule.
        leg[bucket_for(status)] += n

    out: List[Dict[str, Any]] = []
    for leg in legs.values():
        total = leg["placed"] + leg["refused"] + leg["other"]
        leg["total_rows"] = total
        leg["verdict"] = verdict_for(leg)
        if leg["verdict"] == "partially_refused":
            leg["refusal_rate"] = round(leg["refused"] / total, 4) if total else None
        out.append(leg)

    out.sort(key=lambda r: (r["verdict"] != "signalled_never_placed", -r["refused"]))
    dead = [r for r in out if r["verdict"] == "signalled_never_placed"]

    return {
        "db": db,
        "window_days": days,
        # The denominator that licenses every negative below, carried in the
        # payload so a JSON consumer sees it too rather than only the CLI reader.
        "nonbacktest_rows_in_db": total_rows,
        # POPULATION, stated rather than implied: legs with ZERO rows in the
        # window do not appear here at all. A leg absent from this report has
        # not been graded — that is "we did not observe it", not "it is fine".
        "population": (
            f"non-backtest `trades` rows created in the last {days} days, "
            "grouped by (account_id, strategy_name). A leg with zero rows in "
            "the window is ABSENT, not healthy."
        ),
        "legs_graded": len(out),
        "dead_legs": len(dead),
        "legs": out,
    }


def _render(report: Dict[str, Any]) -> str:
    lines = [
        f"dead-leg audit — {report['db']}",
        f"window: last {report['window_days']}d · legs graded: {report['legs_graded']}"
        f" · SIGNALLED-NEVER-PLACED: {report['dead_legs']}",
        f"denominator: {report['nonbacktest_rows_in_db']} non-backtest rows in this DB",
        f"population: {report['population']}",
        "",
        f"{'verdict':<38} {'account':<22} {'strategy':<30} "
        f"{'placed':>7} {'refused':>8} {'total':>6}",
        "-" * 118,
    ]
    for leg in report["legs"]:
        lines.append(
            f"{leg['verdict']:<38} {leg['account_id']:<22} {leg['strategy']:<30} "
            f"{leg['placed']:>7} {leg['refused']:>8} {leg['total_rows']:>6}"
        )
    for leg in report["legs"]:
        if leg["verdict"] == "signalled_never_placed":
            lines += [
                "",
                f"  ⚠️  {leg['account_id']} / {leg['strategy']} — "
                f"{leg['refused']} rows, ZERO reached the exchange:",
                f"      {json.dumps(leg['by_status'])}",
            ]
    if not report["dead_legs"]:
        lines += ["", "No signalled-never-placed legs in this window."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="journal path (default: canonical resolver)")
    ap.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    report = audit(_resolve_db(args.db), args.days)
    print(json.dumps(report, indent=2) if args.json else _render(report))
    # Exit 0 always: this is a REPORT, not a gate. A non-zero exit would invite
    # wiring it into CI as a pass/fail, and a flagged leg needs judgement (a
    # correctly-gating strategy looks identical to a broken one from here).
    return 0


if __name__ == "__main__":
    sys.exit(main())
