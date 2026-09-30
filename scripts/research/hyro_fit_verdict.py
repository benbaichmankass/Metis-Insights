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
``--from-ledger`` skips the builder and grades committed ledgers (used by the tests; it is a
smoke path, NOT a pre-registered run).

Writes ``<out>/verdict.json`` = {verdict, read_state, population, n, ...}.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
import subprocess
import sys
from datetime import datetime
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


def spec_from_ledgers(name: str, paths: List[str]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows += [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
    rows.sort(key=lambda r: _ts(r["entry_time"]))
    e = [_ts(r["entry_time"]) for r in rows]
    x = [_ts(r["exit_time"]) for r in rows]
    span = max((max(x) - min(e)).days, 1)
    return dict(
        name=name, n=len(rows), span_days=span, block_len=4,
        r_samples=[float(r["net_r"]) for r in rows],
        trades_per_day=len(rows) / span,
        same_day_frac=sum(a.date() == b.date() for a, b in zip(e, x)) / len(rows),
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
        out.update(verdict="indeterminate", read_state="underpowered")
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
    out.update(verdict="pass" if ok else "fail", read_state="graded",
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


def harness_cmd(leg: str, params: Dict[str, str], start: str, end: str, emit: str, js: str,
                *, limit: bool) -> List[str]:
    cmd = [sys.executable, str(REPO / "scripts/backtest_ict_scalp.py"), "--symbol", params["symbol"],
           "--timeframe", params["timeframe"], "--start", start, "--end", end, "--strategy-name", leg,
           "--emit-trades", emit, "--json", js]
    if limit:
        cmd += ["--entry-mode", "limit"]
    else:
        cmd += ["--fee-bps-roundtrip", str(HYRO_MARKET_ROUNDTRIP_BPS)]
    return cmd


def run_limit_legs(legs: List[str], days: int, out: Path, *, run=subprocess.run,
                   today: "datetime | None" = None,
                   strategies: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run each leg in limit mode (the verdict arm) and in market mode at 11 bps (report only)."""
    from datetime import timedelta, timezone
    end_d = (today or datetime.now(timezone.utc)).date() - timedelta(days=1)
    start = (end_d - timedelta(days=days)).isoformat()
    end = end_d.isoformat()
    res: Dict[str, Any] = {"ledgers": [], "signals": 0, "filled": 0, "market_arm_11bps": {"n": 0, "net_total_r": 0.0},
                           "legs": {}, "failed": None, "window": [start, end]}
    for leg in legs:
        params = leg_params(leg, strategies)
        lim_emit, lim_js = str(out / f"{leg}__limit.jsonl"), str(out / f"{leg}__limit.json")
        mkt_emit, mkt_js = str(out / f"{leg}__market11.jsonl"), str(out / f"{leg}__market11.json")
        for limit, emit, js in ((True, lim_emit, lim_js), (False, mkt_emit, mkt_js)):
            p = run(harness_cmd(leg, params, start, end, emit, js, limit=limit),
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
    if v.get("read_state") == "underpowered":
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
    ap.add_argument("--mode", choices=("evidence", "limit"), default="evidence",
                    help="evidence = RQ-20260930-501 (default); limit = RQ-20260930-502 maker-entry rule")
    ap.add_argument("--from-ledger", action="append", default=None,
                    help="SMOKE PATH: grade these committed ledgers instead of running the builder")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.mode == "limit":
        stats = run_limit_legs(a.leg, a.days, out)
        if stats["failed"] or not stats["ledgers"]:
            v = dict(verdict="not_applicable", read_state="producer_failed",
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
                v = dict(verdict="not_applicable", read_state="producer_failed", leg=leg,
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
