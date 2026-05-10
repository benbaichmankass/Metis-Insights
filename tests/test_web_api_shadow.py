"""Tests for the shadow-predictions dashboard endpoints
(S-AI-WS8-PART-2).

Verifies the two GET routes (`/api/bot/shadow/predictions` and
`/api/bot/shadow/stats`) over a temporary
`runtime_logs/shadow_predictions.jsonl` populated with seeded
records. Reuses the inspector module's parsing so behavior stays
identical to the CLI from PART-1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from src.web.api import main as api_main  # noqa: E402


_TS_EARLY = "2026-05-10T10:00:00+00:00"
_TS_MID = "2026-05-10T12:00:00+00:00"
_TS_LATE = "2026-05-10T14:00:00+00:00"


def _record(
    *,
    model_id: str = "m-a",
    score: float = 0.5,
    ts: str = _TS_MID,
    stage: str = "shadow",
    row_keys: list[str] | None = None,
) -> dict:
    return {
        "predicted_at_utc": ts,
        "model_id": model_id,
        "stage": stage,
        "score": score,
        "row_keys": list(row_keys) if row_keys is not None else ["confidence", "direction"],
    }


def _seed_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


@pytest.fixture
def client(monkeypatch, tmp_path):
    log = tmp_path / "shadow_predictions.jsonl"
    monkeypatch.setenv("SHADOW_PREDICTIONS_LOG", str(log))
    # Keep other env vars happy where FastAPI app init reads them.
    monkeypatch.setenv("JWT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("ALLOWED_EMAIL", "test@example.com")
    monkeypatch.setenv("WEBAPP_PASSWORD_SHA256", "deadbeef")
    return TestClient(api_main.app, raise_server_exceptions=False), log


class TestPredictionsEndpoint:
    def test_returns_envelope_when_log_missing(self, client):
        c, log = client
        # Don't seed anything.
        r = c.get("/api/bot/shadow/predictions")
        assert r.status_code == 200
        body = r.json()
        assert body["log_present"] is False
        assert body["records"] == []
        assert body["count"] == 0

    def test_returns_seeded_records_newest_first(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", ts=_TS_EARLY, score=0.1),
            _record(model_id="m-b", ts=_TS_LATE, score=0.9),
            _record(model_id="m-c", ts=_TS_MID, score=0.5),
        ])
        r = c.get("/api/bot/shadow/predictions")
        assert r.status_code == 200
        body = r.json()
        assert body["log_present"] is True
        assert body["count"] == 3
        ids = [row["model_id"] for row in body["records"]]
        assert ids == ["m-b", "m-c", "m-a"]  # newest first

    def test_limit_caps_record_count(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id=f"m-{i}", ts=_TS_MID, score=0.1 * i)
            for i in range(10)
        ])
        r = c.get("/api/bot/shadow/predictions?limit=3")
        assert r.status_code == 200
        assert r.json()["count"] == 3

    def test_model_id_filter(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", score=0.1),
            _record(model_id="m-b", score=0.9),
            _record(model_id="m-a", score=0.2),
        ])
        r = c.get("/api/bot/shadow/predictions?model_id=m-a")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert {row["model_id"] for row in body["records"]} == {"m-a"}

    def test_stage_filter(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", stage="shadow"),
            _record(model_id="m-b", stage="advisory"),
        ])
        r = c.get("/api/bot/shadow/predictions?stage=advisory")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["records"][0]["model_id"] == "m-b"

    def test_since_filter(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-early", ts=_TS_EARLY),
            _record(model_id="m-mid", ts=_TS_MID),
            _record(model_id="m-late", ts=_TS_LATE),
        ])
        r = c.get(f"/api/bot/shadow/predictions?since={_TS_MID}")
        assert r.status_code == 200
        body = r.json()
        assert {row["model_id"] for row in body["records"]} == {"m-mid", "m-late"}

    def test_bad_since_returns_400(self, client):
        c, log = client
        r = c.get("/api/bot/shadow/predictions?since=not-a-timestamp")
        assert r.status_code == 400
        assert "since" in r.json()["detail"].lower()

    def test_limit_out_of_range_returns_422(self, client):
        c, log = client
        # Query() ge=1 le=1000 — FastAPI auto-validates.
        r = c.get("/api/bot/shadow/predictions?limit=0")
        assert r.status_code == 422
        r = c.get("/api/bot/shadow/predictions?limit=2000")
        assert r.status_code == 422


class TestStatsEndpoint:
    def test_returns_empty_when_log_missing(self, client):
        c, log = client
        r = c.get("/api/bot/shadow/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["log_present"] is False
        assert body["records"] == []

    def test_aggregates_by_model_id_stage(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", stage="shadow", score=0.1),
            _record(model_id="m-a", stage="shadow", score=0.5),
            _record(model_id="m-a", stage="advisory", score=0.9),
            _record(model_id="m-b", stage="shadow", score=0.7),
        ])
        r = c.get("/api/bot/shadow/stats")
        assert r.status_code == 200
        body = r.json()
        # 3 unique (model_id, stage) tuples.
        assert body["count"] == 3
        keyed = {(row["model_id"], row["stage"]): row for row in body["records"]}
        assert keyed[("m-a", "shadow")]["count"] == 2
        assert keyed[("m-a", "shadow")]["score_mean"] == pytest.approx(0.3)
        assert keyed[("m-a", "advisory")]["count"] == 1
        assert keyed[("m-b", "shadow")]["count"] == 1

    def test_sort_order_count_desc(self, client):
        c, log = client
        _seed_log(log, (
            [_record(model_id="popular")] * 3
            + [_record(model_id="rare")] * 1
        ))
        r = c.get("/api/bot/shadow/stats")
        body = r.json()
        assert [row["model_id"] for row in body["records"]] == ["popular", "rare"]

    def test_stats_since_filter(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", ts=_TS_EARLY),
            _record(model_id="m-b", ts=_TS_LATE),
        ])
        r = c.get(f"/api/bot/shadow/stats?since={_TS_MID}")
        body = r.json()
        assert {row["model_id"] for row in body["records"]} == {"m-b"}

    def test_first_last_seen_serialized(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", ts=_TS_EARLY),
            _record(model_id="m-a", ts=_TS_LATE),
            _record(model_id="m-a", ts=_TS_MID),
        ])
        r = c.get("/api/bot/shadow/stats")
        row = r.json()["records"][0]
        # Both timestamps round-trip as ISO-8601 with tz.
        assert row["first_seen"].startswith("2026-05-10T10:00:00")
        assert row["last_seen"].startswith("2026-05-10T14:00:00")

    def test_row_keys_seen_serialized_sorted(self, client):
        c, log = client
        _seed_log(log, [
            _record(model_id="m-a", row_keys=["b", "a"]),
            _record(model_id="m-a", row_keys=["c", "a"]),
        ])
        r = c.get("/api/bot/shadow/stats")
        row = r.json()["records"][0]
        assert row["row_keys_seen"] == ["a", "b", "c"]


class TestRouterMounted:
    def test_predictions_route_in_openapi(self, client):
        c, log = client
        r = c.get("/openapi.json")
        spec = r.json()
        assert "/api/bot/shadow/predictions" in spec["paths"]
        assert "/api/bot/shadow/stats" in spec["paths"]
