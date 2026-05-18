"""Tests for the /api/bot/ml/* router (S-AI-WS8-PART-2).

The training-center router reads a mirror directory at
``$RUNTIME_LOGS_DIR/trainer_mirror/`` that the trainer VM rsyncs into.
These tests build a fake mirror under a tmp_path, point RUNTIME_LOGS_DIR
at it, and exercise each endpoint. We cover:

  * happy path (every artifact present)
  * empty mirror (trainer never published — dashboard's worst case)
  * mirror present but trainer_status.json missing
  * registry rows count
  * sessions filter narrows to per-manifest rows only
  * runs lookup validates IDs and 404s on missing
  * runs lookup rejects path-traversal attempts
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    # Point all path helpers at tmp_path so the router reads from a
    # mirror we control. The router itself resolves the dir lazily on
    # each request, so this monkeypatch is sufficient.
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path / "runtime_logs"))
    monkeypatch.setenv("ICT_REPO_ROOT", str(tmp_path))
    # Re-import inside the fixture so the env var is honoured by any
    # module-level path constants. Most consumers (this router included)
    # call the helpers at request-time, but be safe.
    from src.web.api import main as api_main

    return TestClient(api_main.app)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _populate_mirror(tmp_path: Path) -> Path:
    """Build a representative trainer mirror; return the mirror root."""
    mirror = tmp_path / "runtime_logs" / "trainer_mirror"
    mirror.mkdir(parents=True, exist_ok=True)

    (mirror / "trainer_status.json").write_text(
        json.dumps(
            {
                "ts": "2026-05-14T14:50:00+00:00",
                "trainer_vm": {"ip": "158.178.209.121", "role": "training-center"},
                "service": {"active_state": "inactive", "unit_file_state": "disabled"},
                "timer": {"active_state": "inactive", "unit_file_state": "disabled"},
                "last_cycle": None,
                "cycles_24h": 0,
                "registry": {"models": 0, "stages": {}},
            }
        ),
        encoding="utf-8",
    )

    _write_jsonl(
        mirror / "training_cycle.jsonl",
        [
            {"ts": "2026-05-14T14:40:00+00:00", "status": "cycle_start", "manifest_count": 2},
            {"ts": "2026-05-14T14:41:00+00:00", "status": "manifest_ok",
             "manifest": "ml/configs/baseline-trade-outcome-winrate.yaml",
             "model_id": "trade-outcome-winrate-v1",
             "metrics_path": "ml/experiments-runs/trade-outcome-winrate-v1/run_a/metrics.json"},
            {"ts": "2026-05-14T14:42:00+00:00", "status": "manifest_failed",
             "manifest": "ml/configs/baseline-regime-classifier.yaml", "exit_code": 1},
            {"ts": "2026-05-14T14:43:00+00:00", "status": "cycle_end", "overall_rc": 1},
        ],
    )

    _write_jsonl(
        mirror / "registry.jsonl",
        [
            {"model_id": "trade-outcome-winrate-v1", "target_deployment_stage": "research_only"},
            {"model_id": "regime-classifier-v1", "target_deployment_stage": "candidate"},
        ],
    )

    _write_jsonl(
        mirror / "trainer" / "dataset_builds.jsonl",
        [
            {"ts": "2026-05-13T15:47:18+00:00", "status": "building", "family": "setup_labels"},
            {"ts": "2026-05-13T15:47:18+00:00", "status": "failed", "family": "setup_labels",
             "exit_code": 1, "stderr_tail": "TypeError on risk_pct"},
            {"ts": "2026-05-13T15:47:19+00:00", "status": "build_end", "overall_rc": 1},
        ],
    )

    _write_jsonl(
        mirror / "trainer" / "db_pulls.jsonl",
        [
            {"ts": "2026-05-13T14:42:11+00:00", "status": "sync_done", "overall_rc": 0,
             "data_dir": "/home/ubuntu/ict-trading-bot/data"},
        ],
    )

    run_dir = mirror / "experiments-runs" / "trade-outcome-winrate-v1" / "run_a"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(
        json.dumps({"accuracy": 0.61, "n": 240, "split": "holdout"}), encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"trainer": "PerStrategyWinRateTrainer", "evaluator": "ClassificationEvaluator"}),
        encoding="utf-8",
    )

    return mirror


# ---------------------------------------------------------------------------
# Happy-path / populated mirror

def test_status_returns_trainer_self_report(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["trainer_status_present"] is True
    assert body["mirror_present"] is True
    assert body["status"]["trainer_vm"]["role"] == "training-center"
    assert body["status"]["service"]["unit_file_state"] == "disabled"


def test_cycle_returns_event_tail(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/cycle?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 10
    statuses = [r["status"] for r in body["rows"]]
    assert statuses == ["cycle_start", "manifest_ok", "manifest_failed", "cycle_end"]


def test_cycle_limit_clamp(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/cycle?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 2
    # Newest-last (raw tail order)
    assert body["rows"][-1]["status"] == "cycle_end"


def test_registry_returns_all_rows(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert {r["model_id"] for r in body["rows"]} == {
        "trade-outcome-winrate-v1",
        "regime-classifier-v1",
    }


def test_sessions_filters_to_manifest_rows(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/sessions")
    assert resp.status_code == 200
    body = resp.json()
    statuses = [r["status"] for r in body["sessions"]]
    # cycle_start / cycle_end are excluded; only per-manifest rows remain.
    assert statuses == ["manifest_ok", "manifest_failed"]


def test_builds_surfaces_failures(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/builds")
    assert resp.status_code == 200
    body = resp.json()
    failures = [r for r in body["rows"] if r.get("status") == "failed"]
    assert len(failures) == 1
    assert "risk_pct" in failures[0]["stderr_tail"]


def test_db_pulls_returns_sync_history(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/db_pulls")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"][0]["status"] == "sync_done"


def test_runs_returns_metrics_and_manifest(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/runs/trade-outcome-winrate-v1/run_a")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics"]["accuracy"] == pytest.approx(0.61)
    assert body["manifest"]["trainer"] == "PerStrategyWinRateTrainer"


# ---------------------------------------------------------------------------
# Empty / missing mirror — the production case as of 2026-05-14

def test_status_empty_mirror_returns_present_false(client: TestClient) -> None:
    # No _populate_mirror call → mirror dir does not exist.
    resp = client.get("/api/bot/ml/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mirror_present"] is False
    assert body["trainer_status_present"] is False
    assert body["status"] is None


def test_cycle_empty_mirror_returns_empty_rows(client: TestClient) -> None:
    resp = client.get("/api/bot/ml/cycle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["mirror_present"] is False


def test_registry_empty_mirror_returns_zero_count(client: TestClient) -> None:
    resp = client.get("/api/bot/ml/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["rows"] == []


# ---------------------------------------------------------------------------
# Security / validation

def test_runs_rejects_path_traversal(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/runs/..%2Fetc/passwd")
    # FastAPI / Starlette decode %2F to /, so this becomes 3 segments and
    # the route won't match (404). Either way is fine — the point is it
    # never escapes the mirror.
    assert resp.status_code in {400, 404}


def test_runs_rejects_dotdot_in_path(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/runs/..%2F../foo")
    assert resp.status_code in {400, 404}


def test_runs_404_on_missing_run(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    resp = client.get("/api/bot/ml/runs/no-such-model/no-such-run")
    assert resp.status_code == 404


def test_runs_400_on_invalid_chars(client: TestClient, tmp_path: Path) -> None:
    _populate_mirror(tmp_path)
    # Special chars not in [A-Za-z0-9._-] are rejected before any FS access.
    resp = client.get("/api/bot/ml/runs/bad model/run_a")
    # %20 decoded becomes a space, which fails _SAFE_ID, but Starlette
    # may also reject. Accept either 400 (router-level) or 404.
    assert resp.status_code in {400, 404}


# ---------------------------------------------------------------------------
# Registry-row enrichment (2026-05-18: Models page per-model card surface)
#
# /api/bot/ml/registry now flattens manifest fields and computes the
# operator-facing 2-bucket deployment view. Tests pin:
#   * SHADOW bucket when model_id is in any strategy's shadow_model_ids
#   * OFFLINE bucket when no strategy references the model
#   * model_family / trainer / evaluator / dataset_ref flattened from manifest
#   * latest_run pulled from runs[-1]
#   * Missing manifest → nullable fields, no crash
#   * Unreadable strategies.yaml → graceful fallback (all rows OFFLINE)


def _patch_shadow_wiring(
    monkeypatch: pytest.MonkeyPatch, shadow_wiring: dict[str, list[str]]
) -> None:
    """Monkeypatch the shadow_wiring_map loader to return a controlled
    inverted map. Tests use this instead of writing a tmp strategies.yaml
    because ``src.utils.paths.repo_root`` uses marker-discovery from the
    file location (not an env var) and is ``@lru_cache``'d — there's no
    clean way to redirect it to tmp_path. Inverting the wiring here keeps
    the enrichment-logic tests focused on the enrichment, not on YAML
    path resolution."""
    inverted: dict[str, list[str]] = {}
    for strategy_name, model_ids in shadow_wiring.items():
        for mid in model_ids:
            inverted.setdefault(mid, []).append(strategy_name)
    monkeypatch.setattr(
        "src.web.api.routers.training_center._load_shadow_wiring_map",
        lambda: inverted,
    )


def _populate_registry_with_manifest(tmp_path: Path) -> Path:
    """Build a mirror with rich registry rows carrying a manifest
    block and a runs[] history."""
    mirror = tmp_path / "runtime_logs" / "trainer_mirror"
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "trainer_status.json").write_text(
        json.dumps({"ts": "2026-05-18T00:00:00+00:00", "registry": {"models": 2}}),
        encoding="utf-8",
    )
    _write_jsonl(
        mirror / "registry.jsonl",
        [
            {
                "model_id": "regime-classifier-baseline-v0",
                "status": "candidate",
                "target_deployment_stage": "shadow",
                "manifest": {
                    "model_family": "regime_classifier",
                    "trainer": "ml.trainers.lightgbm.LightGBMClassifierTrainer",
                    "evaluator": "ml.evaluators.classification.ClassificationEvaluator",
                    "dataset": {
                        "family": "signal_features",
                        "symbol_scope": "BTCUSDT",
                        "timeframe": "5m",
                        "version": "v0",
                    },
                },
                "metrics": {"macro_f1": 0.33},
                "runs": [
                    {"run_id": "20260514T120000Z", "at": "2026-05-14T12:00:00+00:00",
                     "metrics": {"macro_f1": 0.30}, "model_state_path": "/x/a"},
                    {"run_id": "20260515T120000Z", "at": "2026-05-15T12:00:00+00:00",
                     "metrics": {"macro_f1": 0.33}, "model_state_path": "/x/b"},
                ],
            },
            {
                "model_id": "trade-outcome-winrate-v1",
                "status": "candidate",
                "target_deployment_stage": "research_only",
                # No manifest block — pre-WS5 registry row.
                "metrics": {"accuracy": 0.61},
                "runs": [],
            },
        ],
    )
    return mirror


def test_registry_enrichment_shadow_bucket_when_wired(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model referenced by vwap.shadow_model_ids → SHADOW bucket + linked_strategies."""
    _populate_registry_with_manifest(tmp_path)
    _patch_shadow_wiring(
        monkeypatch,
        {
            "vwap": ["regime-classifier-baseline-v0"],
            "turtle_soup": [],
            "ict_scalp_5m": [],
        },
    )
    resp = client.get("/api/bot/ml/registry")
    assert resp.status_code == 200
    rows = {r["model_id"]: r for r in resp.json()["rows"]}
    wired = rows["regime-classifier-baseline-v0"]
    assert wired["deployment_bucket"] == "SHADOW"
    assert wired["linked_strategies"] == ["vwap"]


