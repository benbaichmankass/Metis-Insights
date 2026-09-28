"""Tests for the shadow-predictions dashboard endpoints
(S-AI-WS8-PART-2).

Verifies the two GET routes (`/api/bot/shadow/predictions` and
`/api/bot/shadow/stats`) over a temporary
`runtime_logs/shadow_predictions.jsonl` populated with seeded
records. Reuses the inspector module's parsing so behavior stays
identical to the CLI from PART-1.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from src.web.api import main as api_main  # noqa: E402


_TS_EARLY = "2026-05-10T10:00:00+00:00"
_TS_MID = "2026-05-10T12:00:00+00:00"
_TS_LATE = "2026-05-10T14:00:00+00:00"


def _ts_ago(days: float) -> str:
    """ISO-8601 UTC timestamp `days` before "now" (wall clock at test run)."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


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


def _seed_rotated(active_log: Path, suffix: str, records: list[dict], *, gz: bool = False) -> Path:
    """Write a rotated archive next to `active_log`, named the way
    `scripts/ops/rotate_shadow_log.py` names one:
    `<stem>.<suffix>.jsonl` (optionally gzipped to `...jsonl.gz`, which is
    how production runs it — `deploy/ict-shadow-log-rotate.service` passes
    `--gzip`)."""
    active_log.parent.mkdir(parents=True, exist_ok=True)
    stem = active_log.stem
    ext = active_log.suffix or ".jsonl"
    body = "\n".join(json.dumps(r) for r in records) + "\n"
    if gz:
        target = active_log.with_name(f"{stem}.{suffix}{ext}.gz")
        with gzip.open(target, "wt", encoding="utf-8") as fh:
            fh.write(body)
    else:
        target = active_log.with_name(f"{stem}.{suffix}{ext}")
        target.write_text(body)
    return target


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
        # Pass via params= so the client URL-encodes `+` in the tz offset
        # ('+' in a raw query string decodes to a space server-side).
        r = c.get("/api/bot/shadow/predictions", params={"since": _TS_MID})
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
        # Pass via params= so the client URL-encodes `+` in the tz offset
        # ('+' in a raw query string decodes to a space server-side).
        r = c.get("/api/bot/shadow/stats", params={"since": _TS_MID})
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


