"""The exit-head artifact records its TRAINING WINDOW, and the replay splits on it.

``BL-20260808-EXIT-HEAD-MANIFEST-RECORDS-NO-TRAINING-WINDOW``.

The motivating incident: the first measured exit-head replay (issue #8653)
reported ``delta_gross_r +10.804`` over 2026-02-09 → 2026-08-07 on n=31 trades,
against a head fitted 2026-07-12. Roughly 5 of those 6 months were at-or-before
fitting time, so the delta is in-sample-dominated and is **not an edge** — but
establishing that required INFERRING the data bound from ``trained_at``, because
the artifact carried no ``train_start``/``train_end``.

``trained_at`` is the wall-clock moment of FITTING. It is not the data bound and
must never stand in for one: a head fitted on a given day could have used six
months or three years of history.

Two contracts are pinned here:
  * the exporter WRITES the window (derived from the rows it already holds), and
    is honest-null with a coverage metric when it cannot;
  * the replay turns it into FIELDS (``in_sample_bars`` / ``forward_bars`` /
    ``forward_trades`` + a forward-only delta) rather than a caveat a reader has
    to remember — the same shape ``/performance`` uses for ``rCoverage``.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# `export_exit_head` imports train_exit_head (lightgbm) at module scope, so the
# window helper is reached via a direct spec load only when that import works.
def _training_window():
    try:
        mod = _load("_export_exit_head_for_tests", "scripts/ml/export_exit_head.py")
    except Exception as exc:                      # lightgbm absent in this env
        pytest.skip(f"export_exit_head not importable here: {exc}")
    return mod.training_window


# --- exporter: the window is DERIVED, not invented -------------------------- #

def test_window_is_the_min_and_max_bar_t_of_the_rows():
    tw = _training_window()
    # 2026-01-01T00:00:00Z .. 2026-01-03T00:00:00Z
    rows = [{"bar_t": 1767225600}, {"bar_t": 1767398400}, {"bar_t": 1767312000}]
    w = tw(rows)
    assert w["train_start"] == "2026-01-01T00:00:00+00:00"
    assert w["train_end"] == "2026-01-03T00:00:00+00:00"
    assert w["train_window_coverage"] == 1.0


def test_window_is_honest_null_when_no_row_carries_a_timestamp():
    """No usable `bar_t` must yield None — never a manufactured date.

    A fabricated bound is worse than a missing one: a consumer cannot tell it
    apart from a measured one, which is the whole defect this row is about.
    """
    tw = _training_window()
    w = tw([{"open_r": 0.2}, {"open_r": 0.4}])
    assert w["train_start"] is None and w["train_end"] is None
    assert w["train_window_coverage"] == 0.0


def test_partial_stamping_is_reported_by_coverage_not_hidden():
    """A half-stamped dataset must not pass as a fully-measured window."""
    tw = _training_window()
    w = tw([{"bar_t": 1767225600}, {"bar_t": None}, {"no": "stamp"},
            {"bar_t": "not-an-int"}])
    assert w["train_start"] == w["train_end"] == "2026-01-01T00:00:00+00:00"
    assert w["train_window_coverage"] == 0.25          # 1 of 4 rows usable


def test_empty_row_set_reports_null_coverage_not_zero():
    """Zero rows is 'nothing to measure', which is not the same as 0% coverage."""
    tw = _training_window()
    assert _training_window()([])["train_window_coverage"] is None
    assert tw([])["train_start"] is None


# --- replay: the split is a FIELD, and the arithmetic is real -------------- #

def _split():
    mod = _load("_exit_head_replay_for_tests", "scripts/ml/exit_head_replay.py")
    return mod.split_in_sample


ART = {"train_start": "2026-02-01T00:00:00+00:00",
       "train_end": "2026-07-12T14:44:43+00:00",
       "train_window_coverage": 1.0, "train_trades": 1662}
BARS = ["2026-06-01T00:00:00Z", "2026-07-12T00:00:00Z", "2026-08-01T00:00:00Z"]
ENTRIES = ["2026-06-15T00:00:00Z", "2026-07-20T00:00:00Z", "2026-08-02T00:00:00Z"]


def test_forward_delta_is_computed_over_forward_trades_only():
    """The headline delta and the forward-only delta must differ here.

    Mirrors the #8653 shape: a big headline number carried mostly by in-sample
    trades. If these two ever came out equal on this fixture the split would be
    doing nothing.
    """
    r = _split()(ART, bar_times=BARS, entry_times=ENTRIES,
                 baseline_rs=[-5.0, -1.0, -0.5], replayed_rs=[2.0, 0.5, 0.25])
    headline = sum([2.0, 0.5, 0.25]) - sum([-5.0, -1.0, -0.5])
    assert headline == 9.25
    assert r["forward_delta_gross_r"] == 2.25      # only the 2 forward trades
    assert r["forward_delta_gross_r"] != headline
    assert r["forward_baseline_gross_r"] == -1.5
    assert r["forward_replayed_gross_r"] == 0.75


def test_bar_and_trade_counts_partition_the_population():
    """in_sample + forward must equal the whole, for both bars and trades."""
    r = _split()(ART, bar_times=BARS, entry_times=ENTRIES,
                 baseline_rs=[0.0] * 3, replayed_rs=[0.0] * 3)
    assert r["in_sample_bars"] + r["forward_bars"] == len(BARS)
    assert r["in_sample_trades"] + r["forward_trades"] == len(ENTRIES)
    assert (r["in_sample_bars"], r["forward_bars"]) == (2, 1)
    assert (r["in_sample_trades"], r["forward_trades"]) == (1, 2)


def test_a_row_exactly_on_train_end_counts_as_IN_sample():
    """Boundary goes to in-sample — the conservative direction.

    Counting the boundary as forward would inflate the out-of-sample population,
    i.e. make the evidence claim stronger than the data supports. Weaker-by-
    default is the only safe way for this to be wrong.
    """
    r = _split()({"train_end": "2026-07-12T00:00:00+00:00"},
                 bar_times=["2026-07-12T00:00:00Z"],
                 entry_times=["2026-07-12T00:00:00Z"],
                 baseline_rs=[3.0], replayed_rs=[9.0])
    assert r["in_sample_bars"] == 1 and r["forward_bars"] == 0
    assert r["in_sample_trades"] == 1 and r["forward_trades"] == 0
    assert r["forward_delta_gross_r"] == 0.0


def test_all_in_sample_yields_a_zero_forward_population_not_the_headline():
    """A fully in-sample window must report n=0 forward, never fall back."""
    r = _split()(ART, bar_times=["2026-03-01T00:00:00Z"],
                 entry_times=["2026-03-02T00:00:00Z"],
                 baseline_rs=[-10.0], replayed_rs=[+10.0])
    assert r["forward_trades"] == 0
    assert r["forward_delta_gross_r"] == 0.0        # not +20.0
    assert r["in_sample_trades"] == 1


def test_pre_fix_artifact_reports_UNKNOWN_rather_than_a_silent_omission():
    """No `train_end` -> every derived field is None and the flag says so.

    'We did not measure this' has to be visible. An absent field reads as
    'nothing to worry about', which is the green-while-measuring-nothing class.
    """
    r = _split()({}, bar_times=BARS, entry_times=ENTRIES,
                 baseline_rs=[0.0] * 3, replayed_rs=[0.0] * 3)
    assert r["train_window_present"] is False
    for k in ("in_sample_bars", "forward_bars", "in_sample_trades",
              "forward_trades", "forward_baseline_gross_r",
              "forward_replayed_gross_r", "forward_delta_gross_r"):
        assert r[k] is None, f"{k} should be None on a pre-fix artifact"


def test_an_unparseable_train_end_is_unknown_not_epoch_zero():
    """A garbled bound must degrade to UNKNOWN, never to 1970 (which would make
    every trade look forward and manufacture a fully out-of-sample claim)."""
    r = _split()({"train_end": "not-a-date"}, bar_times=BARS,
                 entry_times=ENTRIES, baseline_rs=[0.0] * 3,
                 replayed_rs=[0.0] * 3)
    assert r["train_window_present"] is False
    assert r["forward_trades"] is None


def test_trained_at_is_never_substituted_for_the_data_bound():
    """An artifact with only `trained_at` must still report UNKNOWN.

    The two are different quantities and the whole backlog row exists because
    they were conflated. If a future edit reaches for `trained_at` to fill the
    gap, this fails.
    """
    r = _split()({"trained_at": "2026-07-12T14:44:43+00:00"}, bar_times=BARS,
                 entry_times=ENTRIES, baseline_rs=[0.0] * 3,
                 replayed_rs=[0.0] * 3)
    assert r["train_window_present"] is False
    assert r["forward_trades"] is None


def test_the_split_travels_with_the_printed_delta_not_only_the_json():
    """The delta alone is what gets quoted, so the qualifier must be printed."""
    src = open(os.path.join(_REPO, "scripts/ml/exit_head_replay.py"),
               encoding="utf-8").read()
    assert "FORWARD-ONLY gross R" in src
    assert "in-sample split: UNKNOWN" in src
    assert '"in_sample_split"' in src           # and carried in the payload
