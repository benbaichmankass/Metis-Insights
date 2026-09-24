#!/usr/bin/env python3
# wiring: manual-only — a research lane runs it by hand to (re)produce
# docs/research/prop-exit-evidence-2026-09-24.{md,json}; nothing schedules it.
"""E65: MFE distribution + bracket-geometry comparison for breakout_1's legs.

Implements the geometries and the decision rule PRE-REGISTERED in §0 of
docs/research/prop-exit-evidence-2026-09-24.md (commit 6d14310). Change the
rule there, in a new commit, BEFORE changing it here.

Every run goes through scripts/backtest_trend.py (the same harness and argv
builder the evidence records use, `regime_debt_matrix.build_harness_cmd`).
This file only (a) swaps the exit flags per geometry, (b) walks the candle
path post hoc for bars-to-MFE / bars-to-level, which the harness does not
emit, and (c) scores the prop ruleset. Step (b) re-derives each trade's MFE
and asserts it equals the harness's own `mfe_r` (the positive control).

    python3 scripts/research/prop_exit_evidence.py --workdir <dir> --out <json>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))
import regime_debt_matrix as rdm  # noqa: E402
import importlib.util as _ilu  # noqa: E402

# Load the CANONICAL harness by path: a bare `import backtest_trend` resolves to
# the retired scripts/research/ fork, which refuses attribute access.
_spec = _ilu.spec_from_file_location("_bt_trend", REPO / "scripts" / "backtest_trend.py")
_bt = _ilu.module_from_spec(_spec)
sys.modules["_bt_trend"] = _bt  # dataclasses resolve the module by name
_spec.loader.exec_module(_bt)
_load_candles, _resample = _bt._load_candles, _bt._resample

LEGS = ["trend_donchian_eth_prop", "trend_donchian_sol_prop"]
DAYS = 365
TIMEOUT = 720
LEVELS = [1.0, 1.5, 2.0, 3.0]
RISK_USD = 75.0          # E59 MEASURED: 1.5% x nominal $5,000 per ticket
DAILY_LOSS_USD = 150.0   # breakout.yaml: 3% of $5,000
CUSHIONS = [94.76, 300.0]
NS = [10, 25, 50]
PATHS = 20000
SEED = 65
MIN_OOS_TRADES = 25
SLIPPAGE_BPS = "5.0"

# Exit flags removed from the T0 argv before a bracket geometry is applied.
_EXIT_FLAGS = {"--trail-mult", "--stale-exit-bars", "--stale-exit-below-r",
               "--trail-decay-arm-r", "--trail-decay-stall-bars",
               "--trail-decay-tight-mult", "--tp-cap-pct", "--tp-r",
               "--bank-frac", "--bank-at-r", "--giveback-min-mfe-r",
               "--giveback-r", "--timeout-bars", "--emit-trades", "--json",
               "--slippage-bps-roundtrip"}

BRACKET = ["--trail-mult", "1000", "--timeout-bars", str(TIMEOUT)]
GEOMETRIES: Dict[str, Optional[List[str]]] = {
    "T0": None,  # the record's argv, unchanged
    "M": BRACKET,
    "B0": BRACKET + ["--tp-cap-pct", "0.099", "--tp-r", "6.0"],
    "B1": BRACKET + ["--tp-cap-pct", "0.099", "--tp-r", "2.0"],
    "B2": BRACKET + ["--tp-cap-pct", "0.099", "--tp-r", "3.0"],
    "B3": BRACKET + ["--tp-cap-pct", "0.099", "--tp-r", "3.0",
                     "--bank-frac", "0.5", "--bank-at-r", "1.5"],
}


def _strip_pairs(argv: List[str], flags: set) -> List[str]:
    """Drop each `flag value` pair whose flag is in `flags`."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in flags:
            skip = True
            continue
        out.append(a)
    return out


def _strip(argv: List[str]) -> List[str]:
    return _strip_pairs(argv, _EXIT_FLAGS)


