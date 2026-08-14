"""Liveness for the DECOUPLED exit-evaluation loop — section 6.1's missing half.

WHY THIS HAS TO EXIST BEFORE THE LOOP DOES.

`heartbeat.txt`'s mtime is the canonical "is the trader responsive" signal, and it
works because the pipeline runs INLINE on the main thread: a hang anywhere in the
tick stops the heartbeat, so `scripts/check_heartbeat.py` sees it. **That coverage
IS the inline execution.** Move exit evaluation onto its own loop and the coupling
breaks in the worst possible direction — the main loop keeps writing a healthy
heartbeat while no open trade is being evaluated. The watchdog stays quiet, the
apps stay green, and the failure is invisible for as long as it lasts. Trading a
measured 31.78 s of coverage margin for an unbounded silent wedge would be a bad
deal at any margin.

So the exit loop gets its own signal and something reads it.

THREE STATES, NEVER COLLAPSED (`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
states"). "We have not looked", "it has never run", and "it ran and is late" are
three different facts and a reader must be able to tell them apart:

  * `unknown`     — the state file is unreadable/garbled. We did not look; this is
                    NOT health, and it is NOT staleness either.
  * `never_ran`   — no pass has completed since this process started. Expected for
                    the first seconds of a boot; alarming after that.
  * `fresh`       — a pass completed within the staleness window.
  * `stale`       — a pass completed, but too long ago. THE wedge signal.

It also records `last_pass_ms` / `max_pass_ms`, which is the point beyond alerting:
the whole decouple rests on a claim (a pass costs ~28 s, so coverage clears 60 s
with ~53% margin) measured OFFLINE on seven ticks. These fields are how that claim
gets checked in production, per-process, against the real book — and `max_pass_ms`
is the load-bearing one, exactly as in `tick_cost` and `exposure_soak`: a mean that
looks fine while the peak blows the window is the 2026-06-09 incident's shape.

Best-effort throughout: this is observability for the money loop and must never
raise into it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STATE_FILE_NAME = "exit_loop_health.json"

_STALE_ENV = "EXIT_LOOP_STALE_SECONDS"
# 180s = 3x the 60s exit-evaluation target, against a measured ~28s pass. Wide
# enough that a slow pass or a paused venue never cries wolf; narrow enough that a
# genuine wedge is caught inside a few minutes rather than a few hours.
_DEFAULT_STALE_S = 180.0

_lock = threading.Lock()
_passes: int = 0
_last_pass_monotonic: Optional[float] = None
_last_pass_utc: Optional[str] = None
_last_pass_ms: Optional[float] = None
_max_pass_ms: Optional[float] = None
_max_pass_at_utc: Optional[str] = None
_started_utc: Optional[str] = None


def stale_seconds() -> float:
    """Resolve the staleness window. Unparseable or non-positive → the default.

    A typo must not silently switch the wedge detector OFF — the same fail-ON
    reasoning as `exposure_soak.cadence_seconds` and `tick_cost.write_cadence_seconds`.
    Disabling this is not offered as a config option at all: the loop it watches has
    no other liveness coverage.
    """
    try:
        v = float(os.environ.get(_STALE_ENV, "") or _DEFAULT_STALE_S)
    except (TypeError, ValueError):
        return _DEFAULT_STALE_S
    return v if v > 0 else _DEFAULT_STALE_S


def record_pass(duration_ms: float) -> None:
    """Record one COMPLETED exit-evaluation pass. Never raises.

    Called after the pass returns, deliberately — a pass that started and hung
    must NOT refresh liveness, which is the entire condition being detected.
    """
    global _passes, _last_pass_monotonic, _last_pass_utc, _last_pass_ms
    global _max_pass_ms, _max_pass_at_utc, _started_utc
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        with _lock:
            if _started_utc is None:
                _started_utc = now_utc
            _passes += 1
            _last_pass_monotonic = time.monotonic()
            _last_pass_utc = now_utc
            _last_pass_ms = duration_ms
            if _max_pass_ms is None or duration_ms > _max_pass_ms:
                _max_pass_ms = duration_ms
                _max_pass_at_utc = now_utc
    except Exception:  # noqa: BLE001 — observability must never break the loop
        return


def status() -> Dict[str, Any]:
    """Current health. Never raises; every field honest about what it knows."""
    try:
        with _lock:
            passes = _passes
            last_mono = _last_pass_monotonic
            snap = {
                "passes": passes,
                "last_pass_utc": _last_pass_utc,
                "last_pass_ms": (round(_last_pass_ms, 1)
                                 if _last_pass_ms is not None else None),
                "max_pass_ms": (round(_max_pass_ms, 1)
                                if _max_pass_ms is not None else None),
                "max_pass_at_utc": _max_pass_at_utc,
                "process_started_utc": _started_utc,
                "stale_threshold_s": stale_seconds(),
            }
        if last_mono is None:
            # NOT stale — nothing has run yet. Collapsing these would make a
            # booting process indistinguishable from a wedged one.
            snap.update(state="never_ran", age_seconds=None, stale=False)
            return snap
        age = time.monotonic() - last_mono
        snap.update(
            age_seconds=round(age, 1),
            stale=age > snap["stale_threshold_s"],
            state="stale" if age > snap["stale_threshold_s"] else "fresh",
        )
        return snap
    except Exception:  # noqa: BLE001
        # We could not look. Emphatically not "healthy", and not "stale" either.
        return {"state": "unknown", "stale": False, "passes": None,
                "age_seconds": None, "last_pass_utc": None}


def write_state_file(runtime_dir: Optional[str] = None) -> Optional[str]:
    """Persist `status()` for the diag surface. Never raises; returns the path."""
    try:
        if runtime_dir is None:
            from src.utils.paths import runtime_logs_dir
            runtime_dir = str(runtime_logs_dir())
        os.makedirs(runtime_dir, exist_ok=True)
        path = os.path.join(runtime_dir, STATE_FILE_NAME)
        payload = dict(status(), generated_at=datetime.now(timezone.utc).isoformat())
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)   # atomic: a reader never sees a half-written file
        return path
    except Exception:  # noqa: BLE001
        return None


def write_disabled_state_file(runtime_dir: Optional[str] = None) -> Optional[str]:
    """Write the state file for a process where the decouple is DISABLED.

    WHY THIS EXISTS (live-verified 2026-08-14, the same day #9233 rolled the
    decouple back).

    Only `_exit_loop` ever called `write_state_file`, and `run_exit_loop_health_check`
    only ran inside `if decoupled:`. So with `EXIT_LOOP_DECOUPLE_DISABLED=1`
    NOTHING rewrote the file and NOTHING re-read it — the payload from the
    PREVIOUS process survived untouched, and it said `"state": "fresh"`.

    Measured on the live trader: `exit_loop_health.json` carried
    `generated_at 2026-08-14T11:46:13Z` while the running process had started at
    `11:46:29Z` — a file stamped 16 seconds BEFORE the process it appeared to
    describe, reporting a healthy loop that did not exist. Every downstream
    reader (the diag surface, a session, the operator) saw `fresh`.

    That is precisely the collapse this module's own docstring says it prevents:
    "we have not looked" and "it ran and is fine" became indistinguishable, in
    the one surface built to tell them apart. `CLAUDE.md` states that `never_ran`
    "is also what a set `EXIT_LOOP_DECOUPLE_DISABLED` produces" — a true
    statement about the vocabulary that the code did not implement, because the
    producer of that state never ran either.

    The fix keeps the four-state vocabulary rather than inventing a fifth: the
    loop genuinely HAS NOT RUN in this process, so `never_ran` is the honest
    grade. `decouple_disabled` says WHY, so a reader can tell a deliberate
    rollback from a thread that failed to start — which `_start_exit_loop`
    already treats as two different conditions and which would otherwise
    re-collapse the moment this file started reporting `never_ran` for both.

    Called every tick on the disabled branch, not once at startup: a
    `generated_at` that stops advancing is itself a fossil, so refreshing it is
    what keeps "disabled since boot" distinguishable from "this file is stale".
    """
    try:
        if runtime_dir is None:
            from src.utils.paths import runtime_logs_dir
            runtime_dir = str(runtime_logs_dir())
        os.makedirs(runtime_dir, exist_ok=True)
        path = os.path.join(runtime_dir, STATE_FILE_NAME)
        payload = {
            "state": "never_ran",
            "stale": False,
            "decouple_disabled": True,
            "passes": 0,
            "age_seconds": None,
            "last_pass_utc": None,
            "last_pass_ms": None,
            "max_pass_ms": None,
            "max_pass_at_utc": None,
            "process_started_utc": None,
            "stale_threshold_s": stale_seconds(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": (
                "EXIT_LOOP_DECOUPLE_DISABLED is set: exit evaluation rides the "
                "main tick, so there is no decoupled loop to be healthy. This is "
                "NOT health — the 60s exit-evaluation target is not met in this "
                "mode."
            ),
        }
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)   # atomic: a reader never sees a half-written file
        return path
    except Exception:  # noqa: BLE001
        return None


def _reset_for_tests() -> None:
    global _passes, _last_pass_monotonic, _last_pass_utc, _last_pass_ms
    global _max_pass_ms, _max_pass_at_utc, _started_utc
    with _lock:
        _passes = 0
        _last_pass_monotonic = None
        _last_pass_utc = None
        _last_pass_ms = None
        _max_pass_ms = None
        _max_pass_at_utc = None
        _started_utc = None


# --------------------------------------------------------------------- alerting
#
# A liveness signal nobody reads is worse than no signal: a reviewer sees the
# field and assumes something acts on it (`provenance-consumer-guard`'s whole
# premise, and the defect PR #8665's exposure block shipped with). So the module
# that produces the state also owns the consumer.
#
# LATCHED, not per-tick. The stale condition persists by nature — once the exit
# loop wedges it is stale on every subsequent tick — and a per-tick alarm is the
# desensitized-alarm P1 this repo has an explicit rule about. One ALERT crossing
# into stale, one OK crossing back out.
#
# ALERT-ONLY, deliberately NOT auto-restart. `ict-liveness-watchdog` may restart
# the trader for a dead heartbeat because a dead heartbeat means the process is
# already not working. A stale exit loop is different: the rest of the trader is
# healthy and still managing risk, so killing it converts a partial degradation
# into a total outage plus a cold-start burst (the `BL-20260609-001` shape).
# Escalating this to autoheal is a separate operator decision with its own
# restart-loop containment, not something to fold into the loop's first version.

_ALERT_STATE_FILENAME = "exit_loop_health_alert_state.json"


def _alert_state_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / _ALERT_STATE_FILENAME


def _load_alert_state() -> dict:
    try:
        p = _alert_state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_alert_state(state: dict) -> None:
    try:
        p = _alert_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        return


def _send(message: str) -> None:
    """Telegram + one typed WARNING push, mirroring account_reachability_alert."""
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, title="Exit loop", body=message)
    except Exception:  # noqa: BLE001
        pass


def run_exit_loop_health_check() -> Dict[str, Any]:
    """Called once per MAIN tick. Latched alert on the exit loop going stale.

    Runs on the main thread on purpose: the main loop is the one thing still known
    to be alive when the exit loop is not, so it is the only place a check can
    observe the condition. Never raises.

    Returns the status dict plus `alerted` / `recovered` so a caller (and the
    tests) can see what it decided rather than inferring it from side effects.
    """
    try:
        st = status()
        state = st.get("state")
        # `unknown` and `never_ran` are NOT alertable: the first means we could not
        # look, the second that a boot has not finished its first pass. Alerting on
        # either would fire on every restart and teach the operator to ignore this.
        if state not in ("fresh", "stale"):
            return dict(st, alerted=False, recovered=False)

        prev = _load_alert_state()
        was_stale = bool(prev.get("stale"))
        is_stale = bool(st.get("stale"))
        alerted = recovered = False

        if is_stale and not was_stale:
            _send(
                "\U0001F534 [ALERT] EXIT LOOP STALE - no exit-evaluation pass "
                f"completed for {st.get('age_seconds')}s "
                f"(threshold {st.get('stale_threshold_s')}s). Open trades are NOT "
                "being evaluated for exits; SL/TP resting at the broker still "
                "apply. The main tick is alive, so this is the exit loop alone."
            )
            alerted = True
        elif was_stale and not is_stale:
            _send(
                "\U0001F7E2 [OK] Exit loop recovered - passes resumed "
                f"(last {st.get('last_pass_ms')}ms)."
            )
            recovered = True

        if is_stale != was_stale:
            _save_alert_state({"stale": is_stale,
                               "at": datetime.now(timezone.utc).isoformat()})
        return dict(st, alerted=alerted, recovered=recovered)
    except Exception:  # noqa: BLE001
        return {"state": "unknown", "stale": False, "alerted": False,
                "recovered": False}
