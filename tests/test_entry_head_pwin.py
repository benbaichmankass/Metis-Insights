"""M18 Phase A — P_win entry-head live annotate contract.

* No artifact -> silent no-op (None), signal builders stamp nothing.
* A staged artifact -> a p_win in [0,1] + a shadow_predictions record
  (event_source "entry_head"); family/tf/symbol mismatches skip.
* Live/train parity: the scorer's SIGNAL-BAR feature block equals the E0
  builder's ``entry_*`` block for a trade opened at the same bar.
* The allocator-soak candidate brief carries head_p_win from the
  SignalPackage raw meta.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

lgb = pytest.importorskip("lightgbm")

from src.runtime import entry_head_pwin as ehp  # noqa: E402

FEATURES = ["entry_mom_8", "entry_dc_dist_atr", "entry_hour",
            "entry_dayofweek", "is_long", "entry_confidence"]


def _frame(n=60):
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for k in range(n):
        price *= 1.004
        rows.append({"timestamp": (t0 + timedelta(hours=k)).isoformat(),
                     "open": price, "high": price * 1.003,
                     "low": price * 0.997, "close": price, "volume": 1.0})
    return pd.DataFrame(rows)


def _artifact(tmp_path, monkeypatch, family="donchian", tf="1h",
              symbols=None, stage="shadow"):
    import numpy as np

    X = np.random.RandomState(1).rand(200, len(FEATURES))
    y = (X[:, 0] > 0.5).astype(int)
    clf = lgb.LGBMClassifier(n_estimators=5, min_child_samples=5, verbose=-1)
    clf.fit(X, y)
    art = {"model_id": "entry-pwin-test-v0", "kind": "entry_pwin",
           "family": family, "tf": tf, "stage": stage,
           "symbols": symbols, "features": FEATURES,
           "booster_txt": clf.booster_.model_to_string()}
    d = tmp_path / "runtime_logs" / "trainer_mirror" / "entry_head"
    d.mkdir(parents=True)
    (d / "entry-pwin-test-v0.json").write_text(json.dumps(art))
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir",
                        lambda: tmp_path / "runtime_logs")
    ehp._CACHE.clear()
    return tmp_path / "runtime_logs"


def _score(df, **kw):
    kwargs = dict(family="donchian", symbol="BTCUSDT", timeframe="1h",
                  direction="long", confidence=0.42, candles_df=df,
                  strategy="trend_donchian")
    kwargs.update(kw)
    return ehp.maybe_score_entry_pwin(**kwargs)


def test_no_artifact_is_silent_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir",
                        lambda: tmp_path / "runtime_logs")
    ehp._CACHE.clear()
    assert _score(_frame()) is None


def test_scores_and_logs_shadow_record(tmp_path, monkeypatch):
    logs = _artifact(tmp_path, monkeypatch)
    out = _score(_frame())
    assert out is not None and 0.0 <= out["p_win"] <= 1.0
    assert out["model_id"] == "entry-pwin-test-v0"
    recs = [json.loads(x) for x in
            (logs / "shadow_predictions.jsonl").read_text().splitlines()]
    assert recs and recs[-1]["event_source"] == "entry_head"
    assert recs[-1]["feature_row"]["entry_confidence"] == 0.42


def test_family_tf_symbol_mismatch_skips(tmp_path, monkeypatch):
    _artifact(tmp_path, monkeypatch, family="donchian", tf="1h",
              symbols=["ETHUSDT"])
    assert _score(_frame(), family="pullback") is None
    assert _score(_frame(), timeframe="4h") is None
    assert _score(_frame(), symbol="BTCUSDT") is None  # not in symbols
    assert _score(_frame(), symbol="ETHUSDT") is not None


def test_live_features_match_builder_entry_block():
    spec = importlib.util.spec_from_file_location(
        "b", Path(__file__).resolve().parents[1]
        / "scripts" / "ml" / "build_exit_head_dataset.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)

    df = _frame(60)
    k0 = 40  # decision bar
    candles = [{"t": pd.Timestamp(r["timestamp"]).timestamp(),
                "high": float(r["high"]), "low": float(r["low"]),
                "close": float(r["close"]), "volume": 1.0}
               for r in df.to_dict("records")]
    tr = {"source": "harness", "strategy": "trend_donchian",
          "symbol": "BTCUSDT", "direction": "long",
          "t_open": candles[k0]["t"], "t_close": candles[55]["t"],
          "entry": candles[k0]["close"],
          "sl": candles[k0]["close"] * 0.98, "final_r": 1.0,
          "final_r_source": "harness_net_r", "exit_reason": "trail_stop",
          "confidence": 0.42}
    ts = [c["t"] for c in candles]
    rows = b.rows_for_trade(tr, candles, ts, b.atr_series(candles))
    assert rows
    built = {f: rows[0][f] for f in
             ("entry_mom_8", "entry_dc_dist_atr", "entry_hour",
              "entry_dayofweek", "entry_confidence")}

    live = ehp._signal_bar_features(df.iloc[:k0 + 1], "long", 0.42)
    assert live is not None
    assert live["entry_hour"] == built["entry_hour"]
    assert live["entry_dayofweek"] == built["entry_dayofweek"]
    assert live["entry_confidence"] == built["entry_confidence"]
    assert live["entry_mom_8"] == pytest.approx(built["entry_mom_8"], abs=1e-6)
    assert live["entry_dc_dist_atr"] == pytest.approx(
        built["entry_dc_dist_atr"], abs=1e-3)


def test_allocator_brief_carries_head_p_win():
    from src.runtime.allocator_soak import _brief

    class C:
        strategy_id = "trend_donchian"
        symbol = "BTCUSDT"
        side = "buy"
        entry_price = 100.0
        stop_loss = 98.0
        take_profit = 104.0
        source_context = {"confidence": 0.42, "priority": 1}
        raw = {"head_p_win": 0.61, "head_p_win_model": "entry-pwin-test-v0"}

    row = _brief(C(), 0.5)
    assert row["head_p_win"] == 0.61
    assert row["head_p_win_model"] == "entry-pwin-test-v0"
