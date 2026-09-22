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


# ---------------------------------------------------------------------------
# E22 — a SKIPPED account must not read as a PASSING one.
#
# MEASURED 2026-09-22T05:30Z from /api/bot/health/latest: `status: ok`,
# `summary: "7/7 checks ok"`, `action_required: false`, while the accounts_api
# check's own detail read "all 8 recorded broker-API accounts ok (1
# manual-bridge skipped: breakout_1; 2 dry/shelved skipped: ib_live,
# oanda_practice)". `breakout_1` is the funded prop account that had received
# zero tickets for three weeks (row E16, 21 of 21). The exclusion was counted
# as a pass.
#
# These tests key on the CTX lists, not on the detail prose, because the prose
# is a claim about the check and the ctx is the check's own record.
# ---------------------------------------------------------------------------


def _ctx_checks(*specs):
    """specs: (name, status, detail, ctx) -> a run_all_checks() stand-in."""
    return [
        HealthCheck(name=n, status=s, detail=d, ctx=c)
        for n, s, d, c in specs
    ]


def test_skipped_accounts_are_named_in_the_summary_not_folded_into_ok(monkeypatch):
    """The exact 2026-09-22 snapshot must no longer be sayable as a bare all-clear."""
    monkeypatch.setattr(
        whs,
        "run_all_checks",
        lambda: _ctx_checks(
            ("service", "ok", "up", None),
            ("git_drift", "ok", "clean", None),
            ("git_fetch", "ok", "fresh", None),
            ("tick", "ok", "recent", None),
            (
                "accounts_api",
                "ok",
                "all 8 recorded broker-API accounts ok",
                {
                    "total": 8,
                    "skipped": ["breakout_1"],
                    "shelved": ["ib_live", "oanda_practice"],
                    "no_data": [],
                },
            ),
            ("db", "ok", "ok", None),
            ("disk", "ok", "ok", None),
        ),
    )
    p = whs.build_payload(now=datetime(2026, 9, 22, 5, 30, tzinfo=timezone.utc))

    # The count is still honestly 7/7 -- every CHECK passed. What changes is
    # that the summary can no longer stop there.
    assert p["summary"].startswith("7/7 checks ok")
    assert "3 account(s) NOT CHECKED" in p["summary"]
    for acc in ("breakout_1", "ib_live", "oanda_practice"):
        assert acc in p["summary"], f"{acc} must be NAMED, not merely counted"

    assert p["not_checked"]["count"] == 3
    assert p["not_checked"]["accounts"] == ["breakout_1", "ib_live", "oanda_practice"]
    assert p["not_checked"]["by_check"]["accounts_api"] == {
        "skipped": ["breakout_1"],
        "shelved": ["ib_live", "oanda_practice"],
    }
    # `no_data: []` is an empty exclusion and must not invent a key.
    assert "no_data" not in p["not_checked"]["by_check"]["accounts_api"]


def test_status_and_action_required_are_deliberately_unmoved(monkeypatch):
    """A permanently-skipped account must NOT put a standing `watch` on every snapshot.

    This is the refusal, asserted so a later change cannot make it quietly:
    a manual bridge is skipped on every snapshot for its whole life, so
    grading the exclusion would desensitise the one surface the operator
    reads. The remedy for "is that account still being fed?" is the E18
    detector, not a louder all-clear.
    """
    monkeypatch.setattr(
        whs,
        "run_all_checks",
        lambda: _ctx_checks(
            ("service", "ok", "up", None),
            ("accounts_api", "ok", "ok", {"skipped": ["breakout_1"]}),
        ),
    )
    p = whs.build_payload()
    assert p["status"] == "ok"
    assert p["action_required"] is False
    assert p["not_checked"]["count"] == 1


def test_no_data_counts_as_not_checked(monkeypatch):
    """`no_data` is "we could not look" and is the dangerous member.

    The accounts_api check subtracts it from its OWN headline
    (`all {total - len(no_data)} ... ok`), so an account whose reading never
    arrived passes exactly as quietly as one that was never eligible.
    """
    monkeypatch.setattr(
        whs,
        "run_all_checks",
        lambda: _ctx_checks(
            ("accounts_api", "ok", "all 2 recorded broker-API accounts ok",
             {"skipped": [], "shelved": [], "no_data": ["bybit_2"]}),
        ),
    )
    p = whs.build_payload()
    assert p["not_checked"]["accounts"] == ["bybit_2"]
    assert "1 account(s) NOT CHECKED: bybit_2" in p["summary"]


def test_zero_not_checked_is_a_real_measurement_not_a_missing_field(monkeypatch):
    """Nothing excluded must be DISTINGUISHABLE from a check that emitted no ctx.

    The positive control for the negative: the field is always present, so
    `count: 0` says "every account was examined", while a check contributing
    no ctx is visible by its ABSENCE from `by_check` rather than by silence.
    """
    monkeypatch.setattr(
        whs,
        "run_all_checks",
        lambda: _ctx_checks(
            ("accounts_api", "ok", "ok", {"skipped": [], "shelved": [], "no_data": []}),
            ("disk", "ok", "ok", None),
        ),
    )
    p = whs.build_payload()
    assert p["not_checked"] == {"accounts": [], "count": 0, "by_check": {}}
    assert "NOT CHECKED" not in p["summary"]
    assert p["summary"] == "2/2 checks ok"


def test_exclusions_are_deduped_and_unioned_across_checks(monkeypatch):
    """Two checks excluding the same account must count it ONCE."""
    monkeypatch.setattr(
        whs,
        "run_all_checks",
        lambda: _ctx_checks(
            ("accounts_api", "ok", "ok", {"skipped": ["breakout_1"]}),
            ("tick", "ok", "ok", {"no_data": ["breakout_1", "alpaca_live"]}),
        ),
    )
    p = whs.build_payload()
    assert p["not_checked"]["accounts"] == ["alpaca_live", "breakout_1"]
    assert p["not_checked"]["count"] == 2
