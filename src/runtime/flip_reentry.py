"""Conditional trend RE-ENTRY for ``FLIP_POLICY=selective`` (Unit A § 7.2).

Design source of truth: ``docs/research/pnl-optimal-conflict-resolution-DESIGN.md``
§ 7.2 — after a selective flip closes the held trend **H** to take a stronger
counter-signal scalp **N**, this module conditionally re-establishes **H** once
the scalp closes — but ONLY if the trend is *still valid*, never by replaying a
stale signal.

> **Tier-3 — order-routing-affecting.** Only consulted when
> ``FLIP_POLICY=selective`` (opt-in, default ``hold``). It records intent and
> makes a pure re-open / skip decision; it never sends an order. Promotion to a
> live ``FLIP_POLICY=selective`` is operator- + backtest-gated (§ 7.4 / § 8).

State model (§ 7.2)
-------------------
A **displaced-intent record** per ``(account, symbol)`` — persisted in the
journal, NOT in-process, so a trader restart mid-scalp doesn't abandon trend
restoration. It is PROJECTED onto the held position's own ``order_packages`` row
(``OP_H``), in its ``meta`` JSON under the ``displaced_intent`` key:

    # data-wiring: the displaced-intent record is an enrichment field on the
    #   EXISTING canonical ``order_packages`` row of the trend that was closed
    #   (OP_H), written via Database.update_order_package(meta=...). NO new table
    #   is created — the source of truth for the displaced trade stays
    #   order_packages, exactly as § 7.2 specifies ("projected on order_packages
    #   via a # data-wiring: declaration"). History therefore needs no backfill:
    #   every record lives on a row that already existed. The new-table-wiring
    #   guard (scripts/check_new_table_wiring.py) is satisfied because there is
    #   no CREATE TABLE; this annotation documents the projection per the
    #   db-wiring skill + § Generation Discipline Rule 3.

Lifecycle of the record's ``status``:

    armed_pending_scalp_close   set on the selective flip (scalp opened)
    armed_ready                 set by the order_monitor close path when the
                                scalp (N) closes — "evaluate re-entry next tick"
    reentered                   the trend was re-opened (terminal)
    skipped:<reason>            re-entry declined (terminal) — e.g. signal stale,
                                out of zone, regime changed, confidence too low,
                                window expired

Re-entry GATES (§ 7.2 — re-open H iff ALL hold):
  1. the trend strategy is *currently* re-emitting a same-side actionable signal
  2. price still within ``FLIP_REENTRY_ZONE_FRAC`` of ``OP_H.entry``
  3. regime unchanged (best-effort; permissive when either side unknown)
  4. re-emitted confidence ≥ ``FLIP_REENTRY_MIN_CONFIDENCE``
  5. within the time/bar window (``FLIP_REENTRY_WINDOW_BARS`` on the strategy TF)

Failure mode: **do NOT re-open** → journal ``flip_reentry_skipped:<reason>``.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Key under ``order_packages.meta`` where the displaced-intent record lives.
DISPLACED_INTENT_META_KEY = "displaced_intent"

# Record status values.
STATUS_ARMED_PENDING = "armed_pending_scalp_close"
STATUS_ARMED_READY = "armed_ready"
STATUS_REENTERED = "reentered"


# ---------------------------------------------------------------------------
# Resolvers — mirror resolve_flip_confidence_threshold in intents.py
# ---------------------------------------------------------------------------


def _resolve_float(
    key: str, default: float, *, settings: Optional[Dict[str, Any]] = None,
    floor: Optional[float] = None,
) -> float:
    raw = None
    if isinstance(settings, dict):
        raw = settings.get(key)
    if raw is None:
        raw = os.environ.get(key, "")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if floor is not None:
        return max(floor, val)
    return val


def resolve_flip_reentry_min_confidence(settings: Optional[Dict[str, Any]] = None) -> float:
    """Minimum re-emitted confidence to allow re-entry (§ 7.2 gate 4).

    Default ``0.0`` — any actionable re-emit passes the confidence gate (the
    other gates still apply). ``FLIP_REENTRY_MIN_CONFIDENCE`` env / settings.
    """
    return _resolve_float("FLIP_REENTRY_MIN_CONFIDENCE", 0.0, settings=settings, floor=0.0)


def resolve_flip_reentry_window_bars(settings: Optional[Dict[str, Any]] = None) -> float:
    """How long (in strategy-TF bars) the displaced record stays re-entry-eligible.

    Default ``8.0`` bars. ``0`` disables the time gate (no expiry).
    ``FLIP_REENTRY_WINDOW_BARS`` env / settings.
    """
    return _resolve_float("FLIP_REENTRY_WINDOW_BARS", 8.0, settings=settings, floor=0.0)


def resolve_flip_reentry_zone_frac(settings: Optional[Dict[str, Any]] = None) -> float:
    """Max fractional price drift from ``OP_H.entry`` still counted "in zone".

    Default ``0.005`` (0.5%). The current re-emit price must be within this
    fraction of the displaced trend's original entry. ``0`` disables the zone
    gate. ``FLIP_REENTRY_ZONE_FRAC`` env / settings.
    """
    return _resolve_float("FLIP_REENTRY_ZONE_FRAC", 0.005, settings=settings, floor=0.0)


# ---------------------------------------------------------------------------
# Displaced-intent record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisplacedIntent:
    """The trend that a selective flip displaced, pending conditional re-entry.

    Persisted as a plain dict on ``OP_H.meta[displaced_intent]`` (see module
    docstring). Frozen for safety; ``to_dict`` / ``from_meta`` bridge the JSON.
    """

    account: str
    symbol: str
    strategy: str
    side: str                       # the displaced trend's side ("long"/"short")
    entry: Optional[float]          # OP_H.entry — the zone anchor
    confidence: Optional[float]     # the displaced trend's entry confidence
    regime: Optional[str]           # regime at displacement (best-effort)
    order_package_id: Optional[str]  # OP_H id (where this record lives)
    displaced_at: float             # epoch seconds
    window_bars: float              # snapshot of the window at displacement
    bar_seconds: float              # strategy-TF bar length (for the time gate)
    status: str = STATUS_ARMED_PENDING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account": self.account,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "side": self.side,
            "entry": self.entry,
            "confidence": self.confidence,
            "regime": self.regime,
            "order_package_id": self.order_package_id,
            "displaced_at": self.displaced_at,
            "window_bars": self.window_bars,
            "bar_seconds": self.bar_seconds,
            "status": self.status,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> Optional["DisplacedIntent"]:
        if not isinstance(d, dict) or not d:
            return None
        try:
            return DisplacedIntent(
                account=str(d.get("account") or ""),
                symbol=str(d.get("symbol") or ""),
                strategy=str(d.get("strategy") or ""),
                side=str(d.get("side") or ""),
                entry=(float(d["entry"]) if d.get("entry") is not None else None),
                confidence=(float(d["confidence"]) if d.get("confidence") is not None else None),
                regime=(str(d["regime"]) if d.get("regime") is not None else None),
                order_package_id=(str(d["order_package_id"]) if d.get("order_package_id") else None),
                displaced_at=float(d.get("displaced_at") or 0.0),
                window_bars=float(d.get("window_bars") or 0.0),
                bar_seconds=float(d.get("bar_seconds") or 0.0),
                status=str(d.get("status") or STATUS_ARMED_PENDING),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class ReentryDecision:
    """Pure re-entry verdict (§ 7.2)."""

    reenter: bool
    reason: str


def evaluate_reentry(
    record: DisplacedIntent,
    *,
    signal_side: Optional[str],
    signal_confidence: Optional[float],
    signal_price: Optional[float],
    signal_regime: Optional[str],
    now: Optional[float] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> ReentryDecision:
    """Decide whether to re-open the displaced trend (§ 7.2 — ALL gates must hold).

    Parameters
    ----------
    record : DisplacedIntent
        The persisted displaced-trend record (from ``OP_H.meta``).
    signal_side, signal_confidence, signal_price, signal_regime :
        The trend strategy's CURRENT re-emit this tick (``None`` for any field
        the caller couldn't resolve). ``signal_side`` is the actionable side
        the strategy is firing *now* — re-entry requires it to match
        ``record.side`` (gate 1). A ``None``/non-actionable side fails gate 1.

    Returns
    -------
    ReentryDecision
        ``reenter=True`` only when every gate passes; otherwise ``reenter=False``
        with a ``flip_reentry_skipped`` reason — never resurrect a stale signal.
    """
    now = time.time() if now is None else float(now)

    # Gate 5 (time/bar window) — checked first so an expired record short-circuits
    # the rest. A window of 0 disables the gate.
    window_bars = resolve_flip_reentry_window_bars(settings)
    if window_bars > 0 and record.bar_seconds > 0:
        max_age = window_bars * record.bar_seconds
        age = now - record.displaced_at
        if age > max_age:
            return ReentryDecision(
                reenter=False,
                reason=(
                    f"flip_reentry_skipped:window_expired "
                    f"(age={age:.0f}s > {max_age:.0f}s = {window_bars:g} bars)"
                ),
            )

    # Gate 1 — the trend strategy must be RE-EMITTING a same-side actionable
    # signal THIS tick. No live re-emit → never replay the stale record.
    side = (signal_side or "").strip().lower()
    if side not in ("long", "short"):
        return ReentryDecision(
            reenter=False,
            reason="flip_reentry_skipped:no_live_signal",
        )
    if side != record.side:
        return ReentryDecision(
            reenter=False,
            reason=(
                f"flip_reentry_skipped:side_changed "
                f"(displaced={record.side} now={side})"
            ),
        )

    # Gate 4 — re-emitted confidence floor.
    min_conf = resolve_flip_reentry_min_confidence(settings)
    if min_conf > 0:
        if signal_confidence is None:
            return ReentryDecision(
                reenter=False,
                reason="flip_reentry_skipped:confidence_unknown",
            )
        if float(signal_confidence) < min_conf:
            return ReentryDecision(
                reenter=False,
                reason=(
                    f"flip_reentry_skipped:low_confidence "
                    f"(conf={float(signal_confidence):.3f} < {min_conf:.3f})"
                ),
            )

    # Gate 2 — price still within FLIP_REENTRY_ZONE_FRAC of OP_H.entry.
    zone_frac = resolve_flip_reentry_zone_frac(settings)
    if zone_frac > 0 and record.entry is not None and record.entry > 0:
        if signal_price is None:
            return ReentryDecision(
                reenter=False,
                reason="flip_reentry_skipped:price_unknown",
            )
        drift = abs(float(signal_price) - record.entry) / record.entry
        if drift > zone_frac:
            return ReentryDecision(
                reenter=False,
                reason=(
                    f"flip_reentry_skipped:out_of_zone "
                    f"(drift={drift:.4f} > {zone_frac:.4f})"
                ),
            )

    # Gate 3 — regime unchanged (best-effort; permissive when either is unknown,
    # so a missing regime tag never strands an otherwise-valid re-entry).
    if record.regime and signal_regime and str(record.regime) != str(signal_regime):
        return ReentryDecision(
            reenter=False,
            reason=(
                f"flip_reentry_skipped:regime_changed "
                f"(was={record.regime} now={signal_regime})"
            ),
        )

    return ReentryDecision(
        reenter=True,
        reason=(
            f"flip_reentry_ok:{record.strategy} {record.side} "
            f"(conf={signal_confidence} regime={signal_regime})"
        ),
    )


# ---------------------------------------------------------------------------
# Persistence — projected onto order_packages.meta (no new table)
# ---------------------------------------------------------------------------


def _db():
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path
    return Database(db_path=trade_journal_db_path())


def _load_op_meta(db, order_package_id: str) -> Dict[str, Any]:
    """Read ``order_packages.meta`` for one id, decoded to a dict.

    There is no by-id getter on ``Database`` (only by-strategy / by-symbol), so
    read the single column directly off the same connection. Best-effort: any
    failure returns ``{}`` so a read-modify-write degrades to write-fresh-meta
    rather than crashing the order path.
    """
    import json as _json
    raw = None
    try:
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT meta FROM order_packages WHERE order_package_id = ?",
                (order_package_id,),
            ).fetchone()
        finally:
            conn.close()
        raw = row[0] if row else None
    except Exception:  # noqa: BLE001 — fall back to no-meta
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = _json.loads(raw)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def persist_displaced_intent(record: DisplacedIntent, *, db=None) -> bool:
    """Write the displaced-intent record onto ``OP_H.meta[displaced_intent]``.

    Best-effort (returns ``False`` on any failure, never raises) — a record-write
    failure must never break the order path. The held trend's order package
    (``record.order_package_id``) is the canonical row; we read-modify-write its
    ``meta`` so the record rides the row that already exists.
    """
    if not record.order_package_id:
        return False
    try:
        db = db or _db()
        meta = _load_op_meta(db, record.order_package_id)
        meta[DISPLACED_INTENT_META_KEY] = record.to_dict()
        affected = db.update_order_package(record.order_package_id, {"meta": meta})
        return bool(affected)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "persist_displaced_intent: write failed for pkg=%s: %s",
            record.order_package_id, exc,
        )
        return False


def set_displaced_status(order_package_id: str, status: str, *, db=None) -> bool:
    """Advance the displaced record's ``status`` on ``OP_H.meta`` (best-effort)."""
    if not order_package_id:
        return False
    try:
        db = db or _db()
        meta = _load_op_meta(db, order_package_id)
        rec = meta.get(DISPLACED_INTENT_META_KEY)
        if not isinstance(rec, dict):
            return False
        rec["status"] = status
        meta[DISPLACED_INTENT_META_KEY] = rec
        affected = db.update_order_package(order_package_id, {"meta": meta})
        return bool(affected)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "set_displaced_status: write failed for pkg=%s: %s",
            order_package_id, exc,
        )
        return False


