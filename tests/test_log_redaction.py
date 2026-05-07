"""Tests that Telegram bot tokens are redacted from log output."""
from __future__ import annotations

import logging


from src.utils.log_redact import (
    RedactingFilter,
    _redact,
    install_redacting_filter,
    suppress_httpx_logging,
)

FAKE_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
TELEGRAM_GETME = f"https://api.telegram.org/bot{FAKE_TOKEN}/getMe"
TELEGRAM_SEND = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"


# ---------------------------------------------------------------------------
# Unit tests for _redact()
# ---------------------------------------------------------------------------

class TestRedactFunction:
    def test_redacts_telegram_url_getme(self):
        result = _redact(TELEGRAM_GETME)
        assert FAKE_TOKEN not in result
        assert "<REDACTED>" in result
        assert "getMe" in result

    def test_redacts_telegram_url_sendmessage(self):
        result = _redact(TELEGRAM_SEND)
        assert FAKE_TOKEN not in result
        assert "<REDACTED>" in result
        assert "sendMessage" in result

    def test_preserves_path_after_token(self):
        result = _redact(TELEGRAM_SEND)
        assert result.endswith("/sendMessage")

    def test_redacts_bare_token(self):
        result = _redact(f"token={FAKE_TOKEN} was used")
        assert FAKE_TOKEN not in result
        assert "<REDACTED_TOKEN>" in result

    def test_leaves_non_telegram_url_alone(self):
        url = "https://api.bybit.com/v5/market/tickers"
        assert _redact(url) == url

    def test_leaves_normal_message_alone(self):
        msg = "Pipeline tick completed successfully"
        assert _redact(msg) == msg

    def test_redacts_multiple_occurrences(self):
        text = f"GET {TELEGRAM_GETME} then POST {TELEGRAM_SEND}"
        result = _redact(text)
        assert FAKE_TOKEN not in result
        assert result.count("<REDACTED>") == 2

    def test_http_scheme_also_redacted(self):
        url = f"http://api.telegram.org/bot{FAKE_TOKEN}/getUpdates"
        result = _redact(url)
        assert FAKE_TOKEN not in result


# ---------------------------------------------------------------------------
# RedactingFilter attached to a logger
# ---------------------------------------------------------------------------

class TestRedactingFilter:
    def _capture_log(self, msg: str, *args) -> str:
        """Return the formatted log message after passing through the filter."""
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg=msg, args=args, exc_info=None,
        )
        filt.filter(record)
        return record.getMessage()

    def test_filter_redacts_url_in_msg(self):
        out = self._capture_log("Sending request to %s", TELEGRAM_SEND)
        assert FAKE_TOKEN not in out
        assert "sendMessage" in out

    def test_filter_redacts_url_in_dict_args(self):
        filt = RedactingFilter()
        # LogRecord stores dict-style args as a plain dict on record.args;
        # simulate that directly after construction.
        record = logging.LogRecord(
            name="test", level=logging.DEBUG,
            pathname="", lineno=0,
            msg="url=%(url)s",
            args=(),
            exc_info=None,
        )
        record.args = {"url": TELEGRAM_GETME}
        filt.filter(record)
        assert FAKE_TOKEN not in str(record.args)

    def test_filter_passes_safe_records(self):
        out = self._capture_log("Tick completed, result=%s", "ok")
        assert out == "Tick completed, result=ok"

    def test_filter_always_returns_true(self):
        filt = RedactingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0,
            msg=TELEGRAM_SEND, args=(), exc_info=None,
        )
        assert filt.filter(record) is True


# ---------------------------------------------------------------------------
# Integration: install_redacting_filter wires up the root logger
# ---------------------------------------------------------------------------

class TestInstallRedactingFilter:
    def test_filter_fires_before_handler(self, caplog):
        # Install the filter directly on the test logger so it fires before
        # caplog's handler captures the record (filters on a Logger run inside
        # Logger.handle(), before callHandlers()).
        test_logger = logging.getLogger("test_redact_integration")
        install_redacting_filter(test_logger)
        with caplog.at_level(logging.INFO, logger="test_redact_integration"):
            test_logger.info("Sending to %s", TELEGRAM_SEND)
        assert caplog.records, "Expected at least one log record"
        for record in caplog.records:
            assert FAKE_TOKEN not in record.getMessage(), (
                f"Token leaked in log record: {record.getMessage()!r}"
            )


# ---------------------------------------------------------------------------
# suppress_httpx_logging sets levels to WARNING
# ---------------------------------------------------------------------------

class TestSuppressHttpxLogging:
    def test_httpx_logger_at_warning(self):
        import logging as _logging
        suppress_httpx_logging()
        assert _logging.getLogger("httpx").level == _logging.WARNING

    def test_httpcore_logger_at_warning(self):
        import logging as _logging
        suppress_httpx_logging()
        assert _logging.getLogger("httpcore").level == _logging.WARNING
