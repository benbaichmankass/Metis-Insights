#!/usr/bin/env python3
# wiring: manual-only - run by the research queue unit RQ-20261024-09a6-03 (research-script-run.yml); otherwise a session runs it by hand.
"""AI-ATM rung B1: a per-bar REMAINING-MOVE quantile head that pulls the TP in (Tier-1 research tooling).

Question (unit RQ-20261024-09a6-03, rule registered before any run): at every bar t of a trade as traded,
can a model predict how much further the favourable move runs, and does pulling the TP in to that point
raise net R per capital-day without giving up net R?

Per ``--leg`` (config-exact: the same harness argv the evidence builder runs, via
``path_metric_giveback.run_leg_paths`` + ``harness_emit --emit-paths``):

* TARGET     ``M_t = max(0, max_{s>t} fav_r_s - open_r_t)`` over the bars after t up to and including the exit
             bar -- the favourable extreme the trade still reached after the decision at bar t's close.
* STATE      R1 of RQ-20261024-09a6-01, bar t only (nothing later is read): open_r, peak_r, bars_in_trade,
             bars_since_peak, distance to the stop and the target IN FORCE for the next bar in R and in ATR
             (target absent -> distance 0 + indicator), momentum (close change over 3 and 6 bars in ATR),
             adx_14, ADX 3-bar slope, 3-bar range / ATR, close location in the bar, leg fixed effects.
             (A local copy: the shared R1 feature function lives in first_passage.py, owned by the
             FIRST-PASSAGE-ON-PATHS lane; consolidate when it lands.)
* HEAD Q     linear quantile regression (IRLS on the pinball loss, ridge-stabilised, numpy only), tau in
             ``--tau``; predictions clamped at 0 (a remaining move is not negative).
* REFERENCE  Q0 = the leg's TRAINING-fold empirical tau-quantile of M within open_r buckets
             (<0, 0-1, 1-2, >=2 R); a thin bucket falls back to the leg's pooled quantile.
* FOLDS      ``--folds`` calendar folds by trade entry; the training set drops every trade of the held-out fold
             AND every trade whose life overlaps the held-out span (purged).
* CLAUSE 1   OOS pinball loss of Q below Q0 in >= ``--clause1-folds`` folds (trade-weighted) AND
             |pooled trade-weighted coverage - tau| <= ``--cov-tol``.
* CLAUSE 2   PULL-IN-ONLY replay. After bar t's close: ``cand = open_r_t + Q_t``; if ``cand`` is below the
             target in force and at least ``--atr-buffer`` ATR above the bar-t close, the TP becomes
             ``min(TP_prev, target in force, cand)``. It never moves up. The trade exits at that TP on the
             first LATER bar whose favourable extreme reaches it (filled at the TP; the venue slippage is in
             the cost stack), else its own exit stands unchanged. On the trade's own exit bar a stop-type exit
             wins the tie (conservative). Net R is priced with ``execution_costs.roundtrip_cost_r`` at the
             harness's own resolved cost policy (``resolve_cost_policy``), so an unchanged trade is
             bit-identical to the harness's net_r. Capital-days via ``capital_efficiency.days_from_bars``.
             PASS needs net R per capital-day above as-traded in >= ``--clause2-folds`` folds AND per-trade net
             R difference >= ``--min-diff`` with trade-cluster bootstrap CI lower bound >= ``--ci-lb`` AND
             maxDD <= ``--dd-mult`` x as traded.

Per-tau verdict: INDETERMINATE if pooled trades < ``--min-trades`` or the pulled-in TP filled on <
``--min-fill-share`` of trades (the head never bound); PASS iff clause 1 and clause 2; else FAIL. Clause 2
without clause 1 is a FAIL (P&L without prediction, TP doctrine). Family: PASS if any tau passes, else
INDETERMINATE if any tau is, else FAIL. Thresholds are flags the unit passes; none are defaulted here.
Nothing here edits config or places an order; writes only under ``--out``.

    python3 scripts/research/remaining_move_head.py --leg trend_donchian_eth_4h --days 2330 \\
        --start 2020-07-01 --end "2026-10-15 23:59" --tau 0.5 --tau 0.7 --folds 6 --clause1-folds 4 \\
        --clause2-folds 5 --cov-tol 0.05 --atr-buffer 0.25 --min-diff -0.02 --ci-lb -0.05 --dd-mult 1.35 \\
        --min-trades 600 --min-fill-share 0.05 --bootstrap 2000 --out /tmp/out

``--self-test`` runs a random-walk null (no calibration gain, verdict FAIL), a never-moves-up check with an
adversarial head, and a planted predictable-extent synthetic that must PASS.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "scripts" / "research", REPO / "scripts" / "ops", REPO / "scripts", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FEATURES = (
    "open_r",
    "peak_r",
    "bars_in_trade",
    "bars_since_peak",
    "stop_dist_r",
    "stop_dist_atr",
    "tgt_dist_r",
    "tgt_dist_atr",
    "tgt_absent",
    "mom3_atr",
    "mom6_atr",
    "adx_14",
    "adx_slope3",
    "range3_atr",
    "close_loc",
)
BUCKETS = (-math.inf, 0.0, 1.0, 2.0, math.inf)
STOP_EXITS = frozenset({"stop", "trail_stop"})
VERDICTS = ("pass", "fail", "indeterminate")


# ── per-trade arrays and the R1 state ───────────────────────────────────────
def _num(v: Any, default: float = float("nan")) -> float:
    return default if v is None else float(v)


def build_trades(path_rows: list[dict[str, Any]], tinfo: dict[str, dict[str, Any]], leg: str) -> list[dict[str, Any]]:
    """Group harness_path_v1 rows by trade into arrays, joined to the trade's entry/risk/cost row."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in path_rows:
        by.setdefault(r["trade_key"], []).append(r)
    out = []
    for key, rows in by.items():
        rows.sort(key=lambda r: int(r["bar_offset"]))
        if len(rows) < 2 or not rows[-1].get("is_exit") or key not in tinfo:
            continue

        def a(f: str, rows: list[dict[str, Any]] = rows) -> np.ndarray:
            return np.array([_num(r.get(f)) for r in rows], dtype=float)

        out.append(
            {
                "key": key,
                "leg": leg,
                "info": tinfo[key],
                "n": len(rows),
                "entry_ts": np.datetime64(str(tinfo[key]["entry_time"]).replace("+00:00", "")),
                "ts": [str(r["ts"]) for r in rows],
                "offset": a("bar_offset"),
                "fav": a("fav_r"),
                "adv": a("adv_r"),
                "c": a("c_r"),
                "open_r": a("open_r"),
                "peak": a("peak_r"),
                "bsp": a("bars_since_peak"),
                "stop": a("stop_r"),
                "target": a("target_r"),
                "atr": a("atr_r"),
                "adx": a("adx_14"),
                "exit_reason": rows[-1].get("exit_reason"),
            }
        )
        out[-1]["last_ts"] = np.datetime64(out[-1]["ts"][-1][:19])
    return out


