#!/usr/bin/env python3
# wiring: research/queue/RQ-20261015-cab7-01..06 via .github/workflows/research-script-run.yml
"""tp-prediction-derive: derive a leg's PREDICTIVE take-profit from its own trades, grade it OOS net of cost.

WHY (BRACKETS-AS-PREDICTIONS, operator 2026-10-14/15; TP doctrine, docs/ARCHITECTURE-CANONICAL.md § "TP doctrine").
Every leg's bracket must carry a REAL prediction of where the move runs out. The e35 sweep answers "does a
FIXED R-multiple cell beat the sentinel at the IS/OOS gate", which is a P&L question over a pre-chosen grid.
This instrument asks the doctrine's question directly, from the data:

  1. replay the leg CONFIG-EXACT with NO take-profit and NO venue cap (``base_args(..., tp_cap_pct=0.0)``, harness
     default ``tp_r`` 50 = never reached), so ``mfe_r`` is how far each trade actually got under the leg's real
     entry, stop and trail -- the uncensored basis mi307 calls "the target-setting book";
  2. on the chronologically FIRST ``IS_FRAC`` of trades, compute the reach-rate curve P(mfe_r >= K) and the
     counterfactual net R of a TP at K for every K in ``GRID`` (venue cap applied per trade, exactly as live:
     ``K_eff = min(K, cap_r)``), and choose K* = the IS-best K whose IS reach is >= ``REACH_FLOOR`` and which rests
     BELOW the median per-trade venue cap (a target at the cap is the sentinel under another name);
  3. on the remaining OOS trades -- never seen by the choice -- grade K* against the leg's CURRENT bracket
     (declared ``tp_r``, capped), paired per trade, with a seeded bootstrap CI, and check the prediction is
     CALIBRATED (OOS reach within ``CAL_TOL`` of IS reach).

``--conditioned`` adds the regime-conditioned target: K*_c per cell of entry-vol tercile (the stop distance as a
fraction of entry, which IS atr_stop_mult x ATR% at entry, so no candle re-read is needed) x ADX-at-entry tercile
(when the emit carries ``adx_at_entry``), cut on IS only; a cell with < ``MIN_CELL_IS`` IS trades falls back to K*.
It is graded against the UNCONDITIONED K* on the same OOS trades: the conditioning must EARN its complexity.

THE COUNTERFACTUAL, AND ITS LIMITS (stated, not hidden)
------------------------------------------------------
A TP only ADDS an exit, so on one trade it is exact given the path: if ``mfe_r < K_eff`` the TP never fills and the
trade is unchanged (``net_r``); if ``mfe_r >= K_eff`` the trade exits at ``K_eff`` and its cost terms are kept from the
row (``net_r - gross_r``: fee + slippage + funding, the harness's full stack via ``execution_costs``). Three limits:
  * the harness's ``mfe_r`` is a LAGGED LOWER BOUND, so reach is UNDER-stated (conservative for a target);
  * funding is charged for the full uncapped hold, so a shorter TP'd trade is charged too much (conservative);
  * an earlier TP exit frees the position slot for an entry the harness skipped; those trades are NOT modelled.
So a PASS here is a screen, not a ship: its named follow-on is the harness-native confirmation of K* (an e35
``tp_r`` cell at the exit-refinement P2 gate + walk-forward) before any Tier-3 proposal.

Thresholds are constants below, copied from research/queue/RQ-20261015-cab7-0*.yaml's decision_rule; editing one
here without the unit is a different experiment. Tier-1 research code; writes only under --out.

Usage:
    tp_prediction_derive.py --legs ada_pullback_2h --out DIR [--conditioned] [--days 1830]
    tp_prediction_derive.py --legs a,b,c --pool --out DIR          # family-pooled (leg is a stratum in the IS cut)
    tp_prediction_derive.py --from-emits DIR --legs X --out DIR    # SMOKE: analyse existing uncapped emits
    tp_prediction_derive.py --self-test
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "research"))

from exit_capture import mfe_r_of  # noqa: E402  (the ONE MFE reader)
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402  (the ONE clamp)

# ── registered constants (RQ-20261015-cab7-0*) ───────────────────────────────
GRID = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]
REACH_FLOOR = 0.10          # TP-REACH-FLOOR: a target touched by < 10% of trades is not a prediction
IS_FRAC = 0.60              # chronological: first 60% of trades choose, last 40% grade
MIN_OOS = 39                # d = 0.45 at alpha 0.05 / power 0.80: (1.96 + 0.8416)^2 / 0.45^2 = 38.76
NONINF_MARGIN = 0.05        # R/trade: a predictive TP may cost at most this vs the current bracket
CAL_TOL = 0.10              # |reach_OOS - reach_IS| for the prediction to count as calibrated
MIN_CELL_IS = 30            # conditioned: IS trades a cell needs to choose its own K
MIN_OWN_CELLS = 2           # conditioned: cells that must carry their own K for the test to exist
BOOT = 2000
SEED = 20261015
WARMUP_DAYS = 60
INTERVAL_CODE = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1d": "D"}
CRYPTO_QUOTES = ("USDT", "USDC", "USD")


# ── per-trade rows ───────────────────────────────────────────────────────────
def parse_rows(raw: List[Dict[str, Any]], leg: str) -> Tuple[List[Dict[str, Any]], int]:
    """Readable rows (mfe, net, cost, cap_r, risk_pct, adx, t) sorted by entry time; returns (rows, unreadable)."""
    out: List[Dict[str, Any]] = []
    bad = 0
    for r in raw:
        m = mfe_r_of(r)
        try:
            entry, sl = float(r["entry"]), float(r["sl"])
            net, gross = float(r["net_r"]), float(r["gross_r"])
            t = str(r.get("entry_time") or "")
        except (KeyError, TypeError, ValueError):
            bad += 1
            continue
        risk = abs(entry - sl)
        if m is None or risk <= 0 or entry <= 0 or not t:
            bad += 1
            continue
        adx = r.get("adx_at_entry")
        try:
            adx = float(adx) if adx is not None else None
        except (TypeError, ValueError):
            adx = None
        out.append({"leg": leg, "t": t, "mfe": float(m), "net": net, "cost": net - gross,
                    "cap_r": TP_VENUE_CAP_PCT * entry / risk, "risk_pct": risk / entry, "adx": adx})
    out.sort(key=lambda x: x["t"])
    return out, bad


def k_eff(row: Dict[str, Any], k: float, capped: bool = True) -> float:
    return min(k, row["cap_r"]) if capped else k


def cf_net(row: Dict[str, Any], k: float) -> float:
    """Counterfactual net R of a TP at k (venue-capped): exact given the path, see module doc."""
    ke = k_eff(row, k)
    return ke + row["cost"] if row["mfe"] >= ke else row["net"]


def reach(rows: List[Dict[str, Any]], k: float) -> Optional[float]:
    if not rows:
        return None
    return sum(1 for r in rows if r["mfe"] >= k_eff(r, k)) / len(rows)


def mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def quantile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def boot_ci(diffs: List[float], seed: int = SEED) -> Tuple[Optional[float], Optional[float]]:
    """Seeded percentile bootstrap 95% CI of the mean of paired per-trade differences."""
    if len(diffs) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(diffs)
    ms = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT))
    return ms[int(0.025 * BOOT)], ms[int(0.975 * BOOT) - 1]


def split(rows: List[Dict[str, Any]], pooled: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Chronological IS/OOS cut; pooled = cut each leg at its own IS_FRAC so every leg is in both halves."""
    if not pooled:
        c = int(len(rows) * IS_FRAC)
        return rows[:c], rows[c:]
    by: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["leg"], []).append(r)
    is_, oos = [], []
    for lg_rows in by.values():
        c = int(len(lg_rows) * IS_FRAC)
        is_ += lg_rows[:c]
        oos += lg_rows[c:]
    return is_, oos


