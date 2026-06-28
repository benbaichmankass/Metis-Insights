"""Regime policy loader + cell evaluator (PERF-20260601-002 phase 2).

Tests cover:
  * The shipped policy YAML loads cleanly and contains the expected
    structural shape (one block per regime, the off cells we decided in
    the matrix).
  * ``would_gate`` returns ``gated=True`` only for explicitly-off cells;
    everything else is permissive (matches the design doc § 4.2
    "default permissive" rule).
  * ``would_gate`` is robust to bad input — unknown regimes, unknown
    strategies, malformed cell values, flat sides, etc. — and never
    raises (phase 2 is observability-only).
  * ``load_policy`` returns ``{}`` on any failure (missing file / bad
    YAML) so a partial deploy can't break the aggregator.
"""
from __future__ import annotations

from pathlib import Path

from src.runtime.regime import load_policy, would_gate


# --- The shipped policy YAML loads + matches the matrix decisions ----------

def test_shipped_policy_loads_and_has_expected_regimes():
    """The committed `config/regime_policy.yaml` must round-trip and
    expose the three regime blocks the design doc specifies."""
    policy = load_policy()
    assert set(policy.keys()) >= {"chop", "transitional", "trending"}
    for regime in ("chop", "transitional", "trending"):
        assert isinstance(policy[regime], dict), f"{regime} must be a mapping"


def test_shipped_policy_off_cells_match_matrix_decisions():
    """Sanity-check that the loudest cells (vwap off everywhere, fvg off
    in chop, trend-short off in trending/transitional) round-trip from
    YAML so a hand-edit can't silently flip a load-bearing decision."""
    policy = load_policy()

    # vwap is OFF in every regime (matrix verdict — net loser everywhere)
    for regime in ("chop", "transitional", "trending"):
        vwap_cell = policy[regime]["vwap"]
        assert vwap_cell["long"] in (False, "off"), (regime, vwap_cell)
        assert vwap_cell["short"] in (False, "off"), (regime, vwap_cell)

    # fvg is OFF in chop (its target regime, but matrix says net loser)
    fvg_chop = policy["chop"]["fvg_range_15m"]
    assert fvg_chop["long"] in (False, "off")
    assert fvg_chop["short"] in (False, "off")

    # trend short is OFF in trending and transitional (BTC uptrend
    # punishes trend-shorts there)
    assert policy["trending"]["trend_donchian"]["short"] in (False, "off")
    assert policy["transitional"]["trend_donchian"]["short"] in (False, "off")
    # But trend short is ON in chop (the +16 R cell we want to reclaim)
    assert policy["chop"]["trend_donchian"]["short"] in (True, "on")


# --- would_gate happy path -------------------------------------------------

def test_would_gate_off_cell_returns_gated():
    policy = load_policy()
    out = would_gate(strategy="vwap", side="long", regime="chop", policy=policy)
    assert out["gated"] is True
    assert out["cell"] == "off"
    assert out["reason"] == "regime_gated_chop"
    assert out["regime"] == "chop"
    assert out["strategy"] == "vwap"
    assert out["side"] == "long"


def test_would_gate_on_cell_returns_allow():
    policy = load_policy()
    out = would_gate(strategy="trend_donchian", side="long",
                     regime="trending", policy=policy)
    assert out["gated"] is False
    assert out["cell"] == "on"


# --- would_gate permissive defaults ----------------------------------------

def test_would_gate_unknown_strategy_is_permissive():
    policy = load_policy()
    out = would_gate(strategy="never_heard_of_it", side="long",
                     regime="trending", policy=policy)
    assert out["gated"] is False
    assert out["cell"] == "default-on"


def test_would_gate_unknown_regime_is_permissive():
    policy = load_policy()
    out = would_gate(strategy="vwap", side="long", regime="unknown",
                     policy=policy)
    assert out["gated"] is False
    assert out["cell"] == "unknown-regime"


def test_would_gate_none_regime_is_permissive():
    """ADX warmup / detector failure upstream → regime=None on the intent.
    Must NEVER fire a would-gate (the strategy never had a chance to be
    measured in any regime)."""
    policy = load_policy()
    out = would_gate(strategy="vwap", side="long", regime=None, policy=policy)
    assert out["gated"] is False
    assert out["cell"] == "unknown-regime"


def test_would_gate_flat_side_is_permissive():
    """Flat / non-directional sides bypass the direction-based policy."""
    policy = load_policy()
    out = would_gate(strategy="vwap", side="flat", regime="chop",
                     policy=policy)
    assert out["gated"] is False


