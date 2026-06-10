"""Tests for GET /api/bot/strategies/{name}/tune (M8 tune-result read route)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from src.web.api.routers import strategy_tune as route  # noqa: E402


def _write_tune(root, date, strategy, param, payload):
    d = root / "strategy_tunes" / date
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{strategy}__{param}.json").write_text(json.dumps(payload))


def test_route_present_false_when_no_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "runtime_logs_dir", lambda: tmp_path)
    out = route.get_strategy_tune("trend_donchian")
    assert out["present"] is False and out["results"] == []


def test_route_rejects_bad_name(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "runtime_logs_dir", lambda: tmp_path)
    assert route.get_strategy_tune("../etc")["present"] is False
    assert route.get_strategy_tune("Trend")["error"] == "invalid_strategy_name"


def test_route_returns_all_params_from_latest_date(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "runtime_logs_dir", lambda: tmp_path)
    # older date — should be ignored once a newer date has results
    _write_tune(tmp_path, "2026-06-08", "trend_donchian", "min_confidence",
                {"strategy": "trend_donchian", "param": "min_confidence", "old": True})
    # newest date with two params
    _write_tune(tmp_path, "2026-06-10", "trend_donchian", "min_confidence",
                {"strategy": "trend_donchian", "param": "min_confidence"})
    _write_tune(tmp_path, "2026-06-10", "trend_donchian", "trail_mult",
                {"strategy": "trend_donchian", "param": "trail_mult"})
    # a different strategy's file must not leak in
    _write_tune(tmp_path, "2026-06-10", "vwap", "threshold", {"strategy": "vwap"})

    out = route.get_strategy_tune("trend_donchian")
    assert out["present"] is True and out["date"] == "2026-06-10"
    params = sorted(r["param"] for r in out["results"])
    assert params == ["min_confidence", "trail_mult"]
    assert all(not r.get("old") for r in out["results"])  # newest date only
