"""The OFFLINE ML vol axis — the second dimension the regime harness was missing.

Closes the measurement half of ``BL-20260730-2D-VOL-CELLS-UNAUDITABLE``: the live
router gates on a 2-D ``(trend, vol)`` cell, the harness only had the trend axis,
so six authored cells that drop real BTC intents could not be re-measured — and a
1-D grade of a strategy carrying a 2-D cell silently POOLED vol states the live
gate already refuses.

Offline + deterministic: no registry, no dataset, no network, no DB. The two
things under test are the ones that can silently produce a wrong-population
answer:

  1. **The label rule is the LIVE rule.** ``ml_vol_label_replay.label_for_p`` must
     agree with ``ml_vol_verdict.ml_vol_regime_for_symbol`` — the function the
     gate decision actually calls — including the ``>=`` boundary. Tested by
     driving the real live resolver through its per-bar publish cache and
     comparing labels, not by restating the rule.
  2. **The as-of join never reads a future bar** and never invents a label for a
     trade outside the labelled span.

scripts/research is not a package, so modules load via importlib.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_RESEARCH = os.path.join(_ROOT, "scripts", "research")


def _load(name: str):
    if _RESEARCH not in sys.path:
        sys.path.insert(0, _RESEARCH)
    spec = importlib.util.spec_from_file_location(name, os.path.join(_RESEARCH, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rte = _load("regime_tag_emitted")
replay = _load("ml_vol_label_replay")


def _ts(s: str):
    return pd.to_datetime(s, utc=True)


def _labels(pairs):
    return [(_ts(t), lab) for t, lab in pairs]


# ---------------------------------------------------------------------------
# 1. the label rule IS the live rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p_volatile",
    [0.0, 0.01, 0.2, 0.4999999, 0.5, 0.5000001, 0.8, 1.0],
)
def test_label_matches_live_gate_exactly(p_volatile):
    """The replay's mapping must equal the live gate's, boundary included.

    This is the test that stops the whole tool measuring a different population
    than the router. Rather than restating ``p >= tau``, it drives the REAL live
    resolver (``ml_vol_regime_for_symbol``) via its per-bar publish cache and
    asserts the replay lands on the same label.
    """
    from src.runtime.regime.ml_vol_verdict import (
        clear_ml_vol_cache,
        ml_vol_regime_for_symbol,
        publish_p_volatile,
    )

    model_id = "stub-regime-head"
    specs = {("BTCUSDT", "15M"): {
        "symbol": "BTCUSDT", "timeframe": "15m",
        "model_id": model_id, "predictor": None, "is_yz": False,
    }}
    clear_ml_vol_cache()
    publish_p_volatile(model_id, "2026-01-01T00:00:00Z", p_volatile)

    live = ml_vol_regime_for_symbol("BTCUSDT", specs=specs)
    tau = replay.live_threshold()

    assert live["vol_regime"] == replay.label_for_p(p_volatile, tau)
    clear_ml_vol_cache()


def test_boundary_is_inclusive_volatile():
    """``P(volatile) == tau`` is VOLATILE (the gate uses ``>=``), not calm."""
    assert replay.label_for_p(0.5, 0.5) == "volatile"
    assert replay.label_for_p(0.4999, 0.5) == "calm"


def test_unscorable_bar_is_unknown_not_calm():
    """A bar with no ``P(volatile)`` is ``unknown`` — never silently ``calm``.

    Live treats ``unknown`` as permissive (it keeps the frozen label); folding it
    into ``calm`` offline would attribute trades to a cell that may be gated.
    """
    assert replay.label_for_p(None, 0.5) == "unknown"


# ---------------------------------------------------------------------------
# 2. the as-of join
# ---------------------------------------------------------------------------


def test_vol_label_at_is_as_of_never_future():
    labs = _labels([("2026-01-01T00:00:00Z", "calm"),
                    ("2026-01-01T01:00:00Z", "volatile")])
    # before the first labelled bar -> unknown (do NOT borrow the earliest label)
    assert rte.vol_label_at(labs, _ts("2025-12-31T23:59:00Z")) == "unknown"
    # exactly on a bar -> that bar
    assert rte.vol_label_at(labs, _ts("2026-01-01T00:00:00Z")) == "calm"
    # between bars -> the PAST bar, not the upcoming one
    assert rte.vol_label_at(labs, _ts("2026-01-01T00:59:59Z")) == "calm"
    assert rte.vol_label_at(labs, _ts("2026-01-01T01:00:00Z")) == "volatile"
    # after the last bar -> carry the last known label forward
    assert rte.vol_label_at(labs, _ts("2026-06-01T00:00:00Z")) == "volatile"


def test_vol_label_at_empty_is_unknown():
    assert rte.vol_label_at([], _ts("2026-01-01T00:00:00Z")) == "unknown"


def test_load_vol_labels_drops_unknown_rows(tmp_path):
    """``unknown`` bars are not a third bucket — the router has no such state."""
    p = tmp_path / "labels.jsonl"
    p.write_text(
        '{"ts": "2026-01-01T00:00:00Z", "vol_regime": "calm"}\n'
        '{"ts": "2026-01-01T00:15:00Z", "vol_regime": "unknown"}\n'
        '{"ts": "2026-01-01T00:30:00Z", "vol_regime": "volatile"}\n',
        encoding="utf-8",
    )
    got = rte.load_vol_labels(str(p))
    assert [lab for _t, lab in got] == ["calm", "volatile"]


# ---------------------------------------------------------------------------
# 3. the 2-D grade — the actual point of the exercise
# ---------------------------------------------------------------------------


def _frame(n=8):
    ts = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.0,
                         "low": 1.0, "close": 1.0})


def test_pooled_trend_cell_splits_into_opposite_signed_subcells():
    """A pooled 1-D cell can hide an opposite-signed vol sub-cell.

    This is the concrete reason a 1-D verdict is not decision-grade for a
    strategy with a 2-D cell: pooled ``trending`` reads +1.0R here while its
    ``volatile`` half is -3.0R. Proposing a cell change off the pooled number
    would gate the wrong population.
    """
    df = _frame()
    adx = pd.Series([30.0] * len(df))  # everything 'trending'
    labs = _labels([("2026-01-01T00:00:00Z", "calm"),
                    ("2026-01-01T04:00:00Z", "volatile")])
    trades = [
        {"entry_time": "2026-01-01T01:00:00Z", "direction": "long", "net_r": 2.0},
        {"entry_time": "2026-01-01T02:00:00Z", "direction": "long", "net_r": 2.0},
        {"entry_time": "2026-01-01T05:00:00Z", "direction": "short", "net_r": -1.5},
        {"entry_time": "2026-01-01T06:00:00Z", "direction": "short", "net_r": -1.5},
    ]
    pooled = rte.tag_emitted_by_regime(trades, adx, df)
    assert pooled["trending"]["net_r"] == pytest.approx(1.0)

    by_cell = rte.tag_emitted_by_cell(trades, adx, df, labs)
    assert by_cell["trending/calm"]["net_r"] == pytest.approx(4.0)
    assert by_cell["trending/calm"]["trades"] == 2
    assert by_cell["trending/volatile"]["net_r"] == pytest.approx(-3.0)
    assert by_cell["trending/volatile"]["short_n"] == 2
    # the split must conserve the pooled total — no trade lost or double-counted
    assert sum(s["net_r"] for s in by_cell.values()) == pytest.approx(
        pooled["trending"]["net_r"]
    )


def test_unlabelled_trades_land_in_unknown_and_are_counted():
    """A trade before the labelled span is NOT folded into calm/volatile.

    Silently absorbing it would inflate a cell with trades whose live gate
    outcome the tool cannot reconstruct — the exact 'complete artifact, wrong
    population' failure this work exists to end.
    """
    df = _frame()
    adx = pd.Series([30.0] * len(df))
    labs = _labels([("2026-01-01T04:00:00Z", "volatile")])
    trades = [
        {"entry_time": "2026-01-01T01:00:00Z", "direction": "long", "net_r": 1.0},
        {"entry_time": "2026-01-01T05:00:00Z", "direction": "long", "net_r": 1.0},
    ]
    by_cell = rte.tag_emitted_by_cell(trades, adx, df, labs)
    assert by_cell["trending/unknown"]["trades"] == 1
    assert by_cell["trending/volatile"]["trades"] == 1

    cov = rte.vol_coverage(by_cell)
    assert cov == {"trades": 2, "vol_labelled": 1, "vol_unknown": 1,
                   "coverage_pct": 50.0}


def test_annotate_adds_cell_key_matching_policy_notation():
    """The emitted key reads like the authored cell (``trending/calm``)."""
    df = _frame()
    adx = pd.Series([30.0] * len(df))
    labs = _labels([("2026-01-01T00:00:00Z", "calm")])
    out = rte.annotate_trades_with_regime(
        [{"entry_time": "2026-01-01T02:00:00Z", "direction": "long", "net_r": 0.5}],
        adx, df, labs,
    )
    assert out[0]["regime"] == "trending"
    assert out[0]["vol_regime"] == "calm"
    assert out[0]["cell"] == "trending/calm"


def test_no_vol_labels_keeps_1d_behaviour_unchanged():
    """Without labels the annotator is byte-for-byte the pre-existing 1-D shape."""
    df = _frame()
    adx = pd.Series([30.0] * len(df))
    out = rte.annotate_trades_with_regime(
        [{"entry_time": "2026-01-01T02:00:00Z", "direction": "long", "net_r": 0.5}],
        adx, df,
    )
    assert out[0]["regime"] == "trending"
    assert "vol_regime" not in out[0]
    assert "cell" not in out[0]


# ---------------------------------------------------------------------------
# 4. vacuity refusals — a green run that measured nothing must fail loudly
# ---------------------------------------------------------------------------


def test_verify_reports_no_overlap_as_its_own_verdict(tmp_path):
    """'Could not measure' is neither a pass nor a finding — it is its own outcome.

    An empty comparison must NOT read as 'verified'
    (docs/CLAUDE-RULES-CANONICAL.md § "Green is not evidence").
    """
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        '{"ts": "2026-01-01T00:00:00Z", "vol_regime": "calm"}\n', encoding="utf-8"
    )
    audit = tmp_path / "audit.jsonl"
    # rows with no ML label at all -> nothing comparable
    audit.write_text(
        '{"ts": "2026-01-01T00:05:00Z", "vol_regime_frozen": "calm"}\n',
        encoding="utf-8",
    )
    res = replay.run_verify(labels_path=labels, audit_path=audit)
    assert res["comparable"] == 0
    assert res["verdict"] == "no_overlap_nothing_verified"
    assert res["agreement_pct"] is None


def test_verify_counts_agreement_and_flags_disagreement(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        '{"ts": "2026-01-01T00:00:00Z", "vol_regime": "calm", "p_volatile": 0.1}\n'
        '{"ts": "2026-01-01T01:00:00Z", "vol_regime": "volatile", "p_volatile": 0.9}\n',
        encoding="utf-8",
    )
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        # agrees (as-of bar 00:00 -> calm)
        '{"ts": "2026-01-01T00:30:00Z", "vol_regime_ml": "calm"}\n'
        # disagrees (as-of bar 01:00 -> volatile, live said calm)
        '{"ts": "2026-01-01T01:30:00Z", "vol_regime_ml": "calm"}\n',
        encoding="utf-8",
    )
    res = replay.run_verify(labels_path=labels, audit_path=audit)
    assert res["comparable"] == 2
    assert res["agree"] == 1
    assert res["agreement_pct"] == 50.0
    assert res["verdict"] == "disagreements_found"
    assert res["disagreement_sample"][0]["live"] == "calm"
    assert res["disagreement_sample"][0]["replayed"] == "volatile"


def test_verify_reads_vol_regime_when_source_is_ml(tmp_path):
    """``regime_hard_gate`` rows carry the decision label in ``vol_regime``."""
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        '{"ts": "2026-01-01T00:00:00Z", "vol_regime": "volatile"}\n', encoding="utf-8"
    )
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        '{"ts": "2026-01-01T00:30:00Z", "vol_regime": "volatile", '
        '"vol_label_source": "ml"}\n',
        encoding="utf-8",
    )
    res = replay.run_verify(labels_path=labels, audit_path=audit)
    assert res["comparable"] == 1
    assert res["verdict"] == "verified"


# ---------------------------------------------------------------------------
# 5. the feature pre-flight — the checks that catch a SILENTLY degraded score
# ---------------------------------------------------------------------------
#
# `ml/predictors/lightgbm.py::_encode_row` turns an unknown categorical into
# -1 and a missing numeric into NaN. Both still return a plausible
# P(volatile). So a subtly-wrong dataset yields confident WRONG labels with no
# error, and every downstream cell verdict inherits it. These are the only
# checks that can catch that before it becomes a decision.


class _StubHead:
    """Minimal stand-in for LightGBMMulticlassPredictor's audited surface."""

    def __init__(self, edges=(0.001, 0.002), labels=("vol_b0", "vol_b1", "vol_b2")):
        self._feature_columns = ["vol_bucket", "rolling_log_return_vol", "log_return"]
        self._categorical_columns = ["vol_bucket"]
        self._cat_mappings = {"vol_bucket": {"vol_b0": 0, "vol_b1": 1, "vol_b2": 2}}
        self.class_labels = ("range", "volatile")
        self.regime_spec = {
            "feature_column": "vol_bucket",
            "vol_feature_column": "rolling_log_return_vol",
            "vol_bucket_edges": list(edges),
            "vol_bucket_labels": list(labels),
            "symbol": "BTCUSDT",
            "timeframe": "15m",
        }


