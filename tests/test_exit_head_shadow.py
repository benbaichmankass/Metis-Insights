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


def _artifact(tmp_path, monkeypatch, tau=0.99, below_r=0.5, stage="shadow"):
    """Train a tiny real booster and stage it where the scorer looks.

    tau=0.99 makes nearly every score a would-exit (P is in [0,1])."""
    import numpy as np

    X = np.random.RandomState(0).rand(200, len(ehs_features()))
    y = (X[:, 1] > 0.5).astype(int)
    clf = lgb.LGBMClassifier(n_estimators=5, min_child_samples=5, verbose=-1)
    clf.fit(X, y)
    art = {"model_id": "exit-head-test-v0", "stage": stage, "tf": "1h",
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
         "timeframe": "1h", "strategy_label": "trend_donchian"},
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


def test_partial_bar_is_trimmed(tmp_path, monkeypatch):
    """The current forming bar must not be scored — training rows are closed
    bars (train/serve parity). A frame ending in a partial bar (timestamp =
    now, i.e. its 1h window hasn't elapsed) scores the PRIOR closed bar."""
    logs = _artifact(tmp_path, monkeypatch)
    df, entry_time = _frame()
    partial = {"timestamp": datetime.now(timezone.utc).isoformat(),
               "open": 200.0, "high": 260.0, "low": 190.0, "close": 250.0}
    df = pd.concat([df, pd.DataFrame([partial])], ignore_index=True)
    meta, pkg = _pkg(entry_time)
    ehs.maybe_score_exit_head(meta, pkg, df, "long")
    rec = json.loads(
        (logs / ehs.SHADOW_LOG_NAME).read_text().strip().splitlines()[-1])
    # with risk 2.0 the partial bar's close=250 would be ~74R; the closed
    # bars top out near 0R — a trimmed frame can't show the spike
    assert rec["feature_row"]["open_r"] < 5.0
    assert rec["feature_row"]["mfe_r"] < 5.0


def test_timeframe_mismatch_skips(tmp_path, monkeypatch):
    """An out-of-family monitor call (e.g. a 1d equities donchian variant)
    must not be scored by the 1h crypto head."""
    logs = _artifact(tmp_path, monkeypatch)
    df, entry_time = _frame()
    meta, pkg = _pkg(entry_time)
    meta["timeframe"] = "1d"
    ehs.maybe_score_exit_head(meta, pkg, df, "long")
    assert not (logs / ehs.SHADOW_LOG_NAME).exists()


def _monitor_pkg(entry_time, extra_meta=None):
    meta = {"entry_time": entry_time, "risk_per_unit": 2.0,
            "timeframe": "1h", "strategy_label": "trend_donchian",
            "atr": 0.5, "trail_mult": 50.0}  # huge trail → ratchet never fires
    meta.update(extra_meta or {})
    return {"entry": 101.5, "sl": 99.0, "direction": "long",
            "symbol": "BTCUSDT", "order_package_id": "pkg-e3-1",
            "tp": 999.0, "meta": meta}


def test_e3_apply_closes_when_advisory_and_declared(tmp_path, monkeypatch):
    """YAML-declared + advisory-stage + policy fires ⇒ a real close."""
    _artifact(tmp_path, monkeypatch, tau=1.01, stage="advisory")
    from src.units.strategies.trend_donchian import monitor

    df, entry_time = _frame()
    pkg = _monitor_pkg(entry_time)
    verdict = monitor({"exit_head_action": "close"}, df, pkg)
    assert verdict is not None
    assert verdict["action"] == "close"
    assert verdict["reason"] == "exit_head"


def test_e3_shadow_stage_never_closes(tmp_path, monkeypatch):
    """A shadow-stage artifact must stay observe-only even when the YAML
    declares the lever — the promotion gate is the artifact stage."""
    logs = _artifact(tmp_path, monkeypatch, tau=1.01, stage="shadow")
    from src.units.strategies.trend_donchian import monitor

    df, entry_time = _frame()
    pkg = _monitor_pkg(entry_time)
    verdict = monitor({"exit_head_action": "close"}, df, pkg)
    assert not (verdict and verdict.get("reason") == "exit_head")
    # ...but the shadow record still logged
    assert (logs / ehs.SHADOW_LOG_NAME).exists()


