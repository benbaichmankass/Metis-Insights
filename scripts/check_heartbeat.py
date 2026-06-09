#!/usr/bin/env python3
"""Heartbeat watchdog — S-022 PR5.

Reads ``runtime_logs/heartbeat.txt`` and pings Telegram if the trader
process appears stuck. Deployed as ``ict-liveness-watchdog.timer``
firing every 60 s on the live VM (``deploy/ict-liveness-watchdog.{
service,timer}``).

Idempotent: state lives in ``runtime_logs/heartbeat_check_state.json``.
A second run inside the same staleness window does NOT re-ping. A
recovery run (heartbeat is fresh again after having been stale) sends
exactly one "recovered" ping.

Optional autoheal: when ``--auto-restart-after N`` is set (or env
``LIVENESS_AUTO_RESTART_AFTER=N``), the watchdog runs
``sudo -n systemctl restart ict-trader-live.service`` after N
consecutive stale checks. Disabled by default — opt-in once the
operator trusts the alert path. The autoheal action sends its own
Telegram ping with the systemctl exit code so the operator sees the
recovery attempt regardless of whether it succeeded.

Stdlib-only: no requests, no anthropic SDK, no internal src.* imports
beyond ``src.runtime.notify`` (also stdlib-only). This means the
watchdog keeps working even if the bot's own venv is wedged.

Exit codes:
  0 — heartbeat is fresh (no action), or alert sent successfully.
  1 — could not stat heartbeat / state files.
  2 — alert needed but Telegram POST failed.

CLI:
  python scripts/check_heartbeat.py
  python scripts/check_heartbeat.py --interval 60 --grace 5
  python scripts/check_heartbeat.py --interval 60 --grace 5 \\
      --auto-restart-after 3

Env vars (override CLI defaults):
  HEARTBEAT_FILE   absolute path to heartbeat.txt. If unset, the
                   script resolves the same path the trader writes to
                   via DATA_DIR / RUNTIME_LOGS_DIR (mirrors
                   ``src.utils.paths``); falls back to repo-relative.
  HEARTBEAT_STATE  absolute path to state json (same resolution).
  TICK_INTERVAL_SECONDS, HEARTBEAT_GRACE_FACTOR, TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID — same names the trader uses.
  LIVENESS_AUTO_RESTART_AFTER  if set to a positive integer N, the
                   watchdog escalates from alert-only to alert +
                   ``systemctl restart ict-trader-live.service``
                   after N consecutive stale checks. 0/unset =
                   alert-only.
  LIVENESS_RESTART_UNIT  systemd unit name to restart on autoheal
                   (default ``ict-trader-live.service``).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolved_runtime_logs_dir() -> Path:
    """Match ``src.utils.paths.runtime_logs_dir`` without importing it.

    The watchdog is stdlib-only by design (it has to keep working when
    the bot's venv is broken), so we re-derive the resolution order
    here instead of importing the helper. Resolution order, same as
    the trader:
      1. ``RUNTIME_LOGS_DIR`` env (per-root override).
      2. ``DATA_DIR`` env (``$DATA_DIR/runtime_logs``).
      3. ``<repo>/runtime_logs/`` fallback.
    Relative env values anchor to ``_REPO_ROOT`` so a CWD shift in
    systemd doesn't change which file we read.
    """
    override = os.environ.get("RUNTIME_LOGS_DIR")
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            candidate = _REPO_ROOT / candidate
        return candidate
    umbrella = os.environ.get("DATA_DIR")
    if umbrella:
        umbrella_root = Path(umbrella).expanduser()
        if not umbrella_root.is_absolute():
            umbrella_root = _REPO_ROOT / umbrella_root
        return umbrella_root / "runtime_logs"
    return _REPO_ROOT / "runtime_logs"


DEFAULT_HEARTBEAT = _resolved_runtime_logs_dir() / "heartbeat.txt"
DEFAULT_STATE = _resolved_runtime_logs_dir() / "heartbeat_check_state.json"


def _system_uptime_s() -> float:
    """Seconds since the host booted.

    Used by the boot-grace suppression so a VM reboot doesn't spam
    ``[CRITICAL] heartbeat stale`` while the trader is merely (re)starting.
    Fail-OPEN: on any read error return ``+inf`` so we treat the host as
    "long up" and never silently suppress a *real* stall.
    """
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        return float(time.clock_gettime(time.CLOCK_BOOTTIME))
    except (OSError, AttributeError, ValueError):
        return float("inf")


def _seconds_since_unit_active(unit: str) -> Optional[float]:
    """Seconds since ``unit`` last entered the active state, or None.

    Reads ``ActiveEnterTimestampMonotonic`` (microseconds since boot, on
    CLOCK_MONOTONIC) via ``systemctl show`` and compares it to the current
    monotonic clock. Used by the startup-grace gate so the watchdog does
    not restart a trader that has only just (re)started and has not yet had
    time to write its first heartbeat (BL-20260605-001 part b).

    Fail-OPEN: returns ``None`` on any error (systemctl missing, parse
    failure, value 0/unset) so a read failure NEVER suppresses a genuinely
    needed restart — the caller treats None as "no startup-grace info,
    proceed".
    """
    try:
        proc = subprocess.run(
            ["systemctl", "show", unit,
             "--property=ActiveEnterTimestampMonotonic", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    try:
        enter_us = int(raw)
    except (TypeError, ValueError):
        return None
    if enter_us <= 0:
        # 0 == never active (or systemd reports unset); no usable timestamp.
        return None
    try:
        now_us = time.clock_gettime(time.CLOCK_MONOTONIC) * 1_000_000.0
    except (OSError, AttributeError, ValueError):
        return None
    delta = (now_us - enter_us) / 1_000_000.0
    # Clock domain mismatch / negative → treat as "no info" (fail-open).
    return delta if delta >= 0 else None


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"WARN: could not save state {path}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Telegram (uses src.runtime.notify so we honour the same redaction +
# error handling as the rest of the bot — but only stdlib import path).
# ---------------------------------------------------------------------------


def send_alert(message: str) -> bool:
    try:
        sys.path.insert(0, str(_REPO_ROOT))
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Telegram POST failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def evaluate(
    *,
    heartbeat_path: Path,
    state_path: Path,
    tick_interval_s: int,
    grace_factor: float,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a dict ``{action, age_s, ...}`` describing what to do.

    Possible actions:
      * "missing"   — heartbeat file does not exist; alert if not already.
      * "stale"     — heartbeat older than tick_interval * grace_factor.
      * "recovered" — heartbeat is fresh again after a previous alert.
      * "ok"        — heartbeat is fresh, no prior alert (do nothing).
    """
    now = now if now is not None else time.time()
    state = load_state(state_path)
    last_status = state.get("last_status")  # "stale" | "recovered" | None
    threshold = tick_interval_s * grace_factor

    if not heartbeat_path.exists():
        if last_status == "stale":
            return {"action": "ok", "reason": "still missing", "age_s": None,
                    "state": state}
        return {"action": "missing", "age_s": None, "state": state}

    try:
        age_s = now - heartbeat_path.stat().st_mtime
    except OSError as exc:
        return {"action": "ok", "reason": f"stat failed: {exc}",
                "age_s": None, "state": state}

    if age_s > threshold:
        if last_status == "stale":
            # Already alerted; check if it has worsened by another full
            # threshold and re-ping if so.
            last_age = float(state.get("last_alert_age_s") or age_s)
            if age_s - last_age >= threshold:
                return {"action": "stale", "age_s": age_s, "reason": "worsened",
                        "state": state}
            return {"action": "ok", "age_s": age_s, "reason": "already alerted",
                    "state": state}
        return {"action": "stale", "age_s": age_s, "reason": "first detection",
                "state": state}

    # Heartbeat is fresh.
    if last_status == "stale":
        return {"action": "recovered", "age_s": age_s, "state": state}
    return {"action": "ok", "age_s": age_s, "state": state}


# ---------------------------------------------------------------------------
# Autoheal — systemctl restart escalation
# ---------------------------------------------------------------------------


def try_autoheal_restart(unit: str) -> Dict[str, Any]:
    """Run ``sudo -n systemctl restart <unit>`` and return its result.

    Stdlib subprocess only. Returns
    ``{ran: bool, returncode: int, stdout: str, stderr: str}``.
    ``ran=False`` only if subprocess itself failed to start (rare —
    typically PATH/permission). A non-zero returncode (sudo refused,
    systemctl missing) is still ``ran=True`` so the caller can render
    a useful Telegram message.
    """
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", unit],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ran": False, "returncode": -1, "stdout": "",
            "stderr": f"subprocess failed: {type(exc).__name__}: {exc}",
        }
    return {
        "ran": True,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-200:],
        "stderr": (proc.stderr or "")[-200:],
    }


