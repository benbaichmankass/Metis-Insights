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
