#!/usr/bin/env python3
"""Web-API self-heal watchdog (BL-20260604-003).

``ict-web-api.service`` hosts the FastAPI on :8001 — the read-only surface
the dashboard + the PM-side diag relays depend on. When it crashes there is
no VM-side recovery: the only autonomous lever is the GitHub-dispatched
``vm-web-api-recover`` workflow, which is subject to the issues.opened
webhook batch-drop (BL-20260527-001) exactly when the read surface is down.
This watchdog closes that loop on the VM, with no GitHub dependency.

Fires every ~2 min (``ict-web-api-watchdog.timer``). Probes the LOCAL
health endpoint ``http://127.0.0.1:8001/api/health``; after
``--restart-after`` consecutive failures it runs
``scripts/ops/restart_web_api.sh`` (systemctl restart + health probe),
guarded by ``--max-restarts`` per episode + ``--cooldown-min`` so a genuine
crashloop alert-escalates instead of looping. Alerts go through
``src.runtime.notify.send_telegram_direct`` — the same path the liveness +
IB-gateway watchdogs use.

Blast radius: ict-web-api serves the dashboard + /api/diag/* + /api/bot/*
reads only. It does NOT execute trades or touch strategy state — restarting
it bounces dashboard polling for a few seconds and nothing else. The
decision state machine (``decide``) is byte-identical to
``scripts/check_ib_gateway.py`` so behaviour is proven.

Stdlib-only (urllib for the probe) so it works even when the trader venv is
wedged.

Exit codes:
  0 — healthy, or a wedge was handled (alert/restart dispatched).
  2 — a wedge needed an alert but Telegram delivery failed.

Usage:
  python scripts/check_web_api.py
  python scripts/check_web_api.py --auto-restart
  python scripts/check_web_api.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _resolved_runtime_logs_dir() -> Path:
    """runtime_logs root, honouring DATA_DIR like the trader's path helpers."""
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "runtime_logs"
    return _REPO_ROOT / "runtime_logs"


DEFAULT_STATE = _resolved_runtime_logs_dir() / "web_api_watchdog_state.json"
DEFAULT_RESTART = _REPO_ROOT / "scripts" / "ops" / "restart_web_api.sh"
DEFAULT_HEALTH_URL = os.environ.get("WEBAPI_WATCHDOG_URL", "http://127.0.0.1:8001/api/health")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state(path: Path) -> Dict[str, Any]:
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


