"""Liveness watchdog — alerts the operator when actionable signals
fire but no trades land.

This module exists because BUG-034 (the VWAP execution gap fixed in
#308) hid for an unknown duration. Per-tick failure pings are great
(``execution_diagnostics.enqueue_execution_failure`` covers those),
but if every tick fails *silently in the same way*, the operator
needs a separate "the trader has been signalling but not trading for
the past hour" alert. That's this module.

CLAUDE.md § Architecture rules § 6 + architecture-audit-2026-05-02
P0-3 mandate this watchdog.

## Wiring

Called once per hour from ``src/main.py``'s hourly cycle (alongside
``build_hourly_report``). The watchdog reads the last hour of
``runtime_logs/signal_audit.jsonl`` and ``trade_journal.db::trades``
and decides whether to enqueue a ping.

## Decision rule

- Count actionable signals — rows where ``side ∈ {buy, sell}`` and
  ``status ∈ {submitted, multi_account_dispatched, dry_run}``.
  ("dry_run" counts because the operator wants to know if everything
  is on dry-run when it shouldn't be.)
- Count trades placed — rows in ``trades`` with
  ``created_at >= since`` and ``is_backtest = 0``.
- If signals ≥ threshold (default 5) AND trades = 0 → ping.

## Anti-spam

A state file at ``runtime_logs/liveness_watchdog_state.json`` records
the slot of the last alert (UTC ``YYYY-MM-DD-HH``). The watchdog
fires at most once per hour-slot; subsequent calls in the same slot
return early. The operator can manually clear the file to re-arm.

## Best-effort

The watchdog must never crash the hourly cycle. Every IO step is
wrapped; a failed read returns 0 and emits a logger.warning.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SIGNAL_AUDIT = runtime_logs_dir() / "signal_audit.jsonl"
_STATE_FILE = runtime_logs_dir() / "liveness_watchdog_state.json"

DEFAULT_SIGNAL_THRESHOLD = 5
DEFAULT_LOOKBACK_HOURS = 1

# Statuses that count as "an actionable signal that the pipeline tried
# to dispatch". Anything outside this set (skipped, halted, news_veto,
# failed_validation) means the pipeline never reached the order layer
# — so a 0-fill window for those signals isn't a watchdog miss.
_ACTIONABLE_STATUSES = {
    "submitted",
    "multi_account_dispatched",
    "dry_run",
}

# Rejection-reason markers that PROVE the order layer was reached and the
# pipeline INTENTIONALLY declined to open/grow a position — i.e. the 0-fill
# is *explained*, not silent. These are the position-management holds:
#   - the netting guard refusing to pyramid an already-held position
#     (``reentry_suppressed_netting_guard:*``)
#   - ``FLIP_POLICY=hold`` and the other intent noops the aggregator emits
#     (``intent_noop:flip_suppressed_hold_policy``, ``intent_noop:at_target``,
#      ``intent_noop:hold_to_bracket_reduce_non_derivative`` …)
# Each is journaled as a ``status='rejected'`` ``trades`` row whose
# ``entry_reason`` carries the marker. A window whose only "missing" fills
# are explained by these is a held-position window in a trending market — NOT
# the silent-execution gap (BUG-034) this watchdog exists to catch. Note this
# deliberately EXCLUDES error-class refusals (``risk_refused`` /
# ``exchange_rejected`` / ``sizing_failed`` / ``dry_run_no_order_placed``):
# those are not intentional holds and remain alert-worthy.
_INTENTIONAL_HOLD_MARKERS = (
    "reentry_suppressed_netting_guard",
    "intent_noop:",
)


@dataclass
class LivenessResult:
    """Summary of the watchdog's last decision."""

    signals_actionable: int
    trades_placed: int
    threshold: int
    lookback_hours: int
    fired: bool
    reason: str
    slot_key: str


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("liveness_watchdog: state read failed: %s", exc)
    return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("liveness_watchdog: state write failed: %s", exc)


def _slot_key(now_utc: datetime) -> str:
    now = now_utc.astimezone(timezone.utc)
    return f"{now.date().isoformat()}-{now.hour:02d}"


