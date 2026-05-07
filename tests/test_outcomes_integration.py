"""Integration tests for outcomes.report() wired into pipeline + main loop."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("pandas", "matplotlib", "matplotlib.pyplot", "numpy", "scipy", "sklearn", "dotenv"):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.outcomes import _Config, _reset_for_tests
from src.runtime.pipeline import run_pipeline


class _DummyClient:
    def place_order(self, **order):
        return {"ok": True}


class _BoomClient:
    def place_order(self, **order):
        raise RuntimeError("bybit 503")


def _settings(**overrides):
    base = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"}
    base.update(overrides)
    return base


def _signal_stub(signal):
    return lambda _settings: signal


_ACTIONABLE = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}


@pytest.fixture
def reporter(tmp_path: Path):
    cfg = _Config(
        rate_limit_window_s=300.0,
        hourly_cap=30,
        outcomes_log=tmp_path / "outcomes.jsonl",
        pending_queue=tmp_path / "pending.jsonl",
    )
    _reset_for_tests(cfg)
    yield cfg
    _reset_for_tests()


@pytest.fixture(autouse=True)
def _silence_existing_telegram():
    """The pipeline already sends a per-tick Telegram message via send_via_alert_manager.
    Patch it for every test in this file so we can assert on outcomes-driven sends only.
    """
    with patch("src.runtime.pipeline.send_via_alert_manager"), \
            patch("src.runtime.pipeline.notify_operator"):
        yield


def test_submitted_logs_info_no_telegram(reporter, tmp_path):
    """Happy path: order submitted → INFO outcome, no Telegram from outcomes."""
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
            patch("src.runtime.outcomes._Reporter._send_telegram_or_queue") as send:
        run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE),
        )
    # INFO is not telegram'd
    send.assert_not_called()
    # And it isn't persisted to the WARN+ log either
    assert not reporter.outcomes_log.exists()


def test_failed_exchange_pages_operator(reporter):
    """Exchange exception → ERROR outcome → telegram."""
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
            patch("src.runtime.outcomes._Reporter._send_telegram_or_queue") as send:
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_BoomClient(),
            signal_builder=_signal_stub(_ACTIONABLE),
        )
    assert result["order_result"]["status"] == "failed_exchange"
    send.assert_called_once()
    msg = send.call_args[0][0]
    assert "[ERROR]" in msg
    assert "pipeline_order" in msg
    assert "failed_exchange" in msg
    # And persisted
    lines = reporter.outcomes_log.read_text().splitlines()
    assert any("failed_exchange" in line for line in lines)


def test_failed_validation_logs_warn_no_telegram(reporter):
    """side='none' → no_signal short-circuit (skipped) before safe_place_order."""
    bad = {"symbol": "BTCUSDT", "side": "none", "price": 50_000.0}
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
            patch("src.runtime.outcomes._Reporter._send_telegram_or_queue") as send:
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(bad),
        )
    # side=none produces "skipped" with reason "no_signal" in run_pipeline's
    # pre-check, before safe_place_order. That's INFO, not WARN.
    # (S-026 G1: the legacy qty=0 short-circuit is gone — strategies no
    # longer emit qty, sizing is the per-account RiskManager's job.)
    assert result["order_result"]["status"] == "skipped"
    send.assert_not_called()


def test_halted_is_info_not_alert(reporter):
    """Halt flag is operator action, not a failure."""
    with patch("src.runtime.pipeline.os.path.exists", return_value=True), \
            patch("src.runtime.outcomes._Reporter._send_telegram_or_queue") as send:
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE),
        )
    assert result["order_result"]["status"] == "halted"
    send.assert_not_called()
    assert not reporter.outcomes_log.exists()


def test_strategy_exception_pages_with_strategy_name(reporter):
    """Multiplexer: strategy raises → ERROR outcome with strategy in fingerprint."""
    from src.runtime import pipeline as pl

    def _exploding(_settings):
        raise ValueError("strategy borked")

    with patch.dict(pl._STRATEGY_BUILDERS, {"_test_explode": _exploding}, clear=False), \
            patch.object(pl, "STRATEGIES", ["_test_explode"]), \
            patch("src.runtime.outcomes._Reporter._send_telegram_or_queue") as send:
        signal = pl.multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})
    assert signal["side"] == "none"
    send.assert_called_once()
    msg = send.call_args[0][0]
    assert "strategy_builder" in msg
    assert "_test_explode" in msg
    assert "ValueError" in msg