def _row(**kw):
    base = {"ts": "2026-01-01T00:00:00Z", "vol_bucket": "vol_b0",
            "rolling_log_return_vol": 0.0005, "log_return": 0.001}
    base.update(kw)
    return base


def test_audit_passes_on_a_consistent_dataset():
    rows = [_row(), _row(rolling_log_return_vol=0.0015, vol_bucket="vol_b1"),
            _row(rolling_log_return_vol=0.005, vol_bucket="vol_b2")]
    rep = replay.audit_feature_rows(_StubHead(), rows)
    assert rep["ok"] is True
    assert rep["vol_bucket_parity"]["status"] == "checked"
    assert rep["vol_bucket_parity"]["mismatches"] == 0


def test_audit_catches_a_missing_numeric_feature():
    """A missing numeric silently becomes NaN and still scores."""
    rows = [_row(), _row(log_return=None)]
    rep = replay.audit_feature_rows(_StubHead(), rows)
    assert rep["ok"] is False
    assert rep["missing_or_null_by_column"] == {"log_return": 1}


def test_audit_catches_an_unseen_categorical_value():
    """An unseen categorical silently becomes -1 — a category never trained on."""
    rows = [_row(), _row(vol_bucket="vol_b9")]
    rep = replay.audit_feature_rows(_StubHead(), rows)
    assert rep["ok"] is False
    assert rep["unseen_categorical_values"]["vol_bucket"] == {"vol_b9": 1}