class TestDriftEndpointRotation:
    """PI-20260927-YZRZQ725-0001 / review-pack D1.

    `ict-shadow-log-rotate.timer` rotates the active log roughly every
    25-29 days in production, which is SHORTER than this endpoint's default
    30-day `reference_days` window. A drift read that only opens the active
    log therefore truncates its own reference window at the last rotation
    boundary, and every advisory model reads `insufficient_data` for 1-2
    weeks after each rotation — which is why no drift-based demote/hold
    decision this repo has made (including "E72") was ever computed on a
    genuinely full window. These tests construct that exact rotation
    boundary inside the window and assert the fix reads through it, plus a
    positive control proving the fix does not just always report
    "sufficient".
    """

    def test_rotation_boundary_inside_window_fills_reference_window(self, client):
        """Active log covers the last ~5 days; a rotated (gzipped) archive
        covers the prior ~25 days. The rotation boundary between them sits
        squarely inside the default 30-day reference window (37d-7d ago).
        Pre-fix, only the active log is read and the reference window comes
        back empty -> `insufficient_data`. Post-fix it must read through the
        archive and return real drift stats."""
        c, log = client
        # Rotated archive: 5 records spread across ~30d ago to ~8d ago, all
        # inside [reference_start, current_start) = [now-37d, now-7d).
        _seed_rotated(
            log, "2026-08-27",
            [
                _record(model_id="mes-regime-5m-lgbm-v2", score=s, ts=_ts_ago(d))
                for d, s in [(30, 0.10), (25, 0.15), (20, 0.20), (14, 0.25), (8, 0.30)]
            ],
            gz=True,
        )
        # Active log (post-rotation, fresh file): 3 records in the current
        # window [now-7d, now).
        _seed_log(log, [
            _record(model_id="mes-regime-5m-lgbm-v2", score=s, ts=_ts_ago(d))
            for d, s in [(5, 0.60), (3, 0.65), (1, 0.70)]
        ])
        r = c.get("/api/bot/shadow/drift", params={"model_id": "mes-regime-5m-lgbm-v2"})
        assert r.status_code == 200
        body = r.json()
        # The whole point of the fix: this must NOT be insufficient_data.
        assert body["verdict"] != "insufficient_data", body
        assert body["reference_count"] == 5
        assert body["current_count"] == 3
        assert body["rotated_logs_read"]["count"] == 1
        assert body["rotated_logs_read"]["paths"][0].endswith(".gz")
        # Real stats were actually computed, not just a non-"insufficient" label.
        assert body["reference_mean"] == pytest.approx(0.20, abs=1e-6)
        assert body["current_mean"] == pytest.approx(0.65, abs=1e-6)
        assert isinstance(body["ks"], float)
        assert isinstance(body["psi"], float)

    def test_multiple_rotations_all_read(self, client):
        """Two rotation events (one gzipped, one plain) both inside the
        window are both folded in — the union is not just "the single most
        recent archive"."""
        c, log = client
        _seed_rotated(
            log, "2026-08-01",
            [_record(model_id="m-a", score=0.1, ts=_ts_ago(32))],
            gz=False,
        )
        _seed_rotated(
            log, "2026-08-20",
            [_record(model_id="m-a", score=0.2, ts=_ts_ago(15))],
            gz=True,
        )
        _seed_log(log, [_record(model_id="m-a", score=0.9, ts=_ts_ago(2))])
        r = c.get("/api/bot/shadow/drift", params={"model_id": "m-a"})
        body = r.json()
        assert body["rotated_logs_read"]["count"] == 2
        assert body["reference_count"] == 2
        assert body["current_count"] == 1
        assert body["verdict"] != "insufficient_data"

    def test_genuinely_insufficient_data_still_reported_as_such(self, client):
        """Positive control: an archive is present and gets read (proving the
        fix isn't dormant), but every one of its records sits BEFORE the
        reference window's own start, and the active log has nothing in the
        reference window either. `reference_count` must be genuinely 0 and
        the verdict must still be `insufficient_data` — the fix must not make
        every case report as sufficient regardless of the real data."""
        c, log = client
        # 60 days ago is well before reference_start (now-37d) -- out of
        # window entirely, so it must NOT be counted.
        _seed_rotated(
            log, "2026-06-01",
            [_record(model_id="m-b", score=0.4, ts=_ts_ago(60))],
        )
        # Active log only has current-window data -- no reference-window
        # coverage from either source.
        _seed_log(log, [_record(model_id="m-b", score=0.9, ts=_ts_ago(2))])
        r = c.get("/api/bot/shadow/drift", params={"model_id": "m-b"})
        assert r.status_code == 200
        body = r.json()
        # The archive WAS read...
        assert body["rotated_logs_read"]["count"] == 1
        # ...but correctly contributed nothing to the reference window.
        assert body["reference_count"] == 0
        assert body["current_count"] == 1
        assert body["verdict"] == "insufficient_data"

    def test_no_rotation_present_behaves_as_before(self, client):
        """No archive next to the active log -> unchanged single-file
        behavior, `rotated_logs_read.count == 0`."""
        c, log = client
        _seed_log(log, [
            _record(model_id="m-c", score=0.3, ts=_ts_ago(20)),
            _record(model_id="m-c", score=0.9, ts=_ts_ago(1)),
        ])
        r = c.get("/api/bot/shadow/drift", params={"model_id": "m-c"})
        body = r.json()
        assert body["rotated_logs_read"]["count"] == 0
        assert body["rotated_logs_read"]["paths"] == []
        assert body["reference_count"] == 1
        assert body["current_count"] == 1
        assert body["verdict"] != "insufficient_data"


class TestRouterMounted:
    def test_predictions_route_in_openapi(self, client):
        c, log = client
        r = c.get("/openapi.json")
        spec = r.json()
        assert "/api/bot/shadow/predictions" in spec["paths"]
        assert "/api/bot/shadow/stats" in spec["paths"]
        assert "/api/bot/shadow/drift" in spec["paths"]


class TestForecastServeBlock:
    """FIX-CA-24: /stats surfaces the served forecast's bar lag."""

    def test_not_written_when_absent(self, client):
        c, log = client
        body = c.get("/api/bot/shadow/stats").json()
        assert body["forecast_serve"]["read_state"] == "not_written"
        assert body["forecast_serve"]["status"] is None

    def test_observed_status_is_surfaced(self, client):
        c, log = client
        log.parent.mkdir(parents=True, exist_ok=True)
        (log.parent / "forecast_live_status.json").write_text(json.dumps({
            "counters": {"fc_served": 3, "fc_stale": 1},
            "symbols": {"BTCUSDT": {"served": False, "lag_bars": 960}},
        }))
        fs = c.get("/api/bot/shadow/stats").json()["forecast_serve"]
        assert fs["read_state"] == "observed"
        assert fs["status"]["symbols"]["BTCUSDT"]["lag_bars"] == 960

    def test_unreadable_is_distinct(self, client):
        c, log = client
        log.parent.mkdir(parents=True, exist_ok=True)
        (log.parent / "forecast_live_status.json").write_text("{not json")
        fs = c.get("/api/bot/shadow/stats").json()["forecast_serve"]
        assert fs["read_state"] == "unreadable"
