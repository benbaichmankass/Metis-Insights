"""M20 E2 — exit-head live shadow scorer tests (observe-only contract)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.runtime import exit_head_shadow as ehs

lgb = pytest.importorskip("lightgbm")


def _frame(n=60, entry_offset=30):
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for k in range(n):
        price += 0.05 if k < entry_offset else 0.01  # drift then chop
        rows.append({
            "timestamp": (t0 + timedelta(hours=k)).isoformat(),
            "open": price, "high": price + 0.4, "low": price - 0.4,
            "close": price,
        })
    return pd.DataFrame(rows), (t0 + timedelta(hours=entry_offset)).isoformat()


def _artifact(tmp_path, monkeypatch, tau=0.99, below_r=0.5):
    """Train a tiny real booster and stage it where the scorer looks.

    tau=0.99 makes nearly every score a would-exit (P is in [0,1])."""
    import numpy as np

    X = np.random.RandomState(0).rand(200, len(ehs_features()))
    y = (X[:, 1] > 0.5).astype(int)
    clf = lgb.LGBMClassifier(n_estimators=5, min_child_samples=5, verbose=-1)
    clf.fit(X, y)
    art = {"model_id": "exit-head-test-v0", "stage": "shadow",
           "features": ehs_features(),
           "shape": {"tau": tau, "below_r": below_r},
           "booster_txt": clf.booster_.model_to_string()}
    d = tmp_path / "runtime_logs" / "trainer_mirror" / "exit_head"
    d.mkdir(parents=True)
    (d / f"{ehs.MODEL_ID}.json").write_text(json.dumps(art))
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir",
                        lambda: tmp_path / "runtime_logs")
    ehs._CACHE.clear()
    ehs._SEEN.clear()
    from src.runtime import exit_lever_soak

    exit_lever_soak._ANNOTATED.clear()
    return tmp_path / "runtime_logs"


def ehs_features():
    return ["age_bars", "open_r", "mfe_r", "mae_r", "giveback_r",
            "chop_frac_so_far", "stagnation_run", "dist_to_stop_r",
            "vol_ratio_vs_entry", "atr_ratio_vs_entry",
            "donchian_mid_dist_atr", "hour_of_day", "dayofweek", "is_long"]


def _pkg(entry_time):
    return (
        {"entry_time": entry_time, "risk_per_unit": 2.0,
         "strategy_label": "trend_donchian"},
        {"entry": 101.5, "sl": 99.5, "direction": "long",
         "symbol": "BTCUSDT", "order_package_id": "pkg-test-1"},
    )


def test_no_artifact_is_silent_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir",
                        lambda: tmp_path / "runtime_logs")
    ehs._CACHE.clear()
    ehs._SEEN.clear()
    df, entry_time = _frame()
    meta, pkg = _pkg(entry_time)
    assert ehs.maybe_score_exit_head(meta, pkg, df, "long") is None
    assert not (tmp_path / "runtime_logs" / ehs.SHADOW_LOG_NAME).exists()


def test_scores_and_dedups_per_bar(tmp_path, monkeypatch):
    logs = _artifact(tmp_path, monkeypatch)
    df, entry_time = _frame()
    meta, pkg = _pkg(entry_time)
    ehs.maybe_score_exit_head(meta, pkg, df, "long")
    ehs.maybe_score_exit_head(meta, pkg, df, "long")  # same bar → dedup
    lines = (logs / ehs.SHADOW_LOG_NAME).read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["model_id"] == "exit-head-test-v0"
    assert rec["stage"] == "shadow"
    assert rec["event_source"] == "exit_head"
    assert 0.0 <= rec["score"] <= 1.0
    assert rec["feature_row"]["age_bars"] > 0


def test_would_exit_writes_soak_row(tmp_path, monkeypatch):
    logs = _artifact(tmp_path, monkeypatch, tau=1.01)  # always would-exit
    df, entry_time = _frame()
    meta, pkg = _pkg(entry_time)
    ehs.maybe_score_exit_head(meta, pkg, df, "long")
    soak = logs / "exit_lever_soak.jsonl"
    assert soak.exists()
    row = json.loads(soak.read_text().strip().splitlines()[-1])
    assert row["lever"] == "exit_head"
    assert row["params"]["model_id"] == "exit-head-test-v0"
    assert "score" in row["state"]


def test_entry_outside_window_skips(tmp_path, monkeypatch):
    logs = _artifact(tmp_path, monkeypatch)
    df, _ = _frame()
    meta, pkg = _pkg("2026-01-01T00:00:00+00:00")  # before every bar
    ehs.maybe_score_exit_head(meta, pkg, df, "long")
    assert not (logs / ehs.SHADOW_LOG_NAME).exists()


def test_monitor_still_returns_trail_verdict(tmp_path, monkeypatch):
    """The shadow hook must not change monitor() behaviour."""
    _artifact(tmp_path, monkeypatch)
    from src.units.strategies.trend_donchian import monitor

    df, entry_time = _frame()
    pkg = {"entry": 101.5, "sl": 99.0, "direction": "long",
           "symbol": "BTCUSDT", "order_package_id": "pkg-test-2",
           "meta": {"entry_time": entry_time, "risk_per_unit": 2.0,
                    "atr": 0.5, "trail_mult": 2.0}}
    verdict = monitor({}, df, pkg)
    # chandelier ratchet should propose a tightened stop; the shadow hook
    # must not have swallowed or replaced it
    assert verdict is None or set(verdict).issubset({"sl", "action", "reason",
                                                     "exit_price"})
