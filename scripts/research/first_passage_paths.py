#!/usr/bin/env python3
# wiring: first_passage.py --paths / --harness-legs (research/queue units RQ-20261024-09a6-01, RQ-20261014-d6e7-06) via research-script-run.yml
"""First passage on the legs AS TRADED: per-bar harness paths, the as-traded exit-now label, R0 vs R1, the policy replay.

WHY THIS EXISTS (checklist row FIRST-PASSAGE-ON-PATHS; Programme 01 section 'AI active trade management --
the merged programme', operator 2026-10-24, verbatim on row ATM-AI-MERGE: "I want to start sort of trying to
merge our two top priorities and trying to get active management, like under AI management, and seeing like
what kind of models we can build to help us like with the live management of trades and moving the brackets
as conditions change."). Every earlier first-passage number came from `first_passage.py --ledgers`, which
RE-WALKS public candles with a FIXED bracket (initial SL = -1 R, TP = K R). No leg trades that bracket. This
module reads the harness's own per-bar emit (`--emit-paths`, `harness_emit.PathRecorder`, #18536): the stop
and target IN FORCE at every bar, so trailing, stall decay, TP revision and stale exits are each leg's own.
`first_passage.py --ledgers` is untouched (tests/test_first_passage_paths.py pins its output byte-for-byte).

INPUTS (one of)
  --paths <glob>         committed/emitted `<leg>__paths.jsonl` files; each needs its sibling `<leg>__trades.jsonl`
                         (the harness's --emit-trades row: entry, sl, gross_r, net_r, cost_*_r, mfe_r, exit_reason).
  --harness-legs a,b,c   run each leg config-exact INSIDE the unit with `path_metric_giveback.run_leg_paths`
                         (the same argv exit_lever_ablation builds) -- no committed corpus is needed.

THE CONSTRUCTION (say "as traded, harness paths" whenever a number from here is quoted)
  * A trade's path = its emitted rows, bar_offset 1..exit. The EXIT row is never an event (the decision at its
    close is moot). An event at bar t is the state after bar t's close: open_r_t, peak_r_t, bars_since_peak_t,
    and the row's own dist_to_stop_r / dist_to_target_r / atr_r / adx_14 (the levels the bar was tested
    against -- spec-literal, never the next bar's level, so no level set after bar t can leak in).
  * exit_r = the exit row's open_r (the harness's own exit price in R; truncation-honest -- never a re-simulated
    barrier). Checked against the trade row's gross_r; a mismatch > 0.01 R is counted and reported.
  * c_exit (R) = the round-trip slippage from `execution_costs.resolve_cost_policy(symbol)` x entry / risk: the
    extra cost an early market exit pays where the leg's own exit would have rested (conservative: the full
    round trip, as first_passage.py --ledgers charges cost_slippage_r once more). Compared per trade with the
    harness's own cost_slippage_r and the median gap is in the record.
  * AS-TRADED LABEL  L_t = 1 if exit_r - open_r_t < -c_exit: the leg's own eventual exit loses to exiting now.
  * TP/SL-FIRST LABEL (beside it, reported, not graded): 1 if the trade exited at its in-force stop, 0 at its
    target, excluded otherwise (stale / timeout / signal exits).
  * PARITY (the instrument's positive control, RULE ONE): max(peak_r over a trade's rows) == the trade row's
    mfe_r within 0.01 R. A leg enters the corpus only if >= 99% of its trades pass (09a6-01's registered bar);
    a leg that fails is listed `parity_failed` and the verdict is INDETERMINATE (the rule says so).
  * R0 (reference) = open_r, peak_r, log(1+bars_in), log(1+bars_since_peak), dist_to_stop_r/atr_r,
    dist_to_target_r/atr_r (0 when absent) + an absent-target indicator, + leg fixed effects. Room-to-stop is in
    the REFERENCE because it is the measured dominant predictor (AUC ~0.81, row EXIT-PATH-METRICS-W43).
  * R1 (candidate) = R0 + momentum over 3 and 6 bars in ATR (close change in R / atr_r, from the entry close at
    0 R; NaN -> train median until k bars exist),
    adx_14, 3-bar ADX change, 3-bar range / ATR, close location in the bar (1 = closed at the favourable end).
    Linear only: first_passage.Logit (L2 IRLS, NaN -> train median).
  * FOLDS: 6 calendar folds by trade entry (equal spans of the pooled entry range). Fit on the OTHER folds,
    PURGED: a train trade whose [entry, exit] overlaps the test fold's span is dropped. Each trade weighs 1, its
    bars share the weight (states within a trade are autocorrelated; power is carried by trades).
  * METRIC: trade-weighted OOS log-loss gain (nats) of R1 over R0, trade-cluster bootstrap 95% CI (2,000
    resamples), per-fold sign, calibration slope of R1 on OOS (label ~ a + s * logit p_R1).
  * POLICY (d6e7-06): a linear value head on R1 (ridge on D_t = exit_r - open_r_t, same folds, OOS only) exits
    at the first bar where the predicted D_t < -c_exit -- "exit when E[R | hold] < R_now - cost", the Q8
    framing. A driftless walk has E[D_t] = 0, so the head should never cross -c_exit there; any exit it takes is
    its information speaking (and pays for itself or loses). Policy net = open_r_t - fee - slippage - funding
    pro-rata to bar t - c_exit; the leg's arm = its own net_r. Graded per family (trend_donchian / pullback)
    on net R per trade, trade bootstrap CI, per-fold sign, maxDD guard (<= 1.35x the leg's, every leg in the
    family); net R per capital-day for both arms from `scripts/capital_efficiency.py` (the single home).

THE FREE NULL (Programme 01 Q8). On a driftless walk with a fixed bracket, P(stop before target from open R x)
is the gambler's ruin (T - x) / (T - S) for stop S and target T. `census` reports observed TP/SL-first by
open-R bucket beside that closed form, and the self-test checks the closed form on a fixed-bracket random walk.

    python3 scripts/research/first_passage.py --harness-legs trend_donchian_eth_4h --mode baseline --out <dir>
    python3 scripts/research/first_passage_paths.py --self-test

Tier-1 research code: replays history through config-exact harness runs on keyless public candles. No VM, no
journal, no live path; nothing it prints changes a bracket (every flip is Tier-3 via exit-refinement).
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "scripts", REPO / "scripts" / "research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import capital_efficiency  # noqa: E402  (the ONE definition of net R per capital-day)
from first_passage import Logit, TF_MIN, _ll  # noqa: E402  (the same linear head and log-loss as --ledgers)

PARITY_TOL_R = 0.01
PARITY_SHARE = 0.99
EXIT_R_TOL = 0.01
STOP_EXITS = frozenset({"stop", "trail_stop", "be_stop", "sl", "sl_hit", "stop_loss"})
TARGET_EXITS = frozenset({"take_profit", "tp", "tp_hit", "target"})
R0 = ["open_r", "peak_r", "log_bars", "log_since_peak", "stop_atr", "target_atr", "no_target"]
R1_EXTRA = ["mom3_atr", "mom6_atr", "adx", "adx_slope3", "range3_atr", "clv"]
R1 = R0 + R1_EXTRA


# --------------------------------------------------------------------------- corpus
def _family(leg: str) -> str:
    return "trend_donchian" if "donchian" in leg else ("pullback" if "pullback" in leg else "other")


def c_exit_r(symbol: str, entry: float, risk: float) -> float:
    """Extra exit cost in R: the venue round-trip slippage from the ONE cost policy, on the entry notional."""
    from src.runtime.execution_costs import resolve_cost_policy
    slip_bps, _fund = resolve_cost_policy(symbol)
    return float(slip_bps) / 1e4 * float(entry) / float(risk) if risk > 0 else float("nan")


def build_trades(leg: str, path_rows: List[dict], trade_rows: List[dict], tf: Optional[str],
                 cost_fn=c_exit_r) -> Tuple[List[dict], Dict[str, Any]]:
    """Join path rows to trade rows by trade_key; one dict per trade with its per-bar arrays."""
    from harness_emit import trade_key
    by_key: Dict[str, List[dict]] = {}
    for r in path_rows:
        by_key.setdefault(r["trade_key"], []).append(r)
    tfm = TF_MIN.get(tf or "", None)
    trades, n_nopath, n_exit_mismatch, par_n, par_ok, slip_gap = [], 0, 0, 0, 0, []
    for t in trade_rows:
        k = trade_key(t.get("strategy") or leg, t.get("symbol"), t.get("entry_time"), t.get("direction"))
        rows = sorted(by_key.get(k, []), key=lambda r: r["bar_offset"])
        if not rows or not rows[-1].get("is_exit"):
            n_nopath += 1
            continue
        entry, sl = float(t["entry"]), float(t["sl"])
        risk = abs(entry - sl)
        if risk <= 0:
            n_nopath += 1
            continue
        exit_r = float(rows[-1]["open_r"])
        if t.get("gross_r") is not None and abs(exit_r - float(t["gross_r"])) > EXIT_R_TOL:
            n_exit_mismatch += 1
        if t.get("mfe_r") is not None:
            par_n += 1
            par_ok += abs(max(float(r["peak_r"]) for r in rows) - float(t["mfe_r"])) <= PARITY_TOL_R
        cx = cost_fn(t.get("symbol"), entry, risk)
        if t.get("cost_slippage_r") is not None and cx == cx:
            slip_gap.append(abs(cx - float(t["cost_slippage_r"])))
        g = lambda f: np.array([np.nan if r.get(f) is None else float(r[f]) for r in rows], dtype=float)  # noqa: E731
        trades.append({
            "leg": leg, "family": _family(leg), "symbol": t.get("symbol"), "tfm": tfm,
            "entry_time": pd.Timestamp(t["entry_time"]), "exit_time": pd.Timestamp(t["exit_time"]),
            "exit_reason": str(t.get("exit_reason") or rows[-1].get("exit_reason")),
            "exit_r": exit_r, "net_r": float(t["net_r"]), "c_exit": cx,
            "cost_fee_r": float(t.get("cost_fee_r") or 0.0), "cost_slippage_r": float(t.get("cost_slippage_r") or 0.0),
            "cost_funding_r": float(t.get("cost_funding_r") or 0.0),
            "bars": len(rows), "open_r": g("open_r"), "peak_r": g("peak_r"), "bsp": g("bars_since_peak"),
            "off": g("bar_offset"), "c_r": g("c_r"), "fav": g("fav_r"), "adv": g("adv_r"),
            "stop_r": g("stop_r"), "target_r": g("target_r"), "d_stop": g("dist_to_stop_r"),
            "d_target": g("dist_to_target_r"), "atr_r": g("atr_r"), "adx": g("adx_14")})
    share = par_ok / par_n if par_n else None
    rep = {"leg": leg, "n_trade_rows": len(trade_rows), "n_path_rows": len(path_rows), "n_trades": len(trades),
           "n_without_path": n_nopath, "n_exit_r_mismatch_gross_r": n_exit_mismatch,
           "parity_n": par_n, "parity_share": None if share is None else round(share, 4),
           "c_exit_vs_harness_slippage_median_gap_r": round(float(np.median(slip_gap)), 5) if slip_gap else None,
           "state": "included" if (share is not None and share >= PARITY_SHARE and trades) else "parity_failed"}
    return trades, rep


def load_paths_glob(pat: str) -> Tuple[List[Tuple[str, List[dict], List[dict], Optional[str]]], List[dict]]:
    out, skipped = [], []
    for p in sorted(glob.glob(pat)):
        tp = p.replace("__paths.jsonl", "__trades.jsonl")
        leg = Path(p).name.replace("__paths.jsonl", "")
        if tp == p or not Path(tp).exists():
            skipped.append({"leg": leg, "paths": p, "reason": "sibling __trades.jsonl missing"})
            continue
        prow = [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]
        trow = [json.loads(ln) for ln in open(tp, encoding="utf-8") if ln.strip()]
        out.append((leg, prow, trow, None))
    return out, skipped


def run_harness_legs(legs: List[str], *, days: int, start: Optional[str], end: Optional[str], wd: Path,
                     data_csv: Optional[str]) -> Tuple[List[Tuple[str, List[dict], List[dict], Optional[str]]],
                                                       List[dict], Dict[str, Any]]:
    """Config-exact harness runs with --emit-paths, via path_metric_giveback.run_leg_paths (one argv builder)."""
    import yaml
    from path_metric_giveback import run_leg_paths
    cfgs = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    cfgs = cfgs.get("strategies", cfgs)
    out, failed, runs = [], [], {}
    for leg in legs:
        base = cfgs.get(leg)
        if not isinstance(base, dict):
            failed.append({"leg": leg, "reason": "not in config/strategies.yaml"})
            continue
        try:
            prow, meta = run_leg_paths(leg, base, days=days, start=start, end=end, wd=wd, data_csv=data_csv,
                                       non_crypto_source="auto")
        except Exception as exc:  # noqa: BLE001  # allow-silent: recorded as a failed leg with its error; the verdict goes INDETERMINATE (a leg's emit failed), never a pooled zero
            failed.append({"leg": leg, "reason": "harness_failed", "error": str(exc)[:400]})
            continue
        trow = [json.loads(ln) for ln in open(wd / f"{leg}__trades.jsonl", encoding="utf-8") if ln.strip()]
        out.append((leg, prow, trow, base.get("timeframe")))
        bt = meta.get("bt") or {}
        runs[leg] = {"argv": meta["argv"], "feed": meta["feed"],
                     "cost_policy": {f: bt.get(f) for f in ("fee_bps_roundtrip", "slippage_bps_roundtrip",
                                                            "funding_bps_per_window")}}
    return out, failed, runs


# --------------------------------------------------------------------------- events
def events(t: dict, fe_legs: List[str]) -> Dict[str, np.ndarray]:
    """Per-bar decision states (exit row excluded): R0/R1 feature matrices, the two labels, D_t."""
    n = t["bars"] - 1
    if n <= 0:
        return {"n": 0}
    c = np.r_[0.0, t["c_r"]]  # index b + 1 = bar offset b + 1; c[0] = the entry close (0 R)
    atr = t["atr_r"][:n]
    ok_atr = np.isfinite(atr) & (atr > 0)
    sa = np.where(ok_atr, atr, np.nan)
    b = np.arange(n)
    # undefined (NaN -> train median) until k closes exist: at bar 1 a "3-bar" change IS open_r, and the collinear
    # copy lets a linear head stack three slopes on the first state (measured on the planted self-test corpus)
    mom = lambda k: np.where(b + 1 >= k, (c[b + 1] - c[np.maximum(b + 1 - k, 0)]) / sa, np.nan)  # noqa: E731
    adx = t["adx"][:n]
    adx_s3 = np.where(b >= 3, adx - adx[np.maximum(b - 3, 0)], np.nan)
    hi = np.array([np.nanmax(t["fav"][max(0, i - 2):i + 1]) for i in b])
    lo = np.array([np.nanmin(t["adv"][max(0, i - 2):i + 1]) for i in b])
    rng = np.maximum(t["fav"][:n] - t["adv"][:n], 1e-9)
    no_tgt = ~np.isfinite(t["d_target"][:n])
    f = {"open_r": t["open_r"][:n], "peak_r": t["peak_r"][:n], "log_bars": np.log1p(t["off"][:n]),
         "log_since_peak": np.log1p(t["bsp"][:n]), "stop_atr": t["d_stop"][:n] / sa,
         "target_atr": np.where(no_tgt, 0.0, t["d_target"][:n] / sa), "no_target": no_tgt.astype(float),
         "mom3_atr": mom(3), "mom6_atr": mom(6), "adx": adx, "adx_slope3": adx_s3,
         "range3_atr": (hi - lo) / sa, "clv": (t["c_r"][:n] - t["adv"][:n]) / rng}
    fe = np.array([[1.0 if t["leg"] == lg else 0.0 for lg in fe_legs[1:]]] * n).reshape(n, max(len(fe_legs) - 1, 0))
    X0 = np.c_[np.column_stack([f[k] for k in R0]), fe]
    X1 = np.c_[np.column_stack([f[k] for k in R1]), fe]
    D = t["exit_r"] - t["open_r"][:n]
    y = (D < -t["c_exit"]).astype(float)
    tpsl = 1.0 if t["exit_reason"] in STOP_EXITS else (0.0 if t["exit_reason"] in TARGET_EXITS else np.nan)
    return {"n": n, "X0": X0, "X1": X1, "y": y, "D": D, "tpsl": tpsl, "x": t["open_r"][:n],
            "stop": t["stop_r"][:n], "target": t["target_r"][:n]}


def calendar_folds(trades: List[dict], k: int = 6) -> List[Tuple[List[int], List[int]]]:
    ent = np.array([t["entry_time"].value for t in trades], dtype=np.int64)
    ext = np.array([t["exit_time"].value for t in trades], dtype=np.int64)
    edges = np.linspace(ent.min(), ent.max() + 1, k + 1)
    fid = np.clip(np.searchsorted(edges, ent, side="right") - 1, 0, k - 1)
    out = []
    for f in range(k):
        te = [int(i) for i in np.nonzero(fid == f)[0]]
        lo_, hi_ = edges[f], edges[f + 1]
        tr = [int(i) for i in np.nonzero(fid != f)[0] if not (ent[i] < hi_ and ext[i] >= lo_)]
        out.append((tr, te))
    return out


def _ridge(X: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float = 1.0):
    med = np.nanmedian(X, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    Xf = np.where(np.isfinite(X), X, med)
    mu, sd = Xf.mean(0), Xf.std(0) + 1e-9
    Z = np.c_[np.ones(len(Xf)), (Xf - mu) / sd]
    reg = np.eye(Z.shape[1]) * lam
    reg[0, 0] = 0.0
    beta = np.linalg.solve((Z * w[:, None]).T @ Z + reg, (Z * w[:, None]).T @ y)

    def pred(Xn: np.ndarray) -> np.ndarray:
        Xn = np.where(np.isfinite(Xn), Xn, med)
        return np.c_[np.ones(len(Xn)), (Xn - mu) / sd] @ beta
    return pred


def fit_info(trades: List[dict], ev: List[dict], folds: List[Tuple[List[int], List[int]]], *,
             n_boot: int = 2000, seed: int = 7) -> Dict[str, Any]:
    """R1 vs R0 on the as-traded label: trade-weighted OOS log-loss gain, cluster bootstrap, folds, slope."""
    per_trade: Dict[int, Dict[str, Any]] = {}
    fold_rep, heads = [], []
    for fi, (tr, te) in enumerate(folds):
        tr = [i for i in tr if ev[i]["n"]]
        te = [i for i in te if ev[i]["n"]]
        if not tr or not te:
            fold_rep.append({"fold": fi, "state": "empty", "n_train_trades": len(tr), "n_test_trades": len(te)})
            heads.append(None)
            continue
        w = np.concatenate([np.full(ev[i]["n"], 1.0 / ev[i]["n"]) for i in tr])
        y = np.concatenate([ev[i]["y"] for i in tr])
        X0, X1 = (np.vstack([ev[i][k] for i in tr]) for k in ("X0", "X1"))
        m0, m1 = Logit().fit(X0, y, w), Logit().fit(X1, y, w)
        D = np.concatenate([ev[i]["D"] for i in tr])
        # The value head is fit PER STATE (weight 1), never trade-weighted: 1/n_bars is a function of the trade's
        # FUTURE length, which is the label's shadow (a short trade is a stop-out), so a trade-weighted fit learns
        # E_w[D | x] != E[D | x] -- measured on the self-test null: -0.5 R predicted at bar 1 against +0.05 R.
        # The decision at bar t applies to every trade alive at t, so the unweighted state mean is the one it needs.
        v1 = _ridge(X1, D, np.ones(len(D)))
        heads.append({"m0": m0, "m1": m1, "v1": v1})
        g = []
        for i in te:
            e = ev[i]
            p0, p1 = m0.predict_proba_sl(e["X0"]), m1.predict_proba_sl(e["X1"])
            gain = float(_ll(e["y"], p0).mean() - _ll(e["y"], p1).mean())
            per_trade[i] = {"fold": fi, "gain": gain, "logit_p1": np.log(p1 / (1 - p1)), "y": e["y"],
                            "auc_parts": (p0, p1)}
            g.append(gain)
        fold_rep.append({"fold": fi, "n_train_trades": len(tr), "n_test_trades": len(te),
                         "gain_nats": round(float(np.mean(g)), 5), "train_base_rate": round(float(np.average(y, weights=w)), 4)})
    gains = np.array([v["gain"] for v in per_trade.values()])
    rng = np.random.default_rng(seed)
    boot = np.array([gains[rng.integers(0, len(gains), len(gains))].mean() for _ in range(n_boot)]) if len(gains) else np.array([])
    slope = None
    if per_trade:
        lz = np.concatenate([np.clip(v["logit_p1"], -8, 8) for v in per_trade.values()])
        ly = np.concatenate([v["y"] for v in per_trade.values()])
        lw = np.concatenate([np.full(len(v["y"]), 1.0 / len(v["y"])) for v in per_trade.values()])
        if 0 < ly.mean() < 1:
            cm = Logit(l2=1e-3).fit(lz[:, None], ly, lw)
            slope = round(float(cm.b[1] / cm.sd[0]), 3)
    per_leg = {}
    for lg in sorted({trades[i]["leg"] for i in per_trade}):
        gl = np.array([v["gain"] for i, v in per_trade.items() if trades[i]["leg"] == lg])
        per_leg[lg] = {"n_trades": len(gl), "gain_nats": round(float(gl.mean()), 5),
                       "graded": bool(len(gl) >= 100)}
    ys = np.concatenate([v["y"] for v in per_trade.values()]) if per_trade else np.array([])
    return {"n_oos_trades": int(len(gains)), "n_oos_states": int(len(ys)),
            "label_rate_oos": round(float(ys.mean()), 4) if len(ys) else None,
            "mean_gain_nats": round(float(gains.mean()), 5) if len(gains) else None,
            "ci95": [round(float(np.percentile(boot, 2.5)), 5), round(float(np.percentile(boot, 97.5)), 5)]
            if len(boot) else None,
            "folds_positive": sum(1 for f in fold_rep if (f.get("gain_nats") or 0) > 0),
            "n_folds_scored": sum(1 for f in fold_rep if "gain_nats" in f),
            "calibration_slope_r1": slope, "folds": fold_rep, "per_leg": per_leg,
            "features": {"R0": R0 + ["leg_fixed_effects"], "R1": R1 + ["leg_fixed_effects"]},
            "_heads": heads, "_per_trade": per_trade}


def _max_dd(x: List[float]) -> float:
    c = np.cumsum(x)
    return float(np.max(np.maximum.accumulate(np.r_[0.0, c]) - np.r_[0.0, c]))


def policy_replay(trades: List[dict], ev: List[dict], folds, heads, *, seed: int = 11,
                  n_boot: int = 2000) -> Dict[str, Any]:
    """Exit at the first bar where the OOS value head predicts D_t < -c_exit; vs the leg's own exit."""
    recs = []
    for fi, (_tr, te) in enumerate(folds):
        h = heads[fi]
        if h is None:
            continue
        for i in te:
            t, e = trades[i], ev[i]
            rec = {"leg": t["leg"], "family": t["family"], "fold": fi, "entry_time": t["entry_time"],
                   "act_net": t["net_r"], "act_bars": t["bars"], "pol_net": t["net_r"], "pol_bars": t["bars"],
                   "early": False, "tfm": t["tfm"]}
            if e["n"]:
                hit = np.nonzero(h["v1"](e["X1"]) < -t["c_exit"])[0]
                if len(hit):
                    b = int(hit[0])
                    frac = (b + 1) / t["bars"]
                    rec.update(pol_net=float(e["x"][b]) - t["cost_fee_r"] - t["cost_slippage_r"]
                               - t["cost_funding_r"] * frac - t["c_exit"], pol_bars=b + 1, early=True)
            recs.append(rec)
    rng = np.random.default_rng(seed)

    def summ(rs: List[dict]) -> Dict[str, Any]:
        if not rs:
            return {"n": 0}
        bm = rs[0]["tfm"]
        d = np.array([r["pol_net"] - r["act_net"] for r in rs])
        boot = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n_boot)])
        fold_pos = {}
        for f in sorted({r["fold"] for r in rs}):
            dd = [r["pol_net"] - r["act_net"] for r in rs if r["fold"] == f]
            fold_pos[str(f)] = round(float(np.mean(dd)), 4)
        legs_dd = {}
        for lg in sorted({r["leg"] for r in rs}):
            lr = sorted([r for r in rs if r["leg"] == lg], key=lambda r: r["entry_time"])
            a, p = _max_dd([r["act_net"] for r in lr]), _max_dd([r["pol_net"] for r in lr])
            legs_dd[lg] = {"n": len(lr), "act_maxdd_r": round(a, 3), "pol_maxdd_r": round(p, 3),
                           "ok": bool(p <= 1.35 * a + 1e-9)}
        ce = lambda net, bars: capital_efficiency.summarize(  # noqa: E731
            bar_minutes=bm, position_bars=float(bars), capital_bars=float(bars), net_total_r=float(net), n_trades=len(rs))
        same_tf = len({r["tfm"] for r in rs}) == 1
        return {"n": len(rs), "act_net_r_per_trade": round(float(np.mean([r["act_net"] for r in rs])), 4),
                "pol_net_r_per_trade": round(float(np.mean([r["pol_net"] for r in rs])), 4),
                "diff_r_per_trade": round(float(d.mean()), 4),
                "diff_ci95": [round(float(np.percentile(boot, 2.5)), 4), round(float(np.percentile(boot, 97.5)), 4)],
                "early_exit_share": round(float(np.mean([r["early"] for r in rs])), 4),
                "folds_diff": fold_pos, "folds_positive": sum(1 for v in fold_pos.values() if v > 0),
                "maxdd_per_leg": legs_dd,
                "capital_efficiency_act": ce(sum(r["act_net"] for r in rs), sum(r["act_bars"] for r in rs)) if same_tf
                else _ce_mixed(rs, "act"),
                "capital_efficiency_pol": ce(sum(r["pol_net"] for r in rs), sum(r["pol_bars"] for r in rs)) if same_tf
                else _ce_mixed(rs, "pol")}
    fams = sorted({r["family"] for r in recs})
    return {"n_oos_trades": len(recs), "pooled": summ(recs),
            "per_family": {f: summ([r for r in recs if r["family"] == f]) for f in fams},
            "per_leg": {lg: summ([r for r in recs if r["leg"] == lg]) for lg in sorted({r["leg"] for r in recs})},
            "construction": "ridge value head on R1 (D_t = exit_r - open_r_t), OOS; exit at first bar with "
                            "predicted D_t < -c_exit; policy net = open_r_t - fee - slippage - funding pro-rata "
                            "- c_exit; actual = the trade's own net_r"}