def choose_k(is_rows: List[Dict[str, Any]]) -> Tuple[Optional[float], Dict[str, Any]]:
    """K* = IS-best mean counterfactual net R among K with IS reach >= floor and K <= median IS cap_r."""
    cap_med = quantile([r["cap_r"] for r in is_rows], 0.5)
    curve = []
    best_k, best_v = None, None
    for k in GRID:
        rr = reach(is_rows, k)
        v = mean([cf_net(r, k) for r in is_rows])
        eligible = rr is not None and rr >= REACH_FLOOR and cap_med is not None and k <= cap_med
        curve.append({"k": k, "reach_is": rr, "mean_net_is": v, "eligible": eligible})
        if eligible and v is not None and (best_v is None or v > best_v):
            best_k, best_v = k, v
    return best_k, {"cap_r_median_is": cap_med, "curve": curve}


# ── the unconditioned rule ───────────────────────────────────────────────────
def grade_unconditioned(rows: List[Dict[str, Any]], base_k: float, pooled: bool) -> Dict[str, Any]:
    is_, oos = split(rows, pooled)
    mfes = [r["mfe"] for r in rows]
    desc = {"n": len(rows), "n_is": len(is_), "n_oos": len(oos), "base_tp_r": base_k,
            "mfe_r_quantiles": {f"p{int(q * 100)}": quantile(mfes, q) for q in (0.5, 0.6, 0.7, 0.8, 0.9)},
            "reach_all_uncapped": {str(k): (sum(1 for m in mfes if m >= k) / len(mfes) if mfes else None)
                                   for k in GRID}}
    k_star, sel = choose_k(is_)
    desc.update(selection=sel, k_star=k_star)
    if len(oos) < MIN_OOS:
        return {**desc, "outcome": "INDETERMINATE", "verdict": "indeterminate",
                "why": f"n_oos {len(oos)} < {MIN_OOS}"}
    if k_star is None:
        return {**desc, "outcome": "INDETERMINATE", "verdict": "indeterminate",
                "why": "no K in the grid is reachable >= floor AND below the median venue cap on IS"}
    diffs = [cf_net(r, k_star) - cf_net(r, base_k) for r in oos]
    lo, hi = boot_ci(diffs)
    r_is, r_oos = reach(is_, k_star), reach(oos, k_star)
    gap = abs(r_oos - r_is)
    desc.update(oos_mean_net_kstar=mean([cf_net(r, k_star) for r in oos]),
                oos_mean_net_base=mean([cf_net(r, base_k) for r in oos]),
                oos_delta_mean=mean(diffs), oos_delta_ci95=[lo, hi],
                reach_is_kstar=r_is, reach_oos_kstar=r_oos, calibration_gap=gap,
                reach_oos_base=reach(oos, base_k))
    if r_oos < REACH_FLOOR or gap > CAL_TOL or hi < -NONINF_MARGIN:
        why = ("OOS reach below floor" if r_oos < REACH_FLOOR else
               f"uncalibrated (gap {gap:.3f} > {CAL_TOL})" if gap > CAL_TOL else
               f"inferior: CI upper {hi:.3f} < -{NONINF_MARGIN}")
        return {**desc, "outcome": "FAIL", "verdict": "fail", "why": why}
    if lo >= -NONINF_MARGIN:
        tag = "PASS_IMPROVES" if lo > 0 else "PASS_NONINFERIOR"
        return {**desc, "outcome": tag, "verdict": "pass",
                "why": f"K*={k_star} reach IS {r_is:.3f} / OOS {r_oos:.3f}, delta CI [{lo:.3f}, {hi:.3f}]"}
    return {**desc, "outcome": "INDETERMINATE", "verdict": "indeterminate",
            "why": f"delta CI [{lo:.3f}, {hi:.3f}] straddles -{NONINF_MARGIN}"}


