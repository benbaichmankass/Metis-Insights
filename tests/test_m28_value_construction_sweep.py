"""M28 — tests for the value-construction sweep (D1/D2/D3 emit through the P4 gate).

Pure/local: exercises the emit + PIT discipline. Grading needs candle CSVs and is
covered by the runner integration (test_thesis_backtest_run.py) — here we assert the
construction emit produces valid, leakage-safe valuation-snapshot rows.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import value_construction_sweep as vcs  # noqa: E402


def _weekly(n, start="2005-01-01"):
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


def _drivers(n=120):
    days = _weekly(n)
    # SPY ERP oscillates (hic=True: higher ERP = cheaper); TLT real_yield ramps (hic=False)
    return {
        ("SPY", "equity_risk_premium"): {
            "series": [(d, float((i % 13) - 6)) for i, d in enumerate(days)],
            "hic": True, "asset_class": "equity"},
        ("TLT", "real_yield_10y"): {
            "series": [(d, 0.5 + 0.01 * i) for i, d in enumerate(days)],
            "hic": False, "asset_class": "bond"},
        ("GLD", "gold_silver_ratio"): {
            "series": [(d, 60.0 + (i % 17)) for i, d in enumerate(days)],
            "hic": True, "asset_class": "commodity"},
        ("SLV", "gold_silver_ratio"): {
            "series": [(d, 60.0 + (i % 17)) for i, d in enumerate(days)],
            "hic": True, "asset_class": "commodity"},
    }


def test_load_value_drivers_reads_backfill_and_filters_tradeable(tmp_path):
    fp = tmp_path / "bf.jsonl"
    rows = [
        {"symbol": "SPY", "metric": "equity_risk_premium", "value": 1.2,
         "higher_is_cheaper": True, "observed_at": "2005-01-01", "asset_class": "equity"},
        {"symbol": "SPY", "metric": "equity_risk_premium", "value": 1.3,
         "higher_is_cheaper": True, "observed_at": "2005-01-08", "asset_class": "equity"},
        {"symbol": "credit_risk", "metric": "credit_spread", "value": 3.0,
         "higher_is_cheaper": True, "observed_at": "2005-01-01", "asset_class": "macro"},
        {"symbol": "TLT", "metric": "real_yield_10y", "value": None,
         "higher_is_cheaper": False, "observed_at": "2005-01-01", "asset_class": "bond"},
    ]
    fp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    drivers = vcs.load_value_drivers(str(fp))
    # credit_risk excluded (not tradeable); null-value TLT row dropped → TLT absent
    assert ("SPY", "equity_risk_premium") in drivers
    assert ("credit_risk", "credit_spread") not in drivers
    d = drivers[("SPY", "equity_risk_premium")]
    assert d["hic"] is True and d["asset_class"] == "equity"
    assert d["series"] == [("2005-01-01", 1.2), ("2005-01-08", 1.3)]


def test_emit_produces_all_constructions_with_valid_schema():
    out = vcs.emit_value_constructions(_drivers(), lookback=52, min_history=30)
    assert set(out) == set(vcs.D1_D2_CONSTRUCTIONS)
    required = ("symbol", "metric", "cheap_score", "percentile", "n_history",
                "higher_is_cheaper", "observed_at", "as_of", "label", "source")
    for name, rows in out.items():
        assert rows, f"{name} emitted no rows"
        r = rows[0]
        for k in required:
            assert k in r, f"{name} row missing {k}"
        assert 0.0 <= r["cheap_score"] <= 1.0
        assert r["metric"].endswith(name) or name == "level_x_turning"


def test_level_x_turning_neutralizes_some_rows():
    out = vcs.emit_value_constructions(_drivers(), lookback=52, min_history=30)
    cond = out["level_x_turning"]
    # conditioning must pull some rows to the neutral 0.5 / "fair"
    neutralized = [r for r in cond if r["cheap_score"] == 0.5 and r["label"] == "fair"]
    passed = [r for r in cond if r.get("inputs", {}).get("conditioned", {}).get("passed")]
    assert neutralized, "expected some rows neutralized by the turning gate"
    assert passed, "expected some rows to pass the turning gate"


def test_xsec_construction_ranks_across_symbols_per_date():
    xsec = vcs.emit_xsec_construction(_drivers(), lookback=52, min_history=30)
    assert xsec, "xsec emitted no rows"
    r = xsec[0]
    assert r["metric"] == "value_xsec"
    assert "cross_section_n" in r["inputs"]
    # a real cross-section has >= min_symbols distinct symbols on a scored date
    by_date = {}
    for row in xsec:
        by_date.setdefault(row["as_of"], set()).add(row["symbol"])
    assert any(len(s) >= 3 for s in by_date.values())


def test_emit_is_leakage_safe_prefix_stable():
    """A row's cheap_score for an early date must not change when future data is appended
    (trailing-window PIT). Emit on a prefix and on the full series; the overlapping
    early rows must be byte-identical."""
    full = _drivers(120)
    prefix = {k: {**v, "series": v["series"][:80]} for k, v in full.items()}
    out_full = vcs.emit_value_constructions(full, lookback=52, min_history=30)
    out_prefix = vcs.emit_value_constructions(prefix, lookback=52, min_history=30)
    for name in ("level", "change", "detrend"):
        pref_by_key = {(r["symbol"], r["metric"], r["as_of"]): r["cheap_score"] for r in out_prefix[name]}
        full_by_key = {(r["symbol"], r["metric"], r["as_of"]): r["cheap_score"] for r in out_full[name]}
        overlap = set(pref_by_key) & set(full_by_key)
        assert overlap, f"{name}: no overlapping rows to compare"
        for k in overlap:
            assert abs(pref_by_key[k] - full_by_key[k]) < 1e-12, f"{name} leaked future data at {k}"