def _ce_mixed(rs: List[dict], arm: str) -> Dict[str, Any]:
    """Mixed timeframes: capital-days summed per leg (each in its own bar minutes) through the single home."""
    days = sum(capital_efficiency.days_from_bars(r[f"{arm}_bars"], r["tfm"]) or 0.0 for r in rs)
    net = sum(r[f"{arm}_net"] for r in rs)
    return {"capital_days": round(days, 4), "net_r_per_capital_day": round(net / days, 4) if days > 0 else None}


def census(trades: List[dict], ev: List[dict]) -> Dict[str, Any]:
    """Descriptive: exit mix, as-traded label rate, oracle 'exit at the best close' saving, p0 calibration."""
    out: Dict[str, Any] = {}
    for lg in sorted({t["leg"] for t in trades}) + ["__pooled__"]:
        idx = [i for i, t in enumerate(trades) if lg == "__pooled__" or t["leg"] == lg]
        if not idx:
            continue
        reasons: Dict[str, int] = {}
        for i in idx:
            reasons[trades[i]["exit_reason"]] = reasons.get(trades[i]["exit_reason"], 0) + 1
        save = [max(0.0, float(np.nanmax(ev[i]["x"])) - trades[i]["c_exit"] - trades[i]["exit_r"]) if ev[i]["n"]
                else 0.0 for i in idx]
        ys = np.concatenate([ev[i]["y"] for i in idx if ev[i]["n"]]) if any(ev[i]["n"] for i in idx) else np.array([])
        out[lg] = {"n_trades": len(idx), "exit_reasons": reasons, "n_states": int(len(ys)),
                   "as_traded_label_rate": round(float(ys.mean()), 4) if len(ys) else None,
                   "oracle_best_close_saving_r_per_trade": round(float(np.mean(save)), 4),
                   "net_r_per_trade": round(float(np.mean([trades[i]["net_r"] for i in idx])), 4)}
    # gambler's-ruin calibration of the TP/SL-first label from the in-force bracket at each state
    cal: Dict[str, List[Tuple[float, float]]] = {}
    for i, t in enumerate(trades):
        e = ev[i]
        if not e["n"] or not np.isfinite(e["tpsl"]):
            continue
        for x, s, tg in zip(e["x"], e["stop"], e["target"]):
            if np.isfinite(tg) and tg > s and s < x < tg:
                k = f"{math.floor(x * 2) / 2:+.1f}"
                cal.setdefault(k, []).append((e["tpsl"], (tg - x) / (tg - s)))
    out["tpsl_first_vs_gamblers_ruin"] = {
        k: {"n_states": len(v), "obs_sl_first": round(float(np.mean([a for a, _ in v])), 4),
            "p0_mean": round(float(np.mean([b for _, b in v])), 4)}
        for k, v in sorted(cal.items(), key=lambda kv: float(kv[0])) if len(v) >= 50}
    return out