def trade_states(t: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X, M, ok) for decisions after bars 0..n-2 of one trade. ``ok`` marks finite, usable rows."""
    n = t["n"]
    i = np.arange(n - 1)
    atr = t["atr"][i]
    c = t["c"]
    cext = np.concatenate([[0.0], c])  # cext[j+1] = c_r of bar j; cext[0] = entry

    def mom(k: int) -> np.ndarray:
        return (cext[i + 1] - cext[np.maximum(i + 1 - k, 0)]) / atr

    stop_n, tgt_n = t["stop"][i + 1], t["target"][i + 1]  # in force for the NEXT bar: known at this close
    absent = np.isnan(tgt_n)
    fav, adv = t["fav"], t["adv"]
    rng3 = np.array([(fav[max(0, j - 2) : j + 1].max() - adv[max(0, j - 2) : j + 1].min()) for j in i]) / atr
    span = fav[i] - adv[i]
    loc = np.where(span > 0, (c[i] - adv[i]) / np.where(span > 0, span, 1.0), 0.5)
    adx = t["adx"]
    slope = adx[i] - adx[np.maximum(i - 3, 0)]
    X = np.column_stack(
        [
            t["open_r"][i],
            t["peak"][i],
            t["offset"][i],
            t["bsp"][i],
            c[i] - stop_n,
            (c[i] - stop_n) / atr,
            np.where(absent, 0.0, tgt_n - c[i]),
            np.where(absent, 0.0, (tgt_n - c[i]) / atr),
            absent.astype(float),
            mom(3),
            mom(6),
            adx[i],
            slope,
            rng3,
            loc,
        ]
    )
    # remaining favourable extreme AFTER the decision bar, up to and including the exit bar
    fut = np.maximum.accumulate(fav[::-1])[::-1]  # fut[j] = max fav over bars j..n-1
    M = np.maximum(0.0, fut[i + 1] - t["open_r"][i])
    ok = np.isfinite(X).all(axis=1) & np.isfinite(M) & (atr > 0)
    return X, M, ok


# ── models ──────────────────────────────────────────────────────────────────
def qfit(
    X: np.ndarray, y: np.ndarray, tau: float, lam: float = 1.0, iters: int = 60
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Linear quantile regression by IRLS on the pinball loss (Hunter-Lange majorisation), standardised X."""
    mu, sd = X.mean(0), X.std(0)
    sd = np.where(sd > 1e-12, sd, 1.0)
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    pen = lam * np.eye(Z.shape[1])
    pen[0, 0] = 0.0
    beta = np.linalg.solve(Z.T @ Z + pen, Z.T @ y)
    for _ in range(iters):
        r = y - Z @ beta
        w = np.where(r >= 0, tau, 1 - tau) / np.maximum(np.abs(r), 1e-3)
        nb = np.linalg.solve((Z * w[:, None]).T @ Z + pen, (Z * w[:, None]).T @ y)
        if np.max(np.abs(nb - beta)) < 1e-6:
            beta = nb
            break
        beta = nb
    return beta, mu, sd


