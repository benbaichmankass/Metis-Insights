"""M28 Phase B — tests for the construction sweep engine (emit variants + grade each)."""

from __future__ import annotations

import datetime as dt
import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import construction_sweep as cs  # noqa: E402


def _weekly(n, start="2020-01-07"):
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


def test_emit_constructions_produces_variants_with_valid_schema():
    days = _weekly(80)
    primary = list(zip(days, [float(i % 17) for i in range(80)]))     # oscillating spec_net
    secondary = list(zip(days, [float((i + 8) % 17) for i in range(80)]))  # phase-shifted comm_net
    out = cs.emit_constructions("USO", primary, secondary=secondary, asset_class="commodity",
                                lookback=52, min_history=30, higher_is_cheaper=False, metric="cot")
    assert set(out) == {"level", "change", "divergence", "detrend"}
    for name, rows in out.items():
        assert rows, f"{name} emitted no rows"
        r = rows[0]
        assert r["metric"] == f"cot_{name}" and r["symbol"] == "USO"
        for k in ("cheap_score", "percentile", "n_history", "higher_is_cheaper", "observed_at", "as_of"):
            assert k in r
        assert r["higher_is_cheaper"] is False


def test_emit_skips_divergence_without_secondary():
    days = _weekly(60)
    primary = list(zip(days, [float(i % 11) for i in range(60)]))
    out = cs.emit_constructions("GLD", primary, secondary=None, lookback=52, min_history=30)
    assert "divergence" not in out and "level" in out and "change" in out


def test_merge_by_construction_combines_symbols():
    a = {"level": [{"symbol": "USO"}], "change": [{"symbol": "USO"}]}
    b = {"level": [{"symbol": "GLD"}]}
    merged = cs.merge_by_construction([a, b])
    assert len(merged["level"]) == 2 and len(merged["change"]) == 1


def test_cot_construction_snapshots_maps_proxies():
    days = _weekly(70)
    rows = [{"date": d, "spec_long": 100 + i, "spec_short": 50, "comm_long": 40, "comm_short": 80 + i}
            for i, d in enumerate(days)]
    markets = {"067651": rows}                       # crude → USO
    got = cs.cot_construction_snapshots(markets, {"067651": "USO"},
                                        {"067651": "commodity"}, lookback=52, min_history=30)
    assert "level" in got and "divergence" in got
    assert all(r["symbol"] == "USO" for r in got["level"])


def test_cot_cross_sectional_snapshots_ranks_across_markets():
    days = _weekly(90)
    # two markets whose spec_net z-scores diverge → a real cross-section per date
    crude = [{"date": d, "spec_long": 100 + i, "spec_short": 50, "comm_long": 40, "comm_short": 80}
             for i, d in enumerate(days)]
    gold = [{"date": d, "spec_long": 100 - i, "spec_short": 50, "comm_long": 40, "comm_short": 80}
            for i, d in enumerate(days)]
    copper = [{"date": d, "spec_long": 100 + (i % 7), "spec_short": 50, "comm_long": 40, "comm_short": 80}
              for i, d in enumerate(days)]
    markets = {"067651": crude, "088691": gold, "085692": copper}
    proxy = {"067651": "USO", "088691": "GLD", "085692": "CPER"}
    acls = {"067651": "commodity", "088691": "commodity", "085692": "commodity"}
    got = cs.cot_cross_sectional_snapshots(markets, proxy, acls, lookback=52, min_history=30)
    assert "xsec" in got and got["xsec"], "no cross-sectional rows"
    r = got["xsec"][0]
    assert r["metric"] == "cot_xsec" and r["symbol"] in {"USO", "GLD", "CPER"}
    assert r["higher_is_cheaper"] is False
    for k in ("cheap_score", "percentile", "n_history", "observed_at", "as_of"):
        assert k in r
    assert 0.0 <= r["cheap_score"] <= 1.0
    # each scored date must carry ≥ min_symbols (3) constituents (a real cross-section)
    assert all(row["n_history"] >= 3 for row in got["xsec"])