# --------------------------------------------------------------------------- verdicts (registered rules)
def verdict_info(fe: Dict[str, Any], *, min_gain: float, min_folds: int, min_n: int,
                 slope_lo: float, slope_hi: float, parity_ok: bool) -> Tuple[str, str]:
    """RULE-RQ20261024-09A6-01-ASTRADED-INFO, verbatim thresholds via the run.command."""
    n, g, ci, s = fe["n_oos_trades"], fe["mean_gain_nats"], fe["ci95"], fe["calibration_slope_r1"]
    if not parity_ok:
        return "indeterminate", "a leg's emit failed its parity check (or its harness run failed)"
    if n < min_n or g is None:
        return "indeterminate", f"n_oos_trades {n} < {min_n}"
    ok_s = s is not None and slope_lo <= s <= slope_hi
    if g >= min_gain and ci[0] > 0 and fe["folds_positive"] >= min_folds and ok_s:
        return "pass", f"R1-R0 {g:.5f} nats >= {min_gain}, CI {ci}, {fe['folds_positive']}/6 folds, slope {s}"
    if ci[1] < min_gain:
        return "fail", f"CI upper {ci[1]} < {min_gain} nats (gain {g}, {fe['folds_positive']}/6 folds, slope {s})"
    return "indeterminate", f"gain {g}, CI {ci}, folds+ {fe['folds_positive']}/6, slope {s}"


