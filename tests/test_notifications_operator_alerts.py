"""Operator-alert banner feed (2026-07-08).

The trader's ``execution_diagnostics.enqueue_*`` alerts Telegram via transient
pending-ping files that the sender consumes + deletes — so they can't back the
Overview notification banner. Every operational alert now ALSO appends a
structured row to ``runtime_logs/operator_alerts.jsonl`` (a bounded ring), and
``GET /api/bot/notifications`` reads its recent tail so a live condition — the
``alpaca_paper`` QQQ "Position CLOSE failing — won't flatten" — surfaces on the
app banner, not only in Telegram.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def runtime_env(tmp_path, monkeypatch):
    """Point runtime_logs_dir at a temp dir and reload the modules under test."""
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path))
    import src.utils.paths as paths

    importlib.reload(paths)
    import src.runtime.execution_diagnostics as ed

    importlib.reload(ed)
    import src.web.api.routers.notifications as nz

    importlib.reload(nz)
    return ed, nz, tmp_path


def test_close_failure_writes_operator_alert_log(runtime_env):
    ed, _nz, tmp_path = runtime_env
    ed.enqueue_close_failure(
        account="alpaca_paper",
        symbol="QQQ",
        side="long",
        qty=16.0,
        consecutive=3,
        error="insufficient qty available for order (requested: 16, available: 0)",
    )
    log = tmp_path / "operator_alerts.jsonl"
    assert log.is_file(), "operator_alerts.jsonl must be written alongside the ping"
    body = log.read_text()
    assert "close_failure" in body
    assert "QQQ" in body
    assert "won't flatten" in body


def test_close_failure_surfaces_as_banner(runtime_env):
    _ed, nz, _tmp = runtime_env
    _ed.enqueue_close_failure(
        account="alpaca_paper", symbol="QQQ", side="long", qty=16.0,
        consecutive=3, error="insufficient qty available",
    )
    banners = nz._operator_alert_banners()
    assert len(banners) == 1
    b = banners[0]
    assert b["kind"] == "close_failure"
    assert b["severity"] == "warning"  # high priority → warning (critical → alert)
    assert "CLOSE failing" in b["message"]
    assert b["detail"] and "alpaca_paper" in b["detail"]


def test_get_notifications_includes_operator_alert(runtime_env):
    _ed, nz, _tmp = runtime_env
    _ed.enqueue_close_failure(
        account="alpaca_paper", symbol="QQQ", side="long", qty=16.0,
        consecutive=3, error="insufficient qty available",
    )
    payload = nz.get_notifications()
    kinds = [b["kind"] for b in payload["banners"]]
    assert "close_failure" in kinds
    assert payload["count"] >= 1


def test_per_tick_repeats_dedupe_to_one_banner(runtime_env):
    """A close-retry firing every tick (only the count changes) collapses to one."""
    _ed, nz, _tmp = runtime_env
    for n in (3, 4, 5, 6):
        _ed.enqueue_close_failure(
            account="alpaca_paper", symbol="QQQ", side="long", qty=16.0,
            consecutive=n, error="insufficient qty available",
        )
    banners = nz._operator_alert_banners()
    assert len(banners) == 1, "digit-normalised dedupe should collapse the retries"


def test_critical_priority_maps_to_alert(runtime_env):
    _ed, nz, _tmp = runtime_env
    _ed.enqueue_orphan_created_flag(
        account="ib_paper", symbol="MHG", side="long", trade_id=999,
        origin="reconciler", reason="naked orphan",
    )  # default priority="critical"
    banners = nz._operator_alert_banners()
    assert banners, "orphan-created flag must surface as a banner"
    assert banners[0]["severity"] == "alert"


def test_no_log_yields_no_banners(runtime_env):
    _ed, nz, _tmp = runtime_env
    assert nz._operator_alert_banners() == []
