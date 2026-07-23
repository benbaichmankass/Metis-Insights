"""M28 Phase B / M29 bridge — tests for the sysdyn mispricing→signal construction."""

from __future__ import annotations

import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import sysdyn_mispricing as sm  # noqa: E402
from src.sysdyn.seed_gas import DEFAULT_PARAMS, _price_from_storage  # noqa: E402


def test_model_implied_price_matches_seed_readout():
    # the bridge must reuse the seed model's readout verbatim (live == model)
    for storage in (1000.0, 2000.0, 3000.0):
        assert sm.model_implied_price(storage, DEFAULT_PARAMS) == _price_from_storage(storage, DEFAULT_PARAMS)
    # at storage_normal the model prices at base_price
    assert math.isclose(sm.model_implied_price(DEFAULT_PARAMS["storage_normal"], DEFAULT_PARAMS),
                        DEFAULT_PARAMS["base_price"])


def test_mispricing_series_only_common_dates_and_sorted():
    storage = [("2020-01-08", 2000.0), ("2020-01-01", 1800.0), ("2020-01-15", 2200.0)]
    price = [("2020-01-01", 3.5), ("2020-01-08", 3.0)]  # no 01-15 price
    out = sm.gas_mispricing_series(storage, price, DEFAULT_PARAMS)
    dates = [d for d, _ in out]
    assert dates == ["2020-01-01", "2020-01-08"]  # common-dates only + sorted


def test_mispricing_relative_vs_absolute_and_orientation():
    # at storage_normal model==base_price==3.0; a market ABOVE fair is a POSITIVE mispricing
    storage = [("2020-01-01", DEFAULT_PARAMS["storage_normal"])]
    rich = sm.gas_mispricing_series(storage, [("2020-01-01", 3.6)], DEFAULT_PARAMS, relative=True)
    cheap = sm.gas_mispricing_series(storage, [("2020-01-01", 2.4)], DEFAULT_PARAMS, relative=True)
    assert rich[0][1] > 0    # market above fair value = rich
    assert cheap[0][1] < 0   # market below fair value = cheap
    # relative divides by model; absolute is the raw diff
    absolute = sm.gas_mispricing_series(storage, [("2020-01-01", 3.6)], DEFAULT_PARAMS, relative=False)
    assert math.isclose(absolute[0][1], 3.6 - 3.0)
    assert math.isclose(rich[0][1], (3.6 - 3.0) / 3.0)


def test_mispricing_relative_guards_nonpositive_model():
    # a params set that could drive model<=0 must be skipped, not divide-by-zero
    bad = dict(DEFAULT_PARAMS, base_price=0.0)
    out = sm.gas_mispricing_series([("2020-01-01", 2000.0)], [("2020-01-01", 3.0)], bad, relative=True)
    assert out == []


def test_emit_mispricing_snapshots_valid_schema_and_orientation():
    # a rising storage draw → falling model price → market progressively richer/cheaper;
    # just assert the schema + the contrarian orientation flag.
    storage = [(f"2020-{m:02d}-01", 2000.0 + 10.0 * i) for i, m in enumerate(range(1, 13))]
    price = [(f"2020-{m:02d}-01", 3.0 + 0.05 * ((i % 5) - 2)) for i, m in enumerate(range(1, 13))]
    rows = sm.emit_mispricing_snapshots(storage, price, lookback=52, min_history=3)
    assert rows, "no snapshots emitted"
    r = rows[0]
    assert r["symbol"] == "UNG" and r["metric"] == "sysdyn_gas_mispricing"
    assert r["asset_class"] == "commodity"
    assert r["higher_is_cheaper"] is False
    for k in ("cheap_score", "percentile", "n_history", "observed_at", "as_of", "z_score", "source"):
        assert k in r
    assert 0.0 <= r["cheap_score"] <= 1.0


def test_emit_defaults_to_seed_params_when_none():
    storage = [(f"2020-{m:02d}-01", 1900.0 + 5.0 * i) for i, m in enumerate(range(1, 9))]
    price = [(f"2020-{m:02d}-01", 3.0 + 0.02 * i) for i, m in enumerate(range(1, 9))]
    rows_none = sm.emit_mispricing_snapshots(storage, price, params=None, min_history=3)
    rows_default = sm.emit_mispricing_snapshots(storage, price, params=DEFAULT_PARAMS, min_history=3)
    assert [r["value"] for r in rows_none] == [r["value"] for r in rows_default]


def test_load_calibrated_params_falls_back_and_overrides(tmp_path):
    import json

    # missing path → seed constants verbatim
    assert sm.load_calibrated_params(str(tmp_path / "nope.json")) == dict(DEFAULT_PARAMS)
    assert sm.load_calibrated_params(None) == dict(DEFAULT_PARAMS)

    # a real scorecard shape overrides fixed_params + train_holdout.params, keeps the rest
    card = {"fixed_params": {"storage_normal": 2766.9},
            "train_holdout": {"params": {"price_k": 0.1, "base_price": 3.37}}}
    p = tmp_path / "card.json"
    p.write_text(json.dumps(card))
    got = sm.load_calibrated_params(str(p))
    assert got["storage_normal"] == 2766.9 and got["price_k"] == 0.1 and got["base_price"] == 3.37
    assert got["inj_rate"] == DEFAULT_PARAMS["inj_rate"]  # untouched key stays seed

    # garbled JSON → seed fallback, never raises
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert sm.load_calibrated_params(str(bad)) == dict(DEFAULT_PARAMS)
