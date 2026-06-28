"""ML vol-axis verdict — Design A, Phase 1 (shadow).

Covers ``src.runtime.regime.ml_vol_verdict``:
  * ``ml_vol_regime`` — P(volatile) → calm/volatile via the threshold, the
    >=τ boundary, and the fail-permissive ``unknown`` degeneracies.
  * The preferred per-bar publish cache path (read without an inline score).
  * The inline ``predict_proba`` fallback when the cache is cold.
  * Stage isolation: only ADVISORY heads are consulted (a shadow head injected
    into the discovery is never wired by the real discovery; the inline path is
    driven off the injected advisory spec table).
  * Prefer v2 over yz when both cover a (symbol, timeframe).

Hermetic: fake predictors + injected advisory spec table; no registry, no
network. ``candles_df`` is a duck-typed list-of-dict frame.
"""
from __future__ import annotations

import pytest

from ml.predictors.base import Predictor
from ml.predictors.shadow import ShadowPredictor
from src.runtime.regime import ml_vol_verdict as mvv
from src.runtime.regime.ml_vol_verdict import (
    VOL_CALM,
    VOL_UNKNOWN,
    VOL_VOLATILE,
    clear_ml_vol_cache,
    ml_vol_regime,
    publish_p_volatile,
)


# --- fakes ----------------------------------------------------------------

class _RegimeProbaBase(Predictor):
    """A regime predictor that returns a fixed P(volatile)."""

    def __init__(self, regime_spec, p_volatile):
        self.regime_spec = regime_spec
        self._p = p_volatile
        self.proba_calls = 0

    def predict(self, row):
        return float(self._p)

    def predict_proba(self, row):
        self.proba_calls += 1
        return {"range": 1.0 - self._p, "volatile": self._p}


class _RaisingProbaBase(Predictor):
    """A regime predictor whose predict_proba blows up (fail-permissive test)."""

    def __init__(self, regime_spec):
        self.regime_spec = regime_spec

    def predict(self, row):
        return 0.0

    def predict_proba(self, row):
        raise RuntimeError("boom")


def _spec(symbol="BTCUSDT", timeframe="1h", vol_col="rolling_log_return_vol"):
    return {
        "feature_column": "vol_bucket",
        "vol_feature_column": vol_col,
        "vol_window_n": 5,
        "vol_bucket_edges": [0.001, 0.002],
        "vol_bucket_labels": ["vol_b0", "vol_b1", "vol_b2"],
        "symbol": symbol,
        "timeframe": timeframe,
    }


def _advisory_predictor(model_id, p_volatile, spec=None, stage="advisory"):
    base = _RegimeProbaBase(spec or _spec(), p_volatile)
    return ShadowPredictor(base, model_id=model_id, stage=stage, log_path=None)


def _entry(predictor, spec=None, is_yz=False):
    spec = spec or _spec()
    return {
        "symbol": spec["symbol"],
        "timeframe": spec["timeframe"],
        "model_id": predictor.model_id,
        "predictor": predictor,
        "is_yz": is_yz,
    }


class _FrameLike:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, key):
        return [r[key] for r in self._rows]


def _candles(n=30, start=100.0, step=1.001, last_ts=1000):
    rows = []
    price = start
    for i in range(n):
        rows.append({
            "timestamp": last_ts - (n - 1 - i),
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price,
            "volume": 1.0,
        })
        price *= step
    return _FrameLike(rows)


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    clear_ml_vol_cache()
    monkeypatch.delenv("ML_VOL_VERDICT_THRESHOLD", raising=False)
    yield
    clear_ml_vol_cache()


# --- happy path (inline fallback) -----------------------------------------

def test_high_p_volatile_yields_volatile():
    pred = _advisory_predictor("btc-regime-1h-v2", 0.8)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_VOLATILE
    assert out["p_volatile"] == pytest.approx(0.8)
    assert out["model_id"] == "btc-regime-1h-v2"
    assert out["source"] == "ml-advisory:btc-regime-1h-v2"


def test_low_p_volatile_yields_calm():
    pred = _advisory_predictor("btc-regime-1h-v2", 0.2)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_CALM
    assert out["p_volatile"] == pytest.approx(0.2)


def test_threshold_boundary_is_inclusive():
    """P(volatile) == τ → volatile (>= is the boundary)."""
    pred = _advisory_predictor("btc-regime-1h-v2", 0.5)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_VOLATILE  # 0.5 >= 0.5


def test_custom_threshold_from_env(monkeypatch):
    monkeypatch.setenv("ML_VOL_VERDICT_THRESHOLD", "0.7")
    pred = _advisory_predictor("btc-regime-1h-v2", 0.6)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_CALM  # 0.6 < 0.7


# --- fail-permissive -------------------------------------------------------

def test_no_advisory_head_is_unknown():
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs={})
    assert out["vol_regime"] == VOL_UNKNOWN
    assert out["p_volatile"] is None
    assert out["source"] == "unavailable"
    assert out["model_id"] is None


def test_missing_symbol_or_timeframe_is_unknown():
    pred = _advisory_predictor("btc-regime-1h-v2", 0.9)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    assert ml_vol_regime(None, "1h", _candles(), specs=table)["vol_regime"] == VOL_UNKNOWN
    assert ml_vol_regime("BTCUSDT", None, _candles(), specs=table)["vol_regime"] == VOL_UNKNOWN


