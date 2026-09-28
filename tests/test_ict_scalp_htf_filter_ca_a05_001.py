"""CA-A05-001 / JC-CA-02 (docs/audits/code-audit-2026-09-27.md §6, operator
decision 2026-09-28: "Remove flag + research").

Proves the config edit that removes ``htf_trend_filter_enabled: true`` from
the 7 ict_scalp variant legs (config/strategies.yaml) is ZERO BEHAVIOUR
CHANGE, on two levels:

1. ``_ict_scalp_variant_builder`` never supplies ``cfg["htf_close"]`` /
   ``cfg["htf_ema"]`` to ``order_package`` — the root cause the finding
   names, and the reason the flag was inert regardless of its value.
2. ``order_package``'s own output is byte-identical whether
   ``htf_trend_filter_enabled`` is explicitly ``true`` in cfg or simply
   absent (the state after this PR) — because ``_resolve_params`` defaults
   it to ``True`` (src/units/strategies/ict_scalp.py `_DEFAULTS`) and the
   filter block only ever consults ``htf_close``/``htf_ema``, which are
   never present either way in production for these 7 legs.

Fully offline — synthetic OHLCV + monkeypatch only, same pattern as
tests/test_ict_scalp_variants.py.
"""
from __future__ import annotations

import pandas as pd
import pytest

import src.runtime.strategy_signal_builders as ssb
from src.units.strategies.ict_scalp import order_package
from tests.test_ict_scalp_5m import _bullish_scalp_frame as _proven_bullish_frame
from tests.test_ict_scalp_variants import _base_cfg, _bullish_scalp_frame, _wire

# The exact 7 legs named in CA-A05-001, all routed through
# _ict_scalp_variant_builder per src/runtime/strategy_signal_builders.py:5589-5599.
SEVEN_DEAD_FILTER_LEGS = (
    "ict_scalp_sol_5m",
    "ict_scalp_xrp_5m",
    "ict_scalp_avax_5m",
    "ict_scalp_xrp_15m",
    "ict_scalp_eth_15m",
    "ict_scalp_sol_15m",
    "ict_scalp_mgc_15m",
)


@pytest.mark.parametrize("name", SEVEN_DEAD_FILTER_LEGS)
def test_variant_builder_never_supplies_htf_close_or_ema(monkeypatch, name):
    """Root cause: the cfg _ict_scalp_variant_builder hands to order_package
    never carries htf_close/htf_ema, for every one of the 7 legs — so
    htf_trend_filter_enabled's value can never change what order_package does.
    """
    captured = {}

    def _capturing_order_package(cfg, candles_df=None):
        captured.update(cfg)
        raise ValueError("no actionable signal (captured cfg for assertion)")

    import src.units.strategies.ict_scalp as ict_scalp_unit
    monkeypatch.setattr(ict_scalp_unit, "order_package", _capturing_order_package)

    cfg = _base_cfg("BTCUSDT", htf_trend_filter_enabled=True)
    frame = _bullish_scalp_frame(base=100.0)
    _wire(monkeypatch, name, "BTCUSDT", frame, cfg)

    assert "htf_close" not in captured
    assert "htf_ema" not in captured


def _order_package_output(*, htf_flag_present: bool) -> dict:
    """Run the REAL order_package (no stub) with a firing bullish setup —
    tests/test_ict_scalp_5m.py's own fixture, proven (by that file's tests)
    to fire a real long signal against order_package's default params — with
    htf_trend_filter_enabled either explicit (today) or absent (after this
    PR's config edit) — and never any htf_close/htf_ema, matching what
    _ict_scalp_variant_builder actually supplies in production.
    """
    cfg = {"symbol": "SOLUSDT"}
    if htf_flag_present:
        cfg["htf_trend_filter_enabled"] = True
    # else: key absent entirely — _resolve_params defaults it to True via
    # _DEFAULTS, exactly like removing the YAML line does.
    frame = _proven_bullish_frame()
    return order_package(cfg, candles_df=frame)


def test_order_package_output_identical_with_flag_true_vs_flag_absent():
    """The actual assertion CLAUDE.md's operator decision requires: proof
    that dropping `htf_trend_filter_enabled: true` from config/strategies.yaml
    changes nothing, because the default (True, via _DEFAULTS) and the
    explicit value produce IDENTICAL order_package output when htf_close/
    htf_ema are absent either way — which is always true in production for
    these 7 legs.
    """
    pkg_explicit_true = _order_package_output(htf_flag_present=True)
    pkg_flag_absent = _order_package_output(htf_flag_present=False)
    assert pkg_explicit_true == pkg_flag_absent


def test_htf_filter_active_flag_is_false_in_both_cases():
    """Belt-and-suspenders on the same claim: order_package's own
    `htf_filter_active` meta field (ict_scalp.py:571-575) must read False
    in both cases, confirming the filter genuinely never gated this trade —
    not merely that the two outputs happen to match by coincidence.
    """
    for htf_flag_present in (True, False):
        pkg = _order_package_output(htf_flag_present=htf_flag_present)
        assert pkg["meta"]["htf_filter_active"] is False


@pytest.mark.parametrize("name", SEVEN_DEAD_FILTER_LEGS)
def test_config_yaml_no_longer_declares_the_dead_flag(name):
    """config/strategies.yaml itself: after this PR, none of the 7 legs may
    carry htf_trend_filter_enabled — field beats comment, so this checks the
    live file, not this test's own claim about it.
    """
    from src.units.strategies import load_strategy_config

    cfg = load_strategy_config()
    leg_cfg = cfg.get(name) or {}
    assert "htf_trend_filter_enabled" not in leg_cfg, (
        f"{name} still declares htf_trend_filter_enabled — CA-A05-001 / "
        "JC-CA-02 removed it because the filter never runs for this leg"
    )


def test_base_ict_scalp_leg_keeps_the_flag_out_of_scope():
    """Negative control: the base `ict_scalp_5m` BTCUSDT leg is NOT one of
    the 7 — it routes through ict_scalp_signal_builder, which DOES fetch HTF
    candles (strategy_signal_builders.py:617-654), so its
    htf_trend_filter_enabled is live and must be left alone.
    """
    from src.units.strategies import load_strategy_config

    cfg = load_strategy_config()
    leg_cfg = cfg.get("ict_scalp_5m") or {}
    assert leg_cfg.get("htf_trend_filter_enabled") is True
