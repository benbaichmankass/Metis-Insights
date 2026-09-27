"""Test the per-tick alert drainer added in `src/main.py`.

The coordinator's circuit breaker (PR #741) and other callers push
alerts onto the in-process queue at `src.units.dashboards.alerts` via
`push_alert(...)`. Pre-this-PR the queue had no autonomous consumer, so
critical alerts (e.g. "account auto-paused after N rejections") only
surfaced when the operator manually issued `/alerts` on Telegram.

This module exercises the drainer's contract: critical alerts are
forwarded; lower levels are not; pop_alerts() drains the queue; a
Telegram-send exception does not break the drainer.
"""
from __future__ import annotations

from typing import List

import pytest


class _RecordingTelegramClient:
    def __init__(self) -> None:
        self.sent: List[str] = []

    def send_message(self, message: str) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _isolate_alerts_queue():
    """Each test runs against a fresh queue so cross-test push leakage
    can't mask drainer bugs."""
    from src.units.dashboards.alerts import clear_alerts
    clear_alerts()
    yield
    clear_alerts()


def _push(level: str, message: str, source: str = "coord") -> None:
    from src.units.dashboards.alerts import push_alert
    push_alert(message, source=source, level=level)


def test_drainer_forwards_critical_to_telegram():
    from src.main import _drain_critical_alerts

    _push("critical", "bybit_2 auto-paused after 3 consecutive rejections")
    tg = _RecordingTelegramClient()
    _drain_critical_alerts(tg)

    assert len(tg.sent) == 1
    assert "CRITICAL" in tg.sent[0]
    assert "bybit_2 auto-paused" in tg.sent[0]


def test_drainer_skips_info_and_warning_levels():
    from src.main import _drain_critical_alerts

    _push("info", "trade filled")
    _push("warning", "borrow availability low")
    tg = _RecordingTelegramClient()
    _drain_critical_alerts(tg)

    assert tg.sent == []


def test_drainer_drains_queue_so_alerts_dont_repeat():
    from src.main import _drain_critical_alerts
    from src.units.dashboards.alerts import list_alerts

    _push("critical", "msg-1")
    tg = _RecordingTelegramClient()
    _drain_critical_alerts(tg)
    assert len(tg.sent) == 1
    # The queue should be empty now.
    assert list_alerts() == []
    # Second drain on an empty queue is a no-op.
    _drain_critical_alerts(tg)
    assert len(tg.sent) == 1


def test_drainer_swallows_telegram_exception():
    """A Telegram outage must not propagate into the trader loop —
    `run_one_tick` calls the drainer at the very end and a raised
    exception would mark the tick as a failure."""
    from src.main import _drain_critical_alerts

    class _BoomClient:
        def send_message(self, _msg: str) -> None:
            raise RuntimeError("telegram is down")

    _push("critical", "boom")
    # Must not raise
    _drain_critical_alerts(_BoomClient())


def test_drainer_processes_multiple_criticals_in_fifo_order():
    from src.main import _drain_critical_alerts

    _push("critical", "first")
    _push("critical", "second")
    _push("info", "ignore me")
    _push("critical", "third")

    tg = _RecordingTelegramClient()
    _drain_critical_alerts(tg)

    assert len(tg.sent) == 3
    assert tg.sent[0].endswith("first")
    assert tg.sent[1].endswith("second")
    assert tg.sent[2].endswith("third")


def test_drainer_handles_missing_message_or_source():
    """Defensive: alerts pushed with weird payloads must not break
    the drainer."""
    from src.main import _drain_critical_alerts
    from src.units.dashboards.alerts import push_alert

    push_alert("", source="", level="critical")
    tg = _RecordingTelegramClient()
    _drain_critical_alerts(tg)

    assert len(tg.sent) == 1


# --- FIX-CA-13 (CA-A08-critical-alert-dropped-on-send-failure) -------------


def _critical_in_queue():
    from src.units.dashboards.alerts import list_alerts
    return [a for a in list_alerts() if a.get("level") == "critical"]


def test_alert_manager_send_alert_reports_failure(monkeypatch):
    """A failed Telegram send must be reported (False), not swallowed to
    None — otherwise no caller can tell the alert was lost."""
    import telegram

    from src.bot.alert_manager import AlertManager

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class _BoomBot:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send_message(self, **_k):
            raise RuntimeError("network down")

    monkeypatch.setattr(telegram, "Bot", _BoomBot)
    am = AlertManager()
    assert am.send_alert("hello") is False


def test_alert_manager_send_alert_reports_success(monkeypatch):
    import telegram

    from src.bot.alert_manager import AlertManager

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []

    class _OkBot:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send_message(self, **k):
            sent.append(k["text"])

    monkeypatch.setattr(telegram, "Bot", _OkBot)
    assert AlertManager().send_alert("hello") is True
    assert sent == ["hello"]


def test_adapter_propagates_send_result():
    from src.main import _AlertManagerAdapter

    class _AM:
        def send_alert(self, _m):
            return False

    assert _AlertManagerAdapter(_AM()).send_message("x") is False


def test_drainer_requeues_critical_on_raised_send():
    from src.main import _drain_critical_alerts

    class _BoomClient:
        def send_message(self, _msg: str) -> None:
            raise RuntimeError("telegram is down")

    _push("critical", "account auto-paused")
    _drain_critical_alerts(_BoomClient())
    q = _critical_in_queue()
    assert len(q) == 1 and q[0]["message"] == "account auto-paused"


def test_drainer_requeues_critical_on_false_send_then_delivers():
    from src.main import _drain_critical_alerts

    class _FlakyClient:
        def __init__(self):
            self.ok = False
            self.sent = []

        def send_message(self, msg: str):
            if not self.ok:
                return False
            self.sent.append(msg)
            return True

    _push("critical", "account auto-paused")
    tg = _FlakyClient()
    _drain_critical_alerts(tg)
    assert len(_critical_in_queue()) == 1  # kept for the next tick
    tg.ok = True
    _drain_critical_alerts(tg)
    assert _critical_in_queue() == []
    assert len(tg.sent) == 1 and "account auto-paused" in tg.sent[0]


def test_drainer_retries_are_bounded(caplog):
    from src.main import _CRITICAL_ALERT_MAX_ATTEMPTS, _drain_critical_alerts

    class _AlwaysFalse:
        def send_message(self, _msg: str):
            return False

    _push("critical", "never delivered")
    for _ in range(_CRITICAL_ALERT_MAX_ATTEMPTS + 2):
        _drain_critical_alerts(_AlwaysFalse())
    assert _critical_in_queue() == []
    assert any("UNDELIVERED" in r.getMessage() for r in caplog.records)
