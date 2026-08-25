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
from src.runtime.dead_leg import bucket_for, eval_state_for, verdict_for  # noqa: E402


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


def _eval_liveness(conn: sqlite3.Connection, days: int) -> Optional[Dict[str, Dict[str, Any]]]:
    """Per-STRATEGY evaluation liveness from the ``signals`` dual-write.

    Returns ``None`` — *we could not look* — when the table is absent or the
    read raises, so the caller can render `unknown` rather than an all-zero map
    that would grade every leg `never_evaluated`. That distinction is the whole
    point: ``SIGNAL_DUAL_WRITE_DISABLED`` is a supported configuration, and a
    detector that read its absence as "no leg has ever evaluated" would alarm on
    the entire fleet.

    Keyed by strategy, NOT by (account, strategy): ``signals`` carries no
    ``account_id``, and a strategy that stops evaluating stops for every account
    it is routed to. The audit attaches the same state to each of that
    strategy's legs and the report says so.

    The timestamp normalisation is load-bearing. ``signals.logged_at_utc`` is
    ISO-8601 with a ``T`` separator and an offset (``2026-08-24T19:59:43.381912+00:00``)
    while ``datetime('now', ?)`` yields ``YYYY-MM-DD HH:MM:SS``. Compared raw as
    strings those agree on the date but disagree on character 11 (``T`` 0x54 vs
    space 0x20), so every row on the boundary DATE would sort as in-window
    regardless of its time. Normalising both to the same 19-char shape removes
    that off-by-one-day edge instead of leaving it to be rediscovered.
    """
    try:
        cur = conn.execute(
            """
            SELECT strategy,
                   COUNT(*)                                              AS ever,
                   SUM(CASE WHEN substr(replace(logged_at_utc,'T',' '),1,19)
                             >= datetime('now', ?) THEN 1 ELSE 0 END)    AS in_window,
                   MAX(logged_at_utc)                                    AS last_eval
              FROM signals
             WHERE strategy IS NOT NULL
             GROUP BY strategy
            """,
            (f"-{int(days)} days",),
        )
        rows = cur.fetchall()
    except sqlite3.Error:
        return None
    return {
        r[0]: {"ever": r[1] or 0, "in_window": r[2] or 0, "last_eval_utc": r[3]}
        for r in rows
    }


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

        # `entry_reason` rides the GROUP BY so `bucket_for` can separate a
        # DECLARED policy skip from a real refusal. Without it this report
        # called `bucket_for(status)` with no reason and could never reach
        # `policy_skipped` — so it graded a `mode: dry_run` account
        # `signalled_never_placed` (maximally alarming, and wrong) while the
        # live alert that shares this exact vocabulary graded the same rows
        # `refusing_by_declaration`. `src/runtime/dead_leg.py` exists to stop
        # the report and the alert disagreeing about a row; the two had drifted
        # anyway, because only one of them passed the second argument.
        cur = conn.execute(
            """
            SELECT account_id, strategy_name, status, entry_reason, COUNT(*) AS n
              FROM trades
             WHERE is_backtest = 0
               AND COALESCE(created_at, timestamp) >= datetime('now', ?)
             GROUP BY account_id, strategy_name, status, entry_reason
            """,
            (f"-{int(days)} days",),
        )
        rows = cur.fetchall()
        evals = _eval_liveness(conn, days)
    finally:
        conn.close()

    legs: Dict[tuple, Dict[str, Any]] = {}
    for account_id, strategy, status, entry_reason, n in rows:
        key = (account_id or "?", strategy or "?")
        leg = legs.setdefault(key, {
            "account_id": key[0], "strategy": key[1],
            "placed": 0, "refused": 0, "policy_skipped": 0, "other": 0,
            "by_status": {},
        })
        # ACCUMULATE, never assign. Now that `entry_reason` is in the GROUP BY
        # one status spans several rows (one per distinct reason), so the
        # previous `= n` kept only the last reason's count and silently
        # under-reported every status that had more than one.
        st = status or "?"
        leg["by_status"][st] = leg["by_status"].get(st, 0) + n
        # An unrecognised status is NOT silently folded into either bucket — a
        # new status the venue or the reconciler starts writing would otherwise
        # change every leg's verdict invisibly. `bucket_for` owns that rule.
        leg[bucket_for(status, entry_reason)] += n

    out: List[Dict[str, Any]] = []
    for leg in legs.values():
        total = (leg["placed"] + leg["refused"]
                 + leg["policy_skipped"] + leg["other"])
        leg["total_rows"] = total
        leg["verdict"] = verdict_for(leg)
        # Evaluation liveness — a SEPARATE axis, never folded into `verdict`.
        # A leg can be `evaluating` and `signalled_never_placed` at once.
        ev = (evals or {}).get(leg["strategy"]) or {}
        leg["eval_state"] = eval_state_for(
            ev.get("in_window"), ev.get("ever"), table_present=evals is not None,
        )
        leg["evals_in_window"] = ev.get("in_window") if evals is not None else None
        leg["last_eval_utc"] = ev.get("last_eval_utc") if evals is not None else None
        if leg["verdict"] == "partially_refused":
            leg["refusal_rate"] = round(leg["refused"] / total, 4) if total else None
        out.append(leg)

    out.sort(key=lambda r: (r["verdict"] != "signalled_never_placed", -r["refused"]))
    dead = [r for r in out if r["verdict"] == "signalled_never_placed"]

    # Strategies that have evaluated before and evaluated ZERO times in the
    # window. Sourced from `signals`, so it is NOT limited to strategies that
    # produced a trade row — which is the entire reason it exists.
    stopped: List[Dict[str, Any]] = []
    for name, ev in sorted((evals or {}).items()):
        # `table_present` explicitly, though this loop cannot run with
        # `evals is None` (it iterates `(evals or {}).items()`). Relying on
        # that is the implicit coupling that let `bucket_for` drift: the
        # sibling call site 20 lines up passes it, and a reader comparing
        # them should not have to derive why one omits it.
        if eval_state_for(
            ev.get("in_window"), ev.get("ever"), table_present=evals is not None,
        ) == "not_evaluating":
            stopped.append({
                "strategy": name,
                "last_eval_utc": ev.get("last_eval_utc"),
                "evals_ever": ev.get("ever"),
            })

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
            "the window is ABSENT, not healthy — see `strategies_not_evaluating`, "
            "which is sourced from `signals` and therefore CAN see a leg that "
            "produced no trade row at all. `eval_state` is per STRATEGY (the "
            "`signals` table carries no account_id), so every leg of one "
            "strategy shares it."
        ),
        "legs_graded": len(out),
        "dead_legs": len(dead),
        # Whether the eval axis was READABLE at all. False => every `eval_state`
        # below is `unknown` and none of them is evidence of anything.
        "eval_liveness_present": evals is not None,
        # The case the leg table structurally CANNOT reach. Legs are built from
        # `trades` rows, so a strategy that stopped running produces no row, no
        # leg, and no line — it just disappears, which is indistinguishable from
        # a strategy that ran all week and found no setup. This list is built
        # from `signals` instead: strategies that HAVE evaluated historically
        # and evaluated ZERO times in the window.
        "strategies_not_evaluating": stopped,
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
        "eval axis: " + (
            "READABLE" if report["eval_liveness_present"]
            else "UNREADABLE — every eval_state below is `unknown`, not `fine`"
        ),
        "",
        f"{'verdict':<34} {'eval':<16} {'account':<20} {'strategy':<28} "
        f"{'placed':>7} {'refused':>8} {'skip':>5} {'total':>6}",
        "-" * 132,
    ]
    for leg in report["legs"]:
        lines.append(
            f"{leg['verdict']:<34} {leg['eval_state']:<16} "
            f"{leg['account_id']:<20} {leg['strategy']:<28} "
            f"{leg['placed']:>7} {leg['refused']:>8} "
            f"{leg['policy_skipped']:>5} {leg['total_rows']:>6}"
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

    stopped = report["strategies_not_evaluating"]
    if stopped:
        lines += [
            "",
            f"STRATEGIES THAT STOPPED EVALUATING ({len(stopped)}) — these produce "
            "no trade rows, so they appear in NO leg line above:",
        ]
        for st in stopped:
            lines.append(
                f"  ⚠️  {st['strategy']:<30} last eval {st['last_eval_utc']} "
                f"({st['evals_ever']} ever)"
            )
        lines += [
            "",
            "  ⚠️  A leg stops evaluating whenever its VENUE IS SHUT. On a window "
            "narrower than",
            "      the longest venue closure this list fills with US-equity legs "
            "every night and",
            "      every weekend, correctly and uselessly. Check the window "
            "against the venue",
            "      before reading any of these as a defect.",
        ]
    elif not report["eval_liveness_present"]:
        lines += [
            "",
            "Evaluation liveness UNREADABLE (no `signals` table, or the read "
            "raised) — this",
            "is `we did not look`, NOT `every leg is running`. Check "
            "SIGNAL_DUAL_WRITE_DISABLED.",
        ]
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
