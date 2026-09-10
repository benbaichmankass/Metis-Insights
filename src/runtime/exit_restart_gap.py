"""The RESTART GAP — the one exit-evaluation interval the instrument cannot see.

WHY THIS IS A SEPARATE MODULE AND NOT A FIELD ON ``exit_loop_health``.

M20's promise is that no live trade goes more than ``EXIT_EVAL_MAX_INTERVAL_SECONDS``
(60 s) without re-evaluation. ``exit_loop_health`` grades that promise from
``max_interval_ms`` — and ``max_interval_ms`` is a **module global that starts at
``None`` and is never reloaded**. It is reset by every restart, and the live trader
restarts on **every merge to ``main``** via ``ict-git-sync`` (three processes in
~8.5 h measured 2026-08-16; twelve in ~8.3 h measured 2026-09-09).

So the interval that spans a restart — last completed pass of process N to first
completed pass of process N+1 — is **excluded by construction**. Process N stops
measuring when it dies. Process N+1 starts with empty accumulators and its own
first pass closes no interval at all (``record_pass`` refuses to invent one, which
is correct). Nobody measures the join.

⚠️ **THAT IS PRECISELY THE WINDOW THE PROMISE IS ABOUT.** A deploy is exactly when
the trader is least likely to be evaluating exits: the old process is stopped, a
new one boots, imports, connects and runs a cold first pass with every cache empty
(the ``BL-20260609-001`` cold-start shape). The instrument goes quiet over the one
interval most likely to be long, and then reports ``within``.

Measured 2026-09-09 (audit F-50): the 11 restart gaps that day were **25.3–53.9 s,
0 of 11 over 60 s, 1.3 % of wall clock** — clean, and watched by nothing. This
module is what watches them. It does not claim the gaps are a problem; it claims
that not knowing was.

**IT CAN ONLY LIVE IN THE DURABLE SOAK.** A cross-process quantity cannot be
computed by a process. ``exit_interval_soak.jsonl`` carries ``process_started_utc``
and ``first_pass_of_process`` on every row for exactly this reason — the soak's own
builder says writing the first-pass row is "what lets a reader see the process
boundary in the data". This is the reader that uses it.

THE GRADE — FIVE STATES, NEVER COLLAPSED
----------------------------------------
``restart_gap_state`` deliberately mirrors ``exit_loop_health.requirement_state``'s
vocabulary, because it answers the SAME question about a DIFFERENT interval:

  * ``not_measured`` — fewer than two processes in the population, so **no restart
    gap EXISTS**. Emphatically not ``within``: a log with one process in it has not
    demonstrated compliance, it has demonstrated nothing.
  * ``unknown``      — we could not look. Timestamps unparseable, the read failed,
    or every candidate boundary was ungradeable. Never ``within``.
  * ``breached``     — at least one gap exceeded the requirement.
  * ``near_miss``    — the worst gap reached ``NEAR_MISS_FRACTION`` of it.
  * ``within``       — every MEASURED gap was inside it.

PER-GAP, ``gap_state`` IS ITS OWN THREE-WAY, AND IS DELIBERATELY NOT REGISTERED
------------------------------------------------------------------------------
  * ``measured``    — both endpoints parsed, the successor's earliest row is a
    genuine first pass, and the gap is non-negative.
  * ``overlapping`` — the successor's first completion PRECEDES the predecessor's
    last. Real (the old process can finish a pass during handover), and excluded
    from the max rather than clamped to zero. Coverage was arguably continuous
    there, but saying so would require reasoning about which pass covered what, and
    a number nobody can defend is worse than a named absence.
  * ``ungradeable`` — the successor's earliest row is NOT flagged
    ``first_pass_of_process``, or a timestamp would not parse. We cannot establish
    where the boundary is, so we do not grade it.

``gap_state`` is **not** in ``collapsed-state-guard``'s registry, and that is a
decision rather than an oversight. The guard requires a consumer OUTSIDE the
producer to branch on every declared state; today the only thing that branches on
all three is the aggregation twelve lines below, in this file. Registering it would
buy a decorative branch in another module — which is the outcome CLAUDE.md's
``BYBIT_HEDGE_MODE_SYMBOLS`` row names as the reason a state is left unregistered
until something genuinely reads it. The COUNTS (``overlapping_gaps`` /
``ungradeable_gaps``) ride on the graded result instead, so the absence stays
legible even while the per-gap value has no external reader.

⚠️ TWO THINGS A CALLER MUST NOT READ INTO A GAP
-----------------------------------------------
1. **A gap is not proof of a restart.** Two adjacent ``process_started_utc`` groups
   are what the DATA shows. If ``EXIT_LOOP_DECOUPLE_DISABLED`` was set for a
   window, nothing wrote to this log at all, and the boundary either side of that
   window renders as one enormous "restart gap". The exit requirement genuinely
   was not met in that mode (``write_disabled_state_file`` says so in terms), so
   the grade is not FALSE — but the LABEL would be, and the fix is to read
   ``from_utc`` / ``to_utc`` rather than to invent a plausibility threshold that
   silently reclassifies large gaps.
2. **A gap is measured completion-to-completion**, identically to a within-process
   interval, so the two are directly comparable. A pass that started and died
   contributes no completion — the same treatment ``record_pass`` gives a pass that
   hangs, and the same reason: a pass that did not finish did not finish.

Pure and side-effect free. Reads no file, opens no socket, touches no order path;
the caller supplies the rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ONE owner for the requirement and the band. Imported rather than re-derived:
# two modules independently deciding what "the requirement" is, is how the live
# gate and its diagnostic came to disagree in `_regime_score_semantics`'s lesson.
# `exit_loop_health` does not import this module at file scope (its import is
# inside a function), so there is no cycle.
from src.runtime.exit_loop_health import NEAR_MISS_FRACTION, requirement_seconds

# The aggregate grade. Module constants so a consumer can branch on the name
# rather than on a bare string — `collapsed-state-guard` credits constant names
# in consumers precisely so the better practice is not penalised.
RESTART_GAP_WITHIN = "within"
RESTART_GAP_NEAR_MISS = "near_miss"
RESTART_GAP_BREACHED = "breached"
RESTART_GAP_NOT_MEASURED = "not_measured"
RESTART_GAP_UNKNOWN = "unknown"

# The per-gap grade. See the module docstring for why this one is unregistered.
GAP_MEASURED = "measured"
GAP_OVERLAPPING = "overlapping"
GAP_UNGRADEABLE = "ungradeable"


def _parse(ts: Any) -> Optional[datetime]:
    """ISO-8601 → aware datetime, or ``None``. Never raises.

    ``None`` means *we could not read this stamp* and is propagated as
    ``ungradeable`` — never as a zero-length gap, which would manufacture perfect
    coverage out of a parse failure.
    """
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _process_groups(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Group rows by ``process_started_utc``; return (groups, unattributed_rows).

    Groups are ordered by their EARLIEST ``logged_at_utc``, not by the group key.
    The key is what identifies a process; the log stamp is what says when it
    actually ran, comes off the same clock as the gap itself, and survives a key
    that is null or malformed. Ordering by a field we may not be able to parse
    would make the ordering itself a hidden failure mode.
    """
    by_key: Dict[str, Dict[str, Any]] = {}
    unattributed = 0
    for r in rows:
        if not isinstance(r, dict):
            unattributed += 1
            continue
        key = r.get("process_started_utc")
        at = _parse(r.get("logged_at_utc"))
        if not isinstance(key, str) or not key or at is None:
            # No process identity, or no readable stamp: this row cannot sit on
            # either side of a boundary. Counted, never silently dropped.
            unattributed += 1
            continue
        g = by_key.get(key)
        if g is None:
            by_key[key] = {"process": key, "first_at": at, "last_at": at,
                           "first_row": r, "rows": 1}
            continue
        g["rows"] += 1
        if at < g["first_at"]:
            g["first_at"], g["first_row"] = at, r
        if at > g["last_at"]:
            g["last_at"] = at
    return sorted(by_key.values(), key=lambda g: g["first_at"]), unattributed


