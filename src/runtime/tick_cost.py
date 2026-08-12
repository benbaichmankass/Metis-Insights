"""Per-tick wall-clock cost of the trader loop — MEASURED, not bounded.

``src/main.py``'s tick is a chain of best-effort hooks: the order monitor, the
pairs executor, the macro-thesis tick, five prop prompts, two reachability
alerts, the IB-state dump, and the exposure soak. **Each is individually
correct** — internally cadence-gated, exception-swallowing, documented as cheap.
Nothing measured the SUM.

That gap matters because both June 2026 wedges were *"a per-tick cost that was
fine in isolation"*: the steady-state regime-scoring fetch (`MB-20260609-001`)
and the cold-start burst that pegged the 2-core box and froze the heartbeat
(`BL-20260609-001`). The defence each time was a bound on the NEW component,
never on the total — so the total has grown un-observed ever since, and the next
hook added will be individually cheap too.

**This module deliberately does NOT enforce a budget.** Setting a cap without a
distribution behind it is exactly the mistake
`gross-exposure-governance-DESIGN.md` § 6-7 records for the exposure ceiling: a
ceiling below normal operation silently throttles correct work, and you cannot
know where normal operation sits until you have measured it. Measure first. A
`TICK_COST_*` budget, if one is ever warranted, is a separate change with this
soak behind it.

**Cost of the measurement itself:** two `time.monotonic()` calls per tick and an
atomic write of a fixed-size payload on a cadence (`TICK_COST_WRITE_SECONDS`,
default 300s). The in-memory max is updated EVERY tick regardless of the write
cadence, so a quiet cadence never loses the peak — the same reasoning as
`exposure_soak`, where the max is the load-bearing statistic and must survive
the sampling gap.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

STATE_FILE_NAME = "tick_cost.json"

_WRITE_CADENCE_ENV = "TICK_COST_WRITE_SECONDS"
_DEFAULT_WRITE_CADENCE_S = 300.0

# Distinct hook names the per-hook accumulator will track. The chain is a fixed
# static list, so this is a BACKSTOP against a caller generating names
# dynamically (a per-symbol or per-account label would grow the payload with the
# book, and the module's whole contract is a fixed-size write on a 2-core box).
# Overflow is RECORDED, never silently dropped — see `_hook_overflow`.
_MAX_HOOK_NAMES = 32

# Process-lifetime accumulators. Deliberately fixed-size: this must not grow
# with uptime on a 2-core box whose memory pressure is already load-bearing.
_ticks: int = 0
_last_ms: Optional[float] = None
_max_ms: Optional[float] = None
_max_at_utc: Optional[str] = None
_sum_ms: float = 0.0
_started_utc: Optional[str] = None
_last_write_ts: Optional[float] = None
_tick_start: Optional[float] = None
# WHICH THREAD owns the main tick (section 6.3). Set at begin_tick; every
# record_hook from a DIFFERENT thread is another loop's time and must not be
# divided by this tick's elapsed time.
_tick_thread: Optional[int] = None

# Per-hook accumulators: {name: {"n", "sum_ms", "max_ms", "max_at_utc"}}.
# Bounded by _MAX_HOOK_NAMES; `_hook_overflow` counts names refused so the
# payload can never quietly become a partial view presented as a whole one.
_hooks: Dict[str, Dict[str, Any]] = {}
_hook_overflow: int = 0

# OFF-LOOP hooks — recorded by a thread that does NOT own the main tick.
#
# Section 6.3, and this is the SECOND time this field has broken in the opposite
# direction. Adding `monitor.*` children under `order_monitor` made a flat sum
# double-count and `attributed_pct` read 136.8%. Decoupling the exit half breaks
# it the other way: `monitor.strategy_monitor_loop` would still be recorded, but
# from the exit loop's thread, so its ~24s would be summed into a numerator whose
# DENOMINATOR is the main tick's elapsed time. Two loops running concurrently do
# not share a clock, and dividing one's cost by the other's duration is not a
# percentage of anything.
#
# Segregating by THREAD rather than by a name convention is deliberate: a naming
# rule is a contract a caller can silently break (exactly what `nested_hooks`
# exists to make visible), whereas the thread identity is a fact. The exit loop's
# own cost is owned by `exit_loop_health.record_pass`, which measures it against
# the right denominator — its own pass. This block exists so that time is still
# VISIBLE here rather than silently dropped, with its own count and no share.
_offloop_hooks: Dict[str, Dict[str, Any]] = {}

# DECOUPLE PREREQUISITE (section 6.2, 2026-08-12). Until now exactly ONE thread
# ever called `record_hook` — the trader's main loop — so unsynchronised state was
# correct by construction. Moving the exit half onto its own loop makes two
# threads record concurrently, and two sequences below are not atomic BY THE
# LANGUAGE: `slot["n"] += 1` / `slot["sum_ms"] += ms` are read-modify-write, and
# `if len(_hooks) >= _MAX_HOOK_NAMES` then insert is check-then-act (two threads
# could both pass the check and over-admit past the bound whose whole job is to
# keep `hook_names_refused` honest).
#
# HONEST ABOUT THE EVIDENCE, because the first version of this comment was not:
# it asserted "a concurrent pair can LOSE an update" as though measured. It is not.
# I tried to reproduce it — 8 threads x 20k increments on a shared dict slot at
# `sys.setswitchinterval(1e-9)`, three trials — and lost ZERO updates every time;
# the accumulator tests below also pass with this lock stubbed out. CPython's GIL
# makes these sequences very hard to actually interleave in practice.
#
# So this lock is DEFENSIVE CORRECTNESS, not a fix for observed corruption. It is
# kept because the guarantee is a CPython implementation detail rather than a
# language promise, because the failure mode would be silent and in the
# flattering direction (a lost sample makes a hook look cheaper), and because an
# uncontended acquire costs nothing measurable next to a 24-second hook. The
# tests are REGRESSION guards — they would catch a refactor that moves work
# outside the lock or introduces a genuinely non-atomic step — and they are
# explicitly NOT evidence that the race occurs today.
#
# The lock is held only around the accumulator arithmetic — never around a hook's
# actual execution — so it cannot serialise the two loops it exists to support.
_hooks_lock = threading.Lock()


def write_cadence_seconds() -> float:
    """Resolve the persist cadence. Unparseable → default, never 'off'.

    A typo must not silently switch the measurement off — the same fail-ON
    reasoning as ``exposure_soak.cadence_seconds``.
    """
    try:
        return float(os.environ.get(_WRITE_CADENCE_ENV, _DEFAULT_WRITE_CADENCE_S))
    except (TypeError, ValueError):
        return _DEFAULT_WRITE_CADENCE_S


def begin_tick() -> None:
    """Mark the start of the tick's hook chain. Never raises."""
    global _tick_start, _started_utc, _tick_thread
    try:
        _tick_start = time.monotonic()
        _tick_thread = threading.get_ident()
        if _started_utc is None:
            _started_utc = datetime.now(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        _tick_start = None


def end_tick() -> Optional[float]:
    """Close the tick, fold it into the accumulators, persist on cadence.

    Returns the tick's duration in ms (None if the start marker was missing —
    which is reported honestly rather than as a zero, since "we did not time
    this tick" and "this tick took no time" are different statements).
    """
    global _ticks, _last_ms, _max_ms, _max_at_utc, _sum_ms, _tick_start, _last_write_ts
    try:
        if _tick_start is None:
            return None
        elapsed_ms = (time.monotonic() - _tick_start) * 1000.0
        _tick_start = None
        _ticks += 1
        _last_ms = elapsed_ms
        _sum_ms += elapsed_ms
        if _max_ms is None or elapsed_ms > _max_ms:
            _max_ms = elapsed_ms
            _max_at_utc = datetime.now(timezone.utc).isoformat()

        now = time.monotonic()
        cadence = write_cadence_seconds()
        if cadence > 0 and (_last_write_ts is None or (now - _last_write_ts) >= cadence):
            _last_write_ts = now
            write_state_file()
        return elapsed_ms
    except Exception:  # noqa: BLE001 — measurement must never break the tick
        _tick_start = None
        return None


def record_hook(name: str, elapsed_ms: float) -> None:
    """Fold one hook's duration into the per-hook accumulators. Never raises."""
    global _hook_overflow
    try:
        with _hooks_lock:
            # Another loop's thread → the off-loop block, never the tick's.
            table = (_hooks if (_tick_thread is None
                                or threading.get_ident() == _tick_thread)
                     else _offloop_hooks)
            slot = table.get(name)
            if slot is None:
                if len(table) >= _MAX_HOOK_NAMES:
                    _hook_overflow += 1
                    return
                slot = {"n": 0, "sum_ms": 0.0, "max_ms": None, "max_at_utc": None}
                table[name] = slot
            slot["n"] += 1
            slot["sum_ms"] += elapsed_ms
            if slot["max_ms"] is None or elapsed_ms > slot["max_ms"]:
                slot["max_ms"] = elapsed_ms
                slot["max_at_utc"] = datetime.now(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001 — measurement must never break the tick
        return


@contextlib.contextmanager
def hook(name: str) -> Iterator[None]:
    """Time one hook of the chain.

    Wraps a hook that is ALREADY exception-swallowing at its own call site, so
    this deliberately does NOT catch: re-raising preserves the caller's existing
    error handling exactly. The duration is recorded either way (``finally``), so
    a hook that raises still shows up in the split rather than vanishing from it
    — a hook that costs 40s and then throws is precisely the one worth seeing.
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        try:
            record_hook(name, (time.monotonic() - t0) * 1000.0)
        except Exception:  # noqa: BLE001
            pass


def _hook_view() -> Dict[str, Any]:
    """Per-hook block + the ATTRIBUTION COVERAGE, which is the load-bearing part.

    A split that reports only the hooks it instrumented invites the reader to
    conclude those hooks ARE the cost. They are a lower bound on it. So the block
    always ships `attributed_mean_ms` against the tick's own `mean_ms` and the
    resulting `attributed_pct` — if that reads 6%, the 253s lives somewhere this
    instrumentation does not look, and saying so is the finding.

    This is the same discipline as `rCoverage` / `pnlCoverage`: report how much
    of the population the number covers, never a bare figure over an unstated
    denominator.

    Per-hook `pct_of_total` is each hook's own share of tick time and is correct
    for a CHILD too (`monitor.foo` really did consume that share). What is NOT
    valid is summing parents and children together — see `snapshot()`, where the
    coverage denominator deliberately counts top-level hooks only.
    """
    out: Dict[str, Any] = {}
    # Copy under the lock, then compute outside it. A live iteration of `_hooks`
    # while the exit-loop thread inserts a name raises "dictionary changed size
    # during iteration" — inside the diag read path, i.e. the observability would
    # break exactly when two loops are running and it is most needed.
    with _hooks_lock:
        items = [(name, dict(slot)) for name, slot in _hooks.items()]
    for name, s in items:
        n = s["n"] or 0
        out[name] = {
            "n": n,
            "mean_ms": round(s["sum_ms"] / n, 1) if n else None,
            "max_ms": round(s["max_ms"], 1) if s["max_ms"] is not None else None,
            "max_at_utc": s["max_at_utc"],
            # Share of this PROCESS's total measured tick time. Answers "what
            # should I fix first?" directly, which the per-hook mean does not
            # when hooks fire on different cadences.
            "pct_of_total": (round(100.0 * s["sum_ms"] / _sum_ms, 1)
                             if _sum_ms > 0 else None),
        }
    return out


def snapshot() -> Dict[str, Any]:
    """Fixed-size view of the accumulators. Pure; never raises."""
    mean = (_sum_ms / _ticks) if _ticks else None
    # Sum of per-hook time attributed to THIS process's ticks, expressed per
    # tick so it is directly comparable to `mean_ms`.
    #
    # TOP-LEVEL HOOKS ONLY — a NESTED hook's time is already inside its parent's,
    # so summing both double-counts. Measured 2026-08-11, first read after the
    # 14-phase monitor split deployed: `attributed_pct` came back **136.8%**, a
    # share of a whole exceeding the whole. The flat sum was correct while every
    # wrap was a sibling (`run_one_tick` + `order_monitor`) and became wrong the
    # moment `monitor.*` children were added underneath one of them — by me, the
    # day before, without touching this function.
    #
    # That is worth naming rather than quietly patching: the field exists to state
    # the coverage of a split, and it silently mis-stated it as soon as the split
    # gained a level. `100 - attributed_pct` was documented as "every other hook
    # COMBINED"; at 136.8% it read as **-36.8%** of uninstrumented time, which is
    # not a conservative error — it is an impossible one, and only obvious because
    # a percentage over 100 cannot be squinted past. A double-count that had
    # landed at 95% would have read as excellent coverage.
    #
    # The hierarchy is carried by the NAME: a dotted name (`monitor.foo`) is a
    # child of some parent wrap; an undotted one is top-level. That convention is
    # ours and is the whole contract — a caller that invents a dotted name without
    # a parent wrap drops itself out of the coverage denominator, so `nested_hooks`
    # below is published to make the hierarchy visible rather than assumed.
    with _hooks_lock:
        _hooks_view = [(n, dict(sl)) for n, sl in _hooks.items()]
        _offloop_view = {n: {"n": sl["n"],
                             "mean_ms": (round(sl["sum_ms"] / sl["n"], 1)
                                         if sl["n"] else None),
                             "max_ms": (round(sl["max_ms"], 1)
                                        if sl["max_ms"] is not None else None),
                             "max_at_utc": sl["max_at_utc"]}
                        for n, sl in _offloop_hooks.items()}
    top_level = {n: s for n, s in _hooks_view if "." not in n}
    hook_sum_ms = sum(s["sum_ms"] for s in top_level.values())
    attributed_mean = (hook_sum_ms / _ticks) if _ticks else None
    return {
        "hooks": _hook_view(),
        "hooks_attributed_mean_ms": (round(attributed_mean, 1)
                                     if attributed_mean is not None else None),
        # None (not 0) when there is nothing to divide by: "we have not measured
        # a tick yet" is not "0% of the tick is attributed".
        "attributed_pct": (round(100.0 * hook_sum_ms / _sum_ms, 1)
                           if _sum_ms > 0 else None),
        # How many hooks are CHILDREN (dotted names), i.e. excluded from the
        # coverage sum above because their time is already inside a parent. Shipped
        # always so the hierarchy is visible: a reader comparing `attributed_pct`
        # against the `hooks` block can otherwise not tell why the listed means do
        # not add up to it. 0 means the block is flat and the two agree exactly.
        "nested_hooks": sum(1 for n, _ in _hooks_view if "." in n),
        # Hooks recorded by a thread that does not own the main tick (the
        # decoupled exit loop). REPORTED, and deliberately absent from
        # `attributed_pct` above: their denominator is their own loop's duration,
        # not this tick's. `{}` once the exit loop is decoupled and idle is a real
        # answer; a non-empty block here while `attributed_pct` looks healthy is
        # the two-loops-running state, not an inconsistency.
        "offloop_hooks": _offloop_view,
        # Non-zero means a caller generated hook names dynamically and the split
        # is PARTIAL. Shipped always so a truncated view cannot read as complete.
        "hook_names_refused": _hook_overflow,
        "ticks_measured": _ticks,
        "last_ms": round(_last_ms, 1) if _last_ms is not None else None,
        # The load-bearing statistic. A mean that looks fine while the peak
        # freezes the heartbeat is precisely the 2026-06-09 shape, so the max
        # ships beside the mean AND beside ticks_measured — a max over 3 ticks
        # and a max over 3000 are different claims.
        "max_ms": round(_max_ms, 1) if _max_ms is not None else None,
        "max_at_utc": _max_at_utc,
        "mean_ms": round(mean, 1) if mean is not None else None,
        "process_started_utc": _started_utc,
    }


def state_file_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / STATE_FILE_NAME


def write_state_file(path: Optional[Any] = None) -> bool:
    """Atomic write of the fixed-size snapshot. Best-effort; never raises.

    Same cross-process shape as ``ib_state.json``: the TRADER measures, the
    separate web-api process serves it at ``/api/diag/tick_cost``.
    """
    try:
        target = path if path is not None else state_file_path()
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **snapshot(),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)
        return True
    except Exception:  # noqa: BLE001
        return False


def read_state() -> Dict[str, Any]:
    """Reader envelope for the diag route. ``present:false`` when unwritten."""
    path = state_file_path()
    out: Dict[str, Any] = {"present": False, "path": str(path),
                           "generated_at": None, "age_seconds": None}
    if not path.exists():
        return out
    try:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["present"] = True
    out.update(payload)
    try:
        gen = payload.get("generated_at")
        if gen:
            dt = datetime.fromisoformat(str(gen))
            out["age_seconds"] = round(
                (datetime.now(timezone.utc) - dt).total_seconds(), 1
            )
    except (TypeError, ValueError):
        out["age_seconds"] = None
    return out


def _reset_for_tests() -> None:
    """Test-only accumulator reset."""
    global _ticks, _last_ms, _max_ms, _max_at_utc, _sum_ms
    global _started_utc, _last_write_ts, _tick_start, _hook_overflow
    _ticks, _last_ms, _max_ms, _max_at_utc = 0, None, None, None
    _sum_ms, _started_utc, _last_write_ts, _tick_start = 0.0, None, None, None
    global _tick_thread
    with _hooks_lock:
        _hooks.clear()
        _offloop_hooks.clear()
    _hook_overflow = 0
    _tick_thread = None