def test_audit_catches_vol_bucket_built_with_different_edges_than_the_head_froze():
    """The train/serve parity check, applied to the replay.

    If the dataset's stored ``vol_bucket`` was computed with different quantile
    edges than the head froze, the replay feeds the head a DIFFERENT category
    than the live serve path would — and nothing else in the pipeline notices,
    because the value is a perfectly valid category.
    """
    # vol=0.0005 is below edge 0.001 → frozen edges say vol_b0; dataset says b2.
    rows = [_row(vol_bucket="vol_b2", rolling_log_return_vol=0.0005)]
    rep = replay.audit_feature_rows(_StubHead(), rows)
    assert rep["ok"] is False
    pa = rep["vol_bucket_parity"]
    assert pa["status"] == "checked" and pa["mismatches"] == 1
    assert pa["examples"][0]["dataset_bucket"] == "vol_b2"
    assert pa["examples"][0]["frozen_edge_bucket"] == "vol_b0"


def test_audit_parity_reports_not_checked_rather_than_faking_a_pass():
    """'Could not compare' is its own outcome — never counted as clean."""
    rows = [{"ts": "2026-01-01T00:00:00Z", "vol_bucket": "vol_b0",
             "rolling_log_return_vol": None, "log_return": 0.001}]
    rep = replay.audit_feature_rows(_StubHead(), rows)
    pa = rep["vol_bucket_parity"]
    assert pa["status"] == "not_checked"
    assert "NOT a pass" in pa["reason"]
    assert rep["ok"] is False  # an unrunnable parity check must not pass the audit