def _pct(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def _walk(df: pd.DataFrame, idx: Dict[pd.Timestamp, int], t: Dict[str, Any]) -> Dict[str, Any]:
    """Re-walk one trade's bars, mirroring the harness's MFE semantics.

    The harness updates the favourable extreme only on bars that did NOT exit
    on the stop or TP (SL-first; the TP bar breaks before the update), and on
    close-based exits (stale/giveback/timeout) it DOES update on the exit bar.
    """
    ei = idx[pd.Timestamp(t["entry_time"])]
    xi = idx[pd.Timestamp(t["exit_time"])]
    entry, sl = float(t["entry"]), float(t["sl"])
    risk = abs(entry - sl)
    long = t["direction"] == "long"
    last = xi if t["exit_reason"] in ("stale_stop", "giveback_stop", "timeout") else xi - 1
    ext, mfe, peak_bar = entry, 0.0, 0
    first: Dict[float, Optional[int]] = {lv: None for lv in LEVELS}
    for j in range(ei + 1, last + 1):
        h, lo = float(df["high"].iloc[j]), float(df["low"].iloc[j])
        ext = max(ext, h) if long else min(ext, lo)
        m = (ext - entry) / risk if long else (entry - ext) / risk
        if m > mfe:
            mfe, peak_bar = m, j - ei
        for lv in LEVELS:
            if first[lv] is None and m >= lv:
                first[lv] = j - ei
    return {"mfe_recomputed": round(mfe, 3), "bars_to_mfe": peak_bar,
            "bars_held": xi - ei, "bars_to_level": first, "risk": risk}


def _run(argv: List[str]) -> None:
    r = subprocess.run(argv, cwd=REPO, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"harness rc={r.returncode}: {r.stderr.decode()[-400:]}")


def _folds(trades: List[Dict[str, Any]], start: pd.Timestamp, end: pd.Timestamp) -> List[Dict[str, Any]]:
    span = (end - start) / 4
    out = []
    for k in range(4):
        a, b = start + span * k, start + span * (k + 1)
        sel = [t for t in trades
               if a <= pd.Timestamp(t["entry_time"]) < b or (k == 3 and pd.Timestamp(t["entry_time"]) == b)]
        out.append({"fold": k + 1, "start": str(a), "end": str(b), "trades": len(sel),
                    "net_r": round(sum(t["net_r"] for t in sel), 4)})
    return out


def _prop_score(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    trades = sorted(trades, key=lambda t: pd.Timestamp(t["exit_time"]))
    usd = [t["net_r"] * RISK_USD for t in trades]
    days: Dict[str, float] = {}
    for t, u in zip(trades, usd):
        d = (pd.Timestamp(t["exit_time"]) - timedelta(minutes=30)).date().isoformat()
        days[d] = days.get(d, 0.0) + u
    worst_day = min(days.values()) if days else None
    # Historical path drawdown in $.
    eq = peak = dd = 0.0
    for u in usd:
        eq += u
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    rng = random.Random(SEED)
    breach: Dict[str, Dict[str, float]] = {}
    maxn = max(NS)
    sims = [[rng.choice(usd) for _ in range(maxn)] for _ in range(PATHS)] if usd else []
    for c in CUSHIONS:
        breach[f"{c:.2f}"] = {}
        for n in NS:
            hits = 0
            for p in sims:
                cum = 0.0
                for u in p[:n]:
                    cum += u
                    if cum <= -c:
                        hits += 1
                        break
            breach[f"{c:.2f}"][str(n)] = round(hits / PATHS, 4) if sims else None
    return {"n": len(usd), "risk_usd": RISK_USD,
            "net_usd": round(sum(usd), 2),
            "days_loss_ge_daily_limit": sum(1 for v in days.values() if v <= -DAILY_LOSS_USD),
            "trading_days": len(days),
            "worst_day_usd": round(worst_day, 2) if worst_day is not None else None,
            "hist_max_drawdown_usd": round(dd, 2),
            "p_breach": breach}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trades-dir", default=None,
                    help="also copy each leg x geometry trades file here")
    a = ap.parse_args(argv)
    wd = Path(a.workdir)
    wd.mkdir(parents=True, exist_ok=True)
    strat = yaml.safe_load(open(REPO / "config/strategies.yaml"))["strategies"]
    result: Dict[str, Any] = {"legs": {}, "account": {}}
    by_geo: Dict[str, List[Dict[str, Any]]] = {g: [] for g in GEOMETRIES}
    for leg in LEGS:
        cfg = strat[leg]
        feed = rdm.resolve_feed(cfg["symbols"][0], cfg["timeframe"])
        csv = str(wd / f"{leg}__data.csv")
        if not os.path.exists(csv):
            rdm._fetch_csv(feed, DAYS, csv)
        df = _resample(_load_candles(csv), feed["resample"]).reset_index(drop=True)
        idx = {pd.Timestamp(ts): i for i, ts in enumerate(df["timestamp"])}
        start, end = pd.Timestamp(df["timestamp"].iloc[0]), pd.Timestamp(df["timestamp"].iloc[-1])
        base, faithful, omitted = rdm.build_harness_cmd(
            leg, cfg, "trend", csv, feed["resample"], "", "")
        legout: Dict[str, Any] = {"data_start": str(start), "data_end": str(end),
                                  "bars": len(df), "t0_fidelity": faithful,
                                  "t0_omitted_levers": omitted, "geometries": {}}
        for g, flags in GEOMETRIES.items():
            emit, jout = str(wd / f"{leg}__{g}__trades.jsonl"), str(wd / f"{leg}__{g}__bt.json")
            # T0 keeps the record's own exit flags; every other geometry has
            # them removed and its bracket flags appended.
            argv_g = (_strip_pairs(base, {"--emit-trades", "--json"}) if flags is None
                      else _strip(base) + flags)
            # Pinned, as §0.2 registered. E60 (#12855) moved the crypto-PERP
            # default to 3.0 bps from bybit_2 fills; breakout_1 is not Bybit,
            # so the venue-agnostic 5.0 is the defensible prop figure.
            argv_g = argv_g + ["--slippage-bps-roundtrip", SLIPPAGE_BPS,
                               "--emit-trades", emit, "--json", jout]
            _run(argv_g)
            bt = json.load(open(jout))
            trades = [json.loads(l) for l in open(emit) if l.strip()]
            mism = 0
            for t in trades:
                w = _walk(df, idx, t)
                if abs(w["mfe_recomputed"] - float(t["mfe_r"])) > 0.002:
                    mism += 1
                t.update(w)
                t["leg"] = leg
                tp_eff = (min(0.099 * float(t["entry"]), 6.0 * w["risk"]) / w["risk"]) if g in ("B0", "T0") else None
                t["tp_r_effective_b0"] = round(tp_eff, 3) if tp_eff else None
            if a.trades_dir:
                Path(a.trades_dir).mkdir(parents=True, exist_ok=True)
                with open(Path(a.trades_dir) / f"{leg}__{g}__trades.jsonl", "w") as fh:
                    for t in trades:
                        fh.write(json.dumps({k: t[k] for k in (
                            "entry_time", "exit_time", "direction", "entry", "sl",
                            "exit_reason", "net_r", "net_r_fee_only", "mfe_r",
                            "bars_to_mfe", "bars_held")}) + "\n")
            held = [t["bars_held"] for t in trades]
            gout: Dict[str, Any] = {
                "argv_exit_flags": flags, "n": len(trades),
                "mfe_positive_control_mismatches": mism,
                "net_r": bt.get("net_total_r"), "net_r_fee_only": bt.get("net_total_r_fee_only"),
                "max_drawdown_r": bt.get("max_drawdown_r"),
                "net_r_per_capital_day": bt.get("net_r_per_capital_day"),
                "capital_days": bt.get("capital_days"),
                "win_rate_pct": bt.get("win_rate_pct"), "by_outcome": bt.get("by_outcome"),
                "tp_r_effective_median": bt.get("tp_r_effective_median"),
                "bars_held_p50": _pct(held, 0.5), "bars_held_p90": _pct(held, 0.9),
                "bars_held_max": max(held) if held else None,
                "folds": _folds(trades, start, end),
            }
            gout["folds_positive"] = sum(1 for f in gout["folds"] if f["net_r"] > 0)
            if g in ("M", "T0"):
                mfe = [float(t["mfe_r"]) for t in trades]
                n = len(trades)

                def reached(t, lv):
                    return float(t["mfe_r"]) >= lv or (
                        t["exit_reason"] == "take_profit" and (t.get("tp_r_effective_b0") or 0) >= lv)
                gout["mfe"] = {
                    "p25": _pct(mfe, .25), "p50": _pct(mfe, .5), "p75": _pct(mfe, .75),
                    "p90": _pct(mfe, .9),
                    "frac_reaching": {str(lv): round(sum(reached(t, lv) for t in trades) / n, 4)
                                      for lv in LEVELS},
                    "bars_to_mfe_p50": _pct([t["bars_to_mfe"] for t in trades], .5),
                    "bars_to_mfe_p90": _pct([t["bars_to_mfe"] for t in trades], .9),
                    "bars_to_level_p50": {str(lv): _pct([t["bars_to_level"][lv] for t in trades
                                                         if t["bars_to_level"][lv] is not None], .5)
                                          for lv in LEVELS},
                    "bars_to_level_p90": {str(lv): _pct([t["bars_to_level"][lv] for t in trades
                                                         if t["bars_to_level"][lv] is not None], .9)
                                          for lv in LEVELS},
                }
                if g == "M":
                    # B0's level per trade on M's trades (entries identical until exits differ)
                    tpe = [min(0.099 * float(t["entry"]), 6.0 * t["risk"]) / t["risk"] for t in trades]
                    gout["mfe"]["b0_tp_level_p50"] = _pct(tpe, .5)
                    gout["mfe"]["frac_reaching_b0_tp"] = round(
                        sum(float(t["mfe_r"]) >= e for t, e in zip(trades, tpe)) / n, 4)
            legout["geometries"][g] = gout
            by_geo[g].extend(trades)
        result["legs"][leg] = legout
    for g in GEOMETRIES:
        if g in ("M",):
            continue
        result["account"][g] = _prop_score(by_geo[g])
    result["decision"] = decide(result)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(a.out, "w"), indent=1, default=str)
    print(json.dumps(result["decision"], indent=1))
    return 0


