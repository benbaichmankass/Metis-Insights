"""GET /api/bot/performance — windowed aggregate performance stats.

Tier-1 read endpoint built for the Android Performance tab (retired from the live feed 2026-09-01; the SPA and any other
consumer that wants headline trade analytics over a selectable window).

Why this exists: the consumers previously pulled ``/api/bot/trades/closed``
(capped at 200 rows) and aggregated client-side. With more than 200 closed
trades that made the headline "Trades" count freeze at 200 and skewed every
derived metric (win rate, expectancy, equity curve) to the most recent 200
fills only. This endpoint computes the aggregates in SQL over the **full**
trade history within the requested window — no row cap — so the numbers are
correct regardless of how many trades the bot has taken.

Window (``?window=``):
  - ``24h`` — trades closed in the last 24 hours.
  - ``7d``  — last 7 days.
  - ``30d`` — last 30 days.
  - ``all`` — all closed trades (default).

The close-time basis is the canonical ``trades.closed_at`` column (P1-B),
falling back to ``COALESCE(t.closed_at, op.updated_at, t.timestamp)`` for rows predating
that column / its backfill — i.e. ``COALESCE(t.closed_at, op.updated_at,
t.timestamp)``. So ``window=24h`` is a true rolling-24h window keyed on real
close time. Backtest + paper rows are excluded from the top-level figures so
they reflect live money, exactly like ``/api/bot/stats``.

Wire shape (camelCase):

    {
      "window": "7d",
      "since": "2026-05-22T09:00:00+00:00" | null,
      "totalTrades": 412,
      "wins": 250,
      "losses": 150,
      "winRate": 60.7,                  # percent, winners / closed × 100
      "totalPnl": 1234.56,
      "expectancy": 3.0,                # totalPnl / totalTrades
      "perStrategy": [
        {"name": "vwap", "trades": 120, "wins": 70, "winRate": 58.3,
         "totalPnl": 540.2, "expectancy": 4.5}
      ],
      "perExitPath": [                  # worst coverage FIRST, not best PnL
        {"exitPath": "pairs_stop", "trades": 40, "wins": 12, "winRate": 30.0,
         "totalPnl": -80.1, "totalPnlMeasured": 0.0,
         "pnlMeasuredCount": 0, "pnlEstimatedCount": 0, "pnlCoverage": 0.0,
         # Is the bucket KEY itself evidence? Counts, never a ratio — an
         # AUTHORED path (pairs_*, sl_cross, ...) never reaches the exit
         # classifier, so ~100% unattested is CORRECT there. The number that
         # matters sits on `reconciler_filled`.
         "labelAttestedCount": 0, "labelRefusedCount": 0,
         "labelUnresolvedCount": 0, "labelUnattestedCount": 40}
      ],
      "equity": [{"t": "2026-05-22T09:01:00+00:00", "cum": 12.5}]  # oldest→newest
    }

Best-effort: returns a zeroed envelope on a missing/locked DB so the consumer
keeps the tab usable. Tier 1 — no auth, no secrets in the response.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.utils.paths import trade_journal_db_path
from src.web.api._asset_class import CLASS_ORDER, asset_class_for_symbol
from src.core.profile_loader import contract_value_usd_for
from src.web.api._clean_trades import (
    exclude_reconciler_predicate,
    exclude_reset_flat_predicate,
    exclude_superseded_predicate,
    not_paper_predicate,
    paper_predicate,
)
from src.web.api._closed_at import close_time_sql
from src.runtime.broker_truth import journal_trust_for, journal_trust_map
from src.runtime.r_provenance import (  # R-DENOMINATOR provenance
    DISAGREEMENT_RATIO_BAR,
    R_CONFIRMED_INITIAL,
    R_CONTAMINATED,
    R_NO_BASIS,
    R_UNVERIFIED,
    R_BASIS_DECLARED,
    R_BASIS_NO_BASIS,
    R_BASIS_REFUSED_WRONG_SIDE,
    R_BASIS_STORED_STOP,
    classify_r,
    disagreement_ratio,
    empty_basis_counts as r_empty_basis_counts,
    empty_counts as r_empty_counts,
    r_multiple_provenanced,
)
from src.runtime.provenance import (
    # The "no value present" sentinel `classify_row` returns as its raw half.
    # Imported rather than re-spelled: a second copy of "(none)" here would be
    # free to drift from the producer's, and this comparison is what separates
    # "the classifier never ran" from "it ran and declined".
    _ABSENT_RAW as _ABSENT_LABEL_SOURCE,
    ESTIMATED,
    EXIT_LABEL_REFUSED_UNMEASURED,
    FABRICATED,
    MEASURED,
    UNVERIFIED,
    classify_pnl,
    classify_row,
    coverage,
)

logger = logging.getLogger(__name__)

# Canonical close-time basis, epoch-ms-aware. The reconciler-filled close path
# writes ``trades.closed_at`` as a raw epoch-milliseconds string; an unguarded
# ``datetime(closed_at)`` returns NULL in SQLite and silently drops those rows
# from the window (the "/performance shows 0 closed trades" bug). This mirrors
# the basis ``/api/bot/trades/closed`` already uses — see src/web/api/_closed_at.py.
_CLOSE_TIME_SQL = close_time_sql("t.closed_at", "op.updated_at", "t.timestamp")

router = APIRouter(prefix="/api/bot", tags=["bot"])

_DB_PATH = Path(trade_journal_db_path())

# window token → lookback timedelta. ``all`` maps to None (no since filter).
_WINDOWS: Dict[str, Optional[timedelta]] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}

# Cap on equity-curve points returned. The aggregates are uncapped, but the
# point-by-point equity series is only for sparkline rendering — a few hundred
# points is plenty and keeps the mobile payload small. When the window holds
# more closed trades than this we down-sample evenly (keep newest exact).
_MAX_EQUITY_POINTS = 500


def _window_since(window: str) -> Optional[str]:
    """ISO-8601 UTC cutoff for *window*, or None for the all-time window."""
    delta = _WINDOWS.get(window)
    if delta is None:
        return None
    return (datetime.now(timezone.utc) - delta).isoformat()


def _empty(window: str, since: Optional[str], error: bool = False) -> Dict[str, Any]:
    """Zeroed aggregate. ``error`` distinguishes a genuine *no-trades* window
    (``error=False``) from a DB read failure (``error=True``) so a consumer
    never renders a fabricated ``$0.00`` over an outage — it can show "—"
    instead. ``profitFactor`` / ``maxDrawdown`` are null (not 0) when there is
    nothing to compute them from."""
    return {
        "window": window,
        "since": since,
        "error": error,
        # Present even here: a key that disappears on an empty/errored window
        # makes a consumer branch on absence, and absence is not one of the
        # states. No rows means no accounts to grade — NOT "nothing diverges".
        "journalTrust": {
            "readState": journal_trust_map().get("read_state"),
            "accountsKnownDivergent": [],
            "accountsUnrecorded": [],
            "accountsUnreadable": [],
        },
        "totalTrades": 0,
        "wins": 0,
        "losses": 0,
        "winRate": 0.0,
        "totalPnl": 0.0,
        "expectancy": 0.0,
        "totalR": None,
        "expectancyR": None,
        "rTradeCount": 0,
        "rCoverage": 0.0,
        # Present on the empty/errored envelope too, with explicit zeros — a
        # key that disappears makes a consumer branch on absence, and absence is
        # not one of the states.
        "rBasis": {
            "declaredInitial": 0, "storedStop": 0,
            "refusedWrongSide": 0, "noBasis": 0,
        },
        # R-denominator provenance — same rule.
        "rProvenance": {
            "contaminated": 0, "confirmedInitial": 0, "unverified": 0,
            "noBasis": 0, "tightenedVsDeclared": 0, "declaredRiskRecords": 0,
            "ratioBar": DISAGREEMENT_RATIO_BAR,
        },
        # PnL provenance — null (not 0.0) here so an empty/errored window stays
        # distinguishable from a window in which nothing was measured. That
        # exact distinction is what `coverage()` exists to preserve.
        "pnlCoverage": None,
        # Measured-PnL sum — 0.0 here (an empty/errored window measured nothing,
        # so its measured sum is genuinely 0), while pnlCoverage stays null to
        # keep "no rows" distinguishable from "rows, none measured". The R4 gate
        # keys its abstain on pnlCoverage, never on this sum alone.
        "totalPnlMeasured": 0.0,
        "pnlMeasuredCount": 0,
        "pnlEstimatedCount": 0,
        "pnlFabricatedCount": 0,
        "pnlUnverifiedCount": 0,
        "profitFactor": None,
        "maxDrawdown": None,
        "perStrategy": [],
        "perExitPath": [],
        "perAssetClass": [],
        "perSymbol": [],
        "equity": [],
    }


# "Paper" / "not paper" SQL predicates + reconciler-artifact exclusion, from
# the canonical src.web.api._clean_trades helper (single source of truth — see
# that module's docstring). Joined ``trades`` alias is ``t``.
_PAPER_PREDICATE = paper_predicate("t.")
_NOT_PAPER_PREDICATE = not_paper_predicate("t.")
# Drop reconciler ``orphan_adopt`` rows from the strategy-performance aggregates
# — they are a recovery/bookkeeping state, not a strategy's trade.
_EXCLUDE_RECONCILER = exclude_reconciler_predicate("t.")
# Drop superseded phantom orphan-flap duplicates (void-flagged by the
# historical reconciliation pass, orphan-flap hardening #5) from the aggregates.
_EXCLUDE_SUPERSEDED = exclude_superseded_predicate("t.") + exclude_reset_flat_predicate("t.")


def _query(
    db_path: Path,
    since: Optional[str],
    demo: bool = False,
    account_ids: Optional[List[str]] = None,
) -> List[sqlite3.Row]:
    """Closed (non-backtest) trades within *since*, oldest→newest.

    ``demo=False`` (default) → real-money rows only.
    ``demo=True``            → paper-account rows only.
    ``account_ids`` (optional) → additionally restrict to those account ids
    (used for the ``paperPortfolio`` sub-block — the live-portfolio-mirror
    paper books, S-PAPER-PORTFOLIO 2026-07-16). Empty/None → no restriction.

    Rows with ``pnl IS NULL`` are excluded — the reconciler fallback path
    in ``order_monitor.py`` closes trades with a NULL pnl when the broker
    close-pnl lookup fails (``exit_reason='reconciler_incomplete'``).
    Including them in the aggregates either as zeros or as wins/losses
    distorts win-rate / expectancy / equity curve in misleading ways
    (the "0-pnl closed trade" complaint, 2026-06-04).

    Oldest-first ordering lets the caller build the cumulative equity curve in a
    single pass without re-sorting.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # De-dup the order_packages join to exactly one row per trade. A raw
        # `LEFT JOIN order_packages ON linked_trade_id = t.id` FANS OUT when a
        # trade has >1 linked order package (entry + protective re-arm, retries,
        # etc.) — that trade's pnl/win would then be counted N times, inflating
        # totalTrades / winRate / totalPnl. Pre-aggregating to one updated_at
        # per linked_trade_id keeps the join 1:1 so each trade contributes
        # exactly once — the canonical "one row per closed trade" basis the rest
        # of the API uses, and the reason /performance could disagree with
        # /stats on real-money totals (single source of truth).
        # R-multiple inputs (entry/stop/size) are OPTIONAL: select them only when
        # the trades table actually has the columns, so a minimal/legacy schema
        # makes R degrade to None (rCoverage 0) instead of erroring the endpoint.
        avail = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        r_select = "".join(
            f"\n                   t.{col} AS {alias},"
            for col, alias in (
                ("entry_price", "entry_price"),
                ("stop_loss", "stop_loss"),
                ("position_size", "qty"),
                # R-DENOMINATOR provenance inputs (src.runtime.r_provenance).
                # `direction` drives the wrong-side proof; `take_profit_1` is
                # the mirrored-bracket discriminator that keeps an
                # intent_reduce row from reading as a trailed stop. Both ride
                # the SAME optional-column guard as the R inputs above: a
                # legacy schema degrades the grade to `unverified`
                # ("we could not look"), never to a confirmation.
                ("direction", "direction"),
                ("take_profit_1", "take_profit_1"),
            )
            if col in avail
        )
        # The INDEPENDENT initial-risk record: `order_packages.meta` carries the
        # strategy's signal-time `risk_per_unit`, and `order_monitor
        # ._apply_update` writes only `sl`/`tp`, so a trailing amend cannot
        # reach it. Joined on the order_packages PRIMARY KEY via
        # `trades.order_package_id` — a 1:1 join by construction, so it cannot
        # reintroduce the fan-out the `linked_trade_id` join below is
        # pre-aggregated to avoid. Optional for the same reason as above.
        # MEASURED 2026-09-02: 965 of 1346 closed non-backtest rows carry an
        # order_package_id, so ~28% of the population is unreachable this way
        # and lands `unverified` — which is the honest grade, not a gap to
        # paper over.
        meta_select = (
            "\n                   opk.meta AS package_meta,"
            if "order_package_id" in avail else "")
        meta_join = (
            "\n            LEFT JOIN order_packages opk"
            "\n              ON opk.order_package_id = t.order_package_id"
            if "order_package_id" in avail else "")
        # `notes` carries the provenance keys behind pnlCoverage, and is OPTIONAL
        # for exactly the same reason the R inputs above are: selecting it
        # unconditionally makes a schema without the column raise
        # `no such column: t.notes`, which the caller turns into a ZEROED envelope
        # — every metric for every window blanked to buy one coverage figure.
        # A missing column degrades pnlCoverage to 0/unverified (the honest
        # reading: no provenance was recorded) and leaves the rest intact.
        notes_select = "\n                   t.notes AS notes," if "notes" in avail else""
        # `exit_reason` backs `perExitPath` and is selected UNCONDITIONALLY —
        # deliberately NOT wrapped in the `avail` guard that `notes` and the R
        # inputs use. Those are genuinely optional; this one is not: the
        # reset-flat exclusion appended below
        # (`_clean_trades.exclude_reset_flat_predicate`) already references
        # `t.exit_reason` on every query, so a trades table without the column
        # has never been able to serve this endpoint at all. A guard that cannot
        # fire is worse than no guard — it advertises a degradation path that
        # does not exist, and the next reader would trust it.
        exit_select = "\n                   t.exit_reason AS exit_reason,"
        sql = f"""
            SELECT t.strategy_name,
                   t.symbol AS symbol,
                   t.account_id AS account_id,
                   t.pnl AS pnl,{r_select}{notes_select}{exit_select}{meta_select}
                   {_CLOSE_TIME_SQL} AS closed_at
            FROM trades t
            LEFT JOIN (
                SELECT linked_trade_id, MIN(updated_at) AS updated_at
                FROM order_packages
                WHERE linked_trade_id IS NOT NULL
                GROUP BY linked_trade_id
            ) op ON op.linked_trade_id = t.id{meta_join}
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
              AND t.pnl IS NOT NULL
        """
        sql += _PAPER_PREDICATE if demo else _NOT_PAPER_PREDICATE
        sql += _EXCLUDE_RECONCILER
        sql += _EXCLUDE_SUPERSEDED
        params: List[Any] = []
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            sql += f" AND t.account_id IN ({placeholders})"
            params.extend(account_ids)
        if since:
            sql += f" AND {_CLOSE_TIME_SQL} >= datetime(?)"
            params.append(since)
        sql += f" ORDER BY {_CLOSE_TIME_SQL} ASC"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _downsample(points: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """Evenly thin *points* to at most *cap*, always keeping the last point."""
    n = len(points)
    if n <= cap:
        return points
    step = n / cap
    out = [points[int(i * step)] for i in range(cap)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def _rget(row: sqlite3.Row, key: str) -> Any:
    """Row value, or ``None`` when the column wasn't selected (the optional
    R-multiple inputs degrade gracefully on a minimal/legacy trades table)."""
    return row[key] if key in row.keys() else None


def _aggregate(rows: List[sqlite3.Row], window: str, since: Optional[str]) -> Dict[str, Any]:
    total = len(rows)
    if total == 0:
        return _empty(window, since)

    wins = 0
    gross_profit = 0.0   # sum of winning-trade pnl (for profit factor)
    gross_loss = 0.0     # abs sum of losing-trade pnl
    total_pnl = 0.0
    total_pnl_measured = 0.0   # sum of pnl over MEASURED+ESTIMATED rows only
    total_r = 0.0          # sum of per-trade R over R-measurable trades only
    r_count = 0            # # trades with a computable R (entry+stop+size known)
    pnl_prov: Dict[str, int] = {}   # pnl-provenance split (measured/…/unverified)
    per: Dict[str, Dict[str, float]] = {}
    per_class: Dict[str, Dict[str, float]] = {}
    per_symbol: Dict[str, Dict[str, Any]] = {}
    per_exit: Dict[str, Dict[str, float]] = {}
    # R-DENOMINATOR provenance. `rCoverage` above says how MUCH of the window is
    # R-measurable; this says whether the risk each R was measured AGAINST was
    # the trade's INITIAL stop at all. `trades.stop_loss` holds the CURRENT
    # trailed stop (order_monitor._apply_update mirrors every confirmed amend
    # onto the row), so a stop trailed to breakeven leaves |entry-stop| near
    # zero and pnl/risk explodes. Nothing is EXCLUDED here — publishing the
    # count and dropping the rows are opposite moves, and dropping them would
    # convert a visible-wrong number into an invisible-wrong one over an
    # unstated population.
    r_prov: Dict[str, int] = r_empty_counts()
    # WHICH risk each published R was divided by. A different question from
    # `r_prov` above (which grades the stored stop) and never a renaming of it:
    # a row graded `unverified` can still be computed on the `declared_initial`
    # basis, because the declared record is independent of the stored stop.
    r_basis_counts: Dict[str, int] = r_empty_basis_counts()
    r_declared_records = 0     # denominator for the line below
    r_tightened = 0            # stored stop >= BAR x tighter than declared risk
    equity: List[Dict[str, Any]] = []
    cum = 0.0
    peak = 0.0           # running equity peak for max-drawdown
    max_dd = 0.0         # most negative (peak - trough) seen, <= 0
    for r in rows:
        pnl = float(r["pnl"] or 0.0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += -pnl
        # R-multiple: pnl normalised by the trade's own risk so a micro crypto
        # trade and a futures contract compare on one axis. None when risk is
        # unknown (missing stop/size); then it counts in NEITHER R numerator nor
        # denominator — never a raw-pnl fallback (the blending bug).
        # ⚠️ THE DENOMINATOR IS THE TRADE'S *INITIAL* RISK, NOT ITS STORED STOP
        # (2026-09-06, MI-144). `trades.stop_loss` holds the FINAL trailed stop
        # — `order_monitor._apply_update` mirrors every confirmed amend onto the
        # row — so `|entry - stop|` collapses on a trade trailed through
        # breakeven, and the legacy `abs()` turned a stop on the WRONG SIDE of
        # entry into a small positive risk instead of refusing it. That single
        # mechanism made a LOSING window publish a POSITIVE expectancyR.
        #
        # MEASURED, live journal pulled 2026-09-06 via /api/bot/db/table/
        # {trades,order_packages} (5518 + 4435 rows; reproduction of this
        # endpoint's own totals asserted first as a positive control):
        #   30d real-money window, n=39 — published totalR +38.2891 /
        #   expectancyR +0.9818 against totalPnl -3.6266 and profitFactor
        #   0.9507. 12 rows (30.8%) graded `contaminated` carried 117.1% of
        #   that R.
        #   WHOLE journal, n=1287 — 104 contaminated rows (8.1%) carried 96.6%
        #   of totalR (+4232.03 of +4381.91). Max single-row R: +3672.3
        #   (`ict_scalp_sol_15m`, bybit_1).
        #
        # `r_multiple_provenanced` prefers the signal-time `risk_per_unit` from
        # `order_packages.meta` (which no trailing amend can reach), falls back
        # to the stored stop when there is no declared record and the stop is
        # not PROVEN wrong-side, and REFUSES the proven-wrong-side row rather
        # than abs()-ing it. A refusal counts in NEITHER the R numerator nor its
        # denominator — the same discipline a missing stop already gets, never a
        # raw-pnl fallback. `rBasis` below publishes which basis every row used,
        # so no published R is over an unstated population.
        rr, r_basis = r_multiple_provenanced(
            {
                "pnl": r["pnl"],
                "entry_price": _rget(r, "entry_price"),
                "stop_loss": _rget(r, "stop_loss"),
                "take_profit_1": _rget(r, "take_profit_1"),
                "direction": _rget(r, "direction"),
                "qty": _rget(r, "qty"),
                "package_meta": _rget(r, "package_meta"),
            },
            contract_value_usd_for(r["symbol"]),
        )
        r_basis_counts[r_basis] = r_basis_counts.get(r_basis, 0) + 1
        if rr is not None:
            total_r += rr
            r_count += 1
        # PnL PROVENANCE — is this number a measurement or a manufacture?
        # The exact sibling of the R-coverage discipline two lines up, applied
        # to the BASE metric instead of the derived one. That asymmetry is the
        # whole 2026-07-30 defect: `rCoverage` correctly refused to let partial
        # R-measurement masquerade as full, while the `pnl` it normalises was
        # silently fabricated for 64.9% of July's closed trades
        # (206 of 829 closed rows of `local_markprice` money). See src/runtime/provenance.
        pnl_bucket = classify_pnl(r)[0]
        pnl_prov[pnl_bucket] = pnl_prov.get(pnl_bucket, 0) + 1
        # R-DENOMINATOR provenance — the sibling of the line above, one
        # derivative up: that grades the R NUMERATOR (`pnl`), this grades the
        # DENOMINATOR (the risk basis). Four states, never collapsed, and
        # `unverified` is emphatically NOT "clean" — it is *we could not look*,
        # and it is the largest bucket by construction.
        r_state = classify_r({
            "direction": _rget(r, "direction"),
            "entry_price": _rget(r, "entry_price"),
            "stop_loss": _rget(r, "stop_loss"),
            "take_profit_1": _rget(r, "take_profit_1"),
            "qty": _rget(r, "qty"),
            "package_meta": _rget(r, "package_meta"),
        })[0]
        r_prov[r_state] = r_prov.get(r_state, 0) + 1
        r_ratio = disagreement_ratio(
            _rget(r, "entry_price"), _rget(r, "stop_loss"), _rget(r, "package_meta"))
        if r_ratio is not None:
            r_declared_records += 1
            if r_ratio >= DISAGREEMENT_RATIO_BAR:
                r_tightened += 1
        # Measured-PnL SUM (the value the R4 promotion gate reads instead of the
        # raw totalPnl — a leg is only judged on money that was actually
        # measured, never manufactured). MEASURED (broker fill / recorded exit)
        # AND ESTIMATED (a defensible close-anchored reconstruction) count; a
        # FABRICATED mark or an UNVERIFIED row is excluded — the same
        # {measured, estimated} subset `pnlProvenance` surfaces per-row.
        pnl_is_measured = pnl_bucket in (MEASURED, ESTIMATED)
        if pnl_is_measured:
            total_pnl_measured += pnl

        name = r["strategy_name"] or "(unknown)"
        bucket = per.setdefault(
            name,
            {"trades": 0.0, "wins": 0.0, "pnl": 0.0, "pnl_measured_sum": 0.0,
             "r": 0.0, "rc": 0.0, "pnl_measured": 0.0, "pnl_estimated": 0.0,
             "r_prov": r_empty_counts(), "r_basis": r_empty_basis_counts()},
        )
        bucket["trades"] += 1
        bucket["r_prov"][r_state] += 1
        bucket["r_basis"][r_basis] += 1
        if pnl > 0:
            bucket["wins"] += 1
        bucket["pnl"] += pnl
        if pnl_bucket == MEASURED:
            bucket["pnl_measured"] += 1
        elif pnl_bucket == ESTIMATED:
            # Counted SEPARATELY and published, because the count and the sum
            # below are over DIFFERENT populations (see the per-strategy dict).
            bucket["pnl_estimated"] += 1
        if pnl_is_measured:
            bucket["pnl_measured_sum"] += pnl
        if rr is not None:
            bucket["r"] += rr
            bucket["rc"] += 1
        # Per-EXIT-PATH provenance (2026-08-25,
        # BL-20260825-EXIT-PROVENANCE-IS-STRUCTURED-BY-EXIT-PATH-SIX-PATHS-AT-ZERO).
        # Coverage is published per STRATEGY above and was published nowhere per
        # exit path, so a path that has NEVER been measured could not be told
        # from one merely below a floor. Measured over all 1,347 closed
        # non-backtest rows on 2026-08-25: six paths sat at 0.0% broker truth
        # across 267 closes (the whole pairs sleeve, the whole intent-reduce
        # path, netting_attributed, reconciler_incomplete) while the global
        # figure read 42.9% — an average of a 66.9% path and a 0.0% path
        # describes neither.
        exit_path = str(_rget(r, "exit_reason") or "(unrecorded)")
        ebucket = per_exit.setdefault(
            exit_path,
            {"trades": 0.0, "wins": 0.0, "pnl": 0.0, "pnl_measured_sum": 0.0,
             "pnl_measured": 0.0, "pnl_estimated": 0.0,
             "label_attested": 0.0, "label_refused": 0.0,
             "label_unresolved": 0.0, "label_unattested": 0.0},
        )
        ebucket["trades"] += 1
        # LABEL attestation — is this row's membership of THIS BUCKET evidence,
        # or a default? (GATE 0 / G1.) `pnlCoverage` beside it grades the row's
        # MONEY; this grades the row's BUCKET KEY, and the two are independent:
        # a row can carry broker-truth pnl and still sit under a label nothing
        # ever checked. `exit_reason` is the bucket key here, and it is exactly
        # the field BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE shows
        # wrong for the majority of the rows it is applied to: the no-record
        # close path hard-codes `reconciler_filled` before any price exists, and
        # until #10262 nothing re-ran the classifier when the price arrived.
        #
        # FOUR STATES, NEVER COLLAPSED — `attested` (the classifier ran and
        # resolved) · `refused` (it ran and DECLINED, because the price it would
        # have compared was FABRICATED) · `unresolved` (it ran and the price sat
        # mid-bracket — a genuine non-bracket close) · `unattested` (it never
        # ran). Folding `refused` or `unresolved` into `unattested` would erase
        # the distinction between "we looked" and "we did not", which is the
        # absence semantics the whole defect class was found through.
        #
        # ⚠️ NO RATIO IS PUBLISHED, DELIBERATELY. A `labelCoverage` would imply
        # one denominator for every path, and there is not one: `sl_cross`,
        # `pairs_stop` and friends are AUTHORED by the producer that closed the
        # trade and never pass through `_classify_broker_exit` at all, so
        # `unattested` is the CORRECT state there, not a gap. Only the
        # reconciler-derived buckets ("", `reconciler_filled`) are ones the
        # classifier was ever meant to reach. Publishing a rate would re-commit
        # the exact error this block exists to expose — reading a number off a
        # population it does not describe.
        _label_src = str(classify_row(r, "exit_reason_source")[1] or "")
        if not _label_src or _label_src == _ABSENT_LABEL_SOURCE:
            ebucket["label_unattested"] += 1
        elif _label_src == EXIT_LABEL_REFUSED_UNMEASURED:
            ebucket["label_refused"] += 1
        elif _label_src == "unresolved":
            ebucket["label_unresolved"] += 1
        else:
            ebucket["label_attested"] += 1
        if pnl > 0:
            ebucket["wins"] += 1
        ebucket["pnl"] += pnl
        if pnl_bucket == MEASURED:
            ebucket["pnl_measured"] += 1
        elif pnl_bucket == ESTIMATED:
            ebucket["pnl_estimated"] += 1
        if pnl_is_measured:
            ebucket["pnl_measured_sum"] += pnl
        # asset-class breakdown (crypto / index / commodity / equity / fx)
        cls = asset_class_for_symbol(r["symbol"])
        cbucket = per_class.setdefault(
            cls, {"trades": 0.0, "wins": 0.0, "pnl": 0.0, "r": 0.0, "rc": 0.0}
        )
        cbucket["trades"] += 1
        if pnl > 0:
            cbucket["wins"] += 1
        cbucket["pnl"] += pnl
        if rr is not None:
            cbucket["r"] += rr
            cbucket["rc"] += 1
        # per-symbol breakdown (drives the dashboard's symbol-stacked asset bar).
        # Computed in this SAME loop over the SAME windowed rows as the class
        # total, so a class that reports a total can never render an empty
        # per-symbol split — the drift that left the client-side `/trades/closed`
        # aggregation blank (a recently-closed trade with a null closedAt that
        # `/performance` still counts).
        sym = str(r["symbol"] or "unknown")
        sbucket = per_symbol.setdefault(
            sym, {"assetClass": cls, "trades": 0.0, "wins": 0.0, "pnl": 0.0}
        )
        sbucket["trades"] += 1
        if pnl > 0:
            sbucket["wins"] += 1
        sbucket["pnl"] += pnl
        cum += pnl
        if cum > peak:
            peak = cum
        drawdown = cum - peak  # <= 0
        if drawdown < max_dd:
            max_dd = drawdown
        equity.append({"t": r["closed_at"], "cum": round(cum, 4)})

    losses = total - wins
    per_strategy = [
        {
            "name": name,
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            # Measured-PnL sum for this strategy — pnl over MEASURED+ESTIMATED
            # rows only. The R4 promotion gate reads THIS, not totalPnl: a leg is
            # judged on measured money, never manufactured. Pair with pnlCoverage
            # below — a low-coverage strategy's measured sum is a thin sample.
            "totalPnlMeasured": round(b["pnl_measured_sum"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
            # R-normalised (cross-instrument-comparable). None when no trade in
            # the bucket had a measurable risk; rTradeCount says how many did.
            "totalR": round(b["r"], 4) if b["rc"] else None,
            "expectancyR": round(b["r"] / b["rc"], 4) if b["rc"] else None,
            "rTradeCount": int(b["rc"]),
            # ⚠️ READ THIS BEFORE READING `totalR` / `expectancyR` ABOVE.
            # `rTradeCount` says how many rows had a measurable risk;
            # `rProvenance` says whether that risk was the trade's INITIAL one.
            # A leg whose `contaminated` count is a large share of
            # `rTradeCount` has an R that is computed from a stop the trade
            # never risked, and its expectancyR can sit orders of magnitude
            # above the truth. The four counts sum to `trades` BY
            # CONSTRUCTION, so the partition is checkable rather than trusted.
            # ⚠️ `unverified` IS NOT "CLEAN" — it is *we could not look*, and
            # it is the largest bucket (1051 of 1346 on the live journal,
            # 2026-09-02). A consumer rendering it as verified has
            # reintroduced the bug.
            "rProvenance": {
                "contaminated": int(b["r_prov"][R_CONTAMINATED]),
                "confirmedInitial": int(b["r_prov"][R_CONFIRMED_INITIAL]),
                "unverified": int(b["r_prov"][R_UNVERIFIED]),
                "noBasis": int(b["r_prov"][R_NO_BASIS]),
            },
            # WHICH risk this leg's R was divided by. `rTradeCount` is
            # `declaredInitial + storedStop` by construction; `refusedWrongSide`
            # + `noBasis` are the rows that count in NEITHER the R numerator nor
            # its denominator, so the four sum to `trades` and the partition is
            # checkable with arithmetic. A leg whose R rides mostly on
            # `storedStop` has an R that a trailing amend can still move.
            "rBasis": {
                "declaredInitial": int(b["r_basis"][R_BASIS_DECLARED]),
                "storedStop": int(b["r_basis"][R_BASIS_STORED_STOP]),
                "refusedWrongSide": int(b["r_basis"][R_BASIS_REFUSED_WRONG_SIDE]),
                "noBasis": int(b["r_basis"][R_BASIS_NO_BASIS]),
            },
            # Per-strategy PnL provenance. This is the field that must be read
            # BEFORE tuning a strategy: a bucket at pnlCoverage 0.0 is being
            # judged entirely on manufactured money.
            #
            # ⚠️ THE COUNT AND THE SUM ARE OVER DIFFERENT POPULATIONS, ON PURPOSE.
            # `pnlCoverage`/`pnlMeasuredCount` are MEASURED-only (the canonical
            # `provenance.coverage` population — ESTIMATED is deliberately NOT
            # "covered"); `totalPnlMeasured` above sums MEASURED **and** ESTIMATED.
            # The R4 gate depends on exactly that asymmetry — a MEASURED-only
            # floor decides whether the wider measured sum is trustworthy at all
            # (`research_results_gate.leg_verdict`) — so neither definition is a
            # bug and neither may be "harmonised" to match the other.
            #
            # `pnlEstimatedCount` is published so the pair is RECONCILABLE. It was
            # missing until 2026-08-11, and its absence made a correct row read as
            # a broken one: `trend_donchian_avax_4h` returned `pnlCoverage: 0.0`
            # beside `totalPnlMeasured: -5415.17` (both rows ESTIMATED, so the sum
            # equalled `totalPnl` exactly) and the only available inference was
            # "the measured sum is falling back to the raw sum" — which is false.
            # A second row, `pairs_bnb_btc_a`, showed 0.0 coverage with a measured
            # sum that did NOT equal totalPnl (-2.961 vs -211.08), which is what
            # ruled that inference out. Two of 51 strategy rows on the live book.
            "pnlMeasuredCount": int(b["pnl_measured"]),
            "pnlEstimatedCount": int(b["pnl_estimated"]),
            "pnlCoverage": (
                round(b["pnl_measured"] / b["trades"], 4) if b["trades"] else None
            ),
        }
        for name, b in per.items()
    ]
    per_strategy.sort(key=lambda s: s["totalPnl"], reverse=True)

    # Per-EXIT-PATH coverage. Same field vocabulary as `perStrategy` on purpose —
    # a second vocabulary for the same quantity is how two surfaces drift into
    # disagreeing about one number.
    #
    # ⚠️ READ `trades` BESIDE `pnlCoverage`, ALWAYS. `pnlCoverage: 0.0` over 115
    # trades and `0.0` over 2 are not the same claim, and this breakdown exists
    # precisely because the global figure hid that distinction.
    #
    # ⚠️ THE POPULATION IS `/performance`'s, NOT the trades table's. `_query`
    # excludes `pnl IS NULL`, so `reconciler_incomplete` — 93 rows, 0.0% broker
    # truth in the 2026-08-25 table-wide cut — does NOT appear here at all. That
    # is not a contradiction between the two readings; it is two populations, and
    # anyone reconciling them needs to know which they are holding.
    per_exit_path = [
        {
            "exitPath": path,
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "totalPnlMeasured": round(b["pnl_measured_sum"], 4),
            # MEASURED-only, like every other pnlCoverage in this file — ESTIMATED
            # is deliberately NOT "covered", and `totalPnlMeasured` above sums
            # MEASURED+ESTIMATED. The asymmetry is load-bearing (see the long note
            # on the per-strategy dict); neither may be harmonised to the other.
            "pnlMeasuredCount": int(b["pnl_measured"]),
            "pnlEstimatedCount": int(b["pnl_estimated"]),
            "pnlCoverage": (
                round(b["pnl_measured"] / b["trades"], 4) if b["trades"] else None
            ),
            # ── Is this bucket's own KEY evidence? (GATE 0 / G1) ────────────
            # Counts, never a ratio — see the long note at the tally site. The
            # four sum to `trades` by construction, so a reader can check the
            # partition rather than take it on faith.
            #
            # ⚠️ `labelUnattestedCount` is NOT a defect on an AUTHORED path.
            # `sl_cross`, `tp_cross`, `pairs_*`, `netting_attributed` and the
            # rest are written by the producer that closed the trade and never
            # reach `_classify_broker_exit`, so they are expected to be ~100%
            # unattested and that is correct. It IS the defect population on
            # the reconciler-derived buckets — `reconciler_filled` and the
            # empty label — which is where a fired bracket gets filed as
            # cleanup machinery, dragging this breakdown's premise with it.
            "labelAttestedCount": int(b["label_attested"]),
            "labelRefusedCount": int(b["label_refused"]),
            "labelUnresolvedCount": int(b["label_unresolved"]),
            "labelUnattestedCount": int(b["label_unattested"]),
        }
        for path, b in per_exit.items()
    ]
    # Worst coverage first: the point of the breakdown is to surface the paths
    # nobody has measured, so sorting by PnL would bury them.
    per_exit_path.sort(key=lambda e: (e["pnlCoverage"] if e["pnlCoverage"] is not None
                                      else 1.0, -e["trades"]))

    per_asset_class = [
        {
            "assetClass": cls,
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
            "totalR": round(b["r"], 4) if b["rc"] else None,
            "expectancyR": round(b["r"] / b["rc"], 4) if b["rc"] else None,
            "rTradeCount": int(b["rc"]),
        }
        for cls, b in per_class.items()
    ]
    # stable, business-readable ordering (crypto, index, commodity, equity, fx…)
    per_asset_class.sort(key=lambda c: (CLASS_ORDER.index(c["assetClass"])
                                        if c["assetClass"] in CLASS_ORDER else 99))

    # Per-symbol breakdown — each symbol tagged with its asset class so the
    # consumer can subdivide an asset-class bar by its constituent symbols.
    per_symbol_list = [
        {
            "symbol": sym,
            "assetClass": b["assetClass"],
            "trades": int(b["trades"]),
            "wins": int(b["wins"]),
            "winRate": round(b["wins"] / b["trades"] * 100.0, 1) if b["trades"] else 0.0,
            "totalPnl": round(b["pnl"], 4),
            "expectancy": round(b["pnl"] / b["trades"], 4) if b["trades"] else 0.0,
        }
        for sym, b in per_symbol.items()
    ]
    # biggest movers first (by |P&L|) — stable palette in the consumer.
    per_symbol_list.sort(key=lambda s: abs(s["totalPnl"]), reverse=True)

    # Profit factor: gross profit / gross loss. None when there are no losing
    # trades (undefined / infinite) or no trades — never a fabricated 0.
    profit_factor: Optional[float] = (
        round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    )
    # Max drawdown is <= 0; None when there were no trades.
    max_drawdown: Optional[float] = round(max_dd, 4) if total else None

    # --- journal trust (2026-08-26) ------------------------------------
    # `totalPnl` / `totalPnlMeasured` above sum this window's journal rows.
    # `pnlCoverage` says how much of that is a MEASUREMENT; this says whether
    # the ACCOUNTS those rows belong to reconcile with the venue's wallet at
    # all. They are different questions: a row can be `measured` on an account
    # whose journal sum is ~8x smaller than broker truth, which is exactly what
    # `bybit_2` is (wallet -$262.52). A session read this window, quoted the
    # journal sum, and reported the account flat; the operator had to correct
    # it from the venue UI
    # (BL-20260826-JOURNAL-READS-DO-NOT-CONSULT-THE-BROKER-TRUTH-LEDGER).
    #
    # Scoped to the accounts ACTUALLY IN THIS WINDOW, so a paper-only window
    # does not carry a real-money account's caveat.
    #
    # ⚠️ `accountsUnrecorded` IS NOT `accountsTrusted`, and is deliberately not
    # named that. The ledger is populated BY HAND from an operator's venue
    # export, so an unrecorded account means nobody has reconciled it — never
    # that it reconciles. `readState: "unreadable"` means we could not look,
    # which is a third thing again.
    _trust_map = journal_trust_map()
    _by_state: Dict[str, List[str]] = {}
    for _aid in sorted({str(_rget(r, "account_id") or "") for r in rows} - {""}):
        _by_state.setdefault(
            journal_trust_for(_aid, _trust_map)["state"], []).append(_aid)

    return {
        "window": window,
        "since": since,
        "error": False,
        "journalTrust": {
            "readState": _trust_map.get("read_state"),
            "accountsKnownDivergent": _by_state.get("known_divergent", []),
            "accountsUnrecorded": _by_state.get("no_record", []),
            "accountsUnreadable": _by_state.get("unreadable", []),
        },
        "totalTrades": total,
        "wins": wins,
        "losses": losses,
        "winRate": round(wins / total * 100.0, 1) if total else 0.0,
        "totalPnl": round(total_pnl, 4),
        "expectancy": round(total_pnl / total, 4) if total else 0.0,
        # R-normalised headline — the cross-instrument-comparable axis. None when
        # NO trade in the window had a measurable risk; rTradeCount / rCoverage
        # report how much of the window is R-measured (transparency, never a
        # raw-pnl fallback). Resolves the cross-notional USD blending in totalPnl.
        "totalR": round(total_r, 4) if r_count else None,
        "expectancyR": round(total_r / r_count, 4) if r_count else None,
        "rTradeCount": r_count,
        "rCoverage": round(r_count / total, 4) if total else 0.0,
        # --- R-DENOMINATOR provenance (2026-09-02) ------------------------
        # `rCoverage` above answers "how much of this window is R-MEASURABLE".
        # It has never answered "and was that risk the trade's INITIAL risk",
        # which is a different question with a worse answer.
        #
        # `trades.stop_loss` holds the CURRENT stop, not the initial one:
        # `order_monitor._apply_update` mirrors every confirmed trailing amend
        # onto the row (correctly — /api/bot/positions must show where the stop
        # IS). R is defined against entry-time risk, so a trailed stop shrinks
        # the denominator and inflates R without bound.
        #
        # MEASURED, live journal copy `/home/ubuntu/ict-trading-bot/data/
        # trade_journal.db` on the trainer VM, mtime 2026-09-02T04:28:35Z,
        # max(created_at) 2026-09-02T04:11:21Z, trader serving sha 2c7ae605.
        # Population: closed, pnl NOT NULL, non-backtest, n=1346 (WIDER than
        # this route's, which also drops reconciler/superseded/reset-flat) —
        # contaminated 118 · confirmedInitial 156 · unverified 1051 · noBasis
        # 21. Worst legs: mgc_trend_1h paper 18 of 19; ict_scalp_sol_5m paper
        # 13 of 29. Structurally clean: vwap (318 real-money rows) and the
        # whole pairs sleeve (283 rows) carry ZERO — neither trails a stop.
        #
        # ⚠️ NOTHING IS EXCLUDED FROM ANY AGGREGATE ABOVE. `totalR` and
        # `expectancyR` are unchanged. Publishing the count and silently
        # dropping the rows are opposite moves, and dropping them would convert
        # a visible-wrong number into an invisible-wrong one over an unstated
        # population. Publish the count; let the consumer decide.
        #
        # ⚠️ `unverified` IS NOT "CLEAN" — it is *we could not look*, and it is
        # the LARGEST bucket (78.1% live). A stop trailed to just SHORT of
        # entry is side-plausible and just as wrong; it is simply not provable
        # from the stored row.
        # --- WHICH RISK EACH PUBLISHED R WAS DIVIDED BY (2026-09-06, MI-144) ---
        # `rProvenance` below GRADES the stored stop. This says which basis the
        # number above was actually computed FROM, so `totalR` / `expectancyR`
        # are never published over an unstated population.
        #
        # `rTradeCount` == `declaredInitial + storedStop` BY CONSTRUCTION;
        # `refusedWrongSide` + `noBasis` are excluded from both the R numerator
        # and its denominator. The four sum to `totalTrades`, so the partition
        # is checkable with arithmetic rather than trusted.
        #
        # ⚠️ `refusedWrongSide` IS NOT A DATA-QUALITY FOOTNOTE — it is the
        # count of rows whose R was, until 2026-09-06, a finite number produced
        # by `abs()`-ing an impossible risk distance. A non-zero value here on a
        # window whose `expectancyR` you are about to act on means the OLD
        # number for that window was wrong, not merely noisy.
        #
        # ⚠️ A HIGH `storedStop` SHARE IS NOT CLEAN. It means no signal-time
        # `risk_per_unit` record exists for those rows, so their denominator is
        # still the CURRENT stop and a trailing amend can still move it — it is
        # simply not PROVABLY wrong. Read it beside `rProvenance.unverified`.
        "rBasis": {
            "declaredInitial": int(r_basis_counts[R_BASIS_DECLARED]),
            "storedStop": int(r_basis_counts[R_BASIS_STORED_STOP]),
            "refusedWrongSide": int(r_basis_counts[R_BASIS_REFUSED_WRONG_SIDE]),
            "noBasis": int(r_basis_counts[R_BASIS_NO_BASIS]),
        },
        "rProvenance": {
            "contaminated": int(r_prov[R_CONTAMINATED]),
            "confirmedInitial": int(r_prov[R_CONFIRMED_INITIAL]),
            "unverified": int(r_prov[R_UNVERIFIED]),
            "noBasis": int(r_prov[R_NO_BASIS]),
            # The second axis, REPORTED beside the states and deliberately NOT
            # folded into them: rows whose stored stop is at least
            # `ratioBar` times TIGHTER than the initial risk their own signal
            # declared. These are the ones the wrong-side proof CANNOT see.
            # `declaredRiskRecords` is its denominator — a bar-crossing count
            # over an unstated denominator is not a claim.
            "tightenedVsDeclared": int(r_tightened),
            "declaredRiskRecords": int(r_declared_records),
            "ratioBar": DISAGREEMENT_RATIO_BAR,
        },
        # --- PnL provenance (the base metric's honest denominator) ---------
        # `totalPnl` above sums whatever the journal recorded. These say how
        # much of that sum is a MEASUREMENT. A window at pnlCoverage 0.0 with a
        # large totalPnl is not a profitable window — it is an unmeasured one.
        #
        # Added 2026-07-30 after the audit that found `rCoverage`'s discipline
        # had been applied to the derived R-metric and NOT to the `pnl` it is
        # derived from: 206 of 829 closed rows carrying -$36,018.60 of mark-price PnL,
        # with the fabricated share running 0.0% (May) -> 30.5% (Jun) -> 64.9%
        # (Jul) while every consumer treated measured and manufactured alike.
        "pnlCoverage": coverage({**pnl_prov, "total": total}),
        # Measured-PnL SUM (MEASURED+ESTIMATED rows only) — the honest headline
        # the R4 research→results promotion gate reads INSTEAD of totalPnl. Never
        # gate on totalPnl: it sums fabricated marks too. Read this beside
        # pnlCoverage — below the coverage floor the measured sum is too thin a
        # sample to gate on and the gate ABSTAINS (R4 design §3). Added 2026-08-01.
        "totalPnlMeasured": round(total_pnl_measured, 4),
        "pnlMeasuredCount": int(pnl_prov.get(MEASURED, 0)),
        "pnlEstimatedCount": int(pnl_prov.get(ESTIMATED, 0)),
        "pnlFabricatedCount": int(pnl_prov.get(FABRICATED, 0)),
        "pnlUnverifiedCount": int(pnl_prov.get(UNVERIFIED, 0)),
        "profitFactor": profit_factor,
        "maxDrawdown": max_drawdown,
        "perStrategy": per_strategy,
        "perExitPath": per_exit_path,
        "perAssetClass": per_asset_class,
        "perSymbol": per_symbol_list,
        "equity": _downsample(equity, _MAX_EQUITY_POINTS),
    }


def _strip_envelope(agg: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the ``window`` / ``since`` / ``error`` envelope keys from an
    aggregate so the demo/paper sub-block doesn't carry duplicate metadata
    (``error`` is an envelope-level signal, not per-sub-block)."""
    return {k: v for k, v in agg.items() if k not in ("window", "since", "error")}


def _portfolio_paper_account_ids() -> List[str]:
    """Account-ids of PAPER accounts flagged ``paper_role: portfolio``.

    S-PAPER-PORTFOLIO (2026-07-16): the live-portfolio-mirror paper books
    (``bybit_portfolio`` / ``alpaca_portfolio``). The ``paperPortfolio``
    sub-block below is computed over just these so a consumer's "Paper" view
    can scope to the real-portfolio mirror instead of the full soak roster.
    Empty list → no portfolio accounts declared (an older config); the caller
    then falls the ``paperPortfolio`` block back to the all-paper ``paper``
    block so the field is always present and never misleadingly empty.

    Best-effort + connection-free: any load error → ``[]`` (fall back).
    """
    try:
        from src.config.accounts_loader import load_accounts_dict
        accounts_yaml = Path(__file__).resolve().parents[4] / "config" / "accounts.yaml"
        accounts = load_accounts_dict(accounts_yaml)
    except Exception:  # noqa: BLE001  # allow-silent: best-effort config read — a missing/garbled accounts.yaml yields no portfolio scoping (get_performance falls back to the all-paper `paper` block), never a 5xx on this Tier-1 read
        return []
    out: List[str] = []
    for aid, cfg in (accounts or {}).items():
        if not isinstance(cfg, dict):
            continue
        if (
            str(cfg.get("account_class") or "").lower() == "paper"
            and str(cfg.get("paper_role") or "").lower() == "portfolio"
        ):
            out.append(str(aid))
    return out


@router.get("/performance")
def get_performance(
    window: str = Query("all", max_length=8),
) -> Dict[str, Any]:
    """Aggregate trade performance for the requested *window*.

    The top-level fields (``totalTrades`` / ``wins`` / ``perStrategy`` / etc.)
    are **real-money** aggregates — this preserves the existing consumer
    contract. The 2026-06-04 reporting-cleanup additively returns a
    ``demo`` sub-block carrying the same shape computed over paper-account
    rows so a consumer can render Real and Paper as separate sections
    without a second request. A ``paper`` sub-block carries the identical
    payload under the clearer name (account_class convention, 2026-06-15);
    ``demo`` is retained as a back-compat alias from the retired Android app.

    Trades with ``pnl IS NULL`` are excluded from both — see ``_query`` for
    why ("0-pnl closed trade" complaint, reconciler fallback path).

    Returns a zeroed envelope (HTTP 200) on an unknown window token or a
    DB read error so the consumer's tab stays usable instead of erroring.
    """
    window = window if window in _WINDOWS else "all"
    since = _window_since(window)
    if not _DB_PATH.exists():
        env = _empty(window, since)
        empty_sub = _strip_envelope(_empty(window, since))
        env["demo"] = empty_sub
        env["paper"] = empty_sub
        env["paperPortfolio"] = empty_sub
        return env
    try:
        live_rows = _query(_DB_PATH, since, demo=False)
        live = _aggregate(live_rows, window, since)
        paper_rows = _query(_DB_PATH, since, demo=True)
        paper = _strip_envelope(_aggregate(paper_rows, window, since))
        live["demo"] = paper   # back-compat alias
        live["paper"] = paper
        # paperPortfolio (S-PAPER-PORTFOLIO 2026-07-16): the same shape computed
        # over ONLY the live-portfolio-mirror paper accounts (paper_role:
        # portfolio), so a consumer's "Paper" view can scope to the real
        # portfolio instead of the full soak roster. Falls back to the all-paper
        # block when no portfolio accounts are declared, so the field is always
        # present (never a misleadingly-empty block on an older config).
        portfolio_ids = _portfolio_paper_account_ids()
        if portfolio_ids:
            pp_rows = _query(_DB_PATH, since, demo=True, account_ids=portfolio_ids)
            live["paperPortfolio"] = _strip_envelope(_aggregate(pp_rows, window, since))
        else:
            live["paperPortfolio"] = paper
        return live
    except sqlite3.Error:  # allow-silent: logged (logger.exception) + best-effort zeroed envelope so the Performance tab stays usable on a DB read failure
        logger.exception("performance: sqlite read failed")
        env = _empty(window, since, error=True)
        empty_sub = _strip_envelope(_empty(window, since))
        env["demo"] = empty_sub
        env["paper"] = empty_sub
        env["paperPortfolio"] = empty_sub
        return env
    except Exception:  # noqa: BLE001  # allow-silent: logged (logger.exception) + best-effort zeroed envelope; never raise a 5xx for this Tier-1 read
        logger.exception("performance: unexpected error")
        env = _empty(window, since, error=True)
        empty_sub = _strip_envelope(_empty(window, since))
        env["demo"] = empty_sub
        env["paper"] = empty_sub
        env["paperPortfolio"] = empty_sub
        return env
