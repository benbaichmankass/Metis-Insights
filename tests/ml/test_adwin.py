"""Tests for the S-MLOPT-S16 ADWIN drift detector (`ml.shadow.adwin`)."""
from __future__ import annotations

import random

from ml.shadow.adwin import ADWIN, scan_stream


def test_stationary_stream_never_drifts():
    # A tight Gaussian-ish stream around a fixed mean should never trip
    # ADWIN at the default delta. 1500 observations covers any reasonable
    # cadence (the 5m head's per-bar rate over ~5 days).
    rng = random.Random(20260607)
    det = ADWIN()
    drift_count = 0
    for _ in range(1500):
        if det.update(rng.gauss(0.5, 0.02)):
            drift_count += 1
    assert drift_count == 0, "stationary stream tripped ADWIN at default delta"


def test_step_change_triggers_drift():
    # Sharp mean shift halfway through must trip the detector. The
    # post-cut window should retain only the recent regime, so its mean
    # tracks the new mean — not the pre-cut average.
    det = ADWIN()
    pre = [0.10] * 300
    post = [0.90] * 300
    fired_at = None
    for i, x in enumerate(pre + post):
        if det.update(x):
            fired_at = i
            break
    assert fired_at is not None and fired_at >= 300
    # The detector should have forgotten the pre-cut tail and now
    # reflect the post-cut regime.
    assert det.mean > 0.5, (
        f"post-drift mean should reflect new regime, got {det.mean:.3f}"
    )


def test_thin_window_under_min_window_does_not_drift():
    # Below min_window, no cut is even attempted — even a wildly
    # different value at the boundary can't trip the detector. Guards
    # against the noise floor on the first few observations.
    det = ADWIN(min_window=20)
    fired = any(det.update(x) for x in ([0.0] * 10 + [1.0] * 9))
    assert fired is False


def test_max_window_caps_memory():
    det = ADWIN(max_window=50)
    for x in [0.5] * 200:
        det.update(x)
    # The FIFO trim keeps width ≤ max_window; the cap itself isn't a
    # drift signal (the stream is stationary).
    assert det.width <= 50


def test_nan_and_inf_are_skipped_not_observed():
    det = ADWIN()
    before = det.width
    assert det.update(float("nan")) is False
    assert det.update(float("inf")) is False
    assert det.width == before


def test_scan_stream_reports_drift_on_step():
    pre = [0.1] * 200
    post = [0.9] * 200
    event = scan_stream(pre + post, model_id="m")
    assert event.drift_detected is True
    assert event.n_observations == 400
    assert event.last_drift_index > 0
    assert event.n_window_after < 400  # something was dropped
    # The post-drift mean reflects the new regime, not the average over
    # the whole stream.
    assert event.mean_window_after > 0.5


def test_scan_stream_quiet_when_stationary():
    rng = random.Random(0xCAFE)
    values = [rng.gauss(0.4, 0.01) for _ in range(800)]
    event = scan_stream(values, model_id="m")
    assert event.drift_detected is False
    assert event.last_drift_index == -1
    # The full window is retained (after FIFO cap, which 800 < 10k).
    assert event.n_window_after == 800


def test_cut_uses_harmonic_bound_orientation():
    # Manually drive a small example to confirm the bound is direction-
    # agnostic: a step DOWN trips just as a step UP does.
    det = ADWIN(min_window=10)
    for x in [1.0] * 200:
        det.update(x)
    fired = False
    for x in [0.0] * 200:
        if det.update(x):
            fired = True
            break
    assert fired is True
    assert det.mean < 0.5