def test_cot_cross_sectional_skips_thin_cross_section():
    days = _weekly(70)
    # only ONE market reports → below min_symbols=3, no cross-section emitted
    only = [{"date": d, "spec_long": 100 + i, "spec_short": 50, "comm_long": 40, "comm_short": 80}
            for i, d in enumerate(days)]
    got = cs.cot_cross_sectional_snapshots({"067651": only}, {"067651": "USO"},
                                           {"067651": "commodity"}, lookback=52, min_history=30)
    assert got["xsec"] == []


def test_crypto_conditioning_produces_base_and_conditioned():
    days = [f"2024-{m:02d}-{d:02d}" for m in range(1, 4) for d in range(1, 29)]  # ~84 daily
    # funding oscillates; OI trends up then down so the rising-OI gate is sometimes True
    funding = {"BTCUSDT": list(zip(days, [0.0001 * ((i % 13) - 6) for i in range(len(days))]))}
    oi = {"BTCUSDT": list(zip(days, [1e6 + 1e4 * (i if i < 40 else 80 - i) for i in range(len(days))]))}
    out = cs.crypto_conditioning_snapshots(funding, oi, lookback=52, min_history=20)
    assert set(out) == {"funding_level", "funding_level_x_oi_rising",
                        "funding_impulse", "funding_impulse_x_oi_rising"}
    assert out["funding_level"] and out["funding_impulse"], "no base rows"
    assert out["funding_level"][0]["metric"] == "crypto_funding_level"
    assert out["funding_impulse"][0]["metric"] == "crypto_funding_impulse"
    assert all(r["higher_is_cheaper"] is False for r in out["funding_level"])
    # each conditioned set has the SAME row count as its base (gating neutralizes, never drops)
    assert len(out["funding_level_x_oi_rising"]) == len(out["funding_level"])
    assert len(out["funding_impulse_x_oi_rising"]) == len(out["funding_impulse"])
    # a neutralized row is pulled to cheap_score 0.5; a passed row keeps a real conviction
    conds = out["funding_level_x_oi_rising"]
    assert any(r["cheap_score"] == 0.5 for r in conds), "no rows neutralized by the OI gate"
    assert any(r["cheap_score"] != 0.5 for r in conds), "no rows passed the OI gate"
    # the gate outcome is traceable in inputs
    assert all("conditioned" in (r.get("inputs") or {}) for r in conds)


def test_crypto_conditioning_skips_symbol_without_funding():
    out = cs.crypto_conditioning_snapshots({"ETHUSDT": []}, {"ETHUSDT": [("2024-01-01", 1e6)]},
                                           lookback=52, min_history=20)
    assert out["funding_impulse"] == [] and out["funding_impulse_x_oi_rising"] == []


def test_grade_constructions_rollup(monkeypatch):
    # stub grade_construction.grade so the test stays offline (no candle loaders)
    import grade_construction as gc

    def fake_grade(records, price_at, **kw):
        wb = records[0].get("symbol") == "WIN"
        return {"verdict": "worth_building" if wb else "no_edge", "worth_building": wb,
                "s2_signal": {}, "s3_pnl": {}, "meta": {}}

    monkeypatch.setattr(gc, "grade", fake_grade)
    constructions = {"level": [{"symbol": "LOSE"}], "change": [{"symbol": "WIN"}], "empty": []}
    out = cs.grade_constructions(constructions, price_at=None, cfg={}, rebalance_every=7,
                                 horizons=[30], pnl_horizon=30)
    assert out["change"]["worth_building"] is True
    assert out["level"]["worth_building"] is False
    assert out["empty"]["verdict"] == "no_data"
    assert out["_sweep"]["worth_building"] == ["change"] and out["_sweep"]["any_worth_building"] is True