# ── the conditioned rule ─────────────────────────────────────────────────────
def _terciles(xs: List[float]) -> Tuple[float, float]:
    return quantile(xs, 1 / 3), quantile(xs, 2 / 3)


def _bucket(x: Optional[float], cuts: Optional[Tuple[float, float]]) -> int:
    if x is None or cuts is None:
        return 0
    return 0 if x <= cuts[0] else 1 if x <= cuts[1] else 2


def grade_conditioned(rows: List[Dict[str, Any]], pooled: bool) -> Dict[str, Any]:
    is_, oos = split(rows, pooled)
    k_star, _ = choose_k(is_)
    vol_cuts = _terciles([r["risk_pct"] for r in is_]) if is_ else None
    adx_vals = [r["adx"] for r in is_ if r["adx"] is not None]
    use_adx = bool(is_) and len(adx_vals) >= 0.9 * len(is_)
    adx_cuts = _terciles(adx_vals) if use_adx else None

    def cell(r: Dict[str, Any]) -> str:
        return f"v{_bucket(r['risk_pct'], vol_cuts)}a{_bucket(r['adx'], adx_cuts) if use_adx else 'x'}"

    cells: Dict[str, List[Dict[str, Any]]] = {}
    for r in is_:
        cells.setdefault(cell(r), []).append(r)
    ks: Dict[str, Optional[float]] = {}
    own = 0
    for c, cr in sorted(cells.items()):
        if len(cr) >= MIN_CELL_IS:
            kc, _ = choose_k(cr)
            ks[c] = kc if kc is not None else k_star
            own += kc is not None
        else:
            ks[c] = k_star
    desc: Dict[str, Any] = {"n": len(rows), "n_is": len(is_), "n_oos": len(oos), "k_star": k_star,
                            "conditioning": "entry-vol tercile" + (" x ADX tercile" if use_adx else " (no adx_at_entry)"),
                            "vol_cuts_is": vol_cuts, "adx_cuts_is": adx_cuts, "cell_k": ks,
                            "cells_with_own_k": own, "cell_n_is": {c: len(v) for c, v in cells.items()}}
    if len(oos) < MIN_OOS or k_star is None:
        return {**desc, "outcome": "INDETERMINATE", "verdict": "indeterminate",
                "why": f"n_oos {len(oos)} < {MIN_OOS}" if len(oos) < MIN_OOS else "no unconditioned K*"}
    if own < MIN_OWN_CELLS:
        return {**desc, "outcome": "INDETERMINATE", "verdict": "indeterminate",
                "why": f"only {own} cell(s) carry their own K (< {MIN_OWN_CELLS}): conditioning not estimable"}
    diffs, gaps, w = [], 0.0, 0
    oos_cells: Dict[str, List[Dict[str, Any]]] = {}
    for r in oos:
        c = cell(r)
        kc = ks.get(c, k_star) or k_star
        diffs.append(cf_net(r, kc) - cf_net(r, k_star))
        oos_cells.setdefault(c, []).append(r)
    for c, cr in oos_cells.items():
        kc = ks.get(c, k_star) or k_star
        is_c = cells.get(c) or is_
        gaps += abs((reach(cr, kc) or 0.0) - (reach(is_c, kc) or 0.0)) * len(cr)
        w += len(cr)
    lo, hi = boot_ci(diffs)
    cal = gaps / w if w else None
    desc.update(oos_delta_vs_unconditioned=mean(diffs), oos_delta_ci95=[lo, hi], weighted_calibration_gap=cal)
    if hi <= 0:
        return {**desc, "outcome": "FAIL", "verdict": "fail",
                "why": f"conditioning adds nothing: CI [{lo:.3f}, {hi:.3f}] <= 0 -> unconditioned K* stays"}
    if lo > 0 and cal is not None and cal <= CAL_TOL:
        return {**desc, "outcome": "PASS", "verdict": "pass",
                "why": f"conditioned beats K* by CI [{lo:.3f}, {hi:.3f}], calibration gap {cal:.3f}"}
    return {**desc, "outcome": "INDETERMINATE", "verdict": "indeterminate",
            "why": f"CI [{lo:.3f}, {hi:.3f}], calibration gap {cal}"}


