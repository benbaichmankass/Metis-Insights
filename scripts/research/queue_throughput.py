#!/usr/bin/env python3
# wiring: scripts/ci/check_research_queue_throughput.py (schedule-keeper alarm) + scripts/ops/render_daily_brief.py §4
"""Research-queue THROUGHPUT — what the queue fired and landed, read from the checkout.

WHY (operator, 2026-09-30, lane RESEARCH-W7): "make sure those tests are running" — the queue not
running is a RECURRING failure. The existing idle alarm (`check_research_queue_health.py` H2) lives
as the LAST STEP of the dispatch job it watches, so a dispatcher whose runs are dropped, skipped or
red before that step raises nothing. This module is the read side; the alarm that uses it runs from
schedule-keeper (push-to-main + cron), outside the thing it watches.

Population: every research/queue/**/*.yaml (stamps) and every research/results/**/*.jsonl row
(`generated_at`), read from the checkout. A stamp is written when a unit FIRES, then lands on main via
a stamp PR ~40-60 min later, so "fired" lags reality by that landing time — the alarm threshold is
chosen with that lag in it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from scripts.research.queue_grade import _parse_ts, health
from scripts.research.queue_replenish import existing_units

#: Alarm when runnable units exist and nothing has FIRED for this long. 12 h was set when the cron
#: was 6-hourly: cron 6 h + GitHub's measured 4-6 h scheduler lag (schedule_keeper.py docstring) is
#: absorbed by the keeper's own dispatch, leaving one cycle (~6 h) + the ~1 h stamp-landing lag; 12 h
#: is that plus one missed cycle. It equals the operator's 2026-09-28 "no unit has run in 12h".
#: The cron went HOURLY on 2026-10-06 (operator: the git infrastructure keeps the queue moving) and
#: this is deliberately KEPT at 12 h: GitHub drops hourly slots outright (schedule_keeper.py, measured
#: 2026-09-20..27), the keeper's own floor is hourly, and a run's stamp still lands through a
#: ~1 h commit-to-main wait, so a tighter alarm would page on scheduler weather rather than on a
#: dispatcher that has actually stopped.
DEFAULT_IDLE_HOURS = 12.0


def throughput(root: Path, *, now: Optional[datetime] = None,
               idle_hours: float = DEFAULT_IDLE_HOURS) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    h = health(root, now=now)
    units, _bad = existing_units(root)
    stamps = [t for u in units.values() if (t := _parse_ts(u.get("last_dispatched_at")))]
    since = now - timedelta(hours=24)
    landed, unreadable = 0, 0
    for f in (root / "research" / "results").glob("*/*.jsonl"):
        try:
            for ln in f.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                t = _parse_ts(json.loads(ln).get("generated_at"))
                landed += bool(t and t >= since)
        except (OSError, ValueError):
            unreadable += 1
    hrs = h["hours_since_last_dispatch"]
    return {"as_of": now.isoformat(), "runnable": h["runnable"], "queued": h["queued"], "units": h["units"],
            "fired_24h": sum(1 for t in stamps if t >= since),
            "fired_7d": sum(1 for t in stamps if t >= now - timedelta(days=7)),
            "results_landed_24h": landed, "result_files_unreadable": unreadable,
            "last_dispatched_at": h["last_dispatched_at"], "hours_since_last_dispatch": hrs,
            "idle_threshold_hours": idle_hours,
            "idle_alarm": bool(h["runnable"] > 0 and (hrs is None or hrs > idle_hours))}


def summary_line(t: Dict[str, Any]) -> str:
    return (f"research queue (24h): fired {t['fired_24h']} · results landed {t['results_landed_24h']} · "
            f"runnable now {t['runnable']} of {t['queued']} queued · last fire "
            f"{t['hours_since_last_dispatch']} h ago" + (" · ⚠️ IDLE ALARM" if t["idle_alarm"] else ""))