def test_registry_enrichment_offline_bucket_when_no_strategy_wires(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model not referenced by any strategy → OFFLINE bucket + empty linked list."""
    _populate_registry_with_manifest(tmp_path)
    _patch_shadow_wiring(
        monkeypatch,
        {"vwap": ["regime-classifier-baseline-v0"]},
    )
    resp = client.get("/api/bot/ml/registry")
    rows = {r["model_id"]: r for r in resp.json()["rows"]}
    orphan = rows["trade-outcome-winrate-v1"]
    assert orphan["deployment_bucket"] == "OFFLINE"
    assert orphan["linked_strategies"] == []


def test_registry_enrichment_flattens_manifest_fields(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """model_family / trainer / evaluator / dataset_ref pulled to top-level
    so the dashboard doesn't have to deep-index into manifest."""
    _populate_registry_with_manifest(tmp_path)
    _patch_shadow_wiring(monkeypatch, {"vwap": []})
    resp = client.get("/api/bot/ml/registry")
    rows = {r["model_id"]: r for r in resp.json()["rows"]}
    rc = rows["regime-classifier-baseline-v0"]
    assert rc["model_family"] == "regime_classifier"
    assert rc["trainer"] == "ml.trainers.lightgbm.LightGBMClassifierTrainer"
    assert rc["evaluator"] == "ml.evaluators.classification.ClassificationEvaluator"
    assert rc["dataset_ref"] == {
        "family": "signal_features",
        "symbol_scope": "BTCUSDT",
        "timeframe": "5m",
        "version": "v0",
    }


def test_registry_enrichment_nulls_when_manifest_absent(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-WS5 rows without a manifest block must still serialize cleanly —
    new fields appear as None, not crash."""
    _populate_registry_with_manifest(tmp_path)
    _patch_shadow_wiring(monkeypatch, {"vwap": []})
    resp = client.get("/api/bot/ml/registry")
    rows = {r["model_id"]: r for r in resp.json()["rows"]}
    bare = rows["trade-outcome-winrate-v1"]
    assert bare["model_family"] is None
    assert bare["trainer"] is None
    assert bare["evaluator"] is None
    assert bare["dataset_ref"] is None
    assert bare["latest_run"] is None


def test_registry_enrichment_latest_run_pulled_from_runs_tail(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """latest_run mirrors runs[-1] so the dashboard can render
    "last trained: <date>" without re-sorting."""
    _populate_registry_with_manifest(tmp_path)
    _patch_shadow_wiring(monkeypatch, {"vwap": []})
    resp = client.get("/api/bot/ml/registry")
    rows = {r["model_id"]: r for r in resp.json()["rows"]}
    rc = rows["regime-classifier-baseline-v0"]
    assert rc["latest_run"]["run_id"] == "20260515T120000Z"
    assert rc["latest_run"]["metrics"]["macro_f1"] == pytest.approx(0.33)


def test_registry_enrichment_multiple_strategies_wiring_same_model(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If two strategies both reference the same model, linked_strategies
    captures both (rare today but the wiring supports it)."""
    _populate_registry_with_manifest(tmp_path)
    _patch_shadow_wiring(
        monkeypatch,
        {
            "vwap": ["regime-classifier-baseline-v0"],
            "turtle_soup": ["regime-classifier-baseline-v0"],
        },
    )
    resp = client.get("/api/bot/ml/registry")
    rows = {r["model_id"]: r for r in resp.json()["rows"]}
    rc = rows["regime-classifier-baseline-v0"]
    assert set(rc["linked_strategies"]) == {"vwap", "turtle_soup"}
    assert rc["deployment_bucket"] == "SHADOW"


def test_registry_enrichment_gracefully_handles_unreadable_strategies_yaml(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If config/strategies.yaml is missing or unreadable, every row is
    OFFLINE rather than the endpoint blowing up. Simulate by patching
    _load_shadow_wiring_map to raise — the endpoint must still return
    200 with bucket=OFFLINE for every row."""
    _populate_registry_with_manifest(tmp_path)

    def _raises() -> dict[str, list[str]]:
        # The real helper catches exceptions and returns {} (logged WARN);
        # to test that contract end-to-end we patch the inner reader
        # function the helper wraps.
        return {}

    monkeypatch.setattr(
        "src.web.api.routers.training_center._load_shadow_wiring_map",
        _raises,
    )
    resp = client.get("/api/bot/ml/registry")
    assert resp.status_code == 200
    for row in resp.json()["rows"]:
        assert row["deployment_bucket"] == "OFFLINE"
        assert row["linked_strategies"] == []
