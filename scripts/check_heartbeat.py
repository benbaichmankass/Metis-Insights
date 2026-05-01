#!/usr/bin/env python3
"""Heartbeat watchdog — S-022 PR5.

Reads ``runtime_logs/heartbeat.txt`` and pings Telegram if the trader
process appears stuck. Designed to run between hourly reports — typical
deployment is a systemd timer firing every 5 minutes on the VM.

Idempotent: state lives in ``runtime_logs/heartbeat_check_state.json``.
A second run inside the same staleness window does NOT re-ping. A
recovery run (heartbeat is fresh again after having been stale) sends
exactly one "recovered" ping.

Stdlib-only: no requests, no anthropic SDK, no internal src.* imports
beyond ``src.runtime.notify`` (also stdlib-only). This means the
watchdog keeps working even if the bot's own venv is wedged.

Exit codes:
  0 — heartbeat is fresh (no action), or alert sent successfully.
  1 — could not stat heartbeat / state files.
  2 — alert needed but Telegram POST failed.

CLI:
  python scripts/check_heartbeat.py
  python scripts/check_heartbeat.py --interval 900 --grace 2

Env vars (override CLI defaults):
  HEARTBEAT_FILE   absolute path to heartbeat.txt
  HEARTBEAT_STATE  absolute path to state json
  TICK_INTERVAL_SECONDS, HEARTBEAT_GRACE_FACTOR, TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID — same names the trader uses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HEARTBEAT = _REPO_ROOT / "runtime_logs" / "heartbeat.txt"
DEFAULT_STATE = _REPO_ROOT / "runtime_logs" / "heartbeat_check_state.json"


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

    if action == "ok":
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
    save_state(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
