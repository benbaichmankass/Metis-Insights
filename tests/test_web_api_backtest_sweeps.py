"""GET /api/bot/backtests/sweeps tests.

Tier-1 read endpoint backed by the trainer mirror at
``$RUNTIME_LOGS_DIR/trainer_mirror/backtests/<UTC-date>/`` — the
strategy-improvement / validation backtest sweeps that
``scripts/ops/run_backtest_sweep.sh`` produces on the trainer VM and
``scripts/ops/publish_trainer_mirror.sh`` rsyncs onto the live VM.

Pins:

  * Envelope shape (``present``, ``dir``, ``mirror_age_seconds``,
    ``sweeps``).
  * Per-sweep wire-shape (``date``, ``summary_md``, ``metrics``,
    ``extra_metrics``, ``generated_at``).
  * Newest-first ordering by directory name.
  * ``limit`` clamped 1..100; default 20.
  * Best-effort: missing mirror dir → ``present: False`` + empty list,
    never a 500 (the dashboard treats it as "no sweeps yet").
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    # The router resolves runtime_logs_dir() per-request, so pointing
    # RUNTIME_LOGS_DIR at tmp_path is sufficient (same contract the
    # training-center router + its tests rely on).
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path / "runtime_logs"))
    monkeypatch.setenv("ICT_REPO_ROOT", str(tmp_path))
    from src.web.api import main as api_main

    return TestClient(api_main.app, raise_server_exceptions=False)


def _mirror_root(tmp_path: Path) -> Path:
    return tmp_path / "runtime_logs" / "trainer_mirror" / "backtests"


def _write_sweep(
    tmp_path: Path,
    date: str,
    *,
    summary: str | None = "# Sweep\n\n| Variant | Win % |\n|---|---:|\n| vwap | 30.7 |\n",
    metrics: dict | None = None,
    extra: dict[str, dict] | None = None,
    csv_bytes: int = 0,
) -> Path:
    d = _mirror_root(tmp_path) / date
    d.mkdir(parents=True, exist_ok=True)
    if summary is not None:
        (d / "SUMMARY.md").write_text(summary, encoding="utf-8")
    if metrics is not None:
        (d / "all_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    for name, payload in (extra or {}).items():
        (d / name).write_text(json.dumps(payload), encoding="utf-8")
    if csv_bytes:
        (d / "candles.csv").write_text("x" * csv_bytes, encoding="utf-8")
    return d


class TestHappyPath:
    def test_envelope_and_wire_shape(self, tmp_path, client):
        _write_sweep(
            tmp_path,
            "2026-05-17",
            summary="# Post-incident\n\n| Variant | Win % |\n|---|---:|\n| V_PROD | 30.7 |\n",
            metrics={"vwap": {"V_PROD": {"trades": 3449, "win_rate": 0.307}}},
            extra={"ict_scalp_metrics.json": {"trades": 100, "win_rate": 0.59}},
        )
        resp = client.get("/api/bot/backtests/sweeps")
        assert resp.status_code == 200
        body = resp.json()
        assert body["present"] is True
        assert body["dir"].endswith("trainer_mirror/backtests")
        assert isinstance(body["mirror_age_seconds"], (int, float))
        assert len(body["sweeps"]) == 1
        sweep = body["sweeps"][0]
        assert sweep["date"] == "2026-05-17"
        assert "V_PROD" in sweep["summary_md"]
        assert sweep["metrics"]["vwap"]["V_PROD"]["trades"] == 3449
        assert sweep["extra_metrics"]["ict_scalp_metrics.json"]["win_rate"] == 0.59
        assert sweep["generated_at"].endswith("Z")

    def test_newest_first_by_date(self, tmp_path, client):
        _write_sweep(tmp_path, "2026-05-10")
        _write_sweep(tmp_path, "2026-05-17")
        _write_sweep(tmp_path, "2026-05-14")
        resp = client.get("/api/bot/backtests/sweeps")
        dates = [s["date"] for s in resp.json()["sweeps"]]
        assert dates == ["2026-05-17", "2026-05-14", "2026-05-10"]

    def test_summary_missing_is_none_not_error(self, tmp_path, client):
        _write_sweep(tmp_path, "2026-05-17", summary=None, metrics={"a": 1})
        sweep = client.get("/api/bot/backtests/sweeps").json()["sweeps"][0]
        assert sweep["summary_md"] is None
        assert sweep["metrics"] == {"a": 1}
        # generated_at still resolves (falls back to dir mtime).
        assert sweep["generated_at"].endswith("Z")

    def test_all_metrics_excluded_from_extra(self, tmp_path, client):
        # all_metrics.json must surface under `metrics`, never duplicated
        # into `extra_metrics` (the *_metrics.json glob would otherwise
        # match it).
        _write_sweep(tmp_path, "2026-05-17", metrics={"x": 1})
        sweep = client.get("/api/bot/backtests/sweeps").json()["sweeps"][0]
        assert sweep["metrics"] == {"x": 1}
        assert "all_metrics.json" not in sweep["extra_metrics"]


class TestLimit:
    def test_limit_caps_results(self, tmp_path, client):
        for i in range(5):
            _write_sweep(tmp_path, f"2026-05-1{i}")
        body = client.get("/api/bot/backtests/sweeps?limit=2").json()
        assert len(body["sweeps"]) == 2

    def test_limit_below_1_rejected(self, client):
        assert client.get("/api/bot/backtests/sweeps?limit=0").status_code == 422

    def test_limit_above_max_rejected(self, client):
        assert client.get("/api/bot/backtests/sweeps?limit=101").status_code == 422


class TestBestEffort:
    def test_missing_mirror_dir(self, client):
        body = client.get("/api/bot/backtests/sweeps").json()
        assert body["present"] is False
        assert body["sweeps"] == []
        assert body["mirror_age_seconds"] is None
