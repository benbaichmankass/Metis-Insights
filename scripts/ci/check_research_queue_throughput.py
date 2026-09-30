#!/usr/bin/env python3
# wiring: .github/workflows/schedule-keeper.yml (every keeper run: push to main + cron)
"""Fail loudly when runnable research units exist but nothing has fired for N hours.

Runs from schedule-keeper, NOT from research-queue-dispatch: an alarm housed in the job it watches
is silent exactly when that job does not run (or dies before its last step) — the recurring failure
this exists for. Exit 1 = idle alarm; prints the throughput line and JSON either way.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.research.queue_throughput import DEFAULT_IDLE_HOURS, summary_line, throughput  # noqa: E402


def _self_test() -> int:
    now = datetime(2030, 1, 10, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / "research/queue").mkdir(parents=True)
        (r / "research/results/RQ-20300101-001").mkdir(parents=True)
        def unit(i, stamp):
            (r / f"research/queue/RQ-20300101-{i:03d}.yaml").write_text(
                f"id: RQ-20300101-{i:03d}\nstatus: queued\ncadence: once\nrun:\n  workflow: x.yml\n"
                f"last_dispatched_at: {stamp}\n")
        unit(1, "null")
        # PLANTED: runnable work and NO fire ever -> alarm
        assert throughput(r, now=now)["idle_alarm"] is True
        unit(2, f"'{(now - timedelta(hours=30)).isoformat()}'")
        t = throughput(r, now=now)
        assert t["idle_alarm"] and t["fired_24h"] == 0 and t["fired_7d"] == 1, t
        # POSITIVE CONTROL: a fire 3 h ago clears it; a landed result is counted
        unit(2, f"'{(now - timedelta(hours=3)).isoformat()}'")
        (r / "research/results/RQ-20300101-001/1.jsonl").write_text(
            json.dumps({"generated_at": (now - timedelta(hours=1)).isoformat()}) + "\n")
        t = throughput(r, now=now)
        assert not t["idle_alarm"] and t["fired_24h"] == 1 and t["results_landed_24h"] == 1, t
        # NO RUNNABLE WORK is not an alarm (nothing to fire)
        (r / "research/queue/RQ-20300101-001.yaml").unlink()
        unit(2, f"'{(now - timedelta(hours=30)).isoformat()}'")
        assert throughput(r, now=now)["idle_alarm"] is False
    print("research-queue-throughput self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--idle-hours", type=float, default=DEFAULT_IDLE_HOURS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    t = throughput(_REPO, idle_hours=a.idle_hours)
    print(json.dumps(t) if a.json else summary_line(t))
    if t["idle_alarm"]:
        print(f"::error::research-queue-throughput: {t['runnable']} runnable unit(s) but nothing fired for "
              f"{t['hours_since_last_dispatch']} h (threshold {a.idle_hours} h). The dispatcher is not running "
              "or not firing: read the last research-queue-dispatch runs.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
