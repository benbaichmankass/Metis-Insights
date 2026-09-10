"""Durable, append-only record of every EXIT-EVALUATION INTERVAL.

Mirrors the canonical soak family (``exposure_soak.py`` / ``pairs_soak.py`` /
``allocator_soak.py``): a pure builder, a best-effort JSONL writer under
``runtime_logs_dir()``, and a pure reader envelope.

**Why this exists — the in-memory max was systematically optimistic.**

``exit_loop_health`` tracks ``max_interval_ms`` and grades it against the
operator's 60 s requirement. Both live in module globals that start at
``None``/``0`` and are NEVER reloaded — the state file is a snapshot the next
process overwrites, not an accumulator it resumes. So the max is scoped to one
process, and the live trader restarts on **every merge to `main`** via
``ict-git-sync``.

Measured 2026-08-16: **five OBSERVED processes in ~10 h** (23:06:29 → 06:24:32 →
07:34:25 → 08:34:15 → 09:07:09), two of them caused by the very session that
shipped the field. Across those five, the only reading that ever approached the
requirement came from the one process that ran through a **quiet overnight
window** and reached n=694 (58940.8 ms, 98.2% of the requirement). Every daytime
process reached n=4–38 and topped out at 31–79%.

(Five, not six: a sixth was inferred from a merge and the data REFUTES it. A
merge to `main` does not promptly restart the trader — the deploy rides the
`ict-git-sync` timer — so restarts must be counted from observed
`process_started_utc` values, never from merge times. The refuting read is
`bot_uptime_s: 3895` at 10:11Z, which dates that process to 09:06:38 and so
makes the 09:07 and the inferred ~09:55 the SAME process. The correction is kept
here because inferring a restart from a merge is the exact mistake this module's
own reasoning is vulnerable to.)

That is not merely a gap in coverage — **it is a bias, and it points the wrong
way.** A maximum over a short window is systematically LOWER than the true
maximum, so the busier the day, the shorter each process lives, and the more
reassuring the number looks. The measurement was most optimistic exactly when
the system was under the most change. `exit_loop_health`'s `requirement_state`
grades a PROCESS across `within` / `breached` / `not_measured` / `unknown`; on an
active day it reports `within` for the trivial reason that no process lived long
enough to draw the tail. This file is the per-INTERVAL record underneath it, and
carries its own boolean (`over_requirement`) rather than reusing that vocabulary —
one interval is not a process grade, and naming them alike invited exactly that
conflation.

**So the interval is recorded here per pass, durably, and the max becomes a
property of the DATA rather than of a process's lifetime.**

**One row per completed pass, deliberately** — not per cadence window. The
interval IS the observation; there is no smaller unit to sample and no reason to
pre-aggregate. Per-pass costs ~2.9k rows/day at the 30 s cadence (comparable to
the other soaks in this family) and buys three things a windowed roll-up cannot:
a restart loses **nothing** rather than the current partial window; any
statistic is recoverable later (p95, the full distribution, a per-hour cut), not
just the max someone thought to precompute; and there is no window bookkeeping
to get wrong. Volume is the honest cost and is stated rather than hidden.

**No enable gate, and no cadence knob.** The cadence is the exit loop's own —
this writes when a pass completes, so a knob here could only *thin* the record,
which would reintroduce exactly the sampling bias the module exists to remove.
A required observability capability must not sit behind a default-off flag
(Prime Directive), and here it must not sit behind a sampling flag either.

**Observe-only.** Nothing reads this back to make a trading decision. It cannot
refuse a trade, opens no socket, and touches no order path — it appends one line
after a pass that has already finished.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SOAK_LOG_NAME = "exit_interval_soak.jsonl"


def build_exit_interval_record(
    *,
    interval_ms: Optional[float],
    pass_ms: Optional[float],
    requirement_s: float,
    process_started_utc: Optional[str],
    passes: Optional[int] = None,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    """Pure builder — a JSON-able dict, or ``None`` on bad input. Never raises.

    ``interval_ms`` is ``None`` for the FIRST pass of a process, and that row is
    still written. The distinction is load-bearing and is carried rather than
    collapsed: a first pass closes no interval (there is no prior completion to
    measure from), which is a different fact from an interval of zero. Writing
    the row anyway is what lets a reader see the process boundary in the data —
    without it, two adjacent processes would look like one continuous series and
    the gap across a restart would be silently attributed to a real interval.

    ``over_requirement`` is computed here rather than left to the reader so the row
    carries the verdict against the requirement **as it stood at the time**. The
    requirement is env-configurable, so a later reader recomputing it against
    today's value would silently re-grade history.
    """
    try:
        rec: Dict[str, Any] = {
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            "process_started_utc": process_started_utc,
            "interval_ms": (round(float(interval_ms), 1)
                            if interval_ms is not None else None),
            "pass_ms": (round(float(pass_ms), 1)
                        if pass_ms is not None else None),
            "requirement_s": float(requirement_s),
            # None (no interval yet) is NOT a breach. Kept explicitly tri-state
            # rather than falsy so a reader cannot count first-passes as passes.
            "over_requirement": (None if interval_ms is None
                         else bool(float(interval_ms) > float(requirement_s) * 1000.0)),
            "first_pass_of_process": interval_ms is None,
        }
        if passes is not None:
            rec["passes_this_process"] = int(passes)
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        return rec
    except (TypeError, ValueError):
        return None
    except Exception:  # noqa: BLE001
        return None


def soak_log_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / SOAK_LOG_NAME


def record_exit_interval(record: Optional[Dict[str, Any]]) -> bool:
    """Best-effort append of one JSON line. Swallows all I/O errors.

    Called from the exit loop after a pass has already completed, so a failure
    here can only lose an observation — never delay or affect an exit.
    """
    if not record:
        return False
    try:
        path = soak_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except OSError:
        return False


def read_tail_records(limit: int = 50) -> List[Dict[str, Any]]:
    """The last ``limit`` rows, in FILE (chronological) order. Never raises.

    ``read_soak_records`` deliberately reads the WHOLE file, because a
    cross-process max truncated to the newest N would reintroduce in the reader
    exactly the per-process sampling bias this module exists to remove. That is
    right for a read surface and wrong for the live trader: the log is measured
    at ~2.9k rows/day and was 6.29 MB on 2026-08-25, and the restart-gap grade
    runs inside the process it is grading.

    So this is the BOUNDED read, and it is bounded for a reason that also makes
    it sufficient: a restart gap joins the LAST row of one process to the FIRST
    row of the next, and those two rows are ADJACENT in an append-only file. A
    handful of rows is enough to see the boundary; the whole file is not needed
    and its cost is not affordable on a tick.

    ⚠️ IT IS A TAIL, SO ITS POPULATION IS THE TAIL. A caller must not report a
    grade computed here as a statement about the log's whole history — the
    honest scope is "the most recent process boundary", and
    ``grade_restart_gaps`` carries ``population`` saying which it got.

    Reads the last ~256 KiB rather than the whole file, then keeps the final
    ``limit`` complete lines. The first line of that window is usually torn and
    is dropped — a torn line is skipped by the parser anyway, and dropping it
    keeps the count honest rather than silently short.
    """
    try:
        limit = max(int(limit), 1)
        path = soak_log_path()
        if not path.exists():
            return []
        size = path.stat().st_size
        window = min(size, 262_144)
        with path.open("rb") as fh:
            fh.seek(size - window)
            blob = fh.read(window)
        text = blob.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if window < size and lines:
            lines = lines[1:]          # the leading fragment, cut mid-line
        out: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue               # a torn line is skipped, never fails the read
            if isinstance(obj, dict):
                out.append(obj)
        return out
    except OSError:
        return []
    except Exception:  # noqa: BLE001
        return []


def read_soak_records(
    *,
    limit: int = 200,
    breached_only: bool = False,
) -> Dict[str, Any]:
    """Newest-first envelope ``{present, log_path, count, records, summary}``.

    ``summary`` is the point of the whole file: the **cross-process** max, which
    no per-process surface can report. Every figure ships beside its denominator
    (``intervals_measured``, ``processes_seen``) for the same reason
    ``exposure_soak`` ships ``max_multiple`` beside ``measured_n`` — a max over
    an unstated sample is not a claim.
    """
    out: Dict[str, Any] = {
        "present": False, "log_path": None, "count": 0,
        "records": [], "summary": {},
    }
    try:
        path = soak_log_path()
        out["log_path"] = str(path)
        if not path.exists():
            return out
        out["present"] = True
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # a torn line is skipped, never fails the read
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return out
    except Exception:  # noqa: BLE001
        return out

    try:
        # Summary is computed over EVERY row on disk, not over the returned
        # page — a cross-process max truncated to the newest N would be exactly
        # the per-process bias this file exists to remove, reintroduced in the
        # reader.
        measured = [r for r in rows
                    if isinstance(r.get("interval_ms"), (int, float))]
        procs = {r.get("process_started_utc") for r in rows
                 if r.get("process_started_utc")}
        breaches = [r for r in measured if r.get("over_requirement") is True]
        summary: Dict[str, Any] = {
            "rows": len(rows),
            "intervals_measured": len(measured),
            "processes_seen": len(procs),
            "breaches": len(breaches),
            "max_interval_ms": (max(r["interval_ms"] for r in measured)
                                if measured else None),
            "mean_interval_ms": (round(
                sum(r["interval_ms"] for r in measured) / len(measured), 1)
                if measured else None),
        }
        if measured:
            peak = max(measured, key=lambda r: r["interval_ms"])
            summary["max_interval_at_utc"] = peak.get("logged_at_utc")
            summary["max_interval_process"] = peak.get("process_started_utc")
        if breaches:
            summary["last_breach_utc"] = breaches[-1].get("logged_at_utc")

        # --- THE RESTART GAP, over the WHOLE log --------------------------------
        #
        # The one interval the requirement covers and every per-process surface
        # excludes by construction: last completed pass of process N to first of
        # N+1. It is computable ONLY here, because a cross-process quantity cannot
        # be computed by a process — which is the same reason this file exists at
        # all, one level up.
        #
        # Computed over every row on disk, like the rest of the summary and for
        # the same reason: a boundary max truncated to the newest page would be
        # the per-process bias reintroduced in the reader. The live trader uses
        # `read_tail_records` instead, because it needs ONE boundary rather than
        # the history and must not read 6 MB on a tick.
        #
        # ⚠️ `processes_seen == 1` here means there is no boundary at all, and the
        # grade is `not_measured` — NOT `within`. Read it beside `gaps_measured`.
        #
        # Imported HERE rather than at module scope: the grader imports
        # `exit_loop_health` for the one definition of the requirement and the
        # band, and this module is imported from that module's write path.
        #
        # ⚠️ NO INNER try/except, DELIBERATELY. `grade_restart_gaps` cannot raise
        # by construction (it owns its own handler and degrades to `unknown`), so
        # the only failure reachable here is an ImportError — i.e. the module is
        # missing from the deploy. That is a DEPLOY DEFECT, and the honest
        # response is the one the enclosing handler already gives: no `summary` at
        # all. Catching it and stamping the grader's could-not-look value here
        # would have re-declared that vocabulary in a file which branches on none
        # of it — the collapse one level up, a state literal with no consumer
        # behind it. (The value is deliberately not spelled out in this comment:
        # `collapsed-state-guard` credits state literals found in COMMENTS as
        # consumer evidence, so writing it would make this file a false consumer
        # of two contracts it does not read. That is the KNOWN, OPEN
        # BL-20260817-COLLAPSED-STATE-GUARD-READS-PROSE, whose own text predicts
        # it "leaves the next author to rediscover it"; this comment is that
        # prediction coming true on 2026-09-09, and is left here so the next
        # author does not pay for it a fourth time.)
        from src.runtime.exit_restart_gap import grade_restart_gaps
        summary["restart_gap"] = grade_restart_gaps(
            rows, population="every row on disk")
        out["summary"] = summary

        page = [r for r in rows if r.get("over_requirement") is True] if breached_only else rows
        page = list(reversed(page))[:max(int(limit), 1)]
        out["records"] = page
        out["count"] = len(page)
    except Exception:  # noqa: BLE001
        return out
    return out