def decide(res: Dict[str, Any]) -> Dict[str, Any]:
    """The §0.4 rule, verbatim in code."""
    out: Dict[str, Any] = {"candidates": {}}
    acct = res["account"]
    for k in ("B1", "B2", "B3"):
        checks: Dict[str, Any] = {}
        for leg, L in res["legs"].items():
            b0, bk = L["geometries"]["B0"], L["geometries"][k]
            r2 = (bk["net_r"] >= b0["net_r"]) or (
                b0["net_r"] > 0 and bk["net_r_per_capital_day"] > b0["net_r_per_capital_day"]
                and bk["net_r"] >= 0.75 * b0["net_r"])
            checks[leg] = {
                "R0": b0["n"] >= MIN_OOS_TRADES,
                "R1": bk["net_r"] > 0,
                "R2": bool(r2),
                "R3": bk["bars_held_p90"] < b0["bars_held_p90"],
                "R4": bk["folds_positive"] >= b0["folds_positive"],
            }
        r5 = (acct[k]["p_breach"]["94.76"]["25"] <= acct["B0"]["p_breach"]["94.76"]["25"]
              and acct[k]["days_loss_ge_daily_limit"] <= acct["B0"]["days_loss_ge_daily_limit"])
        eligible = r5 and all(all(v.values()) for v in checks.values())
        out["candidates"][k] = {"per_leg": checks, "R5": r5, "eligible": eligible}

    def acct_rpcd(g):
        num = sum(res["legs"][l]["geometries"][g]["net_r"] for l in res["legs"])
        den = sum(res["legs"][l]["geometries"][g]["capital_days"] for l in res["legs"])
        return num / den if den else float("-inf")
    elig = [k for k, v in out["candidates"].items() if v["eligible"]]
    out["account_net_r_per_capital_day"] = {g: round(acct_rpcd(g), 4) for g in ("B0", "B1", "B2", "B3")}
    out["recommended"] = max(elig, key=acct_rpcd) if elig else None
    out["b0_fails_R1_on"] = [l for l, L in res["legs"].items() if L["geometries"]["B0"]["net_r"] <= 0]
    return out


if __name__ == "__main__":
    sys.exit(main())
