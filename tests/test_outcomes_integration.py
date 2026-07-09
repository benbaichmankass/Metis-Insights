"""Integration tests for outcomes.report() wired into pipeline + main loop."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("pandas", "matplotlib", "matplotlib.pyplot", "numpy", "scipy", "sklearn", "dotenv"):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.outcomes import _Config, _reset_for_tests  # noqa: E402
from src.runtime.pipeline import run_pipeline  # noqa: E402


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


# G5 (CP-2026-05-02-09): signals without entry_price/stop_loss/take_profit
# trigger a Level.WARN "signal_missing_sltp" report that writes to
# outcomes.jsonl — causing INFO-only tests to see unexpected WARN persists.
# Provide full SL/TP so the pipeline takes the multi-account fast-path or
# the legacy path without the extra WARN noise, keeping tests focused on
# the outcome level they actually claim to test.
_ACTIONABLE = {
    "symbol": "BTCUSDT",
    "side": "buy",
    "qty": 1.0,
    "entry_price": 50_000.0,
    "stop_loss": 49_500.0,
    "take_profit": 51_000.0,
}


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
    """The pipeline already sends a per-tick Telegram message via send_to_operator.
    Patch it for every test in this file so we can assert on outcomes-driven sends only.
    """
    with patch("src.runtime.pipeline.send_to_operator"):
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


def test_dispatch_disabled_refuses_and_never_touches_exchange(reporter):
    """E1-F1 (full-system audit 2026-07-09): with MULTI_ACCOUNT_DISPATCH
    pinned off, a full-SL/TP actionable signal reaches the legacy
    single-client branch. That branch used to invoke the injected exchange
    client (here a _BoomClient that raises) via safe_place_order — the exact
    live-money bypass being removed. It now REFUSES (``status:refused`` reason
    ``multi_account_dispatch_disabled``): the _BoomClient is never called (no
    RuntimeError escapes), and a refusal is INFO — no ERROR page, nothing
    persisted to the WARN+ outcomes log."""
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
            patch("src.runtime.outcomes._Reporter._send_telegram_or_queue") as send:
        result = run_pipeline(
            settings=_settings(MULTI_ACCOUNT_DISPATCH="false"),
            exchange_client=_BoomClient(),
            signal_builder=_signal_stub(_ACTIONABLE),
        )
    assert result["order_result"]["status"] == "refused"
    assert result["order_result"]["reason"] == "multi_account_dispatch_disabled"
    # Refusal is INFO → no operator page, nothing in the WARN+ log.
    send.assert_not_called()
    assert not reporter.outcomes_log.exists()


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
