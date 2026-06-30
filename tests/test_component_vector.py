"""Unit tests for src/research/component_vector.py — the canonical adapter.

Fully offline. Feeds representative fixture ``signal_logic`` dicts (captured
against the real builder keys, 2026-06-30) per strategy and asserts the
canonical components + kinds come out right, that the adapter is tolerant
(missing keys → component absent, never a raise) and pure (unknown strategy →
only the common components).
"""
from __future__ import annotations

from src.research.component_vector import (
    KIND_CATEGORICAL,
    KIND_GATE,
    KIND_GRADED,
    Component,
    extract,
    graded_component_names,
    specs_for,
)


# ---------------------------------------------------------------------------
# Fixture signal_logic rows — mirror the real meta the builders persist.
# ---------------------------------------------------------------------------


def _ict_scalp_logic() -> dict:
    return {
        "strategy_name": "ict_scalp_5m",
        "mitigation_mode": "wick_rejection",
        "htf_filter_active": True,
        "sweep_level": 50_000.0,
        "sweep_extreme": 49_800.0,
        "displacement_body_to_range": 0.72,
        "fvg_low": 49_900.0,
        "fvg_high": 49_950.0,
        "fvg_size": 50.0,
        "atr": 100.0,
        # regime stamp (added on every signal by _stamp_regime_on_meta)
        "regime": "trending",
        "adx_14": 31.4,
        "vol_regime": "volatile",
        "rolling_log_return_vol": 0.018,
    }


def _vwap_logic() -> dict:
    return {
        "strategy_name": "vwap",
        "vwap": 50_100.0,
        "std_dev": 120.0,
        "deviation_std": -1.8,
        "policy_threshold": 1.0,
        "regime": "ranging",
        "adx_14": 12.0,
        "vol_regime": "calm",
        "rolling_log_return_vol": 0.004,
    }


def _trend_donchian_logic() -> dict:
    # trend_donchian writes ATR + regime stamp; confidence is in its own column.
    return {
        "donchian_hi": 51_000.0,
        "donchian_lo": 49_000.0,
        "atr": 200.0,
        "regime": "trending",
        "adx_14": 28.0,
        "vol_regime": "volatile",
        "rolling_log_return_vol": 0.02,
    }


# ---------------------------------------------------------------------------
# ict_scalp
# ---------------------------------------------------------------------------


def test_ict_scalp_components_and_kinds():
    comps = extract("ict_scalp_5m", _ict_scalp_logic(), extra={"confidence": 0.65})

    # sweep_depth_atr = |49800 - 50000| / 100 = 2.0
    assert comps["sweep_depth_atr"].kind == KIND_GRADED
    assert abs(comps["sweep_depth_atr"].value - 2.0) < 1e-9

    # displacement_strength = body_to_range
    assert abs(comps["displacement_strength"].value - 0.72) < 1e-9
    assert comps["displacement_strength"].kind == KIND_GRADED

    # fvg_size_atr = 50 / 100 = 0.5
    assert abs(comps["fvg_size_atr"].value - 0.5) < 1e-9

    # mitigation_mode categorical, lowercased
    assert comps["mitigation_mode"].kind == KIND_CATEGORICAL
    assert comps["mitigation_mode"].value == "wick_rejection"

    # htf_bias_aligned gate
    assert comps["htf_bias_aligned"].kind == KIND_GATE
    assert comps["htf_bias_aligned"].value is True

    # common: confidence (from extra), adx_14, regime, vol_regime, vol
    assert comps["confidence"].kind == KIND_GRADED
    assert abs(comps["confidence"].value - 0.65) < 1e-9
    assert abs(comps["adx_14"].value - 31.4) < 1e-9
    assert comps["regime"].value == "trending"
    assert comps["vol_regime"].value == "volatile"
    assert abs(comps["rolling_log_return_vol"].value - 0.018) < 1e-9


def test_ict_scalp_graded_names_include_specifics():
    names = graded_component_names("ict_scalp_5m")
    for expected in ("sweep_depth_atr", "displacement_strength", "fvg_size_atr",
                     "confidence", "adx_14", "rolling_log_return_vol"):
        assert expected in names
    # mitigation_mode (categorical) + htf_bias_aligned (gate) are NOT graded.
    assert "mitigation_mode" not in names
    assert "htf_bias_aligned" not in names


# ---------------------------------------------------------------------------
# vwap
# ---------------------------------------------------------------------------


def test_vwap_components():
    comps = extract("vwap", _vwap_logic(), extra={"confidence": 0.8})
    assert comps["vwap_deviation_std"].kind == KIND_GRADED
    assert abs(comps["vwap_deviation_std"].value - (-1.8)) < 1e-9
    assert abs(comps["vwap_policy_threshold"].value - 1.0) < 1e-9
    assert comps["regime"].value == "ranging"
    assert comps["vol_regime"].value == "calm"
    assert abs(comps["confidence"].value - 0.8) < 1e-9
    # vwap has no sweep/fvg components
    assert "sweep_depth_atr" not in comps
    assert "fvg_size_atr" not in comps


