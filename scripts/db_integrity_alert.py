#!/usr/bin/env python3
"""DB-integrity alerter — the cron wire for the Phase-4 guardrail.

Runs the read-only ``scripts/check_db_integrity.py`` checker and, when it
reports a RECENT write-path regression (``any_alert``), pings the operator
with a ``[WARN] DB integrity: …`` Telegram message — matching the repo's
bracketed-severity alert idiom (``check_heartbeat.py`` /
``daily_heartbeat.py``), NOT a new mechanism.

Deployed as ``ict-db-integrity.timer`` (hourly) on the live VM
(``deploy/ict-db-integrity.{service,timer}``). **Installing the unit is a
Tier-2 live-VM step** — this script + unit ship inert in the repo; they only
alert once the timer is enabled on the VM.

Idempotent dedup: state lives in
``runtime_logs/db_integrity_alert_state.json``. The alerting set is
fingerprinted (the sorted ``id:recent_count`` pairs); an unchanged
fingerprint inside the same run does NOT re-ping. When the alert clears
(``any_alert`` goes false after having been true) it sends exactly one
``[OK] DB integrity: recovered`` ping.

Reuses ``src.runtime.notify.send_telegram_direct`` (the same FCM-mirrored
chokepoint the rest of the bot uses) so the ping fans out to the phone too.

Exit codes:
  0 — ran (clean, or alert/recovery sent successfully).
  1 — could not read DB / state.
  2 — alert needed but the Telegram POST failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import check_db_integrity as cdi  # noqa: E402  — sibling script, same dir on sys.path

# Re-add scripts/ to sys.path so `import check_db_integrity` resolves when run
# from an arbitrary CWD under systemd.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _state_path() -> Path:
    """``runtime_logs/db_integrity_alert_state.json`` via the canonical root."""
    try:
        from src.utils.paths import runtime_logs_dir

        return runtime_logs_dir() / "db_integrity_alert_state.json"
    except Exception:  # noqa: BLE001 — fall back to repo-relative if paths import fails
        return _REPO_ROOT / "runtime_logs" / "db_integrity_alert_state.json"


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"WARN: could not save state {path}: {exc}", file=sys.stderr)


def _fingerprint(report: Dict[str, Any]) -> str:
    """Stable fingerprint of the alerting set (id:recent_count pairs)."""
    alerts = sorted(
        f"{c['id']}:{c['recent_count']}"
        for c in report.get("checks", [])
        if c.get("alert")
    )
    return ",".join(alerts)


def _send(message: str) -> bool:
    try:
        from src.runtime.notify import send_telegram_direct

        send_telegram_direct(message, parse_mode=None)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Telegram POST failed: {exc}", file=sys.stderr)
        return False


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None, help="DB path override (tests).")
    p.add_argument("--window-hours", type=float, default=cdi.DEFAULT_WINDOW_HOURS)
    p.add_argument("--pnl-grace-hours", type=float, default=cdi.DEFAULT_PNL_GRACE_HOURS)
    p.add_argument("--state", type=Path, default=None, help="State path override (tests).")
    p.add_argument("--dry-run", action="store_true",
                   help="Evaluate + print but never POST Telegram or write state.")
    args = p.parse_args(argv)

    db_path = cdi._resolve_db_path(args.db)
    try:
        report = cdi.run_checks(
            db_path,
            window_hours=args.window_hours,
            pnl_grace_hours=args.pnl_grace_hours,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"db_integrity_alert: check failed ({db_path}): {exc}", file=sys.stderr)
        return 1

    print(cdi.render_summary(report), file=sys.stderr)

    state_path = args.state or _state_path()
    state = _load_state(state_path)
    last_alerting = bool(state.get("last_alerting"))
    last_fp = state.get("last_fingerprint") or ""
    fp = _fingerprint(report)

    if report["any_alert"]:
        if last_alerting and fp == last_fp:
            print("db_integrity_alert: same alert fingerprint; not re-pinging.")
            return 0
        msg = cdi.build_alert_message(report)
        print(msg)
        if args.dry_run:
            return 0
        if not _send(msg):
            return 2
        state.update({
            "last_alerting": True,
            "last_fingerprint": fp,
            "last_alert_ts": datetime.now(timezone.utc).isoformat(),
        })
        _save_state(state_path, state)
        return 0

    # Clean now. Send one recovery ping if we were alerting.
    if last_alerting:
        msg = "[OK] DB integrity: recovered — no recent write-path regressions."
        print(msg)
        if not args.dry_run:
            _send(msg)  # best-effort; recovery ping failure isn't fatal
            state.update({
                "last_alerting": False,
                "last_fingerprint": "",
                "last_recovered_ts": datetime.now(timezone.utc).isoformat(),
            })
            _save_state(state_path, state)
        return 0

    print("db_integrity_alert: clean, no prior alert; nothing to send.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
