"""GET /api/bot/signals — DB-canonical cutover (WC-5, dashboard-truth).

The signals endpoint reads ``trade_journal.db::signals`` (the dual-write
target) as the canonical source, with ``signal_audit.jsonl`` as the fallback.
The single rollback ``SIGNAL_DUAL_WRITE_DISABLED`` couples both sides: it stops
the writer AND flips the reader back to the JSONL audit (never a frozen DB).

Pins:
  * DB rows serve the endpoint (canonical), mapped to the same shape.
  * The buy/sell filter + zone geometry survive the meta round-trip through SQL.
  * Empty table → JSONL fallback (DB not hydrated yet).
  * SIGNAL_DUAL_WRITE_DISABLED → JSONL fallback even when the DB has rows.
  * Fail-loud dual-write: a DB failure escalates once per episode, never raises.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.api import main as api_main
from src.web.api.routers import dashboard as dashboard_router


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate both stores: the audit log (module constant) and the canonical
    DB (env). Dual-write defaults ENABLED (env unset) so the DB is canonical."""
    audit = tmp_path / "signal_audit.jsonl"
    audit.touch()
    monkeypatch.setattr(dashboard_router, "_AUDIT_LOG", audit)
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.delenv("SIGNAL_DUAL_WRITE_DISABLED", raising=False)
    return audit


def _seed_db(**fields) -> None:
    from src.units.db.database import Database

    db = Database()
    db.create_tables()
    db.insert_signal(fields)


def _write_jsonl(audit: Path, records: list[dict]) -> None:
    audit.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


class TestDbCanonical:
    def test_db_row_serves_endpoint(self, client: TestClient) -> None:
        _seed_db(strategy="vwap", symbol="BTCUSDT", side="buy",
                 pattern="vwap", confidence=0.7, price=80700.0)
        body = client.get("/api/bot/signals").json()
        assert len(body) == 1
        assert body[0]["symbol"] == "BTCUSDT"
        assert body[0]["side"] == "buy"
        assert body[0]["pattern"] == "vwap"
        assert body[0]["confidence"] == 0.7
        assert body[0]["price"] == 80700.0

    def test_db_wins_over_jsonl(self, client: TestClient, isolate: Path) -> None:
        # JSONL says one thing, DB says another — DB is canonical.
        _write_jsonl(isolate, [{"symbol": "ETHUSDT", "side": "sell"}])
        _seed_db(strategy="vwap", symbol="BTCUSDT", side="buy")
        body = client.get("/api/bot/signals").json()
        assert len(body) == 1
        assert body[0]["symbol"] == "BTCUSDT"

    def test_zone_geometry_survives_sql_meta_roundtrip(self, client: TestClient) -> None:
        # fvg_*/sweep_level ride in the meta JSON; the reader expands + draws them.
        _seed_db(strategy="ict_scalp_5m", symbol="BTCUSDT", side="buy",
                 fvg_low=75300.0, fvg_high=75200.0, sweep_level=75150.0)
        body = client.get("/api/bot/signals").json()
        zones = {z["kind"]: z for z in body[0]["zones"]}
        assert (zones["fvg"]["low"], zones["fvg"]["high"]) == (75200.0, 75300.0)
        assert zones["sweep"]["price"] == 75150.0

    def test_non_actionable_rows_filtered(self, client: TestClient) -> None:
        # Eval/tick rows (no buy/sell side) are written to the table too;
        # the endpoint filters them out exactly like the JSONL path did.
        _seed_db(strategy="vwap", symbol="BTCUSDT", side="observe")
        _seed_db(strategy="vwap", symbol="BTCUSDT", side="buy")
        body = client.get("/api/bot/signals").json()
        assert len(body) == 1
        assert body[0]["side"] == "buy"


class TestFallbackCoupling:
    def test_empty_table_falls_back_to_jsonl(
        self, client: TestClient, isolate: Path
    ) -> None:
        _write_jsonl(isolate, [{"symbol": "BTCUSDT", "side": "buy", "pattern": "p"}])
        body = client.get("/api/bot/signals").json()  # DB empty → JSONL
        assert len(body) == 1
        assert body[0]["symbol"] == "BTCUSDT"

    def test_disabled_dualwrite_falls_back_to_jsonl(
        self, client: TestClient, isolate: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even with DB rows present, the rollback flag forces the JSONL audit
        # (the DB would be frozen once the writer stops).
        _seed_db(strategy="vwap", symbol="BTCUSDT", side="buy")
        _write_jsonl(isolate, [{"symbol": "ETHUSDT", "side": "sell"}])
        monkeypatch.setenv("SIGNAL_DUAL_WRITE_DISABLED", "true")
        body = client.get("/api/bot/signals").json()
        assert len(body) == 1
        assert body[0]["symbol"] == "ETHUSDT"  # served from JSONL


class TestFailLoudWriter:
    def test_dual_write_failure_escalates_once_then_quiets(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        import src.utils.signal_audit_logger as sal

        # Force the DB insert to raise.
        class _BoomDB:
            def insert_signal(self, _payload):  # noqa: ANN001
                raise RuntimeError("db locked")

        monkeypatch.setattr(sal, "_dual_write_failing", False, raising=False)
        monkeypatch.setattr(
            "src.units.db.database.Database", lambda *a, **k: _BoomDB()
        )
        monkeypatch.delenv("SIGNAL_DUAL_WRITE_DISABLED", raising=False)

        with caplog.at_level(logging.ERROR):
            sal._dual_write_to_db({"symbol": "BTCUSDT", "side": "buy"})  # raises internally
        # First failure escalates to ERROR (never propagates out).
        assert any("dual-write FAILED" in r.message for r in caplog.records)

        caplog.clear()
        with caplog.at_level(logging.ERROR):
            sal._dual_write_to_db({"symbol": "BTCUSDT", "side": "buy"})
        # Second consecutive failure does NOT re-escalate (deduped).
        assert not any("dual-write FAILED" in r.message for r in caplog.records)

    def test_dual_write_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.utils.signal_audit_logger as sal

        def _boom(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("boom")

        monkeypatch.setattr("src.units.db.database.Database", _boom)
        monkeypatch.delenv("SIGNAL_DUAL_WRITE_DISABLED", raising=False)
        # Must swallow — the JSONL audit upstream is the durable record.
        sal._dual_write_to_db({"symbol": "BTCUSDT", "side": "buy"})