def _find_armed_displaced_for_symbol(
    account: str,
    symbol: str,
    *,
    statuses: tuple = (STATUS_ARMED_PENDING, STATUS_ARMED_READY),
    db=None,
    scan_limit: int = 30,
) -> Optional[DisplacedIntent]:
    """Find the most-recent armed displaced-intent record for (account, symbol).

    Scans the newest ``scan_limit`` order packages for ``symbol`` (the same
    primitive the reverse reconciler uses) and returns the first whose
    ``meta[displaced_intent]`` is for this account and in one of ``statuses``.
    Best-effort: any read failure returns ``None``.
    """
    import json as _json
    try:
        db = db or _db()
        rows = db.get_recent_order_packages_for_symbol(symbol, limit=scan_limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_find_armed_displaced_for_symbol: scan failed for %s/%s: %s",
            account, symbol, exc,
        )
        return None
    for row in rows or []:
        raw = row.get("meta")
        meta: Dict[str, Any]
        if isinstance(raw, dict):
            meta = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                decoded = _json.loads(raw)
                meta = decoded if isinstance(decoded, dict) else {}
            except Exception:  # noqa: BLE001
                continue
        else:
            continue
        rec = DisplacedIntent.from_dict(meta.get(DISPLACED_INTENT_META_KEY) or {})
        if rec is None:
            continue
        if rec.account == account and rec.status in statuses:
            return rec
    return None


