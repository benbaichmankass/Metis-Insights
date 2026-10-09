"""Is the prop trail latched, and since when? (TRAIL-PAUSE-PULSE, 2026-10-07)

stdlib only, so both the executor tick (which writes the pulse) and the hourly
attention watch (which reads it) can import it without pandas.

The latch was ``<executor state dir>/modify_rollout.json``: until NO-HALT
(operator 2026-10-09, "There is no halting."; PI-20261009-72XUJX8U-0001) its
EXISTENCE paused the trail for every ticket until ``executor-clear-rollout``.
It is RETIRED: nothing writes it and nothing blocks on it, and
``ModifyRollout`` moves a stale copy aside on the next trail/executor pass. So
on a deployed host ``trail_paused_since`` reads ``None``; a non-null value
means the file is there (the retired code is what is running, or the pass that
removes it has not run yet). The non-blocking record of the last armed step is
``modify_rollout_last.json`` and is NOT read here. ``trail_paused_since`` is a
THREE-valued field and the three are never collapsed:

* ``None``       -- we looked, the file is absent: not paused.
* ISO string     -- the retired latch file is present; its own ``at`` (epoch
                    seconds), else the file's mtime when ``at`` is
                    missing/garbled (the instant is best-effort).
* ``"unknown"``  -- we could not look (the directory or file raised something
                    other than "not found"). NEVER read as "not paused".
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROLLOUT_FILE = "modify_rollout.json"  # == src.prop.prop_trail.ROLLOUT_FILE (tested)
PULSE_FILE = "prop_monitor_pulse.json"
UNKNOWN = "unknown"


def executor_state_dir(account: str, base: Path | None = None) -> Path:
    """Mirrors scripts/ops/prop_executor_tick.sh and the diag resolver:
    breakout_1 keeps ``<base>/executor``, any other account
    ``<base>/accounts/<account>/executor``."""
    base = Path(base) if base else Path(os.environ.get("PROP_BROWSER_BASE")
                                        or Path.home() / ".cache" / "metis-prop-browser")
    return base / "executor" if account == "breakout_1" else base / "accounts" / account / "executor"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def read_trail_paused_since(state_dir: Path) -> str | None:
    """``None`` | ISO timestamp | ``"unknown"`` (see module docstring)."""
    path = Path(state_dir) / ROLLOUT_FILE
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return UNKNOWN
    try:
        at = json.loads(path.read_text()).get("at")
        if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
            return _iso(float(at))
    except (OSError, ValueError, AttributeError):
        pass
    return _iso(st.st_mtime)


def read_pulse(state_dir: Path) -> dict[str, Any] | None:
    """The last pulse the executor wrote, or None when absent/unreadable."""
    try:
        data = json.loads((Path(state_dir) / PULSE_FILE).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_pulse(account: str, state_dir: Path, now: float | None = None) -> dict[str, Any]:
    """This tick's pulse. ``trail_paused_ticks`` counts CONSECUTIVE ticks that
    saw the same latch; null/changed latch resets it, ``unknown`` holds it."""
    now = time.time() if now is None else now
    since = read_trail_paused_since(state_dir)
    prev = read_pulse(state_dir) or {}
    prev_n = prev.get("trail_paused_ticks")
    prev_n = prev_n if isinstance(prev_n, int) and not isinstance(prev_n, bool) else 0
    if since is None:
        n = 0
    elif since == UNKNOWN:
        n = prev_n
    else:
        n = prev_n + 1 if prev.get("trail_paused_since") == since else 1
    return {"account": account, "at": _iso(now), "trail_paused_since": since, "trail_paused_ticks": n}


def write_pulse(state_dir: Path, pulse: dict[str, Any]) -> bool:
    """Atomic best-effort write; False (never raises) when it could not."""
    try:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (PULSE_FILE + ".tmp")
        tmp.write_text(json.dumps(pulse))
        tmp.replace(d / PULSE_FILE)
        return True
    except OSError:
        return False