def qpredict(model: tuple[np.ndarray, np.ndarray, np.ndarray], X: np.ndarray) -> np.ndarray:
    beta, mu, sd = model
    return np.maximum(0.0, np.column_stack([np.ones(len(X)), (X - mu) / sd]) @ beta)


def bucket_of(open_r: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(BUCKETS[1:-1]), open_r, side="right")  # 0:<0 1:0-1 2:1-2 3:>=2


def q0_fit(open_r: np.ndarray, M: np.ndarray, leg: np.ndarray, tau: float, thin: int = 30) -> dict[str, Any]:
    tab: dict[tuple[str, int], float] = {}
    b = bucket_of(open_r)
    for lg in np.unique(leg):
        m = leg == lg
        pooled = float(np.quantile(M[m], tau)) if m.any() else 0.0
        tab[(lg, -1)] = pooled
        for k in range(4):
            mm = m & (b == k)
            tab[(lg, k)] = float(np.quantile(M[mm], tau)) if mm.sum() >= thin else pooled
    return {"tab": tab, "all": float(np.quantile(M, tau))}


def q0_predict(m0: dict[str, Any], open_r: np.ndarray, leg: np.ndarray) -> np.ndarray:
    b = bucket_of(open_r)
    return np.array([m0["tab"].get((lg, int(k)), m0["all"]) for lg, k in zip(leg, b)])


def pinball(y: np.ndarray, q: np.ndarray, tau: float) -> np.ndarray:
    r = y - q
    return np.where(r >= 0, tau * r, (tau - 1) * r)


# ── folds ───────────────────────────────────────────────────────────────────
def fold_edges(entry: np.ndarray, folds: int) -> np.ndarray:
    lo, hi = entry.min(), entry.max()
    return lo + (hi - lo) * np.arange(folds + 1) / folds  # datetime64 ns arithmetic


def fold_ids(entry: np.ndarray, edges: np.ndarray) -> np.ndarray:
    return np.minimum(len(edges) - 2, np.searchsorted(edges[1:-1], entry, side="right"))


# ── the pull-in-only replay ─────────────────────────────────────────────────
def replay(t: dict[str, Any], Q: np.ndarray, ok: np.ndarray, atr_buffer: float) -> dict[str, Any]:
    """Replay ONE trade under the pull-in-only TP. ``Q[i]`` is the head's remaining-move quantile after bar i.

    Returns exit index/price-in-R, whether a pull-in was ever set, whether it filled, and the TP path
    (one entry per decision bar: the TP in force after the decision, inf = none) so a test can assert it
    is non-increasing."""
    n = t["n"]
    tp = math.inf
    pulled = False
    tp_path: list[float] = []
    for i in range(n - 1):
        own = t["target"][i + 1]
        own = math.inf if np.isnan(own) else own
        if ok[i]:
            cand = t["open_r"][i] + Q[i]
            if cand < own and cand >= t["c"][i] + atr_buffer * t["atr"][i]:
                tp = min(tp, own, cand)
                pulled = True
        tp_path.append(tp)
        j = i + 1
        if tp < own and t["fav"][j] >= tp:
            if j == n - 1 and t["exit_reason"] in STOP_EXITS:
                continue  # stop-vs-TP tie on the exit bar: stop wins
            return {"exit_idx": j, "exit_r": float(tp), "pulled": True, "filled": True, "tp_path": tp_path}
    return {"exit_idx": n - 1, "exit_r": None, "pulled": pulled, "filled": False, "tp_path": tp_path}


def net_r_for(t: dict[str, Any], exit_idx: int, exit_r: float | None, cost: dict[str, float]) -> float:
    """Net R of the trade exiting at ``exit_idx``. ``exit_r is None`` -> the harness's own net_r."""
    info = t["info"]
    if exit_r is None:
        return float(info["net_r"])
    from src.runtime import execution_costs as ec

    sign = 1.0 if info["is_long"] else -1.0
    px = info["entry"] + sign * exit_r * info["risk"]
    cb = ec.roundtrip_cost_r(
        entry=info["entry"],
        exit_price=px,
        risk=info["risk"],
        entry_time=info["entry_time"],
        exit_time=t["ts"][exit_idx],
        fee_bps_roundtrip=cost["fee"],
        slippage_bps_roundtrip=cost["slip"],
        funding_bps_per_window=cost["fund"],
        funding_window_hours=cost.get("window_h", 8.0),
    )
    return float(exit_r - cb["total_cost_r"])