def render_autoheal_alert(
    unit: str, restart_result: Dict[str, Any], stale_count: int, age_s: Optional[float]
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    age_min = int(age_s // 60) if age_s else 0
    rc = restart_result.get("returncode")
    err_tail = (restart_result.get("stderr") or "").strip()[-160:]
    if not restart_result.get("ran"):
        return (
            f"[CRITICAL] Autoheal restart FAILED to dispatch ({ts})\n"
            f"Unit: {unit}. Stale {age_min}m, {stale_count} consecutive checks.\n"
            f"subprocess error: {err_tail}\n"
            f"Manual intervention required."
        )
    if rc == 0:
        return (
            f"[ACTION] Autoheal dispatched: systemctl restart {unit} ({ts})\n"
            f"Trigger: heartbeat stale {age_min}m, {stale_count} consecutive checks.\n"
            f"systemctl exit=0. Next heartbeat in ~30 s should confirm recovery."
        )
    return (
        f"[CRITICAL] Autoheal restart returned rc={rc} ({ts})\n"
        f"Unit: {unit}. Stale {age_min}m, {stale_count} consecutive checks.\n"
        f"stderr tail: {err_tail}\n"
        f"Manual intervention required."
    )


def render_autoheal_exhausted(
    unit: str, max_restarts: int, stale_count: int, age_s: Optional[float]
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    age_min = int(age_s // 60) if age_s else 0
    return (
        f"[CRITICAL] Autoheal EXHAUSTED ({ts})\n"
        f"Unit: {unit}. {max_restarts} restarts did not keep the trader "
        f"heartbeating (stale {age_min}m, {stale_count} consecutive checks).\n"
        f"Watchdog is now alert-only for this episode — manual intervention "
        f"required (the trader will not stay up)."
    )


def decide_autoheal(
    *,
    state: Dict[str, Any],
    stale_streak: int,
    threshold: int,
    max_restarts: int,
    cooldown_s: float,
    now: float,
    seconds_since_active: Optional[float],
    startup_grace_s: float,
) -> Dict[str, Any]:
    """Pure decision over streak + prior autoheal bookkeeping.

    Mirrors ``check_ib_gateway.decide``: enforces a per-episode restart
    cap (then a one-shot EXHAUSTED escalation), a cooldown between
    restarts, and a startup-grace so we never bounce a trader that has
    only just (re)started. Returns
    ``{action, new_state}`` where ``action`` ∈
    ``{"none", "restart", "exhausted"}``.

    Bookkeeping (``autoheal_attempts`` / ``last_autoheal_ts``) is NOT
    advanced here — the effecting caller advances it ONLY when a restart
    actually dispatches, so a restart that fails to dispatch (e.g.
    systemctl timing out under CPU saturation — the 2026-06-09 failure
    mode, BL-20260609-001) does not burn an attempt or start a cooldown,
    and the watchdog retries on the next check instead of going quiet.
    """
    s = dict(state)
    if threshold <= 0 or stale_streak < threshold:
        return {"action": "none", "new_state": s}
    attempts = int(s.get("autoheal_attempts") or 0)
    if attempts >= max_restarts:
        if not s.get("autoheal_exhausted_alerted"):
            s["autoheal_exhausted_alerted"] = True
            return {"action": "exhausted", "new_state": s}
        return {"action": "none", "new_state": s}
    if now - float(s.get("last_autoheal_ts") or 0.0) < cooldown_s:
        return {"action": "none", "new_state": s}
    if (
        startup_grace_s > 0
        and seconds_since_active is not None
        and seconds_since_active < startup_grace_s
    ):
        # Trader (re)started very recently; it is still completing startup +
        # its first tick and hasn't written the first heartbeat yet. Don't
        # kill it mid-first-tick (BL-20260605-001 part b).
        return {"action": "none", "new_state": s}
    return {"action": "restart", "new_state": s}


def render_alert(action: str, age_s: Optional[float], hb_path: Path) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if action == "missing":
        return (
            f"[CRITICAL] Trader heartbeat missing\n"
            f"{hb_path} not found.\n"
            f"Detected {ts}. Trader may not have started."
        )
    if action == "stale":
        if age_s is None:
            return f"[CRITICAL] Trader heartbeat stale (age unknown) at {ts}."
        m = int(age_s // 60)
        return (
            f"[CRITICAL] Trader heartbeat stale\n"
            f"Last beat {m}m ago (>{m}m threshold). Detected {ts}.\n"
            f"Process may be stuck or dead."
        )
    if action == "recovered":
        return (
            f"[OK] Trader heartbeat recovered\n"
            f"Resumed at {ts}. Latest beat is fresh."
        )
    return ""


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("TICK_INTERVAL_SECONDS", "900")),
        help="Tick interval in seconds (default: %(default)s).",
    )
    p.add_argument(
        "--grace",
        type=float,
        default=float(os.environ.get("HEARTBEAT_GRACE_FACTOR", "2.0")),
        help="Grace multiplier on the tick interval (default: %(default)s).",
    )
    p.add_argument(
        "--heartbeat",
        type=Path,
        default=Path(os.environ.get("HEARTBEAT_FILE", str(DEFAULT_HEARTBEAT))),
    )
    p.add_argument(
        "--state",
        type=Path,
        default=Path(os.environ.get("HEARTBEAT_STATE", str(DEFAULT_STATE))),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate but do not send Telegram or update state.",
    )
    p.add_argument(
        "--auto-restart-after",
        type=int,
        default=int(os.environ.get("LIVENESS_AUTO_RESTART_AFTER", "0")),
        help=(
            "Consecutive stale checks before dispatching "
            "`sudo -n systemctl restart ict-trader-live.service`. "
            "0 = alert-only (default)."
        ),
    )
    p.add_argument(
        "--restart-unit",
        type=str,
        default=os.environ.get(
            "LIVENESS_RESTART_UNIT", "ict-trader-live.service"
        ),
        help="systemd unit to restart on autoheal (default: %(default)s).",
    )
    p.add_argument(
        "--max-restarts",
        type=int,
        default=int(os.environ.get("LIVENESS_MAX_RESTARTS", "5")),
        help=(
            "Max autoheal restarts per stall episode before the watchdog "
            "stops restarting and escalates to a one-shot [CRITICAL] "
            "EXHAUSTED alert (then alert-only until the heartbeat recovers). "
            "Mirrors check_ib_gateway.py's --max-restarts so a trader that "
            "will not stay up cannot become an unbounded restart loop "
            "(BL-20260605-001). The episode counter resets when the "
            "heartbeat goes fresh again. Default: %(default)s."
        ),
    )
    p.add_argument(
        "--cooldown-min",
        type=float,
        default=float(os.environ.get("LIVENESS_COOLDOWN_MIN", "3")),
        help=(
            "Minimum minutes between autoheal restarts. A trader restart is "
            "cheap/safe (no broker lockout risk, unlike IB) so this is "
            "shorter than the IB watchdog's 20 min, but still spaces "
            "successive restarts so we re-observe the trader between bounces. "
            "Default: %(default)s."
        ),
    )
    p.add_argument(
        "--restart-startup-grace-seconds",
        type=int,
        default=int(
            os.environ.get("LIVENESS_RESTART_STARTUP_GRACE_SECONDS", "180")
        ),
        help=(
            "Do NOT autoheal-restart the trader if it (re)started more "
            "recently than this many seconds ago — it is still completing "
            "startup + its first tick and has not had a chance to write the "
            "first heartbeat yet. Prevents the kill-a-trader-mid-first-tick "
            "restart loop (BL-20260605-001 part b). Read from the unit's "
            "ActiveEnterTimestampMonotonic via `systemctl show`; fail-open "
            "(if that read fails we do NOT block the restart). 0 = disabled. "
            "Default: %(default)s."
        ),
    )
    p.add_argument(
        "--boot-grace-seconds",
        type=int,
        default=int(os.environ.get("LIVENESS_BOOT_GRACE_SECONDS", "0")),
        help=(
            "Suppress heartbeat missing/stale alerts (and autoheal) for the "
            "first N seconds after a host boot, so a VM reboot doesn't spam "
            "'[CRITICAL] heartbeat stale' while the trader is still starting. "
            "While suppressed the watchdog stays silent (the reboot already "
            "had its own ping) and emits no 'recovered' ping once the trader "
            "comes back. If the heartbeat is STILL stale once the window "
            "closes, it alerts as a genuine failure-to-recover. "
            "0 = disabled (default)."
        ),
    )
    args = p.parse_args(argv)

    decision = evaluate(
        heartbeat_path=args.heartbeat,
        state_path=args.state,
        tick_interval_s=args.interval,
        grace_factor=args.grace,
    )
    action = decision["action"]
    age_s = decision["age_s"]
    state = dict(decision["state"])

    # Track consecutive-stale streak independent of alert deduping. A
    # heartbeat older than threshold OR a missing file increments the
    # streak even when ``evaluate`` returns ``action == "ok"`` for
    # alert-dedup reasons ("already alerted"). A fresh heartbeat resets
    # it. This decouples the autoheal threshold from "have we already
    # pinged for this stall."
    threshold_s = args.interval * args.grace
    is_stale = (
        age_s is None  # missing
        or (age_s is not None and age_s > threshold_s)
    )
    stale_streak = int(state.get("stale_streak") or 0)
    stale_streak = stale_streak + 1 if is_stale else 0
    state["stale_streak"] = stale_streak

    # A fresh heartbeat ends the stall episode → reset the autoheal restart
    # budget so the NEXT stall gets a full --max-restarts allowance and the
    # cooldown/exhausted gates don't carry over from a prior episode.
    if not is_stale:
        state["autoheal_attempts"] = 0
        state["autoheal_exhausted_alerted"] = False
        state["last_autoheal_ts"] = 0.0

    if action == "ok":
        # No new alert needed. Autoheal can still fire here — alert
        # dedup must not block escalation. If autoheal hasn't fired
        # (or last fire was at a lower streak by at least `threshold`),
        # restart now. Then persist state once at the end so both the
        # streak counter and any autoheal metadata land in the same
        # write.
        _maybe_autoheal(
            args=args, state=state, stale_streak=stale_streak, age_s=age_s,
        )
        if state != decision["state"]:
            save_state(args.state, state)
        return 0

    # Boot-grace suppression. Right after a host reboot the trader is
    # expected to be (re)starting under systemd, so a stale/missing
    # heartbeat is normal boot noise — not a fault. For the grace window
    # we suppress the alert AND the autoheal, but keep counting the
    # streak so a genuine failure-to-start escalates the instant the
    # window closes. We deliberately do NOT set last_status="stale"
    # here, so when the trader comes up no spurious "recovered" ping
    # fires: the operator hears the reboot ping (sent elsewhere) and,
    # only if the trader fails to return, the post-grace alert.
    if action in {"stale", "missing"} and args.boot_grace_seconds > 0:
        uptime = _system_uptime_s()
        if uptime < args.boot_grace_seconds:
            print(
                f"[boot-grace] suppressing '{action}' alert: uptime "
                f"{int(uptime)}s < grace {args.boot_grace_seconds}s "
                f"(trader expected to be starting after reboot)"
            )
            state["last_boot_grace_ts"] = datetime.now(timezone.utc).isoformat()
            if not args.dry_run:
                save_state(args.state, state)  # persist the streak counter
            return 0

    msg = render_alert(action, age_s, args.heartbeat)
    print(msg)

    if args.dry_run:
        return 0

    sent = send_alert(msg)
    if not sent:
        return 2

    if action in {"stale", "missing"}:
        state["last_status"] = "stale"
        state["last_alert_age_s"] = age_s
        state["last_alert_ts"] = datetime.now(timezone.utc).isoformat()
    elif action == "recovered":
        state["last_status"] = "recovered"
        state["last_alert_age_s"] = None
        state["last_alert_ts"] = datetime.now(timezone.utc).isoformat()
        state["last_autoheal_streak"] = 0  # so next stall can autoheal again

    _maybe_autoheal(
        args=args, state=state, stale_streak=stale_streak, age_s=age_s,
    )
    save_state(args.state, state)
    return 0


def _maybe_autoheal(
    *,
    args: argparse.Namespace,
    state: Dict[str, Any],
    stale_streak: int,
    age_s: Optional[float],
) -> None:
    """Fire ``systemctl restart`` if eligible, with cap + cooldown + grace.

    Mutates ``state`` in place. ``--auto-restart-after 0`` disables this
    entirely. The capped/cooled/grace-gated eligibility is decided by the
    pure ``decide_autoheal``; this wrapper performs the side effects:
      * action ``"restart"`` → dispatch ``systemctl restart`` and, ONLY if
        the dispatch actually ran, advance ``autoheal_attempts`` +
        ``last_autoheal_ts`` (so a dispatch that fails under CPU
        saturation does not burn an attempt or start a cooldown — it is
        retried next check; BL-20260609-001).
      * action ``"exhausted"`` → one-shot [CRITICAL] EXHAUSTED ping.
    Failures Telegram the operator but never propagate.
    """
    threshold = args.auto_restart_after
    if threshold <= 0 or stale_streak < threshold:
        # Cheap pre-gate: only probe the unit / run the decision once the
        # stall is sustained enough to be restart-eligible. Avoids a
        # `systemctl show` on every healthy tick.
        return

    now_epoch = time.time()
    seconds_since_active = _seconds_since_unit_active(args.restart_unit)
    decision = decide_autoheal(
        state=state,
        stale_streak=stale_streak,
        threshold=threshold,
        max_restarts=args.max_restarts,
        cooldown_s=args.cooldown_min * 60.0,
        now=now_epoch,
        seconds_since_active=seconds_since_active,
        startup_grace_s=float(args.restart_startup_grace_seconds),
    )
    state.update(decision["new_state"])
    action = decision["action"]

    if action == "exhausted":
        msg = render_autoheal_exhausted(
            args.restart_unit, args.max_restarts, stale_streak, age_s
        )
        print(msg)
        send_alert(msg)  # best-effort
        return
    if action != "restart":
        return

    result = try_autoheal_restart(args.restart_unit)
    autoheal_msg = render_autoheal_alert(
        args.restart_unit, result, stale_streak, age_s
    )
    print(autoheal_msg)
    send_alert(autoheal_msg)  # best-effort
    state["last_autoheal_returncode"] = result.get("returncode")
    state["last_autoheal_attempt_ts"] = datetime.now(timezone.utc).isoformat()
    if result.get("ran"):
        # Only a dispatched restart consumes an attempt + starts the
        # cooldown. A failed dispatch (subprocess error / timeout under CPU
        # load) is retried on the next check rather than going silent.
        state["autoheal_attempts"] = int(state.get("autoheal_attempts") or 0) + 1
        state["last_autoheal_ts"] = now_epoch
        state["last_autoheal_ts_iso"] = datetime.now(timezone.utc).isoformat()
        state["last_autoheal_streak"] = stale_streak


if __name__ == "__main__":
    raise SystemExit(main())
