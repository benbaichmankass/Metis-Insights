"""Tests for the cost-aware EV Monte-Carlo (src/prop/montecarlo.run_ev_montecarlo).

Deterministic (fixed seed). Each test feeds a SYNTHETIC ledger with a KNOWN
edge and asserts the EV aggregate behaves as the economics demand:

  * positive-edge → mean net $ > 0, P(net>0) high, ~1 account (rarely breaches),
    and banked profit grows with the horizon (bank-ASAP compounding payouts).
  * negative-edge → mean net $ < 0, MULTIPLE accounts burned (re-buy on breach),
    ROI-on-fees == -1.0 (the whole fee outlay is lost).
  * the economics block (fee / payout cadence / withdrawal policy) round-trips
    through the ruleset parser.

This is the metric that credits "burns an account fast but banks > its fee
first" — survival is NOT the objective here, realised $ is.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List

from src.prop.montecarlo import run_ev_montecarlo
from src.prop.ruleset import parse_ruleset


@dataclass
class _FakeTrade:
    pnl: float
    entry_ts: Any
    exit_ts: Any


def _ruleset(account_size=5000.0, target=0.10, daily=0.03, max_dd=0.06,
             fee=45.0, rebuy=45.0, split=0.80, first_payout=14.0, freq=7.0, min_wd=50.0):
    return parse_ruleset(
        {
            "ruleset": "test",
            "account_size_usd": account_size,
            "profit_split": split,
            "phases": {"evaluation": {"profit_target_pct": target, "min_trading_days": 0}},
            "limits": {
                "daily_loss_pct": daily,
                "max_drawdown_pct": max_dd,
                "drawdown_type": "static",
            },
            "funded_soak_days": 30,
            "economics": {
                "account_fee_usd": fee,
                "rebuy_fee_usd": rebuy,
                "payout": {
                    "first_payout_after_days": first_payout,
                    "payout_frequency_days": freq,
                    "min_withdrawal_usd": min_wd,
                },
                "withdrawal_policy": {"mode": "above_start", "buffer_usd": 0.0,
                                      "bank_asap": True, "cadence_days": freq},
            },
        }
    )


def _ledger(r_pattern: List[float], n: int, *, base_risk_usd=25.0, gap_hours=24.0):
    """Ledger of `n` trades cycling `r_pattern` (R-multiples), one per `gap_hours`.

    pnl = R * base_risk_usd; with account 5000 @ base_risk 0.5% → risk_usd 25,
    so the module backs out exactly these R-multiples.
    """
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: List[_FakeTrade] = []
    for i in range(n):
        r = r_pattern[i % len(r_pattern)]
        exit_dt = t0 + timedelta(hours=gap_hours * (i + 1))
        out.append(_FakeTrade(pnl=r * base_risk_usd,
                              entry_ts=(exit_dt - timedelta(hours=1)).isoformat(),
                              exit_ts=exit_dt.isoformat()))
    return out


def test_economics_block_parses():
    rs = _ruleset(fee=45.0, rebuy=30.0, split=0.90, first_payout=10.0, freq=5.0, min_wd=25.0)
    assert rs.economics.account_fee_usd == 45.0
    assert rs.economics.rebuy_fee_usd == 30.0
    assert rs.profit_split == 0.90
    assert rs.economics.payout.first_payout_after_days == 10.0
    assert rs.economics.payout.payout_frequency_days == 5.0
    assert rs.economics.payout.min_withdrawal_usd == 25.0
    assert rs.economics.withdrawal_policy.bank_asap is True
    assert "economics" in rs.to_dict()


def test_positive_edge_is_positive_ev():
    rs = _ruleset()
    # +0.5R/trade expectancy (6 wins@1.5 - 4 losses@1.0 per 10)
    led = _ledger([1.5, 1.5, -1.0, 1.5, -1.0, 1.5, 1.5, -1.0, -1.0, 1.5], 400)
    out = run_ev_montecarlo(led, rs, risk_pct=0.5, base_risk_pct=0.5,
                            account_size=5000, n_paths=1500, horizons_months=(3, 6, 12), seed=1)
    h12 = out["horizons"]["12.0"]
    assert h12["mean_net_usd"] > 0
    assert h12["p_profitable"] > 0.9
    assert h12["mean_accounts"] < 2.0          # rarely breaches → ~1 account
    assert h12["roi_on_fees"] is not None and h12["roi_on_fees"] > 1.0
    # bank-ASAP: realised net grows with the horizon
    assert out["horizons"]["12.0"]["mean_net_usd"] > out["horizons"]["3.0"]["mean_net_usd"]


def test_negative_edge_is_negative_ev_and_burns_accounts():
    rs = _ruleset()
    # -0.25R/trade (3 wins@1.5 - 7 losses@1.0 per 10) → breaches repeatedly
    led = _ledger([1.5, -1.0, -1.0, 1.5, -1.0, -1.0, 1.5, -1.0, -1.0, -1.0], 400)
    out = run_ev_montecarlo(led, rs, risk_pct=0.5, base_risk_pct=0.5,
                            account_size=5000, n_paths=1500, horizons_months=(12,), seed=1)
    h = out["horizons"]["12.0"]
    assert h["mean_net_usd"] < 0
    assert h["p_profitable"] < 0.1
    assert h["mean_accounts"] > 1.5            # re-buys after breaches
    assert h["mean_fees_usd"] > rs.economics.account_fee_usd  # paid for re-buys
    assert h["roi_on_fees"] is not None and h["roi_on_fees"] < 0


def test_empty_ledger_is_safe():
    rs = _ruleset()
    out = run_ev_montecarlo([], rs, risk_pct=0.5, base_risk_pct=0.5,
                            account_size=5000, n_paths=10, horizons_months=(12,), seed=1)
    assert out["error"] == "empty_ledger"
    assert out["horizons"] == {}
