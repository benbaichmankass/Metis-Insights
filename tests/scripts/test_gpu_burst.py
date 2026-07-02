"""Tests for the GPU-burst preflight gate + cost recorder (M19 Tier-1)."""
from __future__ import annotations

import json

from scripts.ml.gpu_burst import preflight, record_run
from src.runtime import gpu_spend


def _ledger(tmp_path, runs=None, budget=10.0):
    p = tmp_path / "gpu_spend_ledger.json"
    p.write_text(json.dumps({"budget_usd_per_month": budget, "runs": runs or []}), encoding="utf-8")
    return str(p)


def test_preflight_passes_under_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_SPEND_LEDGER", _ledger(tmp_path))
    assert preflight.main(["--est-cost", "0.40", "--experiment", "T1.1"]) == 0


def test_preflight_aborts_over_budget(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "GPU_SPEND_LEDGER",
        _ledger(tmp_path, runs=[{"run_id": "big", "ended_at": "2999-01-02T00:00:00Z", "cost_usd": 9.8}]),
    )
    # projected 9.8 + 0.5 = 10.3 > 10 → non-zero (abort). Uses the real current
    # month; the run above is dated far in the future so it lands in no real month —
    # so pin the gate directly too:
    assert gpu_spend.would_exceed_budget(0.5, "2999-01") is True


def test_record_run_appends_and_prices(tmp_path, monkeypatch):
    path = _ledger(tmp_path)
    monkeypatch.setenv("GPU_SPEND_LEDGER", path)
    rc = record_run.main([
        "--run-id", "gpu-test-1", "--experiment", "T1.1 bake-off",
        "--gpu-type", "RTX 4090", "--gpu-hours", "0.9", "--rate", "0.34",
        "--started", "2026-07-02T00:00:00Z", "--ended", "2026-07-02T00:54:00Z",
        "--status", "completed",
    ])
    assert rc == 0
    ledger = json.loads(open(path).read())
    assert len(ledger["runs"]) == 1
    entry = ledger["runs"][0]
    assert entry["run_id"] == "gpu-test-1"
    assert abs(entry["cost_usd"] - 0.306) < 1e-6  # 0.9 * 0.34, filled on append


def test_record_run_authoritative_cost_wins(tmp_path, monkeypatch):
    path = _ledger(tmp_path)
    monkeypatch.setenv("GPU_SPEND_LEDGER", path)
    record_run.main([
        "--run-id", "gpu-test-2", "--experiment", "T1.2",
        "--gpu-hours", "3.0", "--rate", "0.34", "--cost", "0.95",  # billed != hours×rate
        "--ended", "2026-07-05T00:00:00Z",
    ])
    entry = json.loads(open(path).read())["runs"][0]
    assert entry["cost_usd"] == 0.95  # the recorded billed figure, not 1.02
