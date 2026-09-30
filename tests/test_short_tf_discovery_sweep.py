"""scripts/research/short_tf_discovery_sweep.py -- the instrument for RQ-20260930-504.

The harnesses are replaced by a deterministic synthetic runner, so these tests lock the
STATISTICS AND THE RULE (screen -> Bonferroni confirmation -> verdict), not any market fact.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("sweep", REPO / "scripts/research/short_tf_discovery_sweep.py")
sw = importlib.util.module_from_spec(_s)
sys.modules["sweep"] = sw          # @dataclass resolves annotations through sys.modules
_s.loader.exec_module(sw)

FAMS = ("ict_scalp", "fvg_range")
GOOD = "ict_scalp|BTCUSDT|15m"


def _rows(start, end, n, mu, seed, sd=1.0):
    rng = random.Random(seed)
    lo = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    hi = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    span = (hi - lo).total_seconds()
    out = []
    for i in range(n):
        et = lo + timedelta(seconds=span * (i + 0.5) / n)
        out.append(json.dumps({
            "entry_time": et.isoformat(), "exit_time": (et + timedelta(hours=2)).isoformat(),
            "net_r": rng.gauss(mu, sd), "entry": 100.0, "sl": 98.5}))
    return out


def _runner(mus, n_a=400, n_b=300, fail=()):
    calls = []

    def run(cell, start, end):
        calls.append((cell.key, start))
        if cell.key in fail:
            raise RuntimeError("harness exploded")
        stage_a = start == sw.STAGE_A[0]
        mu_a, mu_b = mus.get(cell.key, (-0.3, -0.3))
        return _rows(start, end, n_a if stage_a else n_b, mu_a if stage_a else mu_b,
                     seed=hash(cell.key) % 10_000 + (0 if stage_a else 1))
    run.calls = calls
    return run


def _sweep(runner, **kw):
    return sw.sweep(wave=1, families=FAMS, timeframes=("15m",), runner=runner,
                    data_check=kw.pop("data_check", lambda s, t: (True, "resolved")),
                    strategies=kw.pop("strategies", {}), stage_b_end="2026-09-29", log=lambda *_: None, **kw)


def test_student_t_tail_matches_known_values():
    assert sw.t_sf(0.0, 10) == pytest.approx(0.5)
    assert sw.t_sf(1.812461, 10) == pytest.approx(0.05, abs=2e-4)
    assert sw.t_sf(2.0, 100) == pytest.approx(0.02411, abs=1e-4)
    assert sw.t_sf(1.644854, 1e7) == pytest.approx(0.05, abs=1e-4)
    assert sw.t_sf(-1.812461, 10) == pytest.approx(0.95, abs=2e-4)


def test_mean_test_is_one_sided_for_a_positive_mean():
    r = sw.mean_test([1.0, 2.0, 3.0, 2.5, 1.5])
    assert r["mean"] == pytest.approx(2.0) and r["p"] < 0.01
    assert sw.mean_test([-1.0, -2.0, -1.5, -0.5])["p"] > 0.95


def test_pass_needs_a_confirmed_cell_and_stage_b_runs_only_for_survivors():
    r = _runner({GOOD: (0.35, 0.35)})
    res = _sweep(r)
    assert res["K"] == 10 and res["S"] == 1 and res["verdict"] == "pass"
    assert res["confirmed"] == [GOOD]
    assert res["alpha_b"] == pytest.approx(0.05)                      # 0.05 / S with S = 1
    b_calls = [k for k, start in r.calls if start == sw.STAGE_B_START]
    assert b_calls == [GOOD]                                          # survivors only, once
    cell = next(c for c in res["cells"] if f"{c['family']}|{c['symbol']}|{c['timeframe']}" == GOOD)
    sb = cell["stage_b"]
    assert sb["folds_positive"] >= 3 and sb["indeterminate_underpowered"] is False
    assert sb["venue_fit_reported_not_gating"]["same_utc_day_share"] == pytest.approx(0.917, abs=0.05)   # 2h holds cross midnight ~8% of the time
    # 11 bps arm is strictly worse than the 7.5 verdict arm
    assert sb["mean_net_r_at_report_fee"] < sb["mean_net_r"]


def test_bonferroni_uses_the_number_of_survivors():
    mus = {f"ict_scalp|{s}|15m": (0.35, 0.35) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")}
    res = _sweep(_runner(mus))
    assert res["S"] == 4 and res["alpha_b"] == pytest.approx(0.05 / 4)


def test_null_when_nothing_survives_the_screen():
    res = _sweep(_runner({}))
    assert res["verdict"] == "null" and res["S"] == 0 and res["K"] == 10


def test_fail_when_a_powered_survivor_is_rejected_in_stage_b():
    res = _sweep(_runner({GOOD: (0.35, -0.3)}))
    assert res["S"] == 1 and res["verdict"] == "fail" and res["confirmed"] == []


def test_indeterminate_not_fail_when_the_survivor_is_underpowered():
    res = _sweep(_runner({GOOD: (0.35, -0.3)}, n_b=40))
    assert res["verdict"] == "indeterminate"
    cell = next(c for c in res["cells"] if c["survived_stage_a"])
    assert cell["stage_b"]["indeterminate_underpowered"] is True


def test_fewer_than_half_producing_is_not_applicable_not_null():
    bad = tuple(f"{f}|{s}|15m" for f in FAMS for s in sw.WAVES[1][:6])
    res = _sweep(_runner({}, fail=bad))
    assert res["verdict"] == "not_applicable" and res["read_state"] == "producer_failed"


def test_unrunnable_cells_are_listed_and_excluded_from_k():
    def check(sym, tf):
        return (sym != "AVAXUSDT", "resolves to a PROXY file")
    res = _sweep(_runner({}), data_check=check)
    assert res["K"] == 8 and res["declared"] == 10
    dead = [c for c in res["cells"] if not c["runnable"]]
    assert len(dead) == 2 and all("PROXY" in c["reason"] for c in dead)


def test_window_edges_are_respected_and_folds_split_the_confirmation_window():
    rows = sw.parse_rows(_rows("2024-10-01", "2026-09-29", 100, 0.1, 1), "15m")
    assert len(sw.in_window(rows, "2025-01-01", "2025-12-31")) < 100
    sums = sw.fold_sums(rows, "2024-10-01", "2026-09-29")
    assert len(sums) == 4 and sum(sums) == pytest.approx(sum(r.net_r for r in rows))


def test_hold_and_qualifying_day_attributes_are_reported():
    lines = [json.dumps({"entry_time": "2025-01-01T00:00:00+00:00", "exit_time": "2025-01-01T03:00:00+00:00",
                         "net_r": 1.5, "entry": 100.0, "sl": 98.0}),      # 3% move, same day
             json.dumps({"entry_time": "2025-01-02T23:00:00+00:00", "exit_time": "2025-01-03T01:00:00+00:00",
                         "net_r": 1.5, "entry": 100.0, "sl": 98.0})]      # crosses midnight
    v = sw.venue_fit(sw.parse_rows(lines, "15m"), "2025-01-01", "2025-01-31")
    assert v["same_utc_day_share"] == 0.5
    assert v["hyro_qualifying_days_per_month_strict"] > 0
    assert v["median_hold_hours"] == pytest.approx(2.5)


def test_real_data_check_refuses_when_no_candles_resolve(tmp_path):
    ok, why = sw.default_data_check("NOTASYMBOLUSDT", "5m")
    assert ok is False and why


def test_cli_list_grid_runs_nothing(capsys, tmp_path):
    assert sw.main(["--out", str(tmp_path), "--list-grid", "--families", "ict_scalp",
                    "--timeframes", "15m"]) == 0
    assert "runnable of" in capsys.readouterr().out


def test_subprocess_runner_builds_the_harness_command_and_strips_the_data_override(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        emit = cmd[cmd.index("--emit-trades") + 1]
        Path(emit).write_text(_rows("2021-01-01", "2021-02-01", 3, 0.1, 1)[0] + "\n")

        class P:
            returncode, stdout, stderr = 0, "", ""
        return P()

    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    monkeypatch.setenv("BACKTEST_DATA_PATH", "/some/fixture.csv")
    cell = sw.Cell("fvg_range", "ETHUSDT", "15m")
    lines = sw.subprocess_runner(60)(cell, "2021-01-01", "2024-09-30")
    cmd = seen["cmd"]
    assert cmd[1].endswith("scripts/backtest_fvg_range.py")
    assert cmd[cmd.index("--symbol") + 1] == "ETHUSDT" and cmd[cmd.index("--timeframe") + 1] == "15m"
    assert cmd[cmd.index("--start") + 1] == "2021-01-01" and cmd[cmd.index("--end") + 1] == "2024-09-30"
    assert ["--exit-style", "far"] == cmd[cmd.index("--exit-style"):cmd.index("--exit-style") + 2]
    assert "--fee-bps-roundtrip" not in cmd                # the verdict arm is the harness default
    assert "BACKTEST_DATA_PATH" not in seen["env"]         # a fleet-wide fixture override must not leak in
    assert len(lines) == 1

    scalp = sw.Cell("ict_scalp", "ETHUSDT", "15m", strategy_name="ict_scalp_eth_15m")
    sw.subprocess_runner(60)(scalp, "2021-01-01", "2024-09-30")
    assert seen["cmd"][seen["cmd"].index("--strategy-name") + 1] == "ict_scalp_eth_15m"


def test_a_failing_harness_is_recorded_per_cell_not_swallowed(monkeypatch):
    def boom(cmd, **kw):
        class P:
            returncode, stdout, stderr = 3, "", "no candles"
        return P()

    monkeypatch.setattr(sw.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="rc=3"):
        sw.subprocess_runner(60)(sw.Cell("pullback", "BTCUSDT", "5m"), "2021-01-01", "2021-02-01")


def test_live_leg_lookup_picks_the_configured_leg_for_that_symbol_and_timeframe():
    strategies = {"ict_scalp_eth_15m": {"timeframe": "15m", "symbols": ["ETHUSDT"]},
                  "ict_scalp_xrp_5m": {"timeframe": "5m", "symbols": ["XRPUSDT"]},
                  "eth_pullback_2h": {"timeframe": "2h", "symbols": ["ETHUSDT"]}}
    assert sw.live_leg_for("ict_scalp", "ETHUSDT", "15m", strategies) == "ict_scalp_eth_15m"
    assert sw.live_leg_for("ict_scalp", "ETHUSDT", "5m", strategies) is None
    assert sw.live_leg_for("pullback", "ETHUSDT", "2h", strategies) == "eth_pullback_2h"


def test_a_null_over_a_partly_failed_grid_is_indeterminate_not_null():
    res = _sweep(_runner({}, fail=("ict_scalp|BTCUSDT|15m",)))
    assert res["S"] == 0 and res["verdict"] == "indeterminate" and res["clean"] is False
    assert res["n_failed_a"] == 1 and res["failed_cells"] == ["ict_scalp|BTCUSDT|15m"]
    assert "NOT a null over K" in res["population"]


def test_a_clean_null_reports_clean_true():
    res = _sweep(_runner({}))
    assert res["verdict"] == "null" and res["clean"] is True and res["failed_cells"] == []


def test_fail_downgrades_to_indeterminate_when_any_stage_a_cell_failed():
    res = _sweep(_runner({GOOD: (0.35, -0.3)}, fail=("fvg_range|BTCUSDT|15m",)))
    assert res["verdict"] == "indeterminate" and res["clean"] is False


def test_a_pass_survives_a_failed_cell_but_is_marked_unclean():
    res = _sweep(_runner({GOOD: (0.35, 0.35)}, fail=("fvg_range|BTCUSDT|15m",)))
    assert res["verdict"] == "pass" and res["clean"] is False and res["n_failed_a"] == 1


def test_stage_b_failure_of_a_survivor_is_counted():
    calls = {"n": 0}
    base = _runner({GOOD: (0.35, 0.35)})

    def run(cell, start, end):
        if cell.key == GOOD and start == sw.STAGE_B_START:
            calls["n"] += 1
            raise RuntimeError("late failure")
        return base(cell, start, end)

    res = _sweep(run)
    assert res["n_failed_b"] == 1 and res["clean"] is False and res["verdict"] == "indeterminate"


def test_fetch_writes_the_resolver_filename_and_records_failures_per_key(tmp_path):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        dest = Path(cmd[cmd.index("--output") + 1])
        sym = cmd[cmd.index("--symbol") + 1]

        class P:
            returncode = 1 if sym == "XRPUSDT" else 0
            stdout, stderr = "", "geoblocked"
        if P.returncode == 0:
            dest.write_text("timestamp,open,high,low,close,volume\n")
        return P()

    out = sw.fetch_candles(("BTCUSDT", "XRPUSDT"), ("5m", "30m"), run=fake, data_dir=tmp_path, log=lambda *_: None)
    assert out["BTCUSDT|5m"]["ok"] and (tmp_path / "BTCUSDT_5m.csv").exists()
    assert out["XRPUSDT|30m"]["ok"] is False and "geoblocked" in out["XRPUSDT|30m"]["detail"]
    c = calls[0]
    assert c[c.index("--source") + 1] == "binance_vision" and c[c.index("--interval") + 1] == "5"
    assert c[c.index("--start-date") + 1] == sw.FETCH_START


def test_a_fetch_failure_makes_the_cell_unrunnable_and_a_mostly_unfetched_grid_is_not_applicable():
    fetched = {f"{s}|15m": {"ok": s == "BTCUSDT", "detail": "403"} for s in sw.WAVES[1]}
    res = _sweep(_runner({}), fetch=lambda: fetched)
    dead = [c for c in res["cells"] if not c["runnable"]]
    assert len(dead) == 8 and all("candle fetch failed" in c["reason"] for c in dead)
    assert res["K"] == 2 and res["verdict"] == "not_applicable" and res["read_state"] == "producer_failed"
    assert "declared cells were runnable" in res["population"]
    assert res["candle_source"].startswith("binance_vision")


def test_a_successful_fetch_is_recorded_and_grades_normally():
    fetched = {f"{s}|15m": {"ok": True, "detail": ""} for s in sw.WAVES[1]}
    res = _sweep(_runner({GOOD: (0.35, 0.35)}), fetch=lambda: fetched)
    assert res["verdict"] in ("pass", "pass_caveated") and res["fetch"] == fetched


def test_a_pass_carried_only_by_live_config_cells_is_pass_caveated():
    strategies = {"ict_scalp_btc_15m": {"timeframe": "15m", "symbols": ["BTCUSDT"]}}
    res = _sweep(_runner({GOOD: (0.35, 0.35)}), strategies=strategies)
    cell = next(c for c in res["cells"] if c["survived_stage_a"])
    assert cell["stage_b_not_fully_oos"] is True and res["verdict"] == "pass_caveated"


def test_a_pass_on_a_default_parameter_cell_is_a_plain_pass():
    res = _sweep(_runner({"fvg_range|BTCUSDT|15m": (0.35, 0.35)}))
    cell = next(c for c in res["cells"] if c["survived_stage_a"])
    assert cell["stage_b_not_fully_oos"] is False and res["verdict"] == "pass"


def test_a_shortened_stage_b_is_a_smoke_run(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(sw, "sweep", lambda **kw: seen.update(kw) or {"verdict": "null", "read_state": "measured", "K": 3, "population": "p"})
    sw.main(["--out", str(tmp_path), "--stage-b-end", "2025-06-30"])
    assert seen["smoke"] is True
    seen.clear()
    sw.main(["--out", str(tmp_path)])
    assert seen["smoke"] is False


def test_the_registered_504_and_505_commands_are_not_smoke_runs(tmp_path, monkeypatch):
    """Run 36691408944 was labelled SMOKE because --timeframes 5m,15m,30m != the 4-timeframe default."""
    seen = {}
    monkeypatch.setattr(sw, "sweep", lambda **kw: seen.update(kw) or {"verdict": "null", "read_state": "measured", "K": 3, "population": "p"})
    for tfs, expect_smoke in (("5m,15m,30m", False), ("1m", False), ("5m", True), ("15m,30m", True)):
        seen.clear()
        sw.main(["--out", str(tmp_path), "--timeframes", tfs])
        assert seen["smoke"] is expect_smoke, tfs


# ---- the collector contract (RQ-20260930-501 was mislabelled producer_failed by exactly this class of bug) ----
_sr_spec = importlib.util.spec_from_file_location("script_run_sweep_contract", REPO / "scripts/research/script_run.py")
sr = importlib.util.module_from_spec(_sr_spec)
sys.modules["script_run_sweep_contract"] = sr
_sr_spec.loader.exec_module(sr)


def _derive(tmp_path, verdict):
    from types import SimpleNamespace
    out = Path("out")
    (tmp_path / out).mkdir(parents=True, exist_ok=True)
    (tmp_path / out / "verdict.json").write_text(json.dumps(verdict, default=str))
    (tmp_path / out / "run-manifest.json").write_text(json.dumps(
        {"all_ok": True, "commands": [{"index": 0, "argv": ["python3", "x.py"], "exit_code": 0}]}))
    plan = SimpleNamespace(unit="RQ-20260930-504", out_dir=out, rule_id="RULE-X", rule_registered_at="2026-09-30")
    return sr.derive_record(plan, repo=tmp_path)


@pytest.mark.parametrize("name,runner_mus,kw,want_verdict,want_state", [
    ("pass", {"fvg_range|BTCUSDT|15m": (0.35, 0.35)}, {}, "pass", "measured"),
    ("pass_caveated", {GOOD: (0.35, 0.35)}, {"strategies": {"ict_scalp_btc_15m": {"timeframe": "15m",
                                                                                   "symbols": ["BTCUSDT"]}}},
     "pass", "measured"),
    ("fail", {GOOD: (0.35, -0.3)}, {}, "fail", "measured"),
    ("null", {}, {}, "no_action_warranted", "measured"),
])
def test_every_verdict_lands_in_the_collectors_closed_vocabulary(tmp_path, name, runner_mus, kw, want_verdict,
                                                                  want_state):
    res = _sweep(_runner(runner_mus), **kw)
    rec = _derive(tmp_path, sw.to_result_verdict(res))
    assert rec["read_state"] == want_state and rec["verdict"] == want_verdict, (name, rec)
    assert rec["n"] == "10" and "malformed" not in rec.get("note", "")
    if name == "pass_caveated":
        assert "CAVEATED" in rec["note"]


def test_indeterminate_and_not_applicable_land_correctly(tmp_path):
    ind = _derive(tmp_path, sw.to_result_verdict(_sweep(_runner({GOOD: (0.35, -0.3)}, n_b=40))))
    assert (ind["read_state"], ind["verdict"]) == ("measured", "indeterminate")
    bad = tuple(f"{f}|{s}|15m" for f in FAMS for s in sw.WAVES[1][:6])
    na = _derive(tmp_path / "na", sw.to_result_verdict(_sweep(_runner({}, fail=bad))))
    assert (na["read_state"], na["verdict"], na["n"]) == ("producer_failed", "not_applicable", "null")


def test_an_unclean_or_smoke_result_says_so_in_the_note(tmp_path):
    res = _sweep(_runner({}, fail=("ict_scalp|BTCUSDT|15m",)))
    v = sw.to_result_verdict(res)
    assert v["verdict"] == "indeterminate" and "UNCLEAN" in v["note"]
    res["read_state"] = "smoke"
    v = sw.to_result_verdict(res)
    assert v["population"].startswith("SMOKE RUN") and "SMOKE RUN" in v["note"]