def _count_actionable_signals(since_iso: str, audit_path: Path) -> int:
    if not audit_path.exists():
        return 0
    count = 0
    try:
        with audit_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                ts = row.get("logged_at_utc") or ""
                if ts < since_iso:
                    continue
                if row.get("side") not in {"buy", "sell"}:
                    continue
                status = (row.get("status") or "").strip().lower()
                if status not in _ACTIONABLE_STATUSES:
                    continue
                count += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("liveness_watchdog: signal-audit read failed: %s", exc)
        return 0
    return count


def _count_trades_placed(since_iso: str, db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            # Refusal rows (status='rejected' / 'exchange_rejected') must
            # be excluded — the watchdog's job is "did anything actually
            # land on the exchange?", and rejection rows are the
            # opposite. Counting them would silently neuter the watchdog.
            # (CP-2026-05-03-14.)
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE is_backtest = 0 "
                "AND COALESCE(status, 'open')"
                " NOT IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned') "
                "AND datetime(created_at) >= datetime(?) ",
                (since_iso,),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("liveness_watchdog: trade-journal read failed: %s", exc)
        return 0


def _count_intentional_holds(since_iso: str, db_path: Path) -> int:
    """Count in-window ``trades`` rows refused by an INTENTIONAL position-
    management hold (see ``_INTENTIONAL_HOLD_MARKERS``).

    A non-zero count proves the pipeline reached the order layer and
    deliberately declined to trade (already holding the position /
    ``FLIP_POLICY=hold``) — so a 0-fill window is *explained*, not the
    silent-execution gap this watchdog guards against. Best-effort: a
    missing column (older/test schema) or any read error returns 0, so the
    watchdog degrades to its prior behaviour (alert on a 0-fill window)
    rather than silently suppressing.
    """
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            like_clause = " OR ".join(
                ["entry_reason LIKE ?"] * len(_INTENTIONAL_HOLD_MARKERS)
            )
            params = [f"%{m}%" for m in _INTENTIONAL_HOLD_MARKERS]
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE is_backtest = 0 "
                "AND COALESCE(status, '') IN "
                "('rejected', 'exchange_rejected', 'rejected_too_small') "
                f"AND ({like_clause}) "
                "AND datetime(created_at) >= datetime(?) ",
                (*params, since_iso),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "liveness_watchdog: intentional-hold read failed: %s", exc
        )
        return 0


def check_liveness(
    *,
    now_utc: Optional[datetime] = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    signal_threshold: int = DEFAULT_SIGNAL_THRESHOLD,
    audit_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> LivenessResult:
    """Return a ``LivenessResult`` describing the current state.

    Pure: this function does not enqueue pings or write state — that's
    ``run_liveness_watchdog``'s job. Tests use ``check_liveness`` to
    assert the *decision* without running the side-effects.
    """
    now = now_utc or datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback_hours)
    since_iso = since.isoformat()

    audit = audit_path or _SIGNAL_AUDIT
    from src.utils.paths import trade_journal_db_path
    db = db_path or Path(trade_journal_db_path())

    signals = _count_actionable_signals(since_iso, audit)
    trades = _count_trades_placed(since_iso, db)

    if signals < signal_threshold:
        return LivenessResult(
            signals_actionable=signals,
            trades_placed=trades,
            threshold=signal_threshold,
            lookback_hours=lookback_hours,
            fired=False,
            reason=(
                f"below_threshold: {signals} actionable signals "
                f"(< {signal_threshold}) — quiet window is normal"
            ),
            slot_key=_slot_key(now),
        )
    if trades > 0:
        return LivenessResult(
            signals_actionable=signals,
            trades_placed=trades,
            threshold=signal_threshold,
            lookback_hours=lookback_hours,
            fired=False,
            reason=(
                f"healthy: {signals} signals → {trades} trades placed "
                f"in last {lookback_hours}h"
            ),
            slot_key=_slot_key(now),
        )
    # Threshold breached + zero fills. Before alerting, check whether the
    # 0-fill is EXPLAINED by intentional position-management holds — the order
    # layer WAS reached and deliberately declined to open/grow a position
    # (already holding it / FLIP_POLICY=hold). That is a held-position window
    # in a trending market (the strategy re-fires an entry every tick and the
    # netting guard / hold policy correctly refuses it), NOT the silent-
    # execution gap (BUG-034) this watchdog exists to catch. Suppressing here
    # removes the recurring false-positive URGENT ping while STILL firing when
    # the 0-fill is unexplained (no journal row at all — the true silent gap)
    # or explained only by error-class refusals (risk_refused /
    # exchange_rejected / sizing_failed), which are not intentional holds.
    holds = _count_intentional_holds(since_iso, db)
    if holds > 0:
        return LivenessResult(
            signals_actionable=signals,
            trades_placed=0,
            threshold=signal_threshold,
            lookback_hours=lookback_hours,
            fired=False,
            reason=(
                f"held_or_suppressed: {signals} actionable signals, 0 fills, "
                f"but {holds} would-be trade(s) intentionally held/suppressed "
                f"(netting guard / hold policy) in last {lookback_hours}h — "
                f"the order layer was reached and deliberately declined; not a "
                f"silent execution gap"
            ),
            slot_key=_slot_key(now),
        )

    # Threshold breached + zero fills + nothing explaining it → fire.
    return LivenessResult(
        signals_actionable=signals,
        trades_placed=0,
        threshold=signal_threshold,
        lookback_hours=lookback_hours,
        fired=True,
        reason=(
            f"liveness_alert: {signals} actionable signals fired but "
            f"0 trades placed in last {lookback_hours}h — execution "
            f"path may be silently failing (see "
            f"runtime_logs/pending_pings/ for per-tick diagnostics)"
        ),
        slot_key=_slot_key(now),
    )


