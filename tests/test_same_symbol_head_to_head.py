"""Pure-function tests for the same-symbol head-to-head driver (the registered rule + the metric)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
import same_symbol_head_to_head as h  # noqa: E402

T0 = datetime(2025, 3, 1, tzinfo=timezone.utc)


def trade(day, hold_days, pnl, risk=100.0, entry=100.0):
    # qty*|entry-sl| == risk
    return {"entry_ts": T0 + timedelta(days=day), "exit_ts": T0 + timedelta(days=day + hold_days),
            "qty": risk / 5.0, "entry": entry, "sl": entry - 5.0, "pnl": pnl}


def test_metric_net_r_and_capital_days():
    m = h.metric_from_trades([trade(0, 2, 200.0), trade(5, 3, -100.0)], "2025-01-01")
    assert m["n_oos"] == 2 and m["net_r"] == 1.0 and m["capital_days"] == 5.0
    assert m["net_r_per_capital_day"] == 0.2


def test_metric_excludes_pre_oos_and_zero_risk():
    m = h.metric_from_trades([trade(-100, 1, 999.0), dict(trade(0, 1, 5.0), sl=100.0)], "2025-03-01")
    assert m["n_oos"] == 0 and m["n_skipped_zero_risk"] == 1 and m["net_r_per_capital_day"] is None


def arm(n, v):
    return {"n_oos": n, "net_r_per_capital_day": v}


def test_report_ranks_nothing_and_names_no_winner():
    arms = {"solo:a": arm(30, 0.10), "solo:b": arm(30, 0.05), "together": arm(30, 0.20)}
    d = h.decide(arms, 20)
    assert d["verdict"] == "pass" and "winner" not in d and "keep_legs" not in d
    assert d["per_leg_net_r_per_capital_day"] == {"a": 0.10, "b": 0.05}
    assert d["combined_book_net_r_per_capital_day"] == 0.20


def test_starved_arm_is_indeterminate():
    arms = {"solo:a": arm(30, 0.10), "solo:b": arm(3, 9.9), "together": arm(30, 0.2)}
    d = h.decide(arms, 20)
    assert d["verdict"] == "indeterminate" and "solo:b" in d["reason"] and "winner" not in d


def test_none_metric_is_indeterminate():
    arms = {"solo:a": arm(30, 0.1), "solo:b": arm(30, None), "together": arm(30, 0.2)}
    assert h.decide(arms, 20)["verdict"] == "indeterminate"


def test_needs_together_and_two_solos():
    assert h.decide({"solo:a": arm(30, 1.0)}, 20)["verdict"] == "indeterminate"


def test_apply_cost_policy_sets_the_module_globals():
    bs = h._load_bs()
    bs.SLIPPAGE_BPS_ROUNDTRIP = 0.0
    pol = h.apply_cost_policy(bs, "ETHUSDT")
    assert bs.SLIPPAGE_BPS_ROUNDTRIP == pol["slippage_bps_roundtrip"] > 0
    assert bs.FUNDING_BPS_PER_WINDOW == pol["funding_bps_per_window"]


def test_xrp_legs_registered_in_roster():
    bs = h._load_bs()
    assert {"trend_donchian_xrp_4h", "xrp_pullback_2h", "trend_donchian_eth_4h", "trend_donchian_eth_prop"} <= set(bs.ROSTER)


def test_route_fault_writes_a_contract_valid_producer_failed_verdict(tmp_path, monkeypatch):
    # RQ-20261015-1631-01/-02 landed "verdict.json malformed: read_state 'unavailable'" and lost the
    # reason. A route/harness fault must land producer_failed + not_applicable + n null, reason kept.
    import json
    from scripts.research import script_run

    class _BS:
        ROSTER = {}
    monkeypatch.setattr(h, "_load_bs", lambda: _BS)
    unit = tmp_path / "RQ-20300101-001.yaml"
    unit.write_text("id: RQ-20300101-001\nhead_to_head:\n  symbol: XRPUSDT\n  legs: [a, b]\n  oos_start: '2025-01-01'\n")
    assert h.run(unit, tmp_path / "out") == 0
    v = json.loads((tmp_path / "out" / "verdict.json").read_text())
    assert v["read_state"] == "producer_failed" and v["verdict"] == "not_applicable" and v["n"] is None
    assert "not registered" in v["note"]
    assert v["verdict"] in script_run.VERDICTS and v["read_state"] in script_run.READ_STATES


def test_unresolved_candles_fall_back_to_the_public_archive(tmp_path, monkeypatch):
    # A runner has no data/ dir: RQ-20261015-1631-01/-02 and RQ-20261018-001 landed producer_failed on
    # "no 5m candle source". The driver now fetches the public 5m archive; an empty fetch is still a fault.
    import json

    class _Src:
        ok, path, reason = False, None, "unresolved"

    class _DS:
        @staticmethod
        def resolve_or_refuse(*a, **k):
            return _Src

    class _BS:
        ROSTER = {"a": 1, "b": 1}
        _backtest_data_source = _DS
    monkeypatch.setattr(h, "_load_bs", lambda: _BS)
    monkeypatch.setattr(h, "fetch_5m", lambda symbol, dest, start: None)
    unit = tmp_path / "RQ-20300101-001.yaml"
    unit.write_text("id: RQ-20300101-001\nhead_to_head:\n  symbol: SOLUSDT\n  legs: [a, b]\n  oos_start: '2025-01-01'\n")
    assert h.run(unit, tmp_path / "out") == 0
    v = json.loads((tmp_path / "out" / "verdict.json").read_text())
    assert v["read_state"] == "producer_failed" and "Binance-Vision returned no rows" in v["note"]
