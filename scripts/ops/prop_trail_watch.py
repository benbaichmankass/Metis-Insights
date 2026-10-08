# wiring: imported by scripts/ops/attention_watch.py::build, inside the hourly
# deploy/ict-work-digest.timer carrier (no unit of its own). Tests:
# tests/test_prop_trail_watch.py. Shape follows scripts/ops/prop_silence.py
# (PROP-SILENCE-ALERTS): every probe is ok / breached / unknown with
# level + priority, so the existing edge class (breach -> ping, one clear line,
# unknown never clears) carries it.
"""``prop_trail_paused_<account>`` — does anyone get told when the prop trail
latches (``modify_rollout.json``) and every ticket stops being trailed until
``executor-clear-rollout``?  (TRAIL-PAUSE-PULSE, PI-20261006-KX6ZKFNA-0003.)

Reads the pulse the executor tick writes each tick
(``<state dir>/prop_monitor_pulse.json``, ``src.prop.trail_pause``):

* ``trail_paused_since`` null           -> ok (clears a standing alarm)
* non-null for ONE tick                 -> ok (``waiting``: one watched step is
  the designed rollout, the first tick after it is expected)
* non-null for >= ``PAUSED_TICKS_URGENT`` consecutive ticks -> breached/urgent
* ``"unknown"`` / pulse missing / unreadable / stale (> ``PULSE_MAX_HOURS``) ->
  unknown, which never clears and is never read as "not paused".

Only accounts whose executor state dir exists get a probe: an account with no
executor on this host has no pulse to read and no claim to make.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.prop import trail_pause

OK, BREACHED, UNKNOWN = "ok", "breached", "unknown"

EXECUTOR_ACCOUNTS = ("breakout_1", "tradeify_1", "velotrade_1")
PAUSED_TICKS_URGENT = 2
PULSE_MAX_HOURS = 3


def key(account: str) -> str:
    return f"prop_trail_paused_{account}"


LABELS = {key(a): f"{a} prop trail paused (modify_rollout latch)" for a in EXECUTOR_ACCOUNTS}


def _probe(status: str, detail: str, *, level: str | None = None, priority: str = "high",
           since: str | None = None, ticks: int | None = None) -> dict:
    return {"status": status, "detail": detail, "level": level, "priority": priority,
            "since": since, "ticks": ticks}


def probe_trail_paused(account: str, now: datetime, state_dir: Path | None = None) -> dict:
    d = state_dir or trail_pause.executor_state_dir(account)
    pulse = trail_pause.read_pulse(d)
    if pulse is None or "trail_paused_since" not in pulse:
        return _probe(UNKNOWN, f"{account}: no readable executor pulse in {d.name}/ — cannot tell "
                               f"whether the trail is paused")
    at = pulse.get("at")
    try:
        at_t = datetime.fromisoformat(str(at))
        at_t = at_t if at_t.tzinfo else at_t.replace(tzinfo=timezone.utc)
    except ValueError:
        return _probe(UNKNOWN, f"{account}: pulse 'at' unreadable ({at!r})")
    age_h = max(0.0, (now - at_t).total_seconds() / 3600)
    if age_h > PULSE_MAX_HOURS:
        return _probe(UNKNOWN, f"{account}: last executor pulse {age_h:.1f} h ago "
                               f"(> {PULSE_MAX_HOURS} h) — executor not ticking, trail state unknown")
    since = pulse["trail_paused_since"]
    if since == trail_pause.UNKNOWN:
        return _probe(UNKNOWN, f"{account}: executor could not read the latch file")
    if since is None:
        return _probe(OK, f"{account}: trail not paused")
    if not isinstance(since, str):
        return _probe(UNKNOWN, f"{account}: trail_paused_since unreadable ({since!r})")
    n = pulse.get("trail_paused_ticks")
    n = n if isinstance(n, int) and not isinstance(n, bool) else None
    if n is None:
        return _probe(UNKNOWN, f"{account}: latched since {since} but tick count unreadable", since=since)
    msg = f"{account}: trail PAUSED since {since} for {n} tick(s); review, then executor-clear-rollout"
    if n >= PAUSED_TICKS_URGENT:
        return _probe(BREACHED, msg, level="urgent", priority="urgent", since=since, ticks=n)
    return _probe(OK, f"{msg} (first tick, not yet urgent)", since=since, ticks=n)


def all_probes(now: datetime) -> dict[str, dict]:
    out: dict[str, Any] = {}
    for a in EXECUTOR_ACCOUNTS:
        if trail_pause.executor_state_dir(a).is_dir():
            out[key(a)] = probe_trail_paused(a, now)
    return out
