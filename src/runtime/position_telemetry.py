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

# The live TP clamp. Mirrored from
# src/units/strategies/{trend_donchian,htf_pullback_trend_2h}.py; a test pins
# the agreement so it cannot drift from the value production clamps with.
_TP_SENTINEL_CAP_PCT = 0.099


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
        stop_f, target_f = _f(stop), _f(target)
        r_to_stop = r_to_target = rr_from_here = None
        if risk and risk > 0 and px is not None:
            if stop_f is not None:
                r_to_stop = ((px - stop_f) if is_long else (stop_f - px)) / risk
            if target_f is not None:
                r_to_target = ((target_f - px) if is_long else (px - target_f)) / risk
            # Only meaningful while BOTH sit the correct side of price; a
            # negative leg means the level is already crossed and the ratio
            # would be a sign artefact, not a decision input.
            if (r_to_stop is not None and r_to_target is not None
                    and r_to_stop > 0 and r_to_target >= 0):
                rr_from_here = r_to_target / r_to_stop

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