def _enqueue_liveness_ping(result: LivenessResult) -> bool:
    """Drop a high-priority Telegram ping into the pending-pings inbox.

    Best-effort — failures log a warning and return False. Re-uses the
    same atomic-write pattern as ``execution_diagnostics``.
    """
    try:
        import uuid
        pending_dir = runtime_logs_dir() / "pending_pings"
        pending_dir.mkdir(parents=True, exist_ok=True)
        body = (
            "🔇 Liveness watchdog\n"
            f"{result.signals_actionable} actionable signals fired in "
            f"the last {result.lookback_hours}h, but 0 trades landed.\n"
            "The execution path may be silently failing. Check "
            "runtime_logs/pending_pings/ for the per-tick diagnostic "
            "history; if empty, check journalctl for the trader unit."
        )[:1024]
        payload = {"priority": "urgent", "body": body}
        name = f"{int(uuid.uuid4().int % 10**12):012d}-liveness.json"
        path = pending_dir / name
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("liveness_watchdog: ping enqueue failed: %s", exc)
        return False


def run_liveness_watchdog(
    *,
    now_utc: Optional[datetime] = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    signal_threshold: int = DEFAULT_SIGNAL_THRESHOLD,
) -> LivenessResult:
    """Top-level entry point — runs the check and enqueues a ping if
    fired. Honours the per-slot dedupe via the state file.

    Returns the ``LivenessResult`` regardless so the caller can log
    the outcome (whether or not a ping fired).
    """
    result = check_liveness(
        now_utc=now_utc,
        lookback_hours=lookback_hours,
        signal_threshold=signal_threshold,
    )

    if not result.fired:
        logger.info(
            "liveness_watchdog: %s (signals=%d trades=%d)",
            result.reason, result.signals_actionable, result.trades_placed,
        )
        return result

    state = _load_state()
    if state.get("last_alert_slot") == result.slot_key:
        logger.info(
            "liveness_watchdog: dedupe — already alerted for slot %s",
            result.slot_key,
        )
        return result

    if _enqueue_liveness_ping(result):
        state["last_alert_slot"] = result.slot_key
        state["last_alert_at"] = (now_utc or datetime.now(timezone.utc)).isoformat()
        state["last_alert_signals"] = result.signals_actionable
        state["last_alert_trades"] = result.trades_placed
        _save_state(state)
        logger.warning(
            "liveness_watchdog: ALERT enqueued — %d signals / %d trades "
            "in last %dh (slot=%s)",
            result.signals_actionable, result.trades_placed,
            result.lookback_hours, result.slot_key,
        )

    return result
