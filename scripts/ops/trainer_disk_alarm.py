#!/usr/bin/env python3
"""Push an operator ping when the TRAINER root disk is >= 90% used (FIX-SA-12).

The trainer publishes ``disk`` in ``runtime_logs/trainer_mirror/trainer_status.json``
(publish_trainer_mirror.sh). The only consumer was an SPA banner in absolute GB
(routers/notifications.py); nothing pushed, so 93% used (3.6 GB free, 2026-09-29,
SA-AUD-5-trainer-disk-93pct) was visible only to someone looking.

Three states, never collapsed (RULE: "we did not look" != "the disk is fine"):
  ok       measured, used_pct < THRESHOLD_USED_PCT
  alarm    measured, used_pct >= THRESHOLD_USED_PCT  -> ping (once per 24 h)
  unknown  mirror missing/stale, no disk block, measure failed, or unusable
           figure -> recorded in the state file, NOT pinged: a down trainer is
           owned by the trainer_down banner, and two alarms for one cause is
           the desensitised-alarm pattern.
Always exits 0; the state file is the record. Run hourly by
ict-trainer-disk-alarm.timer on the live VM.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

THRESHOLD_USED_PCT = 90.0
COOLDOWN_S = 24 * 3600
STALE_S = 30 * 60      # publisher runs every ~2 min; 30 min stale == trainer down


def grade(payload, now: float) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "unknown", "no trainer_status.json mirror"
    try:
        age = now - datetime.fromisoformat(str(payload.get("ts")).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return "unknown", f"mirror ts unreadable: {payload.get('ts')!r}"
    if age > STALE_S:
        return "unknown", f"mirror is {int(age)}s old (trainer down or publisher stalled)"
    disk = payload.get("disk")
    if not isinstance(disk, dict):
        return "unknown", "mirror carries no disk block"
    if not disk.get("measured"):
        return "unknown", f"trainer could not measure disk: {disk.get('reason')}"
    pct = disk.get("used_pct")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        return "unknown", f"used_pct unusable: {pct!r}"
    detail = f"{pct}% used, {disk.get('free_gb')} GB free of {disk.get('total_gb')} GB"
    return ("alarm" if pct >= THRESHOLD_USED_PCT else "ok"), detail


def run(payload, state_path: Path, now: float | None = None) -> str:
    now = time.time() if now is None else now
    state, detail = grade(payload, now)
    try:
        prev = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prev = {}
    last_ping = float(prev.get("last_ping_ts") or 0)
    if state == "alarm" and now - last_ping >= COOLDOWN_S:
        from scripts.send_ping import enqueue
        enqueue(
            f"[WARN] trainer disk {detail} (alarm at >= {THRESHOLD_USED_PCT:.0f}% used). "
            f"A full disk fails the daily training cycle and dataset builds. "
            f"Prune list + evidence: docs/audits/system-audit-2026-09-29 FIX-SA-12.",
            priority="high",
        )
        last_ping = now
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"state": state, "detail": detail, "checked_ts": now, "last_ping_ts": last_ping},
        sort_keys=True), encoding="utf-8")
    return state


def main() -> int:
    from src.utils.paths import runtime_logs_dir
    logs = runtime_logs_dir()
    try:
        payload = json.loads((logs / "trainer_mirror" / "trainer_status.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        payload = None
    print(run(payload, logs / "trainer_disk_alarm_state.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
