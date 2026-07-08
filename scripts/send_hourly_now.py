"""Send an hourly report immediately, bypassing the dedup marker.

Usage:
    PYTHONPATH=. python scripts/send_hourly_now.py

Prints the rendered report to stdout and dispatches it through
``src.runtime.outcomes.send_scheduled`` (the same path the in-process
scheduler in `src/main.py` uses). Useful as:

* a post-merge demo for BUG-032 (the operator confirms the pipe is
  hot end-to-end);
* a recovery hook when `summary_markers.json` is corrupt and the
  scheduled path is silently a no-op;
* the ExecStart for ``ict-hourly-snapshot.service`` (M1 P1-C —
  fires once an hour with a 60 s randomized delay).

Concurrency
-----------

The script grabs an ``fcntl.flock`` exclusive lock on
``/tmp/ict-hourly-snapshot.lock`` (override via
``ICT_HOURLY_LOCK_PATH``) before calling ``send_scheduled``. A second
caller running while the first still holds the lock exits non-zero
with code 75 (EX_TEMPFAIL) and a log line — preventing two timer
firings (or a timer + manual ``/hourly``) from racing.
"""
from __future__ import annotations

import fcntl
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOCK_PATH = "/tmp/ict-hourly-snapshot.lock"
LOCK_BUSY_EXIT_CODE = 75  # POSIX EX_TEMPFAIL — caller may retry later

logger = logging.getLogger(__name__)


def _resolve_lock_path() -> Path:
    return Path(os.environ.get("ICT_HOURLY_LOCK_PATH", DEFAULT_LOCK_PATH))


def _acquire_lock(lock_path: Path):
    """Open ``lock_path`` and grab an exclusive non-blocking flock.

    Returns the file handle so the caller can hold the lock for the
    duration of the dispatch. Closing the handle (or process exit)
    releases the lock automatically.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise
    return fh


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    lock_path = _resolve_lock_path()

    try:
        lock_fh = _acquire_lock(lock_path)
    except BlockingIOError:
        logger.warning(
            "send_hourly_now: another instance holds %s; skipping.",
            lock_path,
        )
        return LOCK_BUSY_EXIT_CODE

    try:
        # Lazy imports so the lock acquisition (and its failure mode)
        # surfaces a useful error before we touch the runtime modules.
        from src.runtime.hourly_report import build_combined_hourly_report
        from src.runtime.notify import send_telegram_direct
        from src.runtime.outcomes import send_scheduled

        now = datetime.now(timezone.utc)
        # This is the SINGLE hourly producer (the duplicate in-loop path in
        # src/main.py was removed so the operator gets exactly one dispatch
        # per hour — see TELEGRAM-SPEC.md § 4.1).
        #
        # Notification streamlining (2026-07-08): the operator wants ONE hourly
        # message. The earlier attempt rendered the strategy + accounts halves
        # SEPARATELY and concatenated them, but each half was already capped at
        # Telegram's 4096-char limit, so the concatenation ran up to ~8192 and
        # the "send as one" guard almost never held — it fell back to two
        # messages every hour. ``build_combined_hourly_report`` renders both
        # halves through a SINGLE ``render_html`` call, so the shared truncation
        # guarantees the whole snapshot is exactly one Telegram message.
        combined = build_combined_hourly_report(now_utc=now, tick_interval_s=900)
        print(f"--- combined hourly snapshot ({len(combined)} chars) ---")
        print(combined)
        try:
            send_telegram_direct(combined, parse_mode="HTML")
            print("dispatched (combined hourly snapshot).")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hourly combined HTML send failed (%s); falling back to scheduled",
                exc,
            )
            send_scheduled(combined)
            print("dispatched (combined, scheduled fallback).")

        # Liveness watchdog piggybacks on the hourly cycle (moved here from
        # the trader loop): pings when actionable signals fired but no
        # trades landed (the BUG-034 gap). Best-effort; never raises.
        try:
            from src.runtime.liveness_watchdog import run_liveness_watchdog
            run_liveness_watchdog(now_utc=now)
        except Exception:  # noqa: BLE001
            logger.exception("liveness_watchdog dispatch failed")
        return 0
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