def test_e3_undeclared_never_closes(tmp_path, monkeypatch):
    """An advisory artifact without the YAML declare stays observe-only —
    the strategy leg must opt in explicitly."""
    _artifact(tmp_path, monkeypatch, tau=1.01, stage="advisory")
    from src.units.strategies.trend_donchian import monitor

    df, entry_time = _frame()
    pkg = _monitor_pkg(entry_time)
    verdict = monitor({}, df, pkg)
    assert not (verdict and verdict.get("reason") == "exit_head")


def test_e3_threshold_override_respected(tmp_path, monkeypatch):
    """A YAML exit_head_threshold of 0.0 can never fire (score >= 0)."""
    _artifact(tmp_path, monkeypatch, tau=1.01, stage="advisory")
    from src.units.strategies.trend_donchian import monitor

    df, entry_time = _frame()
    pkg = _monitor_pkg(entry_time)
    verdict = monitor({"exit_head_action": "close",
                       "exit_head_threshold": 0.0}, df, pkg)
    assert not (verdict and verdict.get("reason") == "exit_head")


def test_e3_proven_trade_never_touched(tmp_path, monkeypatch):
    """open_r >= below_r (a proven winner) is never closed by the head,
    regardless of score — the trail owns proven trades."""
    _artifact(tmp_path, monkeypatch, tau=1.01, below_r=-5.0, stage="advisory")
    from src.units.strategies.trend_donchian import monitor

    df, entry_time = _frame()
    pkg = _monitor_pkg(entry_time)
    verdict = monitor({"exit_head_action": "close"}, df, pkg)
    assert not (verdict and verdict.get("reason") == "exit_head")


def test_multi_artifact_scores_both_and_returns_advisory(tmp_path, monkeypatch):
    """M20 P4.2 — a second head rides the same channel: both artifacts score
    + log under their own model_id; the ADVISORY record is the one returned
    for the E3 apply path (shadow heads observe only, by stage)."""
    import numpy as np

    logs = _artifact(tmp_path, monkeypatch, tau=0.10, below_r=0.5,
                     stage="advisory")
    # second, shadow-stage peak head with a peak_full shape (fires HIGH)
    X = np.random.RandomState(1).rand(200, len(ehs_features()))
    y = (X[:, 0] > 0.5).astype(int)
    clf = lgb.LGBMClassifier(n_estimators=5, min_child_samples=5, verbose=-1)
    clf.fit(X, y)
    art = {"model_id": "exit-head-donchian-peak-1h-v1", "stage": "shadow",
           "tf": "1h", "features": ehs_features(), "target": "peak_is_in",
           "shape": {"policy": "peak_full", "tau": 0.0, "below_r": 0.5},
           "booster_txt": clf.booster_.model_to_string()}
    (logs / "trainer_mirror" / "exit_head" / "exit-head-donchian-peak-1h-v1.json"
     ).write_text(json.dumps(art))
    ehs._CACHE.clear()
    ehs._SEEN.clear()

    df, entry_time = _frame()
    meta = {"timeframe": "1h", "risk_per_unit": 2.0, "entry_time": entry_time}
    pkg = {"order_package_id": "pkg-multi", "symbol": "BTCUSDT",
           "entry": 101.5}
    rec = ehs.maybe_score_exit_head(meta, pkg, df, "long")
    assert rec is not None and rec["stage"] == "advisory"
    assert rec["model_id"] == "exit-head-test-v0"
    lines = [json.loads(x) for x in
             (logs / ehs.SHADOW_LOG_NAME).read_text().splitlines()]
    ids = {r["model_id"] for r in lines if r.get("event_source") == "exit_head"}
    assert ids == {"exit-head-test-v0", "exit-head-donchian-peak-1h-v1"}
    # peak_full with tau=0 fires on any score > 0 — would_exit True
    peak = [r for r in lines
            if r["model_id"] == "exit-head-donchian-peak-1h-v1"][-1]
    assert peak["would_exit"] is True
