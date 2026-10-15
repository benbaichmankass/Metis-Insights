#!/usr/bin/env python3
# wiring: research/queue/RQ-20261010-419b-02..04 via .github/workflows/research-script-run.yml
"""leg-replay-emit: queue-runnable, CONFIG-EXACT replay of live legs that KEEPS the per-trade rows.

WHY (PERF-FU-LEG-REPLAY, perf-followup 2026-W41). m20_fleet_exit_sweep keeps summaries only,
mi307_offline_mfe discards its rows, direction_walkforward needs a pre-made emit, nbook_portfolio needs a
staged roster. This is the one runner-side instrument that, for a named leg list, (1) fetches binance_vision
candles, (2) builds the leg's harness args with ``m20_fleet_exit_sweep.base_args`` (the same lever as the
TP-PREDICT-HEAD live-leg corpus -- never re-derived here), (3) runs backtest_trend.py / backtest_pullback.py with
``--emit-trades`` over the window, (4) runs ``direction_walkforward.analyze`` per leg (and pooled), and
(5) writes per-leg daily-R series for portfolio replay.

Outputs under ``--out``: ``emits/<leg>.jsonl``, ``daily_r/<leg>.json`` ({UTC date: net R}),
``replay.json`` (per-leg per-direction folds + pooled), and ``verdict.json`` (research-script-run.yml contract:
verdict in pass/fail/no_action_warranted/indeterminate/not_applicable, read_state in measured/no_data/
producer_failed/not_attempted, ``n`` an integer only when measured).

``--rule`` applies a pre-registered decision rule VERBATIM from the queue unit (thresholds are constants below,
copied from research/queue/RQ-20261010-419b-0{2,3,4}.yaml; editing a threshold here without the unit is a
different experiment):
  short-drag   RQ-20261010-419b-02  pass = CONFIRMED / MIXED, fail = NOT CONFIRMED, indeterminate = n_short < 39
  long-drag    PI-20261011-HOPS1447-0001 unit  short-drag with sides swapped; side-constrained legs are controls
  trend-asym   RQ-20261010-419b-03  per leg, short-only / long-only legs are controls and never graded
  portfolio    RQ-20261010-419b-04  PROPOSE / REDUCE-CONCENTRATION = pass, NO ACTION = no_action_warranted
  none         (default) replay + folds only; verdict is ``indeterminate`` with the raw results in measurement.
Net R is the harness's ``net_r`` (full cost stack the harness charges). Tier-1 research code; writes only --out.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

import direction_walkforward as dwf  # noqa: E402

FOLDS = 4
MIN_PER_DIRECTION = 39            # registered floor: d=0.45 at power 0.8 (units 02/03)
MIN_OOS_DAYS = 39                 # unit 04 INDETERMINATE floor
OOS_FRAC = 0.35                   # unit 04: chronologically last 35% of days
SPLIT_SHIFT = 0.10                # unit 04: improvement must hold at +/- 10 points
CORR_TRIGGER = 0.30
DD_SHARE_TRIGGER = 0.50
SYMBOL_VAR_TRIGGER = 0.50
INTERVAL_CODE = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60"}
WARMUP_DAYS = 45
DEFAULT_LEGS = ("xrp_pullback_2h,ada_pullback_2h,trend_donchian_eth_4h,trend_donchian_xrp_4h")


# ── config / harness args ───────────────────────────────────────────────────
def load_strategies(path: Optional[Path] = None) -> Dict[str, Any]:
    import yaml
    p = path or REPO / "config" / "strategies.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))["strategies"]


def side_constraint(cfg: dict) -> Optional[str]:
    """'short' / 'long' when the leg's config forbids the other side, else None."""
    sf = str(cfg.get("side_filter") or "").lower()
    if sf in ("short", "long"):
        return sf
    if cfg.get("long_only"):
        return "long"
    return None


def _finest_fetchable(tf: str) -> str:
    return "1h" if tf not in INTERVAL_CODE else tf


