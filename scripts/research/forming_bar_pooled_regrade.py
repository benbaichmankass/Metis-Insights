#!/usr/bin/env python3
"""Pooled forming-bar vs closed-bar re-grade across the variant-builder legs.

RQ-20260929-302 / RULE-RQ0929-302-POOLED-FORMING / PI-20260929-VOLSKIP-SIGNAL-0002.

For every leg the live pullback / donchian VARIANT builders evaluate on a
forming bar, run the Stage-0 harness twice — ``decision_bar="closed"`` (Stage 0
today) and ``decision_bar="forming"`` (the live decision, from
scripts/research/forming_bar_entries.py) — with harness kwargs generated from
the leg's config/strategies.yaml block and the full cost stack; pool the net-R
difference across legs; and restate each real-money leg's current evidence
record (comms/strategy_evidence/<leg>.json) under forming-bar mode.

Data: Binance USD-M 1m klines, a LABELLED PROXY for Bybit.
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "scripts"))
import vol_skip_forming_bar_replay as vs  # noqa: E402  (frame builder, cost setter, harness loaders)
sys.path.insert(0, str(ROOT))  # vs prepends scripts/ again; ROOT first (scripts/ml shadows ml)
import forming_bar_entries as fbe  # noqa: E402

BOOT_N = 10_000
BOOT_SEED = 302

# (leg, harness family). The population is registered in the queue unit.
POPULATION: List[Tuple[str, str]] = [
    ("eth_pullback_2h", "pullback"), ("eth_pullback_prop_2h", "pullback"),
    ("sol_pullback_2h", "pullback"), ("xrp_pullback_2h", "pullback"),
    ("ada_pullback_2h", "pullback"), ("avax_pullback_2h", "pullback"),
    ("trend_donchian_sol", "trend"), ("trend_donchian_eth", "trend"),
    ("trend_donchian_sol_prop", "trend"), ("trend_donchian_eth_prop", "trend"),
    ("trend_donchian_eth_4h", "trend"), ("trend_donchian_sol_4h", "trend"),
    ("trend_donchian_xrp_4h", "trend"), ("trend_donchian_ada_4h", "trend"),
    ("trend_donchian_avax_4h", "trend"),
]
REAL_MONEY_ACCOUNT = "bybit_2"

# Fixed live conventions, not per-leg YAML: the resting Bybit TP clamp
# (_TP_SENTINEL_CAP_PCT in both units), and RQ-201/301's timeout/cooldown.
FIXED = {"tp_cap_pct": 0.099, "timeout_bars": 200, "cooldown_bars": 1}
# YAML keys that are metadata / routing / live-only, not harness levers.
NON_LEVER = {"model", "signal_prefixes", "enabled", "execution", "timeframe",
             "symbols", "tp_intent", "long_only", "side_filter"}


def harness_kwargs(block: dict, family: str) -> Tuple[Dict[str, Any], List[str]]:
    """Harness kwargs from the leg's YAML block; returns (kwargs, unmodelled
    lever keys). Only keys the harness's run_backtest accepts are passed."""
    mod = vs.backtest_pullback if family == "pullback" else vs.backtest_trend
    accepted = set(inspect.signature(mod.run_backtest).parameters)
    kw: Dict[str, Any] = dict(FIXED)
    unmodelled = []
    for k, v in block.items():
        if k in NON_LEVER:
            continue
        if k in accepted:
            kw[k] = str(v) if k == "skip_hours" else v
        else:
            unmodelled.append(k)
    kw["side_filter"] = fbe.resolve_side_filter(block)
    if "long_only" in accepted:
        kw["long_only"] = False  # folded into side_filter above
    for k in ("adx_min", "adx_max"):
        if kw.get(k) is not None:
            kw[k] = float(kw[k])
    return kw, sorted(unmodelled)


def _run(mod: Any, bars: pd.DataFrame, leg: str, sym: str, tf: str, kw: dict,
         emit: Path, **extra: Any) -> List[dict]:
    mod.run_backtest(bars[["timestamp", "open", "high", "low", "close"]].copy(),
                     timeframe=tf, symbol=sym, emit_path=str(emit),
                     strategy_name=leg, **kw, **extra)
    return [json.loads(x) for x in emit.read_text().splitlines() if x.strip()]


def _summ(ts: List[dict]) -> Dict[str, Any]:
    return {"n": len(ts), "net_r": round(sum(t["net_r"] for t in ts), 4)}


def _key(t: dict) -> Tuple[str, str]:
    return (t["entry_time"], t["direction"])


