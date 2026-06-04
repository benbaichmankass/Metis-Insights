"""Tests for scripts/write_health_snapshot.py (BL-20260529-005 revival).

Covers the writer's payload shape + the round-trip contract with the two
readers it must satisfy:
  - src/web/api/routers/health_snapshots.py  (the /api/bot/health/* API)
  - src/runtime/insights/template_analyst.py (the M13 `health` card)

The key regression these guard: a healthy live system must render a fresh,
GREEN health card — not the 2026-05-11-frozen FALSE "concern" the dead
writer left behind.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.runtime.health import HealthCheck
from src.runtime.insights.template_analyst import health_template

# Mirrors src/web/api/routers/health_snapshots.py::_HISTORY_PATTERN — the
# history-file contract the /api/bot/health/history parser enforces. Inlined
# (not imported) so this writer unit test doesn't drag in the fastapi web
# stack; if the router's pattern changes, this test must change with it.
_HISTORY_PATTERN = re.compile(r"^health_check_(\d{8}T\d{6}Z)\.json$")

# Load the script module by path (scripts/ is not a package).
_SPEC = importlib.util.spec_from_file_location(
    "write_health_snapshot",
    Path(__file__).resolve().parent.parent / "scripts" / "write_health_snapshot.py",
)
whs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(whs)


def _checks(*specs):
    """specs: (name, status, detail) tuples -> a run_all_checks() stand-in."""
    return [HealthCheck(name=n, status=s, detail=d) for n, s, d in specs]


def test_payload_all_ok(monkeypatch):
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("heartbeat", "ok", "fresh"), ("db", "ok", "reachable")),
    )
    p = whs.build_payload(datetime(2026, 6, 4, 6, 30, tzinfo=timezone.utc))
    assert p["status"] == "ok"
    assert p["action_required"] is False
    assert set(p["checks"]) == {"heartbeat", "db"}
    assert p["checks"]["heartbeat"] == {"status": "ok", "detail": "fresh"}
    assert p["summary"].startswith("2/2 checks ok")


def test_payload_warn_is_watch_not_concern(monkeypatch):
    # A degraded-but-running check => overall "watch", action NOT required.
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("heartbeat", "ok", "fresh"), ("git_drift", "warn", "1 commit behind")),
    )
    p = whs.build_payload()
    assert p["status"] == "watch"
    assert p["action_required"] is False


def test_payload_critical_is_concern(monkeypatch):
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("trader", "critical", "service dead"), ("db", "ok", "ok")),
    )
    p = whs.build_payload()
    assert p["status"] == "concern"
    assert p["action_required"] is True
    assert "trader" in p["summary"]


def test_write_then_read_back(monkeypatch, tmp_path):
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("heartbeat", "ok", "fresh"), ("disk", "ok", "21%")),
    )
    now = datetime(2026, 6, 4, 6, 30, 15, tzinfo=timezone.utc)
    payload = whs.build_payload(now)
    health_dir = tmp_path / "health"
    latest, hist = whs.write_snapshot(payload, now, health_dir)

    # latest.json round-trips
    assert json.loads(latest.read_text()) == payload
    # history filename matches the API's parser exactly
    assert _HISTORY_PATTERN.match(hist.name), hist.name
    assert hist.name == "health_check_20260604T063015Z.json"
    # text tail exists + names every check
    txt = (health_dir / "health_snapshot.txt").read_text()
    assert "heartbeat" in txt and "disk" in txt


def test_m13_card_grades_green_on_healthy(monkeypatch, tmp_path):
    """The whole point: a healthy snapshot makes the M13 health card green."""
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("heartbeat", "ok", "fresh"), ("db", "ok", "ok"), ("ticks", "ok", "fresh")),
    )
    now = datetime.now(timezone.utc)
    payload = whs.build_payload(now)
    health_dir = tmp_path / "health"
    whs.write_snapshot(payload, now, health_dir)

    # Shape the input the M13 generator's health_template consumes
    # (mirrors data_sources.health_data(): fresh mtime => small age).
    data = {
        "rows": {"snapshot": json.loads((health_dir / "latest.json").read_text())},
        "meta": {"present": True, "age_seconds": 30},
    }
    out = health_template(data)
    # Green health card: grade "good" (vocab matches the other endpoints),
    # no stale/failing signals, all checks counted ok. Before the fix this
    # was a permanent "concern" off the 2026-05-11-frozen snapshot.
    assert out["grade"] == "good", out["summary_md"]
    assert not any(s["kind"] == "stale_snapshot" for s in out["signals"])
    assert not any(s["kind"] == "health_failing" for s in out["signals"])
    assert "3 / 3" in out["summary_md"]


def _card(payload, age_seconds=30, present=True):
    return health_template({"rows": {"snapshot": payload}, "meta": {"present": present, "age_seconds": age_seconds}})


def test_m13_card_concern_only_on_critical(monkeypatch):
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("trader", "critical", "service dead"), ("db", "ok", "ok")),
    )
    out = _card(whs.build_payload())
    assert out["grade"] == "concern", out["summary_md"]


def test_m13_card_warn_is_watch_not_concern(monkeypatch):
    # A benign warn must NOT scream "concern" (the old _grade did).
    monkeypatch.setattr(
        whs, "run_all_checks",
        lambda: _checks(("git_drift", "warn", "1 commit behind"), ("db", "ok", "ok")),
    )
    out = _card(whs.build_payload())
    assert out["grade"] == "watch", out["summary_md"]


def test_m13_card_stale_is_watch(monkeypatch):
    monkeypatch.setattr(
        whs, "run_all_checks", lambda: _checks(("db", "ok", "ok")),
    )
    out = _card(whs.build_payload(), age_seconds=7200)  # >1h
    assert out["grade"] == "watch"
    assert any(s["kind"] == "stale_snapshot" for s in out["signals"])


def test_prune_history_drops_old(tmp_path):
    health_dir = tmp_path / "health"
    health_dir.mkdir(parents=True)
    fresh = health_dir / "health_check_20260604T060000Z.json"
    old = health_dir / "health_check_20260501T060000Z.json"
    fresh.write_text("{}")
    old.write_text("{}")
    removed = whs.prune_history(health_dir, datetime(2026, 6, 4, 6, 30, tzinfo=timezone.utc))
    assert removed == 1
    assert fresh.exists() and not old.exists()
