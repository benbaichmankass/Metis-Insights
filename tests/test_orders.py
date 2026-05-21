"""Unit tests for risk guards (M3a) and kill-switch (M3b)."""
import os
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
    the offending strategy without a journalctl dive.

    Sprint S-telegram-format: the message is now sent via
    ``send_telegram_direct`` in HTML mode with collapsable sections,
    but the canonical ``Pipeline result: status=... | strategy=...``
    line is preserved as the bold header so journalctl greps and the
    operator's eyeball-scan still work.
    """
    captured = []
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
         patch("src.runtime.notify.send_telegram_direct",
               side_effect=lambda msg, **kw: captured.append(msg)):
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
         patch("src.runtime.notify.send_telegram_direct",
               side_effect=lambda msg, **kw: captured.append(msg)):
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


def test_pipeline_result_message_renders_collapsable_sections():
    """Sprint S-telegram-format: the per-tick Telegram message must be
    HTML with one ``<blockquote expandable>`` per detail section and a
    bold header carrying the legacy ``Pipeline result: status=...`` text.
    Sections must include the strategy and a stance on the order
    package (generated vs not)."""
    captured = []
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
         patch("src.runtime.notify.send_telegram_direct",
               side_effect=lambda msg, **kw: captured.append(msg)):
        # signal carries entry/sl/tp so the order-package section
        # renders in "generated" mode.
        run_pipeline(
            settings=_settings(MULTI_ACCOUNT_DISPATCH="false"),
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_signal_with_strategy("vwap", has_sltp=True)),
        )
    assert captured, "no Telegram message captured"
    body = captured[-1]
    assert "<b>Pipeline result: status=" in body
    assert "<blockquote expandable>" in body
    assert "Strategy &mdash;" in body or "Strategy — vwap" in body \
        or "<b>Strategy — vwap</b>" in body
    assert "Order package &mdash; generated" in body or \
        "<b>Order package — generated</b>" in body


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
    from src.runtime.pipeline import _pipeline_result_sections

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
    from src.runtime.pipeline import _pipeline_result_sections

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


def test_pipeline_result_failed_validation_includes_remediation_section():
    """When an order fails validation (e.g. qty exceeds MAX_QTY cap), the
    Telegram envelope must surface a 'Why & next step' section — the
    operator should not need to grep journalctl to know which knob is
    wrong.

    Note: ALLOW_LIVE_TRADING is no longer a gate in safe_place_order per
    operator directive 2026-05-03 (per-account mode is the only toggle).
    Trigger failed_validation via the MAX_QTY cap instead.
    """
    captured = []
    with patch("src.runtime.pipeline.os.path.exists", return_value=False), \
         patch("src.runtime.notify.send_telegram_direct",
               side_effect=lambda msg, **kw: captured.append(msg)):
        run_pipeline(
            settings={"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true",
                      "MULTI_ACCOUNT_DISPATCH": "false", "MAX_QTY": "0.0001"},
            exchange_client=_DummyClient(),
            signal_builder=_signal_stub(_signal_with_strategy("vwap")),
        )
    assert captured
    body = captured[-1]
    assert "failed_validation" in body
    assert "Why &amp; next step" in body or "Why & next step" in body
    assert "MAX_QTY" in body


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
