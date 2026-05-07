"""S-031 PR3 regression tests
(architecture-audit-2026-05-02 P1-6).

Per CLAUDE.md § Architecture rules § 5 the Telegram bot is a thin
shell over the UI unit. Pre-PR ``src/bot/telegram_query_bot.py::cmd_price``
made a raw HTTP call to Bybit's public ticker endpoint. Post-PR the
fetch lives in ``src.units.ui.processor.get_price`` and the bot is a
one-liner.

Tests pin:
  1. ``get_price`` returns a float on a happy-path response.
  2. ``get_price`` returns ``None`` on every error shape (network
     exception, non-200, missing fields).
  3. The symbol parameter is forwarded to the Bybit endpoint.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestGetPriceHappyPath:
    def test_returns_float_for_btcusdt(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "result": {"list": [{"lastPrice": "67890.5"}]}
        }
        with patch("requests.get", return_value=fake_resp) as mock_get:
            price = processor.get_price("BTCUSDT")
        assert isinstance(price, float)
        assert price == 67890.5
        # Endpoint + params forwarded correctly.
        args, kwargs = mock_get.call_args
        assert "api.bybit.com" in args[0]
        assert kwargs["params"]["symbol"] == "BTCUSDT"
        assert kwargs["params"]["category"] == "linear"

    def test_default_symbol_is_btcusdt(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "result": {"list": [{"lastPrice": "100.0"}]}
        }
        with patch("requests.get", return_value=fake_resp) as mock_get:
            processor.get_price()
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["symbol"] == "BTCUSDT"

    def test_custom_symbol_forwarded(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "result": {"list": [{"lastPrice": "3500.25"}]}
        }
        with patch("requests.get", return_value=fake_resp) as mock_get:
            price = processor.get_price("ETHUSDT")
        assert price == 3500.25
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["symbol"] == "ETHUSDT"


# ---------------------------------------------------------------------------
# Error paths — every shape returns ``None`` (no exceptions escape)
# ---------------------------------------------------------------------------


class TestGetPriceErrorPaths:
    def test_network_exception_returns_none(self):
        from src.units.ui import processor

        with patch("requests.get", side_effect=ConnectionError("offline")):
            price = processor.get_price("BTCUSDT")
        assert price is None

    def test_timeout_returns_none(self):
        from src.units.ui import processor

        with patch("requests.get", side_effect=TimeoutError("slow")):
            price = processor.get_price("BTCUSDT")
        assert price is None

    def test_missing_result_field_returns_none(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {"retCode": 10001, "retMsg": "bad"}
        with patch("requests.get", return_value=fake_resp):
            price = processor.get_price("BTCUSDT")
        assert price is None

    def test_empty_list_returns_none(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {"result": {"list": []}}
        with patch("requests.get", return_value=fake_resp):
            price = processor.get_price("BTCUSDT")
        assert price is None

    def test_missing_lastprice_returns_none(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "result": {"list": [{"symbol": "BTCUSDT"}]}
        }
        with patch("requests.get", return_value=fake_resp):
            price = processor.get_price("BTCUSDT")
        assert price is None

    def test_unparseable_lastprice_returns_none(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "result": {"list": [{"lastPrice": "not-a-number"}]}
        }
        with patch("requests.get", return_value=fake_resp):
            price = processor.get_price("BTCUSDT")
        assert price is None

    def test_bad_json_returns_none(self):
        from src.units.ui import processor

        fake_resp = MagicMock()
        fake_resp.json.side_effect = ValueError("invalid json")
        with patch("requests.get", return_value=fake_resp):
            price = processor.get_price("BTCUSDT")
        assert price is None