def test_predict_proba_raises_is_unknown():
    pred = ShadowPredictor(
        _RaisingProbaBase(_spec()), model_id="m", stage="advisory", log_path=None,
    )
    table = {("BTCUSDT", "1H"): _entry(pred)}
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_UNKNOWN


def test_candles_none_and_cold_cache_is_unknown():
    """No published score AND no candles → no P(volatile) → unknown."""
    pred = _advisory_predictor("btc-regime-1h-v2", 0.9)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    out = ml_vol_regime("BTCUSDT", "1h", candles_df=None, specs=table)
    assert out["vol_regime"] == VOL_UNKNOWN


def test_discovery_registry_failure_is_unknown(monkeypatch):
    """When the real discovery raises, ml_vol_regime degrades to unknown."""
    def _boom(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(mvv, "discover_advisory_stage_regime_specs", _boom)
    out = ml_vol_regime("BTCUSDT", "1h", _candles())
    assert out["vol_regime"] == VOL_UNKNOWN


# --- per-bar publish cache (preferred path) --------------------------------

def test_published_score_read_without_inline_score():
    """A published P(volatile) is used directly — predict_proba is NOT called."""
    pred = _advisory_predictor("btc-regime-1h-v2", 0.1)  # inline would say calm
    table = {("BTCUSDT", "1H"): _entry(pred)}
    publish_p_volatile("btc-regime-1h-v2", bar_ts=42, p_volatile=0.95)
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_VOLATILE  # from the cache (0.95), not 0.1
    assert out["p_volatile"] == pytest.approx(0.95)
    assert pred.wrapped.proba_calls == 0  # inline scorer untouched


def test_published_none_falls_back_to_inline():
    pred = _advisory_predictor("btc-regime-1h-v2", 0.8)
    table = {("BTCUSDT", "1H"): _entry(pred)}
    publish_p_volatile("btc-regime-1h-v2", bar_ts=42, p_volatile=None)
    out = ml_vol_regime("BTCUSDT", "1h", _candles(), specs=table)
    assert out["vol_regime"] == VOL_VOLATILE
    assert pred.wrapped.proba_calls == 1  # fell back to inline


# --- stage isolation + prefer-v2 (discovery-level) -------------------------

def test_discovery_skips_shadow_stage(monkeypatch):
    """Only advisory heads land in the discovered spec table — a shadow head
    in the registry is excluded (its stage != advisory)."""
    adv_pred = _advisory_predictor("adv-head", 0.9, stage="advisory")

    class _FakeEntry:
        def __init__(self, model_id, stage):
            self.model_id = model_id
            self.target_deployment_stage = stage

    class _FakeRegistry:
        def __init__(self, *a, **k):
            pass

        def list(self):
            return [
                _FakeEntry("shadow-head", "shadow"),
                _FakeEntry("adv-head", "advisory"),
            ]

    monkeypatch.setattr(mvv, "_ADVISORY_SPEC_CACHE", None)
    monkeypatch.setattr(
        "ml.registry.model_registry.ModelRegistry", _FakeRegistry,
    )

    def _fake_resolve(ids, registry, *, log_path=None):
        # Only the advisory id should be requested.
        assert ids == ["adv-head"], ids
        return [adv_pred]

    monkeypatch.setattr("ml.shadow.factory.resolve_predictors", _fake_resolve)
    table = mvv.discover_advisory_stage_regime_specs(force=True)
    assert ("BTCUSDT", "1H") in table
    assert table[("BTCUSDT", "1H")]["model_id"] == "adv-head"
    # The shadow head is absent regardless of stage label.
    assert all(v["model_id"] != "shadow-head" for v in table.values())


def test_prefer_v2_over_yz_in_discovery(monkeypatch):
    """When a v2 (non-yz) and a yz head both cover (symbol, timeframe), keep v2."""
    yz_spec = _spec(vol_col="yang_zhang_vol")
    v2_spec = _spec(vol_col="rolling_log_return_vol")
    yz_pred = _advisory_predictor("btc-regime-1h-yz", 0.9, spec=yz_spec)
    v2_pred = _advisory_predictor("btc-regime-1h-v2", 0.1, spec=v2_spec)

    class _FakeEntry:
        def __init__(self, model_id):
            self.model_id = model_id
            self.target_deployment_stage = "advisory"

    class _FakeRegistry:
        def __init__(self, *a, **k):
            pass

        def list(self):
            return [_FakeEntry("btc-regime-1h-yz"), _FakeEntry("btc-regime-1h-v2")]

    monkeypatch.setattr(mvv, "_ADVISORY_SPEC_CACHE", None)
    monkeypatch.setattr("ml.registry.model_registry.ModelRegistry", _FakeRegistry)
    # Return yz FIRST so a naive "first wins" would pick the wrong head.
    monkeypatch.setattr(
        "ml.shadow.factory.resolve_predictors",
        lambda ids, registry, *, log_path=None: [yz_pred, v2_pred],
    )
    table = mvv.discover_advisory_stage_regime_specs(force=True)
    entry = table[("BTCUSDT", "1H")]
    assert entry["model_id"] == "btc-regime-1h-v2"
    assert entry["is_yz"] is False
