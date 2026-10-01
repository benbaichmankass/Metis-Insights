"""CA-A05-001 / JC-CA-02 (docs/audits/code-audit-2026-09-27.md §6, operator
decision 2026-09-28: "Remove flag + research").

UPDATE (HTF wiring PR): _ict_scalp_variant_builder now fetches HTF candles
(``_fetch_htf_bias``), so the "never supplies htf_close/htf_ema" root cause
described below is fixed and the tests at the top of this file cover the new
behaviour. The order_package-level tests further down remain valid: they
exercise order_package with NO htf data, which is the fetch-failure path.

Original docstring — proves the config edit that removes ``htf_trend_filter_enabled: true`` from
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

import pytest

from src.units.strategies.ict_scalp import order_package
from tests.test_ict_scalp_5m import _bullish_scalp_frame as _proven_bullish_frame
import src.runtime.strategy_signal_builders as ssb
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


def _htf_frame(last_close: float, n: int = 80):
    """1h frame: flat at 100 then a final close of ``last_close`` — bias is
    bullish when last_close > its EMA, bearish when below."""
    import pandas as pd
    closes = [100.0] * (n - 1) + [last_close]
    return pd.DataFrame({"close": closes})


def _capture_cfg(monkeypatch, name, vcfg, htf_frame):
    """Run the REAL _ict_scalp_variant_builder with fetch_candles returning the
    base frame for the strategy timeframe and ``htf_frame`` for the HTF one;
    return (cfg handed to order_package, list of timeframes fetched)."""
    captured, tfs = {}, []

    def _capturing_order_package(cfg, candles_df=None):
        captured.update(cfg)
        raise ValueError("no actionable signal (captured cfg for assertion)")

    import src.units.strategies.ict_scalp as ict_scalp_unit
    monkeypatch.setattr(ict_scalp_unit, "order_package", _capturing_order_package)
    base = _bullish_scalp_frame(base=100.0)
    htf_tf = vcfg.get("htf_filter_timeframe") or "1h"

    def _fetch(symbol, tf, *a, **k):
        tfs.append(tf)
        if tf == htf_tf and htf_frame is not None:
            return htf_frame
        if tf == htf_tf:
            raise RuntimeError("htf down")
        return base

    import src.runtime.market_data as md
    monkeypatch.setattr(md, "fetch_candles", _fetch, raising=False)
    _wire(monkeypatch, name, "BTCUSDT", base, vcfg)  # also runs the builder once
    monkeypatch.setattr(md, "fetch_candles", _fetch, raising=False)
    captured.clear()
    tfs.clear()
    ssb._ict_scalp_variant_builder(name, {"SYMBOL": "BTCUSDT"})
    return captured, tfs


@pytest.mark.parametrize("name", SEVEN_DEAD_FILTER_LEGS)
def test_variant_builder_fetches_htf_and_supplies_close_and_ema(monkeypatch, name):
    """CA-A05-001 fix: with the flag absent (unit default True) the builder
    fetches the HTF timeframe and hands htf_close/htf_ema to order_package."""
    cfg = _base_cfg("BTCUSDT")
    cfg.pop("htf_trend_filter_enabled", None)
    cfg["htf_filter_timeframe"] = "1h"
    cfg["htf_filter_ema_period"] = 20
    captured, tfs = _capture_cfg(monkeypatch, name, cfg, _htf_frame(110.0))
    assert "1h" in tfs
    assert captured["htf_close"] == 110.0
    assert captured["htf_ema"] < 110.0  # bullish bias


def test_variant_builder_flag_false_skips_htf_fetch(monkeypatch):
    cfg = _base_cfg("BTCUSDT", htf_trend_filter_enabled=False)
    cfg["htf_filter_timeframe"] = "1h"
    captured, tfs = _capture_cfg(monkeypatch, "ict_scalp_sol_5m", cfg, _htf_frame(110.0))
    assert "1h" not in tfs
    assert "htf_close" not in captured and "htf_ema" not in captured


def test_variant_builder_htf_fetch_failure_degrades_to_no_gate(monkeypatch):
    cfg = _base_cfg("BTCUSDT")
    cfg.pop("htf_trend_filter_enabled", None)
    cfg["htf_filter_timeframe"] = "1h"
    captured, tfs = _capture_cfg(monkeypatch, "ict_scalp_sol_5m", cfg, None)
    assert "1h" in tfs
    assert "htf_close" not in captured and "htf_ema" not in captured


def test_bearish_htf_blocks_long_through_the_variant_builder(monkeypatch):
    """End to end through the REAL order_package: the same long setup that
    fires with no HTF data is blocked when the HTF bias is bearish."""
    import pandas as pd
    import src.runtime.market_data as md

    frame = _proven_bullish_frame()
    name = "ict_scalp_sol_5m"

    def run(htf_last):
        cfg = {"symbol": "SOLUSDT", "timeframe": "5m", "enabled": True,
               "symbols": ["SOLUSDT"], "htf_filter_timeframe": "1h"}

        def _fetch(symbol, tf, *a, **k):
            if tf == "1h":
                return (pd.DataFrame({"close": [100.0] * 79 + [htf_last]})
                        if htf_last is not None else None)
            return frame

        _wire(monkeypatch, name, "SOLUSDT", frame, cfg)
        monkeypatch.setattr(md, "fetch_candles", _fetch, raising=False)
        return ssb._ict_scalp_variant_builder(name, {"SYMBOL": "SOLUSDT"})

    assert run(None)["side"] == "buy"          # no HTF data: filter off
    assert run(120.0)["side"] == "buy"         # bullish HTF agrees
    assert run(80.0)["side"] == "none"         # bearish HTF blocks the long


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
