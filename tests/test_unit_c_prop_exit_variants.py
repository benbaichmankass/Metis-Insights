"""Unit C — swap-robust prop EXIT variants + the backtest exit-ladder fill mode.

Covers Phase 0 (config-only tightened-exit prop variants of trend_donchian_sol /
trend_donchian_eth, mirroring the validated eth_pullback_prop_2h recipe) and the
Phase 1 backtest scaffold (the harness ``--exit-ladder`` partial-TP fill mode in
scripts/backtest_system.py). Design:
docs/research/prop-dynamic-exits-faster-banking-DESIGN.md § 5.

These are DRAFT Tier-3 (execution: shadow, observe-only) until the prop
EV/survival gate passes; the tests assert the wiring + the tightened params +
that the change is Prime-Directive-clean (no per-strategy risk_pct).
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_VARIANTS = ("trend_donchian_sol_prop", "trend_donchian_eth_prop")


def _strategies() -> dict:
    return yaml.safe_load(open(_ROOT / "config" / "strategies.yaml"))["strategies"]


def _accounts() -> dict:
    return yaml.safe_load(open(_ROOT / "config" / "accounts.yaml"))["accounts"]


# ── Phase 0: config ────────────────────────────────────────────────────────────
def test_variants_present_with_tightened_exits():
    s = _strategies()
    for name in _VARIANTS:
        b = s[name]
        assert b["enabled"] is True
        # DRAFT: observe-only prop soak until the prop EV gate passes.
        assert b["execution"] == "shadow"
        assert b["timeframe"] == "1h"
        # The eth_pullback_prop_2h SWAP-ROBUST recipe: tighter trail + a real
        # 6R cap (vs the live 50.0 sentinel / 5.0 trail the un-tightened cells use).
        assert b["trail_mult"] == 3.5
        assert b["tp_r"] == 6.0
        # Same entry params as the un-tightened siblings (only exits differ).
        assert (b["donchian"], b["atr_period"], b["atr_stop_mult"]) == (20, 14, 2.5)
        assert b["min_confidence"] == 0.60


def test_exit_recipe_matches_validated_prop_sibling():
    # The tightened exits MUST equal the already-validated eth_pullback_prop_2h
    # recipe (the design's proven bar) — drift here defeats Phase 0's premise.
    s = _strategies()
    bar = s["eth_pullback_prop_2h"]
    for name in _VARIANTS:
        assert s[name]["trail_mult"] == bar["trail_mult"]
        assert s[name]["tp_r"] == bar["tp_r"]


def test_entries_unchanged_vs_untightened_siblings():
    # The variants must NOT touch the original demo/real-money cells' entries —
    # only the exits are tightened.
    s = _strategies()
    pairs = (("trend_donchian_sol_prop", "trend_donchian_sol"),
             ("trend_donchian_eth_prop", "trend_donchian_eth"))
    for variant, base in pairs:
        for k in ("donchian", "atr_period", "atr_stop_mult", "min_confidence", "timeframe"):
            assert s[variant][k] == s[base][k], f"{variant}.{k} drifted from {base}"
        # SOL is long-only (its edge holds long-only); ETH stays two-sided.
        assert s["trend_donchian_sol_prop"].get("long_only") is True
        assert "long_only" not in s["trend_donchian_eth_prop"]


def test_untightened_cells_left_untouched():
    # The original prop-routed cells keep their let-winners-run exits.
    s = _strategies()
    for base in ("trend_donchian_sol", "trend_donchian_eth", "eth_pullback_2h"):
        assert s[base]["tp_r"] == 50.0
        assert s[base]["trail_mult"] == 5.0


def test_no_per_strategy_risk_pct():
    # Prime Directive / strategy-risk-guard: a strategy carries NO risk level.
    s = _strategies()
    for name in _VARIANTS:
        assert "risk_pct" not in s[name]


def test_shadow_guard_marker_present():
    # A new execution: shadow line needs the inline shadow-guard marker (CI guard).
    raw = (_ROOT / "config" / "strategies.yaml").read_text().splitlines()
    for name in _VARIANTS:
        # find the block, then its execution line carries the marker
        idx = next(i for i, ln in enumerate(raw) if ln.strip().startswith(f"{name}:"))
        exec_line = next(ln for ln in raw[idx:idx + 25]
                         if ln.strip().startswith("execution:"))
        assert "shadow-guard: allow" in exec_line


def test_routed_to_breakout_1():
    a = _accounts()["breakout_1"]
    for name in _VARIANTS:
        assert name in a["strategies"]


def test_descriptions_and_changelog_present():
    d = json.load(open(_ROOT / "config" / "strategy_descriptions.json"))
    c = json.load(open(_ROOT / "config" / "strategy_changelog.json"))
    for name in _VARIANTS:
        assert name in d and d[name]["short"] and d[name]["how_it_works"]
        assert name in c and isinstance(c[name], list) and c[name]


# ── Phase 0: wiring ──────────────────────────────────────────────────────────
def test_wired_into_builders_multiplexer_priorities_pipeline():
    from src.runtime.strategy_signal_builders import (
        trend_donchian_sol_prop_signal_builder,
        trend_donchian_eth_prop_signal_builder,
    )
    from src.runtime.intent_multiplexer import _default_intent_builders
    from src.runtime.intents import DEFAULT_PRIORITIES
    from src.runtime.pipeline import _STRATEGY_BUILDERS

    builders = _default_intent_builders()
    assert builders["trend_donchian_sol_prop"] is trend_donchian_sol_prop_signal_builder
    assert builders["trend_donchian_eth_prop"] is trend_donchian_eth_prop_signal_builder
    for name in _VARIANTS:
        assert DEFAULT_PRIORITIES[name] == 0          # floor — never wins arbitration
        assert name in _STRATEGY_BUILDERS
    # monitor() resolves via the trend_donchian unit tag (no same-name module).
    assert getattr(trend_donchian_sol_prop_signal_builder, "monitor_unit") == "trend_donchian"
    assert getattr(trend_donchian_eth_prop_signal_builder, "monitor_unit") == "trend_donchian"


def test_builder_disabled_gate():
    import src.units.strategies as us
    from src.runtime import strategy_signal_builders as ssb

    orig = us.load_strategy_config
    us.load_strategy_config = lambda: {"trend_donchian_sol_prop": {"enabled": False}}
    try:
        out = ssb.trend_donchian_sol_prop_signal_builder({})
    finally:
        us.load_strategy_config = orig
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "disabled_in_yaml"


# ── Phase 1: backtest exit-ladder fill mode ──────────────────────────────────
def test_prop_variants_registered_in_backtest_roster():
    import scripts.backtest_system as bt
    for name in _VARIANTS:
        assert name in bt.ROSTER
        assert bt.ROSTER[name]["module"] == "src.units.strategies.trend_donchian"
        assert bt.ROSTER[name]["tf"] == "1h"


def test_build_ladder_targets_long_and_short():
    import scripts.backtest_system as bt
    # R = |entry - sl| = 2; rungs at +1.5R / +3R, qtys 50% / 25% of total.
    long = bt._build_ladder_targets("long", entry=100.0, sl=98.0, qty_total=10.0)
    assert [(t["price"], t["qty"]) for t in long] == [(103.0, 5.0), (106.0, 2.5)]
    short = bt._build_ladder_targets("short", entry=100.0, sl=102.0, qty_total=10.0)
    assert [(t["price"], t["qty"]) for t in short] == [(97.0, 5.0), (94.0, 2.5)]


def test_build_ladder_targets_bad_input_is_empty():
    import scripts.backtest_system as bt
    assert bt._build_ladder_targets("long", 100.0, 100.0, 10.0) == []   # R=0
    assert bt._build_ladder_targets("long", 100.0, 98.0, 0.0) == []     # no qty
    assert bt._build_ladder_targets("flat", 100.0, 98.0, 10.0) == []    # bad side


def test_run_system_backtest_accepts_exit_ladder_kwarg():
    # The kwarg exists + threads through (signature contract; the prop EV gate
    # calls run_system_backtest(..., exit_ladder=...)).
    import inspect
    import scripts.backtest_system as bt
    sig = inspect.signature(bt.run_system_backtest)
    assert "exit_ladder" in sig.parameters
    assert sig.parameters["exit_ladder"].default is False


def test_account_compat_matrix_exposes_exit_ladder_flag():
    import scripts.prop.account_compat_matrix as acm
    src = Path(acm.__file__).read_text()
    assert "--exit-ladder" in src
    assert "exit_ladder=args.exit_ladder" in src
