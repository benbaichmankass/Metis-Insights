"""Money-box import-discipline + pure-logic contract for the SSL corpus encoder (M19 T1.2 P1).

These tests deliberately do NOT importorskip torch/onnxruntime — they must run on
CI (which has neither) to PROVE the trainer/predictor/offline-block modules import
and their windowing / mask / fold-frozen-standardizer / as-of-join logic works
without them. If a torch/onnxruntime import ever leaks to module top-level,
collection here fails.
"""
from __future__ import annotations

import base64

import pytest


# --------------------------------------------------------------------------- #
# Import discipline (lazy torch / onnxruntime).
# --------------------------------------------------------------------------- #
def test_modules_import_without_torch_or_onnxruntime():
    import ml.datasets.corpus_embedding_features  # noqa: F401
    import ml.predictors.ssl_corpus_encoder as pred  # noqa: F401
    import ml.trainers.ssl_corpus_encoder as trn

    # The trainer↔predictor pairing resolves without any heavy dep imported.
    assert trn.SSLCorpusEncoderTrainer.PREDICTOR_CLASS is pred.SSLCorpusEncoderPredictor
    assert trn.TRAINER_QUALNAME.endswith("SSLCorpusEncoderTrainer")


# --------------------------------------------------------------------------- #
# Series resolution.
# --------------------------------------------------------------------------- #
def test_resolve_series_sorted_union_and_pinned():
    from ml.trainers.ssl_corpus_encoder import resolve_series

    rows = [
        {"date": "2020-01-01", "values": {"b": 1.0, "a": 2.0}},
        {"date": "2020-01-02", "values": {"c": 3.0}},
    ]
    assert resolve_series(rows) == ["a", "b", "c"]  # sorted union, deterministic
    assert resolve_series(rows, ["c", "a"]) == ["c", "a"]  # pin fixes set + order
    with pytest.raises(ValueError):
        resolve_series(rows, ["a", "a"])  # duplicate pin rejected


# --------------------------------------------------------------------------- #
# Windowing + leakage-safety.
# --------------------------------------------------------------------------- #
def _panel(dates, values_by_date):
    return [{"date": d, "values": values_by_date[d]} for d in dates]


def test_windowing_shape_and_leakage_safety():
    from ml.trainers.ssl_corpus_encoder import build_panel_windows

    dates = [f"2020-01-{d:02d}" for d in range(1, 8)]  # 7 days
    rows = _panel(dates, {d: {"a": float(i), "b": float(i) * 10} for i, d in enumerate(dates)})
    series = ["a", "b"]
    seq_len = 3
    windows = build_panel_windows(rows, series=series, seq_len=seq_len)

    # 7 rows, L=3 → 5 windows; first (seq_len-1)=2 rows dropped (incomplete).
    assert len(windows) == len(dates) - (seq_len - 1)
    for w in windows:
        assert len(w["values"]) == seq_len
        assert all(len(row) == len(series) for row in w["values"])
        assert len(w["mask"]) == seq_len
        # Leakage-safety: the window's dates are the L consecutive dates ENDING at
        # end_date — strictly increasing, none past end_date (the windower never
        # reaches a future row).
        wd = w["window_dates"]
        assert wd == sorted(wd)
        assert wd[-1] == w["end_date"]
        assert all(d <= w["end_date"] for d in wd)
    # The last window ends at the last date and spans the last L dates.
    assert windows[-1]["window_dates"] == dates[-seq_len:]


def test_missingness_mask_native_none_is_mask1_value0():
    from ml.trainers.ssl_corpus_encoder import build_panel_windows

    # `b` is absent (None) on day 1, present after → a genuine missingness cell.
    rows = [
        {"date": "2020-01-01", "values": {"a": 5.0, "b": None}},
        {"date": "2020-01-02", "values": {"a": 6.0, "b": 7.0}},
    ]
    windows = build_panel_windows(rows, series=["a", "b"], seq_len=2)
    assert len(windows) == 1
    w = windows[0]
    # row 0 (2020-01-01): a observed, b missing → mask (0,1), value (5.0, 0.0-placeholder)
    assert w["mask"][0] == [0.0, 1.0]
    assert w["values"][0] == [5.0, 0.0]  # missing cell's value input is a 0 PLACEHOLDER
    # row 1 (2020-01-02): both observed → mask (0,0)
    assert w["mask"][1] == [0.0, 0.0]
    assert w["values"][1] == [6.0, 7.0]


