"""M31 Track B — tests for the Schwab /chains → normalized-rows adapter (offline)."""

from __future__ import annotations

import os
import sys

import pytest

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import iv_skew_probe as isp  # noqa: E402
import schwab_chain_adapter as sca  # noqa: E402


def _contract(put_call, strike, vol, delta, dte=21):
    return {"putCall": put_call, "strikePrice": strike, "volatility": vol,
            "delta": delta, "daysToExpiration": dte}


def _schwab_payload():
    """A synthetic Schwab /chains payload: 2 expiries, calls+puts, one -999 junk row."""
    return {
        "symbol": "SPY", "status": "SUCCESS", "underlyingPrice": 500.0,
        "callExpDateMap": {
            "2026-08-15:21": {
                "490.0": [_contract("CALL", 490.0, 20.0, 0.62)],
                "500.0": [_contract("CALL", 500.0, 18.0, 0.50)],
                "510.0": [_contract("CALL", 510.0, 17.0, 0.30)],
                "999.0": [_contract("CALL", 999.0, -999.0, -999.0)],   # illiquid sentinel → dropped
            },
            "2026-11-15:113": {
                "500.0": [_contract("CALL", 500.0, 22.0, 0.50, dte=113)],
            },
        },
        "putExpDateMap": {
            "2026-08-15:21": {
                "490.0": [_contract("PUT", 490.0, 24.0, -0.30)],
                "500.0": [_contract("PUT", 500.0, 18.0, -0.50)],
                "510.0": [_contract("PUT", 510.0, 16.0, -0.62)],
            },
            "2026-11-15:113": {
                "500.0": [_contract("PUT", 500.0, 22.0, -0.50, dte=113)],
            },
        },
    }


def test_parse_maps_fields_and_converts_percent_to_fraction():
    parsed = sca.parse_schwab_chain(_schwab_payload())
    assert parsed["underlying"] == 500.0 and parsed["symbol"] == "SPY"
    # 9 contracts (4 front-call + 1 far-call + 3 front-put + 1 far-put) minus the
    # 1 dropped -999 sentinel = 8 real rows
    assert len(parsed["rows"]) == 8
    atm_call = next(r for r in parsed["rows"]
                    if r["type"] == "call" and r["strike"] == 500.0 and r["expiration"] == "2026-08-15")
    assert atm_call["iv"] == 0.18                       # 18.0 percent → 0.18 fraction
    assert atm_call["dte"] == 21 and atm_call["delta"] == 0.50


def test_parse_drops_minus999_sentinels():
    parsed = sca.parse_schwab_chain(_schwab_payload())
    assert all(r["iv"] > 0 for r in parsed["rows"])
    assert not any(r["strike"] == 999.0 for r in parsed["rows"])


def test_parse_uses_clean_date_from_map_key():
    parsed = sca.parse_schwab_chain(_schwab_payload())
    assert {r["expiration"] for r in parsed["rows"]} == {"2026-08-15", "2026-11-15"}


def test_parse_missing_side_and_bad_payload_are_safe():
    # only calls present → still parses the call side, no crash on the missing put map
    only_calls = {"underlyingPrice": 100.0,
                  "callExpDateMap": {"2026-08-15:21": {"100.0": [_contract("CALL", 100.0, 20.0, 0.5)]}}}
    p = sca.parse_schwab_chain(only_calls)
    assert len(p["rows"]) == 1 and p["rows"][0]["type"] == "call"
    # non-dict payload degrades to an empty envelope
    empty = sca.parse_schwab_chain(None)
    assert empty["rows"] == [] and empty["underlying"] is None


def test_end_to_end_parsed_rows_feed_skew_features():
    parsed = sca.parse_schwab_chain(_schwab_payload())
    feats = isp.skew_features(parsed["rows"], parsed["underlying"])
    assert feats["n_expirations"] == 2
    assert feats["atm_iv"] is not None
    # front-expiry put IV (24 at the -0.30Δ put) > call IV (17 at the 0.30Δ call)
    # ⇒ 25Δ risk reversal negative (puts richer — the equity smirk)
    assert feats["rr25"] is not None and feats["rr25"] < 0
    # far ATM IV (22) > near ATM IV (18) ⇒ contango
    assert feats["term_ratio"] is not None and feats["term_ratio"] > 1.0


def test_fetch_chain_is_credential_gated():
    with pytest.raises(RuntimeError, match="credential-gated"):
        sca.fetch_chain("SPY")                          # no access_token → clear error
    with pytest.raises(RuntimeError, match="no http_get"):
        sca.fetch_chain("SPY", access_token="tok")      # token but no injected getter