def test_would_gate_empty_policy_is_permissive_everywhere():
    """An empty {} (the result of a missing / failed load) must never
    gate. Critical: this is what protects the live tick when the operator
    deploys with an unreachable / malformed policy file."""
    out = would_gate(strategy="vwap", side="long", regime="chop", policy={})
    assert out["gated"] is False
    assert out["cell"] == "default-on"


# --- would_gate robustness on malformed cells ------------------------------

def test_would_gate_with_yaml_literal_strings():
    """Tolerate both YAML booleans (on/off → True/False) and the literal
    strings 'on'/'off' so a hand-edit of the YAML can't accidentally
    flip semantics. PyYAML maps yaml-style on/off to booleans by default."""
    policy = {"chop": {"my_strategy": {"long": "off", "short": "on"}}}
    out = would_gate(strategy="my_strategy", side="long", regime="chop",
                     policy=policy)
    assert out["gated"] is True
    out = would_gate(strategy="my_strategy", side="short", regime="chop",
                     policy=policy)
    assert out["gated"] is False


def test_would_gate_weight_value_is_permissive():
    """Phase-4 soft weights (numeric cells) are not active in phase 2;
    a numeric value is treated as permissive with a tagged reason so
    future-phase configs already merged don't suddenly start gating."""
    policy = {"chop": {"my_strategy": {"long": 0.5}}}
    out = would_gate(strategy="my_strategy", side="long", regime="chop",
                     policy=policy)
    assert out["gated"] is False
    assert out["cell"] == "weight"


def test_would_gate_unrecognised_value_is_permissive():
    """Typo in the YAML must NEVER produce a false gate."""
    policy = {"chop": {"my_strategy": {"long": "maybe"}}}
    out = would_gate(strategy="my_strategy", side="long", regime="chop",
                     policy=policy)
    assert out["gated"] is False
    assert out["cell"] == "unknown-value"


# --- load_policy robustness ------------------------------------------------

def test_load_policy_missing_file_returns_empty():
    out = load_policy(path="/tmp/does-not-exist-12345.yaml")
    assert out == {}


