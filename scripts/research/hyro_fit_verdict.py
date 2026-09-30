#!/usr/bin/env python3
"""HyroTrader rule-fit verdict for one or more strategy legs (lane HYRO-STRATS, Tier-1).

Runs the cost-complete evidence builder (``scripts/ops/build_strategy_evidence.py``, fee +
slippage + funding via ``execution_costs``) over a chosen window, pools the emitted per-trade
ledgers, and grades them against the rule registered in
``research/queue/RQ-20260930-501.yaml`` using ``scripts/research/hyro_passprob_mc.py``.

The grading numbers are constants HERE and in the queue unit, fixed BEFORE any run:
  n floor            300 pooled trades (below -> UNDERPOWERED, never FAIL)
  gate A (edge)      pooled full-cost net R > 0
  gate B (rule-fit)  strict ruleset (config/prop_rulesets/hyrotrader.yaml), reading A,
                     risk 1.0%/trade, flag_frac 0.0 (BEST CASE: no fill is flagged), 720-day per-phase horizon
                     (the firm sets no time limit), P(pass BOTH eval phases) >= 0.25 on ALL seeds
  gate C (fragility) same, with every sampled R shifted by -0.03, P(both) >= 0.10 on ALL seeds
  0.25 is the break-even P(both) at the $59 deposit: refundable on first payout, so a failed
  attempt costs $59 and a passed one nets ~$200 (5% x $5k x 80% split) in the first 90 funded days:
  P > 59 / (200 + 59) = 0.228, rounded up.
``--mode limit`` (RQ-20260930-502): instead of the evidence builder, runs ``scripts/backtest_ict_scalp.py
--entry-mode limit`` per leg (post-only limit at the signal bar close, fills only when price trades THROUGH it
within 3 bars, maker 2.0 + taker 5.5 bps = HyroTrader's published schedule, slippage on the taker exit only)
and grades the FILLED ledger with the same gates B and C plus two more, all registered before any run:
  fill rate         n_filled / n_signals >= 0.50
  edge per signal   net_total_r_filled / n_signals > 0 (a book that fills 40% and wins on those is not the book
                    that fills 90%; the per-signal figure is the honest one)
Reported beside it, never gating: the same legs in MARKET mode charged HyroTrader's 11 bps taker-taker round trip
(``--fee-bps-roundtrip 11``), so the question "does maker entry beat what HyroTrader would charge a market fill"
is answered from the same window.
``--mode dayflat`` (RQ-20260930-503): runs ``scripts/backtest_trend.py`` on the native 1h candles of one
configured trend leg (no ``--resample``) with ``--flat-at-utc 23:45 --no-entry-after-utc 20:00``, then
grades that ledger with the base gates plus a same-UTC-day share >= 0.95 (the registered rule).
``--from-ledger`` skips the builder and grades committed ledgers (used by the tests; it is a
smoke path, NOT a pre-registered run).

Writes ``<out>/verdict.json`` = {verdict, read_state, population, n, ...} in the research-result contract
(scripts/research/script_run.py::derive_record): verdict in {pass, fail, no_action_warranted, indeterminate,
not_applicable}; read_state in {measured, no_data, producer_failed, not_attempted}; `measured` needs an integer n,
the other three need n null and verdict not_applicable. (A first version wrote read_state "graded" and the collector
landed the RQ-20260930-501 PASS as producer_failed; tests/test_hyro_verdict_contract.py now runs the real
derive_record over what this script writes.)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_s = importlib.util.spec_from_file_location("hyro_mc", REPO / "scripts/research/hyro_passprob_mc.py")
mc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(mc)

N_FLOOR, P_B, P_C, SHIFT_C, FLAG, RISK, CAP = 300, 0.25, 0.10, -0.03, 0.0, 1.0, 720
SEEDS = (1, 2, 3)


def _ts(x: Any) -> datetime:
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def spec_from_ledgers(name: str, paths: List[str], entry_offset: "timedelta | None" = None) -> Dict[str, Any]:
    """``entry_offset`` shifts the ENTRY MOMENT used for the same-UTC-day share only. Native-candle ledgers
    stamp entry_time with the signal bar's OPEN, but the fill is that bar's close (open + one bar), which is
    the entry moment the day-flat rule is written against (RQ-20260930-503)."""
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows += [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
    rows.sort(key=lambda r: _ts(r["entry_time"]))
    e = [_ts(r["entry_time"]) for r in rows]
    x = [_ts(r["exit_time"]) for r in rows]
    span = max((max(x) - min(e)).days, 1)
    e_fill = [t + entry_offset for t in e] if entry_offset else e
    return dict(
        name=name, n=len(rows), span_days=span, block_len=4,
        r_samples=[float(r["net_r"]) for r in rows],
        trades_per_day=len(rows) / span,
        same_day_frac=sum(a.date() == b.date() for a, b in zip(e_fill, x)) / len(rows),
        stop_pct=st.median(abs(r["entry"] - r["sl"]) / r["entry"] * 100 for r in rows),
    )


def grade(spec: Dict[str, Any], ruleset_path: str) -> Dict[str, Any]:
    from src.prop.ruleset import load_ruleset
    rs = load_ruleset(REPO / ruleset_path)
    n = spec["n"]
    net = sum(spec["r_samples"])
    out: Dict[str, Any] = dict(n=n, net_r=round(net, 3), span_days=spec["span_days"],
                               population=f"{spec['name']} ledger, {spec['span_days']}d, n={n}")
    if n < N_FLOOR:
        # `read_state` is the research-result contract's closed set (measured / no_data / producer_failed /
        # not_attempted). An underpowered run DID measure something (n is an integer), so it is `measured`
        # with verdict `indeterminate`; the word "underpowered" lives in `note`.
        out.update(verdict="indeterminate", read_state="measured",
                   note=f"underpowered: n={n} < N_FLOOR={N_FLOOR}; the floor is never lowered")
        return out
    kw = dict(n_paths=1500, risk_pct=RISK, reading="A", leverage=10.0, winner_mae_r=0.3,
              loser_mfe_r=0.3, flag_frac=FLAG)
    kw.update(p1_cap=CAP, p2_cap=CAP)
    b = [mc.run(dict(spec), rs, seed=s, **kw)["p_pass_both_phases"] for s in SEEDS]
    c = [mc.run(dict(spec, r_shift=SHIFT_C), rs, seed=s, **kw)["p_pass_both_phases"] for s in SEEDS]
    # INFORMATIONAL, not a gate: the same book if half its market fills are flagged (profit credit x0.4)
    info = mc.run(dict(spec), rs, seed=1, **dict(kw, flag_frac=0.5))["p_pass_both_phases"]
    out.update(p_both_gateB=b, p_both_gateC=c, info_p_both_if_half_of_fills_flagged=info)
    ok = net > 0 and min(b) >= P_B and min(c) >= P_C
    out.update(verdict="pass" if ok else "fail", read_state="measured",
               gates=dict(A_net_r_positive=net > 0, B_all_seeds_ge_0_25=min(b) >= P_B,
                          C_all_seeds_ge_0_10=min(c) >= P_C))
    return out


FILL_RATE_FLOOR = 0.50
HYRO_MARKET_ROUNDTRIP_BPS = 11.0


def leg_params(leg: str, strategies: Dict[str, Any] | None = None) -> Dict[str, str]:
    """(symbol, timeframe) of a configured leg, read from config/strategies.yaml."""
    if strategies is None:
        import yaml
        strategies = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())["strategies"]
    cfg = strategies[leg]
    return {"symbol": str(cfg["symbols"][0]), "timeframe": str(cfg["timeframe"])}


INTERVAL_CODE = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60"}
FETCH_START = "2020-12-01"      # a month of warm-up before the earliest window


def fetch_candle_file(symbol: str, timeframe: str, data_dir: Path, *, run=subprocess.run,
                      timeout_s: int = 3600) -> "tuple[Path | None, str]":
    """Binance-vision candles into data_dir/{SYMBOL}_{tf}.csv (the runner has no data/ checkout).
    Never raises: returns (None, reason) on any failure so the run is reported producer_failed."""
    code = INTERVAL_CODE.get(timeframe)
    if code is None:
        return None, f"no interval code for {timeframe}"
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / f"{symbol}_{timeframe}.csv"
    cmd = [sys.executable, str(REPO / "scripts/ops/fetch_backtest_candles.py"), "--symbol", symbol,
           "--source", "binance_vision", "--interval", code, "--start-date", FETCH_START, "--output", str(dest)]
    try:
        p = run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return None, f"fetch of {symbol} {timeframe} timed out after {timeout_s}s"
    if p.returncode != 0 or not dest.exists():
        return None, f"fetch of {symbol} {timeframe} failed rc={p.returncode} {(p.stderr or p.stdout or '')[-200:]}"
    return dest, ""


def harness_cmd(leg: str, params: Dict[str, str], start: str, end: str, emit: str, js: str,
                *, limit: bool, data: "str | None" = None) -> List[str]:
    cmd = [sys.executable, str(REPO / "scripts/backtest_ict_scalp.py"), "--symbol", params["symbol"],
           "--timeframe", params["timeframe"], "--start", start, "--end", end, "--strategy-name", leg,
           "--emit-trades", emit, "--json", js]
    if data:
        cmd += ["--data", data]
    if limit:
        cmd += ["--entry-mode", "limit"]
    else:
        cmd += ["--fee-bps-roundtrip", str(HYRO_MARKET_ROUNDTRIP_BPS)]
    return cmd


def run_limit_legs(legs: List[str], days: int, out: Path, *, run=subprocess.run,
                   today: "datetime | None" = None,
                   strategies: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run each leg in limit mode (the verdict arm) and in market mode at 11 bps (report only)."""
    from datetime import timezone
    end_d = (today or datetime.now(timezone.utc)).date() - timedelta(days=1)
    start = (end_d - timedelta(days=days)).isoformat()
    end = end_d.isoformat()
    res: Dict[str, Any] = {"ledgers": [], "signals": 0, "filled": 0, "market_arm_11bps": {"n": 0, "net_total_r": 0.0},
                           "legs": {}, "failed": None, "window": [start, end]}
    for leg in legs:
        params = leg_params(leg, strategies)
        data_file, why = fetch_candle_file(params["symbol"], params["timeframe"], out / "data", run=run)
        if data_file is None:
            res["failed"] = f"{leg}: {why}"
            return res
        lim_emit, lim_js = str(out / f"{leg}__limit.jsonl"), str(out / f"{leg}__limit.json")
        mkt_emit, mkt_js = str(out / f"{leg}__market11.jsonl"), str(out / f"{leg}__market11.json")
        for limit, emit, js in ((True, lim_emit, lim_js), (False, mkt_emit, mkt_js)):
            p = run(harness_cmd(leg, params, start, end, emit, js, limit=limit, data=str(data_file)),
                    capture_output=True, text=True, cwd=str(REPO))
            if p.returncode != 0 or not Path(js).exists():
                res["failed"] = f"{leg} {'limit' if limit else 'market11'}: rc={p.returncode} {(p.stderr or '')[-300:]}"
                return res
        lj = json.loads(Path(lim_js).read_text())
        le = lj["limit_entry"]
        res["ledgers"].append(lim_emit)
        res["signals"] += int(le["n_signals"])
        res["filled"] += int(le["n_filled"])
        res["legs"][leg] = {k: le[k] for k in ("n_signals", "n_filled", "fill_rate", "net_total_r_filled",
                                                 "net_r_per_signal")}
        mj = json.loads(Path(mkt_js).read_text())
        res["market_arm_11bps"]["n"] += int(mj.get("total_trades") or 0)
        res["market_arm_11bps"]["net_total_r"] = round(
            res["market_arm_11bps"]["net_total_r"] + float(mj.get("net_total_r") or 0.0), 4)
    return res