def send_alert(message: str) -> bool:
    try:
        from src.runtime.notify import send_telegram_direct

        send_telegram_direct(message)
        return True
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the watchdog
        print(f"WARN: alert delivery failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
def run_probe(url: str, timeout_s: int) -> Dict[str, Any]:
    """GET the local health endpoint. Healthy iff HTTP 200. Never raises."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — fixed localhost URL
            code = resp.getcode()
            if code == 200:
                return {"healthy": True, "reason": f"HTTP 200 from {url}"}
            return {"healthy": False, "reason": f"HTTP {code} from {url}"}
    except urllib.error.HTTPError as exc:
        return {"healthy": False, "reason": f"HTTP {exc.code} from {url}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"healthy": False, "reason": f"no response from {url}: {exc}"}


# ---------------------------------------------------------------------------
# Decision (byte-identical to scripts/check_ib_gateway.py::decide)
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

    action ∈ "none" | "recovered" | "detected" | "restart" | "exhausted".
    With auto_restart=False the watchdog is alert-only.
    """
    s = dict(state)
    last_status = s.get("last_status")

    if healthy:
        s["wedged_streak"] = 0
        s["restart_attempts"] = 0
        s["exhausted_alerted"] = False
        s["last_status"] = "ok"
        if last_status == "wedged":
            return {"action": "recovered", "alert": True, "new_state": s}
        return {"action": "none", "alert": False, "new_state": s}

    streak = int(s.get("wedged_streak") or 0) + 1
    s["wedged_streak"] = streak
    attempts = int(s.get("restart_attempts") or 0)
    s["last_status"] = "wedged"
    first_detection = last_status != "wedged"

    def _detect() -> Dict[str, Any]:
        return {"action": "detected" if first_detection else "none",
                "alert": first_detection, "new_state": s}

    if not auto_restart or streak < restart_after:
        return _detect()

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
# Restart + messages
# ---------------------------------------------------------------------------
def try_restart(restart_script: Path, timeout_s: int) -> Dict[str, Any]:
    """Run restart_web_api.sh (systemctl restart + health probe). Never raises."""
    if not restart_script.exists():
        return {"ran": False, "returncode": None, "tail": f"restart script missing: {restart_script}"}
    try:
        proc = subprocess.run(
            ["bash", str(restart_script)],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ran": True, "returncode": None, "recovered": False,
                "tail": f"restart_web_api.sh timed out after {timeout_s}s"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "returncode": None, "tail": f"restart failed to run: {exc}"}
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
    return {"ran": True, "returncode": proc.returncode, "recovered": proc.returncode == 0, "tail": tail}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def render(action: str, *, reason: str, attempt: int, max_restarts: int,
           restart: Optional[Dict[str, Any]]) -> str:
    if action == "recovered":
        return f"[OK] ict-web-api recovered ({_ts()})\nHealth endpoint responding again; dashboard + diag surface restored."
    if action == "detected":
        return (f"[WARN] ict-web-api wedge detected ({_ts()})\n"
                f"{reason}. Dashboard + diag relays are blind until it recovers.")
    if action == "exhausted":
        return (f"[CRITICAL] ict-web-api auto-heal EXHAUSTED ({_ts()})\n"
                f"{reason}. {max_restarts} restarts did not recover it — manual intervention needed.")
    if action == "restart":
        r = restart or {}
        if not r.get("ran"):
            return (f"[CRITICAL] ict-web-api auto-heal restart FAILED to dispatch ({_ts()})\n"
                    f"{reason} (attempt {attempt}/{max_restarts}).\n{r.get('tail', '')}")
        if r.get("recovered"):
            return (f"[ACTION] ict-web-api auto-healed — restarted the service ({_ts()})\n"
                    f"{reason}. Restart attempt {attempt}/{max_restarts}; health probe passed.")
        return (f"[CRITICAL] ict-web-api restart returned rc={r.get('returncode')} "
                f"without a passing health probe ({_ts()})\n"
                f"{reason} (attempt {attempt}/{max_restarts}).\n{r.get('tail', '')}")
    return ""


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=DEFAULT_HEALTH_URL)
    p.add_argument("--restart-script", type=Path, default=DEFAULT_RESTART)
    p.add_argument("--state", type=Path,
                   default=Path(os.environ.get("WEBAPI_WATCHDOG_STATE", str(DEFAULT_STATE))))
    p.add_argument("--probe-timeout", type=int,
                   default=int(os.environ.get("WEBAPI_WATCHDOG_PROBE_TIMEOUT", "10")))
    p.add_argument("--restart-timeout", type=int, default=120)
    p.add_argument("--restart-after", type=int,
                   default=int(os.environ.get("WEBAPI_WATCHDOG_RESTART_AFTER", "2")),
                   help="Consecutive failed probes before a restart (default 2).")
    p.add_argument("--max-restarts", type=int,
                   default=int(os.environ.get("WEBAPI_WATCHDOG_MAX_RESTARTS", "3")),
                   help="Max restarts per wedge episode before alert-only (default 3).")
    p.add_argument("--cooldown-min", type=float,
                   default=float(os.environ.get("WEBAPI_WATCHDOG_COOLDOWN_MIN", "10")),
                   help="Minimum minutes between restarts (default 10).")
    p.add_argument("--auto-restart", action="store_true",
                   default=os.environ.get("WEBAPI_WATCHDOG_AUTO_RESTART", "").lower()
                   in ("1", "true", "yes"),
                   help="Enable the systemctl-restart recovery (default off = alert-only).")
    p.add_argument("--dry-run", action="store_true",
                   help="Evaluate + print, but never restart, alert, or write state.")
    args = p.parse_args(argv)

    verdict = run_probe(args.url, args.probe_timeout)
    healthy = bool(verdict["healthy"])
    reason = verdict["reason"]

    state = load_state(args.state)
    decision = decide(
        healthy=healthy, state=state,
        restart_after=args.restart_after, max_restarts=args.max_restarts,
        cooldown_s=args.cooldown_min * 60.0, now=time.time(), auto_restart=args.auto_restart,
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
            action, reason=reason,
            attempt=int(new_state.get("restart_attempts") or 0),
            max_restarts=args.max_restarts, restart=restart_result,
        )
        if msg:
            print(msg)
            if not send_alert(msg):
                rc = 2

    save_state(args.state, new_state)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