def grade_restart_gaps(
    rows: Sequence[Dict[str, Any]],
    *,
    requirement_s: Optional[float] = None,
    near_miss_fraction: float = NEAR_MISS_FRACTION,
    population: str = "unspecified",
    only_to_process: Optional[str] = None,
) -> Dict[str, Any]:
    """Grade the gaps BETWEEN processes. Pure; never raises.

    ``only_to_process`` narrows to the single boundary that ENDS at that process —
    what a live process wants when grading its own start. Everything else is
    unchanged, including the honest ``not_measured`` when that boundary is not in
    the supplied rows.

    ``population`` is carried into the result and is not decoration: this function
    is called both on a bounded TAIL (from the trader, once per process) and on the
    WHOLE log (from the read surface), and a max over an unstated sample is not a
    claim. Every count ships beside its denominator for the same reason
    ``exposure_soak`` ships ``max_multiple`` beside ``measured_n``.
    """
    req_s = requirement_seconds() if requirement_s is None else float(requirement_s)
    out: Dict[str, Any] = {
        "restart_gap_state": RESTART_GAP_UNKNOWN,
        "population": population,
        "requirement_s": req_s,
        "near_miss_fraction": float(near_miss_fraction),
        "rows_seen": len(rows),
        "processes_seen": 0,
        "boundaries_seen": 0,
        "gaps_measured": 0,
        "overlapping_gaps": 0,
        "ungradeable_gaps": 0,
        "unattributed_rows": 0,
        "breaches": 0,
        "max_gap_ms": None,
        "max_gap_ratio": None,
        "max_gap_from_process": None,
        "max_gap_to_process": None,
        "max_gap_from_utc": None,
        "max_gap_to_utc": None,
        "gaps": [],
    }
    try:
        req_ms = req_s * 1000.0
        groups, unattributed = _process_groups(rows)
        out["unattributed_rows"] = unattributed
        out["processes_seen"] = len(groups)
        if len(groups) < 2:
            # NO boundary exists. Not compliance — nothing to comply.
            out["restart_gap_state"] = RESTART_GAP_NOT_MEASURED
            return out

        gaps: List[Dict[str, Any]] = []
        for prev, cur in zip(groups, groups[1:]):
            if only_to_process is not None and cur["process"] != only_to_process:
                continue
            rec: Dict[str, Any] = {
                "from_process": prev["process"],
                "to_process": cur["process"],
                "from_utc": prev["last_at"].isoformat(),
                "to_utc": cur["first_at"].isoformat(),
                "gap_ms": None,
                "gap_ratio": None,
                "over_requirement": None,
            }
            # ⚠️ THE SUCCESSOR'S EARLIEST ROW MUST BE A GENUINE FIRST PASS. If the
            # log was head-truncated or rotated, an "earliest" row may just be the
            # earliest SURVIVING one, and the gap computed from it would be an
            # arbitrary within-process interval wearing a restart-gap label —
            # systematically SHORT, i.e. wrong in the reassuring direction.
            if cur["first_row"].get("first_pass_of_process") is not True:
                rec["gap_state"] = GAP_UNGRADEABLE
                rec["why"] = ("successor's earliest row is not flagged "
                              "first_pass_of_process — the boundary cannot be "
                              "located, so this is not graded")
                gaps.append(rec)
                continue
            delta_ms = (cur["first_at"] - prev["last_at"]).total_seconds() * 1000.0
            if delta_ms < 0:
                # Excluded from the max rather than clamped to 0. See the docstring.
                rec["gap_state"] = GAP_OVERLAPPING
                rec["gap_ms"] = round(delta_ms, 1)
                rec["why"] = ("the successor completed a pass before the "
                              "predecessor's last — processes overlapped during "
                              "handover; not graded against the requirement")
                gaps.append(rec)
                continue
            rec["gap_state"] = GAP_MEASURED
            rec["gap_ms"] = round(delta_ms, 1)
            rec["gap_ratio"] = round(delta_ms / req_ms, 4) if req_ms > 0 else None
            rec["over_requirement"] = bool(delta_ms > req_ms)
            gaps.append(rec)

        out["gaps"] = gaps
        out["boundaries_seen"] = len(gaps)
        measured = [g for g in gaps if g.get("gap_state") == GAP_MEASURED]
        out["gaps_measured"] = len(measured)
        out["overlapping_gaps"] = sum(
            1 for g in gaps if g.get("gap_state") == GAP_OVERLAPPING)
        out["ungradeable_gaps"] = sum(
            1 for g in gaps if g.get("gap_state") == GAP_UNGRADEABLE)
        out["breaches"] = sum(1 for g in measured if g.get("over_requirement"))

        if not measured:
            # Boundaries existed and none could be graded. That is "we could not
            # look", which is a DIFFERENT fact from "there was no boundary" — and
            # both are different from compliance.
            out["restart_gap_state"] = (
                RESTART_GAP_NOT_MEASURED if not gaps else RESTART_GAP_UNKNOWN)
            return out

        peak = max(measured, key=lambda g: g["gap_ms"])
        out["max_gap_ms"] = peak["gap_ms"]
        out["max_gap_ratio"] = peak["gap_ratio"]
        out["max_gap_from_process"] = peak["from_process"]
        out["max_gap_to_process"] = peak["to_process"]
        out["max_gap_from_utc"] = peak["from_utc"]
        out["max_gap_to_utc"] = peak["to_utc"]

        # ORDER MATTERS, exactly as in `exit_loop_health.status`: `breached` is
        # tested FIRST so the band underneath can never downgrade a real breach.
        if peak["gap_ms"] > req_ms:
            out["restart_gap_state"] = RESTART_GAP_BREACHED
        elif peak["gap_ms"] >= req_ms * float(near_miss_fraction):
            out["restart_gap_state"] = RESTART_GAP_NEAR_MISS
        else:
            out["restart_gap_state"] = RESTART_GAP_WITHIN
        return out
    except Exception:  # noqa: BLE001 — observability must never raise
        # `unknown`, which is what the dict already carries. A read failure must
        # never be able to report compliance.
        out["restart_gap_state"] = RESTART_GAP_UNKNOWN
        return out


def grade_this_process(
    process_started_utc: Optional[str],
    *,
    tail_limit: int = 50,
) -> Dict[str, Any]:
    """Grade the boundary that ENDS at ``process_started_utc``. Never raises.

    The live-trader entry point: a bounded tail read plus the pure grader above.
    Bounded because a restart gap joins two ADJACENT rows, so a handful is
    sufficient — see ``exit_interval_soak.read_tail_records``.

    Returns ``not_measured`` (never ``within``) when the caller has no process
    identity or when the boundary is not in the tail, e.g. the very first process
    ever to write the log.
    """
    try:
        if not isinstance(process_started_utc, str) or not process_started_utc:
            return grade_restart_gaps(
                [], population="no process identity — nothing to grade")
        from src.runtime.exit_interval_soak import read_tail_records
        rows = read_tail_records(limit=tail_limit)
        return grade_restart_gaps(
            rows,
            population=f"last {tail_limit} soak rows (bounded tail, NOT the log's history)",
            only_to_process=process_started_utc,
        )
    except Exception:  # noqa: BLE001
        return {"restart_gap_state": RESTART_GAP_UNKNOWN,
                "population": "read failed", "gaps": [],
                "gaps_measured": 0, "max_gap_ms": None}
