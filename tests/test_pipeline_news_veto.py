"""
Tests for the news veto hook wired into run_pipeline.

Verifies that get_news_score is called for every actionable signal,
that a veto short-circuits safe_place_order, and that a non-veto
passes through to safe_place_order normally.

All tests are network-free; heavy deps are stubbed out via sys.modules
before any src import (same pattern as test_kill_switch.py).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub unavailable heavy deps before importing anything from src.
# ---------------------------------------------------------------------------
for _mod in (
    "pandas",
    "numpy",
    "dotenv",
    "requests",
    "telegram",
    "telegram.ext",
    "src.runtime.signal_notifications",
    "src.runtime.notify",
    "src.utils.signal_audit_logger",
    "src.runtime.signal_writer",
):
    sys.modules.setdefault(_mod, MagicMock())

# Provide a realistic enough dotenv stub so pipeline.py's load_dotenv call is a no-op.
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

from src.news.news_score import NewsScoreResult  # noqa: E402
from src.runtime.pipeline import run_pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _actionable_signal(symbol="BTCUSDT"):
    return {"symbol": symbol, "side": "buy", "qty": 1.0}


def _settings():
    return {"SYMBOL": "BTCUSDT", "DRY_RUN": "true"}


def _neutral_result():
    return NewsScoreResult(adjustment=0.0, veto=False, decision="neutral", reason="no news")


def _veto_result():
    return NewsScoreResult(
        adjustment=-1.0, veto=True, decision="veto", reason="high-impact bearish news"
    )


# ---------------------------------------------------------------------------
# Test 1: veto → safe_place_order is NOT called; status=news_veto
# ---------------------------------------------------------------------------

def test_news_veto_short_circuits_order():
    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()) as mock_news,
        patch("src.runtime.pipeline.safe_place_order") as mock_order,
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal())

    order_result = result["order_result"]
    assert order_result["status"] == "news_veto"
    assert "bearish" in order_result["reason"]
    mock_order.assert_not_called()
    mock_news.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: non-veto → safe_place_order IS called
# ---------------------------------------------------------------------------

def test_news_non_veto_calls_safe_place_order():
    mock_order_result = {"status": "dry_run", "order": None}
    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_neutral_result()),
        patch("src.runtime.pipeline.safe_place_order", return_value=mock_order_result) as mock_order,
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal())

    mock_order.assert_called_once()
    assert result["order_result"]["status"] == "dry_run"


# ---------------------------------------------------------------------------
# Test 3: no actionable signal → get_news_score is NOT called
# ---------------------------------------------------------------------------

def test_no_signal_skips_news_check():
    with (
        patch("src.runtime.pipeline.get_news_score") as mock_news,
        patch("src.runtime.pipeline.safe_place_order"),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        run_pipeline(_settings(), signal_builder=lambda s: {"symbol": "BTCUSDT", "side": "none", "qty": 0})

    mock_news.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: symbol tag derivation — BTCUSDT → ["BTC", "BTCUSDT"]
# ---------------------------------------------------------------------------

def test_symbol_tags_derived_from_signal():
    captured = {}

    def _capture(settings, symbol_tags=None):
        captured["tags"] = symbol_tags
        return _neutral_result()

    with (
        patch("src.runtime.pipeline.get_news_score", side_effect=_capture),
        patch("src.runtime.pipeline.safe_place_order", return_value={"status": "dry_run"}),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal(symbol="BTCUSDT"))

    assert "BTC" in captured["tags"]
    assert "BTCUSDT" in captured["tags"]


# ---------------------------------------------------------------------------
# Test 5: slash-format symbol "BTC/USDT:USDT" → base "BTC" in tags
# ---------------------------------------------------------------------------

def test_symbol_tags_slash_format():
    captured = {}

    def _capture(settings, symbol_tags=None):
        captured["tags"] = symbol_tags
        return _neutral_result()

    with (
        patch("src.runtime.pipeline.get_news_score", side_effect=_capture),
        patch("src.runtime.pipeline.safe_place_order", return_value={"status": "dry_run"}),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal(symbol="BTC/USDT:USDT"))

    assert "BTC" in captured["tags"]


# ---------------------------------------------------------------------------
# Test 6: veto result carries original signal in return dict
# ---------------------------------------------------------------------------

def test_veto_result_contains_signal():
    expected_signal = _actionable_signal()

    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()),
        patch("src.runtime.pipeline.safe_place_order"),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: expected_signal)

    assert result["order_result"]["signal"] == expected_signal


# ---------------------------------------------------------------------------
# Test 7: veto → notify_operator called exactly once with veto message
# ---------------------------------------------------------------------------

def test_news_veto_sends_operator_notification():
    mock_client = MagicMock()

    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()),
        patch("src.runtime.pipeline.safe_place_order"),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.notify_operator") as mock_notify,
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal(),
                     telegram_client=mock_client)

    # notify_operator called at least once for the veto-specific message
    veto_calls = [
        call for call in mock_notify.call_args_list
        if "News veto" in str(call) and "bearish" in str(call)
    ]
    assert len(veto_calls) == 1, (
        f"Expected exactly 1 veto-specific notify call, got {len(veto_calls)}. "
        f"All calls: {mock_notify.call_args_list}"
    )


# ---------------------------------------------------------------------------
# Test 8: notify_operator raising does not alter the pipeline return status
# ---------------------------------------------------------------------------

def test_veto_notify_failure_does_not_change_status():
    """A RuntimeError from the veto-specific notify_operator call must be
    caught by the pipeline's try/except and must not change the return status."""
    mock_client = MagicMock()

    # Raise only on the first call (veto-specific); subsequent calls (generic
    # pipeline-end notify) succeed normally.
    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()),
        patch("src.runtime.pipeline.safe_place_order"),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.notify_operator",
              side_effect=[RuntimeError("Telegram down"), None]),
        patch("src.runtime.pipeline.send_via_alert_manager"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal(),
                              telegram_client=mock_client)

    assert result["order_result"]["status"] == "news_veto"
