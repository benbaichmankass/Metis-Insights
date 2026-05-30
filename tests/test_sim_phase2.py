"""Phase-2 SIM tests — models-in-the-loop.

Covers:
  * the leakage guard on the feature row (no outcome columns ever),
  * ModelScorer driving the LIVE advisory_downsize_factor (a stub predictor
    avoids needing real model files),
  * the engine recording with-model vs without-model R and the ledger's
    models_in_loop diff,
  * the reductive invariant: a model can only shrink R, never amplify it.
"""
from __future__ import annotations

import sys
import types

import pytest

from sim.models import ModelScorer, feature_row_for_trade, _assert_leakage_safe
from sim.ledger import SimLedger, SimTrade


# --------------------------------------------------------------------------
# Feature row + leakage guard
# --------------------------------------------------------------------------
class TestFeatureRow:
    def test_only_signal_time_columns(self):
        row = feature_row_for_trade(
            strategy="vwap", symbol="BTCUSDT", direction="long", confidence=0.8,
            meta={"setup_type": "fvg", "killzone": "ny", "bias": "bull"},
        )
        assert set(row) == {"strategy_name", "symbol", "direction",
                            "confidence", "setup_type", "killzone", "bias"}
        assert row["strategy_name"] == "vwap"
        assert row["confidence"] == 0.8

    def test_leakage_guard_rejects_outcome_columns(self):
        for bad in ("pnl", "r_multiple", "exit_price", "forward_log_return", "won"):
            with pytest.raises(AssertionError):
                _assert_leakage_safe({"strategy_name": "vwap", bad: 1.0})

    def test_leakage_guard_passes_clean_row(self):
        _assert_leakage_safe(feature_row_for_trade(
            strategy="vwap", symbol="BTCUSDT", direction="long", confidence=0.5))


# --------------------------------------------------------------------------
# ModelScorer — drives the LIVE advisory_downsize_factor via a stub predictor
# --------------------------------------------------------------------------
def _scorer_with_stub(score_value, *, size_floor=0.5, bearish_threshold=0.35,
                      quorum="majority", n_models=1):
    """Build a ModelScorer whose predictors are stubs returning score_value."""
    scorer = ModelScorer(
        model_ids=[f"stub-{i}" for i in range(n_models)],
        policy_cfg={"advisory_policy": {
            "mode": "downsize", "bearish_threshold": bearish_threshold,
            "size_floor": size_floor, "quorum": quorum,
        }},
    )

    class _Stub:
        def __init__(self, mid):
            self.model_id = mid

        def predict(self, row):
            return score_value

    # Inject stubs, bypassing the registry/model-file load.
    scorer._predictors = [_Stub(m) for m in scorer.model_ids]
    scorer._loaded = True
    return scorer


class TestModelScorer:
    def test_bearish_score_downsizes_to_floor(self):
        # score 0.1 < threshold 0.35 => bearish => factor == size_floor
        scorer = _scorer_with_stub(0.1, size_floor=0.5)
        row = feature_row_for_trade(strategy="vwap", symbol="BTCUSDT",
                                    direction="long", confidence=0.5)
        factor, scores = scorer.factor_for(row)
        assert factor == pytest.approx(0.5)
        assert scores == {"stub-0": 0.1}

    def test_bullish_score_no_downsize(self):
        scorer = _scorer_with_stub(0.9, size_floor=0.5)
        row = feature_row_for_trade(strategy="vwap", symbol="BTCUSDT",
                                    direction="long", confidence=0.5)
        factor, _ = scorer.factor_for(row)
        assert factor == pytest.approx(1.0)

    def test_quorum_majority_needs_more_than_half(self):
        # 3 models, all bearish => quorum (2) met => downsize.
        scorer = _scorer_with_stub(0.1, size_floor=0.5, n_models=3)
        row = feature_row_for_trade(strategy="vwap", symbol="BTCUSDT",
                                    direction="long", confidence=0.5)
        factor, scores = scorer.factor_for(row)
        assert len(scores) == 3
        assert factor == pytest.approx(0.5)

    def test_factor_never_amplifies(self):
        # Even with an absurd size_floor request, the live function caps at 1.0.
        scorer = _scorer_with_stub(0.9, size_floor=0.5)
        factor, _ = scorer.factor_for(
            feature_row_for_trade(strategy="vwap", symbol="BTCUSDT",
                                  direction="long", confidence=0.5))
        assert factor <= 1.0