def fetch_candles(symbol: str, tf: str, data_dir: Path, start: str, end: str, *,
                  run: Callable = subprocess.run, timeout_s: int = 3600) -> "tuple[Optional[Path], str]":
    """binance_vision candles into data_dir/{SYMBOL}_{tf}.csv. Never raises: (None, reason) on failure."""
    code = INTERVAL_CODE.get(tf)
    if code is None:
        return None, f"no binance_vision interval code for {tf}"
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / f"{symbol}_{tf}.csv"
    if dest.exists() and dest.stat().st_size > 0:
        return dest, ""
    cmd = [sys.executable, str(REPO / "scripts/ops/fetch_backtest_candles.py"), "--symbol", symbol,
           "--source", "binance_vision", "--interval", code, "--start-date", start,
           "--end-date", end, "--output", str(dest)]
    try:
        p = run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return None, f"fetch of {symbol} {tf} timed out after {timeout_s}s"
    if p.returncode != 0 or not dest.exists():
        return None, f"fetch of {symbol} {tf} failed rc={p.returncode} {(p.stderr or p.stdout or '')[-200:]}"
    return dest, ""


def build_cmd(leg: str, cfg: dict, data_dir: Path, emit: Path, summary: Path, start: str, end: str,
              *, tp_cap_pct: Optional[float] = None) -> "tuple[Optional[List[str]], str]":
    """(argv, '') for one leg, or (None, reason). Args come from m20_fleet_exit_sweep.base_args."""
    import m20_fleet_exit_sweep as m20
    fam = m20.classify(leg)
    if fam not in ("donchian", "pullback"):
        return None, f"{leg}: family {fam!r} has no emit-capable harness here (donchian/pullback only)"
    harness = m20.FAMILY_HARNESS[fam]
    tf = str(cfg.get("timeframe") or "1h")
    sym = (cfg.get("symbols") or ["?"])[0]
    path, _proxy, resample = m20.resolve_data(sym, tf, data_dir)
    if path is None:
        return None, f"{leg}: no candle file for {sym} {tf} under {data_dir}"
    cap = m20.LIVE_TP_CAP_PCT if tp_cap_pct is None else tp_cap_pct
    args = m20.base_args(leg, cfg, fam, path, resample, cap)
    return ([sys.executable, str(REPO / harness)] + args +
            ["--start", start, "--end", end, "--strategy-name", leg,
             "--emit-trades", str(emit), "--json", str(summary)], "")


def run_leg(leg: str, cfg: dict, data_dir: Path, out: Path, start: str, end: str, *,
            run: Callable = subprocess.run, fetch: Callable = fetch_candles,
            timeout_s: int = 3600) -> Dict[str, Any]:
    tf = str(cfg.get("timeframe") or "1h")
    sym = (cfg.get("symbols") or ["?"])[0]
    emit = out / "emits" / f"{leg}.jsonl"
    emit.parent.mkdir(parents=True, exist_ok=True)
    got, why = fetch(sym, _finest_fetchable(tf), data_dir, start, end)
    if got is None:
        return {"leg": leg, "ok": False, "reason": why}
    cmd, why = build_cmd(leg, cfg, data_dir, emit, out / "emits" / f"{leg}.summary.json", start, end)
    if cmd is None:
        return {"leg": leg, "ok": False, "reason": why}
    try:
        p = run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return {"leg": leg, "ok": False, "reason": f"harness timed out after {timeout_s}s"}
    (out / "emits" / f"{leg}.log").write_text((p.stdout or "")[-20000:] + "\n" + (p.stderr or "")[-20000:])
    if p.returncode != 0 or not emit.exists():
        return {"leg": leg, "ok": False, "reason": f"harness rc={p.returncode} {(p.stderr or '')[-200:]}"}
    return {"leg": leg, "ok": True, "emit": str(emit), "cmd": [Path(cmd[1]).name] + cmd[2:]}


# ── per-leg analysis ────────────────────────────────────────────────────────
def daily_r(emit_path: str) -> Dict[str, float]:
    """UTC exit-date -> summed net R (R is realised when the trade closes)."""
    d: Dict[str, float] = defaultdict(float)
    for r in dwf._load([emit_path]):
        t = dwf._parse_ts(r.get("exit_time") or r.get("entry_time"))
        d[t.date().isoformat()] += float(r["net_r"])
    return {k: round(v, 6) for k, v in sorted(d.items())}


def leg_folds(emit_paths: List[str], label: str) -> Dict[str, Any]:
    """direction_walkforward.analyze plus the SHORT-side mirror counts the 419b rules need."""
    res = dwf.analyze(emit_paths, FOLDS, label)
    if res.get("error"):
        return {"label": label, "error": res["error"], "n_long": 0, "n_short": 0}
    bf = res["by_fold"]
    p = res["pooled"]
    res["n_long"], res["n_short"] = p["long_n"], p["short_n"]
    res["pooled_long_net_r"], res["pooled_short_net_r"] = p["long_r"], p["short_r"]
    res["folds_negative_short"] = sum(1 for s in bf if s["short_n"] and s["short_r"] < 0)
    res["folds_positive_short"] = sum(1 for s in bf if s["short_n"] and s["short_r"] > 0)
    res["folds_negative_long"] = sum(1 for s in bf if s["long_n"] and s["long_r"] < 0)
    return res