def _month(ts: List[dict]) -> pd.Series:
    if not ts:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([t["entry_time"] for t in ts], utc=True).tz_localize(None).to_period("M")
    return pd.Series([t["net_r"] for t in ts], index=idx).groupby(level=0).sum()


def _window(ts: List[dict], lo: str, hi: str) -> List[dict]:
    a, b = pd.Timestamp(lo), pd.Timestamp(hi)
    return [t for t in ts if a <= pd.Timestamp(t["entry_time"]) <= b]


def real_money_legs() -> set:
    from src.config.accounts_loader import load_accounts_dict
    acc = load_accounts_dict()
    if REAL_MONEY_ACCOUNT not in acc:
        # The loader returns {} on a read failure; "no real-money legs" must not
        # be what that reads as.
        raise SystemExit(f"accounts.yaml unreadable or has no {REAL_MONEY_ACCOUNT}")
    a = acc[REAL_MONEY_ACCOUNT]
    return {s if isinstance(s, str) else s.get("name") for s in (a.get("strategies") or [])}


def leg_run(leg: str, family: str, block: dict, m1: pd.DataFrame, tmp: Path,
            workers: int) -> Dict[str, Any]:
    tf = str(block["timeframe"])
    sym = str(block["symbols"][0])
    bars = vs.build_bars(m1, fbe.tf_minutes(tf))
    mod = vs.backtest_pullback if family == "pullback" else vs.backtest_trend
    costs = vs._set_costs(mod, sym)
    kw, unmodelled = harness_kwargs(block, family)
    a = _run(mod, bars, leg, sym, tf, kw, tmp / f"{leg}.A.jsonl")
    b = _run(mod, bars, leg, sym, tf, kw, tmp / f"{leg}.B.jsonl",
             decision_bar="forming", decision_1m=m1, decision_cfg=block,
             decision_workers=workers)
    first = str(bars["timestamp"].iloc[fbe.WINDOW - 1])
    a = [t for t in a if t["entry_time"] >= first]
    b = [t for t in b if t["entry_time"] >= first]
    ka, kb = {_key(t) for t in a}, {_key(t) for t in b}
    return {"leg": leg, "family": family, "symbol": sym, "timeframe": tf,
            "execution": block.get("execution"), "harness_kwargs": kw,
            "unmodelled_yaml_levers": unmodelled, "costs": costs,
            "bars": len(bars), "first_decision_bar": first,
            "last_bar": str(bars["timestamp"].iloc[-1]),
            "A_closed": _summ(a), "B_forming": _summ(b),
            "shared_n": len(ka & kb), "union_n": len(ka | kb),
            "jaccard": round(len(ka & kb) / len(ka | kb), 4) if ka | kb else None,
            "delta_net_r": round(_summ(b)["net_r"] - _summ(a)["net_r"], 4),
            "_A": a, "_B": b}


