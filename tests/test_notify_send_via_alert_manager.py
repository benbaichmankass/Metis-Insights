"""Regression tests for src/runtime/notify.py.

Two bugs were silently dropping the operator's hourly summary for
multiple sprints (fixed CP-2026-05-02):

  * BUG (silent — operator never received hourlies):
    ``_send_via_alert_manager_async`` called ``mgr.send(message)`` on
    ``AlertManager``, but that class only exposes ``send_alert``. Every
    send raised ``AttributeError``, was caught by
    ``outcomes._send_telegram_or_queue``, and the message was queued in
    the pending-pings JSONL. Operator never saw the hourly report.

  * BUG (visible — ``/hourly failed: BadRequest: Can't parse entities``):
    ``send_telegram_direct`` hardcoded ``parse_mode="HTML"``. The
    hourly report is plain text containing ``<= 15m`` (literal less-
    than) which the HTML parser rejects.

The fix replaces the broken AlertManager dance with a direct stdlib
sync call to ``send_telegram_direct`` with ``parse_mode=None``, and
makes ``parse_mode`` configurable on ``send_telegram_direct``.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime import notify  # noqa: E402


class TestSendViaAlertManagerRouting:
    """``send_via_alert_manager`` MUST route through
    ``send_telegram_direct`` with plain-text mode (parse_mode=None).
    """

    def test_calls_send_telegram_direct_with_parse_mode_none(self):
        with patch.object(notify, "send_telegram_direct") as send:
            notify.send_via_alert_manager("hourly report content")
        send.assert_called_once()
        # parse_mode=None — content is plain text.
        assert send.call_args.kwargs.get("parse_mode") is None
        assert send.call_args.args[0] == "hourly report content"

    def test_propagates_send_failures_so_outcomes_can_queue(self):
        """``_send_telegram_or_queue`` in outcomes.py wraps this in a
        try/except and falls through to the pending-queue JSONL when it
        raises. The previous silent-failure path swallowed errors and
        starved the queue."""
        with patch.object(notify, "send_telegram_direct",
                          side_effect=RuntimeError("Telegram 503")):
            with pytest.raises(RuntimeError, match="Telegram 503"):
                notify.send_via_alert_manager("any message")

    def test_does_not_import_alertmanager(self):
        """Smoke check: the bug it had was ``mgr.send(message)`` on a
        class that only exposes ``send_alert``. The fix bypasses
        AlertManager entirely. Don't reintroduce that import.
        """
        import inspect
        src = inspect.getsource(notify)
        # The class name appears in the docstring (history); the call
        # to AlertManager(...) must not.
        assert "AlertManager()" not in src.replace(
            "via AlertManager", ""
        ), "send_via_alert_manager re-introduced the broken AlertManager dance"


class TestSendTelegramDirectParseMode:
    """``send_telegram_direct`` must accept ``parse_mode=None`` so plain-
    text content (hourly report) is delivered without the HTML parser
    rejecting ``<= 15m`` etc."""

    def _patch_credentials(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot:fake")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _patched_request(self):
        # Build a fake urlopen response context manager.
        body = b'{"ok": true, "result": {"message_id": 1}}'
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.read.return_value = body
        cm = MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        return cm

    def test_default_parse_mode_html_back_compat(self, monkeypatch):
        self._patch_credentials(monkeypatch)
        with patch("src.runtime.notify.urllib.request.urlopen",
                   return_value=self._patched_request()) as urlopen:
            notify.send_telegram_direct("<b>still works</b>")
        # The encoded payload should include parse_mode=HTML.
        call_args = urlopen.call_args
        request_obj = call_args.args[0]
        body = request_obj.data.decode("utf-8")
        assert "parse_mode=HTML" in body

    def test_parse_mode_none_omits_field(self, monkeypatch):
        """When parse_mode=None, the parse_mode field is not sent at
        all → Telegram defaults to plain text. The hourly report body
        contains literal ``<= 15m`` which would be a parse error in
        HTML mode."""
        self._patch_credentials(monkeypatch)
        plain = "Last tick: 12s ago (expected <= 15m)\nAll systems normal"
        with patch("src.runtime.notify.urllib.request.urlopen",
                   return_value=self._patched_request()) as urlopen:
            notify.send_telegram_direct(plain, parse_mode=None)
        call_args = urlopen.call_args
        request_obj = call_args.args[0]
        body = request_obj.data.decode("utf-8")
        assert "parse_mode" not in body, (
            "parse_mode=None must omit the field entirely so Telegram "
            "doesn't try to parse '<= 15m' as HTML"
        )
        assert "expected" in body  # text payload reached the wire

    def test_missing_credentials_no_op(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        # Should not raise; should not attempt urlopen.
        with patch("src.runtime.notify.urllib.request.urlopen") as urlopen:
            notify.send_telegram_direct("anything")
        urlopen.assert_not_called()
