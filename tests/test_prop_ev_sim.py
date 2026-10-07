"""B6 prop EV-per-account-life simulator (scripts/research/prop_ev_sim.py).

The module's own --self-test carries the positive controls (gambler's ruin,
deterministic payouts, daily-loss concurrency, cost arithmetic, path
calibration); this file runs it under pytest and pins the pieces a P2 caller
depends on: the input schema, the ruleset read, the book resolver's staleness
refusal, and the E5 record the CLI lands.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("prop_ev_sim", REPO / "scripts" / "research" / "prop_ev_sim.py")
sim = importlib.util.module_from_spec(_SPEC)
sys.modules["prop_ev_sim"] = sim
_SPEC.loader.exec_module(sim)


def test_self_test_passes():
    assert sim._self_test() == 0


def test_ruleset_read_from_breakout_yaml():
    r = sim.PropRules.from_yaml(sim.DEFAULT_RULESET)
    assert (r.start, r.target_pct, r.daily_loss_pct, r.max_dd_pct) == (5000.0, 0.10, 0.03, 0.06)
    assert (r.fee, r.profit_split, r.first_payout_after_days, r.payout_frequency_days) == (45.0, 0.80, 14.0, 7.0)


def test_operator_confirmed_breakout_terms_are_the_defaults():
    # Operator popup 2026-09-27 ~11:12Z: "Resets to a fresh $5,000" and "No
    # refund". Declared in the ruleset; the CLI defaults read them from there.
    r = sim.PropRules.from_yaml(sim.DEFAULT_RULESET)
    assert (r.funded_start, r.first_payout_refund) == ("fresh", False)
    assert sim.SimConfig().funded_start == "fresh"
    assert sim.SimConfig().first_payout_refund is False


def _write(tmp_path: Path, name: str, rows) -> Path:
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _rows(n: int, r: float, start_day: int = 1):
    return [{"entry_time": f"2025-01-{start_day + i % 27:02d}T0{i % 3 + 1}:00:00+00:00",
             "exit_time": f"2025-01-{start_day + i % 27:02d}T0{i % 3 + 4}:00:00+00:00",
             "gross_r": r, "entry": 100.0, "sl": 98.0, "mfe_r": max(r, 0) + 0.5} for i in range(n)]


def test_schema_a_breakout_costs_applied(tmp_path):
    rows = _rows(3, 1.0)
    trades, day0, days = sim.build_trades({"legA": rows}, sim.CostConfig())
    # 8 bps commission + 3 bps slippage over a 2% stop, intraday (no swap)
    assert all(abs(t.cost_r - 0.0011 / 0.02) < 1e-12 for t in trades)
    assert all(abs(t.net_r - (1.0 - 0.055)) < 1e-12 for t in trades)
    assert day0.hour == 0 and days >= 1


def test_leg_field_overrides_file_leg():
    rows = [dict(r, leg="named") for r in _rows(2, 0.5)]
    trades, _, _ = sim.build_trades({"fromfile": rows}, sim.CostConfig())
    assert {t.leg for t in trades} == {"named"}


def test_schema_b_as_given_requires_net_r():
    with pytest.raises(ValueError):
        sim.build_trades({"x": [{"entry_time": "2025-01-01T00:00:00Z",
                                 "exit_time": "2025-01-01T01:00:00Z"}]}, sim.CostConfig(mode="as_given"))


def test_cli_trades_and_e5_record(tmp_path, monkeypatch, capsys):
    a = _write(tmp_path, "a.jsonl", _rows(40, 1.2) + _rows(40, -1.0, start_day=2))
    import research_result as rr  # noqa: E402 — resolved via the script's own sys.path insert below
    monkeypatch.setattr(rr, "RESULTS_ROOT", tmp_path / "results")
    out = tmp_path / "out.json"
    rc = sim.main(["--trades", f"legA={a}", "--lives", "50", "--outer", "3", "--lives-per-outer", "20",
                   "--out", str(out), "--emit-result", "--run-id", "t1"])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert set(doc["results"]) == set(sim.MARK_MODES)
    assert doc["decision_rule"]["id"] == sim.DECISION_RULE_ID
    assert doc["decision_rule_v2"]["id"] == sim.DECISION_RULE_V2_ID
    rec = json.loads((tmp_path / "results" / "_unattributed" / "t1.jsonl").read_text().splitlines()[0])
    assert rr.validate(rec) == []
    assert rec["decision_rule"]["id"] == sim.DECISION_RULE_V2_ID      # v2 is the default for new candidates
    assert rec["population"]["n"] == 80


sys.path.insert(0, str(REPO / "scripts" / "research"))


def test_book_resolver_matches_current_config():
    paths, prov = sim.resolve_book("breakout_1")
    assert set(paths) == {"trend_donchian_sol_prop", "trend_donchian_eth_prop"}
    for leg, p in prov.items():
        assert (REPO / p["trades_file"]).exists(), leg
    # the resolver REPORTS drift; the CLI refuses on it. Pin that the field exists and is boolean.
    assert all(isinstance(p["fingerprint_matches"], bool) for p in prov.values())


def test_stale_book_is_refused(monkeypatch):
    def fake_resolve(book):
        return {}, {"leg": {"fingerprint_matches": False}}
    monkeypatch.setattr(sim, "resolve_book", fake_resolve)
    assert sim.main(["--book", "breakout_1"]) == 2


def test_qualifying_day_gate_is_observable():
    """Lane VELOTRADE-FIT (2026-10-06): whether Velotrade's 5-qualifying-day rule BINDS has
    to be readable off the summary, not inferred. passed is a subset of target-reached,
    the gap between them is the gate's cost, and with the gate off the two coincide."""
    import numpy as np
    rows = sim._toy_rows([1.0] * 400)
    trades, _, days = sim.build_trades({"toy": rows}, sim.CostConfig(mode="as_given"))
    hist = sim.History(trades, days)
    cfg = sim.SimConfig(risk_pct=0.02, sizing="start", horizon_days=60, block_days=days)
    def stream():
        return ((t.entry, t.exit, t) for t in hist.trades)
    on = sim.simulate_life(hist, sim.PropRules(qual_day_min_days=5, qual_day_profit_pct=0.02), cfg,
                           "realized", np.random.default_rng(0), stream=stream())
    counters = {}
    blocked = sim.simulate_life(hist, sim.PropRules(qual_day_min_days=5, qual_day_profit_pct=0.03), cfg,
                                "realized", np.random.default_rng(0), counters=counters, stream=stream())
    off = sim.simulate_life(hist, sim.PropRules(), cfg, "realized", np.random.default_rng(0), stream=stream())
    assert on.passed and on.target_first_reached_days < on.days_to_pass and on.qual_days_eval == 5
    assert not blocked.passed and blocked.target_first_reached_days is not None
    assert counters["pass_deferred_qual_days"] > 0
    assert off.target_first_reached_days == off.days_to_pass
    q = sim.summarize([on, blocked], sim.PropRules())["qualifying_days"]
    assert q["p_target_reached_eval"] == 1.0 and q["p_pass_given_target_reached"] == 0.5
    assert q["p_target_reached_not_passed"] == 0.5


def test_p_pass_by_days_is_a_cdf_over_all_lives():
    """PI-20261006-TPQQDKFF-0001: P(pass by 30/90/180 d) is over ALL lives, so a life that
    never passed counts against every horizon and the curve plateaus at p_pass_eval."""
    def life(passed, days):
        lf = sim.Life()
        lf.passed, lf.days_to_pass = passed, days
        return lf
    lives = [life(True, 10.0), life(True, 30.0), life(True, 100.0), life(False, None)]
    got = sim.p_pass_by_days(lives)
    assert got == {"30": 0.5, "90": 0.5, "180": 0.75}, got      # day 30 is inclusive
    assert list(got) == ["30", "90", "180"]
    assert got["180"] <= sim.summarize(lives, sim.PropRules())["p_pass_eval"] == 0.75
    assert sim.summarize(lives, sim.PropRules())["p_pass_by_days"] == got
    assert sim.p_pass_by_days([life(False, None)] * 3) == {"30": 0.0, "90": 0.0, "180": 0.0}