def verdict_policy(pe: Dict[str, Any], *, min_gain_r: float, min_folds: int, min_n: int,
                   parity_ok: bool) -> Tuple[str, str, Dict[str, str]]:
    """RULE-RQ20261014-D6E7-06-HARNESS-NATIVE-EXIT-POLICY: per family; PASS if a family passes, FAIL if all fail."""
    fam_v: Dict[str, str] = {}
    why = []
    for f, s in pe.get("per_family", {}).items():
        if f == "other" or s.get("n", 0) < min_n:
            fam_v[f] = "indeterminate"
            why.append(f"{f}: n {s.get('n', 0)} < {min_n}")
            continue
        ci, d = s["diff_ci95"], s["diff_r_per_trade"]
        dd_ok = all(v["ok"] for v in s["maxdd_per_leg"].values())
        if d >= min_gain_r and ci[0] > 0 and s["folds_positive"] >= min_folds and dd_ok:
            fam_v[f] = "pass"
        elif ci[1] < min_gain_r:
            fam_v[f] = "fail"
        else:
            fam_v[f] = "indeterminate"
        why.append(f"{f}: {fam_v[f]} (diff {d:+.4f} R/trade, CI {ci}, folds+ {s['folds_positive']}/6, "
                   f"maxDD {'ok' if dd_ok else 'BREACHED'}, n {s['n']})")
    if not parity_ok:
        return "indeterminate", "a leg's emit failed its parity check (or its harness run failed); " + "; ".join(why), fam_v
    if not fam_v:
        return "indeterminate", "no family scored", fam_v
    if "pass" in fam_v.values():
        return "pass", "; ".join(why), fam_v
    if all(v == "fail" for v in fam_v.values() if v) and set(fam_v) - {"other"}:
        return "fail", "; ".join(why), fam_v
    return "indeterminate", "; ".join(why), fam_v


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- driver
def run_paths(a: argparse.Namespace, corpus_override=None) -> Dict[str, Any]:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    failed: List[dict] = []
    runs: Dict[str, Any] = {}
    if corpus_override is not None:
        corpus, src = corpus_override, "in-process synthetic corpus (self-test)"
    elif a.paths:
        corpus, failed = load_paths_glob(a.paths)
        src = f"emitted path files {a.paths}"
    else:
        legs = [s.strip() for s in a.harness_legs.split(",") if s.strip()]
        wd = Path(tempfile.mkdtemp(prefix="first_passage_paths_", dir=str(out)))
        corpus, failed, runs = run_harness_legs(legs, days=a.days, start=a.start, end=a.end, wd=wd,
                                                data_csv=a.data_csv)
        src = f"config-exact harness runs with --emit-paths of {legs}, window {a.start} .. {a.end}"
    trades, reports = [], []
    for leg, prow, trow, tf in corpus:
        ts, rep = build_trades(leg, prow, trow, tf or (trow[0].get("meta") or {}).get("timeframe") if trow else tf,
                               cost_fn=getattr(a, "_cost_fn", None) or c_exit_r)
        if rep["state"] == "included" and ts and ts[0]["tfm"] is None:
            rep["state"] = "timeframe_unresolved"
        reports.append(rep)
        if rep["state"] == "included":
            trades.extend(ts)
    parity_ok = not failed and all(r["state"] == "included" for r in reports) and bool(reports)
    fe_legs = sorted({t["leg"] for t in trades})
    ev = [events(t, fe_legs) for t in trades]
    meas: Dict[str, Any] = {"legs": reports, "failed_or_skipped": failed, "runs": runs,
                            "construction": "as_traded_harness_paths (stop/target in force per bar)",
                            "label": "L_t = 1 if exit_r - open_r_t < -c_exit (c_exit: resolve_cost_policy round-trip "
                                     "slippage x entry / risk)",
                            "n_states": int(sum(e["n"] for e in ev))}
    if trades:
        meas["census"] = census(trades, ev)
    verdict, why = "indeterminate", "no trades in the corpus"
    if trades and a.mode in ("baseline", "policy"):
        fl = calendar_folds(trades, a.n_folds)
        fe = fit_info(trades, ev, fl, n_boot=a.n_boot)
        meas["info"] = _strip(fe)
        if a.mode == "baseline":
            verdict, why = verdict_info(fe, min_gain=a.min_gain_nats, min_folds=a.min_folds, min_n=a.min_n,
                                        slope_lo=0.7, slope_hi=1.3, parity_ok=parity_ok)
        else:
            pe = policy_replay(trades, ev, fl, fe["_heads"], n_boot=a.n_boot)
            verdict, why, fam = verdict_policy(pe, min_gain_r=a.min_trade_gain_r, min_folds=a.min_folds,
                                               min_n=a.min_n, parity_ok=parity_ok)
            pe["family_verdicts"] = fam
            meas["policy"] = pe
    elif trades and a.mode == "census":
        why = "census is descriptive; no registered rule grades it"
    n_tr = len(trades)
    pop = (f"{src}; {n_tr} trades / {meas['n_states']} pre-exit bar states on "
           f"{sum(r['state'] == 'included' for r in reports)} legs passing the peak_r==mfe_r parity control; "
           "as traded (harness paths, the leg's own stop/target per bar), net of the harness's venue-aware cost "
           "stack, 6 purged calendar folds by entry")
    v = {"verdict": verdict, "read_state": "measured" if trades else "unmeasured", "population": pop,
         "n": n_tr, "measurement": meas, "note": f"paths/{a.mode}: {why}"}
    (out / "verdict.json").write_text(json.dumps(v, indent=2, default=str) + "\n", encoding="utf-8")
    return v