def test_non_finite_cell_is_treated_as_missing():
    from ml.trainers.ssl_corpus_encoder import build_panel_windows

    rows = [
        {"date": "2020-01-01", "values": {"a": float("nan")}},
        {"date": "2020-01-02", "values": {"a": "not-a-number"}},
    ]
    w = build_panel_windows(rows, series=["a"], seq_len=2)[0]
    assert w["mask"] == [[1.0], [1.0]]  # NaN + unparseable → both missing
    assert w["values"] == [[0.0], [0.0]]


# --------------------------------------------------------------------------- #
# Fold-frozen standardizer math.
# --------------------------------------------------------------------------- #
def test_standardizer_ignores_missing_and_guards_constant():
    from ml.trainers.ssl_corpus_encoder import fit_cell_standardizer

    # One window, L=3, F=2. Series a = [0,2,4] (observed); series b = [const 5, 5, missing].
    values = [[[0.0, 5.0], [2.0, 5.0], [4.0, 0.0]]]
    masks = [[[0.0, 0.0], [0.0, 0.0], [0.0, 1.0]]]  # b missing on the last row
    mean, std = fit_cell_standardizer(values, masks, 2)
    # a: mean 2, population std sqrt(mean((−2,0,2)^2)) = sqrt(8/3)
    assert mean[0] == pytest.approx(2.0)
    assert std[0] == pytest.approx((8.0 / 3.0) ** 0.5)
    # b: only observed cells (5,5) → mean 5, std 0 guarded to 1.0.
    assert mean[1] == pytest.approx(5.0)
    assert std[1] == pytest.approx(1.0)


def test_standardize_window_observed_and_missing():
    from ml.trainers.ssl_corpus_encoder import standardize_window

    values = [[2.0, 100.0], [4.0, 0.0]]
    mask = [[0.0, 0.0], [0.0, 1.0]]  # (1,1) missing
    mean = [3.0, 100.0]
    std = [1.0, 10.0]
    out = standardize_window(values, mask, mean, std)
    assert out[0] == pytest.approx([-1.0, 0.0])  # (2-3)/1, (100-100)/10
    assert out[1][0] == pytest.approx(1.0)       # (4-3)/1
    assert out[1][1] == 0.0                       # missing → 0.0, NOT (0-100)/10


def test_serve_reuses_frozen_train_stats_not_val_stats():
    """Fold-freeze: a val window is standardized by the TRAIN stats, not its own."""
    from ml.trainers.ssl_corpus_encoder import fit_cell_standardizer, standardize_window

    train_windows_v = [[[0.0], [2.0], [4.0]]]  # train series values 0,2,4 → mean 2
    train_windows_m = [[[0.0], [0.0], [0.0]]]
    mean, std = fit_cell_standardizer(train_windows_v, train_windows_m, 1)
    assert mean[0] == pytest.approx(2.0)

    # A val window with a DIFFERENT distribution (all 100s). Standardizing it must
    # use the frozen train mean (2), so the output is (100-2)/std — never centered
    # on the val window's own mean (which would give 0).
    val_v = [[100.0], [100.0]]
    val_m = [[0.0], [0.0]]
    out = standardize_window(val_v, val_m, mean, std)
    assert out[0][0] == pytest.approx((100.0 - 2.0) / std[0])
    assert out[0][0] != pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Predictor: constructs + degrades without onnxruntime.
# --------------------------------------------------------------------------- #
def test_predictor_constructs_and_predict_degrades_without_onnxruntime():
    from ml.predictors.ssl_corpus_encoder import SSLCorpusEncoderPredictor

    state = {
        "series": ["a", "b"],
        "seq_len": 4,
        "embedding_dim": 8,
        "onnx_b64": base64.b64encode(b"not-a-real-graph").decode("ascii"),
    }
    # Construction must not build an onnxruntime session (lazy).
    p = SSLCorpusEncoderPredictor(state)
    # predict() with no window returns 0.0 WITHOUT touching onnxruntime.
    assert p.predict({"unrelated": 1}) == 0.0
    assert p.predict({}) == 0.0


