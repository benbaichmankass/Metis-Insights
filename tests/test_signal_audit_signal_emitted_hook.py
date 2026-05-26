"""Tests for the SIGNAL_EMITTED mobile-push observer hook in
``signal_audit_logger.log_signal`` (M12 S5).

The hook fires `publish_event(SIGNAL_EMITTED, …)` for buy/sell rows
only, mirroring the `/api/bot/signals` server-side filter so the same
rows the dashboard surfaces are the same rows that wake a subscribed
phone. The audit-writer path is the load-bearing invariant — a publish
failure must never propagate.
"""
from __future__ import annotations

import importlib
import sys
from typing import Any

# Same boilerplate as tests/test_s012_signal_audit.py — other tests
# install a MagicMock under this module name; force-reload the real one.
sys.modules.pop("src.utils.signal_audit_logger", None)
import src.utils.signal_audit_logger  # noqa: E402,F401
importlib.reload(src.utils.signal_audit_logger)


def _capture(monkeypatch, tmp_path) -> list[tuple[str, dict[str, Any]]]:
    """Stub publish_event so the test can assert on call args without
    touching the FCM stack."""
    from src.utils import signal_audit_logger as sal

    # Audit writer needs a real on-disk file.
    audit_file = tmp_path / "signal_audit.jsonl"
    monkeypatch.setattr(sal, "SIGNAL_FILE", audit_file)
    # Disable the dual-write to keep the test isolated from the DB.
    monkeypatch.setenv("SIGNAL_DUAL_WRITE_DISABLED", "true")

    seen: list[tuple[str, dict[str, Any]]] = []

    def fake_publish(kind: str, payload: dict[str, Any]) -> None:
        seen.append((kind, payload))

    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event", fake_publish, raising=True
    )
    return seen


def test_log_signal_fires_signal_emitted_on_buy(tmp_path, monkeypatch):
    from src.utils import signal_audit_logger as sal

    seen = _capture(monkeypatch, tmp_path)
    sal.log_signal({
        "event": "pipeline_result",
        "side": "buy",
        "symbol": "BTCUSDT",
        "strategy": "vwap",
        "pattern": "fade",
        "confidence": 0.72,
        "price": 80700.0,
    })
    assert len(seen) == 1
    kind, payload = seen[0]
    assert kind == "signal_emitted"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["side"] == "buy"
    assert payload["strategy"] == "vwap"
    assert payload["pattern"] == "fade"
    assert payload["confidence"] == 0.72
    assert payload["price"] == 80700.0


def test_log_signal_fires_signal_emitted_on_sell(tmp_path, monkeypatch):
    from src.utils import signal_audit_logger as sal

    seen = _capture(monkeypatch, tmp_path)
    sal.log_signal({"side": "sell", "symbol": "MES"})
    assert len(seen) == 1
    assert seen[0][0] == "signal_emitted"


def test_log_signal_fires_on_long_and_short_aliases(tmp_path, monkeypatch):
    from src.utils import signal_audit_logger as sal

    seen = _capture(monkeypatch, tmp_path)
    sal.log_signal({"side": "long", "symbol": "ETHUSDT"})
    sal.log_signal({"side": "short", "symbol": "ETHUSDT"})
    assert len(seen) == 2
    assert all(k == "signal_emitted" for k, _ in seen)


def test_log_signal_skips_non_directional_rows(tmp_path, monkeypatch):
    """Rows without a buy/sell/long/short side don't fire the hook —
    avoids flooding the operator with "candle observed" / "no signal"
    pipeline-tick events.
    """
    from src.utils import signal_audit_logger as sal

    seen = _capture(monkeypatch, tmp_path)
    sal.log_signal({"event": "pipeline_tick", "symbol": "BTCUSDT"})
    sal.log_signal({"event": "no_signal", "symbol": "BTCUSDT"})
    sal.log_signal({"side": "none", "symbol": "BTCUSDT"})
    sal.log_signal({"side": "", "symbol": "BTCUSDT"})
    sal.log_signal({})
    assert seen == []


def test_log_signal_side_is_case_insensitive(tmp_path, monkeypatch):
    """Some upstream emitters pass `'Buy'` or `'BUY'`; the filter should
    accept those without forcing the caller to normalize first."""
    from src.utils import signal_audit_logger as sal

    seen = _capture(monkeypatch, tmp_path)
    sal.log_signal({"side": "BUY", "symbol": "BTCUSDT"})
    sal.log_signal({"side": "Sell", "symbol": "BTCUSDT"})
    assert len(seen) == 2


def test_log_signal_drops_null_fields_from_payload(tmp_path, monkeypatch):
    """FCM data messages don't allow nulls — the hook strips None-valued
    keys so the underlying dispatcher doesn't have to."""
    from src.utils import signal_audit_logger as sal

    seen = _capture(monkeypatch, tmp_path)
    sal.log_signal({
        "side": "buy",
        "symbol": "BTCUSDT",
        "strategy": None,    # never populated for this row
        "confidence": None,
        "price": 80000,
    })
    assert len(seen) == 1
    payload = seen[0][1]
    assert "symbol" in payload
    assert "price" in payload
    assert "strategy" not in payload
    assert "confidence" not in payload


def test_log_signal_swallows_publish_exception(tmp_path, monkeypatch):
    """The audit writer must keep functioning even if mobile_push throws
    — that's the load-bearing invariant of the observer-only trust
    contract."""
    from src.utils import signal_audit_logger as sal

    audit_file = tmp_path / "signal_audit.jsonl"
    monkeypatch.setattr(sal, "SIGNAL_FILE", audit_file)
    monkeypatch.setenv("SIGNAL_DUAL_WRITE_DISABLED", "true")

    def raising_publish(kind: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("FCM blew up")

    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event", raising_publish, raising=True
    )
    # Must not raise.
    sal.log_signal({"side": "buy", "symbol": "BTCUSDT"})
    # Audit row should still have been written before the publish failed.
    assert audit_file.exists()
    contents = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(contents) == 1


def test_signal_emitted_promoted_to_in_flight() -> None:
    """When the wire-up lands, IN_FLIGHT must include SIGNAL_EMITTED —
    the runbook + Android UI both lean on this to flip the kind from
    "reserved" to "in flight"."""
    from src.runtime.mobile_push import event_kinds

    assert event_kinds.SIGNAL_EMITTED in event_kinds.IN_FLIGHT