def _bar_minutes(trades: list[dict[str, Any]]) -> float | None:
    import capital_efficiency as ca
    import pandas as pd

    best = max(trades, key=lambda t: t["n"])
    return ca.bar_minutes_from_frame(pd.DataFrame({"timestamp": pd.to_datetime(best["ts"], utc=True)}))


def max_dd(net: np.ndarray, exit_ts: np.ndarray) -> float:
    cum = np.cumsum(net[np.argsort(exit_ts, kind="stable")])
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:] - cum)) if len(cum) else 0.0


# ── the grade ───────────────────────────────────────────────────────────────
def grade(
    trades: list[dict[str, Any]],
    cost_by_leg: dict[str, dict[str, float]],
    *,
    taus: list[float],
    folds: int,
    clause1_folds: int,
    clause2_folds: int,
    cov_tol: float,
    atr_buffer: float,
    min_diff: float,
    ci_lb: float,
    dd_mult: float,
    min_trades: int,
    min_fill_share: float,
    bootstrap: int,
    seed: int = 11,
) -> dict[str, Any]:
    import capital_efficiency as ca

    rng = np.random.default_rng(seed)
    legs_all = sorted({t["leg"] for t in trades})
    bm = {lg: _bar_minutes([t for t in trades if t["leg"] == lg]) for lg in legs_all}
    entry = np.array([t["entry_ts"] for t in trades])
    last = np.array([t["last_ts"] for t in trades])
    edges = fold_edges(entry, folds)
    fid = fold_ids(entry, edges)
    # state tables (all trades): X, M, ok, owning trade, leg
    tabs = [trade_states(t) for t in trades]
    Xs = [tb[0][tb[2]] for tb in tabs]
    Ms = [tb[1][tb[2]] for tb in tabs]
    n_states_dropped = int(sum((~tb[2]).sum() for tb in tabs))
    leg_oh_of = {lg: np.eye(len(legs_all))[k] for k, lg in enumerate(legs_all)}

    def with_leg(i: int) -> np.ndarray:
        return np.hstack([Xs[i], np.tile(leg_oh_of[trades[i]["leg"]], (len(Xs[i]), 1))]) if len(legs_all) > 1 else Xs[i]

    XL = [with_leg(i) for i in range(len(trades))]
    out: dict[str, Any] = {
        "per_tau": {},
        "n_trades": len(trades),
        "legs": legs_all,
        "folds": folds,
        "n_states": int(sum(len(m) for m in Ms)),
        "n_states_dropped_nonfinite": n_states_dropped,
        "bar_minutes": bm,
    }
    for tau in taus:
        # OOS predictions per trade, fold by fold (purged training)
        Qh = [None] * len(trades)
        Q0 = [None] * len(trades)
        for f in range(folds):
            lo, hi = edges[f], edges[f + 1]
            te = np.where(fid == f)[0]
            tr = np.array([i for i in range(len(trades)) if fid[i] != f and not (entry[i] < hi and last[i] >= lo) and len(Ms[i])])
            if len(te) == 0 or len(tr) == 0:
                continue
            Xtr = np.vstack([XL[i] for i in tr])
            ytr = np.concatenate([Ms[i] for i in tr])
            model = qfit(Xtr, ytr, tau)
            orx = np.concatenate([Xs[i][:, 0] for i in tr])
            lgtr = np.concatenate([[trades[i]["leg"]] * len(Ms[i]) for i in tr])
            m0 = q0_fit(orx, ytr, lgtr, tau)
            for i in te:
                if len(Ms[i]):
                    Qh[i] = qpredict(model, XL[i])
                    Q0[i] = q0_predict(m0, Xs[i][:, 0], np.array([trades[i]["leg"]] * len(Ms[i])))
        # clause 1: trade-weighted pinball per fold + pooled coverage
        per_fold, cov_num, cov_den = [], 0.0, 0
        for f in range(folds):
            idx = [i for i in np.where(fid == f)[0] if Qh[i] is not None]
            if not idx:
                per_fold.append({"fold": f, "n_trades": 0, "gain": None})
                continue
            lq = np.mean([pinball(Ms[i], Qh[i], tau).mean() for i in idx])
            l0 = np.mean([pinball(Ms[i], Q0[i], tau).mean() for i in idx])
            per_fold.append(
                {
                    "fold": f,
                    "n_trades": len(idx),
                    "loss_q": round(float(lq), 5),
                    "loss_q0": round(float(l0), 5),
                    "gain": round(float(l0 - lq), 5),
                    "q_better": bool(lq < l0),
                }
            )
            cov_num += sum(float((Ms[i] <= Qh[i]).mean()) for i in idx)
            cov_den += len(idx)
        coverage = cov_num / cov_den if cov_den else None
        better = sum(1 for p in per_fold if p.get("q_better"))
        c1 = bool(better >= clause1_folds and coverage is not None and abs(coverage - tau) <= cov_tol)

        # clause 2: replay as traded vs pull-in-only (head) and vs pull-in-only (Q0 rule, informational)
        def run_policy(Qs: list[Any]) -> dict[str, Any]:
            rows = []
            for i, t in enumerate(trades):
                if Qs[i] is None:
                    continue
                okmask = trade_states(t)[2]
                Qfull = np.zeros(t["n"] - 1)
                Qfull[okmask] = Qs[i]
                r = replay(t, Qfull, okmask, atr_buffer)
                cost = cost_by_leg[t["leg"]]
                net_a = float(t["info"]["net_r"])
                net_p = net_r_for(t, r["exit_idx"], r["exit_r"], cost) if r["filled"] else net_a
                bars_a, bars_p = float(t["offset"][-1]), float(t["offset"][r["exit_idx"]])

                def d(b: float, t: dict[str, Any] = t) -> float:
                    return ca.days_from_bars(b, bm[t["leg"]]) or 0.0

                rows.append(
                    {
                        "fold": int(fid[i]),
                        "net_a": net_a,
                        "net_p": net_p,
                        "days_a": d(bars_a),
                        "days_p": d(bars_p),
                        "pulled": r["pulled"],
                        "filled": r["filled"],
                        "leg": t["leg"],
                        "exit_a": t["last_ts"],
                        "exit_p": np.datetime64(t["ts"][r["exit_idx"]][:19]),
                    }
                )
            return rows

        rows = run_policy(Qh)
        rows0 = run_policy(Q0)
        out["per_tau"][str(tau)] = {
            "tau": tau,
            "clause1": {"pass": c1, "coverage": coverage, "per_fold": per_fold, "folds_q_better": better},
            **policy_stats(rows, folds, clause2_folds, min_diff, ci_lb, dd_mult, bootstrap, rng),
            "q0_rule_policy": policy_stats(rows0, folds, clause2_folds, min_diff, ci_lb, dd_mult, 0, rng),
        }
        pt = out["per_tau"][str(tau)]
        n = len(rows)
        fill_share = pt["share_filled"]
        if n < min_trades or fill_share is None or fill_share < min_fill_share:
            v, why = (
                "indeterminate",
                (f"{n} trades graded (< {min_trades}) or pulled-in TP filled on {fill_share} of trades (< {min_fill_share})"),
            )
        elif c1 and pt["clause2"]["pass"]:
            v, why = "pass", "clause 1 and clause 2 both hold"
        elif not c1:
            v, why = (
                "fail",
                (
                    "clause 1 failed: remaining move not predictable beyond Q0"
                    + ("; clause 2 held (P&L WITHOUT PREDICTION is a FAIL by the TP doctrine)" if pt["clause2"]["pass"] else "")
                ),
            )
        else:
            v, why = "fail", "clause 1 held, clause 2 failed: calibrated but pulling the TP in does not pay"
        pt["verdict"], pt["why"] = v, why
    vs = [p["verdict"] for p in out["per_tau"].values()]
    out["verdict"] = "pass" if "pass" in vs else ("indeterminate" if "indeterminate" in vs else "fail")
    return out


