"""Tests for scripts/check_harness_lever_coupling.py
(BL-20260730-HARNESS-LEVER-MAP-COUPLING-GUARD).

Fail-closed guard: every key on an enabled, harness-classified strategy must be
classified (PLAIN | LEVER_FLAG | _UNREPLAYABLE | UNMODELLED). The load test asserts
the LIVE config passes (so the UNMODELLED registry stays complete as strategies
evolve); the unit tests assert detection of a brand-new key, and the skip rules.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = os.path.join(_ROOT, "scripts", "check_harness_lever_coupling.py")
_spec = importlib.util.spec_from_file_location("check_harness_lever_coupling", _MOD)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def _trend(**extra):
    base = {"donchian": 20, "enabled": True, "symbols": ["BTCUSDT"], "timeframe": "1h"}
    base.update(extra)
    return base


def test_live_config_has_no_coupling_gap():
    # The whole point: the committed config must classify cleanly. If a new key is
    # added to a strategy, either this test's UNMODELLED registry or the debt
    # matrix's PLAIN/LEVER_FLAG map must grow with it.
    assert g.main([]) == 0


def test_brand_new_key_is_flagged():
    gaps = g.find_coupling_gaps({"x": _trend(brand_new_lever=3)})
    assert gaps == [("x", "trend", "brand_new_lever")]


def test_known_unmodelled_key_does_not_trip():
    # giveback_r is a real lever the TREND harness deliberately does not model.
    assert g.find_coupling_gaps({"y": _trend(giveback_r=1.5)}) == []


def test_lever_flag_key_does_not_trip():
    assert g.find_coupling_gaps({"y": _trend(stale_exit_bars=8)}) == []


def test_unreplayable_key_does_not_trip():
    assert g.find_coupling_gaps({"y": _trend(exit_head_model="m")}) == []


def test_disabled_strategy_is_skipped():
    assert g.find_coupling_gaps({"z": _trend(enabled=False, brand_new_lever=3)}) == []


def test_unclassifiable_family_is_skipped():
    # No donchian / pullback / squeeze markers -> classify() is None -> no lever
    # maps to check against -> out of scope (not a false pass, an explicit skip).
    scalp = {"enabled": True, "symbols": ["BTCUSDT"], "timeframe": "1h", "weird_key": 1}
    assert g.find_coupling_gaps({"scalp": scalp}) == []


def test_pullback_and_squeeze_families_resolve():
    pb = {"trend_lookback": 40, "pullback_frac": 0.5, "enabled": True,
          "symbols": ["BTCUSDT"], "timeframe": "2h", "novel_pb_key": 1}
    sq = {"bb_period": 20, "kc_mult": 1.0, "enabled": True,
          "symbols": ["BTCUSDT"], "timeframe": "4h", "novel_sq_key": 1}
    assert g.find_coupling_gaps({"p": pb}) == [("p", "pullback", "novel_pb_key")]
    assert g.find_coupling_gaps({"s": sq}) == [("s", "squeeze", "novel_sq_key")]
