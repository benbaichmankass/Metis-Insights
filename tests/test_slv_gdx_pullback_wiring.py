"""Regression tests for the SLV/GDX daily-pullback wiring (Tier-3, 2026-06-28).

The M17 audit (S-AUDIT-H1) found `slv_pullback_1d` + `gdx_pullback_1d` declared
`enabled: true` / `execution: live` and routed to alpaca_live (real money) in
config, but with **no signal builder registered** — so they were inert (the
MES `MULTI_SYMBOL_ENABLED` "looks-live-but-stranded" class). These tests pin the
builders into the registry so the gap can't silently reopen, mirroring
`test_m15_eth_pullback_wiring.py`.
"""
from __future__ import annotations


def test_slv_gdx_builders_registered_in_intent_multiplexer():
    from src.runtime.intent_multiplexer import _default_intent_builders

    builders = _default_intent_builders()
    assert "slv_pullback_1d" in builders, "slv_pullback_1d has no registered builder (inert)"
    assert "gdx_pullback_1d" in builders, "gdx_pullback_1d has no registered builder (inert)"
    assert callable(builders["slv_pullback_1d"])
    assert callable(builders["gdx_pullback_1d"])


def test_slv_gdx_disabled_gate_returns_side_none(monkeypatch):
    """With the strategy disabled in YAML, the builder must short-circuit to
    side=none (the canonical `enabled` gate), not raise or fetch candles.

    The builder imports ``load_strategy_config`` locally from
    ``src.units.strategies`` inside the function, so the patch must target that
    source module — patching the builder's own namespace would no-op (the proven
    ``test_m15_eth_pullback_wiring`` pattern)."""
    from src.runtime.strategy_signal_builders import slv_pullback_1d_signal_builder

    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {"slv_pullback_1d": {"enabled": False}},
    )
    out = slv_pullback_1d_signal_builder({"symbol": "SLV"})
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "disabled_in_yaml"


def test_slv_gdx_yaml_enabled_live_with_expected_symbols():
    from src.units.strategies import load_strategy_config

    cfg = load_strategy_config()
    for key, sym in (("slv_pullback_1d", "SLV"), ("gdx_pullback_1d", "GDX")):
        s = cfg[key]
        assert s["enabled"] is True, f"{key} not enabled"
        assert s["execution"] == "live", f"{key} not live"
        assert s["symbols"] == [sym], f"{key} symbol drift"
        # mirrors gld_pullback_1d params (SRQ-20260627)
        assert s["timeframe"] == "1d"
        assert (s["trend_lookback"], s["pullback_lookback"], s["pullback_frac"]) == (40, 15, 0.618)