def test_predictor_rejects_bad_state():
    from ml.predictors.ssl_corpus_encoder import SSLCorpusEncoderPredictor

    with pytest.raises(ValueError):
        SSLCorpusEncoderPredictor({"series": [], "seq_len": 4, "onnx_b64": "x"})
    with pytest.raises(ValueError):
        SSLCorpusEncoderPredictor({"series": ["a"], "seq_len": 4})  # no onnx


# --------------------------------------------------------------------------- #
# Offline embedding block: producer + one-day-lag as-of join.
# --------------------------------------------------------------------------- #
def test_compute_corpus_embedding_rows_with_stub_embedder():
    from ml.datasets.corpus_embedding_features import (
        compute_corpus_embedding_rows,
        corpus_embedding_columns,
    )

    dates = [f"2020-02-{d:02d}" for d in range(1, 6)]  # 5 days
    rows = _panel(dates, {d: {"a": float(i)} for i, d in enumerate(dates)})
    out_dim = 2

    def stub(window):
        # Deterministic: [last a, window length].
        last_a = window[-1]["values"].get("a", 0.0)
        return [float(last_a), float(len(window))]

    emb = compute_corpus_embedding_rows(
        rows, embed_fn=stub, seq_len=3, out_dim=out_dim
    )
    # min_context defaults to seq_len=3 → first full window ends at index 2.
    assert [r["date"] for r in emb] == dates[2:]
    cols = corpus_embedding_columns(out_dim)
    assert set(cols).issubset(emb[0].keys())
    # The window ending at the last date has length 3 (capped at seq_len).
    assert emb[-1]["corpus_emb_1"] == 3.0
    assert emb[-1]["corpus_emb_0"] == 4.0  # last a value on the 5th day (i=4)


def test_compute_corpus_embedding_rows_degrades_on_embedder_failure():
    from ml.datasets.corpus_embedding_features import compute_corpus_embedding_rows

    dates = [f"2020-02-{d:02d}" for d in range(1, 4)]
    rows = _panel(dates, {d: {"a": 1.0} for d in dates})

    def boom(window):
        raise RuntimeError("encoder unavailable")

    # A failing embedder yields NO rows (the as-of join then leaves neutral 0.0) —
    # never a crash, never a fabricated embedding.
    assert compute_corpus_embedding_rows(rows, embed_fn=boom, seq_len=3, out_dim=2) == []


def test_sidestream_one_day_lag_rekey():
    from ml.datasets.corpus_embedding_features import corpus_embedding_sidestream

    emb = [
        {"date": "2020-03-01", "corpus_emb_0": 1.0, "corpus_emb_1": 2.0},
        {"date": "2020-03-02", "corpus_emb_0": 3.0, "corpus_emb_1": 4.0},
    ]
    side = corpus_embedding_sidestream(
        emb, lag_days=1, columns=("corpus_emb_0", "corpus_emb_1")
    )
    # Panel day D's embedding becomes available at ts D+1 (one-day lag).
    assert side[0]["ts"] == "2020-03-02T00:00:00Z"
    assert side[1]["ts"] == "2020-03-03T00:00:00Z"
    assert side[0]["corpus_emb_0"] == 1.0


def test_align_corpus_embeddings_lag_and_zero_default():
    from ml.datasets.corpus_embedding_features import (
        align_corpus_embeddings,
        corpus_embedding_sidestream,
    )

    cols = ("corpus_emb_0",)
    emb = [
        {"date": "2020-03-01", "corpus_emb_0": 11.0},
        {"date": "2020-03-02", "corpus_emb_0": 22.0},
    ]
    side = corpus_embedding_sidestream(emb, lag_days=1, columns=cols)  # ts 03-02, 03-03

    # Intraday bars across three days.
    bar_ts = [
        "2020-03-01T09:00:00Z",  # before any lagged embedding → 0.0 default
        "2020-03-02T09:00:00Z",  # sees panel day 03-01's embedding (one-day lag)
        "2020-03-03T09:00:00Z",  # sees panel day 03-02's embedding
    ]
    joined = align_corpus_embeddings(bar_ts, side, columns=cols)
    assert joined["corpus_emb_0"] == [0.0, 11.0, 22.0]
