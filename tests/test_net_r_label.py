"""Tests for the M24 P1 net-of-cost R label pipeline (src/runtime/net_r_label.py)."""
from __future__ import annotations

from src.runtime.net_r_label import (
    net_r_coverage,
    net_r_for_trade,
    risk_usd_for_trade,
)


def _trade(**overrides):
    """A resolved, broker-costed, risk-computable closed-trade row.

    Defaults: long BTCUSDT, entry 100 / stop 90 / qty 2 → risk = 10*2*1 = 20.
    """
    base = {
        "id": 1,
        "strategy": "trend_donchian",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "position_size": 2.0,
        "gross_pnl": 40.0,
        "fee_taker_usd": 0.0,
        "fee_maker_usd": 0.0,
        "funding_paid_usd": 0.0,
        "cost_source": "broker",
    }
    base.update(overrides)
    return base


def test_risk_usd_basic_and_missing():
    assert risk_usd_for_trade(_trade()) == 20.0  # |100-90| * 2 * 1.0
    # contract_value multiplier applied
    assert risk_usd_for_trade(_trade(contract_value_usd=5.0)) == 100.0
    # missing stop → uncomputable
    assert risk_usd_for_trade(_trade(stop_loss=None)) is None
    # entry == stop → risk 0 → None (never a fabricated basis)
    assert risk_usd_for_trade(_trade(stop_loss=100.0)) is None


def test_clean_broker_costed_winner():
    # gross 40, taker 1.0, maker 0.5, funding 0.3 → net 38.2; risk 20 → net_R 1.91
    t = _trade(gross_pnl=40.0, fee_taker_usd=1.0, fee_maker_usd=0.5,
               funding_paid_usd=0.3, cost_source="broker")
    r = net_r_for_trade(t)
    assert r is not None
    assert r["trade_id"] == 1
    assert abs(r["net_pnl_usd"] - 38.2) < 1e-9
    assert r["risk_usd"] == 20.0
    assert abs(r["net_R"] - 1.91) < 1e-9
    assert r["cost_source"] == "broker"
    assert r["costed"] is True


def test_estimate_costed_loser():
    # A losing trade with an estimate cost_source. gross -20, taker 0.8 → net -20.8
    t = _trade(id=7, gross_pnl=-20.0, fee_taker_usd=0.8, cost_source="estimate")
    r = net_r_for_trade(t)
    assert r is not None
    assert r["trade_id"] == 7
    assert abs(r["net_pnl_usd"] - (-20.8)) < 1e-9
    assert abs(r["net_R"] - (-1.04)) < 1e-9   # -20.8 / 20
    assert r["cost_source"] == "estimate"
    assert r["costed"] is True


def test_funding_included_in_net():
    # Funding dominates a thin-edge perp: gross 2.0, funding 1.5 → net 0.5 → 0.025R
    t = _trade(gross_pnl=2.0, funding_paid_usd=1.5, cost_source="broker")
    r = net_r_for_trade(t)
    assert abs(r["net_pnl_usd"] - 0.5) < 1e-9
    assert abs(r["net_R"] - 0.025) < 1e-9


def test_missing_stop_returns_none():
    # No risk basis → net_R uncomputable → None (never a raw-pnl fallback).
    assert net_r_for_trade(_trade(stop_loss=None)) is None
    assert net_r_for_trade(_trade(position_size=None)) is None
    assert net_r_for_trade(_trade(entry_price=None)) is None


def test_missing_gross_pnl_returns_none():
    # Unresolved trade (no pnl) → uncomputable even though risk is fine.
    assert net_r_for_trade(_trade(gross_pnl=None, pnl=None)) is None


def test_pnl_fallback_when_gross_absent():
    # gross_pnl absent → falls back to `pnl`.
    t = _trade()
    del t["gross_pnl"]
    t["pnl"] = 30.0
    r = net_r_for_trade(t)
    assert abs(r["net_pnl_usd"] - 30.0) < 1e-9
    assert abs(r["net_R"] - 1.5) < 1e-9


def test_null_cost_source_is_uncosted():
    # cost_source null → costs still default to 0, but costed=False and the
    # emitted cost_source is None (never silently marked costed).
    t = _trade(cost_source=None, fee_taker_usd=5.0)
    r = net_r_for_trade(t)
    assert r is not None
    assert r["costed"] is False
    assert r["cost_source"] is None
    # fee still subtracted (missing-column-treated-as-0 rule only zeroes ABSENT
    # numeric cost columns; a present fee is honoured regardless of source label)
    assert abs(r["net_pnl_usd"] - 35.0) < 1e-9


def test_unknown_cost_source_is_uncosted():
    r = net_r_for_trade(_trade(cost_source="banana"))
    assert r is not None
    assert r["costed"] is False
    assert r["cost_source"] is None


def test_coverage_report_buckets_and_cells():
    trades = [
        # broker-costed BTC (trend_donchian) — computable
        _trade(id=1, strategy="trend_donchian", symbol="BTCUSDT", cost_source="broker"),
        # estimate-costed BTC (same cell) — computable
        _trade(id=2, strategy="trend_donchian", symbol="BTC/USDT:USDT", cost_source="estimate"),
        # uncosted BTC (same cell, null source) — computable
        _trade(id=3, strategy="trend_donchian", symbol="BTCUSDT", cost_source=None),
        # r-uncomputable BTC (same cell, no stop) — dropped from R
        _trade(id=4, strategy="trend_donchian", symbol="BTCUSDT", stop_loss=None),
        # a different cell: broker-costed MES / squeeze
        _trade(id=5, strategy="squeeze_breakout", symbol="MES", cost_source="broker"),
    ]
    rep = net_r_coverage(trades)

    assert rep["total"] == 5
    assert rep["broker_costed"] == 2
    assert rep["estimate_costed"] == 1
    assert rep["uncosted"] == 1
    assert rep["r_uncomputable"] == 1

    # Two cells, sorted by (strategy, symbol): squeeze/MES then trend/BTCUSDT.
    cells = rep["by_cell"]
    assert len(cells) == 2
    by_key = {(c["strategy"], c["symbol"]): c for c in cells}

    btc = by_key[("trend_donchian", "BTCUSDT")]  # BTC/USDT:USDT folds in
    assert btc["total"] == 4
    assert btc["broker_costed"] == 1
    assert btc["estimate_costed"] == 1
    assert btc["uncosted"] == 1
    assert btc["r_uncomputable"] == 1

    mes = by_key[("squeeze_breakout", "MES")]
    assert mes["total"] == 1
    assert mes["broker_costed"] == 1


def test_coverage_empty():
    rep = net_r_coverage([])
    assert rep["total"] == 0
    assert rep["by_cell"] == []
