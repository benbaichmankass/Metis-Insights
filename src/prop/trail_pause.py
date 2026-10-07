"""Is the prop trail latched, and since when? (TRAIL-PAUSE-PULSE, 2026-10-07)

stdlib only, so both the executor tick (which writes the pulse) and the hourly
attention watch (which reads it) can import it without pandas.

The latch is ``<executor state dir>/modify_rollout.json`` (``ModifyRollout``):
its EXISTENCE pauses the trail for every ticket until ``executor-clear-rollout``
removes it. ``trail_paused_since`` is a THREE-valued field and the three are
never collapsed:

* ``None``       -- we looked, the file is absent: not paused.
* ISO string     -- latched; the latch's own ``at`` (epoch seconds), else the
                    file's mtime when ``at`` is missing/garbled (existence is
                    the fact that blocks; the instant is best-effort).
* ``"unknown"``  -- we could not look (the directory or file raised something
                    other than "not found"). NEVER read as "not paused".
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROLLOUT_FILE = "modify_rollout.json"  # == src.prop.prop_trail.ROLLOUT_FILE (tested)
PULSE_FILE = "prop_monitor_pulse.json"
UNKNOWN = "unknown"


def executor_state_dir(account: str, base: Optional[Path] = None) -> Path:
    """Mirrors scripts/ops/prop_executor_tick.sh and the diag resolver:
    breakout_1 keeps ``<base>/executor``, any other account
    ``<base>/accounts/<account>/executor``."""
    base = Path(base) if base else Path(os.environ.get("PROP_BROWSER_BASE")
                                        or Path.home() / ".cache" / "metis-prop-browser")
    return base / "executor" if account == "breakout_1" else base / "accounts" / account / "executor"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def read_trail_paused_since(state_dir: Path) -> Optional[str]:
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


def read_pulse(state_dir: Path) -> Optional[Dict[str, Any]]:
    """The last pulse the executor wrote, or None when absent/unreadable."""
    try:
        data = json.loads((Path(state_dir) / PULSE_FILE).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_pulse(account: str, state_dir: Path, now: Optional[float] = None) -> Dict[str, Any]:
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


def write_pulse(state_dir: Path, pulse: Dict[str, Any]) -> bool:
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
