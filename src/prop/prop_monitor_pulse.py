"""Prop monitoring pulse — a periodic 'still monitoring' heartbeat per open prop trade.

The prop account is a **manual bridge** (``docs/integrations/breakout-poc-manual-bridge-DESIGN.md``):
the bot has no broker API, so it only learns a fill/close when the executor
posts it back (``prop_report.ingest_report``). Between those report-backs the
operator has no signal that the system is still tracking the position — unlike
a live-broker trade, whose monitor runs every tick.

This module closes that gap. While a prop position is open, it emits a
``prop_monitor`` pulse on a fixed cadence (default every 15 min) saying "still
monitoring — no change". It does **not** replace the real-time ``prop_fill`` /
``prop_closed`` notifications; it's the liveness signal *between* them.

Design notes:

- **Open-position detection is derived, not stored.** A position is "open" when
  the latest ``prop_fills`` row for its key is ``open``/``filled`` (not
  ``closed``/``skipped``). The key is the ``ticket_id`` when present, else
  ``(account_id, symbol, direction)`` (so a fill reported without a ticket link
  still gets tracked). Levels (entry/SL/TP) are enriched from the linked
  outbound ticket when available.
- **Cadence state is a small JSON file** (``runtime_logs/prop_monitor_pulse.json``):
  ``{position_key: last_pulse_iso}``. Persisted so a trader restart doesn't
  re-fire every open position immediately, and pruned to only currently-open
  keys so it can't grow unbounded.
- **Baseline, not gated.** There is no default-off enable flag (Prime
  Directive). The only knob is the cadence ``PROP_MONITOR_PULSE_SECONDS``
  (default 900); set it ``<= 0`` to pause pulses without a redeploy.
- **Best-effort + isolated.** Every path swallows its own exceptions; a pulse
  failure never propagates into the trader loop. Called once per tick from
  ``src/main.py``; the cadence gate inside means a 60 s tick still only pings
  every 15 min.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.prop import prop_journal

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 900  # 15 minutes
_STATE_FILENAME = "prop_monitor_pulse.json"
_OPEN_STATUSES = {"open", "filled"}


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


def _interval_seconds() -> int:
    """Pulse cadence in seconds (env-overridable; ``<= 0`` pauses pulses)."""
    raw = os.environ.get("PROP_MONITOR_PULSE_SECONDS")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_INTERVAL_SECONDS
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return _DEFAULT_INTERVAL_SECONDS


def _position_key(fill: Dict[str, Any]) -> str:
    """Stable identity for a prop position across its fill rows."""
    tid = fill.get("ticket_id")
    if tid:
        return f"ticket:{tid}"
    return (
        f"akd:{fill.get('account_id') or ''}|"
        f"{str(fill.get('symbol') or '').upper()}|"
        f"{str(fill.get('direction') or '').lower()}"
    )


def find_open_prop_positions(
    *, account_id: Optional[str] = None, now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return the currently-open prop positions, derived from ``prop_fills``.

    Fills are grouped by :func:`_position_key`; the newest fill per key decides
    the position's state. A position is open when that newest fill's status is
    ``open``/``filled``. Each returned dict carries the position summary +
    levels enriched from the linked outbound ticket when one exists.
    """
    now = now or _now()
    try:
        fills = prop_journal.list_fills(account_id=account_id, limit=2000)
    except Exception as exc:  # noqa: BLE001 — never break the trader loop
        logger.warning("prop_monitor_pulse: list_fills failed: %s", exc)
        return []
    if not fills:
        return []

    # list_fills is newest-first (ORDER BY id DESC) — the first row seen per
    # key is therefore the latest fill for that position.
    latest_by_key: Dict[str, Dict[str, Any]] = {}
    for f in fills:
        key = _position_key(f)
        if key not in latest_by_key:
            latest_by_key[key] = f

    # Ticket enrichment (entry/sl/tp) keyed by ticket_id, loaded once.
    tickets_by_id: Dict[str, Dict[str, Any]] = {}
    try:
        for t in prop_journal.list_tickets(account_id=account_id, limit=500):
            tid = t.get("ticket_id")
            if tid:
                tickets_by_id[str(tid)] = t
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_monitor_pulse: list_tickets failed: %s", exc)

    out: List[Dict[str, Any]] = []
    for key, f in latest_by_key.items():
        status = str(f.get("status") or "").lower()
        if status not in _OPEN_STATUSES:
            continue
        tid = f.get("ticket_id")
        tk = tickets_by_id.get(str(tid)) if tid else None
        opened_at = (
            f.get("opened_at") or f.get("reported_at") or f.get("created_at")
        )
        opened_dt = _parse_iso(opened_at)
        age_minutes = (
            int((now - opened_dt).total_seconds() // 60)
            if opened_dt is not None else None
        )
        out.append({
            "key": key,
            "account_id": f.get("account_id"),
            "symbol": f.get("symbol"),
            "direction": f.get("direction"),
            "qty": f.get("qty"),
            "entry_price": f.get("entry_price"),
            "ticket_id": tid,
            "entry": (tk or {}).get("entry") if tk else f.get("entry_price"),
            "sl": (tk or {}).get("sl") if tk else f.get("sl"),
            "tp": (tk or {}).get("tp") if tk else f.get("tp"),
            "opened_at": opened_at,
            "age_minutes": age_minutes,
            "status": status,
        })
    return out


def _state_path() -> str:
    from src.utils.paths import runtime_logs_dir

    return str(runtime_logs_dir() / _STATE_FILENAME)


def _load_state() -> Dict[str, str]:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(state: Dict[str, str]) -> None:
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("prop_monitor_pulse: state save failed: %s", exc)


def run_prop_monitor_pulse(
    *, now: Optional[datetime] = None,
    interval_seconds: Optional[int] = None,
    emitter: Optional[Callable[[Dict[str, Any]], Dict[str, bool]]] = None,
) -> Dict[str, Any]:
    """Emit a 'still monitoring' pulse for any open prop trade that is due.

    Called once per trader tick. Internally rate-limited per position to
    ``interval_seconds`` (env ``PROP_MONITOR_PULSE_SECONDS``, default 900).
    A newly-seen open position pulses immediately (an acknowledgement that
    monitoring has started), then every interval thereafter. Returns a stats
    dict ``{open, fired, skipped, paused}``. Never raises.
    """
    stats = {"open": 0, "fired": 0, "skipped": 0, "paused": False}
    interval = interval_seconds if interval_seconds is not None else _interval_seconds()
    if interval <= 0:
        stats["paused"] = True
        return stats

    now = now or _now()
    try:
        positions = find_open_prop_positions(now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_monitor_pulse: open-position scan failed: %s", exc)
        return stats
    stats["open"] = len(positions)

    if emitter is None:
        from src.prop.breakout_notify import emit_prop_monitor_pulse as emitter  # type: ignore

    state = _load_state()
    new_state: Dict[str, str] = {}
    open_keys = {p["key"] for p in positions}

    for pos in positions:
        key = pos["key"]
        last = _parse_iso(state.get(key))
        due = last is None or (now - last).total_seconds() >= interval
        if not due:
            # Carry the prior pulse time forward unchanged.
            new_state[key] = state[key]
            stats["skipped"] += 1
            continue
        try:
            emitter(pos)
            stats["fired"] += 1
            logger.info(
                "prop_monitor_pulse: fired pulse %s %s [%s] age=%smin (key=%s)",
                pos.get("symbol"), pos.get("direction"),
                pos.get("account_id"), pos.get("age_minutes"), key,
            )
        except Exception as exc:  # noqa: BLE001 — emission never fatal
            logger.warning("prop_monitor_pulse: emit failed for %s: %s", key, exc)
        new_state[key] = now.isoformat()

    # Drop state for positions that are no longer open (closed/skipped/gone)
    # so the file only ever holds live keys. (Closed keys not in open_keys are
    # simply not copied into new_state.)
    if new_state != state or open_keys != set(state.keys()):
        _save_state(new_state)

    # Observability: surface the scan outcome in the trader journal whenever
    # there's an open prop position (so the pulse is verifiable via journalctl,
    # not just by the operator's Telegram). Silent when nothing is open, to
    # avoid spamming a per-tick line on every tick with no prop trades.
    if stats["open"]:
        logger.info(
            "prop_monitor_pulse: open=%d fired=%d skipped=%d (interval=%ds)",
            stats["open"], stats["fired"], stats["skipped"], interval,
        )

    return stats


__all__ = [
    "find_open_prop_positions",
    "run_prop_monitor_pulse",
]