def test_vwap_deviation_fallback_key():
    # build_vwap_signal writes deviation_std; tolerate the bare "deviation".
    comps = extract("vwap", {"deviation": -2.1, "vwap": 1.0})
    assert "vwap_deviation_std" in comps
    assert abs(comps["vwap_deviation_std"].value - (-2.1)) < 1e-9


# ---------------------------------------------------------------------------
# trend_donchian
# ---------------------------------------------------------------------------


def test_trend_donchian_common_only_plus_confidence():
    comps = extract("trend_donchian", _trend_donchian_logic(), extra={"confidence": 0.42})
    # trend_donchian has no strategy-specific graded components; the edge axis
    # is the composite confidence + the regime stamp.
    assert abs(comps["confidence"].value - 0.42) < 1e-9
    assert abs(comps["adx_14"].value - 28.0) < 1e-9
    assert comps["regime"].value == "trending"
    assert "sweep_depth_atr" not in comps


# ---------------------------------------------------------------------------
# Tolerance + purity
# ---------------------------------------------------------------------------


def test_missing_keys_absent_no_raise():
    # Empty signal_logic — nothing to extract except what extra supplies.
    comps = extract("ict_scalp_5m", {}, extra={"confidence": 0.5})
    assert "sweep_depth_atr" not in comps
    assert "fvg_size_atr" not in comps
    assert comps["confidence"].value == 0.5  # came from extra
    # no regime stamp present → no regime component
    assert "regime" not in comps


def test_none_signal_logic_tolerant():
    comps = extract("ict_scalp_5m", None, extra=None)
    assert comps == {}  # nothing derivable, no raise


def test_atr_zero_makes_atr_normalised_absent():
    logic = _ict_scalp_logic()
    logic["atr"] = 0.0  # division guard → those components drop out
    comps = extract("ict_scalp_5m", logic)
    assert "sweep_depth_atr" not in comps
    assert "fvg_size_atr" not in comps
    # displacement_strength is a plain key (no ATR), so it survives.
    assert "displacement_strength" in comps


def test_unparseable_values_dropped():
    comps = extract(
        "vwap",
        {"deviation_std": "not-a-number", "policy_threshold": None},
    )
    assert "vwap_deviation_std" not in comps
    assert "vwap_policy_threshold" not in comps


def test_unknown_strategy_only_common():
    logic = {
        "regime": "trending",
        "adx_14": 20.0,
        "vol_regime": "calm",
        "sweep_extreme": 1.0,
        "sweep_level": 2.0,
        "atr": 1.0,
    }
    comps = extract("totally_unknown_strategy", logic, extra={"confidence": 0.3})
    # Only common components — NOT the ict_scalp sweep_depth_atr even though the
    # keys are present, because the unknown strategy has no spec for it.
    assert "sweep_depth_atr" not in comps
    assert comps["regime"].value == "trending"
    assert comps["vol_regime"].value == "calm"
    assert abs(comps["adx_14"].value - 20.0) < 1e-9
    assert abs(comps["confidence"].value - 0.3) < 1e-9


def test_extra_overrides_signal_logic():
    # extra is the more-authoritative column (order_packages.confidence) and
    # wins where present.
    comps = extract("vwap", {"confidence": 0.1}, extra={"confidence": 0.9})
    assert abs(comps["confidence"].value - 0.9) < 1e-9


def test_purity_no_mutation_of_inputs():
    logic = _ict_scalp_logic()
    snapshot = dict(logic)
    extra = {"confidence": 0.5}
    extract("ict_scalp_5m", logic, extra=extra)
    assert logic == snapshot  # input not mutated
    assert extra == {"confidence": 0.5}


def test_categorical_filters_unknown_sentinels():
    comps = extract("vwap", {"regime": "unknown", "vol_regime": "none"})
    # "unknown"/"none" sentinels are filtered to absent (not a real category).
    assert "regime" not in comps
    assert "vol_regime" not in comps


def test_specs_for_returns_fresh_list():
    a = specs_for("ict_scalp_5m")
    b = specs_for("ict_scalp_5m")
    assert a is not b
    assert all(spec.kind in (KIND_GRADED, KIND_CATEGORICAL, KIND_GATE) for spec in a)


def test_component_is_frozen_record():
    comps = extract("vwap", _vwap_logic())
    c = comps["vwap_deviation_std"]
    assert isinstance(c, Component)
    # frozen dataclass — value/kind are read-only attributes
    assert hasattr(c, "value") and hasattr(c, "kind")
