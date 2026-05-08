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


logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[2]
HEARTBEAT_FILE = _REPO_ROOT / "runtime_logs" / "heartbeat.txt"


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
# Two consumers care about translating a heartbeat age into a label:
# the dashboard (`/api/bot/stats`) and the diag surface
# (`/api/diag/status` + `/api/diag/snapshot`). They previously
# hard-coded `< 600s → running, < 1800s → paused, else stopped`. With
# the production tick interval at 900 s (15 min), the 600 s running
# threshold falsely flagged every healthy trader as "paused" for the
# last 5 minutes of every cycle — a third of the time. Convention now
# matches `scripts/check_heartbeat.py`, which uses
# `tick_interval × HEARTBEAT_GRACE_FACTOR` (default 2.0) as the alarm
# threshold:
#
#   age < tick_interval × 1.2  → "running"  (one cycle hasn't elapsed)
#   age < tick_interval × 2.0  → "paused"   (one cycle missed; may recover)
#   age ≥ tick_interval × 2.0  → "stopped"  (alarm-worthy; check_heartbeat
#                                            has likely paged by now)
#
# Both bounds are env-driven so changing TICK_INTERVAL_SECONDS keeps
# all three thresholds (run / pause / stop / alarm) in sync.

_DEFAULT_TICK_INTERVAL_SEC = 900
_RUNNING_FACTOR = 1.2
_PAUSED_FACTOR = 2.0


def _tick_interval_seconds() -> int:
    raw = os.environ.get("TICK_INTERVAL_SECONDS", str(_DEFAULT_TICK_INTERVAL_SEC))
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_TICK_INTERVAL_SEC
    return value if value > 0 else _DEFAULT_TICK_INTERVAL_SEC


def heartbeat_thresholds() -> tuple[int, int]:
    """Return ``(running_max_sec, paused_max_sec)`` derived from env.

    Caller-friendly tuple for routers that want to label the heartbeat
    age. See module docstring for the convention.
    """
    tick = _tick_interval_seconds()
    return int(tick * _RUNNING_FACTOR), int(tick * _PAUSED_FACTOR)


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