DAYFLAT_SAME_DAY_MIN = 0.95
DAYFLAT_FLAT_AT, DAYFLAT_NO_ENTRY_AFTER = "23:45", "20:00"
DAYFLAT_BAR = timedelta(hours=1)          # registered: NATIVE 1h candles, bar_label open


def _load_rdm():
    sys.path.insert(0, str(REPO / "scripts" / "research"))
    import regime_debt_matrix
    return regime_debt_matrix


def dayflat_cmd(leg: str, cfg: Dict[str, Any], csv: str, emit: str, js: str, start: str, end: str):
    """Returns (argv, levers the harness does not model). The leg's live-parameter trend argv (regime_debt_matrix.build_harness_cmd) on NATIVE candles: the
    ``--resample`` pair it always adds is removed (RQ-503 registers native 1h, bar_label open), and the two
    day-flat levers are appended."""
    rdm = _load_rdm()
    argv, _faithful, omitted = rdm.build_harness_cmd(leg, cfg, "trend", csv, cfg["timeframe"], emit, js)
    if "--resample" in argv:
        i = argv.index("--resample")
        del argv[i:i + 2]
    argv += ["--strategy-name", leg, "--start", start, "--end", end,
             "--flat-at-utc", DAYFLAT_FLAT_AT, "--no-entry-after-utc", DAYFLAT_NO_ENTRY_AFTER]
    return argv, omitted


