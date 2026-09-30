"""Sanity controls for scripts/research/hyro_passprob_mc.py (lane HYRO-STRATS)."""
import importlib.util
from pathlib import Path

from src.prop.ruleset import load_ruleset

REPO = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("hyro_mc", REPO / "scripts/research/hyro_passprob_mc.py")
mc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(mc)

RS = load_ruleset(REPO / "config/prop_rulesets/hyrotrader.yaml")
KW = dict(n_paths=300, seed=1, reading="A", leverage=10.0, winner_mae_r=0.3, loser_mfe_r=0.3,
          flag_frac=0.0)


def _spec(wr, w, loss, tpd=2.0):
    return dict(name="t", win_rate=wr, win_r=w, loss_r=loss, trades_per_day=tpd, stop_pct=1.5,
                same_day_frac=1.0)


def test_rulesets_parse_strict_vs_lenient():
    lenient = load_ruleset(REPO / "config/prop_rulesets/hyrotrader_lenient.yaml")
    assert RS.limits.max_drawdown_pct == 0.05 and lenient.limits.max_drawdown_pct == 0.06
    assert RS.evaluation.min_trading_days == 5 and RS.consistency.enabled


def test_huge_edge_passes_and_zero_edge_rarely_does():
    hi = mc.run(_spec(0.9, 1.5, 0.5), RS, risk_pct=0.5, **KW)
    zero = mc.run(_spec(0.4, 1.0, 1.0), RS, risk_pct=0.5, **KW)
    assert hi["p_pass_phase1"] > 0.9
    assert zero["p_pass_phase1"] < 0.25


def test_more_risk_means_more_breach_at_zero_edge():
    lo = mc.run(_spec(0.5, 1.0, 1.0), RS, risk_pct=0.5, **KW)
    hi = mc.run(_spec(0.5, 1.0, 1.0), RS, risk_pct=2.0, **KW)
    assert hi["p_phase1_breach"] > lo["p_phase1_breach"]


def test_qualifying_days_gate_blocks_passing():
    # stop 0.2% => a 1R trade moves price 0.2% < 1% of notional under reading A: no day qualifies
    s = _spec(0.9, 1.5, 0.5)
    s["stop_pct"] = 0.2
    r = mc.run(s, RS, risk_pct=0.5, **KW)
    assert r["p_pass_phase1"] == 0.0


def test_big_winner_is_not_counted_as_intraday_drawdown():
    # every trade +5R at 1% risk: a 5% gain per trade must never trip the 4% trailing daily rule
    s = dict(name="w", win_rate=1.0, win_r=5.0, loss_r=1.0, trades_per_day=1.0, stop_pct=2.0,
             same_day_frac=1.0)
    r = mc.run(s, RS, risk_pct=1.0, **KW)
    assert r["p_phase1_breach_daily_trailing"] == 0.0
    assert r["p_pass_phase1"] > 0.9


def test_fit_verdict_underpowered_below_floor_and_grades_committed_ledger():
    s = importlib.util.spec_from_file_location("hfv", REPO / "scripts/research/hyro_fit_verdict.py")
    hfv = importlib.util.module_from_spec(s)
    s.loader.exec_module(hfv)
    led = REPO / "comms/strategy_evidence/runs/2026-09-25/ict_scalp_eth_15m__trades.jsonl"
    if not led.exists():
        return
    spec = hfv.spec_from_ledgers("eth15", [str(led)])
    v = hfv.grade(spec, "config/prop_rulesets/hyrotrader.yaml")
    assert v["verdict"] == "indeterminate" and v["read_state"] == "underpowered" and v["n"] == 117


def test_funded_payouts_reported_and_positive_for_big_edge():
    r = mc.run(_spec(0.9, 1.5, 0.5), RS, risk_pct=0.5, **KW)
    assert r["funded_payout_usd_per_month_mean_given_funded"] > 100
    assert r["days_phase1_p10_p50_p90"][0] <= r["days_phase1_p10_p50_p90"][2]