def pooled_stats(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    lo = min(pd.Timestamp(r["first_decision_bar"]) for r in runs).tz_localize(None).to_period("M")
    hi = max(pd.Timestamp(r["last_bar"]) for r in runs).tz_localize(None).to_period("M")
    months = pd.period_range(lo, hi, freq="M")
    dm = pd.Series(0.0, index=months)
    for r in runs:
        dm = dm.add(_month(r["_B"]).reindex(months, fill_value=0.0), fill_value=0.0)
        dm = dm.sub(_month(r["_A"]).reindex(months, fill_value=0.0), fill_value=0.0)
    d = float(dm.sum())
    rng = np.random.default_rng(BOOT_SEED)
    draws = dm.to_numpy()[rng.integers(0, len(dm), size=(BOOT_N, len(dm)))].sum(axis=1)
    ci = [round(float(x), 4) for x in np.percentile(draws, [5, 95])]
    se = float(draws.std(ddof=1))
    dy = dm.groupby(dm.index.year).sum()
    max_year = int(dy.abs().idxmax())
    per_leg = {r["leg"]: r["delta_net_r"] for r in runs}
    max_leg = max(per_leg, key=lambda k: abs(per_leg[k]))
    shared = sum(r["shared_n"] for r in runs)
    union = sum(r["union_n"] for r in runs)
    return {"legs": len(runs), "months": len(dm),
            "A_closed": {"n": sum(r["A_closed"]["n"] for r in runs),
                         "net_r": round(sum(r["A_closed"]["net_r"] for r in runs), 4)},
            "B_forming": {"n": sum(r["B_forming"]["n"] for r in runs),
                          "net_r": round(sum(r["B_forming"]["net_r"] for r in runs), 4)},
            "jaccard": round(shared / union, 4) if union else None,
            "delta_net_r": round(d, 4), "ci90": ci, "boot_se": round(se, 4),
            "mde_80pct_power": round((1.645 + 0.842) * se, 4),
            "per_year_delta": {str(k): round(float(v), 4) for k, v in dy.items()},
            "max_year": max_year, "ex_max_year_delta": round(d - float(dy[max_year]), 4),
            "max_leg": max_leg, "ex_max_leg_delta": round(d - per_leg[max_leg], 4),
            "legs_forming_worse": sum(1 for v in per_leg.values() if v < 0),
            "legs_forming_better": sum(1 for v in per_leg.values() if v > 0)}


def grade(p: Dict[str, Any]) -> Dict[str, Any]:
    lo, hi = p["ci90"]
    if (hi < 0 and p["ex_max_year_delta"] < 0 and p["ex_max_leg_delta"] < 0
            and abs(p["delta_net_r"]) >= 10.0):
        v = "MOVE_LIVE_TO_CLOSED"
    elif lo > 0 and p["ex_max_year_delta"] > 0 and p["ex_max_leg_delta"] > 0:
        v = "FORMING_ROBUSTLY_BETTER"
    else:
        v = "INCONCLUSIVE"
    return {"verdict": v, "underpowered": abs(p["delta_net_r"]) < p["mde_80pct_power"]}


def restate(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    path = ROOT / "comms/strategy_evidence" / f"{run['leg']}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text())
    src = ROOT / rec["cost_stack"]["source"] if rec.get("cost_stack") else None
    bt = json.loads(src.read_text()) if src and src.exists() else {}
    lo, hi = bt.get("data_start"), bt.get("data_end")
    if not lo or not hi:
        return {"record": str(path.relative_to(ROOT)), "error": "record window unreadable"}
    a, b = _window(run["_A"], lo, hi), _window(run["_B"], lo, hi)
    fa, fb = _summ(a), _summ(b)
    passes = (rec.get("decision_rule") or {}).get("verdict") == "pass"
    return {"record": str(path.relative_to(ROOT)), "window": [lo, hi],
            "record_net_r_oos": rec.get("net_r_oos"), "record_n": rec.get("n_trades_oos"),
            "record_verdict": (rec.get("decision_rule") or {}).get("verdict"),
            "proxy_closed": fa, "proxy_forming": fb,
            "rule_d1_under_forming": "pass" if fb["net_r"] > 0 else "fail",
            "STANDING_AT_RISK": bool(passes and fb["net_r"] <= 0)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--klines-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--leg", action="append", default=None)
    args = ap.parse_args(argv)
    import yaml
    cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text())
    cfg = cfg.get("strategies", cfg)
    pop = [(lg, f) for lg, f in POPULATION if not args.leg or lg in args.leg]
    pop.sort(key=lambda lf: str(cfg[lf[0]]["symbols"][0]))  # one 1m load per symbol
    rm = real_money_legs()
    tmp = Path(args.out).with_suffix(".tmp.d")
    tmp.mkdir(parents=True, exist_ok=True)
    m1_cache: Dict[str, pd.DataFrame] = {}
    runs = []
    for leg, family in pop:
        block = cfg[leg]
        if not block.get("enabled"):
            raise SystemExit(f"{leg} is not enabled — population drifted; re-register")
        sym = str(block["symbols"][0])
        if sym not in m1_cache:
            if args.fetch:
                vs.fetch_1m(args.klines_dir, sym)
            m1_cache = {sym: vs.load_1m(args.klines_dir, sym)}  # one symbol resident
        r = leg_run(leg, family, block, m1_cache[sym], tmp, args.workers)
        r["real_money_bybit_2"] = leg in rm
        runs.append(r)
        print(f"{leg}: A n={r['A_closed']['n']} {r['A_closed']['net_r']:+.2f}R  "
              f"B n={r['B_forming']['n']} {r['B_forming']['net_r']:+.2f}R  J={r['jaccard']}",
              flush=True)
    pooled = pooled_stats(runs)
    out = {
        "unit": "RQ-20260929-302", "rule": "RULE-RQ0929-302-POOLED-FORMING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "Binance USD-M perp 1m klines (data.binance.vision); a "
                       "LABELLED PROXY for Bybit",
        "pooled": pooled, "grade": grade(pooled),
        "evidence_restatement": {r["leg"]: restate(r) for r in runs if r["real_money_bybit_2"]},
        "legs": {r["leg"]: {k: v for k, v in r.items() if not k.startswith("_")} for r in runs},
    }
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps({"grade": out["grade"], "pooled": pooled}, default=str))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
