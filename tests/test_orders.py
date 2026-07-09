"""Unit tests for risk guards (M3a) and kill-switch (M3b)."""
import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub optional heavy deps so pipeline can be imported without a full install.
for _mod in ("pandas", "matplotlib", "matplotlib.pyplot", "numpy", "scipy", "sklearn", "dotenv"):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.orders import safe_place_order  # noqa: E402
from src.runtime.pipeline import run_pipeline  # noqa: E402


class _DummyClient:
    def place_order(self, **order):
        return {"ok": True}


class _CountingClient:
    """Records place_order invocations so a test can assert the legacy
    branch never reaches the exchange (E1-F1)."""

    def __init__(self):
        self.calls = 0

    def place_order(self, **order):
        self.calls += 1
        return {"ok": True}


def _settings(**overrides):
    base = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true"}
    base.update(overrides)
    return base


def _buy_order(price=50_000.0, qty=1.0):
    return {"symbol": "BTCUSDT", "side": "buy", "qty": qty, "price": price}


# ---------------------------------------------------------------------------
# MAX_POSITION_USD guard — REMOVED 2026-06-24 (notional cap deleted; a
# leftover MAX_POSITION_USD value is now ignored). The daily-loss + open-
# positions + halt guards below remain the active order-layer rails.
# ---------------------------------------------------------------------------


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


def test_run_pipeline_refuses_sltp_less_signal_and_never_touches_exchange():
    """E1-F1 (full-system audit 2026-07-09): an actionable signal with no
    top-level SL/TP reaches the legacy single-client branch. That branch
    used to size a placeholder qty and place a naked order on the injected
    exchange client — a live-money bypass of the one sanctioned order path.
    It now REFUSES (``status:refused``) and never calls ``place_order``.
    (Halt-flag absent so we reach the order block; ``_ACTIONABLE_SIGNAL``
    carries no entry/sl/tp.)"""
    client = _CountingClient()
    with patch("src.runtime.pipeline.os.path.exists", return_value=False):
        result = run_pipeline(
            settings=_settings(),
            exchange_client=client,
            signal_builder=_signal_stub(_ACTIONABLE_SIGNAL),
        )
    order_result = result["order_result"]
    assert order_result["status"] == "refused"
    assert order_result["reason"] == "actionable_signal_missing_sltp"
    assert client.calls == 0, "legacy path must never place an order (E1-F1)"


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


def test_safe_place_order_allow_live_diagnostic_includes_source_and_value():
    """Operator directive 2026-05-03: ALLOW_LIVE_TRADING is no longer a
    gate in safe_place_order — the per-account RiskManager (mode: live |
    dry_run in accounts.yaml) is the single authoritative toggle.
    safe_place_order is a payload-validation + halt-flag + risk-cap rail.

    Contract: orders with valid payload reach the exchange regardless of
    the ALLOW_LIVE_TRADING setting in the settings dict.
    """
    settings = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "false"}
    result = safe_place_order(_buy_order(), settings, _DummyClient())
    # ALLOW_LIVE_TRADING is no longer consulted — order submits.
    assert result["status"] == "submitted"


def test_pipeline_result_sections_omits_not_generated_on_no_signal_tick():
    """CP-2026-05-03-18 P3 (cosmetic): on a no-signal tick the
    'Order package — not generated' section must not fire. Pre-fix it
    rendered every tick, adding noise to the operator's per-tick
    feed when no signal had even been considered. The body's text
    references the legacy single-client validation path, which
    only makes sense when the strategy actually fired."""
    from src.runtime.pipeline_result import _pipeline_result_sections

    no_signal = {
        "symbol": "BTCUSDT",
        "side": "none",
        "meta": {"strategy_name": "vwap", "reason": "no setup on the latest bar"},
    }
    result = {"status": "no_signal"}
    sections = _pipeline_result_sections(
        signal=no_signal, result=result, strategy="vwap",
    )
    summaries = [s.summary for s in sections]
    assert "Order package — not generated" not in summaries, (
        "P3 regression: the 'not generated' body must skip on "
        f"side='none' ticks (got summaries={summaries!r})"
    )
    # The other legitimate sections still render.
    assert any("Strategy" in s for s in summaries)


def test_pipeline_result_sections_keeps_not_generated_when_actionable_but_missing_sltp():
    """Inverse pin: when side IS actionable (buy/sell) but the signal
    didn't carry entry/sl/tp at the top level, the 'not generated'
    section is the operator's diagnostic for the legacy single-client
    fallback — it must keep firing in that case."""
    from src.runtime.pipeline_result import _pipeline_result_sections

    actionable_no_sltp = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "meta": {"strategy_name": "vwap"},
    }
    sections = _pipeline_result_sections(
        signal=actionable_no_sltp, result={"status": "failed_validation"},
        strategy="vwap",
    )
    summaries = [s.summary for s in sections]
    assert "Order package — not generated" in summaries, (
        "P3 inverse pin: actionable signal lacking sl/tp must still "
        "render the 'not generated' diagnostic so the operator sees "
        "it took the legacy path"
    )


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
