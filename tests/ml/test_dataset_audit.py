"""Tests for `ml.datasets.audit.audit_dataset`.

Pins the build-time label-quality + degeneracy audit (the quarantine gate):
  (a) an all-zero feature column → flagged + quarantine (BL-20260628-XA-TRAINING-ZERO)
  (b) a clean varied dataset with a balanced label → ok, no quarantine
  (c) a single-class classification label → label flagged (the f1=0 case)
plus DataFrame-input support, a regression target, the threshold parameter,
and fail-permissive behaviour on junk input.

Row shape matches what `ml.datasets.builder.DatasetBuilder` writes to
`data.jsonl` (a list of JSON-object dicts); the manifest mock mirrors the
canonical `trainer_config.feature_columns` / `target_column` shape the
trainers read (see ml/configs/btc-regime-15m-lgbm-v2.yaml).
"""
from __future__ import annotations

import pytest

from ml.datasets.audit import audit_dataset


def _manifest(features, target="regime_label"):
    """Minimal manifest mock — the canonical trainer_config shape."""
    return {
        "model_id": "test-audit-model",
        "trainer_config": {
            "target_column": target,
            "feature_columns": list(features),
        },
    }


def _balanced_rows(n=40):
    """Varied features + a balanced 2-class regime_label."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "rolling_log_return_vol": 0.001 + (i % 7) * 0.0003,
                "log_return": (-1) ** i * 0.0012 * (1 + i % 5),
                "hour_of_day": i % 24,
                "regime_label": "volatile" if i % 2 == 0 else "range",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# (a) all-zero feature column → flagged + quarantine
# ---------------------------------------------------------------------------
def test_all_zero_feature_flagged_and_quarantines():
    rows = _balanced_rows()
    # kill one feature: every row's xa_peer1_log_return is 0.0 (the
    # BL-20260628-XA-TRAINING-ZERO degenerate cross-asset block).
    for r in rows:
        r["xa_peer1_log_return"] = 0.0
    manifest = _manifest(
        ["rolling_log_return_vol", "log_return", "xa_peer1_log_return"]
    )

    report = audit_dataset(rows, manifest)

    assert report["quarantine"] is True
    assert report["ok"] is False
    feats = {f["name"]: f for f in report["features"]}
    dead = feats["xa_peer1_log_return"]
    assert dead["flagged"] is True
    assert dead["zero_fraction"] == 1.0
    assert "BL-20260628-XA-TRAINING-ZERO" in (dead["reason"] or "")
    # the healthy features are not flagged
    assert feats["rolling_log_return_vol"]["flagged"] is False
    assert feats["log_return"]["flagged"] is False
    # the flag is surfaced at the top level too
    assert any("xa_peer1_log_return" in f for f in report["flags"])


# ---------------------------------------------------------------------------
# (b) clean dataset → ok, no quarantine
# ---------------------------------------------------------------------------
def test_clean_dataset_ok_no_quarantine():
    rows = _balanced_rows()
    manifest = _manifest(
        ["rolling_log_return_vol", "log_return", "hour_of_day"]
    )

    report = audit_dataset(rows, manifest)

    assert report["quarantine"] is False
    assert report["ok"] is True
    assert report["flags"] == []
    assert report["n_rows"] == len(rows)
    assert all(f["flagged"] is False for f in report["features"])
    # balanced 2-class label, not flagged
    assert report["label"]["kind"] == "classification"
    assert report["label"]["n_classes"] == 2
    assert report["label"]["flagged"] is False
    assert set(report["label"]["balance"]) == {"volatile", "range"}


# ---------------------------------------------------------------------------
# (c) single-class label → label flagged (degenerate f1=0 target)
# ---------------------------------------------------------------------------
def test_single_class_label_flagged():
    rows = _balanced_rows()
    for r in rows:
        r["regime_label"] = "range"  # collapse to one class
    manifest = _manifest(["rolling_log_return_vol", "log_return"])

    report = audit_dataset(rows, manifest)

    assert report["label"]["flagged"] is True
    assert report["label"]["n_classes"] == 1
    assert "single-class" in (report["label"]["reason"] or "")
    assert report["quarantine"] is True
    assert report["ok"] is False
    # features themselves are fine — the quarantine is label-driven
    assert all(f["flagged"] is False for f in report["features"])


# ---------------------------------------------------------------------------
# constant (non-zero) feature → variance==0 flag
# ---------------------------------------------------------------------------
def test_constant_nonzero_feature_flagged():
    rows = _balanced_rows()
    for r in rows:
        r["const_feat"] = 5.0  # constant but non-zero
    manifest = _manifest(["log_return", "const_feat"])

    report = audit_dataset(rows, manifest)

    feats = {f["name"]: f for f in report["features"]}
    assert feats["const_feat"]["flagged"] is True
    assert feats["const_feat"]["variance"] == 0.0
    assert "variance == 0" in (feats["const_feat"]["reason"] or "")
    assert report["quarantine"] is True


# ---------------------------------------------------------------------------
# near-fully-NaN/absent feature → nan_fraction flag
# ---------------------------------------------------------------------------
def test_mostly_missing_feature_flagged():
    rows = _balanced_rows(n=100)
    # only the first row carries the column; the rest are absent
    rows[0]["sparse_feat"] = 1.23
    manifest = _manifest(["log_return", "sparse_feat"])

    report = audit_dataset(rows, manifest)
    feats = {f["name"]: f for f in report["features"]}
    assert feats["sparse_feat"]["flagged"] is True
    assert feats["sparse_feat"]["nan_fraction"] >= 0.99
    assert "nan_fraction" in (feats["sparse_feat"]["reason"] or "")


# ---------------------------------------------------------------------------
# categorical STRING feature (present, multi-value) → NOT flagged
# (BL-20260718-AUDIT-CATEGORICAL-FALSEPOS: coercing non-numeric strings to NaN
# false-flagged every string categorical — e.g. vol_bucket's vol_b0/b1/b2 —
# as a dead column, even though it carries information the booster uses.)
# ---------------------------------------------------------------------------
def test_categorical_string_feature_not_flagged():
    rows = _balanced_rows(n=90)
    # vol_bucket: a healthy 3-value string categorical (fully present).
    for i, r in enumerate(rows):
        r["vol_bucket"] = f"vol_b{i % 3}"
    manifest = _manifest(["log_return", "vol_bucket"])

    report = audit_dataset(rows, manifest)
    feats = {f["name"]: f for f in report["features"]}
    vb = feats["vol_bucket"]
    assert vb["flagged"] is False, vb.get("reason")
    # present, informative categorical: 3 distinct values, zero truly-missing
    assert vb["n_unique"] == 3
    assert vb["nan_fraction"] == 0.0
    # a healthy categorical must not quarantine the manifest by itself
    assert report["quarantine"] is False


# ---------------------------------------------------------------------------
# constant (single-value) categorical → flagged (the categorical variance==0)
# ---------------------------------------------------------------------------
def test_constant_categorical_feature_flagged():
    rows = _balanced_rows(n=60)
    for r in rows:
        r["cat_const"] = "only_one_value"
    manifest = _manifest(["log_return", "cat_const"])

    report = audit_dataset(rows, manifest)
    feats = {f["name"]: f for f in report["features"]}
    cc = feats["cat_const"]
    assert cc["flagged"] is True
    assert cc["n_unique"] == 1
    assert "categorical" in (cc["reason"] or "")
    assert report["quarantine"] is True


# ---------------------------------------------------------------------------
# DataFrame input is supported (duck-typed, no pandas import in the module)
# ---------------------------------------------------------------------------
def test_dataframe_input_supported():
    pd = pytest.importorskip("pandas")
    rows = _balanced_rows()
    for r in rows:
        r["xa_peer1_log_return"] = 0.0
    df = pd.DataFrame(rows)
    manifest = _manifest(
        ["rolling_log_return_vol", "log_return", "xa_peer1_log_return"]
    )

    report = audit_dataset(df, manifest)

    assert report["n_rows"] == len(rows)
    feats = {f["name"]: f for f in report["features"]}
    assert feats["xa_peer1_log_return"]["flagged"] is True
    assert report["quarantine"] is True


# ---------------------------------------------------------------------------
# regression target: continuous → not class-flagged; constant → flagged
# ---------------------------------------------------------------------------
def test_regression_target_distribution_ok():
    rows = []
    for i in range(60):
        rows.append(
            {
                "feat_a": 0.1 + i * 0.01,
                "r_multiple": -2.0 + i * 0.137,  # continuous spread
            }
        )
    manifest = _manifest(["feat_a"], target="r_multiple")

    report = audit_dataset(rows, manifest)
    assert report["label"]["kind"] == "regression"
    assert report["label"]["flagged"] is False
    assert report["label"]["stats"]["count"] == 60
    assert report["quarantine"] is False


def test_constant_regression_target_flagged():
    rows = [{"feat_a": float(i), "r_multiple": 0.0} for i in range(50)]
    manifest = _manifest(["feat_a"], target="r_multiple")

    report = audit_dataset(rows, manifest)
    assert report["label"]["kind"] == "regression"
    assert report["label"]["flagged"] is True
    assert "constant" in (report["label"]["reason"] or "")
    assert report["quarantine"] is True


# ---------------------------------------------------------------------------
# threshold parameter is honoured
# ---------------------------------------------------------------------------
def test_dead_fraction_threshold_parameter():
    rows = _balanced_rows(n=100)
    # 95% zero, 5% non-zero in the "halfdead" feature
    for idx, r in enumerate(rows):
        r["halfdead"] = 0.0 if idx >= 5 else 1.5
    manifest = _manifest(["log_return", "halfdead"])

    # default 0.99 → 0.95 zero-fraction does NOT trip
    rpt_default = audit_dataset(rows, manifest)
    assert {f["name"]: f for f in rpt_default["features"]}["halfdead"][
        "flagged"
    ] is False

    # lowered to 0.90 → it trips
    rpt_strict = audit_dataset(rows, manifest, dead_fraction_threshold=0.90)
    assert {f["name"]: f for f in rpt_strict["features"]}["halfdead"][
        "flagged"
    ] is True
    assert rpt_strict["quarantine"] is True


# ---------------------------------------------------------------------------
# fail-permissive: empty dataset + junk rows never raise
# ---------------------------------------------------------------------------
def test_empty_dataset_quarantines_not_raises():
    manifest = _manifest(["a", "b"])
    report = audit_dataset([], manifest)
    assert report["n_rows"] == 0
    assert report["quarantine"] is True
    assert "0 rows" in " ".join(report["flags"])


def test_junk_rows_do_not_raise():
    manifest = _manifest(["a"])
    # not iterable-of-mappings — must degrade, not crash
    report = audit_dataset(None, manifest)
    assert report["n_rows"] == 0
    assert report["quarantine"] is True


def test_report_schema_keys_present():
    rows = _balanced_rows()
    manifest = _manifest(["log_return"])
    report = audit_dataset(rows, manifest)
    for key in (
        "ok", "manifest", "n_rows", "dead_fraction_threshold",
        "features", "label", "flags", "quarantine",
    ):
        assert key in report, f"missing report key {key!r}"
    assert report["manifest"] == "test-audit-model"
    # each feature row carries the documented sub-keys
    for f in report["features"]:
        for k in ("name", "zero_fraction", "nan_fraction", "variance",
                  "flagged", "reason"):
            assert k in f
