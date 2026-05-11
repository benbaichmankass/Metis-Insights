"""Process heartbeat file — S-022 PR5.

Each successful pipeline tick writes ``runtime_logs/heartbeat.txt`` with
a one-line timestamp + counter. The mtime is the canonical "is the
trader alive?" signal:

  * Used by ``src/runtime/health.py::check_tick_freshness`` (which used
    to fall back to ``signal_audit.jsonl`` mtime — that file is also
    touched by ad-hoc scripts and was a noisy proxy).
  * Used by ``scripts/check_heartbeat.py`` — a standalone, stdlib-only
    watchdog that the VM's systemd timer can run every 5 minutes between
    hourly reports.
  * Used by ``src/web/api/routers/{diag,dashboard}`` to label the trader
    as running / paused / stopped via :func:`heartbeat_label`.

Designed to be tiny and never-raising. A heartbeat write that fails
only logs a warning — the tick loop keeps going.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.utils.paths import runtime_logs_dir


logger = logging.getLogger(__name__)


HEARTBEAT_FILE = runtime_logs_dir() / "heartbeat.txt"


def write_heartbeat(
    *,
    status: str = "ok",
    tick: Optional[int] = None,
    path: Optional[Path] = None,
) -> bool:
    """Write the heartbeat atomically. Returns True on success.

    The on-disk format is intentionally human-readable so you can
    ``cat runtime_logs/heartbeat.txt`` on the VM:

        2026-05-01T14:00:03+00:00  ok  tick=4218

    Atomic via tempfile + rename so a partial write never leaves an
    empty/corrupt file. Falls back to a plain write if the rename fails
    (e.g. a Windows filesystem) — at worst the next tick overwrites it.
    """
    target = path or HEARTBEAT_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"{ts}  {status}  tick={tick if tick is not None else '-'}"

        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".heartbeat.", suffix=".tmp", dir=str(target.parent),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(line + "\n")
            os.replace(tmp_name, target)
        except OSError:
            # Filesystem doesn't support rename; just write directly.
            target.write_text(line + "\n", encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_heartbeat failed: %s", exc)
        return False


# ── Liveness label ──────────────────────────────────────────────────────────
#
# Two consumers translate a heartbeat age into a label: the dashboard
# (`/api/bot/stats`) and the diag surface (`/api/diag/status` +
# `/api/diag/snapshot`). The basis used to be the *tick* interval
# (heartbeat written once per tick), which meant labelling an entire
# tick cycle's worth of normal idleness as "paused" near the end of
# each cycle. After 2026-05-08 the heartbeat is refreshed *between
# ticks* every ``HEARTBEAT_INTERVAL_SECONDS`` (default 60 s, see
# ``src/main.py``), so the right basis for the label is that cadence,
# not the tick:
#
#   age < cadence × 3   → "running"  (≤ 2 missed beats — well within
#                                     normal jitter, even mid-tick)
#   age < cadence × 10  → "paused"   (~10 missed beats — the process
#                                     is unresponsive but may recover)
#   age ≥ cadence × 10  → "stopped"  (alarm-worthy)
#
# Both bounds are env-driven so changing HEARTBEAT_INTERVAL_SECONDS
# keeps all three thresholds in sync. ``scripts/check_heartbeat.py``
# still uses ``TICK_INTERVAL_SECONDS × HEARTBEAT_GRACE_FACTOR`` for
# its alarm threshold; that's a separate watchdog and is left at
# its current convention pending a follow-up alignment.

_DEFAULT_HEARTBEAT_INTERVAL_SEC = 60
_RUNNING_FACTOR = 3
_PAUSED_FACTOR = 10


def _heartbeat_interval_seconds() -> int:
    raw = os.environ.get(
        "HEARTBEAT_INTERVAL_SECONDS", str(_DEFAULT_HEARTBEAT_INTERVAL_SEC)
    )
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_HEARTBEAT_INTERVAL_SEC
    return value if value > 0 else _DEFAULT_HEARTBEAT_INTERVAL_SEC


def heartbeat_thresholds() -> tuple[int, int]:
    """Return ``(running_max_sec, paused_max_sec)`` derived from env.

    Caller-friendly tuple for routers that want to label the heartbeat
    age. See module docstring for the convention.
    """
    cadence = _heartbeat_interval_seconds()
    return cadence * _RUNNING_FACTOR, cadence * _PAUSED_FACTOR


def heartbeat_label(age_seconds: float) -> str:
    """Map an age (seconds since heartbeat mtime) to a status label.

    Returns ``"running" | "paused" | "stopped"`` per the env-derived
    thresholds. Callers that already have an "is the file present"
    answer should branch on that first; this helper assumes a present
    heartbeat.
    """
    running_max, paused_max = heartbeat_thresholds()
    if age_seconds < running_max:
        return "running"
    if age_seconds < paused_max:
        return "paused"
    return "stopped"