# ── replay (fetch + config-exact UNCAPPED harness) ───────────────────────────
def _is_crypto(sym: str) -> bool:
    return sym.upper().endswith(CRYPTO_QUOTES) and len(sym) > 4


def fetch(symbol: str, tf: str, data_dir: Path, start: str, end: str, *,
          run: Callable = subprocess.run) -> Tuple[Optional[Path], str]:
    """Candles into data_dir/{SYMBOL}_{tf}.csv: binance_vision for crypto, dukascopy then yfinance otherwise."""
    code = INTERVAL_CODE.get(tf)
    if code is None:
        return None, f"no interval code for {tf}"
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / f"{symbol}_{tf}.csv"
    if dest.exists() and dest.stat().st_size > 0:
        return dest, ""
    sources = ["binance_vision"] if _is_crypto(symbol) else ["dukascopy", "yfinance"]
    why = []
    for src in sources:
        cmd = [sys.executable, str(REPO / "scripts/ops/fetch_backtest_candles.py"), "--symbol", symbol,
               "--source", src, "--interval", code, "--start-date", start, "--end-date", end,
               "--output", str(dest)]
        try:
            p = run(cmd, capture_output=True, text=True, timeout=3600, cwd=str(REPO))
        except subprocess.TimeoutExpired:
            why.append(f"{src}: timeout")
            continue
        if p.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return dest, ""
        why.append(f"{src}: rc={p.returncode} {(p.stderr or p.stdout or '')[-160:]}")
    return None, f"fetch {symbol} {tf} failed: {why}"


