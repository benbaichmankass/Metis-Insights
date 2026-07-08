"""Prop monitoring pulse — a periodic 'still monitoring' heartbeat per open prop trade.

The prop account is a **manual bridge** (``docs/integrations/breakout-poc-manual-bridge-DESIGN.md``):
the bot has no broker API, so it only learns a fill/close when the executor
posts it back (``prop_report.ingest_report``). Between those report-backs the
operator has no signal that the system is still tracking the position — unlike
a live-broker trade, whose monitor runs every tick.

This module closes that gap. While any prop position is open, it emits **one
consolidated** ``prop_monitor`` pulse on a fixed cadence (default hourly) saying
"still monitoring N open prop trades — no change" and listing each. It does
**not** replace the real-time ``prop_fill`` / ``prop_closed`` notifications;
it's the liveness signal *between* them.

Notification-streamlining update (operator directive 2026-07-08): the pulse was
per-position every 15 min; it is now **once an hour, one ping with all the open
trades**. The cadence default moved 900 → 3600 and the emit fans a single
consolidated message rather than one per position.

Design notes:

- **Open-position detection is derived, not stored.** A position is "open" when
  the latest ``prop_fills`` row for its ``(account_id, symbol, direction)`` key
  is ``open``/``filled`` (not ``closed``/``skipped``). Levels (entry/SL/TP) are
  enriched from the linked outbound ticket when available.
- **Cadence state is a small JSON file** (``runtime_logs/prop_monitor_pulse.json``):
  a single ``{"__consolidated__": last_pulse_iso}`` timestamp. Persisted so a
  trader restart doesn't immediately re-fire, and reset to empty when nothing
  is open.
- **Baseline, not gated.** There is no default-off enable flag (Prime
  Directive). The only knob is the cadence ``PROP_MONITOR_PULSE_SECONDS``
  (default 3600); set it ``<= 0`` to pause pulses without a redeploy.
- **Best-effort + isolated.** Every path swallows its own exceptions; a pulse
  failure never propagates into the trader loop. Called once per tick from
  ``src/main.py``; the cadence gate inside means a 60 s tick still only pings
  every hour.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.prop import prop_journal

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 3600  # hourly (was 900 = 15 min; streamlined 2026-07-08)
_STATE_FILENAME = "prop_monitor_pulse.json"
_OPEN_STATUSES = {"open", "filled"}
_CONSOLIDATED_KEY = "__consolidated__"

# Direction aliases collapse to one canonical side so a position keyed by
# (account, symbol, direction) is stable regardless of which vocabulary a
# report-back used. Prop fills arrive from a human bridge, so the SAME position
# can be reported "buy" on the open (broker vocabulary) and "long" on the close
# (order-package vocabulary). Without this, the aliased fills land under
# DIFFERENT keys and a closed position's stale "buy" fill lingers as a phantom
# open — the 2026-07-08 ETHUSDT "still monitoring a closed trade" pulse
# (BL-20260708-PROP-PULSE-DIRECTION-ALIAS).
_DIRECTION_ALIASES = {
    "buy": "long", "b": "long", "bought": "long",
    "sell": "short", "s": "short", "sold": "short",
}


def _canonical_direction(direction: Any) -> str:
    """Map any long/short vocabulary to the canonical ``long``/``short``.

    ``buy``/``sell`` (broker vocabulary) collapse to ``long``/``short``
    (order-package vocabulary); an already-canonical or unknown value passes
    through lowercased, so nothing is silently dropped.
    """
    d = str(direction or "").strip().lower()
    return _DIRECTION_ALIASES.get(d, d)


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
    """Stable identity for a prop position across its fill rows.

    Always keyed on (account_id, symbol, direction) — never on ticket_id.
    A prop account can only hold one position per symbol/direction at a time,
    so the akd key is sufficient AND avoids the split-key bug where an open
    fill (no ticket) and its close fill (matched to a ticket) end up under
    different keys, making the close invisible to the open-position filter.

    Direction is **canonicalized** (buy→long, sell→short) so a position
    reported "buy" on the open and "long" on the close shares one key — else
    the aliased fills split into two keys and a closed position's stale open
    fill lingers as a phantom-open pulse (BL-20260708-PROP-PULSE-DIRECTION-ALIAS).
    """
    return (
        f"akd:{fill.get('account_id') or ''}|"
        f"{str(fill.get('symbol') or '').upper()}|"
        f"{_canonical_direction(fill.get('direction'))}"
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
    emitter: Optional[Callable[[List[Dict[str, Any]]], Dict[str, bool]]] = None,
) -> Dict[str, Any]:
    """Emit ONE consolidated 'still monitoring' pulse for all open prop trades.

    Called once per trader tick. Rate-limited GLOBALLY to ``interval_seconds``
    (env ``PROP_MONITOR_PULSE_SECONDS``, default 3600 = hourly). When at least
    one prop position is open and the consolidated pulse is due, fires a single
    ping listing every open position (operator directive 2026-07-08 — "once an
    hour, one ping with all the open trades"). Returns a stats dict
    ``{open, fired, skipped, paused}`` (``fired`` is 0 or 1). Never raises.
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

    state = _load_state()
    if not positions:
        # Nothing open → reset the consolidated timestamp so the next open
        # position pulses promptly (and the state file never carries a stale
        # key). Only write when it actually changes.
        if state:
            _save_state({})
        return stats

    if emitter is None:
        from src.prop.breakout_notify import emit_prop_monitor_consolidated as emitter  # type: ignore

    last = _parse_iso(state.get(_CONSOLIDATED_KEY))
    due = last is None or (now - last).total_seconds() >= interval
    if not due:
        stats["skipped"] = 1
        logger.info(
            "prop_monitor_pulse: open=%d fired=0 (not due; interval=%ds)",
            stats["open"], interval,
        )
        return stats

    try:
        emitter(positions)
        stats["fired"] = 1
        logger.info(
            "prop_monitor_pulse: fired consolidated pulse for %d open prop trade(s): %s",
            stats["open"],
            ", ".join(f"{p.get('symbol')} {p.get('direction')}" for p in positions),
        )
    except Exception as exc:  # noqa: BLE001 — emission never fatal
        logger.warning("prop_monitor_pulse: consolidated emit failed: %s", exc)

    _save_state({_CONSOLIDATED_KEY: now.isoformat()})
    logger.info(
        "prop_monitor_pulse: open=%d fired=%d (interval=%ds)",
        stats["open"], stats["fired"], interval,
    )
    return stats


__all__ = [
    "find_open_prop_positions",
    "run_prop_monitor_pulse",
]
