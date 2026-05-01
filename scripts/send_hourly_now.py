"""Send an hourly report immediately, bypassing the dedup marker.

Usage:
    PYTHONPATH=. python scripts/send_hourly_now.py

Prints the rendered report to stdout and dispatches it through
``src.runtime.outcomes.send_scheduled`` (the same path the in-process
scheduler in `src/main.py` uses). Useful as:

* a post-merge demo for BUG-032 (the operator confirms the pipe is
  hot end-to-end);
* a recovery hook when `summary_markers.json` is corrupt and the
  scheduled path is silently a no-op.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone


def main() -> int:
    # Imported lazily so a syntax/import error in the runtime modules
    # surfaces with a useful traceback instead of crashing at module load.
    from src.runtime.hourly_report import build_hourly_report
    from src.runtime.outcomes import send_scheduled

    now = datetime.now(timezone.utc)
    msg = build_hourly_report(now_utc=now, tick_interval_s=900)
    print(msg)
    print("---")
    print(f"dispatching ({len(msg)} chars) ...")
    send_scheduled(msg)
    print("dispatched. If Telegram is unreachable, the message was queued "
          "to runtime_logs/pending_pings.jsonl for the VM-side drainer.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
