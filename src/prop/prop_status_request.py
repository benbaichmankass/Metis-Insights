"""Prop account-status request — ask the operator for a fresh snapshot when the
rule-distance guard is flying blind.

The prop account is a **manual bridge**: the bot only knows the account's
balance/equity when the operator reports it back (``prop_report.ingest_report``
with ``kind:"account_status"``). The rule-distance safety panel (distance to
the daily-loss limit and the static-DD floor, ``prop_reconcile.compute_rule_distance``)
is computed from the LATEST ``prop_account_status`` row — with no row, or a
stale one, it can never warn before an account-killer is breached.

This module closes the loop from the bot's side: while a prop position is
OPEN and the latest account-status snapshot is absent or older than
``PROP_STATUS_REQUEST_MAX_AGE_HOURS``, it pings the operator on the prop bot
with a **paste-ready reply template** (both accepted formats — the ``bal``
one-liner and the JSON block — exactly what ``telegram_report_handler``
parses), then waits ``PROP_STATUS_REQUEST_COOLDOWN_HOURS`` before asking again.

Design notes (mirrors ``prop_monitor_pulse``):

- **Open-position detection is reused**, not re-derived —
  :func:`src.prop.prop_monitor_pulse.find_open_prop_positions`.
- **Cadence state is a small JSON file**
  (``runtime_logs/prop_status_request.json``): ``{account_id: last_request_iso}``,
  pruned to accounts that currently hold open positions.
- **Baseline, not gated.** No default-off enable flag (Prime Directive). Knobs:
  ``PROP_STATUS_REQUEST_MAX_AGE_HOURS`` (default 24; ``<= 0`` pauses the
  feature) and ``PROP_STATUS_REQUEST_COOLDOWN_HOURS`` (default 12).
- **Best-effort + isolated.** Every path swallows its own exceptions; called
  once per trader tick from ``src/main.py``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_HOURS = 24.0
_DEFAULT_COOLDOWN_HOURS = 12.0
_STATE_FILENAME = "prop_status_request.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _hours_knob(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _state_path() -> str:
    from src.utils.paths import runtime_logs_dir

    return str(runtime_logs_dir() / _STATE_FILENAME)


def _load_state() -> Dict[str, str]:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: Dict[str, str]) -> None:
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError as exc:
        logger.warning("prop_status_request: state save failed: %s", exc)


def _status_age_hours(account_id: str, now: datetime) -> Optional[float]:
    """Age of the newest ``prop_account_status`` row, or ``None`` when absent."""
    try:
        from src.prop import prop_journal

        row = prop_journal.latest_account_status(account_id)
    except Exception as exc:  # noqa: BLE001 — a read failure must not raise
        logger.warning("prop_status_request: status read failed: %s", exc)
        return None
    if not row:
        return None
    ts = _parse_iso(row.get("reported_at") or row.get("created_at") or row.get("ts"))
    if ts is None:
        return None
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def run_prop_status_request(now: Optional[datetime] = None) -> List[str]:
    """One tick of the status-request loop. Returns account_ids pinged."""
    max_age_h = _hours_knob("PROP_STATUS_REQUEST_MAX_AGE_HOURS", _DEFAULT_MAX_AGE_HOURS)
    if max_age_h <= 0:  # paused without a redeploy
        return []
    cooldown_h = _hours_knob(
        "PROP_STATUS_REQUEST_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS)
    now = now or _now()

    try:
        from src.prop.prop_monitor_pulse import find_open_prop_positions

        positions = find_open_prop_positions() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_status_request: open-position scan failed: %s", exc)
        return []
    if not positions:
        # Nothing open ⇒ the rule-distance guard has nothing live to protect;
        # prune state so a long-flat account re-asks promptly on the next open.
        _save_state({})
        return []

    by_account: Dict[str, List[Dict[str, Any]]] = {}
    for pos in positions:
        acct = str(pos.get("account_id") or pos.get("account") or "").strip()
        if acct:
            by_account.setdefault(acct, []).append(pos)

    state = _load_state()
    pinged: List[str] = []
    for acct, open_positions in by_account.items():
        age_h = _status_age_hours(acct, now)
        if age_h is not None and age_h < max_age_h:
            continue  # snapshot fresh enough — nothing to ask
        last_req = _parse_iso(state.get(acct))
        if last_req and (now - last_req).total_seconds() < cooldown_h * 3600.0:
            continue  # asked recently — don't nag
        try:
            from src.prop.breakout_notify import emit_prop_status_request

            emit_prop_status_request(acct, open_positions, age_hours=age_h)
            state[acct] = now.isoformat()
            pinged.append(acct)
        except Exception as exc:  # noqa: BLE001 — notification never fatal
            logger.warning(
                "prop_status_request: emit failed for %s: %s", acct, exc)

    # prune to accounts that still hold open positions
    _save_state({k: v for k, v in state.items() if k in by_account})
    return pinged


__all__ = ["run_prop_status_request"]
