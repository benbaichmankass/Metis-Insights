"""Tests for src.prop.evaluator — synthetic curves/ledgers breaching each rule.

Each case is built BY HAND (no real backtester, no network, no candle data) so
the evaluator stays pure + deterministic. Account size 25_000 throughout:
  - daily-loss limit 3%  → −$750 from a day's start
  - max-DD static  6%    → −$1500 from start (floor $23_500)
  - profit target 10%    → $27_500
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.prop.evaluator import (
    TradeRecord,
    evaluate,
    worst_off_start_drawdown_pct,
)
from src.prop.ruleset import parse_ruleset

ACCOUNT = 25_000.0


def _ts(day: int, hour: int = 12) -> str:
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc).isoformat()


def _ruleset(**over):
    base = {
        "ruleset": "test",
        "account_size_usd": ACCOUNT,
        "phases": {"evaluation": {"profit_target_pct": 0.10, "min_trading_days": 0}},
        "limits": {
            "daily_loss_pct": 0.03,
            "max_drawdown_pct": 0.06,
            "drawdown_type": "static",
            "max_position_pct": None,
        },
        "consistency": {"enabled": False, "max_single_day_profit_share": 0.40},
        "funded_soak_days": 30,
    }
    # shallow-merge overrides into the nested blocks
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return parse_ruleset(base)


# ---------------------------------------------------------------------------
# 1. Daily-loss breach
# ---------------------------------------------------------------------------
def test_daily_loss_breach():
    rs = _ruleset()
    # Day 1 starts at 25_000, drops to 24_000 within the day → 4% > 3%.
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 12), 24_000.0),   # −4% intraday → BREACH
        (_ts(1, 18), 24_500.0),
    ]
    trades = [TradeRecord("s", _ts(1, 9), _ts(1, 12), -1_000.0)]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is False
    assert v["eval"]["first_breach"]["rule"] == "daily_loss"
    assert v["eval"]["first_breach"]["ts"] == _ts(1, 12)


# ---------------------------------------------------------------------------
# 2. Static max-drawdown breach
# ---------------------------------------------------------------------------
def test_static_max_drawdown_breach():
    rs = _ruleset()
    # Spread the loss across days so no single day breaches the 3% daily cap,
    # but cumulative equity falls below the static 6% floor ($23_500).
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 24_400.0),   # −2.4% day1 (ok)
        (_ts(2, 9), 24_400.0),
        (_ts(2, 18), 23_900.0),   # −2.0% day2 (ok), cum −4.4% (ok)
        (_ts(3, 9), 23_900.0),
        (_ts(3, 18), 23_300.0),   # −2.5% day3 (ok), cum −6.8% → DD BREACH
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(1, 18), -600.0),
        TradeRecord("s", _ts(2, 9), _ts(2, 18), -500.0),
        TradeRecord("s", _ts(3, 9), _ts(3, 18), -600.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is False
    assert v["eval"]["first_breach"]["rule"] == "max_drawdown"
    assert v["eval"]["first_breach"]["ts"] == _ts(3, 18)


# ---------------------------------------------------------------------------
# 3. Position-size breach
# ---------------------------------------------------------------------------
def test_position_size_breach():
    rs = _ruleset(limits={"max_position_pct": 0.50})  # cap 50% of account
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 25_200.0),
    ]
    # one trade with notional 15_000 (60% of account) → BREACH
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(1, 18), 200.0, notional=15_000.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is False
    assert v["eval"]["first_breach"]["rule"] == "position_size"


# ---------------------------------------------------------------------------
# 4. Consistency breach (only counts once target is hit)
# ---------------------------------------------------------------------------
def test_consistency_breach():
    rs = _ruleset(consistency={"enabled": True, "max_single_day_profit_share": 0.40})
    # Reach +10% ($27_500): day1 makes $2_400 of $3_000 total → 80% share > 40%.
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 27_400.0),   # +$2_400 day1
        (_ts(2, 9), 27_400.0),
        (_ts(2, 18), 27_700.0),   # +$300 day2 → crosses +10% target
        (_ts(3, 18), 28_000.0),   # +$300 day3
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(1, 18), 2_400.0),
        TradeRecord("s", _ts(2, 9), _ts(2, 18), 300.0),
        TradeRecord("s", _ts(3, 9), _ts(3, 18), 300.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is False
    assert v["eval"]["first_breach"]["rule"] == "consistency"
    # worst-day share surfaced in metrics
    assert v["metrics"]["consistency_worst_day_share"] == pytest.approx(0.80, abs=1e-3)


def test_consistency_ignored_when_target_not_hit():
    # Same lopsided day, but never reaches +10% → consistency must NOT fail it
    # (the rule only applies once the target is cleared, design §5.5).
    rs = _ruleset(consistency={"enabled": True, "max_single_day_profit_share": 0.40})
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 25_900.0),   # +$900 only — well short of +10%
    ]
    trades = [TradeRecord("s", _ts(1, 9), _ts(1, 18), 900.0)]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    # not a breach — just "eval not reached"
    assert v["eval"]["passed"] is False
    assert v["eval"]["first_breach"] is None
    assert "NOT REACHED" in v["headline"]


# ---------------------------------------------------------------------------
# 5. Clean pass — reaches target, no breach, funded soak survives
# ---------------------------------------------------------------------------
def test_clean_pass_and_funded_survive():
    rs = _ruleset()
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 25_400.0),
        (_ts(2, 18), 26_200.0),
        (_ts(3, 18), 27_000.0),
        (_ts(4, 18), 27_600.0),   # crosses +10% ($27_500) on day 4
        # funded soak window (no breaches, gentle grind up)
        (_ts(10, 18), 28_000.0),
        (_ts(20, 18), 28_400.0),
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(1, 18), 400.0),
        TradeRecord("s", _ts(2, 9), _ts(2, 18), 800.0),
        TradeRecord("s", _ts(3, 9), _ts(3, 18), 800.0),
        TradeRecord("s", _ts(4, 9), _ts(4, 18), 600.0),
        TradeRecord("s", _ts(10, 9), _ts(10, 18), 400.0),
        TradeRecord("s", _ts(20, 9), _ts(20, 18), 400.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is True
    assert v["eval"]["first_breach"] is None
    assert v["eval"]["days_to_target"] == 3  # day 4 minus day 1
    assert v["funded_soak"]["survived"] is True
    assert v["headline"] == "EVAL PASS / FUNDED SURVIVE"
    # equity never dipped below start → off-start DD is 0; eval-pass equity is
    # the balance when +10% was first crossed ($27_600 on day 4).
    assert v["eval"]["static_dd_off_start_pct"] == 0.0
    assert v["eval"]["equity_at_eval_pass"] == 27_600.0


# ---------------------------------------------------------------------------
# 6. Eval not reached (no breach, target never hit)
# ---------------------------------------------------------------------------
def test_eval_not_reached():
    rs = _ruleset()
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 25_300.0),
        (_ts(2, 18), 25_600.0),
        (_ts(3, 18), 26_000.0),   # only +4%, never +10%, never breaches
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(1, 18), 300.0),
        TradeRecord("s", _ts(2, 9), _ts(2, 18), 300.0),
        TradeRecord("s", _ts(3, 9), _ts(3, 18), 400.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is False
    assert v["eval"]["first_breach"] is None
    assert v["funded_soak"]["survived"] is False  # never entered funded
    assert "NOT REACHED" in v["headline"]


# ---------------------------------------------------------------------------
# 7. Eval passes but funded soak fails (breach after the target)
# ---------------------------------------------------------------------------
def test_eval_pass_funded_fail():
    rs = _ruleset()
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(2, 18), 27_600.0),   # +10.4% → eval passes day 2
        # funded soak: a clean −7% static DD breach later
        (_ts(10, 9), 27_600.0),
        (_ts(10, 18), 27_000.0),
        (_ts(11, 18), 26_500.0),
        (_ts(12, 18), 25_700.0),  # funded ref is 27_600; −6.9% → DD breach
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(2, 18), 2_600.0),
        TradeRecord("s", _ts(10, 9), _ts(10, 18), -600.0),
        TradeRecord("s", _ts(11, 9), _ts(11, 18), -500.0),
        TradeRecord("s", _ts(12, 9), _ts(12, 18), -800.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["passed"] is True
    assert v["funded_soak"]["survived"] is False
    assert v["funded_soak"]["first_breach"]["rule"] == "max_drawdown"
    assert "FUNDED FAIL" in v["headline"]


# ---------------------------------------------------------------------------
# 8. Trailing drawdown differs from static
# ---------------------------------------------------------------------------
def test_trailing_drawdown_breach():
    rs = _ruleset(limits={"drawdown_type": "trailing", "daily_loss_pct": None})
    # Equity climbs to a peak then falls 6.5% from THAT peak (not from start),
    # which static would not catch (still above the start floor).
    curve = [
        (_ts(1, 18), 25_000.0),
        (_ts(2, 18), 30_000.0),   # peak
        (_ts(3, 18), 28_000.0),   # −6.7% from peak → trailing BREACH (static ok: +12%)
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(2, 18), 5_000.0),
        TradeRecord("s", _ts(3, 9), _ts(3, 18), -2_000.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    assert v["eval"]["first_breach"]["rule"] == "max_drawdown"
    assert v["eval"]["first_breach"].get("drawdown_type") == "trailing"


# ---------------------------------------------------------------------------
# 9. worst_off_start_drawdown_pct — the rule-measure helper
# ---------------------------------------------------------------------------
def test_worst_off_start_drawdown_pct():
    # Deepest point below start is $23_750 → (25_000-23_750)/25_000 = 5%.
    curve = [
        (_ts(1, 9), 25_000.0),
        (_ts(1, 18), 23_750.0),  # −5% off start
        (_ts(2, 18), 30_000.0),  # well into profit
        (_ts(3, 18), 28_000.0),  # −6.7% peak-to-trough, but +12% off start
    ]
    assert worst_off_start_drawdown_pct(curve, ACCOUNT) == pytest.approx(0.05, abs=1e-9)
    # equity always above start → clamps to 0.0
    assert worst_off_start_drawdown_pct(
        [(_ts(1), 25_100.0), (_ts(2), 26_000.0)], ACCOUNT
    ) == 0.0
    # empty curve / bad account → None
    assert worst_off_start_drawdown_pct([], ACCOUNT) is None
    assert worst_off_start_drawdown_pct(curve, 0.0) is None


# ---------------------------------------------------------------------------
# 10. RECONCILIATION: a static pass whose PEAK-TO-TROUGH DD exceeds the limit
#     because the deep swing happened while the account was IN PROFIT. This is
#     the fade+squeeze+fvg case from the 2026-06-16 matrix (9.87% peak-to-trough
#     but <6% off-start) — proves the pass is legit, NOT a bug.
# ---------------------------------------------------------------------------
def test_static_pass_with_deep_peak_to_trough_in_profit():
    rs = _ruleset()  # static 6% off start, +10% target
    curve = [
        (_ts(1, 9), 25_000.0),
        # climb into profit first, never dipping below start
        (_ts(2, 18), 25_500.0),
        (_ts(3, 18), 28_000.0),   # +12% → crosses +10% target here
        # now a deep peak-to-trough swing, but it stays ABOVE the start floor
        (_ts(4, 18), 26_100.0),   # −6.8% from the 28_000 peak, still +4.4% off start
        (_ts(5, 18), 28_500.0),   # recover
    ]
    trades = [
        TradeRecord("s", _ts(1, 9), _ts(2, 18), 500.0),
        TradeRecord("s", _ts(2, 9), _ts(3, 18), 2_500.0),
        TradeRecord("s", _ts(3, 9), _ts(4, 18), -1_900.0),
        TradeRecord("s", _ts(4, 9), _ts(5, 18), 2_400.0),
    ]
    v = evaluate(rs, curve, trades, account_size=ACCOUNT)
    # The off-start DD never breached 6% → the verdict is a legit PASS.
    assert v["eval"]["passed"] is True
    assert v["eval"]["first_breach"] is None
    # Rule measure (off-start) is 0% here — equity never dipped below start.
    assert v["eval"]["static_dd_off_start_pct"] == 0.0
    # Eval-pass equity recorded at the +10% crossing.
    assert v["eval"]["equity_at_eval_pass"] == 28_000.0
