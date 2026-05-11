"""Pin the Bybit V5 direct-call signing logic in BybitConnector.

2026-05-11 set-leverage incident: ccxt's auto-generated
``private_post_v5_position_set_leverage`` returns retCode=10003 on
the operator's UTA key despite the key having every permission scope
and successfully placing orders via the SAME ccxt client. Diagnosis:
ccxt's V5 path-aware signing has a routing bug on this specific
endpoint. Fix: sign the V5 request directly per Bybit's documented
auth spec:

  sign_string = timestamp + api_key + recv_window + body
  signature   = hex(HMAC-SHA256(secret, sign_string))

These tests pin the signing exactly so any future regression in the
auth math is caught before deploy.

The HTTP layer is mocked — we don't actually hit Bybit. The signing
itself is deterministic given (timestamp, api_key, recv_window,
body, secret); tests fix those inputs to a known vector.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from src.exchange.bybit_connector import BybitConnector


_FIXED_TS_MS = 1700000000000  # 2023-11-14T22:13:20Z — arbitrary, just stable
_KEY = "test_api_key_AAAA"
_SECRET = "test_secret_BBBB"


@pytest.fixture
def connector(monkeypatch):
    """A BybitConnector wired with a stub ccxt client (apiKey/secret only).
    No network — _v5_signed_post is exercised behind a mocked requests.post."""
    # bypass real ccxt instantiation; we only need apiKey + secret
    # exposed via the attribute path _v5_signed_post reads.
    with patch("src.exchange.bybit_connector.ccxt.bybit") as fake_ctor:
        fake_client = MagicMock()
        fake_client.apiKey = _KEY
        fake_client.secret = _SECRET
        fake_ctor.return_value = fake_client
        c = BybitConnector(api_key=_KEY, api_secret=_SECRET, testnet=False)
    return c


def _expected_signature(timestamp: str, body: str, recv_window: str = "5000") -> str:
    sign_string = timestamp + _KEY + recv_window + body
    return hmac.new(
        _SECRET.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_v5_signed_post_sends_canonical_body_and_correct_signature(
    connector, monkeypatch
):
    """Pin the auth contract: signed POST hits the right URL, body is
    compact JSON, sign-string is exactly timestamp+key+recvWindow+body."""
    captured = {}

    def fake_post(url, data, headers, timeout):  # noqa: A002
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.json.return_value = {"retCode": 0, "retMsg": "OK", "result": {}}
        resp.status_code = 200
        resp.text = '{"retCode":0}'
        return resp

    monkeypatch.setattr(
        "src.exchange.bybit_connector.requests.post", fake_post
    )
    monkeypatch.setattr(
        "src.exchange.bybit_connector.time.time", lambda: _FIXED_TS_MS / 1000
    )

    payload = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "buyLeverage": "3",
        "sellLeverage": "3",
    }
    result = connector._v5_signed_post("/v5/position/set-leverage", payload)
    assert result == {"retCode": 0, "retMsg": "OK", "result": {}}

    # URL must be the live host (testnet=False on the fixture).
    assert captured["url"] == "https://api.bybit.com/v5/position/set-leverage"

    # Body is compact JSON (no whitespace). Signing requires exact bytes
    # to match what was signed; any reformat would invalidate sig.
    expected_body = json.dumps(payload, separators=(",", ":"))
    assert captured["data"] == expected_body
    assert " " not in captured["data"]

    # Headers per Bybit V5 spec.
    h = captured["headers"]
    assert h["X-BAPI-API-KEY"] == _KEY
    assert h["X-BAPI-SIGN-TYPE"] == "2"
    assert h["X-BAPI-TIMESTAMP"] == str(_FIXED_TS_MS)
    assert h["X-BAPI-RECV-WINDOW"] == "5000"
    assert h["Content-Type"] == "application/json"

    # Signature is HMAC-SHA256(secret, timestamp+key+recvWindow+body).
    assert h["X-BAPI-SIGN"] == _expected_signature(
        str(_FIXED_TS_MS), expected_body
    )


def test_v5_signed_post_testnet_uses_testnet_host(monkeypatch):
    """testnet=True routes to api-testnet.bybit.com, not api.bybit.com."""
    with patch("src.exchange.bybit_connector.ccxt.bybit") as fake_ctor:
        fake_client = MagicMock()
        fake_client.apiKey = _KEY
        fake_client.secret = _SECRET
        fake_ctor.return_value = fake_client
        c = BybitConnector(api_key=_KEY, api_secret=_SECRET, testnet=True)

    captured = {}

    def fake_post(url, data, headers, timeout):  # noqa: A002
        captured["url"] = url
        resp = MagicMock()
        resp.json.return_value = {"retCode": 0, "retMsg": "OK"}
        return resp

    monkeypatch.setattr(
        "src.exchange.bybit_connector.requests.post", fake_post
    )
    c._v5_signed_post("/v5/position/set-leverage", {"category": "linear"})
    assert captured["url"].startswith("https://api-testnet.bybit.com")


def test_v5_signed_post_raises_when_credentials_missing(monkeypatch):
    """A connector with empty apiKey/secret must fail loudly — the V5
    signed call would otherwise produce a HMAC over an empty key
    (deterministic-but-invalid signature) and surface as a confusing
    retCode=10003 from Bybit. Pre-validate to surface the misconfig at
    the call site instead."""
    with patch("src.exchange.bybit_connector.ccxt.bybit") as fake_ctor:
        fake_client = MagicMock()
        fake_client.apiKey = ""
        fake_client.secret = ""
        fake_ctor.return_value = fake_client
        c = BybitConnector(api_key=None, api_secret=None, testnet=False)

    with pytest.raises(RuntimeError, match="apiKey"):
        c._v5_signed_post("/v5/position/set-leverage", {"category": "linear"})


def test_set_leverage_treats_retcode_0_as_success(connector, monkeypatch):
    """retCode=0 (newly set) → returns the response, doesn't raise."""
    monkeypatch.setattr(
        "src.exchange.bybit_connector.time.time", lambda: _FIXED_TS_MS / 1000
    )

    def fake_post(url, data, headers, timeout):  # noqa: A002
        resp = MagicMock()
        resp.json.return_value = {"retCode": 0, "retMsg": "OK", "result": {}}
        return resp

    monkeypatch.setattr(
        "src.exchange.bybit_connector.requests.post", fake_post
    )
    result = connector.set_leverage("BTCUSDT", 3, category="linear")
    assert result["retCode"] == 0


