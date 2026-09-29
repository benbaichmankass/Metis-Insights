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
  H1  runnable units >= --min-runnable, where RUNNABLE means the dispatcher would
      fire it within the next cycle (status queued + a real *.yml run.workflow +
      due by the dispatcher's own rule: never run, or cadence elapsed). A `once`
      unit that already ran, or a monthly unit that ran yesterday, is queued but
      NOT runnable -- counting those (as this did until 2026-09-29) read 21
      runnable while 3 would fire, and the alarm stayed quiet on idle runners.
  H2  the newest last_dispatched_at across ALL units is younger than --max-idle-hours

Exit 1 on a problem a refill cannot fix (an unfillable H1, or H2), so the caller
(research-queue-dispatch.yml's alarm step) pages; exit 3 (EXIT_REFILL) when the
ONLY problem is an H1 the committed templates can fill, so the caller fires
research-queue-replenish.yml instead of paging. `--self-test` plants all three.
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
#: Exit code when the ONLY problem is an H1 the templates can fill: the caller
#: fires research-queue-replenish.yml instead of paging. Measured 2026-09-29
#: 10:07Z (manager, via lane M19-S1): runnable 8 of 58 on main with 153
#: unclaimed template points -- starved, but self-healing, and the refill is
#: daily while the dispatcher consumes up to 3 units every 6 h, so a bare
#: page would have rung after most cycles. A page is for what a refill
#: cannot fix: an unfillable gap, or a dispatcher that stopped firing.
EXIT_REFILL = 3


def refill_capacity(root: Path, *, target: int) -> dict:
    """{deficit, capacity, fillable}: what a replenish at `target` could add
    from the committed templates right now (population: every unclaimed
    template point at HEAD's rosters and evidence)."""
    from scripts.research.queue_replenish import plan
    pl = plan(root, day="2030-01-01", target=target)
    capacity = sum(int(v) for v in pl["candidates"].values())
    deficit = max(0, int(pl["deficit"]))
    return {"deficit": deficit, "capacity": capacity, "fillable": capacity >= deficit}


def grade(h: dict, *, min_runnable: int, max_idle_hours: float, refill: dict = None) -> list:
    """Problem strings. An H1 the templates can fill is tagged `H1-REFILL` so the
    caller refills instead of paging (`refill` = refill_capacity(); None = not
    measured, graded as a plain H1 page)."""
    problems = []
    if h["runnable"] < min_runnable:
        base = (f"runnable units {h['runnable']} < {min_runnable} "
                f"(would fire within the next dispatch cycle; {h.get('queued', '?')} read queued, "
                f"of {h['units']} unit(s))")
        if refill and refill.get("fillable"):
            problems.append(f"H1-REFILL {base}; the templates hold {refill['capacity']} unclaimed point(s) "
                            f"for a deficit of {refill['deficit']} -- fire research-queue-replenish.yml, do not page")
        else:
            cap = (f"; the templates hold only {refill['capacity']} point(s) for a deficit of {refill['deficit']}"
                   if refill else "; refill capacity not measured")
            problems.append(f"H1 {base}{cap} -- a refill cannot close this gap")
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
        for i in range(3):   # daily units that ran 30 h ago: elapsed, so they fire next cycle
            (r / "research" / "queue" / f"RQ-20300101-{i+1:03d}.yaml").write_text(
                f"id: RQ-20300101-{i+1:03d}\nstatus: queued\ncadence: daily\nrun:\n  workflow: x.yml\n"
                f"last_dispatched_at: '{(now - timedelta(hours=30)).isoformat()}'\n")
        # PLANTED (manager, 2026-09-29): queued but NOT runnable -- a once-unit that already
        # ran and a monthly unit that ran yesterday. Both counted as runnable before.
        (r / "research" / "queue" / "RQ-20300101-010.yaml").write_text(
            f"id: RQ-20300101-010\nstatus: queued\ncadence: once\nrun:\n  workflow: x.yml\n"
            f"last_dispatched_at: '{(now - timedelta(hours=30)).isoformat()}'\n")
        (r / "research" / "queue" / "RQ-20300101-011.yaml").write_text(
            f"id: RQ-20300101-011\nstatus: queued\ncadence: monthly\nrun:\n  workflow: x.yml\n"
            f"last_dispatched_at: '{(now - timedelta(days=1)).isoformat()}'\n")
        h = health(r, now=now)
        assert h["queued"] == 5 and h["runnable"] == 3, h
        p = grade(h, min_runnable=25, max_idle_hours=12)
        assert any(x.startswith("H1 ") for x in p) and any(x.startswith("H2") for x in p), p
        # PLANTED (manager 2026-09-29 10:07Z): a fillable H1 is a REFILL (exit 3), not a
        # page; an unfillable one pages; an H2 beside a fillable H1 still pages.
        p_fill = grade(h, min_runnable=25, max_idle_hours=48, refill={"deficit": 22, "capacity": 153, "fillable": True})
        assert len(p_fill) == 1 and p_fill[0].startswith("H1-REFILL"), p_fill
        assert _exit_code(p_fill) == EXIT_REFILL
        p_dry = grade(h, min_runnable=25, max_idle_hours=48, refill={"deficit": 22, "capacity": 3, "fillable": False})
        assert len(p_dry) == 1 and p_dry[0].startswith("H1 ") and "cannot close" in p_dry[0], p_dry
        assert _exit_code(p_dry) == 1 and _exit_code(p_fill + ["H2 idle"]) == 1 and _exit_code([]) == 0
        assert grade(h, min_runnable=3, max_idle_hours=48) == []
        (r / "research" / "queue" / "RQ-20300101-004.yaml").write_text("id: RQ-20300101-004\nstatus: queued\nrun:\n  workflow: 'none — session-local'\n")
        assert health(r, now=now)["runnable"] == 3, "a session-bound note is not runnable"
        (r / "research" / "queue" / "RQ-20300101-005.yaml").write_text(": : [\n")
        assert any(x.startswith("H0") for x in grade(health(r, now=now), min_runnable=1, max_idle_hours=48))
    print("research-queue-health self-test OK")
    return 0


def _exit_code(problems: list) -> int:
    """0 clean; EXIT_REFILL when every problem is a fillable H1; else 1 (page)."""
    if not problems:
        return 0
    return EXIT_REFILL if all(x.startswith("H1-REFILL") for x in problems) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--min-runnable", type=int, default=DEFAULT_MIN_RUNNABLE)
    ap.add_argument("--max-idle-hours", type=float, default=DEFAULT_MAX_IDLE_HOURS)
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    h = health(_REPO)
    refill = None
    if h["runnable"] < args.min_runnable:
        try:
            refill = refill_capacity(_REPO, target=args.min_runnable)
        except Exception as e:  # noqa: BLE001 -- an unreadable template store must PAGE, never read as fillable
            print(f"::warning::research-queue-health: could not measure refill capacity ({e!r}); grading H1 as a page")
    problems = grade(h, min_runnable=args.min_runnable, max_idle_hours=args.max_idle_hours, refill=refill)
    print(f"research-queue-health: runnable={h['runnable']} of {h['units']} unit(s); last dispatch "
          f"{h['last_dispatched_at']} ({h['hours_since_last_dispatch']} h ago); needs_review={len(h['needs_review'])}"
          + (f"; refill capacity {refill['capacity']} for deficit {refill['deficit']}" if refill else ""))
    rc = _exit_code(problems)
    for p in problems:
        print(f"::{'notice' if rc == EXIT_REFILL else 'error'}::research-queue-health: {p}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
