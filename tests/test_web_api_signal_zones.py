"""GET /api/bot/signals — drawable `zones` from logged decision geometry.

The signal builders log the geometry the strategy already computed
(ict_scalp's fvg_low/high + sweep_level). The endpoint turns those into
a generic ``zones`` list the dashboard chart draws — never a separately
computed indicator.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from src.web.api import main as api_main
    from src.web.api.routers import dashboard as dbr

    audit = tmp_path / "signal_audit.jsonl"
    monkeypatch.setattr(dbr, "_AUDIT_LOG", audit)
    return TestClient(api_main.app, raise_server_exceptions=False), audit


def _write(audit: Path, records: list[dict]) -> None:
    audit.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_fvg_and_sweep_zones_surface(client):
    c, audit = client
    _write(audit, [{
        "event": "ict_scalp_eval", "strategy": "ict_scalp_5m", "symbol": "BTCUSDT",
        "side": "buy", "entry": 75729.4, "stop_loss": 75348.25, "take_profit": 76301.1,
        "confidence": 0.64, "fvg_low": 75200.0, "fvg_high": 75300.0, "sweep_level": 75150.0,
    }])
    body = c.get("/api/bot/signals").json()
    assert len(body) == 1
    zones = {z["kind"]: z for z in body[0]["zones"]}
    assert zones["fvg"]["low"] == 75200.0 and zones["fvg"]["high"] == 75300.0
    assert zones["sweep"]["price"] == 75150.0


def test_fvg_bounds_normalised(client):
    # low/high recorded out of order still surface as low<=high.
    c, audit = client
    _write(audit, [{"symbol": "BTCUSDT", "side": "buy", "fvg_low": 100.0, "fvg_high": 90.0}])
    fvg = next(z for z in c.get("/api/bot/signals").json()[0]["zones"] if z["kind"] == "fvg")
    assert (fvg["low"], fvg["high"]) == (90.0, 100.0)


def test_no_geometry_means_empty_zones(client):
    c, audit = client
    _write(audit, [{"strategy": "vwap", "symbol": "BTCUSDT", "side": "sell", "entry": 1.0}])
    assert c.get("/api/bot/signals").json()[0]["zones"] == []