# --------------------------------------------------------------------------
# Ledger — with-model vs without-model diff
# --------------------------------------------------------------------------
# ModelScorer with the REAL loader (catches ctor/signature drift the stub
# tests above can't — e.g. the ShadowPredictor `stage=` kwarg). Uses the
# constant-baseline trainer so it needs no pandas / real model artifacts.
# --------------------------------------------------------------------------
class TestModelScorerRealLoader:
    def _register_constant_model(self, tmp_path, model_id, constant, stage="shadow"):
        import json
        from ml.registry.model_registry import ModelRegistry

        state_path = tmp_path / f"{model_id}_state.json"
        state_path.write_text(json.dumps({
            "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
            "constant": constant,
        }))
        registry_root = tmp_path / "registry-store"
        registry = ModelRegistry(registry_root)
        manifest = {"manifest_version": "v1", "target_deployment_stage": "research_only"}
        registry.register(model_id=model_id, manifest=manifest,
                          model_state_path=str(state_path), metrics={"mae": 0.1},
                          code_revision="x")
        return registry_root

    def test_real_loader_loads_and_scores_shadow_stage_model(self, tmp_path):
        """Regression: ModelScorer must construct ShadowPredictor correctly
        (incl. the required stage= kwarg) and score via the real loader.
        A constant=0.1 model is bearish (< 0.35) => factor == size_floor."""
        from sim.models import ModelScorer, feature_row_for_trade

        registry_root = self._register_constant_model(tmp_path, "const-bear", 0.1)
        scorer = ModelScorer(
            model_ids=["const-bear"],
            policy_cfg={"advisory_policy": {"mode": "downsize", "bearish_threshold": 0.35,
                                            "size_floor": 0.5, "quorum": "majority"}},
            registry_root=str(registry_root),
        )
        row = feature_row_for_trade(strategy="vwap", symbol="BTCUSDT",
                                    direction="long", confidence=0.5)
        factor, scores = scorer.factor_for(row)
        # The model MUST have loaded (this is what the missing-stage bug broke).
        assert scores == {"const-bear": pytest.approx(0.1)}
        assert factor == pytest.approx(0.5)

    def test_real_loader_bullish_model_no_downsize(self, tmp_path):
        from sim.models import ModelScorer, feature_row_for_trade

        registry_root = self._register_constant_model(tmp_path, "const-bull", 0.9)
        scorer = ModelScorer(
            model_ids=["const-bull"],
            policy_cfg={"advisory_policy": {"mode": "downsize", "bearish_threshold": 0.35,
                                            "size_floor": 0.5, "quorum": "majority"}},
            registry_root=str(registry_root),
        )
        factor, scores = scorer.factor_for(feature_row_for_trade(
            strategy="vwap", symbol="BTCUSDT", direction="long", confidence=0.5))
        assert scores == {"const-bull": pytest.approx(0.9)}
        assert factor == pytest.approx(1.0)


class TestRegimeAwareScoring:
    """A regime predictor must be scored on a vol-enriched row (live
    regime_shadow path), and SKIPPED on a (symbol,timeframe) mismatch — not
    scored on a constant. This is what makes regime models testable in SIM."""

    def _scorer_with_regime_stub(self, *, spec):
        from sim.models import ModelScorer

        class _RegimeBase:
            regime_spec = spec

            def predict(self, row):
                # Echo the vol_bucket the enrichment injected, so the test can
                # assert the row was tailored (constant if enrichment didn't run).
                return 0.1 if row.get("vol_bucket") == "vol_b1" else 0.9

        class _Pred:
            model_id = "regime-stub"
            wrapped = _RegimeBase()

            def predict(self, row):
                return _RegimeBase().predict(row)

        scorer = ModelScorer(
            model_ids=["regime-stub"],
            policy_cfg={"advisory_policy": {"mode": "downsize", "bearish_threshold": 0.35,
                                            "size_floor": 0.5, "quorum": "majority"}},
        )
        scorer._predictors = [_Pred()]
        scorer._loaded = True
        return scorer

    def test_regime_model_scored_on_live_vol_bucket(self):
        from sim.models import feature_row_for_trade

        # Two buckets split at 0.01; a high-vol closes series lands in vol_b1.
        spec = {"symbol": "BTCUSDT", "timeframe": "5m", "vol_window_n": 5,
                "vol_bucket_labels": ["vol_b0", "vol_b1"], "vol_bucket_edges": [0.01],
                "feature_column": "vol_bucket", "vol_feature_column": "rolling_log_return_vol"}
        scorer = self._scorer_with_regime_stub(spec=spec)
        # Big swings => high rolling vol => bucket vol_b1 => bearish 0.1.
        closes = [100, 110, 95, 120, 90, 130]
        factor, scores = scorer.factor_for(
            feature_row_for_trade(strategy="vwap", symbol="BTCUSDT",
                                  direction="long", confidence=0.5),
            closes=closes, symbol="BTCUSDT", timeframe="5m")
        assert scores == {"regime-stub": pytest.approx(0.1)}  # vol-enriched, not constant
        assert factor == pytest.approx(0.5)

    def test_regime_model_skipped_on_timeframe_mismatch(self):
        from sim.models import feature_row_for_trade

        spec = {"symbol": "BTCUSDT", "timeframe": "5m", "vol_window_n": 5,
                "vol_bucket_labels": ["vol_b0", "vol_b1"], "vol_bucket_edges": [0.01]}
        scorer = self._scorer_with_regime_stub(spec=spec)
        # Decision is on 2h candles — regime model is 5m → must be skipped.
        factor, scores = scorer.factor_for(
            feature_row_for_trade(strategy="trend_donchian", symbol="BTCUSDT",
                                  direction="long", confidence=0.5),
            closes=[100, 110, 95, 120, 90, 130], symbol="BTCUSDT", timeframe="2h")
        assert scores == {}          # skipped, not scored on a constant
        assert factor == pytest.approx(1.0)