def policy_stats(
    rows: list[dict[str, Any]],
    folds: int,
    clause2_folds: int,
    min_diff: float,
    ci_lb: float,
    dd_mult: float,
    bootstrap: int,
    rng: Any,
) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "share_pulled": None, "share_filled": None, "clause2": {"pass": False}}
    na = np.array([r["net_a"] for r in rows])
    npl = np.array([r["net_p"] for r in rows])
    da = np.array([r["days_a"] for r in rows])
    dp = np.array([r["days_p"] for r in rows])
    fo = np.array([r["fold"] for r in rows])
    per_fold, wins = [], 0
    for f in range(folds):
        m = fo == f
        if not m.any() or da[m].sum() <= 0 or dp[m].sum() <= 0:
            per_fold.append({"fold": f, "n": int(m.sum()), "rate_traded": None, "rate_policy": None})
            continue
        ra, rp = na[m].sum() / da[m].sum(), npl[m].sum() / dp[m].sum()
        wins += int(rp > ra)
        per_fold.append(
            {
                "fold": f,
                "n": int(m.sum()),
                "rate_traded": round(float(ra), 4),
                "rate_policy": round(float(rp), 4),
                "policy_better": bool(rp > ra),
            }
        )
    diff = npl - na
    ci = None
    if bootstrap:
        bs = np.array([diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(bootstrap)])
        ci = [round(float(np.quantile(bs, 0.025)), 4), round(float(np.quantile(bs, 0.975)), 4)]
    dd_a = max_dd(na, np.array([r["exit_a"] for r in rows]))
    dd_p = max_dd(npl, np.array([r["exit_p"] for r in rows]))
    dd_ok = dd_p <= dd_mult * dd_a if dd_a > 0 else dd_p <= 0
    mean_diff = float(diff.mean())
    c2 = bool(wins >= clause2_folds and mean_diff >= min_diff and ci is not None and ci[0] >= ci_lb and dd_ok)
    return {
        "n": len(rows),
        "share_pulled": round(float(np.mean([r["pulled"] for r in rows])), 4),
        "share_filled": round(float(np.mean([r["filled"] for r in rows])), 4),
        "net_r_traded": round(float(na.sum()), 3),
        "net_r_policy": round(float(npl.sum()), 3),
        "capital_days_traded": round(float(da.sum()), 2),
        "capital_days_policy": round(float(dp.sum()), 2),
        "net_r_per_capital_day_traded": round(float(na.sum() / da.sum()), 4) if da.sum() > 0 else None,
        "net_r_per_capital_day_policy": round(float(npl.sum() / dp.sum()), 4) if dp.sum() > 0 else None,
        "mean_diff_net_r": round(mean_diff, 4),
        "diff_ci95": ci,
        "max_dd_traded": round(dd_a, 3),
        "max_dd_policy": round(dd_p, 3),
        "clause2": {"pass": c2, "folds_policy_better": wins, "per_fold": per_fold, "dd_ok": bool(dd_ok)},
    }


