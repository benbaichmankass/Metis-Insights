"""GET /api/bot/signals — drawable `zones` from logged decision geometry.

The signal builders log the geometry the strategy already computed
(ict_scalp's fvg_low/high + sweep_level). The endpoint turns those into
a generic ``zones`` list the dashboard chart draws — never a separately
computed indicator.

Fixture structure mirrors tests/test_dashboard_data_contract.py (module-top
imports + a dedicated _AUDIT_LOG isolation fixture) so the in-suite audit
log can't leak into these assertions.
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


@pytest.fixture
def isolate_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the signals endpoint at an isolated audit log.

    Post-WC-5 cutover ``/api/bot/signals`` reads the DB first; force the JSONL
    fallback (``SIGNAL_DUAL_WRITE_DISABLED``) so these zone-mapping tests
    exercise the audit log they write. The DB-canonical path + the fallback
    coupling are covered by ``tests/test_signals_db_cutover.py``. The mapping
    itself (``_map_signals``) is shared by both sources.
    """
    audit = tmp_path / "signal_audit.jsonl"
    audit.touch()
    monkeypatch.setattr(dashboard_router, "_AUDIT_LOG", audit)
    monkeypatch.setenv("SIGNAL_DUAL_WRITE_DISABLED", "true")
    return audit


def _write(audit: Path, records: list[dict]) -> None:
    audit.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_fvg_and_sweep_zones_surface(client: TestClient, isolate_audit: Path) -> None:
    _write(isolate_audit, [{
        "event": "ict_scalp_eval", "strategy": "ict_scalp_5m", "symbol": "BTCUSDT",
        "side": "buy", "entry": 75729.4, "stop_loss": 75348.25, "take_profit": 76301.1,
        "confidence": 0.64, "fvg_low": 75200.0, "fvg_high": 75300.0, "sweep_level": 75150.0,
    }])
    body = client.get("/api/bot/signals").json()
    assert len(body) == 1
    zones = {z["kind"]: z for z in body[0]["zones"]}
    assert zones["fvg"]["low"] == 75200.0 and zones["fvg"]["high"] == 75300.0
    assert zones["sweep"]["price"] == 75150.0


def test_fvg_bounds_normalised(client: TestClient, isolate_audit: Path) -> None:
    # low/high recorded out of order still surface as low<=high.
    _write(isolate_audit, [{"symbol": "BTCUSDT", "side": "buy", "fvg_low": 100.0, "fvg_high": 90.0}])
    body = client.get("/api/bot/signals").json()
    assert len(body) == 1
    fvg = next(z for z in body[0]["zones"] if z["kind"] == "fvg")
    assert (fvg["low"], fvg["high"]) == (90.0, 100.0)


def test_no_geometry_means_empty_zones(client: TestClient, isolate_audit: Path) -> None:
    _write(isolate_audit, [{"strategy": "vwap", "symbol": "BTCUSDT", "side": "sell", "entry": 1.0}])
    body = client.get("/api/bot/signals").json()
    assert len(body) == 1
    assert body[0]["zones"] == []
