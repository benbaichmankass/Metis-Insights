"""Tests for src.runtime.conviction_inputs — the model-score -> conviction-input
adapter (design § 3 / § 4a)."""

from __future__ import annotations

import json

import pytest

import src.runtime.conviction_inputs as ci
from ml.calibration.calibrators import PlattCalibrator
from src.runtime.conviction_inputs import (
    INFLUENCE_STAGES,
    build_conviction_inputs,
    classify_head,
    load_calibrators,
)


def test_classify_head_slots():
    assert classify_head("trade-outcome-winrate-baseline-v0") == "c_wr"
    assert classify_head("trade-outcome-global-baseline-v0") == "c_wr"
    assert classify_head("setup-quality-baseline-v0") == "c_setup"
    assert classify_head("setup-quality-audit-baseline-v0") == "c_setup"
    assert classify_head("btc-regime-1h-lgbm-yz-v1") == "c_reg"
    # sizing-lens / non-conviction heads
    assert classify_head("execution-quality-baseline-v0") is None
    assert classify_head("prop-mission-policy-baseline-v0") is None
    assert classify_head("something-else") is None


def test_cstrat_from_raw_when_no_calibrator():
    inputs, prov = build_conviction_inputs("trend_donchian", 0.62, None)
    assert inputs == {"c_strat": pytest.approx(0.62)}
    assert prov["c_strat"]["calibrated"] is False


def test_cstrat_uses_strategy_calibrator():
    cal = {"trend_donchian": PlattCalibrator(a=0.0, b=0.0)}  # sigmoid(0)=0.5
    inputs, prov = build_conviction_inputs("trend_donchian", 0.9, None, calibrators=cal)
    assert inputs["c_strat"] == pytest.approx(0.5)
    assert prov["c_strat"]["calibrated"] is True


def test_head_default_normalization():
    scores = {
        "trade-outcome-winrate-baseline-v0": {"stage": "shadow", "score": 0.8},
        "setup-quality-baseline-v0": {"stage": "shadow", "score": 0.0},  # R-mult 0 -> 0.5
    }
    inputs, _ = build_conviction_inputs("ict_scalp_5m", 0.4, scores)
    assert inputs["c_wr"] == pytest.approx(0.8)
    assert inputs["c_setup"] == pytest.approx(0.5)  # (0+3)/6
    assert inputs["c_strat"] == pytest.approx(0.4)


def test_setup_quality_rmultiple_mapping():
    scores = {"setup-quality-baseline-v0": {"stage": "shadow", "score": 3.0}}
    inputs, _ = build_conviction_inputs("x", None, scores)
    assert inputs["c_setup"] == pytest.approx(1.0)  # +3 R -> top
    scores2 = {"setup-quality-baseline-v0": {"stage": "shadow", "score": -3.0}}
    inputs2, _ = build_conviction_inputs("x", None, scores2)
    assert inputs2["c_setup"] == pytest.approx(0.0)


def test_regime_skipped_without_calibrator():
    scores = {"btc-regime-1h-lgbm-yz-v1": {"stage": "advisory", "score": 0.98}}
    inputs, _ = build_conviction_inputs("x", 0.5, scores)
    assert "c_reg" not in inputs  # single scalar not honestly an alignment prob


def test_multiple_heads_same_slot_averaged():
    scores = {
        "trade-outcome-winrate-baseline-v0": {"stage": "shadow", "score": 0.6},
        "trade-outcome-global-baseline-v0": {"stage": "shadow", "score": 0.8},
    }
    inputs, prov = build_conviction_inputs("x", None, scores)
    assert inputs["c_wr"] == pytest.approx(0.7)
    assert len(prov["c_wr"]) == 2


def test_influencing_only_filters_shadow_stage():
    scores = {
        "trade-outcome-winrate-baseline-v0": {"stage": "shadow", "score": 0.9},
        "setup-quality-baseline-v0": {"stage": "advisory", "score": 1.5},
    }
    # observed view: both contribute
    obs, _ = build_conviction_inputs("x", 0.5, scores)
    assert "c_wr" in obs and "c_setup" in obs
    # influencing view: only advisory+ heads (strategy c_strat is always live)
    inf, _ = build_conviction_inputs("x", 0.5, scores, influencing_only=True)
    assert "c_wr" not in inf            # shadow head excluded
    assert "c_setup" in inf             # advisory head kept
    assert "c_strat" in inf


