"""
Tests for the news veto hook wired into run_pipeline.

Verifies that get_news_score is called for every actionable signal,
that a veto short-circuits the order block, and that a non-veto passes
through to the order decision normally.

(E1-F1, full-system audit 2026-07-09: the legacy single-client
``safe_place_order`` placement was removed from the pipeline — an
SL/TP-less actionable signal now refuses instead of placing a naked
order — so these tests no longer patch ``pipeline.safe_place_order``,
which no longer exists as a module attribute.)

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
# Test 1: veto → order block is NOT reached; status=news_veto
# ---------------------------------------------------------------------------

def test_news_veto_short_circuits_order():
    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()) as mock_news,
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal())

    order_result = result["order_result"]
    assert order_result["status"] == "news_veto"
    assert "bearish" in order_result["reason"]
    mock_news.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: non-veto → the signal proceeds to the order block
# ---------------------------------------------------------------------------

def test_news_non_veto_proceeds_to_order_block():
    """A neutral (non-veto) news result must NOT short-circuit — the signal
    reaches the order block. ``_actionable_signal()`` carries no SL/TP, so
    the order block refuses it (E1-F1: ``status:refused`` reason
    ``actionable_signal_missing_sltp``) rather than short-circuiting at the
    veto (which would be ``news_veto``). The distinct status proves the
    non-veto path was taken."""
    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_neutral_result()),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal())

    order_result = result["order_result"]
    assert order_result["status"] != "news_veto"
    assert order_result["status"] == "refused"
    assert order_result["reason"] == "actionable_signal_missing_sltp"


# ---------------------------------------------------------------------------
# Test 3: no actionable signal → get_news_score is NOT called
# ---------------------------------------------------------------------------

def test_no_signal_skips_news_check():
    with (
        patch("src.runtime.pipeline.get_news_score") as mock_news,
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator"),
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
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator"),
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
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator"),
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
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator"),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: expected_signal)

    assert result["order_result"]["signal"] == expected_signal


# ---------------------------------------------------------------------------
# Test 7: veto → send_to_operator called exactly once with veto message
# ---------------------------------------------------------------------------

def test_news_veto_sends_operator_notification():
    mock_client = MagicMock()

    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator") as mock_notify,
    ):
        run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal(),
                     telegram_client=mock_client)

    # send_to_operator called at least once for the veto-specific message
    veto_calls = [
        call for call in mock_notify.call_args_list
        if "News veto" in str(call) and "bearish" in str(call)
    ]
    assert len(veto_calls) == 1, (
        f"Expected exactly 1 veto-specific notify call, got {len(veto_calls)}. "
        f"All calls: {mock_notify.call_args_list}"
    )


# ---------------------------------------------------------------------------
# Test 8: send_to_operator raising does not alter the pipeline return status
# ---------------------------------------------------------------------------

def test_veto_notify_failure_does_not_change_status():
    """A RuntimeError from the veto-specific send_to_operator call must be
    caught by the pipeline's try/except and must not change the return status."""
    mock_client = MagicMock()

    # Raise only on the first call (veto-specific); subsequent calls (generic
    # pipeline-end notify) succeed normally.
    with (
        patch("src.runtime.pipeline.get_news_score", return_value=_veto_result()),
        patch("src.runtime.pipeline.inject_runtime_counters", side_effect=lambda s, _: dict(s)),
        patch("src.runtime.pipeline.write_signal"),
        patch("src.runtime.pipeline.log_signal"),
        patch("src.runtime.pipeline.send_to_operator",
              side_effect=[RuntimeError("Telegram down"), None]),
    ):
        result = run_pipeline(_settings(), signal_builder=lambda s: _actionable_signal(),
                              telegram_client=mock_client)

    assert result["order_result"]["status"] == "news_veto"
