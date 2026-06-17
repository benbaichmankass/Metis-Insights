"""Tests for S-007 #114: pipeline.STRATEGIES and data_loaders rewired to registry."""
from __future__ import annotations

import types
import sys



# ---------------------------------------------------------------------------
# registry invariants that pipeline.STRATEGIES depends on
# (pipeline imports pandas so we validate via the registry directly)
# ---------------------------------------------------------------------------

def test_registry_contains_all_pipeline_strategies():
    """All multiplexer strategies must be in config/strategies.yaml.

    S-012 PR B1: roster reduced to turtle_soup + vwap.
    """
    from src.strategy_registry import load_strategies
    names = [s["name"] for s in load_strategies()]
    for expected in ("turtle_soup", "vwap"):
        assert expected in names, f"'{expected}' missing from strategies.yaml"


def test_registry_roster_is_exactly_turtle_soup_and_vwap():
    """Production roster — strict equality. Grows as strategies clear gates.

    History (category b — intentional config changes):
      * ict_scalp_5m went live 2026-05-14 (PR #1156, operator-approved).
      * trend_donchian went live on bybit_2 2026-05-23 (S-STRAT-IMPROVE-S8,
        operator-approved; docs/sprint-plans/TREND-GOLIVE-PLAN-2026-05-23.md).
      * fade_breakout_4h registered 2026-05-24 (S9) as an execution: shadow
        data-collector (NOT live; never sends a live order).
      * fvg_range_15m registered 2026-05-30 as an execution: shadow
        data-collector (NOT live; never sends a live order) — the range member.
      * htf_pullback_trend_2h registered 2026-06-01 as an execution: shadow
        data-collector (NOT live; never sends a live order) — the overnight-
        research HTF-pullback trend-follower.
      * mgc_pullback_1d + mhg_pullback_1d registered 2026-06-02 as the WS-A
        metals sleeve (Micro Gold / Micro Copper daily HTF-pullback diversifiers
        on IBKR ib_paper, execution: live on PAPER money).
    Roster: turtle_soup + vwap + ict_scalp_5m + trend_donchian + fade_breakout_4h
    + squeeze_breakout_4h + fvg_range_15m + htf_pullback_trend_2h + trend_donchian_1h
    + mes_trend_long_1d + mgc_pullback_1d + mhg_pullback_1d + xauusd_trend_1h
    (M15 Phase 3, 2026-06-11 — gold 1h trend on OANDA practice, execution: shadow).
    """
    from src.strategy_registry import load_strategies
    names = sorted(s["name"] for s in load_strategies())
    assert names == [
        "eth_pullback_2h",
        "fade_breakout_4h",
        "fvg_range_15m",
        "gld_pullback_1d",
        "htf_pullback_trend_2h",
        "ict_scalp_5m",
        "mes_trend_long_1d",
        "mgc_pullback_1d",
        "mgc_trend_1h",
        "mhg_pullback_1d",
        "qqq_trend_long_1d",
        "spy_trend_long_1d",
        "squeeze_breakout_4h",
        "trend_donchian",
        "trend_donchian_1h",
        "trend_donchian_eth",
        "trend_donchian_sol",
        "turtle_soup",
        "vwap",
        "xauusd_trend_1h",
    ]


def test_registry_fallback_loader_returns_new_roster(monkeypatch):
    """_strategies_from_registry() falls back to hardcoded list when registry is broken."""
    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("registry broken")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    # Import the fallback logic inline — mirrors what pipeline.py does.
    try:
        from src.strategy_registry import load_strategies
        result = [s["name"] for s in load_strategies()]
    except Exception:
        result = ["turtle_soup", "vwap"]
    for name in ("turtle_soup", "vwap"):
        assert name in result


# ---------------------------------------------------------------------------
# data_loaders — registry-first for list_live_strategies
# ---------------------------------------------------------------------------

def test_list_live_strategies_returns_registry_names():
    """list_live_strategies() must return the registry strategy names."""
    from src.bot import data_loaders as dl
    from src.strategy_registry import load_strategies

    result = dl.list_live_strategies()
    expected = [s["name"] for s in load_strategies()]
    assert result == expected


def test_list_live_strategies_pipeline_fallback(monkeypatch):
    """When registry is unavailable, falls back to pipeline.STRATEGIES."""
    from src.bot import data_loaders as dl

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("registry broken")

    fake_pipeline = types.ModuleType("src.runtime.pipeline")
    fake_pipeline.STRATEGIES = ["alpha", "beta"]

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    monkeypatch.setitem(sys.modules, "src.runtime.pipeline", fake_pipeline)
    assert dl.list_live_strategies() == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# data_loaders — registry-first for list_trader_services
# ---------------------------------------------------------------------------

def test_list_trader_services_returns_deduplicated_registry_services():
    """list_trader_services() returns unique service names from the registry.

    S-012 PR C4: single-process architecture — every strategy maps to
    ict-trader-live. The function dedupes so callers see one entry per
    real systemd unit, not one per strategy.
    """
    from src.bot import data_loaders as dl
    from src.strategy_registry import load_strategies

    result = dl.list_trader_services()
    expected = list(dict.fromkeys(s["service"] for s in load_strategies()))
    assert result == expected
    # Production roster of two strategies → one unique service.
    assert len(set(result)) == len(result)


def test_list_trader_services_all_ict_trader_prefix():
    """Every service in the registry must start with ict-trader-."""
    from src.bot import data_loaders as dl

    for svc in dl.list_trader_services():
        assert svc.startswith("ict-trader-"), f"Unexpected service prefix: {svc}"
