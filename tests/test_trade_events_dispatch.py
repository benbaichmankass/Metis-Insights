"""Tests for the trade-lifecycle notification dispatcher (trade_events).

Covers:
- The typed FCM publish fires inline (so the journal observer hooks keep
  their synchronous-capture contract).
- Paper / live tagging in the formatted Telegram line.
- The ``TRADE_EVENT_TELEGRAM_DISABLED`` rollback lever suppresses the
  Telegram send (no thread, no call).
- ``notify_trade_event`` never raises even if the FCM publish blows up.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.runtime.mobile_push import trade_events


def test_format_tags_paper_and_live() -> None:
    paper = trade_events.format_trade_event_message(
        "trade_opened",
        {"symbol": "MES", "direction": "buy", "account_class": "paper"},
    )
    live = trade_events.format_trade_event_message(
        "trade_closed",
        {"symbol": "BTCUSDT", "direction": "sell", "pnl": 3.0, "account_class": "real_money"},
    )
    assert "[paper]" in paper
    assert "[live]" in live


def test_notify_publishes_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda kind, payload: captured.append((kind, payload)),
    )
    # Disable Telegram so the test doesn't spawn a network thread.
    monkeypatch.setenv("TRADE_EVENT_TELEGRAM_DISABLED", "1")

    trade_events.notify_trade_event("trade_closed", {"symbol": "BTCUSDT"})

    assert captured == [("trade_closed", {"symbol": "BTCUSDT"})]


def test_telegram_disabled_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.runtime.mobile_push.publish_event", lambda *a, **k: None)
    monkeypatch.setenv("TRADE_EVENT_TELEGRAM_DISABLED", "true")
    sent: list[str] = []
    monkeypatch.setattr(
        "src.runtime.notify.send_telegram_direct",
        lambda *a, **k: sent.append("called"),
    )

    trade_events.notify_trade_event("trade_opened", {"symbol": "MES"})
    time.sleep(0.05)  # would-be thread window

    assert sent == []


def test_notify_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _boom)
    monkeypatch.setenv("TRADE_EVENT_TELEGRAM_DISABLED", "1")

    # Must not raise.
    trade_events.notify_trade_event("trade_closed", {"symbol": "BTCUSDT"})
