"""Tests for src/runtime/api_reporting.py — S-023 PR3."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.api_reporting import (
    _excerpt,
    _redact_for_telegram,
    report_api_failure,
)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_strips_long_token():
    raw = "key=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456 more"
    out = _redact_for_telegram(raw)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456" not in out
    assert "<REDACTED" in out


def test_redact_strips_kv_api_key():
    raw = '{"api_key": "abcdef1234567890", "other": "ok"}'
    out = _redact_for_telegram(raw)
    assert "abcdef1234567890" not in out
    assert "REDACTED" in out


def test_redact_strips_bearer_authorization():
    raw = "Authorization: Bearer xyzxyzxyzxyzxyzxyz"
    out = _redact_for_telegram(raw)
    assert "xyzxyzxyzxyzxyzxyz" not in out


def test_redact_strips_camelcase_apikey():
    raw = '{"apiKey": "myActualSecretKey", "data": 1}'
    out = _redact_for_telegram(raw)
    assert "myActualSecretKey" not in out


def test_redact_preserves_short_tokens_and_normal_text():
    raw = 'short=abc plain English text retCode=10003 retMsg="API key is invalid"'
    out = _redact_for_telegram(raw)
    # Short value isn't long-token-shaped — must remain
    assert "short=abc" in out
    # retCode is fine to keep
    assert "retCode=10003" in out
    assert "API key is invalid" in out


def test_redact_handles_non_string_input():
    out = _redact_for_telegram(12345)
    assert isinstance(out, str)
    assert "12345" in out


# ---------------------------------------------------------------------------
# Excerpt rendering
# ---------------------------------------------------------------------------


def test_excerpt_renders_dict_as_json():
    out = _excerpt({"retCode": 10003, "retMsg": "bad"})
    assert "retCode" in out and "10003" in out


def test_excerpt_truncates_long_payloads():
    # Use a varied string with spaces so the long-token redactor
    # doesn't collapse the whole payload.
    huge = {"messages": [
        f"row {i}: lorem ipsum dolor sit amet, consectetur adipiscing"
        for i in range(200)
    ]}
    out = _excerpt(huge, max_chars=100)
    assert len(out) <= 100
    assert out.endswith("...")


def test_redact_preserves_short_id_strings():
    """Short hex / base32 IDs (e.g. account_id 'bybit_1', 'live') must
    NOT be redacted — they are non-secret identifiers."""
    out = _redact_for_telegram("account=bybit_1 status=ok orderId=abc123")
    assert "bybit_1" in out
    assert "abc123" in out


def test_excerpt_handles_none():
    assert _excerpt(None) == ""


def test_excerpt_falls_back_to_str_on_unjsonable():
    class _Boom:
        def __repr__(self):
            return "<Boom>"
        def __str__(self):
            raise RuntimeError("str fail")
    out = _excerpt(_Boom())
    assert isinstance(out, str)
    assert "Boom" in out  # falls back to repr


def test_excerpt_redacts_credentials_in_dicts():
    out = _excerpt({"api_key": "abcdef1234567890abcdef1234567890",
                    "retCode": 0})
    assert "abcdef1234567890abcdef1234567890" not in out
    assert "retCode" in out


# ---------------------------------------------------------------------------
# report_api_failure
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_outcomes():
    """Capture every outcomes.report() call without sending Telegram."""
    rec = []

    def fake(action, status, *, level, reason=None, **ctx):
        rec.append({"action": action, "status": status,
                    "level": getattr(level, "value", level),
                    "reason": reason, "ctx": ctx})
        return {}

    with patch("src.runtime.outcomes.report", side_effect=fake):
        yield rec


def test_report_api_failure_routes_through_outcomes(captured_outcomes):
    report_api_failure(
        exchange="bybit", op="get_wallet_balance",
        account_id="bybit_1",
        error="Bybit error retCode=10003: API key is invalid.",
        response={"retCode": 10003, "retMsg": "API key is invalid."},
    )
    assert len(captured_outcomes) == 1
    rec = captured_outcomes[0]
    assert rec["action"] == "api_call"
    assert rec["status"] == "bybit_get_wallet_balance_failed"
    assert rec["level"] == "error"
    assert "10003" in rec["reason"]
    assert rec["ctx"]["exchange"] == "bybit"
    assert rec["ctx"]["op"] == "get_wallet_balance"
    assert rec["ctx"]["account"] == "bybit_1"
    assert rec["ctx"]["retCode"] == 10003
    assert "API key is invalid" in rec["ctx"]["retMsg"]


def test_report_api_failure_includes_exception_type(captured_outcomes):
    report_api_failure(
        exchange="bybit", op="place_order", account_id="bybit_2",
        error="ConnectionError: timed out",
        exception=ConnectionError("timed out"),
    )
    rec = captured_outcomes[0]
    assert rec["ctx"]["exception_type"] == "ConnectionError"


def test_report_api_failure_redacts_credentials_in_response(captured_outcomes):
    report_api_failure(
        exchange="bybit", op="get_wallet_balance", account_id="bybit_1",
        error="some error",
        response={
            "retCode": 1, "retMsg": "fail",
            "request_signature": "abcdef1234567890abcdef1234567890abc",
        },
    )
    rec = captured_outcomes[0]
    excerpt = rec["ctx"].get("response_excerpt", "")
    assert "abcdef1234567890abcdef1234567890abc" not in excerpt


def test_report_api_failure_redacts_credentials_in_error_string(captured_outcomes):
    """Even if the caller passes a non-redacted error message verbatim."""
    long_token = "abcdef1234567890ABCDEF1234567890ZZZZZZZZ"
    report_api_failure(
        exchange="bybit", op="x", account_id="a",
        error=f"network failed: header=Bearer {long_token}",
    )
    rec = captured_outcomes[0]
    assert long_token not in rec["reason"]


def test_report_api_failure_never_raises(captured_outcomes):
    """If outcomes.report itself raises, this function must swallow."""
    with patch("src.runtime.outcomes.report", side_effect=RuntimeError("boom")):
        # No assert — just must not propagate
        report_api_failure(exchange="x", op="y", account_id="z", error="e")


def test_report_api_failure_handles_str_response(captured_outcomes):
    report_api_failure(
        exchange="bybit", op="x", account_id="a",
        error="HTTP 500", response="<html>upstream timeout</html>",
    )
    rec = captured_outcomes[0]
    assert rec["status"] == "bybit_x_failed"
    # Non-dict response → response_excerpt absent (only structured ones
    # populate it), but still doesn't crash
    assert rec["reason"] == "HTTP 500"


# ---------------------------------------------------------------------------
# End-to-end: account_balance_with_diagnostic dispatches a ping on retCode
# ---------------------------------------------------------------------------


def test_balance_retcode_failure_dispatches_ping(captured_outcomes):
    from src.bot import data_loaders as dl

    fake_client = MagicMock()
    fake_client.get_wallet_balance.return_value = {
        "retCode": 10003, "retMsg": "API key is invalid.",
    }

    import os
    os.environ["BYBIT_API_KEY_TEST"] = "x"
    os.environ["BYBIT_API_SECRET_TEST"] = "y"
    try:
        with patch.object(dl, "bybit_client_for", return_value=fake_client):
            diag = dl.account_balance_with_diagnostic({
                "account_id": "bybit_1", "exchange": "bybit",
                "api_key_env": "BYBIT_API_KEY_TEST",
            })
    finally:
        os.environ.pop("BYBIT_API_KEY_TEST", None)
        os.environ.pop("BYBIT_API_SECRET_TEST", None)

    assert diag["status"] == "api_error"
    matches = [r for r in captured_outcomes
               if r["status"] == "bybit_get_wallet_balance_failed"]
    assert len(matches) == 1
    assert matches[0]["ctx"]["account"] == "bybit_1"
    assert matches[0]["ctx"]["retCode"] == 10003


def test_balance_exception_dispatches_ping(captured_outcomes):
    from src.bot import data_loaders as dl

    fake_client = MagicMock()
    fake_client.get_wallet_balance.side_effect = ConnectionError("timed out")

    import os
    os.environ["BYBIT_API_KEY_TEST"] = "x"
    os.environ["BYBIT_API_SECRET_TEST"] = "y"
    try:
        with patch.object(dl, "bybit_client_for", return_value=fake_client):
            diag = dl.account_balance_with_diagnostic({
                "account_id": "bybit_1", "exchange": "bybit",
                "api_key_env": "BYBIT_API_KEY_TEST",
            })
    finally:
        os.environ.pop("BYBIT_API_KEY_TEST", None)
        os.environ.pop("BYBIT_API_SECRET_TEST", None)

    assert diag["status"] == "api_error"
    matches = [r for r in captured_outcomes
               if r["status"] == "bybit_get_wallet_balance_failed"]
    assert len(matches) == 1
    assert matches[0]["ctx"]["exception_type"] == "ConnectionError"


def test_balance_missing_creds_does_not_dispatch_ping(captured_outcomes, monkeypatch):
    """credentials_check fires *before* the API call — that path should
    only show up in /accounts_status, not as a Telegram alert (the
    operator already sees it on the next hourly report)."""
    from src.bot import data_loaders as dl

    monkeypatch.delenv("BYBIT_API_KEY_TEST", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET_TEST", raising=False)

    diag = dl.account_balance_with_diagnostic({
        "account_id": "bybit_1", "exchange": "bybit",
        "api_key_env": "BYBIT_API_KEY_TEST",
    })
    assert diag["status"] == "missing_creds"
    # No api_call ping for the cred-missing path
    matches = [r for r in captured_outcomes if r["action"] == "api_call"]
    assert matches == []
