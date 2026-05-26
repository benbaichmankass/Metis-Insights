"""Tests for the M12 S1 Telegram → FCM mirror in src/runtime/notify.py.

Every operator-facing Telegram message should also fire
``publish_event("telegram", {...})`` so the Android companion app gets a
push for it. The hook lives in ``send_telegram_direct`` because every
higher-level helper (``send_to_operator``, ``notify_operator``,
``send_via_alert_manager``) funnels through there.

These tests pin three invariants:

1. Successful Telegram send → mirror fires once with the message text.
2. Telegram credentials missing → mirror STILL fires (push as a strict
   superset of Telegram delivery, so the operator can see the message
   even when Telegram is unreachable).
3. ``publish_event`` raising MUST NOT propagate into ``send_telegram_direct``
   — the trader / hourly report / watchdog must not fail because the
   notifier blew up.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from src.runtime import notify


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Clear Telegram creds by default; individual tests override."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    yield


def test_mirror_fires_on_successful_send(monkeypatch):
    """Happy path: creds set, urlopen returns 2xx + ok=true, mirror fires."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake-chat")

    captured = []

    def _capture(kind, payload):
        captured.append((kind, payload))

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _capture)

    fake_resp = mock.MagicMock()
    fake_resp.getcode.return_value = 200
    fake_resp.read.return_value = json.dumps(
        {"ok": True, "result": {"message_id": 42}}
    ).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        notify.send_telegram_direct("hello operator", parse_mode="HTML")

    assert len(captured) == 1
    kind, payload = captured[0]
    assert kind == "telegram"
    assert payload["text"] == "hello operator"
    assert payload["parse_mode"] == "HTML"


def test_mirror_fires_even_when_telegram_creds_missing(monkeypatch):
    """Push is a strict superset of Telegram delivery — fire the mirror
    even when the Telegram send path early-returns on missing creds."""
    captured = []

    def _capture(kind, payload):
        captured.append((kind, payload))

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _capture)

    notify.send_telegram_direct("ping with no telegram creds")

    assert len(captured) == 1
    assert captured[0][0] == "telegram"
    assert captured[0][1]["text"] == "ping with no telegram creds"
    assert captured[0][1]["parse_mode"] == "HTML"  # default arg


def test_mirror_exception_does_not_propagate(monkeypatch):
    """A bug in publish_event must not break the Telegram send. The hook
    is wrapped in try/except with an `allow-silent` justification; this
    test pins that the wrapper is in place and effective."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake-chat")

    def _boom(kind, payload):
        raise RuntimeError("mobile_push is busted")

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _boom)

    fake_resp = mock.MagicMock()
    fake_resp.getcode.return_value = 200
    fake_resp.read.return_value = json.dumps(
        {"ok": True, "result": {"message_id": 1}}
    ).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        # Should NOT raise — the wrapper swallows the RuntimeError above.
        notify.send_telegram_direct("hello even though notifier is broken")


def test_mirror_truncates_oversized_payload(monkeypatch):
    """FCM data messages cap at 4 KB. Long hourly reports get truncated."""
    captured = []

    def _capture(kind, payload):
        captured.append((kind, payload))

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _capture)

    long_message = "x" * 5000  # well over the 3000-char cap.
    notify.send_telegram_direct(long_message, parse_mode=None)

    assert len(captured) == 1
    text = captured[0][1]["text"]
    assert len(text) <= 3000
    assert text.endswith("…(truncated)")


def test_mirror_passes_parse_mode_through(monkeypatch):
    """The Android side may render Markdown / HTML differently from plain;
    the hint should reach the device."""
    captured = []

    def _capture(kind, payload):
        captured.append((kind, payload))

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _capture)

    notify.send_telegram_direct("plain text msg", parse_mode=None)
    assert captured[0][1]["parse_mode"] == "plain"

    captured.clear()
    notify.send_telegram_direct("markdown *bold*", parse_mode="MarkdownV2")
    assert captured[0][1]["parse_mode"] == "MarkdownV2"