# ── harness runs ────────────────────────────────────────────────────────────
def load_trade_info(trades_jsonl: Path) -> dict[str, dict[str, Any]]:
    import harness_emit as he

    info = {}
    for ln in trades_jsonl.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        entry, sl = r.get("entry"), r.get("sl")
        if entry is None or sl is None or r.get("net_r") is None:
            continue
        key = he.trade_key(r["strategy"], r["symbol"], r["entry_time"], r["direction"])
        info[key] = {
            "entry": float(entry),
            "risk": abs(float(entry) - float(sl)),
            "is_long": r["direction"] == "long",
            "entry_time": r["entry_time"],
            "net_r": float(r["net_r"]),
        }
    return info


def main(argv: list[str] | None = None) -> int:
    if "--self-test" in (sys.argv[1:] if argv is None else argv):
        return _self_test()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leg", action="append", required=True)
    ap.add_argument("--days", type=int, default=2330)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--data-csv", default=None, help="one candle CSV for every leg (tests)")
    ap.add_argument("--non-crypto-source", choices=("auto", "dukascopy"), default="auto")
    ap.add_argument("--tau", action="append", type=float, required=True)
    ap.add_argument("--folds", type=int, required=True)
    ap.add_argument("--clause1-folds", type=int, required=True)
    ap.add_argument("--clause2-folds", type=int, required=True)
    ap.add_argument("--cov-tol", type=float, required=True)
    ap.add_argument("--atr-buffer", type=float, required=True)
    ap.add_argument("--min-diff", type=float, required=True)
    ap.add_argument("--ci-lb", type=float, required=True)
    ap.add_argument("--dd-mult", type=float, required=True)
    ap.add_argument("--min-trades", type=int, required=True)
    ap.add_argument("--min-fill-share", type=float, required=True)
    ap.add_argument("--bootstrap", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    import yaml
    from path_metric_giveback import run_leg_paths

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    legs_cfg = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text(encoding="utf-8"))
    legs_cfg = legs_cfg.get("strategies", legs_cfg)
    wd = Path(tempfile.mkdtemp(prefix="remaining_move_head_"))
    trades: list[dict[str, Any]] = []
    cost_by_leg: dict[str, dict[str, float]] = {}
    runs: dict[str, Any] = {}
    for leg in a.leg:
        base = legs_cfg.get(leg)
        if not isinstance(base, dict):
            print(f"ERROR: leg {leg!r} not in config/strategies.yaml", file=sys.stderr)
            (out / "failure.json").write_text(json.dumps({"stage": "config", "leg": leg}) + "\n")
            return 2
        try:
            rows, meta = run_leg_paths(
                leg,
                base,
                days=a.days,
                start=a.start,
                end=a.end,
                wd=wd,
                data_csv=a.data_csv,
                non_crypto_source=a.non_crypto_source,
            )
            info = load_trade_info(wd / f"{leg}__trades.jsonl")
        except Exception as exc:  # noqa: BLE001  # allow-silent: not silent -- the cause is printed, written to failure.json and the run exits 1
            print(f"ERROR: {exc}", file=sys.stderr)
            (out / "failure.json").write_text(json.dumps({"stage": "harness", "leg": leg, "error": str(exc)}, indent=2) + "\n")
            return 1
        bt = meta["bt"]
        cost_by_leg[leg] = {
            "fee": float(bt.get("fee_bps_roundtrip") or 0.0),
            "slip": float(bt.get("slippage_bps_roundtrip") or 0.0),
            "fund": float(bt.get("funding_bps_per_window") or 0.0),
        }
        tl = build_trades(rows, info, leg)
        trades += tl
        runs[leg] = {"argv": meta["argv"], "feed": meta["feed"], "n_trades_graded": len(tl), "cost_policy": cost_by_leg[leg]}
    if not trades:
        (out / "failure.json").write_text(json.dumps({"stage": "no_trades"}) + "\n")
        return 1
    g = grade(
        trades,
        cost_by_leg,
        taus=a.tau,
        folds=a.folds,
        clause1_folds=a.clause1_folds,
        clause2_folds=a.clause2_folds,
        cov_tol=a.cov_tol,
        atr_buffer=a.atr_buffer,
        min_diff=a.min_diff,
        ci_lb=a.ci_lb,
        dd_mult=a.dd_mult,
        min_trades=a.min_trades,
        min_fill_share=a.min_fill_share,
        bootstrap=a.bootstrap,
    )
    pop = (
        f"{len(trades)} harness trades across {', '.join(a.leg)}, window {a.start} .. {a.end}, config-exact, net of the "
        "harness's venue-aware cost stack; every bar of every trade (exit bar excluded) is a decision state; "
        f"{g['n_states']} states ({g['n_states_dropped_nonfinite']} dropped non-finite)"
    )
    rec = {
        "verdict": g["verdict"],
        "read_state": "measured",
        "population": pop,
        "n": len(trades),
        "measurement": {**g, "runs": runs},
        "note": "; ".join(f"tau {k}: {v['verdict'].upper()} ({v['why']})" for k, v in g["per_tau"].items()),
    }
    (out / "verdict.json").write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps({"verdict": g["verdict"], "per_tau": {k: (v["verdict"], v["why"]) for k, v in g["per_tau"].items()}}, indent=1)
    )
    return 0


