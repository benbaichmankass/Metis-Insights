#!/usr/bin/env python3
"""research-queue-health — the queue must hold enough runnable units and must
have dispatched something recently; otherwise PAGE.

WHY THIS EXISTS (operator, 2026-09-28, relayed by the manager): "keep
replenishing it" plus "ALARMS: if runnable units < 25 or no unit has run in
12h, send a Telegram ping". Silence is the failure this class always has:
a queue that ran dry, or a dispatcher that stopped firing, looks exactly like
a quiet, healthy one until someone looks.

WHAT IT CHECKS (population: every research/queue/**/*.yaml, read from the
checkout; never inferred from a receipt alone):
  H1  runnable units (status queued + a real *.yml run.workflow) >= --min-runnable
  H2  the newest last_dispatched_at across ALL units is younger than --max-idle-hours

Exit 1 on either, printing the numbers, so the caller (research-queue-dispatch.yml's
alarm step) can dispatch the existing send-ping system-action. `--self-test`
plants both failures.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.research.queue_grade import health  # noqa: E402

DEFAULT_MIN_RUNNABLE = 25
DEFAULT_MAX_IDLE_HOURS = 12.0


def grade(h: dict, *, min_runnable: int, max_idle_hours: float) -> list:
    problems = []
    if h["runnable"] < min_runnable:
        problems.append(f"H1 runnable units {h['runnable']} < {min_runnable} (of {h['units']} unit(s))")
    if h["hours_since_last_dispatch"] is None:
        problems.append("H2 no unit carries a last_dispatched_at at all -- nothing has ever run")
    elif h["hours_since_last_dispatch"] > max_idle_hours:
        problems.append(f"H2 last dispatch {h['hours_since_last_dispatch']} h ago > {max_idle_hours} h "
                        f"(newest stamp {h['last_dispatched_at']})")
    if h["unreadable"]:
        problems.append(f"H0 unreadable unit(s): {h['unreadable']}")
    return problems


def _self_test() -> int:
    import tempfile
    now = datetime(2030, 1, 10, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / "research" / "queue").mkdir(parents=True)
        for i in range(3):
            (r / "research" / "queue" / f"RQ-20300101-{i+1:03d}.yaml").write_text(
                f"id: RQ-20300101-{i+1:03d}\nstatus: queued\nrun:\n  workflow: x.yml\n"
                f"last_dispatched_at: '{(now - timedelta(hours=30)).isoformat()}'\n")
        h = health(r, now=now)
        p = grade(h, min_runnable=25, max_idle_hours=12)
        assert any(x.startswith("H1") for x in p) and any(x.startswith("H2") for x in p), p
        assert grade(h, min_runnable=3, max_idle_hours=48) == []
        (r / "research" / "queue" / "RQ-20300101-004.yaml").write_text("id: RQ-20300101-004\nstatus: queued\nrun:\n  workflow: 'none — session-local'\n")
        assert health(r, now=now)["runnable"] == 3, "a session-bound note is not runnable"
        (r / "research" / "queue" / "RQ-20300101-005.yaml").write_text(": : [\n")
        assert any(x.startswith("H0") for x in grade(health(r, now=now), min_runnable=1, max_idle_hours=48))
    print("research-queue-health self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--min-runnable", type=int, default=DEFAULT_MIN_RUNNABLE)
    ap.add_argument("--max-idle-hours", type=float, default=DEFAULT_MAX_IDLE_HOURS)
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    h = health(_REPO)
    problems = grade(h, min_runnable=args.min_runnable, max_idle_hours=args.max_idle_hours)
    print(f"research-queue-health: runnable={h['runnable']} of {h['units']} unit(s); last dispatch "
          f"{h['last_dispatched_at']} ({h['hours_since_last_dispatch']} h ago); needs_review={len(h['needs_review'])}")
    for p in problems:
        print(f"::error::research-queue-health: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
