#!/usr/bin/env python3
"""Alert on a failed systemd unit (FIX-SA-08). Fired by ict-notify-failure@.service.

Always appends one row to ``runtime_logs/unit_failures.jsonl`` and enqueues one
operator ping, rate-limited to one per unit per ``COOLDOWN_S`` so a timer that
fails every minute cannot become alarm fatigue. Never raises: an alert path that
can itself fail loudly would mask the failure it exists to report.

Usage: notify_unit_failure.py <unit-name>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

COOLDOWN_S = 6 * 3600
_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")


def _logs_dir() -> Path:
    try:
        from src.utils.paths import runtime_logs_dir
        return runtime_logs_dir()
    except Exception:  # noqa: BLE001
        return REPO_ROOT / "runtime_logs"


def _unit_facts(unit: str) -> dict[str, str]:
    """Result / exit status as systemd holds them. Empty dict = could not look."""
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", "Result", "-p", "ExecMainStatus"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except Exception:  # noqa: BLE001
        return {}
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def should_ping(state: dict, unit: str, now: float, cooldown_s: int = COOLDOWN_S) -> bool:
    return now - float(state.get(unit, 0) or 0) >= cooldown_s


def run(unit: str, now: float | None = None, logs: Path | None = None) -> str:
    """Returns 'pinged' | 'suppressed'. Raises nothing the caller must handle."""
    now = time.time() if now is None else now
    logs = logs or _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    facts = _unit_facts(unit)
    state_path = logs / "unit_failure_notify_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        state = {}
    ping = should_ping(state, unit, now)
    with (logs / "unit_failures.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now, "unit": unit, "facts": facts,
                             "pinged": ping}, sort_keys=True) + "\n")
    if not ping:
        return "suppressed"
    state[unit] = now
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    from scripts.send_ping import enqueue
    enqueue(
        f"[WARN] systemd unit FAILED: {unit} "
        f"(Result={facts.get('Result', '?')}, exit={facts.get('ExecMainStatus', '?')}). "
        f"Its timer keeps firing, so the timer alone will not show this. "
        f"Read: journalctl?unit={unit.removesuffix('.service')} via diag.",
        priority="high",
    )
    return "pinged"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not _UNIT_RE.match(argv[0]):
        print("usage: notify_unit_failure.py <unit-name>", file=sys.stderr)
        return 0
    try:
        print(run(argv[0]))
    except Exception as exc:  # noqa: BLE001
        print(f"notify_unit_failure: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