def test_influence_stages_set():
    assert INFLUENCE_STAGES == {"advisory", "limited_live", "live_approved"}


def test_load_calibrators_roundtrip(tmp_path):
    art = tmp_path / "cal.json"
    art.write_text(json.dumps({
        "trend_donchian": PlattCalibrator(a=1.0, b=0.0).to_dict(),
    }))
    cal = load_calibrators(str(art))
    assert "trend_donchian" in cal
    assert 0.0 <= cal["trend_donchian"].predict(0.5) <= 1.0


def test_load_calibrators_missing_file_is_empty():
    assert load_calibrators("/nonexistent/cal.json") == {}


# --------------------------------------------------------------------------- #
# calibrator-path resolution order (env -> mirrored live path -> in-repo default)
# --------------------------------------------------------------------------- #
def test_cal_path_env_override_wins(monkeypatch, tmp_path):
    """CONVICTION_CALIBRATORS_PATH is honored verbatim even before the file
    exists (an operator pin is respected), ahead of the mirror + default."""
    env_path = str(tmp_path / "operator_pin.json")
    monkeypatch.setenv("CONVICTION_CALIBRATORS_PATH", env_path)
    # mirror exists but must be ignored when the env override is set
    monkeypatch.setattr(ci, "_mirrored_cal_path",
                        lambda: str(tmp_path / "mirror.json"))
    (tmp_path / "mirror.json").write_text("{}")
    assert ci._cal_path() == env_path


def test_cal_path_prefers_mirror_when_present(monkeypatch, tmp_path):
    """With no env override and the mirrored file present, the mirror wins over
    the in-repo default."""
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    mirror = tmp_path / "mirror.json"
    mirror.write_text("{}")
    monkeypatch.setattr(ci, "_mirrored_cal_path", lambda: str(mirror))
    assert ci._cal_path() == str(mirror)


def test_cal_path_falls_back_to_default(monkeypatch):
    """No env override and no mirrored file → the legacy in-repo default."""
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    monkeypatch.setattr(ci, "_mirrored_cal_path",
                        lambda: "/nonexistent/mirror/calibrators.json")
    assert ci._cal_path() == ci._DEFAULT_CAL_PATH


def test_cached_load_uses_mirror(monkeypatch, tmp_path):
    """End-to-end: load_calibrators_cached reads the mirrored artifact when it is
    the resolved path, still fail-permissive (a roundtrippable calibrator
    deserializes)."""
    mirror = tmp_path / "mirror.json"
    mirror.write_text(json.dumps(
        {"trend_donchian": PlattCalibrator(a=1.0, b=0.0).to_dict()}))
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    monkeypatch.setattr(ci, "_mirrored_cal_path", lambda: str(mirror))
    # reset the process cache so the mtime check re-reads
    monkeypatch.setattr(ci, "_cal_cache", None)
    monkeypatch.setattr(ci, "_cal_mtime", None)
    cal = ci.load_calibrators_cached()
    assert "trend_donchian" in cal


def test_cached_load_missing_everywhere_is_empty(monkeypatch):
    """No env, no mirror, no default file → {} (raw normalization)."""
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    monkeypatch.setattr(ci, "_mirrored_cal_path", lambda: "")
    monkeypatch.setattr(ci, "_DEFAULT_CAL_PATH", "/nonexistent/default.json")
    monkeypatch.setattr(ci, "_cal_cache", None)
    monkeypatch.setattr(ci, "_cal_mtime", None)
    assert ci.load_calibrators_cached() == {}


def test_never_raises_on_garbage():
    # malformed model_scores entries are skipped, not raised
    inputs, _ = build_conviction_inputs(
        "x", "not-a-number", {"trade-outcome-x": "garbage", "y": {"score": None}}
    )
    assert isinstance(inputs, dict)  # no exception
