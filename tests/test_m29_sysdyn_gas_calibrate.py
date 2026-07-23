"""M29 P1b — tests for the gas-seed calibration runner + scorecard (no network).

The load-bearing check is a synthetic round-trip: generate a price series FROM the
seed model (known params, calendar exog) and confirm the runner reproduces it
out-of-sample and emits a well-formed scorecard. Individual-param identifiability
(esp. the weakly-identified price_feedback) is intentionally NOT over-asserted —
that equifinality is exactly what the stability block is there to MEASURE, not a
property the round-trip must exhibit.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import sysdyn_gas_calibrate as gc  # noqa: E402
import sysdyn_gas_data as gd  # noqa: E402
from src.sysdyn.engine import simulate  # noqa: E402
from src.sysdyn.seed_gas import (  # noqa: E402
    DEFAULT_PARAMS,
    build_gas_storage_model,
    price_series,
)


def _synthetic_weekly_price(truth, weeks=160, start=dt.date(2019, 1, 4)):
    dates = [(start + dt.timedelta(days=7 * i)).isoformat() for i in range(weeks)]
    exog = gd.calendar_exog(dates)                 # same driver the runner rebuilds
    model = build_gas_storage_model(initial_storage=DEFAULT_PARAMS["storage_normal"])
    observed = list(price_series(simulate(model, truth, exog, weeks, dt=1.0)))
    return list(zip(dates, observed))


def test_synthetic_round_trip_fits_out_of_sample():
    truth = {**DEFAULT_PARAMS, "inj_rate": 90.0, "wd_rate": 14.0, "price_k": 1.8,
             "price_feedback": 0.6, "base_price": 3.0}
    dated = _synthetic_weekly_price(truth)

    card = gc.run_calibration(
        dated_price=dated, window_years=None, holdout_frac=0.25, n_folds=3,
        generated_at="2026-07-23T00:00:00Z",
    )

    assert "error" not in card
    th = card["train_holdout"]
    # The calibrated model reproduces price it never saw (the honest OOS check).
    assert th["oos_r2"] is not None and th["oos_r2"] > 0.9
    assert th["in_sample_r2"] is not None and th["in_sample_r2"] > 0.99
    # price_k scales the price readout directly → recovered close on synthetic data.
    assert th["params"]["price_k"] == pytest.approx(1.8, rel=0.2)
    # base_price (freed level anchor) recovers the truth reference.
    assert th["params"]["base_price"] == pytest.approx(3.0, rel=0.2)
    # stability block is populated + judged on the structural params only.
    st = card["stability"]
    assert st["structural_max_rel_spread"] is not None
    assert set(st["param_rel_spread"]) >= set(gc.STRUCTURAL_FREE_PARAMS)
    assert card["verdict"]["label"] in {
        "identifiable_seasonal_edge", "identifiable_no_oos_edge",
        "oos_edge_but_equifinal", "equifinal_no_edge",
    }
    assert card["generated_at"] == "2026-07-23T00:00:00Z"
    assert card["target"].startswith("weekly_henry_hub_price_")


def test_thin_data_returns_error_envelope():
    dated = [("2020-01-03", 2.1), ("2020-01-10", 2.2), ("2020-01-17", 2.0)]
    card = gc.run_calibration(dated_price=dated, n_folds=4)
    assert "error" in card and card["n_obs"] == 3
    assert "train_holdout" not in card          # short-circuits before the fit


def test_base_price_excluded_from_identifiability_verdict():
    # base_price is in the free set but must NOT be in the structural spread judged.
    truth = {**DEFAULT_PARAMS, "inj_rate": 80.0, "wd_rate": 12.0, "price_k": 1.5,
             "price_feedback": 0.5, "base_price": 3.0}
    card = gc.run_calibration(dated_price=_synthetic_weekly_price(truth), window_years=None, n_folds=3)
    assert "base_price" in card["free_params"]
    assert "base_price" not in gc.STRUCTURAL_FREE_PARAMS
    # the structural spread key set never includes base_price
    structural = {k: card["stability"]["param_rel_spread"][k] for k in gc.STRUCTURAL_FREE_PARAMS}
    assert "base_price" not in structural