def replay_uncapped(leg: str, cfg: dict, data_dir: Path, out: Path, start: str, end: str, fetch_start: str, *,
                    run: Callable = subprocess.run) -> Dict[str, Any]:
    import m20_fleet_exit_sweep as m20
    fam = m20.classify(leg)
    if fam not in ("donchian", "pullback"):
        return {"ok": False, "reason": f"family {fam!r}: no emit-capable harness here (donchian/pullback only)"}
    tf = str(cfg.get("timeframe") or "1h")
    sym = str((cfg.get("symbols") or ["?"])[0])
    got, why = fetch(sym, tf, data_dir, fetch_start, end, run=run)
    if got is None:
        return {"ok": False, "reason": why}
    path, _proxy, resample = m20.resolve_data(sym, tf, data_dir)
    if path is None:
        return {"ok": False, "reason": f"no candle file for {sym} {tf} under {data_dir}"}
    emit = out / "emits" / f"{leg}.jsonl"
    emit.parent.mkdir(parents=True, exist_ok=True)
    # tp_cap_pct=0.0 -> no --tp-cap-pct and no --tp-r: the harness default 50R sentinel, uncapped = no TP exit.
    args = m20.base_args(leg, cfg, fam, path, resample, 0.0)
    cmd = ([sys.executable, str(REPO / m20.FAMILY_HARNESS[fam])] + args +
           ["--start", start, "--end", end, "--strategy-name", leg, "--emit-trades", str(emit)])
    try:
        p = run(cmd, capture_output=True, text=True, timeout=3600, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "harness timeout"}
    (out / "emits" / f"{leg}.log").write_text((p.stdout or "")[-20000:] + "\n" + (p.stderr or "")[-20000:])
    if p.returncode != 0 or not emit.exists():
        return {"ok": False, "reason": f"harness rc={p.returncode} {(p.stderr or '')[-200:]}"}
    return {"ok": True, "emit": str(emit), "cmd": [Path(cmd[1]).name] + cmd[2:]}