def add_args(ap: argparse.ArgumentParser) -> None:
    """The --paths options, added to first_passage.py's parser (defaults leave --ledgers mode untouched)."""
    ap.add_argument("--paths", default=None, help="glob of <leg>__paths.jsonl (each with its sibling __trades.jsonl)")
    ap.add_argument("--harness-legs", default=None, help="comma list: run each leg config-exact with --emit-paths")
    ap.add_argument("--days", type=int, default=2330, help="--harness-legs: candle history to fetch")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--data-csv", default=None, help="--harness-legs: one candle CSV for every leg (tests)")
    ap.add_argument("--n-folds", type=int, default=6)
    ap.add_argument("--n-boot", type=int, default=2000)


# --------------------------------------------------------------------------- self-test
def synthetic_corpus(planted: bool, n_trades: int = 900, seed: int = 5, trail: bool = True,
                     target: Optional[float] = 3.0, max_bars: int = 60,
                     sub: int = 10) -> List[Tuple[str, List[dict], List[dict], str]]:
    """Two synthetic legs (one per family) emitted in the harness_path_v1 schema.

    Each bar is `sub` sub-steps of ONE walk: the close is the last sub-step, high/low are the sub-step extremes, and
    the stop/target are tested sub-step by sub-step and filled AT the level. So the bar extremes belong to the same
    martingale as the close (a wick that is not part of the close process is a free exit edge, and an earlier
    draft of this test planted exactly that by accident: trade-weighted E[D] = -0.28 R on its "null").
    planted=False: driftless -> E[exit_r - open_r_t | anything known at t] = 0 (optional stopping), up to the
    sub-step overshoot. planted=True: a per-trade REGIME the ADX reads -- half the trades trend (adx ~30, drift
    +0.08 R/bar), half chop (adx ~14, drift -0.08 R/bar). The information lives in adx_14, which only R1 carries;
    R0 (where the trade sits) sees it only through the path's shadow. (A momentum-persistence plant was tried first
    and R0's bars-since-peak absorbed most of it: +0.0007 nats, below the bar -- a positive control must plant
    what only the candidate can see.)"""
    from harness_emit import trade_key
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2020-07-01", tz="UTC")
    legs = {"trend_donchian_syn_4h": ([], [], "4h"), "syn_pullback_2h": ([], [], "2h")}
    names = list(legs)
    for k in range(n_trades):
        leg = names[k % 2]
        prow, trow, tf = legs[leg]
        tfm = TF_MIN[tf]
        et = t0 + pd.Timedelta(hours=int(k * 2300 * 24 / n_trades))
        entry, risk = 100.0, 2.0
        c, peak, peak_j, stop, mae = [0.0], 0.0, 0, -1.0, 0.0
        z = float(rng.choice([-1.0, 1.0])) if planted else 0.0
        rows, ex = [], None
        for j in range(1, max_bars + 1):
            drift = 0.08 * z
            x, fav, adv, px = c[-1], c[-1], c[-1], None
            for _ in range(sub):
                x += drift / sub + rng.normal(0, 0.3 / math.sqrt(sub))
                if x <= stop:
                    ex, px, adv = ("trail_stop" if stop > -1.0 else "stop"), stop, min(adv, stop)
                    break
                if target is not None and x >= target:
                    ex, px, fav = "take_profit", target, max(fav, target)
                    break
                fav, adv = max(fav, x), min(adv, x)
            cl = px if ex else x
            if not ex and j == max_bars:
                ex, px = "timeout", cl
            row = {"schema": "harness_path_v1", "trade_key": trade_key(leg, "SYNUSDT", str(et), "long"),
                   "strategy": leg, "symbol": "SYNUSDT", "bar_index": j, "bar_offset": j,
                   "ts": str(et + pd.Timedelta(minutes=tfm * j)), "o_r": round(c[-1], 4), "c_r": round(cl, 4),
                   "fav_r": round(fav, 4), "adv_r": round(adv, 4), "stop_r": round(stop, 4),
                   "target_r": target, "dist_to_stop_r": round(cl - stop, 4),
                   "dist_to_target_r": None if target is None else round(target - cl, 4),
                   "atr_r": 0.35, "adx_14": round(22 + 8 * z + 3 * rng.normal(), 4), "is_exit": False}
            if fav > peak:
                peak, peak_j = fav, j
            mae = max(mae, -adv)
            row.update(open_r=round(cl, 6), peak_r=round(peak, 6), mae_r=round(mae, 6),
                       bars_since_peak=j - peak_j)
            if ex:
                row.update(is_exit=True, exit_reason=ex)
            rows.append(row)
            c.append(cl)
            if ex:
                break
            if trail:
                stop = max(stop, peak - 1.5)
        gross = float(rows[-1]["open_r"])
        trow.append({"strategy": leg, "symbol": "SYNUSDT", "entry_time": str(et), "direction": "long",
                     "exit_time": rows[-1]["ts"], "entry": entry, "sl": entry - risk, "exit_reason": ex,
                     "gross_r": round(gross, 4), "net_r": round(gross - 0.08, 4), "mfe_r": round(peak, 3),
                     "cost_fee_r": 0.05, "cost_slippage_r": 0.02, "cost_funding_r": 0.01})
        prow.extend(rows)
    return [(leg, p, t, tf) for leg, (p, t, tf) in legs.items()]


