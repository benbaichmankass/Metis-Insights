"""Phase-4 SIM tests — multi-variation sweep.

Uses stub strategies so the sweep runs without real candles/models, and
asserts the ranking + output format (the SUMMARY.md / all_metrics.json the
dashboard sweep surface reads).
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from sim.sweep import run_sweep, render_summary_md, to_all_metrics, write_sweep


def _make_stub(signal_dict):
    mod = types.ModuleType("sim_stub_p4")

    def order_package(cfg, candles_df=None):
        return signal_dict

    mod.order_package = order_package
    return mod


@pytest.fixture
def patch_units(monkeypatch):
    import sim.engine as engine
    registered = []

    def register(name, module):
        modname = f"sim_stub_p4_{name}"
        sys.modules[modname] = module
        registered.append(modname)
        new_map = dict(engine.STRATEGY_UNITS)
        new_map[name] = modname
        monkeypatch.setattr(engine, "STRATEGY_UNITS", new_map)

    yield register
    for m in registered:
        sys.modules.pop(m, None)


def _candles(n):
    # Bars that always reach TP for a long (high 110 >= tp), so trades win.
    return [{"ts": f"2021-01-01T00:{i:02d}:00Z", "open": 100, "high": 110,
             "low": 99, "close": 100, "volume": 1.0} for i in range(n)]


class TestRunSweep:
    def test_ranks_variants_by_net_r(self, patch_units):
        # Two strategies: a winner (long hits tp) and a loser (long hits sl).
        winner = {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                  "sl": 95, "tp": 108, "confidence": 0.9, "meta": {}}
        loser = {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                 "sl": 99.5, "tp": 100000, "confidence": 0.9, "meta": {}}  # tp unreachable, sl 99.5 -> low 99 hits
        patch_units("turtle_soup", _make_stub(winner))
        patch_units("vwap", _make_stub(loser))

        variants = [
            {"name": "winner_only", "strategies": ["turtle_soup"]},
            {"name": "loser_only", "strategies": ["vwap"]},
        ]
        results = run_sweep(variants=variants, candles=_candles(40), warmup_bars=5)
        assert [r["name"] for r in results][0] == "winner_only"  # best first
        assert results[0]["headline"]["net_r"] > results[1]["headline"]["net_r"]

    def test_variant_without_strategies_raises(self):
        with pytest.raises(ValueError):
            run_sweep(variants=[{"name": "bad", "strategies": []}],
                      candles=_candles(40), warmup_bars=5)

    def test_headline_fields_present(self, patch_units):
        sig = {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
               "sl": 95, "tp": 108, "confidence": 0.9, "meta": {}}
        patch_units("turtle_soup", _make_stub(sig))
        results = run_sweep(variants=[{"name": "v", "strategies": ["turtle_soup"]}],
                            candles=_candles(40), warmup_bars=5)
        h = results[0]["headline"]
        for key in ("strategies", "models", "closed_trades", "win_rate",
                    "net_r", "expectancy_r", "max_drawdown_r"):
            assert key in h


class TestSweepOutput:
    def _fake_results(self):
        return [
            {"name": "b", "headline": {"strategies": ["vwap"], "models": [],
             "closed_trades": 10, "win_rate": 0.6, "net_r": 5.0,
             "net_r_no_model": 5.0, "expectancy_r": 0.5, "max_drawdown_r": 2.0},
             "summary": {"decision_attrition": {}}},
            {"name": "a", "headline": {"strategies": ["vwap"], "models": ["m"],
             "closed_trades": 8, "win_rate": 0.5, "net_r": 3.0,
             "net_r_no_model": 4.0, "expectancy_r": 0.375, "max_drawdown_r": 3.0},
             "summary": {"decision_attrition": {
                 "m": {"readiness": "insufficient funnel volume (8 < 30) for promotion confidence",
                       "funnel_scored": 8, "eval_n": 1000, "attrition_ratio": 0.008,
                       "bearish": 4, "influenced": 2, "bearish_net_r": -1.0}}}},
        ]

    def test_summary_md_has_leaderboard_and_flags(self):
        md = render_summary_md(self._fake_results(), span=["t0", "t1"], symbol="BTCUSDT")
        assert "SIM variation sweep" in md
        assert "| 1 | b |" in md and "| 2 | a |" in md
        assert "Attrition flags" in md
        assert "insufficient funnel volume" in md

    def test_all_metrics_shape(self):
        m = to_all_metrics(self._fake_results())
        assert set(m) == {"headline", "extra", "generated_at"}
        assert m["headline"]["net_r"] == 5.0
        assert len(m["extra"]["variants"]) == 2

    def test_write_sweep_files(self, tmp_path):
        write_sweep(self._fake_results(), out_dir=tmp_path / "s1",
                    span=["t0", "t1"], symbol="BTCUSDT")
        assert (tmp_path / "s1" / "SUMMARY.md").exists()
        am = json.loads((tmp_path / "s1" / "all_metrics.json").read_text())
        assert am["headline"]["net_r"] == 5.0
        assert (tmp_path / "s1" / "variants.json").exists()