# ── self-test: random-walk null, never-moves-up, planted predictable extent ─
_COST = {"fee": 10.0, "slip": 5.0, "fund": 1.0}


def _synth(n_trades: int, planted: bool, seed: int, hazard: float = 0.08) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Synthetic harness_path_v1 trades (entry 100, risk 2 -> 1 R = 2 price units, hourly bars, 2021..2024).

    planted=False: random-walk closes, memoryless exit hazard, stop/target far away -> M_t is independent of the
    state apart from the zero floor. planted=True: each trade has a hidden extent E ~ U(1.5,4); the path rises to
    E then gives it all back to the -1 stop; adx_14 = 10*(E - open_r) + noise, so the remaining move is linearly
    visible in the state (and Q's calibration is checkable)."""
    rng = np.random.default_rng(seed)
    t0 = np.datetime64("2021-01-01T00:00:00")
    paths, info = [], {}
    for k in range(n_trades):
        entry_ts = t0 + np.timedelta64(int(k * (3 * 365 * 24) / n_trades), "h")
        key = f"synth|SYN|{entry_ts!s}+00:00|long"
        rows, c, peak, peak_j, mae = [], 0.0, 0.0, 0, 0.0
        E = rng.uniform(1.5, 4.0)
        j = 0
        while True:
            j += 1
            if planted:
                step = (0.35 if c < E - 0.35 else (E - c)) if j <= 40 and c < E - 1e-9 and not rows_peaked(rows, E) else -0.3
                step += rng.normal(0, 0.05)
            else:
                step = rng.normal(0, 0.25)
            nc = c + step
            fav, adv = max(c, nc) + abs(rng.normal(0, 0.03)), min(c, nc) - abs(rng.normal(0, 0.03))
            stop = -1.0 if planted else -50.0
            hit_stop = planted and adv <= stop
            end = hit_stop or (not planted and rng.random() < hazard) or j >= 400
            if hit_stop:
                nc, adv = stop, stop
            if fav > peak:
                peak, peak_j = fav, j
            mae = max(mae, -min(adv, 0.0))
            adx = (10.0 * (E - nc) + rng.normal(0, 3.0)) if planted else 20.0 + rng.normal(0, 3.0)
            rows.append(
                {
                    "trade_key": key,
                    "strategy": "synth",
                    "symbol": "SYN",
                    "bar_index": j,
                    "bar_offset": j,
                    "ts": str(entry_ts + np.timedelta64(j, "h")),
                    "o_r": c,
                    "c_r": nc,
                    "fav_r": fav,
                    "adv_r": adv,
                    "stop_r": stop,
                    "target_r": None,
                    "dist_to_stop_r": nc - stop,
                    "dist_to_target_r": None,
                    "atr_r": 0.5,
                    "adx_14": adx,
                    "open_r": nc,
                    "peak_r": peak,
                    "mae_r": mae,
                    "bars_since_peak": j - peak_j,
                    "is_exit": bool(end),
                    **({"exit_reason": "stop" if hit_stop else "timeout"} if end else {}),
                }
            )
            c = nc
            if end:
                break
        if len(rows) < 3:
            continue
        paths += rows
        from src.runtime import execution_costs as ec

        cb = ec.roundtrip_cost_r(
            entry=100.0,
            exit_price=100.0 + 2.0 * c,
            risk=2.0,
            entry_time=str(entry_ts) + "+00:00",
            exit_time=rows[-1]["ts"],
            fee_bps_roundtrip=_COST["fee"],
            slippage_bps_roundtrip=_COST["slip"],
            funding_bps_per_window=_COST["fund"],
        )
        info[key] = {
            "entry": 100.0,
            "risk": 2.0,
            "is_long": True,
            "entry_time": str(entry_ts) + "+00:00",
            "net_r": c - cb["total_cost_r"],
        }
    return paths, info


def rows_peaked(rows: list[dict[str, Any]], E: float) -> bool:
    return bool(rows) and rows[-1]["peak_r"] >= E - 0.2


GRADE_KW = {
    "taus": [0.5, 0.7],
    "folds": 6,
    "clause1_folds": 4,
    "clause2_folds": 5,
    "cov_tol": 0.05,
    "atr_buffer": 0.25,
    "min_diff": -0.02,
    "ci_lb": -0.05,
    "dd_mult": 1.35,
    "min_trades": 600,
    "min_fill_share": 0.05,
    "bootstrap": 300,
}


def _synth_grade(planted: bool, n: int, seed: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paths, info = _synth(n, planted, seed)
    trades = build_trades(paths, info, "SYN")
    return grade(trades, {"SYN": _COST}, **GRADE_KW), trades


def _self_test() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"{'ok  ' if cond else 'FAIL'} {name} {detail}")
        ok &= bool(cond)

    # 1. random walk: no calibration gain, verdict not PASS
    g, trades = _synth_grade(False, 900, 5)
    for tau, v in g["per_tau"].items():
        rel = float(np.mean([p["gain"] / p["loss_q0"] for p in v["clause1"]["per_fold"] if p.get("gain") is not None]))
        check(f"random-walk tau={tau}: no calibration gain (mean rel gain {rel:.4f} < 0.01)", rel < 0.01)
        check(f"random-walk tau={tau}: not a PASS", v["verdict"] != "pass", v["verdict"])
    check("random-walk family verdict is not pass", g["verdict"] != "pass", g["verdict"])
    # 2. a TP never moves up -- even for an adversarial head whose Q rises every bar
    t = trades[0]
    n1 = t["n"] - 1
    for name, Q in (
        ("rising", np.linspace(0.0, 5.0, n1)),
        ("falling", np.linspace(5.0, 0.0, n1)),
        ("random", np.random.default_rng(1).uniform(0, 3, n1)),
    ):
        worst = True
        for tr in trades[:200]:
            q = np.resize(Q, tr["n"] - 1)
            tp_path = replay(tr, q, np.ones(tr["n"] - 1, dtype=bool), 0.25)["tp_path"]
            worst &= all(b <= a + 1e-12 for a, b in itertools.pairwise(tp_path))
        check(f"TP never moves up ({name} head, 200 trades)", worst)
    # 3. planted predictable-extent PASSES
    gp, _ = _synth_grade(True, 900, 7)
    for tau, v in gp["per_tau"].items():
        check(
            f"planted tau={tau}: clause 1 holds (q better in {v['clause1']['folds_q_better']}/6 folds, coverage "
            f"{v['clause1']['coverage']:.3f})",
            v["clause1"]["pass"],
        )
    check(
        "planted family verdict is PASS",
        gp["verdict"] == "pass",
        json.dumps({k: (v["verdict"], v["why"]) for k, v in gp["per_tau"].items()}),
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
