"""M31 P2 — the position-telemetry record: the missing state between entry and exit.

WHY THIS EXISTS
---------------
The system holds ENTRY facts (``order_packages``), EXIT facts (``trades``,
written at close), and per-pass transient recomputation — with **nothing in
between**. `trail_decay.since_entry_peak` computes MFE-in-R on every exit-loop
pass for every open position on the donchian/pullback family, and the caller
collapses it to one boolean and discards the rest; the undeclared path writes
one row to a soak whose own docstring says *"Nothing reads it back."*

So a live trade's trajectory is computed roughly every 30 seconds and thrown
away. Answering *"should we hold this 18-day XRP short?"* therefore took four
diag pulls and a page of hand arithmetic, and MFE — the one quantity every exit
lever is tuned on — was not reconstructible at all.

Design: ``docs/design/position-telemetry-DESIGN.md``. Roadmap: M31.

WHAT THIS IS NOT
----------------
**Observe-only.** Nothing reads this back to change an exit; a lever that does
is P5 and Tier-3. This module cannot refuse a trade, cannot move a stop, and
never raises into the monitor.

FOUR CONSTRAINTS, each from this repo's own scar tissue
-------------------------------------------------------
1. **Derive, do not re-derive.** The peak comes from
   ``trail_decay.since_entry_peak`` — the SAME function the lever arms on. A
   second definition is how `_regime_score_semantics.py` had to be written.
2. **Bounded and measured.** One small upsert per open trade per pass; the peak
   computation already happens. This runs on the live exit loop — the June 2026
   wedge class, where every component was individually cheap and the SUM was
   never watched — so the write is wrapped in ``tick_cost.hook`` and lands in
   ``offloop_hooks`` from the first commit.
3. **Never collapse a state.** ``peak_state`` carries the reason MFE is absent
   (``unanchored`` / ``thin_window`` / ``no_risk``), never a fabricated 0.0.
   Registered with ``collapsed-state-guard`` as ``position_telemetry.peak_state``.
4. **Stamp provenance.** ``peak_r`` from BAR EXTREMES is **ESTIMATED**, not
   MEASURED — it cannot see an intrabar excursion. ``src/runtime/provenance.py``
   owns the vocabulary.

THE FIELD THAT ANSWERS THE OPERATOR'S QUESTION
-----------------------------------------------
``rr_from_here = r_to_target / r_to_stop`` — upside left against give-back at
risk. On the motivating trade that is 1.04 / 1.46 = 0.71, i.e. holding for the
target risks more than it stands to make. Nothing computed it before.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TABLE = "position_telemetry"

# Mirrors trail_decay's peak states so a reader never has to map between two
# vocabularies; re-exported here because this is the table's contract.
from src.runtime.trail_decay import (  # noqa: E402
    PEAK_MEASURED, PEAK_NO_RISK, PEAK_THIN_WINDOW, PEAK_UNANCHORED,
    since_entry_peak)

PEAK_STATES = (PEAK_MEASURED, PEAK_UNANCHORED, PEAK_THIN_WINDOW, PEAK_NO_RISK)

# The venue TP clamp -- ONE owner, imported rather than mirrored.
from src.runtime.tp_venue_cap import (  # noqa: E402
    TP_VENUE_CAP_PCT as _TP_SENTINEL_CAP_PCT)


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _r(value: Optional[float], nd: int = 4) -> Optional[float]:
    return None if value is None else round(value, nd)


def cap_r(entry: Optional[float], risk: Optional[float],
          cap_pct: float = _TP_SENTINEL_CAP_PCT) -> Optional[float]:
    """The highest MFE in R this trade can print before its capped TP fills.

    ``None`` when undeterminable — never 0.0, which would read as "this trade
    can make nothing" rather than "we could not compute the ceiling".
    """
    e, rk = _f(entry), _f(risk)
    if e is None or rk is None or rk <= 0 or e <= 0:
        return None
    return cap_pct * e / rk


def r_distances(*, price: Optional[float], stop: Optional[float],
                target: Optional[float], risk: Optional[float],
                is_long: bool) -> tuple:
    """The ONE definition of ``(r_to_stop, r_to_target, rr_from_here)``.

    Extracted from ``build_record`` (which now calls it) so that the BACKTEST
    HARNESS can compute the identical quantity instead of re-deriving it.
    ``scripts/backtest_trend.py`` imports this for its ``rr_floor`` lever — the
    M31 P5 candidate — and the sibling of ``src/research/trail_levers.py`` ("the
    ONE trail-lever rule") and ``src/runtime/execution_costs.py`` ("the ONE
    shared cost model"), for the same reason those exist.

    A second derivation would be the exact defect M31 was created to close —
    *the harness measured a book production does not run* — and it would be
    invisible, because both copies would look correct in isolation.

    ``rr_from_here`` is ``None`` unless BOTH legs sit the correct side of price
    (``r_to_stop > 0``, ``r_to_target >= 0``). A negative leg means the level is
    already crossed, so the ratio would be a sign artefact rather than a
    decision input. ``None`` here is *"not meaningful"*, never 0.0.

    ⚠️ **Unbounded above as ``r_to_stop`` → 0.** Live, the fleet's only closed
    telemetry row sat 0.0337R from its stop and reported **201.87**, 19.6× the
    next value across the same 14 rows. Grade a lever on the DECISION it makes;
    do not fit a floor over the raw ratio's mean/variance/unwinsorised quantile,
    which near-stop rows dominate
    (``docs/design/m31-p5-telemetry-reading-lever-PROPOSAL.md`` § 3.1).
    """
    r_to_stop = r_to_target = rr = None
    px, stop_f, target_f = _f(price), _f(stop), _f(target)
    if risk and risk > 0 and px is not None:
        if stop_f is not None:
            r_to_stop = ((px - stop_f) if is_long else (stop_f - px)) / risk
        if target_f is not None:
            r_to_target = ((target_f - px) if is_long else (px - target_f)) / risk
        if (r_to_stop is not None and r_to_target is not None
                and r_to_stop > 0 and r_to_target >= 0):
            rr = r_to_target / r_to_stop
    return r_to_stop, r_to_target, rr


def build_record(
    *,
    open_pkg: Dict[str, Any],
    meta: Dict[str, Any],
    window,
    direction: str,
    current_price: Optional[float],
    stop: Optional[float] = None,
    target: Optional[float] = None,
    strategy: Optional[str] = None,
    account_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """The telemetry row for one open position. Pure; never raises.

    Returns ``None`` only when there is no identity to key on — a row we could
    not attribute is worse than no row.
    """
    try:
        pkg_id = open_pkg.get("order_package_id") or open_pkg.get("orderPackageId")
        if not pkg_id:
            return None

        entry = _f(open_pkg.get("entry"))
        risk = _f(meta.get("risk_per_unit"))
        anchored = bool(meta.get("entry_time"))

        peak = since_entry_peak(window, entry, risk, direction, anchored=anchored)
        peak_r = _f(peak.get("peak_r"))

        px = _f(current_price)
        if px is None:
            try:
                px = _f(window["close"].astype(float).to_numpy()[-1])
            except (KeyError, ValueError, TypeError, AttributeError, IndexError):
                px = None

        is_long = direction == "long"
        open_r = None
        if entry is not None and risk and risk > 0 and px is not None:
            open_r = (px - entry) / risk if is_long else (entry - px) / risk

        ceiling = cap_r(entry, risk)
        pct_of_cap = None
        if ceiling and ceiling > 0 and open_r is not None:
            pct_of_cap = 100.0 * open_r / ceiling

        # Distances in R from HERE — the quantities a hold/close decision needs.
        # Delegated to `r_distances` so the backtest harness computes the
        # IDENTICAL quantity rather than a second derivation of it; see that
        # function's docstring for why that matters here specifically.
        r_to_stop, r_to_target, rr_from_here = r_distances(
            price=px, stop=stop, target=target, risk=risk, is_long=is_long)

        giveback_r = None
        if peak_r is not None and open_r is not None:
            giveback_r = peak_r - open_r

        ts = (now or datetime.now(timezone.utc)).isoformat()
        return {
            "order_package_id": str(pkg_id),
            "trade_id": (str(open_pkg.get("linked_trade_id"))
                         if open_pkg.get("linked_trade_id") is not None else None),
            "strategy": strategy or meta.get("strategy_label")
            or open_pkg.get("strategy_name"),
            "symbol": open_pkg.get("symbol"),
            "account_id": account_id or open_pkg.get("account_id"),
            "direction": direction,
            "entry": _r(entry, 8),
            "risk_per_unit": _r(risk, 8),
            "last_price": _r(px, 8),
            "open_r": _r(open_r),
            "peak_r": _r(peak_r),
            "peak_state": peak.get("peak_state"),
            "giveback_r": _r(giveback_r),
            "bars_held": int(peak.get("bars") or 0),
            "bars_since_peak": peak.get("bars_since_peak"),
            "cap_r": _r(ceiling),
            "pct_of_cap": _r(pct_of_cap, 2),
            "r_to_stop": _r(r_to_stop),
            "r_to_target": _r(r_to_target),
            "rr_from_here": _r(rr_from_here),
            # ESTIMATED, always: a bar-extreme peak cannot see an intrabar
            # excursion, so this is a defensible reconstruction and never a
            # broker-confirmed measurement.
            "peak_provenance": "estimated",
            "levers": json.dumps(_lever_view(meta), sort_keys=True),
            "updated_at": ts,
        }
    except Exception:  # noqa: BLE001 — telemetry must never break the monitor
        logger.debug("position_telemetry: record build failed", exc_info=True)
        return None


def _lever_view(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Which R-threshold levers this package declares, and their arms.

    Whether each is REACHABLE is `config/lever_reachability.json` (M31 P1) —
    deliberately not recomputed here.
    """
    out: Dict[str, Any] = {}
    for key in ("trail_decay_arm_r", "giveback_min_mfe_r"):
        v = _f(meta.get(key))
        if v is not None and v > 0:
            out[key] = v
    return out


_UPSERT = f"""
INSERT INTO {TABLE} (
    order_package_id, trade_id, strategy, symbol, account_id, direction,
    entry, risk_per_unit, last_price, open_r, peak_r, peak_state, giveback_r,
    bars_held, bars_since_peak, cap_r, pct_of_cap, r_to_stop, r_to_target,
    rr_from_here, peak_provenance, levers, updated_at
) VALUES (
    :order_package_id, :trade_id, :strategy, :symbol, :account_id, :direction,
    :entry, :risk_per_unit, :last_price, :open_r, :peak_r, :peak_state,
    :giveback_r, :bars_held, :bars_since_peak, :cap_r, :pct_of_cap, :r_to_stop,
    :r_to_target, :rr_from_here, :peak_provenance, :levers, :updated_at
)
ON CONFLICT(order_package_id) DO UPDATE SET
    trade_id=excluded.trade_id, last_price=excluded.last_price,
    -- Backfill on UPDATE too: every row written before the resolver existed
    -- carries NULL, and the upsert is the only path a live open position takes
    -- after its first pass. COALESCE order is load-bearing — a later pass whose
    -- lookup misses must never wipe an account already established.
    account_id=COALESCE(excluded.account_id, {TABLE}.account_id),
    open_r=excluded.open_r, peak_r=MAX(COALESCE({TABLE}.peak_r, -1e18),
                                       COALESCE(excluded.peak_r, -1e18)),
    peak_state=excluded.peak_state, giveback_r=excluded.giveback_r,
    bars_held=excluded.bars_held, bars_since_peak=excluded.bars_since_peak,
    cap_r=excluded.cap_r, pct_of_cap=excluded.pct_of_cap,
    r_to_stop=excluded.r_to_stop, r_to_target=excluded.r_to_target,
    rr_from_here=excluded.rr_from_here, levers=excluded.levers,
    updated_at=excluded.updated_at
"""


def _resolve_account_id(conn: sqlite3.Connection,
                        record: Dict[str, Any]) -> Optional[str]:
    """The account for this row, looked up from ``trades`` via ``trade_id``.

    **Why a lookup rather than a caller argument.** Measured on the live VM
    2026-08-16, the FIRST post-deploy read of this table: all 12 rows carried
    ``account_id: null``, and no row could ever have carried anything else —
    ``order_packages`` has no ``account_id`` column, and the monitor signature
    ``monitor(cfg, candles_df, open_pkg)`` has no account in scope to pass one.
    A structurally unpopulatable column reads, to a consumer, exactly like
    *"this position has no account"*.

    **Why it is resolved HERE and not inside the upsert SQL.** The first
    attempt put a correlated subquery in the INSERT, which made the whole
    telemetry write fail when ``trades`` was absent — two existing persistence
    tests caught it immediately. This module is observe-only and must never
    break the monitor, so a best-effort enrichment may not become a hard
    dependency of the write it enriches. A failed lookup returns ``None`` and
    the row still lands.

    ``trades.id`` is INTEGER while ``trade_id`` is stored TEXT, so the int()
    coercion is load-bearing, not decoration.
    """
    existing = record.get("account_id")
    if existing:
        return str(existing)
    trade_id = record.get("trade_id")
    if trade_id in (None, ""):
        return None
    try:
        row = conn.execute(
            "SELECT account_id FROM trades WHERE id = ?", (int(trade_id),)
        ).fetchone()
    except (sqlite3.Error, TypeError, ValueError):
        return None  # allow-silent: enrichment only — the row still writes
    return row[0] if row and row[0] else None


def write_record(record: Dict[str, Any], db_path: Optional[str] = None) -> bool:
    """Best-effort upsert. Returns whether the row landed; never raises.

    ``peak_r`` is kept as a RUNNING MAXIMUM rather than overwritten: MFE is
    one-way by definition, and a pass whose window is briefly shorter (a
    re-fetch, a restart) must not be able to walk a recorded peak backwards.
    The `-1e18` sentinel makes a NULL lose to any real value without letting a
    NULL overwrite one.
    """
    if not record:
        return False
    try:
        from src.utils.paths import trade_journal_db_path
        path = db_path or str(trade_journal_db_path())
        with sqlite3.connect(path, timeout=5.0) as conn:
            record = dict(record)
            record["account_id"] = _resolve_account_id(conn, record)
            conn.execute(_UPSERT, record)
        return True
    except Exception:  # noqa: BLE001 — never raise into the monitor
        logger.debug("position_telemetry: write failed", exc_info=True)
        return False


def record_position_telemetry(**kwargs) -> Optional[Dict[str, Any]]:
    """Build + persist in one call, timed into ``offloop_hooks``.

    The instrumentation is not decoration: this runs on the live exit loop, and
    an unmeasured per-pass cost is precisely the shape of both June 2026 wedges.
    """
    db_path = kwargs.pop("db_path", None)
    try:
        from src.runtime import tick_cost
        ctx = tick_cost.hook("monitor.position_telemetry")
    except Exception:  # noqa: BLE001 — measurement must never gate the write
        from contextlib import nullcontext
        ctx = nullcontext()
    with ctx:
        rec = build_record(**kwargs)
        if rec is not None:
            write_record(rec, db_path=db_path)
        return rec


# ---------------------------------------------------------------------------
# M31 P3 — THE READ HALF.
#
# P2 shipped the writer and nothing read it back, which is the `exit_price_source`
# shape this repo already paid for once (written in 12 files, branched on in one).
# These helpers are the single owner of "what does a telemetry row MEAN", so the
# diag surface and `/api/bot/positions` cannot drift into two answers.
#
# They add the three things the TABLE CANNOT SAY:
#
#   1. `lifecycle` — whether the row is FINAL. The table is UPSERT-on-
#      `order_package_id` with no status: when a trade closes the row simply
#      stops being updated, so a closed row is byte-shaped like an open one and
#      the only in-table hint is a staler `updated_at`, which is NOT a signal
#      (a quiet leg and a closed leg both go stale). Measured 2026-08-17: 14
#      rows, 13 open + 1 closed, and the closed one was findable only by this
#      join. The durable fix is a terminal writer
#      (`PB-20260817-TELEMETRY-HAS-NO-TERMINAL-SNAPSHOT`, Tier-2); this is the
#      read-side mitigation, and it is why every consumer must ask here rather
#      than eyeball `updated_at`.
#   2. `peak_pct_of_cap` — how close the trade EVER got to its venue ceiling.
#      The stored `pct_of_cap` is computed from `open_r`, i.e. where it is NOW.
#      Both are correct for what they name; only one answers "was the ceiling
#      ever approached", which is the M31 P4 Check-A quantity.
#   3. `arm_reach` — whether this row's declared lever arm is even reachable
#      under this row's own ceiling. `arm_r > cap_r` means the lever cannot fire
#      on this trade however it goes. Tracking id on its own line, NEVER
#      wrapped — a line-broken id resolves to nothing and reads as tracked
#      while being tracked by nobody (artifact-validity-guard caught exactly
#      that on this file's first commit):
#      BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP
#
# Still observe-only: a lever that READS any of this to change an exit is P5
# and Tier-3.
# ---------------------------------------------------------------------------

#: Is this row FINAL? Never collapsed — "we could not tell" is not "still open".
LIFECYCLE_STATES = (
    "open",                   # the joined trade is open: peak_r is a PARTIAL
    "closed",                 # the joined trade is closed: peak_r is final-ish
    "unknown_no_trade_id",    # the package never filled / carries no trade id
    "unknown_trade_absent",   # a trade_id that the trades table does not have
)

#: WHICH evidence decided finality. A stamped fact and a derived inference must
#: never be reported as the same thing — a pre-migration row can only ever be
#: `derived_join`, and reading that as `stamped` would overstate the record.
FINALITY_SOURCES = (
    "stamped",        # the close path wrote terminal_state='final' — no join needed
    "derived_join",   # inferred from trades.status (pre-stamp rows, and backfill gaps)
    "not_final",      # the trade is open: there is no finality to source
    "unknown",        # neither available
)

#: Can the declared arm be reached under this row's own venue ceiling?
ARM_REACH_STATES = (
    "reachable",         # arm_r <= cap_r on this row
    "unreachable",       # arm_r  > cap_r — the lever cannot fire on this trade
    "no_arm_declared",   # the row declares no R-threshold lever
    "unmeasured",        # arm or cap missing: we could not look
)


def enrich_record(row: Dict[str, Any],
                  trade_status: Optional[str],
                  trade_seen: bool) -> Dict[str, Any]:
    """Annotate one telemetry row. Pure; never raises.

    ``trade_seen`` is passed separately from ``trade_status`` on purpose: a
    missing status and an absent trade are different facts, and folding them
    into one nullable string is the collapse this function exists to avoid.
    """
    out = dict(row)

    # THE STORED STAMP WINS. `terminal_state='final'` is written by the close
    # path (`Database._stamp_telemetry_terminal`, M31 P5 precondition 1), so a
    # stamped row is final WITHOUT the trades join — which is the whole point:
    # anything reading the table directly (Data Explorer, an ad-hoc query, a
    # future lever) previously could not tell a closed row from an open one.
    # `finality_source` says WHICH evidence decided, so a stamped fact and a
    # derived inference are never reported as the same thing.
    stamped = str(row.get("terminal_state") or "").strip().lower() == "final"
    tid = row.get("trade_id")

    if stamped:
        out["lifecycle"] = "closed"
        out["finality_source"] = "stamped"
    elif tid is None or str(tid).strip() == "":
        out["lifecycle"] = "unknown_no_trade_id"
        out["finality_source"] = "unknown"
    elif not trade_seen:
        out["lifecycle"] = "unknown_trade_absent"
        out["finality_source"] = "unknown"
    else:
        st = (trade_status or "").strip().lower()
        out["lifecycle"] = "open" if st == "open" else "closed"
        # An OPEN trade is not a finality claim at all; only a derived CLOSE is.
        out["finality_source"] = "derived_join" if st != "open" else "not_final"

    # ALWAYS true, on every row, whatever the lifecycle: the last write precedes
    # the close by up to one exit-loop pass, and a bar extreme cannot see an
    # intrabar excursion. Consumers that average or gate on peak_r must know it
    # is a floor, not the MFE.
    out["peak_r_is_lower_bound"] = True

    peak, cap = _f(row.get("peak_r")), _f(row.get("cap_r"))
    out["peak_pct_of_cap"] = (
        _r(100.0 * peak / cap, 2) if (peak is not None and cap and cap > 0) else None)

    arm = None
    try:
        levers = row.get("levers")
        if isinstance(levers, str) and levers.strip():
            levers = json.loads(levers)
        if isinstance(levers, dict):
            # The largest declared arm is the binding one: if the highest arm is
            # unreachable the lever chain is capped there regardless of any
            # lower rung.
            arms = [_f(v) for v in levers.values()]
            arms = [a for a in arms if a is not None]
            arm = max(arms) if arms else None
    except (ValueError, TypeError):
        arm = None
    out["arm_r"] = _r(arm)

    if arm is None:
        out["arm_reach"] = "no_arm_declared"
    elif cap is None or cap <= 0:
        out["arm_reach"] = "unmeasured"
    else:
        out["arm_reach"] = "reachable" if arm <= cap else "unreachable"
    return out


def read_records(db_path: Optional[str] = None,
                 limit: int = 500,
                 strategy: Optional[str] = None) -> Dict[str, Any]:
    """Telemetry rows LEFT JOINed to `trades` for finality, newest-first.

    A LEFT JOIN, not an inner one: a row whose trade is absent must still be
    RETURNED and graded ``unknown_trade_absent``, because dropping it would
    make an unattributable row look like a row that does not exist.

    Returns an envelope, never a bare list — ``present: false`` distinguishes
    "the table has not been created yet" from "the table is empty", which is the
    same distinction ``/api/diag/journal`` was fixed to stop collapsing.
    """
    envelope: Dict[str, Any] = {
        "present": False, "count": 0, "rows": [],
        "lifecycle_states": list(LIFECYCLE_STATES),
        "arm_reach_states": list(ARM_REACH_STATES),
        "finality_sources": list(FINALITY_SOURCES),
        "error": None,
    }
    try:
        from src.utils.paths import trade_journal_db_path
        path = db_path or str(trade_journal_db_path())
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            sql = (f"SELECT t.*, tr.status AS _trade_status, "
                   f"tr.id AS _trade_seen FROM {TABLE} t "
                   f"LEFT JOIN trades tr ON tr.id = CAST(t.trade_id AS INTEGER) ")
            params: list[Any] = []
            if strategy:
                sql += "WHERE t.strategy = ? "
                params.append(strategy)
            sql += "ORDER BY t.updated_at DESC LIMIT ?"
            params.append(max(1, min(int(limit), 1000)))
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        envelope["error"] = str(exc)
        return envelope

    out = []
    for r in rows:
        d = dict(r)
        status_val = d.pop("_trade_status", None)
        seen = d.pop("_trade_seen", None) is not None
        out.append(enrich_record(d, status_val, seen))

    counts: Dict[str, int] = {}
    reach: Dict[str, int] = {}
    finality: Dict[str, int] = {}
    for d in out:
        counts[d["lifecycle"]] = counts.get(d["lifecycle"], 0) + 1
        reach[d["arm_reach"]] = reach.get(d["arm_reach"], 0) + 1
        finality[d["finality_source"]] = finality.get(d["finality_source"], 0) + 1
    envelope.update({
        "present": True, "count": len(out), "rows": out,
        "summary": {
            "by_lifecycle": counts,
            "by_arm_reach": reach,
            # Read this beside `final_rows`: a closed count that is entirely
            # `derived_join` means the stamp is not reaching the close path, and
            # `final_rows` is then only as good as the join.
            "by_finality_source": finality,
            # The Check-A invariant, computed here so every consumer reads the
            # same number: a row whose peak EXCEEDED its own venue ceiling.
            "peak_above_cap": sum(
                1 for d in out
                if (d.get("peak_pct_of_cap") or 0) > 100.0),
            "final_rows": counts.get("closed", 0),
        },
    })
    return envelope


def telemetry_by_trade_id(db_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """`trade_id -> enriched row`, for consumers that already hold a trade id.

    Best-effort: any failure returns ``{}`` so a telemetry outage degrades a
    read route to "no R block" rather than breaking it.
    """
    try:
        env = read_records(db_path=db_path, limit=1000)
        return {str(d["trade_id"]): d for d in env.get("rows", [])
                if d.get("trade_id") is not None}
    except Exception:  # noqa: BLE001 — a reader must never break its caller
        logger.debug("position_telemetry: by-trade-id read failed", exc_info=True)
        return {}