def run_dayflat(leg: str, days: int, out: Path, *, run=subprocess.run, today: "datetime | None" = None,
                strategies: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from datetime import timezone
    if strategies is None:
        import yaml
        strategies = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())["strategies"]
    cfg = strategies[leg]
    end_d = (today or datetime.now(timezone.utc)).date() - timedelta(days=1)
    start, end = (end_d - timedelta(days=days)).isoformat(), end_d.isoformat()
    res: Dict[str, Any] = {"ledgers": [], "failed": None, "window": [start, end], "leg": leg}
    csv, why = fetch_candle_file(str(cfg["symbols"][0]), str(cfg["timeframe"]), out / "data", run=run)
    if csv is None:
        res["failed"] = f"{leg}: {why}"
        return res
    emit, js = str(out / f"{leg}__dayflat.jsonl"), str(out / f"{leg}__dayflat.json")
    argv, omitted = dayflat_cmd(leg, cfg, str(csv), emit, js, start, end)
    p = run(argv, capture_output=True, text=True, cwd=str(REPO))
    if p.returncode != 0 or not Path(emit).exists():
        res["failed"] = f"{leg} dayflat: rc={p.returncode} {(p.stderr or '')[-300:]}"
        return res
    res["ledgers"].append(emit)
    res["levers_not_modelled_by_harness"] = omitted
    return res