def test_set_leverage_treats_retcode_110043_as_success(connector, monkeypatch):
    """retCode=110043 (leverage not modified) is the idempotent case —
    common on every boot when the leverage already matches. Must NOT
    raise; the trader's pre-flight runs this on every start."""
    monkeypatch.setattr(
        "src.exchange.bybit_connector.time.time", lambda: _FIXED_TS_MS / 1000
    )

    def fake_post(url, data, headers, timeout):  # noqa: A002
        resp = MagicMock()
        resp.json.return_value = {
            "retCode": 110043,
            "retMsg": "leverage not modified",
            "result": {},
        }
        return resp

    monkeypatch.setattr(
        "src.exchange.bybit_connector.requests.post", fake_post
    )
    result = connector.set_leverage("BTCUSDT", 3, category="linear")
    assert result["retCode"] == 110043


def test_set_leverage_raises_on_real_error(connector, monkeypatch):
    """retCode=10003 or any non-success code must raise so the caller's
    pre-flight surfaces the failure rather than silently no-opping."""
    monkeypatch.setattr(
        "src.exchange.bybit_connector.time.time", lambda: _FIXED_TS_MS / 1000
    )

    def fake_post(url, data, headers, timeout):  # noqa: A002
        resp = MagicMock()
        resp.json.return_value = {
            "retCode": 10003,
            "retMsg": "API key is invalid.",
            "result": {},
        }
        return resp

    monkeypatch.setattr(
        "src.exchange.bybit_connector.requests.post", fake_post
    )
    with pytest.raises(Exception, match="10003"):
        connector.set_leverage("BTCUSDT", 3, category="linear")
