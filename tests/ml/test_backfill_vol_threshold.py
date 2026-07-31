"""The vol_threshold recovery must MEASURE, and must refuse when it cannot.

BL-20260730-DATASET-DIRS-MISSING-VOL-THRESHOLD. 25 of 38 on-disk
market_features dirs record no `vol_threshold`, including BTCUSDT/15m/v520 —
the dataset the live advisory BTC head pins.

The recovery is exact because the label rule is exact
(`forward_vol > vol_threshold` → "volatile") and `forward_log_return_vol` is
emitted, so the true threshold is bracketed by
`[max(forward_vol | range), min(forward_vol | volatile))`.

The tests that matter most here are the REFUSALS. A backfill that fills in a
plausible value where the data does not determine one would manufacture
provenance — the exact failure `src/runtime/provenance.py` exists to prevent,
committed by the tool built to repair a provenance hole.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "backfill_vt", _ROOT / "scripts" / "ml" / "backfill_dataset_vol_threshold.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bf = _load()


def _stage(tmp_path: Path, rows, *, build_params=None, symbol="BTCUSDT",
           tf="15m", version="v001", family="market_features") -> Path:
    d = tmp_path / family / symbol / tf / version
    d.mkdir(parents=True, exist_ok=True)
    with (d / "data.jsonl").open("w", encoding="utf-8") as fh:
        for vol, label in rows:
            fh.write(json.dumps({"forward_log_return_vol": vol,
                                 "regime_label": label}) + "\n")
    (d / "metadata.json").write_text(json.dumps({
        "family": family, "version": version,
        "build_params": build_params if build_params is not None else {},
    }), encoding="utf-8")
    return d


def _rows_for(threshold: float, n: int = 200):
    """Rows labelled by the REAL rule, so the bracket is genuine."""
    out = []
    for i in range(n):
        vol = 0.0002 + i * 0.0001
        out.append((vol, "volatile" if vol > threshold else "range"))
    return out


@pytest.mark.parametrize("threshold", [0.001, 0.003, 0.005, 0.01])
def test_recovers_each_production_threshold_exactly(tmp_path, threshold):
    _stage(tmp_path, _rows_for(threshold))
    (res,) = bf.scan(str(tmp_path))
    assert res["state"] == "derived", res
    assert res["value"] == threshold
    assert res["bracket"]["lo"] <= threshold < res["bracket"]["hi"]


def test_the_bracket_is_a_real_containment_not_a_nearest_match(tmp_path):
    """The recovered value must lie INSIDE the measured interval."""
    _stage(tmp_path, _rows_for(0.005))
    (res,) = bf.scan(str(tmp_path))
    lo, hi = res["bracket"]["lo"], res["bracket"]["hi"]
    assert lo <= 0.005 < hi
    # and the neighbours must NOT be admissible, else it wasn't discriminating
    assert not (lo <= 0.003 < hi)
    assert not (lo <= 0.01 < hi)


def test_refuses_when_the_bracket_admits_two_candidates(tmp_path):
    """Coarse data cannot separate 0.003 from 0.005 — so it must not try."""
    rows = [(0.0005, "range"), (0.0008, "range"), (0.02, "volatile"), (0.03, "volatile")]
    _stage(tmp_path, rows)
    (res,) = bf.scan(str(tmp_path))
    assert res["state"] == "refused"
    assert "ambiguous" in res["reason"]


def test_refuses_a_degenerate_all_range_dir(tmp_path):
    """With no volatile rows the threshold is only 'above the max' — an open
    interval, not a value."""
    _stage(tmp_path, [(0.0001 * i, "range") for i in range(1, 50)])
    (res,) = bf.scan(str(tmp_path))
    assert res["state"] == "refused"
    assert "degenerate" in res["reason"]


def test_refuses_a_degenerate_all_volatile_dir(tmp_path):
    _stage(tmp_path, [(0.05 + 0.001 * i, "volatile") for i in range(50)])
    (res,) = bf.scan(str(tmp_path))
    assert res["state"] == "refused"
    assert "degenerate" in res["reason"]


def test_refuses_an_inconsistent_bracket(tmp_path):
    """Labels that no single threshold explains must not be forced into one."""
    rows = [(0.009, "range"), (0.001, "volatile")]
    _stage(tmp_path, rows)
    (res,) = bf.scan(str(tmp_path))
    assert res["state"] == "refused"
    assert "inconsistent" in res["reason"]


def test_leaves_an_already_recorded_dir_alone(tmp_path):
    _stage(tmp_path, _rows_for(0.005), build_params={"vol_threshold": 0.001})
    (res,) = bf.scan(str(tmp_path))
    assert res["state"] == "already_recorded"
    assert res["value"] == 0.001, "a recorded value is authoritative, never overwritten"


def test_apply_stamps_the_value_as_DERIVED_with_its_evidence(tmp_path):
    """A recovered value must never be indistinguishable from a recorded one."""
    d = _stage(tmp_path, _rows_for(0.005))
    (res,) = bf.scan(str(tmp_path))
    bf.apply_one(res["path"], res["value"], res["bracket"]["lo"], res["bracket"]["hi"])
    bp = json.loads((d / "metadata.json").read_text(encoding="utf-8"))["build_params"]
    assert bp["vol_threshold"] == 0.005
    assert bp["vol_threshold_source"] == "derived_from_labels"
    assert bp["vol_threshold_bracket"][0] <= 0.005 < bp["vol_threshold_bracket"][1]


def test_apply_does_not_touch_the_data(tmp_path):
    d = _stage(tmp_path, _rows_for(0.005))
    before = (d / "data.jsonl").read_bytes()
    (res,) = bf.scan(str(tmp_path))
    bf.apply_one(res["path"], res["value"], res["bracket"]["lo"], res["bracket"]["hi"])
    assert (d / "data.jsonl").read_bytes() == before


def test_trend_threshold_is_never_recovered(tmp_path):
    """It is INERT — it affects no emitted column, so the labels carry zero
    information about it. Recovering it would be the same defect one level
    down. See BL-20260730-TREND-THRESHOLD-INERT."""
    d = _stage(tmp_path, _rows_for(0.005))
    (res,) = bf.scan(str(tmp_path))
    bf.apply_one(res["path"], res["value"], res["bracket"]["lo"], res["bracket"]["hi"])
    bp = json.loads((d / "metadata.json").read_text(encoding="utf-8"))["build_params"]
    assert "trend_threshold" not in bp


def test_empty_root_is_reported_as_absent_not_clean():
    """Scanning nothing must not read as 'all dirs are fine'."""
    assert bf.scan("/nonexistent-datasets-root") == []
