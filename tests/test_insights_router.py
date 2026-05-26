"""Tests for the AI Analyst insights router (M13 S1 / PR B).

The router is a thin, read-only veneer over the file-based cache that
``ict-insights-generator`` writes. These tests cover:

1. Cache-hit path — present file is returned with ``cache_age_seconds``
   stamped from mtime.
2. Cache-miss path — missing file returns a placeholder envelope (200,
   not 500) so the dashboard doesn't break when the generator hasn't
   landed its first run.
3. Strategy-name validation — invalid names 400 (or 404 from FastAPI's
   path routing) rather than escaping the cache dir.
4. **The load-bearing invariant**: the router does NOT import the
   ``anthropic`` SDK. Two-process split is the cost-control and
   latency-control mechanism; if the router ever imports anthropic the
   contract is broken.

The tests mount just the insights router on a minimal FastAPI app
rather than importing ``src.web.api.main`` whole — this isolates the
router from sibling routers that pull in heavy ML deps (``pandas``,
``ccxt``, etc.) that are unrelated to M13.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def insights_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the router's module-level ``_INSIGHTS_DIR`` at a tmp path.

    Monkeypatching the constant directly (rather than reloading the
    module via env vars) is the simple, reliable knob: it avoids any
    module-cache or path-cache subtlety, and pytest reverts the patch
    automatically between tests.

    Returns the ``<tmp>/insights`` directory (created), so each test
    can drop cache files directly into it.
    """
    from src.web.api.routers import insights as insights_module

    insights = tmp_path / "insights"
    insights.mkdir()
    monkeypatch.setattr(insights_module, "_INSIGHTS_DIR", insights)
    return insights


@pytest.fixture
def client(insights_dir: Path) -> TestClient:
    # Mount just the insights router on a minimal app; avoids pulling
    # in sibling routers' heavy deps (pandas, ccxt, jwt, etc.) that the
    # M13 router doesn't share.
    from src.web.api.routers import insights as insights_router

    app = FastAPI()
    app.include_router(insights_router.router)
    return TestClient(app, raise_server_exceptions=False)


def _write_cache(insights_dir: Path, name: str, payload: dict) -> Path:
    path = insights_dir / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sample_payload(model_id: str = "claude-haiku-4-5-20251001") -> dict:
    return {
        "summary_md": "All five strategies posted small positive PnL; "
        "no anomalies in the last 24h.",
        "grade": "good",
        "signals": [],
        "data_window": {
            "start": "2026-05-26T00:00:00+00:00",
            "end": "2026-05-27T00:00:00+00:00",
        },
        "row_counts": {
            "trades": 12,
            "order_packages": 18,
            "signals": 240,
            "audit_events": 1024,
        },
        "generated_at": "2026-05-27T00:00:01+00:00",
        "model_id": model_id,
    }


# ---------------------------------------------------------------------------
# Cache-hit path
# ---------------------------------------------------------------------------


def test_summary_cache_hit_returns_payload_with_age(
    client: TestClient, insights_dir: Path
) -> None:
    _write_cache(insights_dir, "summary.json", _sample_payload())

    resp = client.get("/api/bot/insights/summary")
    assert resp.status_code == 200, resp.text
    out = resp.json()

    assert out["cache_present"] is True
    assert "All five strategies" in out["summary_md"]
    assert out["grade"] == "good"
    assert out["row_counts"]["trades"] == 12
    assert out["model_id"] == "claude-haiku-4-5-20251001"
    assert isinstance(out["cache_age_seconds"], int)
    assert out["cache_age_seconds"] >= 0
    assert out["cache_age_seconds"] < 60


def test_recent_passes_through_requested_limit(
    client: TestClient, insights_dir: Path
) -> None:
    _write_cache(insights_dir, "recent.json", _sample_payload())

    resp = client.get("/api/bot/insights/recent?limit=15")
    assert resp.status_code == 200
    out = resp.json()
    assert out["cache_present"] is True
    assert out["requested_limit"] == 15


