#!/usr/bin/env python3
"""IB Gateway auto-heal watchdog.

Detects the recurring "MES goes dark" wedge — the ``ib-gateway`` container
stays *up* but its IBKR session is dead (data farms ``usfarm`` / ``ushmds`` /
``secdefnj`` broken, every historical-data / account request times out), so
``ib_paper`` collects no MES candles and all MES strategies skip every tick.
Root cause (BL-20260527-003): during IBKR's overnight server reset the
Gateway's in-place re-login can hit a transient "Unrecognized Username or
Password" dialog; IBC parks on it and never retries, and ``restart:
unless-stopped`` never fires because the process does not die — it hangs. The
*only* reliable recovery is a full container restart (``docker restart
ib-gateway``), which re-logins cleanly once IBKR is healthy. This watchdog
automates exactly that recovery so it stops needing a human / a manual
``vm-ib-gateway-recover`` dispatch.

Deployed as ``ict-ib-gateway-watchdog.timer`` (every 5 min on the live VM,
``deploy/ict-ib-gateway-watchdog.{service,timer}``).

Detection
---------
Runs the existing non-mutating probe ``scripts/ib_connect_check.py --json
<account>`` and reads the substantive result, NOT just its exit code: a
logged-out Gateway still reports ``connected=true`` (the local API handshake
succeeds) but ``net_liquidation`` comes back ``None`` because the account /
data read times out upstream. So the Gateway is healthy only when it is
``connected`` AND ``net_liquidation`` is populated.

Recovery (opt-in via ``--auto-restart``)
-----------------------------------------
After ``--restart-after`` consecutive wedged checks, fire
``scripts/ops/restart_ib_gateway.sh`` (``docker restart`` + login probe).
Guard rails so a genuinely-bad-credential or IBKR-lockout situation can NOT
turn into a restart loop (every restart is a fresh IBKR login; too many failed
logins risk locking the account):
  * ``--cooldown-min`` minimum gap between restarts.
  * ``--max-restarts`` cap per wedge episode; once exhausted the watchdog
    stops restarting and alert-only escalates ("manual intervention").
  * Both counters reset when the Gateway recovers.

Telegram alerts (first detection, each restart, recovery, exhaustion) reuse
``src.runtime.notify.send_telegram_direct`` — the same path the liveness
watchdog uses.

Exit codes:
  0 — healthy, or a wedge was handled (alert/restart dispatched).
  2 — an alert was needed but the Telegram POST failed.

CLI:
  python scripts/check_ib_gateway.py --probe-account ib_paper
  python scripts/check_ib_gateway.py --probe-account ib_paper --auto-restart
  python scripts/check_ib_gateway.py --probe-account ib_paper --dry-run
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
    """Resolve the runtime_logs dir the same way the trader does, without
    importing src (so the watchdog runs even if the bot package is broken).
    Order: RUNTIME_LOGS_DIR → $DATA_DIR/runtime_logs → <repo>/runtime_logs.
    """
    override = os.environ.get("RUNTIME_LOGS_DIR")
    if override:
        p = Path(override).expanduser()
        return p if p.is_absolute() else _REPO_ROOT / p
    umbrella = os.environ.get("DATA_DIR")
    if umbrella:
        p = Path(umbrella).expanduser()
        return (p if p.is_absolute() else _REPO_ROOT / p) / "runtime_logs"
    return _REPO_ROOT / "runtime_logs"


DEFAULT_STATE = _resolved_runtime_logs_dir() / "ib_gateway_watchdog_state.json"
DEFAULT_PROBE = _REPO_ROOT / "scripts" / "ib_connect_check.py"
DEFAULT_RESTART = _REPO_ROOT / "scripts" / "ops" / "restart_ib_gateway.sh"


# ---------------------------------------------------------------------------
# State
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
# Telegram (stdlib import path, same as check_heartbeat.py)
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
# Probe — run ib_connect_check.py and classify the result
# ---------------------------------------------------------------------------


def classify_probe(stdout: str) -> Dict[str, Any]:
    """Map an ``ib_connect_check.py --json`` payload to a health verdict.

    Healthy only when the probed account is BOTH ``connected`` AND has a
    populated ``net_liquidation`` — a logged-out Gateway answers the local
    API handshake (``connected=true``) but its upstream account read times
    out (``net_liquidation=None``), which is exactly the wedge signature.

    Returns ``{"healthy": bool, "reason": str}``.
    """
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {"healthy": False, "reason": "probe produced no parseable JSON"}
    results = payload.get("results") or []
    if not results:
        return {"healthy": False, "reason": payload.get("error") or "no probe results"}
    snap = results[0]
    if not snap.get("connected"):
        return {
            "healthy": False,
            "reason": f"connect failed: {snap.get('error') or 'not connected'}",
        }
    if snap.get("net_liquidation") is None:
        return {
            "healthy": False,
            "reason": "API handshake OK but net_liquidation=None — IBKR session/data down",
        }
    return {"healthy": True, "reason": f"net_liquidation={snap.get('net_liquidation')}"}


def run_probe(probe_path: Path, account: str, timeout_s: int) -> Dict[str, Any]:
    """Run the connectivity probe; never raise. Returns classify_probe()."""
    try:
        proc = subprocess.run(
            [sys.executable, str(probe_path), "--json", account],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"healthy": False, "reason": f"probe timed out after {timeout_s}s"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"healthy": False, "reason": f"probe failed to run: {exc}"}
    verdict = classify_probe(proc.stdout)
    if not verdict["healthy"] and not proc.stdout.strip():
        verdict["reason"] = (
            f"probe exited {proc.returncode} with no output: "
            f"{(proc.stderr or '').strip()[-160:]}"
        )
    return verdict


# ---------------------------------------------------------------------------
# Recovery — docker restart via the existing wrapper
# ---------------------------------------------------------------------------


def try_restart(restart_path: Path, timeout_s: int) -> Dict[str, Any]:
    """Run restart_ib_gateway.sh; never raise. Returns
    ``{ran, returncode, login_completed, tail}``.
    """
    try:
        proc = subprocess.run(
            ["bash", str(restart_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "returncode": -1, "login_completed": False,
                "tail": f"restart failed to run: {exc}"}
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ran": True,
        "returncode": proc.returncode,
        "login_completed": ("login_completed=yes" in out) or ("Login has completed" in out),
        "tail": out.strip()[-300:],
    }


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide(
    *,
    healthy: bool,
    state: Dict[str, Any],
    restart_after: int,
    max_restarts: int,
    cooldown_s: float,
    now: float,
    auto_restart: bool,
) -> Dict[str, Any]:
    """Pure decision over current health + prior state.

    Returns ``{action, alert, new_state}`` where ``action`` is one of
    ``"none" | "recovered" | "detected" | "restart" | "exhausted"``.
    ``alert`` is True when the caller should send the message for ``action``.
    With ``auto_restart=False`` the watchdog is alert-only: it detects + warns
    once per episode but never restarts (so no restart bookkeeping advances).
    """
    s = dict(state)
    last_status = s.get("last_status")  # "ok" | "wedged" | None

    if healthy:
        s["wedged_streak"] = 0
        s["restart_attempts"] = 0
        s["exhausted_alerted"] = False
        s["last_status"] = "ok"
        if last_status == "wedged":
            return {"action": "recovered", "alert": True, "new_state": s}
        return {"action": "none", "alert": False, "new_state": s}

    # Wedged.
    streak = int(s.get("wedged_streak") or 0) + 1
    s["wedged_streak"] = streak
    attempts = int(s.get("restart_attempts") or 0)
    s["last_status"] = "wedged"
    first_detection = last_status != "wedged"

    def _detect() -> Dict[str, Any]:
        return {"action": "detected" if first_detection else "none",
                "alert": first_detection, "new_state": s}

    # Alert-only mode, or not yet a sustained wedge → detect/alert, never restart.
    if not auto_restart or streak < restart_after:
        return _detect()

    # Eligible to restart: enforce max-attempts + cooldown so we never loop
    # (every restart is a fresh IBKR login; looping risks an account lockout).
    if attempts >= max_restarts:
        if not s.get("exhausted_alerted"):
            s["exhausted_alerted"] = True
            return {"action": "exhausted", "alert": True, "new_state": s}
        return {"action": "none", "alert": False, "new_state": s}
    if now - float(s.get("last_restart_ts") or 0.0) < cooldown_s:
        return _detect()

    s["restart_attempts"] = attempts + 1
    s["last_restart_ts"] = now
    return {"action": "restart", "alert": True, "new_state": s}


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def render(action: str, *, account: str, reason: str, streak: int,
           attempt: int, max_restarts: int, restart: Optional[Dict[str, Any]]) -> str:
    if action == "recovered":
        return (f"[OK] IB Gateway recovered ({_ts()})\n"
                f"{account} reconnected; MES data flowing again.")
    if action == "detected":
        return (f"[WARN] IB Gateway wedge detected ({_ts()})\n"
                f"{account}: {reason}. MES strategies are skipping ticks.")
    if action == "exhausted":
        return (f"[CRITICAL] IB Gateway auto-heal EXHAUSTED ({_ts()})\n"
                f"{account}: {reason}. {max_restarts} restarts did not recover it — "
                f"manual intervention needed (possible credential/lockout issue).")
    if action == "restart":
        r = restart or {}
        if not r.get("ran"):
            return (f"[CRITICAL] IB Gateway auto-heal restart FAILED to dispatch ({_ts()})\n"
                    f"{account}: {reason} (attempt {attempt}/{max_restarts}).\n{r.get('tail','')}")
        if r.get("login_completed"):
            return (f"[ACTION] IB Gateway auto-healed — restarted ib-gateway ({_ts()})\n"
                    f"{account}: {reason}. Restart attempt {attempt}/{max_restarts}; "
                    f"IBC login completed. MES should resume within ~1 min.")
        return (f"[CRITICAL] IB Gateway restart returned rc={r.get('returncode')} "
                f"without confirming login ({_ts()})\n"
                f"{account}: {reason} (attempt {attempt}/{max_restarts}).\n{r.get('tail','')}")
    return ""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--probe-account", default=os.environ.get("IB_WATCHDOG_ACCOUNT", "ib_paper"))
    p.add_argument("--probe-script", type=Path, default=DEFAULT_PROBE)
    p.add_argument("--restart-script", type=Path, default=DEFAULT_RESTART)
    p.add_argument("--state", type=Path,
                   default=Path(os.environ.get("IB_WATCHDOG_STATE", str(DEFAULT_STATE))))
    p.add_argument("--probe-timeout", type=int,
                   default=int(os.environ.get("IB_WATCHDOG_PROBE_TIMEOUT", "90")))
    p.add_argument("--restart-timeout", type=int, default=210)
    p.add_argument("--restart-after", type=int,
                   default=int(os.environ.get("IB_WATCHDOG_RESTART_AFTER", "2")),
                   help="Consecutive wedged checks before a restart (default 2).")
    p.add_argument("--max-restarts", type=int,
                   default=int(os.environ.get("IB_WATCHDOG_MAX_RESTARTS", "3")),
                   help="Max restarts per wedge episode before alert-only (default 3).")
    p.add_argument("--cooldown-min", type=float,
                   default=float(os.environ.get("IB_WATCHDOG_COOLDOWN_MIN", "20")),
                   help="Minimum minutes between restarts (default 20).")
    p.add_argument("--auto-restart", action="store_true",
                   default=os.environ.get("IB_WATCHDOG_AUTO_RESTART", "").lower()
                   in ("1", "true", "yes"),
                   help="Enable the docker-restart recovery (default off = alert-only).")
    p.add_argument("--dry-run", action="store_true",
                   help="Evaluate + print, but never restart, alert, or write state.")
    args = p.parse_args(argv)

    verdict = run_probe(args.probe_script, args.probe_account, args.probe_timeout)
    healthy = bool(verdict["healthy"])
    reason = verdict["reason"]

    state = load_state(args.state)
    decision = decide(
        healthy=healthy,
        state=state,
        restart_after=args.restart_after,
        max_restarts=args.max_restarts,
        cooldown_s=args.cooldown_min * 60.0,
        now=time.time(),
        auto_restart=args.auto_restart,
    )
    action = decision["action"]
    new_state = decision["new_state"]
    restart_result: Optional[Dict[str, Any]] = None

    print(f"probe: healthy={healthy} reason={reason} action={action}")

    if args.dry_run:
        return 0

    if action == "restart":
        restart_result = try_restart(args.restart_script, args.restart_timeout)

    rc = 0
    if decision["alert"] or action == "restart":
        msg = render(
            action,
            account=args.probe_account,
            reason=reason,
            streak=int(new_state.get("wedged_streak") or 0),
            attempt=int(new_state.get("restart_attempts") or 0),
            max_restarts=args.max_restarts,
            restart=restart_result,
        )
        if msg:
            print(msg)
            if not send_alert(msg):
                rc = 2

    save_state(args.state, new_state)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
