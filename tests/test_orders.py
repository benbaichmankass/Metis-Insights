"""Unit tests for risk guards (M3a) and kill-switch (M3b)."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub optional heavy deps so pipeline can be imported without a full install.
for _mod in ("pandas", "matplotlib", "matplotlib.pyplot", "numpy", "scipy", "sklearn", "dotenv"):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.orders import safe_place_order  # noqa: E402
from src.runtime.pipeline import HALT_FLAG_PATH, run_pipeline  # noqa: E402


class _DummyClient:
    def place_order(self, **order):
        return {"ok": True}


def _settings(**overrides):
    base = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"}
    base.update(overrides)
    return base


def _buy_order(price=50_000.0, qty=1.0):
    return {"symbol": "BTCUSDT", "side": "buy", "qty": qty, "price": price}


# ---------------------------------------------------------------------------
# MAX_POSITION_USD guard
# ---------------------------------------------------------------------------


def test_safe_place_order_raises_when_max_position_usd_exceeded():
    # notional = 1.0 qty * 50_000 price = 50_000 USD  >  10_000 limit
    settings = _settings(MAX_POSITION_USD="10000")
    with pytest.raises(ValueError, match="MAX_POSITION_USD"):
        safe_place_order(_buy_order(price=50_000.0, qty=1.0), settings, _DummyClient())


def test_safe_place_order_passes_when_notional_within_max_position_usd():
    # notional = 1.0 * 50_000 = 50_000 < 100_000
    settings = _settings(MAX_POSITION_USD="100000")
    result = safe_place_order(_buy_order(price=50_000.0, qty=1.0), settings, _DummyClient())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# MAX_DAILY_LOSS_USD guard
# ---------------------------------------------------------------------------


def test_safe_place_order_raises_when_max_daily_loss_usd_at_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="200", CURRENT_DAILY_LOSS_USD="200")
    with pytest.raises(ValueError, match="MAX_DAILY_LOSS_USD"):
        safe_place_order(_buy_order(), settings, _DummyClient())


def test_safe_place_order_raises_when_daily_loss_exceeds_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="200", CURRENT_DAILY_LOSS_USD="350")
    with pytest.raises(ValueError, match="MAX_DAILY_LOSS_USD"):
        safe_place_order(_buy_order(), settings, _DummyClient())


def test_safe_place_order_passes_when_daily_loss_below_limit():
    settings = _settings(MAX_DAILY_LOSS_USD="500", CURRENT_DAILY_LOSS_USD="100")
    result = safe_place_order(_buy_order(), settings, _DummyClient())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# MAX_OPEN_POSITIONS guard
# ---------------------------------------------------------------------------


def test_safe_place_order_raises_when_max_open_positions_at_limit():
    settings = _settings(MAX_OPEN_POSITIONS="3", CURRENT_OPEN_POSITIONS="3")
    with pytest.raises(ValueError, match="MAX_OPEN_POSITIONS"):
        safe_place_order(_buy_order(), settings, _DummyClient())


def test_safe_place_order_raises_when_max_open_positions_exceeded():
    settings = _settings(MAX_OPEN_POSITIONS="3", CURRENT_OPEN_POSITIONS="5")
    with pytest.raises(ValueError, match="MAX_OPEN_POSITIONS"):
        safe_place_order(_buy_order(), settings, _DummyClient())


def test_safe_place_order_passes_when_open_positions_below_limit():
    settings = _settings(MAX_OPEN_POSITIONS="3", CURRENT_OPEN_POSITIONS="2")
    result = safe_place_order(_buy_order(), settings, _DummyClient())
    assert result["status"] == "submitted"


# ---------------------------------------------------------------------------
# Kill-switch / halt flag  (checked in run_pipeline before order submission)
# ---------------------------------------------------------------------------


def _signal_stub(signal: dict):
    """Return a pipeline signal_builder that always yields the given signal."""
    return lambda _settings: signal


_ACTIONABLE_SIGNAL = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}


def test_run_pipeline_skips_order_when_halt_flag_exists():
    with patch("src.runtime.pipeline.os.path.exists", return_value=True):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE_SIGNAL),
        )
    order_result = result["order_result"]
    assert order_result["status"] == "halted"
    assert order_result.get("reason") == "halt_flag_active"


def test_run_pipeline_places_order_when_halt_flag_absent():
    with patch("src.runtime.pipeline.os.path.exists", return_value=False):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_ACTIONABLE_SIGNAL),
        )
    assert result["order_result"]["status"] == "submitted"


# ---------------------------------------------------------------------------
# G5 — Pipeline result Telegram message attribution
# ---------------------------------------------------------------------------


def _signal_with_strategy(name: str, has_sltp: bool = True):
    """Actionable signal with meta.strategy_name populated. ``has_sltp``
    toggles the entry/sl/tp top-level fields the multi-account dispatch
    fast-path requires."""
    sig = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "qty": 1.0,
        "price": 50_000.0,
        "meta": {"strategy_name": name},
    }
    if has_sltp:
        sig.update(
            entry_price=50_000.0,
            stop_loss=49_500.0,
            take_profit=51_000.0,
        )
    return sig


def test_pipeline_result_message_includes_strategy_name():
    """G5 — the operator's Telegram 'Pipeline result' line must surface
    the firing strategy so per-tick failed_validation messages identify
    the offending strategy without a journalctl dive."""
    captured = []
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
         patch("src.runtime.pipeline.send_via_alert_manager",
               side_effect=lambda msg: captured.append(msg)):
        run_pipeline(
            settings=_settings(MULTI_ACCOUNT_DISPATCH="false"),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_signal_with_strategy("vwap")),
        )
    msgs = [m for m in captured if "Pipeline result" in m]
    assert msgs, f"no Pipeline result message captured. got: {captured}"
    assert "strategy=vwap" in msgs[-1], (
        f"Pipeline result message missing strategy attribution: {msgs[-1]!r}"
    )


def test_pipeline_result_message_strategy_falls_back_to_multiplexed_when_meta_missing():
    """S-026 G4 (BUG-033): when a builder forgets meta.strategy_name AND
    no STRATEGY env / settings fallback is available, the message falls
    back to ``strategy=multiplexed`` (the production builder name)
    rather than ``strategy=unknown``.

    The operator's hourly summary counts ``strategy=unknown`` as a real
    bucket; a missing label is uninformative noise. ``multiplexed`` is
    the safe default because it matches the actual builder name when
    STRATEGY is unset.
    """
    captured = []
    sig_no_meta = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
         patch.dict(os.environ, {}, clear=False), \
         patch("src.runtime.pipeline.send_via_alert_manager",
               side_effect=lambda msg: captured.append(msg)):
        # Drop STRATEGY so the fallback chain hits the final default.
        os.environ.pop("STRATEGY", None)
        # Settings without STRATEGY field.
        settings = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true",
                    "MULTI_ACCOUNT_DISPATCH": "false"}
        run_pipeline(
            settings=settings,
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(sig_no_meta),
        )
    msgs = [m for m in captured if "Pipeline result" in m]
    assert msgs, f"no Pipeline result message captured. got: {captured}"
    assert "strategy=multiplexed" in msgs[-1], (
        f"BUG-033: 'unknown' must not leak; expected 'multiplexed'. got: {msgs[-1]!r}"
    )
    assert "strategy=unknown" not in msgs[-1]


# ---------------------------------------------------------------------------
# G5 — _signal_carries_full_sltp predicate (the smoking-gun gate)
# ---------------------------------------------------------------------------


def test_signal_carries_full_sltp_true_when_top_level_fields_present():
    from src.runtime.pipeline import _signal_carries_full_sltp
    sig = _signal_with_strategy("turtle_soup", has_sltp=True)
    assert _signal_carries_full_sltp(sig)


def test_signal_carries_full_sltp_false_for_vwap_shape_no_sltp():
    """Reproduces the operator's bug: VWAP's build_vwap_signal returns a
    signal with meta.strategy_name='vwap' but no entry/sl/tp at top
    level. The multi-account dispatch fast-path correctly skips it."""
    from src.runtime.pipeline import _signal_carries_full_sltp
    vwap_shape = {
        "symbol": "BTCUSDT", "side": "buy", "qty": 1.0,
        "meta": {
            "strategy_name": "vwap",
            "vwap": 50_000.0, "current_price": 49_500.0,
            "std_dev": 100.0, "deviation_std": -2.5,
        },
    }
    assert not _signal_carries_full_sltp(vwap_shape)


def test_signal_carries_full_sltp_accepts_meta_aliases():
    """meta.sl / meta.tp are accepted as aliases for the top-level
    stop_loss / take_profit fields (some builders nest them)."""
    from src.runtime.pipeline import _signal_carries_full_sltp
    sig = {
        "symbol": "BTCUSDT", "side": "buy", "qty": 1.0,
        "meta": {"price": 50_000.0, "sl": 49_500.0, "tp": 51_000.0,
                 "strategy_name": "synthetic"},
    }
    assert _signal_carries_full_sltp(sig)