def test_audit_uses_the_live_bucketing_function_not_a_reimplementation():
    """Parity must be computed with the same fn the serve path uses."""
    from src.runtime.regime_shadow import bucket_for_vol
    edges, labels = [0.001, 0.002], ["vol_b0", "vol_b1", "vol_b2"]
    # saturation + boundary semantics are the live function's, whatever they are
    assert bucket_for_vol(0.001, edges, labels) == "vol_b0"   # <= is inclusive
    assert bucket_for_vol(0.0011, edges, labels) == "vol_b1"
    assert bucket_for_vol(99.0, edges, labels) == "vol_b2"    # saturates


def test_projection_keeps_ts_plus_only_the_heads_columns(tmp_path):
    """Memory discipline: a wide market_features row must not be materialised whole.

    The BTCUSDT/15m frame is ~100 columns × 175,272 rows in a 480 MB file, and
    the trainer is 1 OCPU / 6 GB with a standing OOM history. A replay that
    OOMs mid-write leaves a TRUNCATED labels file — a present-but-wrong
    artifact, which is worse than no artifact.
    """
    p = tmp_path / "wide.jsonl"
    wide = {"ts": "2026-01-01T00:00:00Z", "vol_bucket": "vol_b0",
            "rolling_log_return_vol": 0.0005, "log_return": 0.001,
            "regime_label": "range", "forward_log_return": 0.02}
    wide.update({f"tsfm_emb_{i}": 0.1 for i in range(40)})
    p.write_text(json.dumps(wide) + "\n", encoding="utf-8")

    got = list(replay.iter_projected_rows(
        p, ["vol_bucket", "rolling_log_return_vol", "log_return"]))
    assert len(got) == 1
    assert set(got[0]) == {"ts", "vol_bucket", "rolling_log_return_vol", "log_return"}
    # the forward label must never ride along — it is the thing being predicted
    assert "forward_log_return" not in got[0]
    assert "regime_label" not in got[0]
    assert not any(k.startswith("tsfm_emb_") for k in got[0])


