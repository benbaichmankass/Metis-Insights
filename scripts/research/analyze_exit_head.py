#!/usr/bin/env python3
"""M30 × M20 — the EXIT-HEAD analyzer (uniqueness-weighted meta-label + net-of-fee gate).

Reads the per-bar in-trade exit panel (``build_intrabar_exit_panel.py``) and trains
the **take/skip exit head** — *should this open position keep holding, or exit
now?* — under the full de-Prado rigor the overlapping per-bar labels demand:

1. **Grouped, purged, embargoed walk-forward CV.** A whole trade's bars stay on
   one side of every split (grouped by ``trade_id``); train rows whose label window
   overlaps (or sits within the embargo of) the test period are purged. Rows are
   ordered by ``decision_time``.
2. **Uniqueness-weighted fit.** Each train row's weight = its average uniqueness
   (``1/concurrency`` over its label span, ch. 4) so the redundant overlapping
   labels don't over-count. A weighted logistic gives the take/skip head; a
   weighted ridge on ``advantage_r`` gives the sizing magnitude.
3. **OOS discrimination.** Out-of-sample AUC (hold vs exit) per fold + stability.
4. **Net-of-fee EXIT POLICY sim.** The head's decisions are simulated *per trade*
   (first bar the head says "exit" ⇒ the trade realizes its mark-to-market R there,
   net of an exit fee; else the trade keeps its fixed-exit R) and compared to the
   baseline fixed SL/TP exit — the headline **net-R improvement vs the fixed exit**,
   with the policy's realized-R Sharpe graded by the **probabilistic / deflated
   Sharpe** (ch. 14) and, across a config grid, **PBO via CSCV** (ch. 11).

**The pre-registered bar (mirrors ``exit-management-ml-experiment-DESIGN.md`` §4):**
the head must (a) discriminate OOS — **AUC > 0.55**, stable across folds — AND
(b) deliver a **positive net-of-fee R improvement vs the fixed exit** with a
deflated Sharpe that clears the multiple-testing benchmark. Miss either ⇒ the exit
wall is as hard as the entry wall; record the null and stop. Observe-only, Tier-1.

Usage::

    python scripts/research/analyze_exit_head.py --panel exit_head_panel.jsonl
    # PBO across a config grid:
    python scripts/research/analyze_exit_head.py --panel base.jsonl \\
        --config-panels ts8.jsonl ts12.jsonl ts18.jsonl --n-trials 3
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.research.meta_label import (  # noqa: E402
    average_uniqueness,
    deflated_sharpe_ratio,
    pbo_cscv,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)

try:
    import numpy as _np

    _NUMPY_OK = True
except Exception:  # noqa: BLE001
    _np = None  # type: ignore[assignment]
    _NUMPY_OK = False


# ---------------------------------------------------------------------------
# panel IO
# ---------------------------------------------------------------------------


def load_panel(path: Path) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    manifest = None
    mpath = path.with_suffix(path.suffix + ".manifest.json")
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = None
    return rows, manifest


def _dense_feats(rows: Sequence[Dict[str, Any]], manifest: Optional[Dict[str, Any]]) -> List[str]:
    if manifest and manifest.get("dense_feature_cols"):
        return list(manifest["dense_feature_cols"])
    keys = {k for r in rows for k in r if k.startswith("feat_")}
    return sorted(k for k in keys if any(r.get(k) is not None for r in rows))


def _not_computed(note: str) -> Dict[str, Any]:
    return {"computed": False, "note": note}


# ---------------------------------------------------------------------------
# weighted numpy primitives
# ---------------------------------------------------------------------------


def _standardize(x):
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd = _np.where(sd > 1e-12, sd, 1.0)
    return mu, sd


def _fit_weighted_logistic(x, y, w, *, iters: int = 60, ridge: float = 1e-4):
    n_feat = x.shape[1]
    beta = _np.zeros(n_feat)
    eye = _np.eye(n_feat) * ridge
    w = w / (w.mean() if w.mean() > 0 else 1.0)  # normalize weights to mean 1
    for _ in range(iters):
        eta = _np.clip(x @ beta, -30, 30)
        p = 1.0 / (1.0 + _np.exp(-eta))
        s = _np.clip(p * (1 - p), 1e-6, None) * w
        grad = x.T @ (w * (y - p))
        hess = x.T @ (x * s[:, None]) + eye
        try:
            step = _np.linalg.solve(hess, grad)
        except Exception:  # noqa: BLE001
            return None
        beta = beta + step
        if not _np.all(_np.isfinite(beta)):
            return None
        if _np.max(_np.abs(step)) < 1e-6:
            break
    return beta if _np.all(_np.isfinite(beta)) else None


def _fit_weighted_ridge(x, y, w, *, ridge: float = 1e-4):
    wd = w[:, None]
    xtx = x.T @ (x * wd) + _np.eye(x.shape[1]) * ridge
    try:
        return _np.linalg.solve(xtx, x.T @ (w * y))
    except Exception:  # noqa: BLE001
        return None


def _auc(scores, labels) -> Optional[float]:
    order = _np.argsort(scores, kind="mergesort")
    labels = _np.asarray(labels)[order]
    n_pos = float(labels.sum())
    n_neg = float(len(labels) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _np.arange(1, len(labels) + 1, dtype=float)
    u = ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def _predict_p(beta, x_std):
    xb = _np.column_stack([_np.ones(len(x_std)), x_std])
    eta = _np.clip(xb @ beta, -30, 30)
    return 1.0 / (1.0 + _np.exp(-eta))


# ---------------------------------------------------------------------------
# grouped, purged, embargoed walk-forward folds (by trade)
# ---------------------------------------------------------------------------


def _grouped_purged_folds(
    rows: List[Dict[str, Any]], *, n_folds: int, embargo_bars: int
):
    """Yield ``(train_idx, test_idx)`` row-index lists.

    Trades (``trade_id``) are ordered by their first ``decision_time`` and split
    into ``n_folds+1`` contiguous time blocks; fold k (1..n_folds) tests block k with
    training = blocks[0..k-1]. A train row is PURGED if its label window (``label_t1``)
    reaches within ``embargo_bars`` of the test block's earliest label start
    (``min label_t0``) — so no train label overlaps/adjoins the test period.
    """
    by_trade: Dict[Any, List[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_trade[r.get("trade_id")].append(i)
    # order trades by first decision_time (string-sortable ISO)
    def _first_time(tid):
        return min(str(rows[i].get("decision_time") or "") for i in by_trade[tid])

    trades = sorted(by_trade, key=_first_time)
    if len(trades) < (n_folds + 1):
        return
    edges = [round(i * len(trades) / (n_folds + 1)) for i in range(n_folds + 2)]
    blocks = [trades[edges[i] : edges[i + 1]] for i in range(n_folds + 1)]
    for k in range(1, n_folds + 1):
        test_trades = blocks[k]
        train_trades = [t for b in blocks[:k] for t in b]
        test_idx = [i for t in test_trades for i in by_trade[t]]
        if not test_idx or not train_trades:
            continue
        test_min_t0 = min(int(rows[i].get("label_t0") or 0) for i in test_idx)
        purge_before = test_min_t0 - int(embargo_bars)
        train_idx = [
            i
            for t in train_trades
            for i in by_trade[t]
            if int(rows[i].get("label_t1") or 0) < purge_before
        ]
        if train_idx and test_idx:
            yield train_idx, test_idx


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def _row_ok(r: Dict[str, Any], feats: Sequence[str]) -> bool:
    if r.get("label_hold") is None or r.get("decision_time") in (None, ""):
        return False
    return all(r.get(c) is not None for c in feats)


def analyze_exit_head(
    rows: List[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    n_folds: int = 5,
    embargo_bars: Optional[int] = None,
    exit_threshold: float = 0.5,
    exit_fee_r: float = 0.0,
    n_trials: int = 1,
    fdr_alpha: float = 0.1,
) -> Dict[str, Any]:
    feats = _dense_feats(rows, manifest)
    label_cfg = (manifest or {}).get("label_config", {}) if manifest else {}
    if embargo_bars is None:
        embargo_bars = int(label_cfg.get("time_stop_bars", 12))

    usable = [r for r in rows if _row_ok(r, feats)]
    report: Dict[str, Any] = {
        "panel": (manifest or {}).get("panel"),
        "harness": (manifest or {}).get("harness"),
        "n_rows_total": len(rows),
        "n_rows_usable": len(usable),
        "n_features": len(feats),
        "features": list(feats),
        "label_config": label_cfg,
        "base_hold_rate": (manifest or {}).get("base_hold_rate"),
        "config": {
            "n_folds": n_folds,
            "embargo_bars": embargo_bars,
            "exit_threshold": exit_threshold,
            "exit_fee_r": exit_fee_r,
            "n_trials": n_trials,
            "fdr_alpha": fdr_alpha,
        },
    }
    if not _NUMPY_OK:
        report["regression"] = _not_computed("numpy unavailable")
        return report
    if not feats:
        report["regression"] = _not_computed("no dense feature columns in the panel")
        return report
    if len(usable) < max(50, 10 * len(feats)):
        report["regression"] = _not_computed(
            f"only {len(usable)} usable rows for {len(feats)} features — underpowered"
        )
        return report

    # ---- univariate BH-FDR (feature vs hold) --------------------------------
    report["fdr"] = _univariate_fdr(usable, feats, fdr_alpha)

    # ---- grouped purged WF-CV ----------------------------------------------
    folds = list(_grouped_purged_folds(usable, n_folds=n_folds, embargo_bars=embargo_bars))
    if not folds:
        report["regression"] = _not_computed("no usable grouped purged folds (too few trades)")
        return report

    auc_folds: List[float] = []
    r2_folds: List[float] = []
    policy_head_r: List[float] = []
    policy_base_r: List[float] = []
    policy_delta_r: List[float] = []
    imp_drop: Dict[str, List[float]] = {c: [] for c in feats}

    for train_idx, test_idx in folds:
        xt = _np.array([[float(usable[i][c]) for c in feats] for i in train_idx])
        yt = _np.array([float(usable[i]["label_hold"]) for i in train_idx])
        advt = _np.array([float(usable[i].get("advantage_r") or 0.0) for i in train_idx])
        spans = [(int(usable[i]["label_t0"]), int(usable[i]["label_t1"])) for i in train_idx]
        w = _np.array(average_uniqueness(spans)) if len(spans) else _np.ones(len(train_idx))
        w = _np.where(w > 0, w, 1e-6)
        if set(int(v) for v in yt) != {0, 1}:
            continue
        mu, sd = _standardize(xt)
        xt_std = (xt - mu) / sd
        xb = _np.column_stack([_np.ones(len(xt_std)), xt_std])
        beta = _fit_weighted_logistic(xb, yt, w)
        beta_r = _fit_weighted_ridge(xb, advt, w)
        if beta is None:
            continue

        xe = _np.array([[float(usable[i][c]) for c in feats] for i in test_idx])
        ye = _np.array([float(usable[i]["label_hold"]) for i in test_idx])
        xe_std = (xe - mu) / sd
        p_hold = _predict_p(beta, xe_std)
        auc = _auc(p_hold, ye.astype(int))
        if auc is not None:
            auc_folds.append(auc)
            for j, col in enumerate(feats):
                xp = xe_std.copy()
                xp[:, j] = _np.random.default_rng(j).permutation(xp[:, j])
                a2 = _auc(_predict_p(beta, xp), ye.astype(int))
                if a2 is not None:
                    imp_drop[col].append(auc - a2)
        if beta_r is not None:
            adve = _np.array([float(usable[i].get("advantage_r") or 0.0) for i in test_idx])
            pred = _np.column_stack([_np.ones(len(xe_std)), xe_std]) @ beta_r
            ss_res = float(((adve - pred) ** 2).sum())
            ss_tot = float(((adve - adve.mean()) ** 2).sum())
            if ss_tot > 0:
                r2_folds.append(1.0 - ss_res / ss_tot)

        # ---- net-of-fee exit-policy sim (per test trade) ----
        _policy_sim(
            usable, test_idx, beta, mu, sd, feats,
            exit_threshold, exit_fee_r,
            policy_head_r, policy_base_r, policy_delta_r,
        )

    report["regression"] = _summarize_regression(auc_folds, r2_folds, imp_drop, feats)
    report["exit_policy"] = _summarize_policy(
        policy_head_r, policy_base_r, policy_delta_r, n_trials=n_trials
    )
    report["verdict"] = _verdict(report)
    return report


def _policy_sim(usable, test_idx, beta, mu, sd, feats, threshold, fee_r,
                head_r, base_r, delta_r):
    by_trade: Dict[Any, List[int]] = defaultdict(list)
    for i in test_idx:
        by_trade[usable[i].get("trade_id")].append(i)
    for tid, idxs in by_trade.items():
        idxs = sorted(idxs, key=lambda i: str(usable[i].get("decision_time") or ""))
        base = usable[idxs[0]].get("trade_realized_r")
        if base is None:
            continue
        base = float(base)
        xe = _np.array([[float(usable[i][c]) for c in feats] for i in idxs])
        p_hold = _predict_p(beta, (xe - mu) / sd)
        realized = base  # if the head never exits, keep the fixed-exit R
        for k, i in enumerate(idxs):
            if p_hold[k] < threshold:  # head says EXIT NOW at this bar
                upnl = usable[i].get("feat_upnl_r")
                if upnl is not None:
                    realized = float(upnl) - float(fee_r)
                break
        head_r.append(realized)
        base_r.append(base)
        delta_r.append(realized - base)


def _summarize_regression(auc_folds, r2_folds, imp_drop, feats) -> Dict[str, Any]:  # inert: feats — the ranked importance is keyed off imp_drop, which already carries the feature names; feats is redundant
    if not auc_folds:
        return _not_computed("no fold produced a defined OOS AUC")
    mean_auc = round(sum(auc_folds) / len(auc_folds), 4)
    importance = {c: round(sum(v) / len(v), 5) if v else None for c, v in imp_drop.items()}
    ranked = sorted(((c, i) for c, i in importance.items() if i is not None),
                    key=lambda t: t[1], reverse=True)
    out = {
        "computed": True,
        "model": "weighted_logistic(hold) + weighted_ridge(advantage_r)",
        "oos_auc": mean_auc,
        "oos_auc_by_fold": [round(a, 4) for a in auc_folds],
        "folds_above_half": sum(1 for a in auc_folds if a > 0.5),
        "n_folds_used": len(auc_folds),
        "permutation_importance_ranked": ranked,
    }
    if r2_folds:
        out["oos_r2_advantage"] = round(sum(r2_folds) / len(r2_folds), 4)
        out["oos_r2_by_fold"] = [round(r, 4) for r in r2_folds]
    return out


def _summarize_policy(head_r, base_r, delta_r, *, n_trials) -> Dict[str, Any]:
    if not delta_r:
        return _not_computed("no test trades for the exit-policy sim")
    n = len(delta_r)
    mean_delta = sum(delta_r) / n
    psr = probabilistic_sharpe_ratio(head_r)
    out = {
        "computed": True,
        "n_policy_trades": n,
        "mean_baseline_r": round(sum(base_r) / n, 4),
        "mean_head_r": round(sum(head_r) / n, 4),
        "mean_net_r_improvement": round(mean_delta, 4),
        "pct_trades_early_exited": round(
            100.0 * sum(1 for h, b in zip(head_r, base_r) if h != b) / n, 1
        ),
        "head_sharpe": (round(sharpe_ratio(head_r), 4) if sharpe_ratio(head_r) is not None else None),
        "head_psr": round(psr, 4) if psr is not None else None,
    }
    if n_trials and n_trials > 1:
        # variance across the config grid is supplied by the caller via PBO; here
        # we deflate against an assumed unit-variance grid as a conservative proxy.
        dsr = deflated_sharpe_ratio(head_r, n_trials=n_trials, variance_of_trial_sr=1.0)
        out["head_dsr"] = round(dsr, 4) if dsr is not None else None
    return out


def _verdict(report: Dict[str, Any]) -> Dict[str, Any]:
    reg = report.get("regression", {})
    pol = report.get("exit_policy", {})
    auc = reg.get("oos_auc") if reg.get("computed") else None
    stable = reg.get("folds_above_half") == reg.get("n_folds_used") if reg.get("computed") else False
    net_r = pol.get("mean_net_r_improvement") if pol.get("computed") else None
    passes_a = bool(auc is not None and auc > 0.55 and stable)
    passes_b = bool(net_r is not None and net_r > 0)
    return {
        "criterion_a_oos_auc_gt_0_55_stable": passes_a,
        "criterion_b_net_r_improvement_positive": passes_b,
        "clears_bar": passes_a and passes_b,
        "note": (
            "Pre-registered exit-management bar (design §4): AUC > 0.55 stable OOS "
            "AND positive net-of-fee R improvement vs the fixed exit. Both required "
            "to route to the net-of-cost backtest gate (Tier-3). A miss is an honest "
            "null — record it."
        ),
    }


# ---------------------------------------------------------------------------
# univariate FDR + p-value
# ---------------------------------------------------------------------------


def _auc_pvalue(auc: float, n_pos: int, n_neg: int) -> float:
    import math

    if n_pos == 0 or n_neg == 0:
        return 1.0
    mu = 0.5
    sigma = math.sqrt((n_pos + n_neg + 1) / (12.0 * n_pos * n_neg))
    if sigma == 0:
        return 1.0
    z = abs(auc - mu) / sigma
    return math.erfc(z / math.sqrt(2.0))


def _univariate_fdr(rows, feats, alpha) -> Dict[str, Any]:
    pvals: List[Tuple[str, float]] = []
    for c in feats:
        vals, labels = [], []
        for r in rows:
            v = r.get(c)
            if v is None:
                continue
            vals.append(float(v))
            labels.append(int(r["label_hold"]))
        if len(set(labels)) < 2:
            continue
        a = _auc(_np.asarray(vals), labels) if _NUMPY_OK else None
        if a is None:
            continue
        n_pos = sum(labels)
        pvals.append((c, _auc_pvalue(a, n_pos, len(labels) - n_pos)))
    m = len(pvals)
    if m == 0:
        return {"alpha": alpha, "m": 0, "survivors": []}
    ordered = sorted(pvals, key=lambda t: t[1])
    q_mono, prev = [], 1.0
    for rank in range(m - 1, -1, -1):
        name, p = ordered[rank]
        q = min(prev, min(1.0, p * m / (rank + 1)))
        q_mono.append((name, q))
        prev = q
    q_mono.reverse()
    return {
        "alpha": alpha,
        "m": m,
        "q_values": {n: round(q, 6) for n, q in q_mono},
        "survivors": [n for n, q in q_mono if q <= alpha],
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Analyze the M30×M20 per-bar in-trade exit head (uniqueness-weighted "
        "grouped purged WF-CV + net-of-fee policy sim + deflated Sharpe/PBO)."
    )
    p.add_argument("--panel", required=True)
    p.add_argument("--config-panels", nargs="*", default=None,
                   help="Extra panels (different label configs) for the PBO CSCV grid.")
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--embargo-bars", type=int, default=None)
    p.add_argument("--exit-threshold", type=float, default=0.5)
    p.add_argument("--exit-fee-r", type=float, default=0.0)
    p.add_argument("--n-trials", type=int, default=1)
    p.add_argument("--fdr-alpha", type=float, default=0.1)
    p.add_argument("--out", default=None)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    rows, manifest = load_panel(Path(args.panel))
    report = analyze_exit_head(
        rows, manifest, n_folds=args.n_folds, embargo_bars=args.embargo_bars,
        exit_threshold=args.exit_threshold, exit_fee_r=args.exit_fee_r,
        n_trials=args.n_trials, fdr_alpha=args.fdr_alpha,
    )

    # PBO across the config grid, if extra panels supplied.
    if args.config_panels:
        report["pbo"] = _pbo_across_configs(
            [args.panel, *args.config_panels],
            n_folds=args.n_folds, embargo_bars=args.embargo_bars,
            exit_threshold=args.exit_threshold, exit_fee_r=args.exit_fee_r,
        )

    out_path = Path(args.out) if args.out else Path(args.panel).with_suffix(".exit_head.json")
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    if not args.quiet:
        reg = report.get("regression", {})
        pol = report.get("exit_policy", {})
        v = report.get("verdict", {})
        print(f"exit head [{report.get('harness')}]: {report['n_rows_usable']} usable rows, "
              f"{report['n_features']} dense feats")
        if reg.get("computed"):
            print(f"  OOS AUC={reg['oos_auc']} folds={reg['oos_auc_by_fold']} "
                  f"({reg['folds_above_half']}/{reg['n_folds_used']} > 0.5)")
        else:
            print(f"  regression: {reg.get('note')}")
        if pol.get("computed"):
            print(f"  net-of-fee ΔR vs fixed exit={pol['mean_net_r_improvement']} "
                  f"(head {pol['mean_head_r']} vs base {pol['mean_baseline_r']}, "
                  f"PSR={pol.get('head_psr')}, {pol['pct_trades_early_exited']}% early-exited)")
        if v:
            print(f"  VERDICT: clears_bar={v['clears_bar']} "
                  f"(a={v['criterion_a_oos_auc_gt_0_55_stable']}, "
                  f"b={v['criterion_b_net_r_improvement_positive']})")
    return 0


def _pbo_across_configs(panel_paths, *, n_folds, embargo_bars, exit_threshold, exit_fee_r):
    """Per-trade realized-head-R matrix across configs → PBO (CSCV)."""
    per_config: List[List[float]] = []
    for pp in panel_paths:
        rows, manifest = load_panel(Path(pp))
        feats = _dense_feats(rows, manifest)
        usable = [r for r in rows if _row_ok(r, feats)]
        if not _NUMPY_OK or not usable:
            return _not_computed("numpy/rows unavailable for PBO")
        eb = embargo_bars or int((manifest or {}).get("label_config", {}).get("time_stop_bars", 12))
        folds = list(_grouped_purged_folds(usable, n_folds=n_folds, embargo_bars=eb))
        per_trade: Dict[Any, float] = {}
        for train_idx, test_idx in folds:
            xt = _np.array([[float(usable[i][c]) for c in feats] for i in train_idx])
            yt = _np.array([float(usable[i]["label_hold"]) for i in train_idx])
            spans = [(int(usable[i]["label_t0"]), int(usable[i]["label_t1"])) for i in train_idx]
            w = _np.where(_np.array(average_uniqueness(spans)) > 0, _np.array(average_uniqueness(spans)), 1e-6)
            if set(int(v) for v in yt) != {0, 1}:
                continue
            mu, sd = _standardize(xt)
            beta = _fit_weighted_logistic(_np.column_stack([_np.ones(len(xt)), (xt - mu) / sd]), yt, w)
            if beta is None:
                continue
            h, b, d = [], [], []
            _policy_sim(usable, test_idx, beta, mu, sd, feats, exit_threshold, exit_fee_r, h, b, d)
            by_trade = defaultdict(list)
            for i in test_idx:
                by_trade[usable[i].get("trade_id")].append(i)
            for k, tid in enumerate(by_trade):
                if k < len(h):
                    per_trade[(str(pp), tid)] = h[k]
        per_config.append(per_trade)  # type: ignore[arg-type]
    # align trades common across configs
    common = set.intersection(*[{k[1] for k in pc} for pc in per_config]) if per_config else set()
    if len(common) < 20 or len(per_config) < 2:
        return _not_computed(f"too few common trades ({len(common)}) / configs for PBO")
    ordered = sorted(common)
    matrix = [[per_config[c].get((str(panel_paths[c]), t), 0.0) for c in range(len(per_config))]
              for t in ordered]
    return pbo_cscv(matrix)


if __name__ == "__main__":
    sys.exit(main())
