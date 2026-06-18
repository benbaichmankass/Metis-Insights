"""Tests for scripts/ops/recombination_sweep.py — the recombination orchestrator.

Pure (no subprocess / no network): exercises only the enumeration + coherence
+ label functions. Asserts:
  (a) the coherence mask drops the documented incoherent tuples
      (pullback never gets the `selective` selectivity variant),
  (b) the label format is stable, and
  (c) a known pool yields the expected coherent tuple count.

Tier-1 research tooling — see
docs/research/strategy-primitives-recombination-DESIGN.md.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "recombination_sweep.py"


def _load_module():
    import sys

    spec = importlib.util.spec_from_file_location("recombination_sweep", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the module-level @dataclass can resolve
    # sys.modules[cls.__module__] (dataclasses requires this).
    sys.modules["recombination_sweep"] = mod
    spec.loader.exec_module(mod)
    return mod


# A small, fully-known pool exercising both the trend (selective allowed) and
# pullback (selective masked) coherence rules.
_POOL = {
    "schema_version": 1,
    "entries": {
        "trend_donchian": {"harness": "backtest_trend.py", "family": "trend"},
        "htf_pullback": {"harness": "backtest_pullback.py", "family": "pullback"},
    },
    "axes": {
        "symbol": ["ETHUSDT", "SOLUSDT"],
        "timeframe": {"trend": ["4h"], "pullback": ["2h"]},
        "regime_filter": [
            {"name": "none"},
            {"name": "trend_only", "adx_min": 20},
            {"name": "strong_trend_only", "adx_min": 25},
        ],
        "exit_trail": [
            {"name": "baseline", "trail_mult": 5.0},
            {"name": "tight", "trail_mult": 3.0},
        ],
        "selectivity": [
            {"name": "base", "min_confidence": 0.0},
            {"name": "selective", "min_confidence": 0.6},
        ],
    },
    "coherence": {
        "selectivity_by_family": {
            "trend": ["base", "selective"],
            "pullback": ["base"],
        },
    },
}


def test_tuple_count_matches_coherent_product():
    """(c) Known pool → expected coherent tuple count.

    trend:    2 sym × 1 tf × 3 regime × 2 exit × 2 sel = 24
    pullback: 2 sym × 1 tf × 3 regime × 2 exit × 1 sel = 12   (selective masked)
    total = 36
    """
    mod = _load_module()
    tuples = mod.enumerate_tuples(_POOL)
    trend = [t for t in tuples if t.family == "trend"]
    pull = [t for t in tuples if t.family == "pullback"]
    assert len(trend) == 24
    assert len(pull) == 12
    assert len(tuples) == 36


def test_coherence_drops_selective_for_pullback():
    """(a) The coherence mask drops the documented incoherent tuples.

    pullback must NEVER carry the `selective` selectivity variant (its live
    confidence is structural 0.0); trend MUST include it.
    """
    mod = _load_module()
    tuples = mod.enumerate_tuples(_POOL)
    pull_sel = {t.selectivity["name"] for t in tuples if t.family == "pullback"}
    trend_sel = {t.selectivity["name"] for t in tuples if t.family == "trend"}
    assert pull_sel == {"base"}
    assert "selective" in trend_sel
    # And no pullback label ever encodes the non-zero confidence floor.
    assert all("conf0.6" not in t.label for t in tuples if t.family == "pullback")


def test_label_format_is_stable():
    """(b) Label format is deterministic and matches the documented template."""
    mod = _load_module()
    tuples = {t.label: t for t in mod.enumerate_tuples(_POOL)}
    # Behaviour-preserving baseline cell.
    assert "trend_ETHUSDT_4h_adxnone_trail5_conf0" in tuples
    # ADX-min flagship cell + tight trail + selective floor.
    assert "trend_SOLUSDT_4h_adxmin25_trail3_conf0.6" in tuples
    # Pullback baseline.
    assert "pullback_ETHUSDT_2h_adxnone_trail5_conf0" in tuples
    # Labels are unique (stable, collision-free).
    labels = [t.label for t in mod.enumerate_tuples(_POOL)]
    assert len(labels) == len(set(labels))


def test_build_label_tokens():
    """The label builder emits the exact regime/trail/conf tokens."""
    mod = _load_module()
    label = mod._build_label(
        "trend_donchian", "trend", "ETHUSDT", "4h",
        {"name": "trend_only", "adx_min": 20},
        {"name": "baseline", "trail_mult": 5.0},
        {"name": "base", "min_confidence": 0.0},
    )
    assert label == "trend_ETHUSDT_4h_adxmin20_trail5_conf0"