def test_recent_rejects_out_of_range_limit(
    client: TestClient, insights_dir: Path
) -> None:
    _write_cache(insights_dir, "recent.json", _sample_payload())

    resp = client.get("/api/bot/insights/recent?limit=999")
    assert resp.status_code == 422


def test_strategy_cache_hit_resolves_per_strategy_file(
    client: TestClient, insights_dir: Path
) -> None:
    payload = _sample_payload(model_id="claude-sonnet-4-6")
    payload["summary_md"] = "vwap had three losing setups."
    _write_cache(insights_dir, "strategy_vwap.json", payload)

    resp = client.get("/api/bot/insights/strategy/vwap")
    assert resp.status_code == 200
    out = resp.json()
    assert out["cache_present"] is True
    assert "vwap" in out["summary_md"]
    assert out["model_id"] == "claude-sonnet-4-6"


def test_health_cache_hit(client: TestClient, insights_dir: Path) -> None:
    _write_cache(insights_dir, "health.json", _sample_payload(model_id="claude-sonnet-4-6"))

    resp = client.get("/api/bot/insights/health")
    assert resp.status_code == 200
    assert resp.json()["cache_present"] is True


# ---------------------------------------------------------------------------
# Cache-miss path — placeholder envelope, 200 not 500
# ---------------------------------------------------------------------------


def test_summary_cache_miss_returns_placeholder(
    client: TestClient, insights_dir: Path
) -> None:
    resp = client.get("/api/bot/insights/summary")
    assert resp.status_code == 200, resp.text
    out = resp.json()

    assert out["cache_present"] is False
    assert "not yet generated" in out["summary_md"]
    assert out["grade"] == "good"
    assert out["signals"] == []
    assert out["cache_age_seconds"] is None
    assert out["generated_at"] is None
    assert out["model_id"] is None
    assert "insights" in out["cache_path"]


def test_strategy_cache_miss_for_unknown_strategy_still_200(
    client: TestClient, insights_dir: Path
) -> None:
    resp = client.get("/api/bot/insights/strategy/turtle_soup")
    assert resp.status_code == 200
    out = resp.json()
    assert out["cache_present"] is False
    assert "turtle_soup" in out["cache_path"]


def test_malformed_cache_falls_back_to_placeholder(
    client: TestClient, insights_dir: Path
) -> None:
    # Half-written file — generator crashed mid-write.
    (insights_dir / "summary.json").write_text("not json {", encoding="utf-8")

    resp = client.get("/api/bot/insights/summary")
    assert resp.status_code == 200
    out = resp.json()
    assert out["cache_present"] is False  # JSON decode failed → placeholder.


# ---------------------------------------------------------------------------
# Path-traversal / name validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "strategy_with_slash%2Finner",  # url-encoded slash
        "UPPER",
        "name.json",
        "with-dash",
    ],
)
def test_strategy_invalid_name_returns_400(
    client: TestClient, insights_dir: Path, bad_name: str
) -> None:
    resp = client.get(f"/api/bot/insights/strategy/{bad_name}")
    # FastAPI may convert path-traversal at routing layer to 404 before
    # reaching the handler — both 400 and 404 are safe ("nothing got
    # served"); we just want NOT 200 (i.e. no cache file got served).
    assert resp.status_code in (400, 404), (
        f"name={bad_name!r} unexpectedly returned {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Load-bearing invariant: router does not import `anthropic`
# ---------------------------------------------------------------------------


def test_router_module_does_not_import_anthropic(
    insights_dir: Path,
) -> None:
    """The cache-only read path is the entire cost-control mechanism.

    If this test fails, the router is doing synchronous LLM calls — that
    means dashboard taps wait on the Anthropic API (latency) and every
    request burns tokens (cost). The two-process split exists to make
    that impossible.
    """
    sys.modules.pop("anthropic", None)
    sys.modules.pop("src.web.api.routers.insights", None)

    importlib.import_module("src.web.api.routers.insights")

    assert "anthropic" not in sys.modules, (
        "src/web/api/routers/insights.py must NOT import the anthropic SDK "
        "— the router is supposed to be a cache-only read path. "
        "Synchronous LLM calls belong in src/runtime/insights/ (the "
        "generator process), not in the request handler."
    )
