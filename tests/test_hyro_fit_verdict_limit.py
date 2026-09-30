"""`hyro_fit_verdict.py --mode limit` (RQ-20260930-502): the maker-entry grading rule.

The harness is replaced by a fake that writes real committed ledgers, so these lock the RULE
(fill rate, edge per signal, gates B and C, the 11 bps market arm as report-only), not a market fact.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("hfv_limit", REPO / "scripts/research/hyro_fit_verdict.py")
hfv = importlib.util.module_from_spec(_s)
sys.modules["hfv_limit"] = hfv
_s.loader.exec_module(hfv)

LEDGERS = {leg: REPO / f"comms/strategy_evidence/runs/2026-09-25/{leg}__trades.jsonl"
           for leg in ("ict_scalp_eth_15m", "ict_scalp_sol_15m", "ict_scalp_xrp_15m")}
STRATS = {leg: {"symbols": [leg.split("_")[2].upper() + "USDT"], "timeframe": "15m"} for leg in LEDGERS}
pytestmark = pytest.mark.skipif(not all(p.exists() for p in LEDGERS.values()),
                                reason="committed ledgers unavailable")


def _fake_run(signals_per_leg, fail_on=None):
    calls = []

    def run(cmd, **kw):
        if "fetch_backtest_candles.py" in " ".join(map(str, cmd)):
            class F:
                returncode = 1 if fail_on == "fetch" else 0
                stderr = "no network"
                stdout = ""
            if fail_on != "fetch":
                Path(cmd[cmd.index("--output") + 1]).write_text("ts,o,h,l,c,v\n")
            return F()
        calls.append(cmd)
        leg = cmd[cmd.index("--strategy-name") + 1]
        emit, js = cmd[cmd.index("--emit-trades") + 1], cmd[cmd.index("--json") + 1]
        limit = "--entry-mode" in cmd
        shutil.copy(LEDGERS[leg], emit)
        n = sum(1 for _ in open(LEDGERS[leg]))
        body = {"total_trades": n, "net_total_r": 4.0}
        if limit:
            body["limit_entry"] = {"n_signals": signals_per_leg, "n_filled": n, "fill_rate": n / signals_per_leg,
                                   "net_total_r_filled": 6.0, "net_r_per_signal": 6.0 / signals_per_leg}
        Path(js).write_text(json.dumps(body))

        class P:
            returncode = 3 if fail_on == (leg, limit) else 0
            stderr = "boom"
        return P()
    run.calls = calls
    return run


def _grade(tmp_path, signals_per_leg):
    stats = hfv.run_limit_legs(list(LEDGERS), 1830, tmp_path, run=_fake_run(signals_per_leg),
                               today=datetime(2026, 9, 30, tzinfo=timezone.utc), strategies=STRATS)
    spec = hfv.spec_from_ledgers("book", stats["ledgers"])
    return stats, hfv.grade_limit(spec, stats, "config/prop_rulesets/hyrotrader.yaml")


def test_commands_carry_limit_mode_and_the_11bps_market_arm(tmp_path):
    run = _fake_run(200)
    hfv.run_limit_legs(["ict_scalp_eth_15m"], 1830, tmp_path, run=run,
                       today=datetime(2026, 9, 30, tzinfo=timezone.utc), strategies=STRATS)
    lim, mkt = run.calls
    assert lim[lim.index("--entry-mode") + 1] == "limit" and "--fee-bps-roundtrip" not in lim
    assert mkt[mkt.index("--fee-bps-roundtrip") + 1] == "11.0" and "--entry-mode" not in mkt
    assert lim[lim.index("--start") + 1] == "2021-09-25" and lim[lim.index("--end") + 1] == "2026-09-29"


def test_passes_only_with_fill_rate_and_edge_per_signal_and_the_prop_gates(tmp_path):
    _, v = _grade(tmp_path, signals_per_leg=250)          # 378 filled / 750 signals = 0.504
    assert v["fill_rate"] == pytest.approx(0.504, abs=0.001)
    assert v["gates"]["fill_rate_ge_0_50"] and v["gates"]["net_r_per_signal_positive"]
    assert v["verdict"] == "pass"
    assert v["market_arm_11bps_reported_not_gating"]["n"] == 378


def test_low_fill_rate_fails_even_when_filled_trades_are_profitable(tmp_path):
    _, v = _grade(tmp_path, signals_per_leg=400)          # 378 / 1200 = 0.315
    assert v["net_r"] > 0 and v["fill_rate"] < 0.5 and v["verdict"] == "fail"
    assert v["gates"]["fill_rate_ge_0_50"] is False


def test_a_failed_harness_run_is_reported_not_graded(tmp_path):
    stats = hfv.run_limit_legs(list(LEDGERS), 1830, tmp_path,
                               run=_fake_run(250, fail_on=("ict_scalp_sol_15m", True)),
                               today=datetime(2026, 9, 30, tzinfo=timezone.utc), strategies=STRATS)
    assert stats["failed"] and "ict_scalp_sol_15m" in stats["failed"]


def test_leg_params_come_from_the_configured_leg():
    assert hfv.leg_params("ict_scalp_eth_15m", STRATS) == {"symbol": "ETHUSDT", "timeframe": "15m"}


def test_underpowered_limit_run_stays_indeterminate_and_is_never_flipped_to_fail(tmp_path):
    stats = hfv.run_limit_legs(["ict_scalp_eth_15m"], 1830, tmp_path, run=_fake_run(250),
                               today=datetime(2026, 9, 30, tzinfo=timezone.utc), strategies=STRATS)
    v = hfv.grade_limit(hfv.spec_from_ledgers("eth", stats["ledgers"]), stats,
                        "config/prop_rulesets/hyrotrader.yaml")
    assert v["verdict"] == "indeterminate" and v["read_state"] == "measured" and v["n"] == 117


def test_limit_outputs_are_accepted_by_the_real_collector(tmp_path):
    from types import SimpleNamespace
    sr_spec = importlib.util.spec_from_file_location("script_run_limit_contract", REPO / "scripts/research/script_run.py")
    sr = importlib.util.module_from_spec(sr_spec)
    sys.modules["script_run_limit_contract"] = sr
    sr_spec.loader.exec_module(sr)

    def derive(sub, verdict):
        out = Path("out")
        (tmp_path / sub / out).mkdir(parents=True, exist_ok=True)
        (tmp_path / sub / out / "verdict.json").write_text(json.dumps(verdict))
        (tmp_path / sub / out / "run-manifest.json").write_text(json.dumps(
            {"all_ok": True, "commands": [{"index": 0, "argv": ["python3", "x.py"], "exit_code": 0}]}))
        plan = SimpleNamespace(unit="RQ-20260930-502", out_dir=out, rule_id="R", rule_registered_at="2026-09-30")
        return sr.derive_record(plan, repo=tmp_path / sub)

    (tmp_path / "g").mkdir()
    _, v = _grade(tmp_path / "g", signals_per_leg=250)
    rec = derive("a", v)
    assert rec["read_state"] == "measured" and rec["verdict"] in ("pass", "fail")
    failed = dict(verdict="not_applicable", read_state="producer_failed", n=None, population="x")
    rec = derive("b", failed)
    assert (rec["read_state"], rec["n"]) == ("producer_failed", "null")


def test_every_harness_call_gets_a_fetched_data_file(tmp_path):
    run = _fake_run(200)
    hfv.run_limit_legs(["ict_scalp_eth_15m"], 1830, tmp_path, run=run,
                       today=datetime(2026, 9, 30, tzinfo=timezone.utc), strategies=STRATS)
    assert len(run.calls) == 2
    for cmd in run.calls:
        d = Path(cmd[cmd.index("--data") + 1])
        assert d.name == "ETHUSDT_15m.csv" and d.exists()


def test_a_failed_candle_fetch_is_producer_failed_not_a_grade(tmp_path):
    stats = hfv.run_limit_legs(["ict_scalp_eth_15m"], 1830, tmp_path, run=_fake_run(200, fail_on="fetch"),
                               today=datetime(2026, 9, 30, tzinfo=timezone.utc), strategies=STRATS)
    assert stats["failed"] and "fetch" in stats["failed"] and not stats["ledgers"]


# ---- `--mode dayflat` (RQ-20260930-503) ----
DF_LEDGER = REPO / "comms/strategy_evidence/runs/2026-09-25/trend_donchian_eth_prop__trades.jsonl"
DF_STRATS = {"trend_donchian_eth_prop": {
    "symbols": ["ETHUSDT"], "timeframe": "1h", "donchian": 20, "atr_period": 14, "atr_stop_mult": 2.5,
    "trail_mult": 3.5, "min_confidence": 0.6}}
_TODAY = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _df_run(fail_fetch=False, rc=0):
    calls = []

    def run(cmd, **kw):
        if "fetch_backtest_candles.py" in " ".join(map(str, cmd)):
            class F:
                returncode = 1 if fail_fetch else 0
                stderr, stdout = "no network", ""
            if not fail_fetch:
                Path(cmd[cmd.index("--output") + 1]).write_text("timestamp,open,high,low,close,volume\n")
            return F()
        calls.append(cmd)
        shutil.copy(DF_LEDGER, cmd[cmd.index("--emit-trades") + 1])

        class P:
            returncode = rc
            stderr = "boom"
        return P()
    run.calls = calls
    return run


@pytest.mark.skipif(not DF_LEDGER.exists(), reason="committed ledger unavailable")
def test_dayflat_runs_native_candles_with_the_two_registered_levers(tmp_path):
    run = _df_run()
    stats = hfv.run_dayflat("trend_donchian_eth_prop", 1830, tmp_path, run=run, today=_TODAY, strategies=DF_STRATS)
    (cmd,) = run.calls
    assert "--resample" not in cmd                       # registered: NATIVE 1h, bar_label open
    assert cmd[cmd.index("--flat-at-utc") + 1] == "23:45" and cmd[cmd.index("--no-entry-after-utc") + 1] == "20:00"
    assert cmd[cmd.index("--data") + 1].endswith("ETHUSDT_1h.csv")
    assert cmd[cmd.index("--start") + 1] == "2021-09-25" and stats["failed"] is None and stats["ledgers"]


def test_dayflat_fetch_failure_is_reported_not_graded(tmp_path):
    stats = hfv.run_dayflat("trend_donchian_eth_prop", 1830, tmp_path, run=_df_run(fail_fetch=True),
                            today=_TODAY, strategies=DF_STRATS)
    assert stats["failed"] and "fetch" in stats["failed"] and not stats["ledgers"]


@pytest.mark.skipif(not DF_LEDGER.exists(), reason="committed ledger unavailable")
def test_dayflat_same_day_share_below_0_95_fails_and_underpowered_stays_indeterminate(tmp_path):
    spec = hfv.spec_from_ledgers("x", [str(DF_LEDGER)])
    spec = dict(spec, n=400, same_day_frac=0.90, r_samples=(spec["r_samples"] * 4)[:400])
    v = hfv.grade_dayflat(spec, {"window": []}, "config/prop_rulesets/hyrotrader.yaml")
    assert v["verdict"] == "fail" and v["gates"]["same_day_share_ge_0_95"] is False
    small = dict(spec, n=120, r_samples=spec["r_samples"][:120])
    assert hfv.grade_dayflat(small, {"window": []}, "config/prop_rulesets/hyrotrader.yaml")["verdict"] == "indeterminate"
