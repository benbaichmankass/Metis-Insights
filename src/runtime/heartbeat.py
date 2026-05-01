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
