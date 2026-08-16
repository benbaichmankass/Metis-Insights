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

Measured 2026-08-16: **six processes in ~10 h** (23:06 → 06:24 → 07:34 → 08:34 →
09:07 → 09:55), two of them caused by the very session that shipped the field.
Across those six, the only reading that ever approached the requirement came
from the one process that ran through a **quiet overnight window** and reached
n=694 (58940.8 ms, 98.2% of the requirement). Every daytime process reached
n=4–38 and topped out at 31–79%.

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
        out["summary"] = summary

        page = [r for r in rows if r.get("over_requirement") is True] if breached_only else rows
        page = list(reversed(page))[:max(int(limit), 1)]
        out["records"] = page
        out["count"] = len(page)
    except Exception:  # noqa: BLE001
        return out
    return out