def _read_jsonl(p: Path) -> List[Dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def declared_tp_r(cfg: dict) -> float:
    for key in ("tp_r", "target_r", "tp_at_r"):
        v = cfg.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 50.0


# ── assembly ─────────────────────────────────────────────────────────────────
def assemble(legs: List[str], emits: Dict[str, Path], strategies: Dict[str, Any], *, conditioned: bool,
             pooled: bool, window: str) -> Dict[str, Any]:
    per_leg: Dict[str, Any] = {}
    all_rows: List[Dict[str, Any]] = []
    unreadable = {}
    for lg in legs:
        rows, bad = parse_rows(_read_jsonl(emits[lg]), lg)
        unreadable[lg] = bad
        all_rows += rows
        if not pooled:
            g = (grade_conditioned(rows, False) if conditioned
                 else grade_unconditioned(rows, declared_tp_r(strategies[lg]), False))
            per_leg[lg] = g
    rule = "conditioned" if conditioned else "unconditioned"
    if pooled:
        bases = {declared_tp_r(strategies[lg]) for lg in legs}
        if not conditioned and len(bases) != 1:
            return dict(verdict="not_applicable", read_state="producer_failed", n=None,
                        population=f"pooled legs declare different tp_r {sorted(bases)}; the paired base is undefined")
        g = (grade_conditioned(all_rows, True) if conditioned
             else grade_unconditioned(all_rows, bases.pop(), True))
        per_leg["pooled:" + "+".join(legs)] = g
    n = len(all_rows)
    if n == 0:
        return dict(verdict="not_applicable", read_state="no_data", n=None,
                    population=f"{window}: no readable trade on {legs}")
    verdicts = [g["verdict"] for g in per_leg.values()]
    verdict = ("pass" if "pass" in verdicts else "fail" if all(v == "fail" for v in verdicts)
               else "indeterminate")
    outcomes = {k: g["outcome"] for k, g in per_leg.items()}
    meas = {"window": window, "rule": rule, "pooled": pooled, "per_leg": per_leg,
            "unreadable_rows": unreadable,
            "constants": {"grid": GRID, "reach_floor": REACH_FLOOR, "is_frac": IS_FRAC, "min_oos": MIN_OOS,
                          "noninf_margin_r": NONINF_MARGIN, "cal_tol": CAL_TOL, "min_cell_is": MIN_CELL_IS,
                          "boot": BOOT, "seed": SEED, "venue_cap_pct": TP_VENUE_CAP_PCT}}
    return dict(verdict=verdict, read_state="measured", n=n,
                population=f"{'+'.join(legs)} {window}, config-exact UNCAPPED no-TP harness replay, {n} trades, "
                           f"rule={rule}: {outcomes}",
                note="tp-prediction-derive: counterfactual TP net of the row's own cost stack; a PASS is a screen -- "
                     "its follow-on is the harness-native e35 tp_r confirmation of K* before any Tier-3 proposal",
                measurement=meas)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--legs", default="")
    ap.add_argument("--out", default=None)
    ap.add_argument("--days", type=int, default=1830)
    ap.add_argument("--end", default=None, help="window end YYYY-MM-DD (default: yesterday UTC)")
    ap.add_argument("--conditioned", action="store_true")
    ap.add_argument("--pool", action="store_true", help="grade the legs as ONE pooled family")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--from-emits", default=None, help="SMOKE/TEST: dir of UNCAPPED <leg>.jsonl emits")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.out or not a.legs:
        ap.error("--legs and --out are required")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(v: Dict[str, Any]) -> None:
        (out / "verdict.json").write_text(json.dumps(v, indent=1, default=str) + "\n", encoding="utf-8")

    legs = [s.strip() for s in a.legs.split(",") if s.strip()]
    import yaml
    try:
        strategies = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())["strategies"]
    except (OSError, KeyError, ValueError, yaml.YAMLError) as exc:
        write(dict(verdict="not_applicable", read_state="producer_failed", n=None,
                   population=f"cannot read config/strategies.yaml: {exc}"))
        return 0
    missing = [lg for lg in legs if lg not in strategies]
    if missing:
        write(dict(verdict="not_applicable", read_state="producer_failed", n=None,
                   population=f"legs not in config/strategies.yaml: {missing}"))
        return 0
    end_d = (datetime.fromisoformat(a.end).date() if a.end
             else datetime.now(timezone.utc).date() - timedelta(days=1))
    start_d = end_d - timedelta(days=a.days)
    window = f"{start_d.isoformat()}..{end_d.isoformat()} ({a.days} d)"
    emits: Dict[str, Path] = {}
    failed: Dict[str, str] = {}
    if a.from_emits:
        for lg in legs:
            p = Path(a.from_emits) / f"{lg}.jsonl"
            (emits.__setitem__(lg, p) if p.exists() else failed.__setitem__(lg, "no emit file"))
    else:
        data_dir = Path(a.data_dir) if a.data_dir else out / "data"
        fs = (start_d - timedelta(days=WARMUP_DAYS)).isoformat()
        for lg in legs:
            r = replay_uncapped(lg, strategies[lg], data_dir, out, start_d.isoformat(), end_d.isoformat(), fs)
            (emits.__setitem__(lg, Path(r["emit"])) if r["ok"] else failed.__setitem__(lg, r["reason"]))
    if failed:
        write(dict(verdict="not_applicable", read_state="producer_failed", n=None,
                   population=f"leg replay failed, NEVER MEASURED: {failed}"))
        return 0
    v = assemble(legs, emits, strategies, conditioned=a.conditioned, pooled=a.pool, window=window)
    write(v)
    print(json.dumps({k: v.get(k) for k in ("verdict", "read_state", "n", "population")}, indent=1))
    return 0


# ── self-test: planted controls that must be able to fail ────────────────────
def _synth(n: int, *, mfe_fn: Callable[[int], float], net_fn: Callable[[int, float], float],
           adx_fn: Optional[Callable[[int], float]] = None, risk_fn: Callable[[int], float] = lambda i: 0.01,
           leg: str = "x") -> List[Dict[str, Any]]:
    rows = []
    for i in range(n):
        m = mfe_fn(i)
        rows.append({"leg": leg, "t": f"2024-01-01T{i:06d}", "mfe": m, "net": net_fn(i, m), "cost": -0.05,
                     "cap_r": TP_VENUE_CAP_PCT / risk_fn(i), "risk_pct": risk_fn(i),
                     "adx": adx_fn(i) if adx_fn else None})
    return rows


