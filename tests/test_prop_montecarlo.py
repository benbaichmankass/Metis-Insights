"""Tests for the prop-firm Monte-Carlo survival+speed module (src/prop/montecarlo.py).

Deterministic (fixed seed). Each test feeds a SYNTHETIC trade ledger with a
KNOWN property and asserts the aggregate behaves as the property demands:

  * all-winners  → P(pass)=1, P(breach)=0, survival=1.
  * one catastrophic loss every block → P(breach)>0, static_drawdown cause.
  * a quick-double winner → median trades-to-pass is small.
  * the R-multiple back-out reproduces a known sizing-independent outcome.

The ledger items mimic the engine's ``_ClosedTrade`` via a tiny stand-in with
the attributes the module reads (pnl, entry_ts, exit_ts).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List

import pytest

from src.prop.montecarlo import (
    ledger_to_r_sequence,
    run_montecarlo,
)
from src.prop.ruleset import parse_ruleset


@dataclass
class _FakeTrade:
    pnl: float
    entry_ts: Any
    exit_ts: Any


def _ruleset(account_size=5000.0, target=0.10, daily=0.03, max_dd=0.06):
    return parse_ruleset(
        {
            "ruleset": "test",
            "account_size_usd": account_size,
            "phases": {"evaluation": {"profit_target_pct": target, "min_trading_days": 0}},
            "limits": {
                "daily_loss_pct": daily,
                "max_drawdown_pct": max_dd,
                "drawdown_type": "static",
            },
            "funded_soak_days": 30,
        }
    )


def _ledger(pnls: List[float], *, start="2023-01-01", gap_hours=24.0):
    """Build a ledger of trades with the given pnls, one trade per `gap_hours`."""
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    out: List[_FakeTrade] = []
    for i, p in enumerate(pnls):
        exit_dt = t0 + timedelta(hours=gap_hours * (i + 1))
        entry_dt = exit_dt - timedelta(hours=1)
        out.append(_FakeTrade(pnl=p, entry_ts=entry_dt.isoformat(), exit_ts=exit_dt.isoformat()))
    return out


# ---------------------------------------------------------------------------
# R-sequence back-out
# ---------------------------------------------------------------------------
def test_r_sequence_constant_winners():
    # base_risk 1% of $5000 = $50 risk. A +$50 trade is exactly +1R.
    # The SECOND trade sizes against $5050 (balance compounded), so a +$50
    # trade there is +50/50.5 R != 1R — verifies balance-aware back-out.
    led = _ledger([50.0, 50.0])
    seq = ledger_to_r_sequence(led, initial_balance=5000.0, base_risk_pct=1.0)
    assert len(seq) == 2
    assert seq[0].r_multiple == pytest.approx(1.0)            # 50 / (5000*0.01)
    assert seq[1].r_multiple == pytest.approx(50.0 / 50.5)    # 50 / (5050*0.01)


def test_r_sequence_gap_seconds():
    led = _ledger([10.0, 10.0, 10.0], gap_hours=24.0)
    seq = ledger_to_r_sequence(led, initial_balance=5000.0, base_risk_pct=1.0)
    assert seq[0].gap_seconds == 0.0                # first trade has no predecessor
    assert seq[1].gap_seconds == pytest.approx(86400.0)
    assert seq[2].gap_seconds == pytest.approx(86400.0)


def test_r_sequence_orders_by_exit():
    # Feed out-of-order; ledger_to_r_sequence must sort by exit_ts.
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    later = _FakeTrade(pnl=10.0, entry_ts=t0.isoformat(),
                       exit_ts=(t0 + timedelta(days=2)).isoformat())
    earlier = _FakeTrade(pnl=20.0, entry_ts=t0.isoformat(),
                         exit_ts=(t0 + timedelta(days=1)).isoformat())
    seq = ledger_to_r_sequence([later, earlier], initial_balance=5000.0, base_risk_pct=1.0)
    # earlier trade (pnl=20) must be first → R = 20/(5000*0.01) = 0.4
    assert seq[0].r_multiple == pytest.approx(0.4)
    assert seq[1].r_multiple == pytest.approx(10.0 / 50.2)


# ---------------------------------------------------------------------------
# All-winners → certain pass, zero breach
# ---------------------------------------------------------------------------
def test_all_winners_pass_certain_no_breach():
    # Every trade +0.5R at 1% risk = +$25 on $5000 → steadily climbs to +10%.
    led = _ledger([25.0] * 400, gap_hours=12.0)
    agg = run_montecarlo(
        led, _ruleset(), risk_pct=1.0, base_risk_pct=1.0,
        n_paths=300, block_len=8, seed=7,
    )
    assert agg["p_pass"] == 1.0
    assert agg["p_breach"] == 0.0
    assert agg["breach_by_cause"] == {}
    for m in ("3.0", "6.0", "12.0"):
        assert agg["survival"][m] == 1.0
    assert agg["trades_to_pass"]["median"] is not None
    assert agg["end_return"]["median"] > 0.10


# ---------------------------------------------------------------------------
# A catastrophic loss → guaranteed static-DD breach
# ---------------------------------------------------------------------------
def test_catastrophic_loss_breaches_static_dd():
    # Every trade is exactly -7R at base 1% risk. To keep the LEDGER's replay
    # balance positive (so every R stays -7 instead of decaying to 0 once a
    # degenerate all-loss replay goes negative), the pnls decay geometrically:
    # pnl_k = -0.07 * balance_before_k. Then the SIMULATED first trade is
    # -7% of $5000 = -$350 → $4650 <= $4700 static floor → P(breach)=1.
    bal = 5000.0
    pnls = []
    for _ in range(40):
        p = -0.07 * bal
        pnls.append(p)
        bal += p
    led = _ledger(pnls, gap_hours=48.0)
    agg = run_montecarlo(
        led, _ruleset(), risk_pct=1.0, base_risk_pct=1.0,
        n_paths=200, block_len=4, seed=11,
    )
    assert agg["p_pass"] == 0.0
    assert agg["p_breach"] == 1.0
    assert "static_drawdown" in agg["breach_by_cause"]
    assert agg["breach_by_cause"]["static_drawdown"] == pytest.approx(1.0)
    for m in ("3.0", "6.0", "12.0"):
        assert agg["survival"][m] == 0.0


def test_daily_loss_breach_cause():
    # Two trades same day, each -2% of balance. Day-start = 5000; after two
    # trades on day 1 the realised day loss > 3% → daily_loss breach (the
    # static 6% floor is NOT yet hit by trade 1's -2%, so daily_loss fires
    # first via the two-per-day cadence). gap_hours small keeps them same day.
    led = _ledger([-100.0] * 40, gap_hours=2.0)  # -100 = -2% of 5000
    agg = run_montecarlo(
        led, _ruleset(), risk_pct=1.0, base_risk_pct=1.0,
        n_paths=200, block_len=4, seed=13,
    )
    assert agg["p_breach"] == 1.0
    # daily_loss should be the dominant (or sole) cause given the cadence
    assert "daily_loss" in agg["breach_by_cause"]


# ---------------------------------------------------------------------------
# Speed: a strong edge passes fast
# ---------------------------------------------------------------------------
def test_fast_pass_small_trade_count():
    # Constant +2R winners at base 1% risk: pnl_k = +0.02*balance_before so the
    # ledger R stays exactly 2.0 (not decaying as a fixed-$ winner would).
    # At sim risk 1% each trade is +2% of the live balance → ~5 trades compound
    # to +10%. A strong-edge ledger passes FAST → small median trade count.
    bal = 5000.0
    pnls = []
    for _ in range(200):
        p = 0.02 * bal
        pnls.append(p)
        bal += p
    led = _ledger(pnls, gap_hours=24.0)
    agg = run_montecarlo(
        led, _ruleset(), risk_pct=1.0, base_risk_pct=1.0,
        n_paths=200, block_len=4, seed=17,
    )
    assert agg["p_pass"] == 1.0
    # +2%/trade compounding → ceil(log(1.1)/log(1.02)) ≈ 5 trades
    assert agg["trades_to_pass"]["median"] <= 6
    assert agg["days_to_pass"]["median"] is not None


# ---------------------------------------------------------------------------
# Risk_pct scaling moves the needle (sizing independence in action)
# ---------------------------------------------------------------------------
def test_higher_risk_passes_faster():
    # Mild positive edge: +0.3R winners and -0.2R losers, net positive.
    pnls = ([15.0] * 3 + [-10.0] * 2) * 80  # 0.3R win / 0.2R loss @1% on $5000
    led = _ledger(pnls, gap_hours=8.0)
    rs = _ruleset()
    lo = run_montecarlo(led, rs, risk_pct=0.3, base_risk_pct=1.0, n_paths=300, seed=3)
    hi = run_montecarlo(led, rs, risk_pct=1.0, base_risk_pct=1.0, n_paths=300, seed=3)
    # higher risk → higher (or equal) pass probability for a positive-edge ledger
    assert hi["p_pass"] >= lo["p_pass"]


def test_empty_ledger_clean_envelope():
    agg = run_montecarlo([], _ruleset(), risk_pct=0.5, base_risk_pct=1.0, n_paths=10, seed=1)
    assert agg["n_ledger_trades"] == 0
    assert agg["p_pass"] == 0.0
    assert agg["error"] == "empty_ledger"


def test_determinism_same_seed():
    led = _ledger([20.0, -10.0, 30.0, -15.0] * 50, gap_hours=10.0)
    rs = _ruleset()
    a = run_montecarlo(led, rs, risk_pct=0.6, base_risk_pct=1.0, n_paths=150, seed=99)
    b = run_montecarlo(led, rs, risk_pct=0.6, base_risk_pct=1.0, n_paths=150, seed=99)
    assert a["p_pass"] == b["p_pass"]
    assert a["p_breach"] == b["p_breach"]
    assert a["survival"] == b["survival"]
    assert a["trades_to_pass"] == b["trades_to_pass"]
