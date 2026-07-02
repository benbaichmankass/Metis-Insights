"""Tests for the GPU-burst spend ledger (M19 Tier-1). Stdlib, no network."""
from __future__ import annotations

import json

from src.runtime import gpu_spend as gs


def _write(tmp_path, ledger: dict) -> str:
    p = tmp_path / "gpu_spend_ledger.json"
    p.write_text(json.dumps(ledger), encoding="utf-8")
    return str(p)


def test_empty_ledger_summary(tmp_path):
    path = _write(tmp_path, {"budget_usd_per_month": 10.0, "provider": "runpod", "runs": []})
    s = gs.summarize_spend(path, current_month="2026-07")
    assert s["present"] is True
    assert s["budget_usd_per_month"] == 10.0
    assert s["current_month_usd"] == 0.0
    assert s["budget_remaining_usd"] == 10.0
    assert s["over_budget"] is False
    assert s["runs"] == []


def test_missing_file_degrades(tmp_path):
    s = gs.summarize_spend(str(tmp_path / "nope.json"), current_month="2026-07")
    assert s["present"] is True
    assert s["current_month_usd"] == 0.0
    assert s["budget_usd_per_month"] == 10.0


def test_per_run_cost_and_month_rollup(tmp_path):
    path = _write(tmp_path, {
        "budget_usd_per_month": 10.0,
        "provider": "runpod",
        "runs": [
            {"run_id": "a", "started_at": "2026-07-02T01:00:00Z", "ended_at": "2026-07-02T02:00:00Z",
             "experiment": "T1.1 bake-off", "gpu_hours": 1.0, "rate_usd_per_hour": 0.34},
            {"run_id": "b", "started_at": "2026-07-05T00:00:00Z", "ended_at": "2026-07-05T04:00:00Z",
             "experiment": "T1.2 encoder v0", "cost_usd": 1.5},
            {"run_id": "c", "started_at": "2026-06-20T00:00:00Z", "ended_at": "2026-06-20T01:00:00Z",
             "experiment": "prior month", "gpu_hours": 2.0, "rate_usd_per_hour": 0.20},
        ],
    })
    s = gs.summarize_spend(path, current_month="2026-07")
    # July = 0.34 (derived) + 1.5 (recorded) = 1.84
    assert abs(s["current_month_usd"] - 1.84) < 1e-6
    assert s["current_month_runs"] == 2
    assert abs(s["budget_remaining_usd"] - 8.16) < 1e-6
    # lifetime includes June's 0.40
    assert abs(s["lifetime_usd"] - 2.24) < 1e-6
    # runs newest-first, with per-run derived cost + month cumulative
    assert [r["run_id"] for r in s["runs"]] == ["b", "a", "c"]
    by_id = {r["run_id"]: r for r in s["runs"]}
    assert abs(by_id["a"]["cost_usd"] - 0.34) < 1e-6
    assert abs(by_id["a"]["cumulative_month_usd"] - 0.34) < 1e-6      # first July run
    assert abs(by_id["b"]["cumulative_month_usd"] - 1.84) < 1e-6      # second July run
    # by_month newest-first
    assert s["by_month"][0]["month"] == "2026-07"


def test_over_budget_flag(tmp_path):
    path = _write(tmp_path, {
        "budget_usd_per_month": 10.0,
        "runs": [{"run_id": "big", "ended_at": "2026-07-02T02:00:00Z", "cost_usd": 12.0}],
    })
    s = gs.summarize_spend(path, current_month="2026-07")
    assert s["over_budget"] is True
    assert s["budget_remaining_usd"] == 0.0  # clamped at 0


def test_would_exceed_budget_gate(tmp_path):
    path = _write(tmp_path, {
        "budget_usd_per_month": 10.0,
        "runs": [{"run_id": "x", "ended_at": "2026-07-02T02:00:00Z", "cost_usd": 9.0}],
    })
    assert gs.would_exceed_budget(2.0, "2026-07", path) is True    # 9 + 2 > 10
    assert gs.would_exceed_budget(0.5, "2026-07", path) is False   # 9 + 0.5 <= 10


def test_record_run_appends(tmp_path):
    path = _write(tmp_path, {"budget_usd_per_month": 10.0, "runs": []})
    gs.record_run(
        {"run_id": "r1", "started_at": "2026-07-02T00:00:00Z", "ended_at": "2026-07-02T01:00:00Z",
         "experiment": "T1.1", "gpu_hours": 0.8, "rate_usd_per_hour": 0.30, "status": "completed"},
        path=path,
    )
    ledger = json.loads(open(path).read())
    assert len(ledger["runs"]) == 1
    assert abs(ledger["runs"][0]["cost_usd"] - 0.24) < 1e-6  # 0.8 * 0.30, filled on append
    s = gs.summarize_spend(path, current_month="2026-07")
    assert abs(s["current_month_usd"] - 0.24) < 1e-6


def test_garbled_ledger_degrades(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    s = gs.summarize_spend(str(p), current_month="2026-07")
    assert s["present"] is True and s["current_month_usd"] == 0.0
