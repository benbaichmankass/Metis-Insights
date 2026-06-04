#!/usr/bin/env python3
"""Write the health-snapshot artifacts the dashboard + M13 analyst read.

Revives the JSON producer that the 2026-05-12 ``health-snapshot.yml``
refactor deleted (it removed ``scripts/run_health_check.py`` and turned
the cron into a passive artifact-collector). Since then nothing wrote the
VM's ``artifacts/health/{latest.json,health_check_*.json}`` — so
``/api/bot/health/{latest,history}`` and the M13 ``health`` insight card
have been serving a snapshot frozen at 2026-05-11, surfacing a permanent
FALSE "concern (0/11)" on the dashboard. Root-cause + plan:
health-review backlog ``BL-20260529-005``.

This script is the writer half. It runs ON the live VM via
``ict-health-snapshot.timer`` (every ~15 min), calls the existing
``src.runtime.health.run_all_checks()`` 7-point suite, and writes:

  - ``<artifacts>/health/latest.json``        — newest snapshot (served by /latest)
  - ``<artifacts>/health/health_check_<TS>.json`` — history entry (served by /history)
  - ``<artifacts>/health/health_snapshot.txt``    — human text tail (served by /snapshot)

``<artifacts>`` is resolved through ``src.utils.paths.artifacts_dir()`` —
the SAME resolver ``health_snapshots.py`` (the API) and
``insights/data_sources.py`` (the M13 generator) use to READ — so writer
and readers agree. The unit carries the ``data-dir.conf`` drop-in so it
runs with ``DATA_DIR=/data/bot-data`` (matching ``ict-web-api``); without
it the writer would emit to ``<repo>/artifacts`` while the API reads
``/data/bot-data/artifacts`` — exactly the path-split that caused the
2026-05-12 stale-data incident.

No external/network calls; reads local files, the journal DB, and
``systemctl`` only (each check is internally exception-guarded by
``run_all_checks``).

Payload shape (consumed by the readers):
    {
      "timestamp": "<UTC ISO-8601>",       # -> /history payload_timestamp + M13 data_window.end
      "status": "ok|watch|concern",        # overall, worst-of per-check
      "summary": "<N>/<M> checks ok; ...",
      "action_required": <bool>,           # true iff a critical check failed
      "model": "deterministic:v1",
      "checks": {"<name>": {"status": "ok|warn|critical", "detail": "...", "ctx": {...}}, ...}
    }
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the repo root importable when run as a standalone script
# (systemd ExecStart=/usr/bin/python3 scripts/write_health_snapshot.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.health import run_all_checks  # noqa: E402
from src.utils.paths import artifacts_dir  # noqa: E402

# Per-check statuses that count as "passing" — must match the OK-set in
# src/runtime/insights/template_analyst.py::health_template so the M13 card
# and this writer agree on what "failing" means.
_OK_STATUSES = {"ok", "pass", "running"}

# History-file timestamp format — must match the router's parser
# (src/web/api/routers/health_snapshots.py::_HISTORY_PATTERN
#  = ^health_check_(\d{8}T\d{6}Z)\.json$).
_TS_FMT = "%Y%m%dT%H%M%SZ"

# Bound the history dir: drop health_check_*.json older than this. The
# /history endpoint clamps `hours` to 14 days, so keep a little over that.
_HISTORY_RETENTION = timedelta(days=15)


def build_payload(now: datetime | None = None) -> dict:
    """Run the health suite and assemble the snapshot payload."""
    now = now or datetime.now(timezone.utc)
    checks = run_all_checks()

    check_map: dict[str, dict] = {}
    worst = "ok"  # ok < warn < critical
    for c in checks:
        entry: dict = {"status": c.status, "detail": c.detail}
        if getattr(c, "ctx", None):
            entry["ctx"] = c.ctx
        check_map[c.name] = entry
        if c.status == "critical":
            worst = "critical"
        elif c.status == "warn" and worst != "critical":
            worst = "warn"

    failing = [n for n, e in check_map.items() if e["status"] not in _OK_STATUSES]
    ok_count = len(check_map) - len(failing)
    overall = {"critical": "concern", "warn": "watch", "ok": "ok"}[worst]
    summary = f"{ok_count}/{len(check_map)} checks ok"
    if failing:
        summary += f"; not-ok: {', '.join(failing)}"

    return {
        "timestamp": now.isoformat(),
        "status": overall,
        "summary": summary,
        "action_required": worst == "critical",
        "model": "deterministic:v1",
        "checks": check_map,
    }


def _render_text(payload: dict) -> str:
    lines = [
        f"health snapshot @ {payload['timestamp']}  status={payload['status']}",
        f"summary: {payload['summary']}",
        "",
    ]
    for name, e in payload["checks"].items():
        lines.append(f"[{e['status']:>8}] {name}: {e['detail']}")
    return "\n".join(lines) + "\n"


def write_snapshot(payload: dict, now: datetime, health_dir: Path) -> tuple[Path, Path]:
    """Write latest.json (atomically), the timestamped history file, and the text tail."""
    health_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, default=str)

    latest = health_dir / "latest.json"
    tmp = health_dir / ".latest.json.tmp"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(latest)  # atomic on same filesystem — readers never see a partial file

    hist = health_dir / f"health_check_{now.strftime(_TS_FMT)}.json"
    hist.write_text(body, encoding="utf-8")

    (health_dir / "health_snapshot.txt").write_text(_render_text(payload), encoding="utf-8")
    return latest, hist


def prune_history(health_dir: Path, now: datetime) -> int:
    """Drop health_check_*.json older than the retention window. Returns count removed."""
    cutoff = now - _HISTORY_RETENTION
    removed = 0
    for entry in health_dir.glob("health_check_*.json"):
        stem = entry.name[len("health_check_"):-len(".json")]
        try:
            ts = datetime.strptime(stem, _TS_FMT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        payload = build_payload(now)
    except Exception as exc:  # noqa: BLE001 — never let the suite crash the writer silently
        print(f"write_health_snapshot: run_all_checks failed: {exc}", file=sys.stderr)
        return 1
    health_dir = artifacts_dir() / "health"
    try:
        latest, hist = write_snapshot(payload, now, health_dir)
        pruned = prune_history(health_dir, now)
    except OSError as exc:
        print(f"write_health_snapshot: write failed ({health_dir}): {exc}", file=sys.stderr)
        return 1
    print(
        f"write_health_snapshot: wrote {latest} + {hist.name} "
        f"status={payload['status']} checks={len(payload['checks'])} pruned={pruned}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