# --------------------------------------------------------------------------
class TestModelsInLoopDiff:
    def test_diff_reports_cut_losers_and_winners(self):
        lg = SimLedger()
        # A losing trade the model downsized (good: cut a loser).
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t0", 100, 90, 120,
                               exit_ts="t1", exit=90, exit_reason="sl", r_multiple=-1.0,
                               model_factor=0.5, r_multiple_model=-0.5,
                               model_scores={"m": 0.1}))
        # A winning trade the model downsized (bad: cut a winner).
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t2", 100, 90, 120,
                               exit_ts="t3", exit=120, exit_reason="tp", r_multiple=2.0,
                               model_factor=0.5, r_multiple_model=1.0,
                               model_scores={"m": 0.1}))
        # A trade the model left alone.
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t4", 100, 90, 120,
                               exit_ts="t5", exit=120, exit_reason="tp", r_multiple=2.0,
                               model_factor=1.0, r_multiple_model=2.0,
                               model_scores={"m": 0.9}))
        m = lg.summary()["models_in_loop"]
        assert m["scored_trades"] == 3
        assert m["downsized_trades"] == 2
        assert m["net_r_without_model"] == pytest.approx(3.0)   # -1 + 2 + 2
        assert m["net_r_with_model"] == pytest.approx(2.5)      # -0.5 + 1 + 2
        assert m["delta_r"] == pytest.approx(-0.5)
        assert m["downsize_cut_losers"] == 1
        assert m["downsize_cut_winners"] == 1

    def test_no_model_means_no_diff_section(self):
        lg = SimLedger()
        lg.open_trade(SimTrade("vwap", "BTCUSDT", "long", "t0", 100, 90, 120,
                               exit_ts="t1", exit=120, exit_reason="tp", r_multiple=2.0))
        assert "models_in_loop" not in lg.summary()


# --------------------------------------------------------------------------
# Engine integration — model scorer flows through run_replay
# --------------------------------------------------------------------------
def _make_stub_strategy_module(signal_dict):
    """signal_dict is a fixed order_package dict returned every bar."""
    mod = types.ModuleType("sim_stub_p2")

    def order_package(cfg, candles_df=None):
        return signal_dict

    mod.order_package = order_package
    return mod


@pytest.fixture
def patch_units(monkeypatch):
    import sim.engine as engine
    registered = []

    def register(name, module):
        modname = f"sim_stub_p2_{name}"
        sys.modules[modname] = module
        registered.append(modname)
        new_map = dict(engine.STRATEGY_UNITS)
        new_map[name] = modname
        monkeypatch.setattr(engine, "STRATEGY_UNITS", new_map)

    yield register
    for m in registered:
        sys.modules.pop(m, None)


def _candles(n, base=100.0):
    return [{"ts": f"2021-01-01T00:{i:02d}:00Z", "open": base, "high": base + 5,
             "low": base - 5, "close": base, "volume": 1.0} for i in range(n)]


class TestEngineWithModel:
    def test_model_factor_recorded_and_applied(self, patch_units):
        from sim.engine import run_replay

        # A long whose bar spans both sl and tp -> resolves (conservatively to
        # SL) immediately, so it closes and books R each bar.
        ts_long = {"symbol": "BTCUSDT", "direction": "long", "entry": 100,
                   "sl": 95, "tp": 104, "confidence": 0.9, "meta": {}}

        patch_units("turtle_soup", _make_stub_strategy_module(ts_long))
        scorer = _scorer_with_stub(0.1, size_floor=0.5)  # always bearish -> 0.5x

        ledger = run_replay(candles=_candles(60), strategies=["turtle_soup"],
                            warmup_bars=5, model_scorer=scorer)
        closed = [t for t in ledger.trades if not t.is_open()]
        assert closed, "expected at least one closed trade"
        t = closed[0]
        assert t.model_factor == pytest.approx(0.5)
        # with-model R must be exactly half the without-model R.
        assert t.r_multiple_model == pytest.approx(t.r_multiple * 0.5)
        m = ledger.summary()["models_in_loop"]
        assert m["net_r_with_model"] == pytest.approx(m["net_r_without_model"] * 0.5)