def grade_dayflat(spec: Dict[str, Any], stats: Dict[str, Any], ruleset_path: str) -> Dict[str, Any]:
    """RQ-20260930-503 rule: base gates (n floor, net R > 0, B, C) + same-UTC-day share >= 0.95."""
    v = grade(spec, ruleset_path)
    share = float(spec["same_day_frac"])
    v.update(same_utc_day_share=round(share, 4), window=stats.get("window"),
             levers_not_modelled_by_harness=stats.get("levers_not_modelled_by_harness"))
    if v["verdict"] == "indeterminate":          # underpowered: never flipped to FAIL
        return v
    ok = v["verdict"] == "pass" and share >= DAYFLAT_SAME_DAY_MIN
    v["verdict"] = "pass" if ok else "fail"
    v.setdefault("gates", {}).update(same_day_share_ge_0_95=share >= DAYFLAT_SAME_DAY_MIN)
    return v


def grade_limit(spec: Dict[str, Any], stats: Dict[str, Any], ruleset_path: str) -> Dict[str, Any]:
    """RQ-20260930-502 rule: base gates (n floor, net R > 0, B, C) + fill rate + edge per signal."""
    v = grade(spec, ruleset_path)
    signals, filled = int(stats["signals"]), int(stats["filled"])
    fill_rate = (filled / signals) if signals else 0.0
    net = sum(spec["r_samples"])
    per_signal = (net / signals) if signals else 0.0
    v.update(n_signals=signals, n_filled=filled, fill_rate=round(fill_rate, 4),
             net_r_per_signal=round(per_signal, 5), legs=stats.get("legs"),
             market_arm_11bps_reported_not_gating=stats.get("market_arm_11bps"),
             window=stats.get("window"))
    if v["verdict"] == "indeterminate":          # underpowered (n below the floor): never flipped to FAIL
        return v
    ok = v["verdict"] == "pass" and fill_rate >= FILL_RATE_FLOOR and per_signal > 0
    v["verdict"] = "pass" if ok else "fail"
    v.setdefault("gates", {}).update(fill_rate_ge_0_50=fill_rate >= FILL_RATE_FLOOR,
                                     net_r_per_signal_positive=per_signal > 0)
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leg", action="append", required=True)
    ap.add_argument("--days", type=int, default=1830)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ruleset", default="config/prop_rulesets/hyrotrader.yaml")
    ap.add_argument("--mode", choices=("evidence", "limit", "dayflat"), default="evidence",
                    help="evidence = RQ-20260930-501 (default); limit = RQ-20260930-502 maker-entry rule")
    ap.add_argument("--from-ledger", action="append", default=None,
                    help="SMOKE PATH: grade these committed ledgers instead of running the builder")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.mode == "dayflat":
        stats = run_dayflat(a.leg[0], a.days, out)
        if stats["failed"] or not stats["ledgers"]:
            v = dict(verdict="not_applicable", read_state="producer_failed", n=None,
                     population=f"dayflat harness run failed: {stats['failed']}")
        else:
            v = grade_dayflat(spec_from_ledgers(a.leg[0], stats["ledgers"], entry_offset=DAYFLAT_BAR), stats, a.ruleset)
        (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n")
        print(json.dumps(v, indent=1))
        return 0
    if a.mode == "limit":
        stats = run_limit_legs(a.leg, a.days, out)
        if stats["failed"] or not stats["ledgers"]:
            v = dict(verdict="not_applicable", read_state="producer_failed", n=None,
                     population=f"limit-mode harness run failed: {stats['failed']}")
        else:
            v = grade_limit(spec_from_ledgers("+".join(a.leg), stats["ledgers"]), stats, a.ruleset)
        (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n")
        print(json.dumps(v, indent=1))
        return 0
    if a.from_ledger:
        ledgers = a.from_ledger
    else:
        cmd = [sys.executable, str(REPO / "scripts/ops/build_strategy_evidence.py"),
               "--days", str(a.days), "--out", str(out / "records"), "--workdir", str(out / "runs")]
        for leg in a.leg:
            cmd += ["--strategy", leg]
        r = subprocess.run(cmd, capture_output=True, text=True)
        (out / "builder.log").write_text(r.stdout + "\n" + r.stderr)
        ledgers = []
        for leg in a.leg:
            rec = out / "records" / f"{leg}.json"
            src = json.loads(rec.read_text()).get("source_run") if rec.exists() else None
            if not src or not Path(src).exists():
                v = dict(verdict="not_applicable", read_state="producer_failed", n=None, leg=leg,
                         population=f"{leg}: no ledger emitted (builder rc={r.returncode})")
                (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n")
                print(json.dumps(v))
                return 0
            ledgers.append(src)
    v = grade(spec_from_ledgers("+".join(a.leg), ledgers), a.ruleset)
    (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n")
    print(json.dumps(v, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