# ── rule: RQ-20261010-419b-02 ───────────────────────────────────────────────
def _short_confirmed(f: Dict[str, Any]) -> bool:
    return (f["n_short"] >= MIN_PER_DIRECTION and f["folds_negative_short"] >= 3
            and f["pooled_short_net_r"] < 0)


def _short_not_confirmed(f: Dict[str, Any]) -> bool:
    return f["n_short"] >= MIN_PER_DIRECTION and (f["folds_negative_short"] <= 2
                                                  or f["pooled_short_net_r"] >= 0)


def grade_short_drag(per_leg: Dict[str, Dict[str, Any]], pooled: Dict[str, Any]) -> Dict[str, Any]:
    legs = list(per_leg)
    conf = [lg for lg in legs if _short_confirmed(per_leg[lg])]
    pool_conf = _short_confirmed(pooled) and all(per_leg[lg]["pooled_short_net_r"] <= 0 for lg in legs)
    if len(conf) == len(legs) or pool_conf:
        outcome = "CONFIRMED"
    elif conf:
        outcome = "MIXED"
    elif all(_short_not_confirmed(per_leg[lg]) for lg in legs) and _short_not_confirmed(pooled):
        outcome = "NOT_CONFIRMED"
    elif any(per_leg[lg]["n_short"] < MIN_PER_DIRECTION for lg in legs) or pooled["n_short"] < MIN_PER_DIRECTION:
        outcome = "INDETERMINATE"
    else:
        outcome = "MIXED"        # legs disagree, none confirmed alone: no per-leg proposal
    verdict = {"CONFIRMED": "pass", "MIXED": "pass" if conf else "fail",
               "NOT_CONFIRMED": "fail", "INDETERMINATE": "indeterminate"}[outcome]
    return {"outcome": outcome, "verdict": verdict, "confirmed_legs": conf, "pooled_confirmed": pool_conf}


# ── rule: long-drag (perf-followup 2026-W42, PI-20261011-HOPS1447-0001) ────
def _long_confirmed(f: Dict[str, Any]) -> bool:
    return (f["n_long"] >= MIN_PER_DIRECTION and f["folds_negative_long"] >= 3
            and f["pooled_long_net_r"] < 0)


def _long_not_confirmed(f: Dict[str, Any]) -> bool:
    return f["n_long"] >= MIN_PER_DIRECTION and (f["folds_negative_long"] <= 2
                                                 or f["pooled_long_net_r"] >= 0)