def test_projection_leaves_a_genuinely_absent_column_detectable():
    """Projection must not MASK a missing feature — the audit still has to see it."""
    head = _StubHead()
    rows = [{"ts": "2026-01-01T00:00:00Z", "vol_bucket": "vol_b0",
             "rolling_log_return_vol": 0.0005}]  # log_return absent entirely
    rep = replay.audit_feature_rows(head, rows)
    assert rep["ok"] is False
    assert rep["missing_or_null_by_column"] == {"log_return": 1}


def test_score_distribution_flags_a_degenerate_head():
    """A near-constant P(volatile) is a scoring bug, not a regime."""
    flat = replay._describe_scores([0.5] * 100)
    assert flat["distinct_values"] == 1
    varied = replay._describe_scores([i / 100 for i in range(100)])
    assert varied["distinct_values"] == 100
    assert varied["median"] == pytest.approx(0.5, abs=0.02)
    assert replay._describe_scores([])["n"] == 0


def test_malformed_labels_line_raises_rather_than_silently_dropping(tmp_path):
    """A holed labels file would grade cells on a partial population."""
    p = tmp_path / "bad.jsonl"
    p.write_text('{"ts": "2026-01-01T00:00:00Z", "vol_regime": "calm"}\nNOT JSON\n',
                 encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        list(replay.iter_dataset_rows(p))
