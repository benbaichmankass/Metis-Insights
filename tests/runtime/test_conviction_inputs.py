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


# --------------------------------------------------------------------------- #
# c_reg via the regime-alignment calibrator (piece 4 of the A+B program)
# --------------------------------------------------------------------------- #
_REGIME_MODEL = "btc-regime-1h-lgbm-yz-v1"


def _alignment(a: float, b: float, *, direction: str | None = None):
    """Build a {model_id: {direction: Calibrator}} alignment map for the test."""
    from ml.calibration.regime_alignment import POOLED_DIRECTION

    key = direction or POOLED_DIRECTION
    return {_REGIME_MODEL: {key: PlattCalibrator(a=a, b=b)}}


def test_creg_flows_when_alignment_calibrator_present():
    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 0.5}}
    # sigmoid(0*0.5 + 0) = 0.5 — a known calibrated alignment prob
    ra = _alignment(a=0.0, b=0.0)
    inputs, prov = build_conviction_inputs(
        "x", 0.5, scores, direction="long", regime_alignment=ra
    )
    assert inputs["c_reg"] == pytest.approx(0.5)
    assert prov["c_reg"][0]["kind"] == "regime_alignment"
    assert prov["c_reg"][0]["calibrated"] is True


def test_creg_known_value_from_score():
    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 2.0}}
    # sigmoid(1*2 + 0) ≈ 0.8808
    ra = _alignment(a=1.0, b=0.0)
    inputs, _ = build_conviction_inputs(
        "x", None, scores, direction="long", regime_alignment=ra
    )
    assert inputs["c_reg"] == pytest.approx(0.8807970, abs=1e-5)


def test_creg_direction_sensitivity():
    """Long vs short can map the SAME regime score to different alignment."""
    from ml.calibration.regime_alignment import POOLED_DIRECTION

    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 1.0}}
    ra = {
        _REGIME_MODEL: {
            "long": PlattCalibrator(a=5.0, b=0.0),    # sigmoid(5) ≈ 0.993
            "short": PlattCalibrator(a=-5.0, b=0.0),  # sigmoid(-5) ≈ 0.007
            POOLED_DIRECTION: PlattCalibrator(a=0.0, b=0.0),  # 0.5
        }
    }
    long_in, _ = build_conviction_inputs(
        "x", None, scores, direction="long", regime_alignment=ra)
    short_in, _ = build_conviction_inputs(
        "x", None, scores, direction="short", regime_alignment=ra)
    assert long_in["c_reg"] > 0.9
    assert short_in["c_reg"] < 0.1
    assert long_in["c_reg"] != short_in["c_reg"]


def test_creg_falls_back_to_pooled_when_no_direction_calibrator():
    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 0.5}}
    ra = _alignment(a=0.0, b=0.0)  # only the pooled "all" calibrator
    inputs, _ = build_conviction_inputs(
        "x", None, scores, direction="short", regime_alignment=ra)
    assert inputs["c_reg"] == pytest.approx(0.5)  # pooled used


def test_creg_absent_when_alignment_for_other_model_only():
    """No alignment calibrator for THIS regime head → c_reg dropped (unchanged)."""
    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 0.98}}
    ra = {"some-other-regime-model": {"all": PlattCalibrator(a=1.0, b=0.0)}}
    inputs, _ = build_conviction_inputs(
        "x", 0.5, scores, direction="long", regime_alignment=ra)
    assert "c_reg" not in inputs


def test_creg_byte_for_byte_unchanged_without_alignment_arg():
    """The exact pre-calibrator behaviour: no regime_alignment passed → c_reg
    absent, byte-for-byte the test_regime_skipped_without_calibrator case."""
    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 0.98}}
    inputs, prov = build_conviction_inputs("x", 0.5, scores)
    assert "c_reg" not in inputs
    assert "c_reg" not in prov


def test_creg_fail_permissive_on_bad_alignment_calibrator():
    """A calibrator whose predict raises must not strand the build — c_reg drops."""
    class _Boom(PlattCalibrator):
        def predict(self, x):  # noqa: ARG002
            raise ValueError("boom")

    scores = {_REGIME_MODEL: {"stage": "advisory", "score": 0.5}}
    ra = {_REGIME_MODEL: {"all": _Boom(a=1.0, b=0.0)}}
    inputs, _ = build_conviction_inputs(
        "x", 0.5, scores, direction="long", regime_alignment=ra)
    assert "c_reg" not in inputs  # predict raised → predict_alignment None → dropped
    assert "c_strat" in inputs    # other inputs unaffected


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


# --------------------------------------------------------------------------- #
# regime_alignment cached loader (reads the same artifact, separate section)
# --------------------------------------------------------------------------- #
def test_load_regime_alignment_cached_reads_section(monkeypatch, tmp_path):
    from ml.calibration.regime_alignment import REGIME_ALIGNMENT_KEY

    art = tmp_path / "calibrators.json"
    art.write_text(json.dumps({
        # a confidence calibrator at the top level (must coexist)
        "trend_donchian": PlattCalibrator(a=1.0, b=0.0).to_dict(),
        REGIME_ALIGNMENT_KEY: {
            "btc-regime-1h-lgbm-yz-v1": {
                "all": PlattCalibrator(a=0.0, b=0.0).to_dict()},
        },
    }))
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    monkeypatch.setattr(ci, "_mirrored_cal_path", lambda: str(art))
    monkeypatch.setattr(ci, "_ra_cache", None)
    monkeypatch.setattr(ci, "_ra_mtime", None)
    ra = ci.load_regime_alignment_cached()
    assert "btc-regime-1h-lgbm-yz-v1" in ra
    assert "all" in ra["btc-regime-1h-lgbm-yz-v1"]
    # the confidence section still loads independently + ignores the new key
    monkeypatch.setattr(ci, "_cal_cache", None)
    monkeypatch.setattr(ci, "_cal_mtime", None)
    cal = ci.load_calibrators_cached()
    assert "trend_donchian" in cal
    assert REGIME_ALIGNMENT_KEY not in cal  # not a Calibrator → skipped cleanly


def test_load_regime_alignment_cached_missing_is_empty(monkeypatch):
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    monkeypatch.setattr(ci, "_mirrored_cal_path", lambda: "")
    monkeypatch.setattr(ci, "_DEFAULT_CAL_PATH", "/nonexistent/default.json")
    monkeypatch.setattr(ci, "_ra_cache", None)
    monkeypatch.setattr(ci, "_ra_mtime", None)
    assert ci.load_regime_alignment_cached() == {}


def test_load_regime_alignment_cached_corrupt_artifact_is_empty(monkeypatch, tmp_path):
    """A corrupt artifact → {} (fail-permissive), so c_reg simply stays dropped."""
    art = tmp_path / "calibrators.json"
    art.write_text("{ this is not valid json")
    monkeypatch.delenv("CONVICTION_CALIBRATORS_PATH", raising=False)
    monkeypatch.setattr(ci, "_mirrored_cal_path", lambda: str(art))
    monkeypatch.setattr(ci, "_ra_cache", None)
    monkeypatch.setattr(ci, "_ra_mtime", None)
    assert ci.load_regime_alignment_cached() == {}