def arm_ready_on_scalp_close(account: str, symbol: str, *, db=None) -> bool:
    """Advance an ``armed_pending_scalp_close`` record to ``armed_ready``.

    Called from the order-monitor close path (§ 7.2 — "triggered from the
    monitor close path") when a position on (account, symbol) closes: if a
    displaced-trend record is waiting on this account/symbol, mark it ready so
    the NEXT live tick evaluates re-entry. Best-effort; no-op (returns False)
    when there's no pending record. Idempotent — only flips the pending state.
    """
    rec = _find_armed_displaced_for_symbol(
        account, symbol, statuses=(STATUS_ARMED_PENDING,), db=db,
    )
    if rec is None or not rec.order_package_id:
        return False
    ok = set_displaced_status(rec.order_package_id, STATUS_ARMED_READY, db=db)
    if ok:
        logger.info(
            "[flip_reentry] scalp closed on %s/%s — displaced trend %s %s "
            "armed_ready for re-entry (pkg=%s)",
            account, symbol, rec.strategy, rec.side, rec.order_package_id,
        )
    return ok


def consume_reentry_for_signal(
    *,
    account: str,
    symbol: str,
    strategy: Optional[str],
    signal_side: Optional[str],
    signal_confidence: Optional[float],
    signal_price: Optional[float],
    signal_regime: Optional[str],
    db=None,
    now: Optional[float] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Optional[ReentryDecision]:
    """Evaluate (and terminalise) a ready displaced-intent against a live signal.

    Called on a live tick when ``strategy`` fires an actionable signal on
    (account, symbol). If a matching ``armed_ready`` displaced record exists for
    this strategy, evaluate the § 7.2 gates against the CURRENT signal:

      * gates pass → return ``ReentryDecision(reenter=True, …)`` and mark the
        record ``reentered`` (the caller then lets the signal proceed to open —
        i.e. re-entry is "allow the trend's own fresh signal through", never a
        synthetic replay of the stale one).
      * gates fail → mark the record ``skipped:<reason>`` and return the
        decision (the caller journals ``flip_reentry_skipped:<reason>``).

    Returns ``None`` when there is no ready record for this (account, symbol,
    strategy) — the common case, zero added behaviour. Best-effort; a read/write
    failure returns ``None`` (the signal then proceeds through the normal path).
    """
    if not strategy:
        return None
    try:
        rec = _find_armed_displaced_for_symbol(
            account, symbol, statuses=(STATUS_ARMED_READY,), db=db,
        )
    except Exception:  # noqa: BLE001
        return None
    if rec is None or rec.strategy != strategy:
        return None

    decision = evaluate_reentry(
        rec,
        signal_side=signal_side,
        signal_confidence=signal_confidence,
        signal_price=signal_price,
        signal_regime=signal_regime,
        now=now,
        settings=settings,
    )
    try:
        if decision.reenter:
            set_displaced_status(rec.order_package_id, STATUS_REENTERED, db=db)
        else:
            set_displaced_status(rec.order_package_id, f"skipped:{decision.reason}", db=db)
    except Exception:  # noqa: BLE001 — terminalisation is best-effort
        pass
    return decision


def load_displaced_intent(order_package_id: str, *, db=None) -> Optional[DisplacedIntent]:
    """Read the displaced-intent record off ``OP_H.meta`` (best-effort)."""
    if not order_package_id:
        return None
    try:
        db = db or _db()
        meta = _load_op_meta(db, order_package_id)
        return DisplacedIntent.from_dict(meta.get(DISPLACED_INTENT_META_KEY) or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "load_displaced_intent: read failed for pkg=%s: %s",
            order_package_id, exc,
        )
        return None
