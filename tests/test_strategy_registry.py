"""Tests for src/strategy_registry.py (S-007 PR #113)."""
from __future__ import annotations

import os
import textwrap

import pytest

import src.strategy_registry as reg

# Path to the real YAML so integration tests can use it directly.
_REAL_YAML = os.path.join(
    os.path.dirname(__file__), "..", "config", "strategies.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, content: str) -> str:
    p = tmp_path / "strategies.yaml"
    p.write_text(textwrap.dedent(content))
    return str(p)


# ---------------------------------------------------------------------------
# load_strategies — unit tests with synthetic YAML
# ---------------------------------------------------------------------------

def test_load_strategies_returns_list(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            service: ict-trader-alpha
            model: alpha_v1.joblib
          beta:
            service: ict-trader-beta
            model: null
    """)
    strategies = reg.load_strategies(path)
    assert isinstance(strategies, list)
    assert len(strategies) == 2


def test_load_strategies_fields(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          alpha:
            service: ict-trader-alpha
            model: alpha_v1.joblib
    """)
    s = reg.load_strategies(path)[0]
    assert s["name"] == "alpha"
    assert s["service"] == "ict-trader-alpha"
    assert s["model"] == "alpha_v1.joblib"


def test_load_strategies_null_model(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          gamma:
            service: ict-trader-gamma
            model: null
    """)
    s = reg.load_strategies(path)[0]
    assert s["model"] is None


def test_load_strategies_missing_service_defaults_to_live(tmp_path):
    """S-012 PR C4: missing service defaults to ict-trader-live.

    Single-process architecture (PM § 8 #1): every strategy runs inside
    the same systemd unit. Per-strategy service names are aspirational
    metadata that this sprint removed.
    """
    path = _write_yaml(tmp_path, """
        strategies:
          delta:
            model: null
    """)
    s = reg.load_strategies(path)[0]
    assert s["service"] == "ict-trader-live"


def test_load_strategies_bad_yaml_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies: "not-a-mapping"
    """)
    with pytest.raises(ValueError, match="expected mapping"):
        reg.load_strategies(path)


# ---------------------------------------------------------------------------
# model_path — unit tests
# ---------------------------------------------------------------------------

def test_model_path_returns_none_for_null_model(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          vwap:
            service: ict-trader-vwap
            model: null
    """)
    assert reg.model_path("vwap", path) is None


def test_model_path_returns_absolute_path(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          breakout_confirmation:
            service: ict-trader-breakout
            model: btc_v1.joblib
    """)
    result = reg.model_path("breakout_confirmation", path)
    assert result is not None
    assert os.path.isabs(result)
    assert result.endswith("btc_v1.joblib")


def test_model_path_unknown_strategy_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          vwap:
            service: ict-trader-vwap
            model: null
    """)
    with pytest.raises(KeyError, match="nonexistent"):
        reg.model_path("nonexistent", path)


# ---------------------------------------------------------------------------
# service_name — unit tests
# ---------------------------------------------------------------------------

def test_service_name_returns_string(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          ict:
            service: ict-trader-ict
            model: null
    """)
    assert reg.service_name("ict", path) == "ict-trader-ict"


def test_service_name_unknown_strategy_raises(tmp_path):
    path = _write_yaml(tmp_path, """
        strategies:
          vwap:
            service: ict-trader-vwap
            model: null
    """)
    with pytest.raises(KeyError):
        reg.service_name("not_there", path)


# ---------------------------------------------------------------------------
# Integration — real config/strategies.yaml
# ---------------------------------------------------------------------------

def test_real_yaml_loads():
    strategies = reg.load_strategies(_REAL_YAML)
    # turtle_soup + vwap + ict_scalp_5m + trend_donchian + fade_breakout_4h +
    # squeeze_breakout_4h + fvg_range_15m.
    # Bumped 2 → 3 by ict_scalp_5m, 3 → 4 by the trend_donchian go-live
    # (S-STRAT-IMPROVE-S8, 2026-05-23), 4 → 5 by the fade_breakout_4h shadow
    # wiring (S9, 2026-05-24), 5 → 6 by squeeze_breakout_4h, then 6 → 7 by the
    # fvg_range_15m shadow wiring (2026-05-30), then 7 → 8 by the
    # htf_pullback_trend_2h shadow wiring (2026-06-01), then 8 → 9 by the
    # trend_donchian_1h shadow A/B wiring (2026-06-01), then 9 → 10 by the
    # mes_trend_long_1d shadow wiring (2026-06-01), then 10 → 12 by the WS-A
    # metals sleeve mgc_pullback_1d + mhg_pullback_1d (2026-06-02), then 12 → 13
    # by the M15 Phase-3 gold sleeve xauusd_trend_1h (2026-06-11, OANDA practice,
    # execution: shadow). Live turn-on is gated by each strategy's `enabled` /
    # `execution` flags in the YAML, not the count. 13 → 16 by the M15
    # Phase-4 ETF buildout (spy/qqq trend + gld pullback on alpaca_paper,
    # 2026-06-11), then 16 → 17 by the M15 WS-C alt sleeve eth_pullback_2h
    # (ETH/USDT 2h HTF-pullback on bybit_1 demo, 2026-06-11), then 17 → 18 by
    # the mgc_trend_1h gold sleeve (IBKR MGC micro futures on ib_paper paper
    # money, 2026-06-12 — the venue-swap sibling of xauusd_trend_1h after OANDA
    # US blocked XAU_USD, BL-20260611-007).
    # 18 → 20 by the prop alt variants trend_donchian_sol + trend_donchian_eth
    # (2026-06-17, PB-20260616-004 — routed to the breakout_1 prop account).
    # 20 → 29 by the 9 paper_ready alt cells wired to bybit_1 DEMO (2026-06-18,
    # Tier-3): 5 trend_4h (trend_donchian_{eth,sol,xrp,ada,avax}_4h) + 4
    # pullback_2h ({sol,xrp,ada,avax}_pullback_2h). WS-C k-fold paper_ready
    # (net-of-fee positive + 2x-fee headroom; fail only the strict every-fold
    # gate — SRQ-20260618-001/-002). Demo-only soak, NOT live-money-ready.
    # 29 → 32 by the ETF-breadth daily sweep (2026-06-20, Tier-3): iwm_trend_long_1d
    # (small-cap trend) + tlt_pullback_1d + ief_pullback_1d (Treasury-bond pullback)
    # — three new daily-ETF cells on alpaca_paper (paper money).
    # 32 → 34 by the intraday ETF pilot (2026-06-20 § 0e, Tier-3): gld_pullback_1h
    # (GLD 1h bidirectional pullback) + slv_trend_1h (SLV 1h bidirectional Donchian
    # trend) — the first INTRADAY ETF cells on alpaca_paper (paper money).
    # 34 → 38 by the intraday ETF rollout 2b (2026-06-20 § 0e, Tier-3) completing
    # the intraday ETF sleeve: spy_pullback_1h + qqq_pullback_1h + tlt_pullback_1h
    # (1h bidirectional pullback) + uso_trend_1h (1h LONG-ONLY Donchian trend) on
    # alpaca_paper (paper money).
    # 38 → 39 by the swap-robust prop variant eth_pullback_prop_2h (2026-06-25,
    # DRAFT Tier-3): a tighter-exit (tp_r 6 / trail 3.5) sibling of eth_pullback_2h
    # routed to breakout_1 as execution: shadow — the live let-winners-run exits go
    # net-negative after Breakout's 0.09%/day swap; this variant flips post-swap
    # positive + passes the funded-EV gate 4/4 folds. Observe-only soak, NOT a
    # live-money promotion. docs/research/eth-pullback-prop-swap-aware-2026-06-25.md.
    # 39 → 41 by the daily ETF pullback pair slv_pullback_1d + gdx_pullback_1d
    # (2026-06-27, Tier-3): same htf_pullback_trend_2h unit as gld_pullback_1d,
    # routed to alpaca_paper + alpaca_live (SLV ~$25, GDX ~$43/share — the
    # lowest-priced ETFs, best chance of fitting the alpaca_live budget at 2% risk).
    # 41 → 43 by the Unit C prop EXIT variants trend_donchian_sol_prop +
    # trend_donchian_eth_prop (2026-06-29, DRAFT Tier-3): the validated
    # eth_pullback_prop_2h recipe (trail 3.5 / tp_r 6) applied to the un-tightened
    # SOL/ETH prop cells, routed to breakout_1 as execution: shadow. Observe-only
    # soak, NOT a live-money promotion. docs/research/prop-dynamic-exits-faster-banking-DESIGN.md.
    # 43 → 45 by the leveraged Nasdaq-100 ETF trend cells tqqq_trend_long_1d (3x) +
    # qld_trend_long_1d (2x) (2026-06-30, Tier-3): same trend_donchian unit + params
    # as qqq_trend_long_1d, backtested on the actual leveraged price series
    # (decay + expense embedded) — both paper_ready, TQQQ beat the QQQ cell. Routed
    # to alpaca_paper (paper money). docs/research/leveraged-etf-research-2026-06-30.md.
    # 45 → 48 by the sub-$100 proxy cells splg_trend_long_1d + iaum_pullback_1d +
    # scha_trend_long_1d (2026-07-07, Tier-3): cheap-share equivalents of SPY/GLD/IWM.
    # 48 → 51 by the M27 P0 Batch-1 ict_scalp alt variants ict_scalp_sol_5m +
    # ict_scalp_xrp_5m + ict_scalp_avax_5m (2026-07-21, Tier-3, operator-approved
    # promotion): execution:live demo-soak on bybit_1 only (XRP carries a
    # strategy-local off-cells regime gate; SOL/AVAX ungated per their own
    # M27 Batch-1 evidence).
    # 51 → 54 by the M27 P1 15m scalp legs ict_scalp_xrp_15m + ict_scalp_eth_15m +
    # ict_scalp_sol_15m (2026-07-22, Tier-3, operator-approved promotion, PR #7400):
    # net-of-fee anchored k-fold gate cleared (>=3/4 folds, ungated), routed to
    # bybit_1 paper/demo alongside the existing 5m legs. Real-money bybit_2 is a
    # separate later Tier-3 gate.
    assert len(strategies) == 54


def test_real_yaml_has_required_strategies():
    strategies = reg.load_strategies(_REAL_YAML)
    names = {s["name"] for s in strategies}
    assert names == {
        "turtle_soup", "vwap", "ict_scalp_5m", "trend_donchian", "fade_breakout_4h",
        "squeeze_breakout_4h", "fvg_range_15m", "htf_pullback_trend_2h", "trend_donchian_1h",
        "mes_trend_long_1d", "mgc_pullback_1d", "mhg_pullback_1d",
        "xauusd_trend_1h", "mgc_trend_1h",
        "spy_trend_long_1d", "qqq_trend_long_1d", "gld_pullback_1d",
        # Leveraged Nasdaq-100 ETF trend cells (2026-06-30, Tier-3) — TQQQ 3x + QLD 2x:
        "tqqq_trend_long_1d", "qld_trend_long_1d",
        # ETF-breadth daily sweep (2026-06-20, Tier-3) — 3 new daily-ETF cells:
        "iwm_trend_long_1d", "tlt_pullback_1d", "ief_pullback_1d",
        # Intraday ETF pilot (2026-06-20 § 0e, Tier-3) — 2 new INTRADAY ETF cells:
        "gld_pullback_1h", "slv_trend_1h",
        # Intraday ETF rollout 2b (2026-06-20 § 0e, Tier-3) — 4 cells completing
        # the intraday ETF sleeve (SPY/QQQ/TLT 1h pullback + USO 1h long-only trend):
        "spy_pullback_1h", "qqq_pullback_1h", "tlt_pullback_1h", "uso_trend_1h",
        "eth_pullback_2h",
        "trend_donchian_sol", "trend_donchian_eth",
        # 9 paper_ready alt cells on bybit_1 DEMO (2026-06-18, Tier-3):
        "trend_donchian_eth_4h", "trend_donchian_sol_4h", "trend_donchian_xrp_4h",
        "trend_donchian_ada_4h", "trend_donchian_avax_4h",
        "sol_pullback_2h", "xrp_pullback_2h", "ada_pullback_2h", "avax_pullback_2h",
        # swap-robust prop variant — breakout_1 shadow soak (DRAFT, Tier-3, 2026-06-25):
        "eth_pullback_prop_2h",
        # daily ETF pullback pair on alpaca_paper + alpaca_live (2026-06-27, Tier-3):
        "slv_pullback_1d", "gdx_pullback_1d",
        # Unit C prop EXIT variants — breakout_1 shadow soak (DRAFT, Tier-3, 2026-06-29):
        "trend_donchian_sol_prop", "trend_donchian_eth_prop",
        # sub-$100 proxy cells on alpaca_paper (2026-07-07, Tier-3) — cheap-share
        # equivalents of SPY/GLD/IWM: SPLG + IAUM + SCHA.
        "splg_trend_long_1d", "iaum_pullback_1d", "scha_trend_long_1d",
        # M27 P0 Batch-1 alt variants (2026-07-21, Tier-3, operator-approved
        # promotion): execution:live demo-soak on bybit_1 only.
        "ict_scalp_sol_5m", "ict_scalp_xrp_5m", "ict_scalp_avax_5m",
        # M27 P1 15m scalp legs (2026-07-22, Tier-3, operator-approved
        # promotion, PR #7400): execution:live demo-soak on bybit_1 only.
        "ict_scalp_xrp_15m", "ict_scalp_eth_15m", "ict_scalp_sol_15m",
    }


def test_real_yaml_vwap_no_model():
    assert reg.model_path("vwap", _REAL_YAML) is None


def test_real_yaml_turtle_soup_no_model():
    assert reg.model_path("turtle_soup", _REAL_YAML) is None


def test_real_yaml_service_names():
    # S-012 single-process: every strategy currently runs inside
    # ict-trader-live. The `service:` field is scheduled for removal in PR C4.
    assert reg.service_name("turtle_soup", _REAL_YAML) == "ict-trader-live"
    assert reg.service_name("vwap", _REAL_YAML) == "ict-trader-live"


def test_real_yaml_all_strategies_have_service():
    for s in reg.load_strategies(_REAL_YAML):
        assert s["service"], f"strategy '{s['name']}' missing service"