def test_load_policy_malformed_yaml_returns_empty(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(":::: not yaml ::::")
    out = load_policy(path=str(p))
    assert out == {}


def test_load_policy_non_mapping_returns_empty(tmp_path):
    """A YAML file that parses to a list (not a mapping) must be
    rejected — the aggregator expects a regime-keyed dict."""
    p = tmp_path / "list.yaml"
    p.write_text("- chop\n- trending\n")
    out = load_policy(path=str(p))
    assert out == {}


# --- the committed YAML's path resolution ---------------------------------

def test_default_policy_path_resolves_to_repo_config():
    """The module's default path must point at the in-repo config file
    so an operator running the bot from anywhere (cron, systemd, manual)
    picks up the committed policy."""
    from src.runtime.regime.policy import _REGIME_POLICY_PATH
    expected_tail = "config/regime_policy.yaml"
    assert _REGIME_POLICY_PATH.endswith(expected_tail), _REGIME_POLICY_PATH
    assert Path(_REGIME_POLICY_PATH).is_file()


# === S-MLOPT-S15b — 2-D trend × vol axis ===================================

def test_would_gate_without_vol_regime_is_byte_identical_1d():
    """Default-preserving: omitting vol_regime returns the exact 1-D shape —
    no vol_* keys leak into a caller that didn't ask for the vol axis."""
    policy = load_policy()
    out = would_gate(strategy="vwap", side="long", regime="chop", policy=policy)
    assert set(out.keys()) == {"gated", "reason", "cell", "regime", "strategy", "side"}
    assert out["gated"] is True  # vwap off in chop (1-D unchanged)


def test_would_gate_with_vol_regime_adds_vol_keys():
    policy = load_policy()
    out = would_gate(strategy="vwap", side="long", regime="chop",
                     policy=policy, vol_regime="volatile")
    # 1-D verdict unchanged…
    assert out["gated"] is True
    assert out["cell"] == "off"
    # …plus the observe-only vol axis.
    assert out["vol_regime"] == "volatile"
    assert out["vol_gated"] is False  # shipped trend_vol is empty → permissive
    assert out["vol_cell"] == "default-on"


def test_would_gate_2d_off_cell_gates_vol_axis_only():
    """A 2-D off cell sets vol_gated=True WITHOUT touching the 1-D decision."""
    policy = {
        "trend_vol": {
            "trending": {"volatile": {"vwap": {"long": "off", "short": "on"}}}
        }
    }
    out = would_gate(strategy="vwap", side="long", regime="trending",
                     policy=policy, vol_regime="volatile")
    # 1-D: trending/vwap not listed in this in-memory policy → permissive.
    assert out["gated"] is False
    assert out["cell"] == "default-on"
    # 2-D: explicitly off → vol_gated.
    assert out["vol_gated"] is True
    assert out["vol_cell"] == "off"
    assert out["vol_reason"] == "vol_gated_trending_volatile"
    # The other side is explicitly on.
    out2 = would_gate(strategy="vwap", side="short", regime="trending",
                      policy=policy, vol_regime="volatile")
    assert out2["vol_gated"] is False
    assert out2["vol_cell"] == "on"


def test_would_gate_2d_permissive_when_vol_cell_absent():
    policy = {
        "trend_vol": {"trending": {"volatile": {"vwap": {"long": "off"}}}}
    }
    # calm is not listed under trending → permissive default on the vol axis.
    out = would_gate(strategy="vwap", side="long", regime="trending",
                     policy=policy, vol_regime="calm")
    assert out["vol_gated"] is False
    assert out["vol_cell"] == "default-on"


def test_would_gate_2d_unknown_vol_regime_is_permissive():
    policy = {"trend_vol": {"chop": {"calm": {"vwap": {"long": "off"}}}}}
    out = would_gate(strategy="vwap", side="long", regime="chop",
                     policy=policy, vol_regime="unknown")
    assert out["vol_gated"] is False
    assert out["vol_cell"] == "vol-unknown"


def test_would_gate_2d_unknown_trend_regime_is_permissive():
    policy = {"trend_vol": {"chop": {"calm": {"vwap": {"long": "off"}}}}}
    out = would_gate(strategy="vwap", side="long", regime=None,
                     policy=policy, vol_regime="calm")
    assert out["vol_gated"] is False
    assert out["vol_cell"] == "trend-unknown"


def test_would_gate_2d_flat_side_never_gates_vol():
    policy = {"trend_vol": {"chop": {"calm": {"vwap": {"long": "off"}}}}}
    out = would_gate(strategy="vwap", side="flat", regime="chop",
                     policy=policy, vol_regime="calm")
    assert out["vol_gated"] is False


def test_would_gate_2d_empty_policy_is_permissive():
    out = would_gate(strategy="vwap", side="long", regime="chop",
                     policy={}, vol_regime="volatile")
    assert out["vol_gated"] is False
    assert out["vol_cell"] == "default-on"


def test_shipped_policy_has_schema_2_and_wellformed_trend_vol():
    """The committed table ships at schema_version 2 with the Design-A
    evidence-based trend_vol OFF-cells authored from the vol-split
    (docs/research/A-vol-gating-OFFcell-design-2026-06-27.md). The cells are a
    behavioural no-op for live orders until REGIME_ML_VERDICT_MODE=use AND the
    regime router enforces (baseline-on since the Design-A vol-gate go-live;
    kill-switch REGIME_ROUTER_DISABLED) — this test guards the shipped shape,
    not the live gate.

    Guards: (a) schema_version 2; (b) every trend_vol leaf is a well-formed
    on/off side under trend→vol→strategy (no malformed cell); (c) the four
    evidence OFF-cells are present (a future accidental edit that drops/flips
    one is caught)."""
    policy = load_policy()
    assert policy.get("schema_version") == 2

    trend_vol = policy.get("trend_vol") or {}
    assert isinstance(trend_vol, dict) and trend_vol, "trend_vol authored, non-empty"

    valid_sides = {"long", "short"}
    valid_vals = {True, False, "on", "off"}
    for trend, vols in trend_vol.items():
        assert trend in {"trending", "transitional", "chop"}, trend
        for vol, strats in vols.items():
            assert vol in {"calm", "volatile"}, vol
            for strat, sides in strats.items():
                assert isinstance(sides, dict), (trend, vol, strat)
                for side, val in sides.items():
                    assert side in valid_sides, (trend, vol, strat, side)
                    assert val in valid_vals, (trend, vol, strat, side, val)

    # The four authored evidence OFF-cells (off == False after YAML parse).
    assert trend_vol["trending"]["volatile"]["trend_donchian"]["long"] is False
    assert trend_vol["trending"]["calm"]["squeeze_breakout_4h"]["short"] is False
    assert trend_vol["transitional"]["calm"]["trend_donchian"]["long"] is False
    assert trend_vol["chop"]["calm"]["trend_donchian"]["long"] is False