def _self_args(mode: str, out: str) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=mode)
    ap.add_argument("--out", default=out)
    ap.add_argument("--min-gain-nats", type=float, default=0.002)
    ap.add_argument("--min-folds", type=int, default=4)
    ap.add_argument("--min-n", type=int, default=100)
    ap.add_argument("--min-trade-gain-r", type=float, default=0.05)
    add_args(ap)
    a = ap.parse_args(["--n-boot", "400"])
    a._cost_fn = lambda sym, entry, risk: 0.02  # noqa: E731  (synthetic symbol: no venue in the cost table)
    return a


def _self_test() -> int:
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'} {label} {detail}")
        ok &= bool(cond)

    # 1. the free null: fixed bracket (-1, +2), no trail, driftless walk -> SL-first frequency ~ gambler's ruin
    corp = synthetic_corpus(False, n_trades=1500, trail=False, target=2.0, max_bars=400, seed=9)
    trades = [t for leg, p, tr, tf in corp for t in build_trades(leg, p, tr, tf, cost_fn=lambda *_: 0.02)[0]]
    ev = [events(t, sorted({t["leg"] for t in trades})) for t in trades]
    cal = census(trades, ev)["tpsl_first_vs_gamblers_ruin"]
    gap = max(abs(v["obs_sl_first"] - v["p0_mean"]) for v in cal.values()) if cal else 1.0
    check("gambler's ruin null holds on a fixed-bracket random walk", gap < 0.08, f"(max bucket gap {gap:.3f}, "
          f"{len(cal)} buckets)")
    with tempfile.TemporaryDirectory() as td:
        for planted, want_info, want_pol in ((False, ("fail", "indeterminate"), ("fail", "indeterminate")),
                                             (True, ("pass",), ("pass",))):
            corp = synthetic_corpus(planted)
            rep = [build_trades(leg, p, tr, tf, cost_fn=lambda *_: 0.02)[1] for leg, p, tr, tf in corp]
            check(f"planted={planted}: parity control passes", all(r["state"] == "included" for r in rep),
                  str([r["parity_share"] for r in rep]))
            vb = run_paths(_self_args("baseline", str(Path(td) / f"b{planted}")), corpus_override=corp)
            check(f"planted={planted}: info verdict in {want_info}", vb["verdict"] in want_info, vb["note"])
            vp = run_paths(_self_args("policy", str(Path(td) / f"p{planted}")), corpus_override=corp)
            check(f"planted={planted}: policy verdict in {want_pol}", vp["verdict"] in want_pol, vp["note"])
        # 2. a broken emit (peak_r below the trade's mfe_r) must fail parity and force INDETERMINATE
        corp = synthetic_corpus(True, n_trades=300)
        leg, p, tr, tf = corp[0]
        for r in tr:
            r["mfe_r"] = round(float(r["mfe_r"]) + 0.5, 3)
        vb = run_paths(_self_args("baseline", str(Path(td) / "broken")), corpus_override=[(leg, p, tr, tf)] + corp[1:])
        check("broken emit -> parity_failed -> indeterminate", vb["verdict"] == "indeterminate" and
              vb["measurement"]["legs"][0]["state"] == "parity_failed", vb["note"])
    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    print("run through first_passage.py --paths/--harness-legs, or --self-test", file=sys.stderr)
    sys.exit(2)
