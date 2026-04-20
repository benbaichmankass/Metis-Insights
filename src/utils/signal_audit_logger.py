from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE = Path(__file__).resolve().parents[2] / "runtime_logs"
BASE.mkdir(parents=True, exist_ok=True)
SIGNAL_FILE = BASE / "signal_audit.jsonl"
SUMMARY_FILE = BASE / "summary_markers.json"


def log_signal(event: Dict[str, Any]) -> None:
    payload = dict(event or {})
    payload.setdefault("logged_at_utc", datetime.now(timezone.utc).isoformat())
    with SIGNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def _load_state() -> Dict[str, str]:
    if SUMMARY_FILE.exists():
        try:
            return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: Dict[str, str]) -> None:
    SUMMARY_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_send_summary(now_utc: datetime) -> bool:
    now_utc = now_utc.astimezone(timezone.utc)
    if now_utc.hour == 7:
        slot = f"{now_utc.date()}-07"
    elif now_utc.hour == 19:
        slot = f"{now_utc.date()}-19"
    else:
        return False
    state = _load_state()
    if state.get("last_slot") == slot:
        return False
    state["last_slot"] = slot
    _save_state(state)
    return True