def grade_long_drag(per_leg: Dict[str, Dict[str, Any]], pooled: Dict[str, Any],
                    constraints: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """The short-drag rule with the sides swapped. A leg whose config already forbids one side is a
    control (it emits no longs, or no shorts) and is never graded; the pool is graded only when no
    control is in it, because a control's trades would dilute the pooled long statistic."""
    constraints = constraints or {}
    controls = {lg: f"control: config side constraint {constraints[lg]!r} -- never graded"
                for lg in per_leg if constraints.get(lg)}
    legs = [lg for lg in per_leg if lg not in controls]
    if not legs:
        return {"outcome": "INDETERMINATE", "verdict": "indeterminate", "confirmed_legs": [],
                "pooled_confirmed": False, "controls": controls}
    conf = [lg for lg in legs if _long_confirmed(per_leg[lg])]
    pool_ok = not controls
    pool_conf = pool_ok and _long_confirmed(pooled) and all(per_leg[lg]["pooled_long_net_r"] <= 0 for lg in legs)
    if len(conf) == len(legs) or pool_conf:
        outcome = "CONFIRMED"
    elif conf:
        outcome = "MIXED"
    elif all(_long_not_confirmed(per_leg[lg]) for lg in legs) and (not pool_ok or _long_not_confirmed(pooled)):
        outcome = "NOT_CONFIRMED"
    elif any(per_leg[lg]["n_long"] < MIN_PER_DIRECTION for lg in legs) or (pool_ok and pooled["n_long"] < MIN_PER_DIRECTION):
        outcome = "INDETERMINATE"
    else:
        outcome = "MIXED"
    verdict = {"CONFIRMED": "pass", "MIXED": "pass" if conf else "fail",
               "NOT_CONFIRMED": "fail", "INDETERMINATE": "indeterminate"}[outcome]
    return {"outcome": outcome, "verdict": verdict, "confirmed_legs": conf, "pooled_confirmed": pool_conf,
            "controls": controls}


# ── rule: RQ-20261010-419b-03 ───────────────────────────────────────────────
def grade_trend_asym(per_leg: Dict[str, Dict[str, Any]], constraints: Dict[str, Optional[str]]) -> Dict[str, Any]:
    graded, controls = {}, {}
    for leg, f in per_leg.items():
        if constraints.get(leg):
            controls[leg] = f"control: config side constraint {constraints[leg]!r} -- never graded"
            continue
        if f["n_short"] < MIN_PER_DIRECTION:
            graded[leg] = "INDETERMINATE"
        elif _short_confirmed(f):
            graded[leg] = "CONFIRMED"
        else:
            graded[leg] = "NOT_CONFIRMED"
        if f["n_short"] and f.get("folds_positive_short", 0) >= 3 and f["pooled_short_net_r"] > 0:
            graded[leg] += "+COUNTER_EXAMPLE_STABLE_POSITIVE_SHORT"
    vals = list(graded.values())
    if any(v.startswith("CONFIRMED") for v in vals):
        verdict = "pass"
    elif vals and all(v.startswith("NOT_CONFIRMED") for v in vals):
        verdict = "fail"
    else:
        verdict = "indeterminate"
    return {"verdict": verdict, "per_leg": graded, "controls": controls}


# ── rule: RQ-20261010-419b-04 (stdlib only) ─────────────────────────────────
def _calendar(series: Dict[str, Dict[str, float]]) -> List[str]:
    days = sorted({d for s in series.values() for d in s})
    if not days:
        return []
    a = datetime.fromisoformat(days[0]).date()
    b = datetime.fromisoformat(days[-1]).date()
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def _vec(series: Dict[str, float], cal: List[str]) -> List[float]:
    return [series.get(d, 0.0) for d in cal]


def _corr(x: List[float], y: List[float]) -> Optional[float]:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def _max_dd(v: List[float]) -> "tuple[float, int, int]":
    """(depth >= 0, peak_idx, trough_idx) of the cumulative-R curve (starting from 0)."""
    cum = 0.0
    peak_i, pk, tr = -1, (-1), -1
    peak = 0.0
    depth = 0.0
    for i, x in enumerate(v):
        cum += x
        if cum > peak:
            peak, peak_i = cum, i
        if peak - cum > depth:
            depth, pk, tr = peak - cum, peak_i, i
    return round(depth, 6), pk, tr


def _sum(vs: List[List[float]], lo: int = 0, hi: Optional[int] = None) -> List[float]:
    n = len(vs[0]) if vs else 0
    hi = n if hi is None else hi
    return [sum(v[i] for v in vs) for i in range(lo, hi)]


def _loo(vecs: Dict[str, List[float]], frac: float) -> Dict[str, Any]:
    n = len(next(iter(vecs.values())))
    lo = n - int(round(n * frac))
    full = _sum(list(vecs.values()), lo)
    full_dd = _max_dd(full)[0]
    full_r = sum(full)
    out = {}
    for leg in vecs:
        rest = _sum([v for k, v in vecs.items() if k != leg], lo)
        out[leg] = {"oos_days": n - lo, "full_net_r": round(full_r, 4), "full_max_dd": full_dd,
                    "loo_net_r": round(sum(rest), 4), "loo_max_dd": _max_dd(rest)[0],
                    "improves": sum(rest) > full_r and _max_dd(rest)[0] < full_dd}
    return out


def grade_portfolio(series: Dict[str, Dict[str, float]], symbols: Dict[str, str]) -> Dict[str, Any]:
    cal = _calendar(series)
    legs = list(series)
    if len(legs) < 2 or not cal:
        return {"verdict": "indeterminate", "outcome": "INDETERMINATE", "why": "fewer than 2 legs / no days"}
    vecs = {lg: _vec(series[lg], cal) for lg in legs}
    n = len(cal)
    oos_days = int(round(n * OOS_FRAC))
    total = _sum(list(vecs.values()))
    dd, pk, tr = _max_dd(total)
    corr, shared = {}, {}
    for a in legs:
        corr[a], shared[a] = {}, {}
        for b in legs:
            corr[a][b] = _corr(vecs[a], vecs[b])
            shared[a][b] = sum(1 for x, y in zip(vecs[a], vecs[b]) if x != 0 and y != 0)
    # co-drawdown overlap: share of A's losing days that are also B's losing days
    codd = {a: {b: (sum(1 for x, y in zip(vecs[a], vecs[b]) if x < 0 and y < 0) /
                    max(1, sum(1 for x in vecs[a] if x < 0))) for b in legs} for a in legs}
    net = {lg: round(sum(vecs[lg]), 4) for lg in legs}
    dd_share = {lg: (round(-sum(vecs[lg][pk + 1:tr + 1]) / dd, 4) if dd > 0 and pk >= -1 else None) for lg in legs}
    sym_net: Dict[str, List[float]] = {}
    for lg in legs:
        s = symbols[lg]
        sym_net[s] = [a + b for a, b in zip(sym_net[s], vecs[lg])] if s in sym_net else list(vecs[lg])
    var_t = sum((x - sum(total) / n) ** 2 for x in total)
    sym_var_share = {}
    for s, v in sym_net.items():
        mv, mt = sum(v) / n, sum(total) / n
        sym_var_share[s] = round(sum((a - mv) * (b - mt) for a, b in zip(v, total)) / var_t, 4) if var_t else None
    base = _loo(vecs, OOS_FRAC)
    alt = [_loo(vecs, OOS_FRAC + SPLIT_SHIFT), _loo(vecs, max(0.05, OOS_FRAC - SPLIT_SHIFT))]
    rest_corr = {}
    for lg in legs:
        rest = _sum([v for k, v in vecs.items() if k != lg])
        rest_corr[lg] = _corr(vecs[lg], rest)
    nz_shared = {lg: min(shared[lg][k] for k in legs if k != lg) for lg in legs}
    proposals = []
    for lg in legs:
        corr_ok = rest_corr[lg] is not None and rest_corr[lg] >= CORR_TRIGGER and nz_shared[lg] >= MIN_OOS_DAYS
        dd_ok = dd_share[lg] is not None and dd_share[lg] > DD_SHARE_TRIGGER
        robust = base[lg]["improves"] and all(a[lg]["improves"] for a in alt)
        if robust and (corr_ok or dd_ok):
            proposals.append(lg)
    reduce_syms = [s for s, v in sym_var_share.items() if v is not None and v > SYMBOL_VAR_TRIGGER]
    if oos_days < MIN_OOS_DAYS:
        outcome, verdict = "INDETERMINATE", "indeterminate"
    elif proposals:
        outcome, verdict = "PROPOSE", "pass"
    elif reduce_syms:
        outcome, verdict = "REDUCE_CONCENTRATION", "pass"
    else:
        outcome, verdict = "NO_ACTION", "no_action_warranted"
    return {"verdict": verdict, "outcome": outcome, "propose_remove": proposals,
            "reduce_concentration_symbols": reduce_syms, "days": n, "oos_days": oos_days,
            "net_r": net, "max_dd_r": dd, "dd_share": dd_share, "symbol_var_share": sym_var_share,
            "corr": {a: {b: (None if c is None else round(c, 4)) for b, c in r.items()} for a, r in corr.items()},
            "nonzero_shared_days": shared, "co_drawdown_overlap": {a: {b: round(x, 4) for b, x in r.items()}
                                                                    for a, r in codd.items()},
            "corr_to_rest": {lg: (None if c is None else round(c, 4)) for lg, c in rest_corr.items()},
            "loo_oos": base, "loo_oos_split_plus10": alt[0], "loo_oos_split_minus10": alt[1]}


# ── driver ──────────────────────────────────────────────────────────────────
def write_verdict(out: Path, v: Dict[str, Any]) -> None:
    (out / "verdict.json").write_text(json.dumps(v, indent=1) + "\n", encoding="utf-8")


def assemble(legs: List[str], emits: Dict[str, str], strategies: Dict[str, Any], rule: str,
             out: Path, window: str) -> Dict[str, Any]:
    per_leg = {lg: leg_folds([emits[lg]], lg) for lg in legs if lg in emits}
    per_leg = {lg: f for lg, f in per_leg.items() if not f.get("error")}
    series = {lg: daily_r(emits[lg]) for lg in per_leg}
    (out / "daily_r").mkdir(parents=True, exist_ok=True)
    for lg, s in series.items():
        (out / "daily_r" / f"{lg}.json").write_text(json.dumps(s, indent=0) + "\n")
    pooled = leg_folds([emits[lg] for lg in per_leg], "pooled:" + "+".join(per_leg)) if per_leg else {}
    n = sum(f["total_trades"] for f in per_leg.values())
    meas: Dict[str, Any] = {"window": window, "folds": FOLDS, "per_leg": per_leg, "pooled": pooled,
                            "side_constraints": {lg: side_constraint(strategies[lg]) for lg in per_leg}}
    note = "leg-replay-emit: config-exact harness replay (base_args), net_r per trade"
    if not per_leg:
        return dict(verdict="not_applicable", read_state="no_data", n=None,
                    population=f"{window}: no leg produced a trade", note=note)
    if rule == "short-drag":
        g = grade_short_drag(per_leg, pooled)
    elif rule == "long-drag":
        g = grade_long_drag(per_leg, pooled, meas["side_constraints"])
    elif rule == "trend-asym":
        g = grade_trend_asym(per_leg, meas["side_constraints"])
    elif rule == "portfolio":
        syms = {lg: str((strategies[lg].get("symbols") or ["?"])[0]) for lg in per_leg}
        g = grade_portfolio(series, syms)
    else:
        g = {"verdict": "indeterminate", "outcome": "UNGRADED (no --rule)"}
    meas["rule"] = rule
    meas["grade"] = g
    (out / "replay.json").write_text(json.dumps(meas, indent=1, default=str) + "\n")
    return dict(verdict=g["verdict"], read_state="measured", n=n,
                population=f"{'+'.join(per_leg)} {window}, config-exact harness replay, {n} trades, rule={rule}: "
                           f"{g.get('outcome')}",
                note=note + f"; outcome={g.get('outcome')}", measurement=meas)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--legs", default=DEFAULT_LEGS, help="comma-separated leg ids from config/strategies.yaml")
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=1830)
    ap.add_argument("--end", default=None, help="window end YYYY-MM-DD (default: yesterday UTC)")
    ap.add_argument("--rule", choices=("none", "short-drag", "long-drag", "trend-asym", "portfolio"), default="none")
    ap.add_argument("--data-dir", default=None, help="candle dir (default: <out>/data)")
    ap.add_argument("--from-emits", default=None, help="SMOKE/TEST: dir of <leg>.jsonl emits; skips fetch+harness")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    legs = [s.strip() for s in a.legs.split(",") if s.strip()]
    try:
        strategies = load_strategies()
    except (OSError, KeyError, ValueError, UnicodeDecodeError, __import__("yaml").YAMLError) as exc:  # reported as producer_failed
        write_verdict(out, dict(verdict="not_applicable", read_state="producer_failed", n=None,
                                population=f"cannot read config/strategies.yaml: {exc}"))
        return 0
    missing = [lg for lg in legs if lg not in strategies]
    if missing:
        write_verdict(out, dict(verdict="not_applicable", read_state="producer_failed", n=None,
                                population=f"legs not in config/strategies.yaml: {missing}"))
        return 0
    end_d = (datetime.fromisoformat(a.end).date() if a.end
             else datetime.now(timezone.utc).date() - timedelta(days=1))
    start_d = end_d - timedelta(days=a.days)
    window = f"{start_d.isoformat()}..{end_d.isoformat()} ({a.days} d)"
    emits: Dict[str, str] = {}
    failed: Dict[str, str] = {}
    if a.from_emits:
        for lg in legs:
            p = Path(a.from_emits) / f"{lg}.jsonl"
            (emits.__setitem__(lg, str(p)) if p.exists() else failed.__setitem__(lg, "no emit file"))
    else:
        data_dir = Path(a.data_dir) if a.data_dir else out / "data"
        fetch_start = (start_d - timedelta(days=WARMUP_DAYS)).isoformat()
        for lg in legs:
            r = run_leg(lg, strategies[lg], data_dir, out, start_d.isoformat(), end_d.isoformat(),
                        fetch=lambda s, t, d, _s, _e: fetch_candles(s, t, d, fetch_start, end_d.isoformat()))
            (emits.__setitem__(lg, r["emit"]) if r["ok"] else failed.__setitem__(lg, r["reason"]))
    if failed:
        # A leg that did not replay makes every cross-leg statistic a statement about a different roster.
        write_verdict(out, dict(verdict="not_applicable", read_state="producer_failed", n=None,
                                population=f"leg replay failed, NEVER MEASURED: {failed}"))
        return 0
    v = assemble(legs, emits, strategies, a.rule, out, window)
    write_verdict(out, v)
    print(json.dumps({k: v[k] for k in ("verdict", "read_state", "n", "population")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
