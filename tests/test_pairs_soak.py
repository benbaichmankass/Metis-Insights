"""Tests for src/runtime/pairs_soak.py — builder/writer/reader trio (temp path)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.runtime import pairs_soak as psoak  # noqa: E402


def test_builder_shape_and_drops_none():
    r = psoak.build_pairs_soak_record(event="open", pair="SOLUSDT/BTCUSDT",
                                      symbol_a="SOLUSDT", symbol_b="BTCUSDT",
                                      account_id="bybit_1", z=2.3, dropped=None, qty_a=25.0)
    assert r["event"] == "open" and r["pair"] == "SOLUSDT/BTCUSDT"
    assert r["z"] == 2.3 and r["qty_a"] == 25.0
    assert "dropped" not in r          # None fields dropped
    assert "logged_at_utc" in r


def test_builder_rejects_empty():
    assert psoak.build_pairs_soak_record(event="", pair="X/Y", symbol_a="X",
                                         symbol_b="Y", account_id="a") is None


def test_write_then_read_roundtrip(tmp_path, monkeypatch):
    log = tmp_path / "pairs_soak.jsonl"
    monkeypatch.setattr(psoak, "soak_log_path", lambda: log)
    for ev, z in [("skip_flat", 0.4), ("open", 2.5), ("close", 0.1)]:
        rec = psoak.build_pairs_soak_record(event=ev, pair="SOLUSDT/BTCUSDT",
                                            symbol_a="SOLUSDT", symbol_b="BTCUSDT",
                                            account_id="bybit_1", z=z)
        assert psoak.record_pairs_soak(rec) is True
    env = psoak.read_soak_records(limit=100)
    assert env["present"] is True
    assert env["count"] == 3
    assert env["records"][0]["event"] == "close"      # newest-first
    assert env["summary"]["by_event"] == {"skip_flat": 1, "open": 1, "close": 1}
    # event filter
    only_open = psoak.read_soak_records(event="open")
    assert only_open["count"] == 1 and only_open["records"][0]["event"] == "open"


def test_read_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(psoak, "soak_log_path", lambda: tmp_path / "nope.jsonl")
    env = psoak.read_soak_records()
    assert env["present"] is False and env["count"] == 0


def test_router_imports():
    import importlib.util
    if importlib.util.find_spec("fastapi") is None:
        import pytest
        pytest.skip("fastapi not installed in this environment (present in CI)")
    from src.web.api.routers import pairs as pairs_router
    assert pairs_router.router is not None
