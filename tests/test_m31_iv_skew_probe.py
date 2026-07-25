"""M31 Track B — tests for the IV-skew feature core (no network; synthetic chains)."""

from __future__ import annotations

import math
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import iv_skew_probe as isp  # noqa: E402


# ---- synthetic chain builders ---------------------------------------------

def _smile_chain(underlying, expiration, dte, *, put_rich=0.0, base_iv=0.20, curv=0.0):
    """A synthetic single-expiry chain with a controllable smirk + curvature.

    ``put_rich`` lifts downside-strike IV (equity smirk when > 0); ``curv`` lifts
    both wings symmetrically (a smile). Deltas are a monotone proxy so the
    nearest-delta lookups resolve deterministically."""
    rows = []
    for i in range(-4, 5):                         # 9 strikes around spot
        strike = underlying * (1.0 + 0.02 * i)
        m = strike / underlying - 1.0              # signed moneyness
        iv = base_iv - put_rich * m + curv * (m ** 2) * 100.0
        # call + put at each strike; put delta negative, call delta positive,
        # magnitude falling as the strike moves OTM (a clean monotone proxy).
        call_delta = max(0.02, 0.5 - i * 0.1)
        put_delta = -max(0.02, 0.5 + i * 0.1)
        rows.append({"expiration": expiration, "dte": dte, "type": "call",
                     "strike": strike, "iv": iv, "delta": call_delta})
        rows.append({"expiration": expiration, "dte": dte, "type": "put",
                     "strike": strike, "iv": iv, "delta": put_delta})
    return rows


# ---- helpers ---------------------------------------------------------------

def test_clean_rows_drops_bad_and_keeps_good():
    rows = [
        {"type": "call", "strike": 100, "iv": 0.2, "delta": 0.3},
        {"type": "call", "strike": 100, "iv": 0.0, "delta": 0.3},      # iv <= 0
        {"type": "put", "strike": -5, "iv": 0.2, "delta": -0.3},       # strike <= 0
        {"type": "spread", "strike": 100, "iv": 0.2, "delta": 0.3},    # bad type
        {"type": "put", "strike": 100, "iv": "nan", "delta": -0.3},    # non-finite iv
    ]
    clean = isp._clean_rows(rows)
    assert len(clean) == 1 and clean[0]["type"] == "call"


def test_nearest_and_group():
    rows = _smile_chain(100.0, "E1", 10)
    by = isp.group_by_expiration(rows)
    assert set(by.keys()) == {"E1"}
    calls = [r for r in rows if r["type"] == "call"]
    c = isp._nearest(calls, lambda r: r["delta"], 0.25)
    assert abs(c["delta"] - 0.25) <= 0.1                # picks the ~25Δ call


def test_atm_iv_is_the_near_spot_strike():
    rows = _smile_chain(100.0, "E1", 10, put_rich=0.0, base_iv=0.20)
    atm = isp.atm_iv(rows, 100.0)
    assert atm is not None and math.isclose(atm, 0.20, abs_tol=1e-9)   # flat smile → base


def test_ols_slope_sign_and_thin_guard():
    assert isp._ols_slope([0.0, 1.0], [0.0, 1.0]) is None             # < 3 points
    assert isp._ols_slope([1.0, 1.0, 1.0], [0, 1, 2]) is None         # zero x-variance
    s = isp._ols_slope([0.0, 1.0, 2.0], [0.0, 2.0, 4.0])
    assert math.isclose(s, 2.0, rel_tol=1e-9)


# ---- feature semantics -----------------------------------------------------

def test_risk_reversal_negative_when_puts_are_richer():
    # put_rich > 0 lifts downside (put) IV above call IV → RR25 negative.
    rows = _smile_chain(100.0, "E1", 10, put_rich=0.5)
    rr = isp.risk_reversal(rows, target_delta=0.25)
    assert rr is not None and rr < 0


def test_risk_reversal_zero_on_flat_smile():
    rows = _smile_chain(100.0, "E1", 10, put_rich=0.0)
    rr = isp.risk_reversal(rows, target_delta=0.25)
    assert rr is not None and abs(rr) < 1e-9


def test_risk_reversal_none_when_a_wing_missing():
    calls_only = [r for r in _smile_chain(100.0, "E1", 10) if r["type"] == "call"]
    assert isp.risk_reversal(calls_only) is None


def test_butterfly_positive_with_curvature():
    rows = isp._clean_rows(_smile_chain(100.0, "E1", 10, curv=0.5))
    atm = isp.atm_iv(rows, 100.0)
    bf = isp.butterfly(rows, atm, target_delta=0.25)
    assert bf is not None and bf > 0                    # wings above ATM


def test_skew_slope_negative_for_downside_smirk():
    # put_rich > 0 ⇒ lower strikes carry higher IV ⇒ IV falls with strike ⇒ slope < 0.
    rows = isp._clean_rows(_smile_chain(100.0, "E1", 10, put_rich=0.5))
    s = isp.skew_slope(rows, 100.0)
    assert s is not None and s < 0


def test_term_ratio_contango_and_single_expiry_none():
    near = _smile_chain(100.0, "E1", 10, base_iv=0.18)
    far = _smile_chain(100.0, "E2", 90, base_iv=0.24)   # higher far IV → contango
    by = isp.group_by_expiration(isp._clean_rows(near + far))
    ratio = isp.iv_term_ratio(by, 100.0)
    assert ratio is not None and ratio > 1.0
    # single expiry → no term structure
    assert isp.iv_term_ratio(isp.group_by_expiration(isp._clean_rows(near)), 100.0) is None


# ---- aggregate -------------------------------------------------------------

def test_skew_features_bundle_shape():
    near = _smile_chain(100.0, "E1", 10, put_rich=0.5, base_iv=0.18, curv=0.2)
    far = _smile_chain(100.0, "E2", 90, base_iv=0.24)
    feats = isp.skew_features(near + far, 100.0)
    assert feats["n_expirations"] == 2 and feats["n_rows"] == 36
    assert feats["atm_iv"] is not None
    assert feats["rr25"] is not None and feats["rr25"] < 0      # put-rich front
    assert feats["skew_slope"] is not None and feats["skew_slope"] < 0
    assert feats["term_ratio"] is not None and feats["term_ratio"] > 1.0


def test_skew_features_degrades_to_nulls_on_thin_input():
    feats = isp.skew_features([{"type": "call", "strike": 100, "iv": 0.2, "delta": 0.3}], 100.0)
    assert feats["n_rows"] == 1
    assert feats["rr25"] is None                        # no put wing
    assert feats["term_ratio"] is None                  # single expiry
    assert feats["skew_slope"] is None                  # < 3 points