def self_test() -> int:
    ok = total = 0

    def chk(label: str, cond: bool) -> None:
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"  FAIL {label}")

    # 1. counterfactual arithmetic
    row = {"mfe": 3.0, "net": -1.05, "cost": -0.05, "cap_r": 9.9}
    chk("TP touched -> exits at K net of cost", abs(cf_net(row, 2.0) - 1.95) < 1e-9)
    chk("TP not touched -> unchanged", cf_net(row, 4.0) == -1.05)
    chk("venue cap binds below K", abs(cf_net({**row, "cap_r": 2.5}, 4.0) - 2.45) < 1e-9)
    # 2. a leg whose trades all reverse at ~2R then stop out: a 1.75R TP must PASS_IMPROVES vs a 50R sentinel
    rev = _synth(300, mfe_fn=lambda i: 2.0 + (i % 5) * 0.1, net_fn=lambda i, m: -1.05)
    g = grade_unconditioned(rev, 50.0, False)
    chk("reversal leg: PASS_IMPROVES (positive control)", g["outcome"] == "PASS_IMPROVES")
    chk("reversal leg: K* reachable", g["k_star"] is not None and g["reach_oos_kstar"] >= REACH_FLOOR)
    # 3. a leg whose edge is the fat right tail: trades either stop at -1 or run to 20R and exit at 19R
    tail = _synth(400, mfe_fn=lambda i: 20.0 if i % 4 == 0 else 0.5,
                  net_fn=lambda i, m: 18.95 if m > 10 else -1.05, risk_fn=lambda i: 0.001)
    g = grade_unconditioned(tail, 50.0, False)
    chk("tail leg: a fixed TP is inferior -> FAIL (negative control)", g["outcome"] == "FAIL")
    # 4. thin leg -> INDETERMINATE, never a verdict
    g = grade_unconditioned(rev[:60], 50.0, False)
    chk("thin leg -> INDETERMINATE", g["outcome"] == "INDETERMINATE")
    # 5. drift: IS reaches 2R always, OOS never -> uncalibrated FAIL
    drift = _synth(300, mfe_fn=lambda i: 2.6 if i < 180 else 0.4, net_fn=lambda i, m: -1.05)
    g = grade_unconditioned(drift, 50.0, False)
    chk("drift -> FAIL (uncalibrated or below floor)", g["outcome"] == "FAIL")
    # 6. conditioned: high-vol trades reach 4R, low-vol only 1.5R -> conditioning must PASS
    cond = _synth(900, mfe_fn=lambda i: 4.2 if i % 3 == 2 else (1.6 if i % 3 == 0 else 2.6),
                  net_fn=lambda i, m: -1.05, risk_fn=lambda i: 0.005 * (1 + i % 3), adx_fn=lambda i: 20 + i % 7)
    g = grade_conditioned(cond, False)
    chk("conditioned control: vol-dependent reach -> PASS", g["outcome"] == "PASS")
    homog = _synth(900, mfe_fn=lambda i: 2.6, net_fn=lambda i, m: -1.05,
                   risk_fn=lambda i: 0.005 * (1 + i % 3), adx_fn=lambda i: 20 + i % 7)
    g = grade_conditioned(homog, False)
    chk("conditioned null: homogeneous reach -> not PASS", g["outcome"] != "PASS")
    # 7. pooled split keeps every leg in both halves
    a_ = _synth(100, mfe_fn=lambda i: 2.0, net_fn=lambda i, m: -1.05, leg="a")
    b_ = _synth(100, mfe_fn=lambda i: 2.0, net_fn=lambda i, m: -1.05, leg="b")
    is_, oos = split(a_ + b_, True)
    chk("pooled split: both legs in IS and OOS", {r["leg"] for r in is_} == {"a", "b"} == {r["leg"] for r in oos})
    # 8. parse refuses unreadable rows and counts them
    rows, bad = parse_rows([{"entry": 1.0, "sl": 0.99, "net_r": 1, "gross_r": 1.05, "mfe_r": 2,
                             "entry_time": "t"}, {"entry": "x"}], "z")
    chk("parse: one readable, one counted unreadable", len(rows) == 1 and bad == 1)
    print(f"self-test: {ok}/{total} passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
