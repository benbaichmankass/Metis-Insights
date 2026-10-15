#!/usr/bin/env python3
# wiring: research/queue units declaring run.command ["python3","scripts/research/ml_bakeoff.py",...]
#         via .github/workflows/research-script-run.yml (runner) or research-script-run-trainer.yml (trainer VM)
"""Generic ML bake-off: a LEARNER LIST on ONE dataset with ONE metric against a BASELINE,
graded by the unit's REGISTERED decision rule — the ML twin of a strategy harness unit.

WHY (ML-RQ-RUNNER, 2026-10-11)
------------------------------
Strategy units flow dispatcher -> runner -> result record -> research-ops applies the
pre-registered rule. ML tests had no such path: every ML unit was a prose note ("trainer-vm-diag
issue") that only a session could run. This engine is the missing instrument, and it rides the
EXISTING plumbing: the unit declares ``run.command`` (this script), ``script_run.py`` runs it
and ``verdict.json`` becomes the E5 result record the collector lands and research-ops reads.
Nothing here is a parallel pipeline.

THE UNIT BLOCK (``ml_bakeoff:`` in the unit YAML — registered BEFORE the run, with the rule)
--------------------------------------------------------------------------------------------
::

    ml_bakeoff:
      task: classification            # classification | regression
      metric: macro_f1                # macro_f1 | f1_class:<label> | accuracy | logloss | brier | auc_ovr | mae | rmse
      higher_is_better: true          # default true for f1/accuracy/auc, false for logloss/brier/mae/rmse
      dataset:
        kind: parquet|csv|jsonl|synthetic   # path may be a glob; relative paths resolve from $ML_DATA_ROOT, then the repo
                                    # (trainer shards: datasets-out/market_features/<SYM>/<tf>/<vNNN>/data.jsonl; tail_rows: N keeps the last N)
        path: ...                     # or per-group: groups: {BTCUSDT: {path: ...}, ETHUSDT: {path: ...}}
        ts_col: ts
        target: regime_label
        features: [log_return, ...]
        group_col: symbol             # optional (single-frame multi-group)
        holdout_after: '2025-09-01'   # optional extra window ("holdout") graded by the same rule
      cv: {folds: 5, embargo_rows: 96, min_train_frac: 0.4}    # purged expanding walk-forward
      baseline: incumbent             # name in learners
      controls: [har_rv_control]      # names in learners (reported + usable as `vs` in the rule)
      candidates: [chronos2, ...]     # names in learners (the arms under test)
      learners:
        incumbent: {kind: lightgbm, params: {...}, feature_blocks: [...]}
        ...
      rule: {...}                     # machine form of decision_rule.rule — see ``evaluate_rule``

A learner arm whose library/weights are unavailable is recorded ``status: unavailable`` with the
reason; it is NEVER silently dropped and NEVER scored. A FAIL requires every declared candidate to
have been evaluated (a FAIL closes only what was tested); otherwise the verdict is INDETERMINATE.
Licence/research-only flags ride through to the result (TabPFN weights are non-commercial).

Tier-1 research tooling. Reads a dataset, writes only ``--out``. No config, no order path.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

_REPO = Path(__file__).resolve().parents[2]

HIGHER_BETTER = {"macro_f1": True, "accuracy": True, "auc_ovr": True, "logloss": False,
                 "brier": False, "mae": False, "rmse": False}

# two-sided 95% t critical values, df 1..30 (beyond: 1.96) — no scipy on a bare runner
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
        2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
        2.045, 2.042]


# ── metrics (numpy only) ─────────────────────────────────────────────────────
def _f1_per_class(y: np.ndarray, p: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros(k)
    for c in range(k):
        tp = float(np.sum((y == c) & (p == c)))
        fp = float(np.sum((y != c) & (p == c)))
        fn = float(np.sum((y == c) & (p != c)))
        out[c] = 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)
    return out


def _auc_binary(y: np.ndarray, s: np.ndarray) -> float:
    """Mann-Whitney AUC with average ranks for ties."""
    npos, nneg = int((y == 1).sum()), int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    sorted_s = s[order]
    ranks = np.empty(len(s))
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def compute_metric(name: str, y: np.ndarray, out: np.ndarray, classes: List[Any]) -> float:
    """``out`` is a proba matrix (n, C) for classification, a vector for regression."""
    if name in ("mae", "rmse"):
        e = out.astype(float) - y.astype(float)
        return float(np.mean(np.abs(e))) if name == "mae" else float(math.sqrt(np.mean(e * e)))
    k = len(classes)
    yi = y.astype(int)
    pred = np.argmax(out, axis=1)
    if name == "macro_f1":
        present = np.array([np.any(yi == c) or np.any(pred == c) for c in range(k)])
        return float(_f1_per_class(yi, pred, k)[present].mean()) if present.any() else float("nan")
    if name.startswith("f1_class:"):
        label = name.split(":", 1)[1]
        idx = [str(c) for c in classes].index(label)
        return float(_f1_per_class(yi, pred, k)[idx])
    if name == "accuracy":
        return float(np.mean(pred == yi))
    if name == "logloss":
        pr = np.clip(out[np.arange(len(yi)), yi], 1e-12, 1.0)
        return float(-np.mean(np.log(pr)))
    if name == "brier":
        oh = np.eye(k)[yi]
        return float(np.mean(np.sum((out - oh) ** 2, axis=1)))
    if name == "auc_ovr":
        aucs = [_auc_binary((yi == c).astype(int), out[:, c]) for c in range(k)]
        aucs = [a for a in aucs if not math.isnan(a)]
        return float(np.mean(aucs)) if aucs else float("nan")
    raise ValueError(f"unknown metric {name!r}")


def metric_higher_is_better(name: str, declared: Optional[bool]) -> bool:
    if declared is not None:
        return bool(declared)
    return HIGHER_BETTER.get(name, True) if not name.startswith("f1_class:") else True


# ── feature blocks ───────────────────────────────────────────────────────────
def _roll_mean(x: np.ndarray, w: int) -> np.ndarray:
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def block_har_rv(df, spec: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Causal HAR-RV regressors (Corsi 2009): realised-vol proxies at day/week/month horizons from the
    return column. The learner combines them — this is the NO-NEURAL CONTROL, not a fitted forecast."""
    col = spec.get("return_col", "log_return")
    r2 = np.asarray(df[col], dtype=float) ** 2
    d, w, m = (int(spec.get(k, v)) for k, v in (("d", 1), ("w", 5), ("m", 22)))
    sc = int(spec.get("bars_per_unit", 1))
    return {"har_rv_d": _roll_mean(r2, d * sc), "har_rv_w": _roll_mean(r2, w * sc), "har_rv_m": _roll_mean(r2, m * sc)}


def block_join_columns(df, spec: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Join a precomputed side-stream (e.g. fc_* from build_forecasts.py for another backend) on ts."""
    import pandas as pd
    path = _resolve_path(str(spec["path"]))
    side = pd.read_parquet(path) if str(path).endswith(".parquet") else pd.read_csv(path)
    on = spec.get("on", "ts")
    side[on] = pd.to_datetime(side[on], utc=True)
    left = pd.DataFrame({on: pd.to_datetime(df[on], utc=True)})
    merged = left.merge(side[[on] + list(spec["columns"])], on=on, how="left")
    pre = spec.get("prefix", "")
    return {pre + c: merged[c].to_numpy(dtype=float) for c in spec["columns"]}


def block_df_columns(df, spec: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Give ONE learner extra columns that ride in the dataset itself (declare them in ``dataset.extra_columns`` so
    the loader reads them but the shared base feature list does not include them) -- e.g. the shard's own ``fc_*``
    Chronos columns for the arm that tests them, against an arm that does not get them. A column missing from the
    shard is NaN (the loader's rule), so a dead side-stream shows up as a dead feature, never as a silent drop."""
    return {c: np.asarray(df[c], dtype=float) for c in spec["columns"]}


FEATURE_BLOCKS: Dict[str, Callable[..., Dict[str, np.ndarray]]] = {
    "har_rv": block_har_rv, "join_columns": block_join_columns, "df_columns": block_df_columns}


# ── learners ─────────────────────────────────────────────────────────────────
class Unavailable(Exception):
    pass


def _fit_prior(Xtr, ytr, Xte, k, params):  # inert: Xtr, params — the class prior ignores features and has no parameters (shared learner signature)
    freq = np.bincount(ytr.astype(int), minlength=k).astype(float)
    freq = freq / max(freq.sum(), 1.0)
    return np.tile(freq, (len(Xte), 1))


def _fit_numpy_logit(Xtr, ytr, Xte, k, params):
    """Multinomial ridge-logit by gradient descent — dependency-free, so the engine runs on a bare runner."""
    mu, sd = np.nanmean(Xtr, axis=0), np.nanstd(Xtr, axis=0) + 1e-9
    A = np.nan_to_num((Xtr - mu) / sd)
    B = np.nan_to_num((Xte - mu) / sd)
    A = np.hstack([A, np.ones((len(A), 1))])
    B = np.hstack([B, np.ones((len(B), 1))])
    Y = np.eye(k)[ytr.astype(int)]
    W = np.zeros((A.shape[1], k))
    lr, l2 = float(params.get("lr", 0.5)), float(params.get("l2", 1e-3))
    for _ in range(int(params.get("iters", 300))):
        Z = A @ W
        Z -= Z.max(axis=1, keepdims=True)
        P = np.exp(Z)
        P /= P.sum(axis=1, keepdims=True)
        W -= lr * (A.T @ (P - Y) / len(A) + l2 * W)
    Z = B @ W
    Z -= Z.max(axis=1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(axis=1, keepdims=True)


def _pad_proba(P: np.ndarray, present: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros((len(P), k))
    out[:, present] = P
    return out


def _fit_sklearn_like(make: Callable[[Dict[str, Any]], Any]):
    def fit(Xtr, ytr, Xte, k, params):
        m = make(params)
        m.fit(Xtr, ytr.astype(int))
        present = np.array(sorted(set(ytr.astype(int))))
        return _pad_proba(m.predict_proba(Xte), present, k)
    return fit


def _mk_lightgbm(p):
    try:
        import lightgbm as lgb
    except ImportError as e:
        raise Unavailable(f"lightgbm not importable: {e}")
    return lgb.LGBMClassifier(**{"verbose": -1, "n_estimators": 200, "learning_rate": 0.05, "num_leaves": 31, **p})


def _mk_catboost(p):
    try:
        from catboost import CatBoostClassifier
    except ImportError as e:
        raise Unavailable(f"catboost not importable: {e}")
    # defaults first, unit params override: a unit declaring verbose must not raise "multiple values for keyword argument"
    # (RQ-20261014-4591-04 landed catboost as unavailable for exactly that, 2026-10-14)
    return CatBoostClassifier(**{"verbose": 0, "iterations": 300, **p})


def _mk_hgb(p):
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as e:
        raise Unavailable(f"scikit-learn not importable: {e}")
    return HistGradientBoostingClassifier(**p)


def _mk_extratrees(p):
    """Bagged randomised trees (variance reduction, not boosting) -- a different inductive bias from the GBDT family."""
    try:
        from sklearn.ensemble import ExtraTreesClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import make_pipeline
    except ImportError as e:
        raise Unavailable(f"scikit-learn not importable: {e}")
    return make_pipeline(SimpleImputer(strategy="median"),
                         ExtraTreesClassifier(**{"n_estimators": 300, "min_samples_leaf": 20, "n_jobs": 1,
                                                 "random_state": 0, **p}))


def _mk_mlp(p):
    """Small standardised MLP (the neural tabular family; CPU, seeded, early-stopped)."""
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as e:
        raise Unavailable(f"scikit-learn not importable: {e}")
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         MLPClassifier(**{"hidden_layer_sizes": (32, 16), "alpha": 1e-3, "max_iter": 200,
                                          "early_stopping": True, "random_state": 0, **p}))


def _mk_tabicl(p):
    try:
        from tabicl import TabICLClassifier
    except ImportError as e:
        raise Unavailable(f"tabicl not importable: {e}")
    return TabICLClassifier(**p)


def _mk_tabpfn(p):
    try:
        from tabpfn import TabPFNClassifier
    except ImportError as e:
        raise Unavailable(f"tabpfn not importable: {e}")
    return TabPFNClassifier(**p)


# kind -> (fit_predict, licence, research_only)
LEARNERS: Dict[str, Tuple[Callable, str, bool]] = {
    "prior": (_fit_prior, "n/a", False),
    "numpy_logit": (_fit_numpy_logit, "n/a", False),
    "lightgbm": (_fit_sklearn_like(_mk_lightgbm), "MIT", False),
    "catboost": (_fit_sklearn_like(_mk_catboost), "Apache-2.0", False),
    "sklearn_hgb": (_fit_sklearn_like(_mk_hgb), "BSD-3-Clause", False),
    "sklearn_extratrees": (_fit_sklearn_like(_mk_extratrees), "BSD-3-Clause", False),
    "sklearn_mlp": (_fit_sklearn_like(_mk_mlp), "BSD-3-Clause", False),
    "tabicl": (_fit_sklearn_like(_mk_tabicl), "BSD-3-Clause (verify on the Hub)", False),
    # Prior Labs weights are `license:other` (non-commercial): offline research evaluation ONLY.
    "tabpfn": (_fit_sklearn_like(_mk_tabpfn), "Prior Labs non-commercial (license:other)", True),
}


def probe_learner(kind: str, params: Dict[str, Any]) -> Optional[str]:
    """None when the arm can run; else the reason it cannot (an import check — no data touched)."""
    if kind not in LEARNERS:
        return f"unknown learner kind {kind!r} (known: {sorted(LEARNERS)})"
    try:
        probe = {"lightgbm": _mk_lightgbm, "catboost": _mk_catboost, "sklearn_hgb": _mk_hgb,
                 "sklearn_extratrees": _mk_extratrees, "sklearn_mlp": _mk_mlp, "tabicl": _mk_tabicl, "tabpfn": _mk_tabpfn}.get(kind)
        if probe:
            probe(dict(params or {}))
    except Unavailable as e:
        return str(e)
    except Exception as e:  # allow-silent: returned as the arm's `unavailable` reason in the result, never dropped
        return f"{type(e).__name__}: {e}"
    return None


# ── datasets ─────────────────────────────────────────────────────────────────
def _resolve_path(p: str) -> Path:
    cand = [Path(p)]
    root = os.environ.get("ML_DATA_ROOT")
    if root:
        cand.insert(0, Path(root) / p)
    cand.append(_REPO / p)
    for c in cand:
        hits = sorted(glob.glob(str(c)))
        if hits:
            # A glob resolves to the alphabetically-last match (newest version dir). Say which, every time, and
            # record it: an implicit input is how a bake-off silently grades a different dataset than intended.
            print(f"ml_bakeoff: dataset {p!r} -> {hits[-1]} (1 of {len(hits)} match(es))", file=sys.stderr)
            return Path(hits[-1])
    raise FileNotFoundError(f"dataset path {p!r} not found (tried {[str(c) for c in cand]})")


def synthetic_frame(n: int, seed: int, signal: float, n_classes: int = 3):
    import pandas as pd
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 4))
    latent = signal * (x[:, 0] * x[:, 1] + 0.5 * np.abs(x[:, 2])) + rng.normal(size=n)
    qs = np.quantile(latent, np.linspace(0, 1, n_classes + 1)[1:-1])
    y = np.digitize(latent, qs)
    ts = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts": ts, "x0": x[:, 0], "x1": x[:, 1], "x2": x[:, 2], "x3": x[:, 3],
                         "log_return": rng.normal(scale=0.01, size=n), "label": y})


def load_groups(ds: Dict[str, Any]) -> Dict[str, Any]:
    import pandas as pd
    kind = ds.get("kind", "parquet")
    out: Dict[str, Any] = {}
    if kind == "synthetic":
        for i, g in enumerate(ds.get("groups") or ["G0"]):
            out[str(g)] = synthetic_frame(int(ds.get("n", 3000)), int(ds.get("seed", 7)) + i,
                                          float(ds.get("signal", 1.5)))
        return out
    want = [c for c in dict.fromkeys([ds.get("ts_col", "ts"), ds["target"], *list(ds["features"]),
                                      *([ds["group_col"]] if ds.get("group_col") else []),
                                      *list(ds.get("extra_columns") or [])])]

    def read(path: str):
        p = _resolve_path(path)
        sp = str(p)
        if sp.endswith(".parquet"):
            return pd.read_parquet(p, columns=[c for c in want if c])
        if sp.endswith((".jsonl", ".json")):
            # Trainer dataset shards are data.jsonl (BTC 15m v520 = 480 MB): stream line by line and keep ONLY the
            # declared columns so a 6 GB box never holds the full width. A column missing from a row is NaN, never
            # silently dropped from the frame (a declared-but-absent feature is the 'dead column' bug class).
            rows = []
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    rows.append({c: r.get(c) for c in want})
            tail = int(ds.get("tail_rows") or 0)
            return pd.DataFrame(rows[-tail:] if tail else rows)
        return pd.read_csv(p, usecols=lambda c: c in set(want))
    if isinstance(ds.get("groups"), dict):
        for g, gs in ds["groups"].items():
            out[str(g)] = read(gs["path"])
    else:
        df = read(ds["path"])
        gc = ds.get("group_col")
        if gc:
            for g, sub in df.groupby(gc):
                out[str(g)] = sub.reset_index(drop=True)
        else:
            out["ALL"] = df
    return out


# ── cross-validation ─────────────────────────────────────────────────────────
def purged_folds(n: int, folds: int, embargo: int, min_train_frac: float) -> List[Tuple[np.ndarray, np.ndarray]]:
    start = int(n * min_train_frac)
    edges = np.linspace(start, n, folds + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        tr_end = a - embargo
        if tr_end < max(50, int(0.1 * n)) or b - a < 20:
            continue
        out.append((np.arange(0, tr_end), np.arange(a, b)))
    return out


def run_group(df, spec: Dict[str, Any], learner_specs: Dict[str, Dict[str, Any]], metric: str,
              status: Dict[str, Optional[str]]) -> Dict[str, Any]:
    ds, cv = spec["dataset"], spec.get("cv") or {}
    ts_col, target = ds.get("ts_col", "ts"), ds["target"]
    df = df.dropna(subset=[target]).sort_values(ts_col).reset_index(drop=True)
    if spec.get("task", "classification") == "classification":
        classes = sorted(df[target].dropna().unique().tolist(), key=str)
        cmap = {c: i for i, c in enumerate(classes)}
        y_all = df[target].map(cmap).to_numpy()
    else:
        classes = []
        y_all = df[target].to_numpy(dtype=float)
    k = len(classes)
    base_cols = list(ds["features"])
    windows: Dict[str, List[Tuple[np.ndarray, np.ndarray]]] = {}
    windows["cv"] = purged_folds(len(df), int(cv.get("folds", 5)), int(cv.get("embargo_rows", 0)),
                                 float(cv.get("min_train_frac", 0.4)))
    if ds.get("holdout_after"):
        import pandas as pd
        cut = pd.Timestamp(ds["holdout_after"], tz="UTC")
        t = pd.to_datetime(df[ts_col], utc=True)
        te = np.where(t >= cut)[0]
        tr = np.where(t < cut)[0]
        tr = tr[: max(0, len(tr) - int(cv.get("embargo_rows", 0)))]
        windows["holdout"] = [(tr, te)] if len(tr) >= 50 and len(te) >= 20 else []
    res: Dict[str, Any] = {"n_rows": int(len(df)), "classes": [str(c) for c in classes], "arms": {}}
    for name, ls in learner_specs.items():
        why = status.get(name)
        if why:
            res["arms"][name] = {"status": "unavailable", "reason": why}
            continue
        cols = dict((c, df[c].to_numpy(dtype=float)) for c in base_cols)
        for blk in ls.get("feature_blocks") or []:
            cols.update(FEATURE_BLOCKS[blk["kind"]](df, blk))
        names = list(cols)
        X = np.column_stack([cols[c] for c in names]) if names else np.zeros((len(df), 0))
        fit = LEARNERS[ls["kind"]][0]
        arm: Dict[str, Any] = {"status": "ok", "features": names, "windows": {}}
        try:
            for wname, wf in windows.items():
                vals, ntest = [], 0
                for tr, te in wf:
                    out = fit(X[tr], y_all[tr], X[te], k, dict(ls.get("params") or {}))
                    vals.append(compute_metric(metric, y_all[te], out, classes))
                    ntest += len(te)
                arm["windows"][wname] = {"fold_metric": vals, "n_test_rows": ntest}
        except Unavailable as e:
            arm = {"status": "unavailable", "reason": str(e)}
        except Exception as e:  # allow-silent: one arm breaking is contained and recorded as status=error + reason in the result
            arm = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
        res["arms"][name] = arm
    return res


# ── statistics + rule ────────────────────────────────────────────────────────
def paired_delta(a: List[float], b: List[float]) -> Dict[str, Any]:
    d = np.array([x - y for x, y in zip(a, b)], dtype=float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n == 0:
        return {"n_folds": 0, "mean": None, "ci95": None}
    mean = float(d.mean())
    if n < 2:
        return {"n_folds": n, "mean": mean, "ci95": None, "note": "single fold: no interval"}
    se = float(d.std(ddof=1) / math.sqrt(n))
    t = _T95[n - 2] if n - 1 <= len(_T95) else 1.96
    return {"n_folds": n, "mean": mean, "ci95": [mean - t * se, mean + t * se],
            "sign_consistent": bool(np.all(d > 0) or np.all(d < 0))}


def build_deltas(groups: Dict[str, Any], names: List[str], refs: List[str], windows: List[str],
                 better_high: bool) -> Dict[str, Any]:
    """deltas[group][window][learner][ref] — signed so that POSITIVE always means 'learner is better'."""
    out: Dict[str, Any] = {}
    sign = 1.0 if better_high else -1.0
    for g, gr in groups.items():
        for w in windows:
            for lname in names:
                for r in refs:
                    la, ra = gr["arms"].get(lname, {}), gr["arms"].get(r, {})
                    lv = (la.get("windows") or {}).get(w, {}).get("fold_metric")
                    rv = (ra.get("windows") or {}).get(w, {}).get("fold_metric")
                    if lv is None or rv is None or len(lv) != len(rv):
                        continue
                    d = paired_delta([sign * x for x in lv], [sign * x for x in rv])
                    out.setdefault(g, {}).setdefault(w, {}).setdefault(lname, {})[r] = d
    return out


_OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}


def _clause_holds(cl: Dict[str, Any], cand: str, deltas: Dict[str, Any], baseline: str) -> Tuple[bool, str]:
    ref = baseline if cl["vs"] == "baseline" else cl["vs"]
    win = cl.get("window", "cv")
    groups = cl.get("group", "any")
    glist = [groups] if isinstance(groups, str) and groups != "any" else (groups if isinstance(groups, list) else None)

    def one(g: str) -> Optional[bool]:
        d = deltas.get(g, {}).get(win, {}).get(cand, {}).get(ref)
        if not d or d.get("mean") is None:
            return None
        return _OPS[cl["op"]](d["mean"], float(cl["value"]))
    if glist is None:   # any group
        vals = [one(g) for g in deltas]
        return any(v is True for v in vals), f"any group {cl['op']} {cl['value']} vs {ref}/{win}"
    vals = [one(g) for g in glist]
    return (all(v is True for v in vals) and len(vals) > 0), f"{glist} {cl['op']} {cl['value']} vs {ref}/{win}"


def _clause_evaluable(cl: Dict[str, Any], cand: str, deltas: Dict[str, Any], baseline: str) -> bool:
    """True when every group the clause names has a measured delta (mean not None) in its window."""
    ref = baseline if cl["vs"] == "baseline" else cl["vs"]
    win = cl.get("window", "cv")
    groups = cl.get("group", "any")
    glist = list(deltas) if groups == "any" else ([groups] if isinstance(groups, str) else list(groups or []))
    return bool(glist) and all((deltas.get(g, {}).get(win, {}).get(cand, {}).get(ref) or {}).get("mean") is not None
                               for g in glist)


def evaluate_rule(rule: Dict[str, Any], deltas: Dict[str, Any], candidates: List[str], baseline: str,
                  arm_status: Dict[str, str], min_folds: int, folds_seen: int) -> Dict[str, Any]:
    """Apply the REGISTERED machine rule. PASS: some candidate meets ALL ``pass_if`` clauses.
    FAIL: ``fail_if_no_candidate_reaches`` is declared AND no candidate meets ANY of its clauses AND every
    candidate was evaluated. Otherwise INDETERMINATE. Too few folds is INDETERMINATE regardless."""
    detail: Dict[str, Any] = {"candidates": {}}
    if folds_seen < min_folds:
        return {"verdict": "indeterminate", "why": f"only {folds_seen} usable folds < min_folds {min_folds}", **detail}
    passed = []
    for c in candidates:
        if arm_status.get(c) != "ok":
            detail["candidates"][c] = {"evaluated": False, "status": arm_status.get(c)}
            continue
        checks = [(_clause_holds(cl, c, deltas, baseline), cl) for cl in rule.get("pass_if", [])]
        ok = bool(checks) and all(r[0] for r, _ in checks)
        detail["candidates"][c] = {"evaluated": True, "passes_all": ok,
                                   "clauses": [{"holds": r[0], "what": r[1]} for r, _ in checks]}
        if ok:
            passed.append(c)
    if passed:
        return {"verdict": "pass", "why": f"candidate(s) {passed} meet every pass_if clause", "passing": passed, **detail}
    floor = rule.get("fail_if_no_candidate_reaches")
    evaluated_all = all(arm_status.get(c) == "ok" for c in candidates)
    if floor is not None:
        alive = [c for c in candidates if arm_status.get(c) == "ok"
                 and any(_clause_holds(cl, c, deltas, baseline)[0] for cl in floor)]
        # ⚠️ A floor clause over a window that produced NO folds (mean None -- e.g. an empty
        # 30-day holdout) was never measured; reading it as "does not hold" landed FAIL on
        # RQ-20261012-75b2-25/-27 whose own rules say holdout rows < 20 is INDETERMINATE
        # (RESEARCH-GRADE-BACKLOG, 2026-10-15). A FAIL needs every floor clause evaluated.
        unmeasured = sorted({_clause_holds(cl, c, deltas, baseline)[1] for c in candidates
                             if arm_status.get(c) == "ok" for cl in floor
                             if not _clause_evaluable(cl, c, deltas, baseline)})
        if not alive and evaluated_all and unmeasured:
            return {"verdict": "indeterminate",
                    "why": f"no candidate reaches the floor, but floor clause(s) {unmeasured} have no measured "
                           "folds -- an unmeasured window is not a FAIL", **detail}
        if not alive and evaluated_all:
            return {"verdict": "fail", "why": "no candidate reaches the registered floor on any clause", **detail}
        if not alive and not evaluated_all:
            return {"verdict": "indeterminate",
                    "why": "no evaluated candidate reaches the floor, but some declared arms were unavailable — "
                           "a FAIL closes only what was tested, so this is not a FAIL", **detail}
    return {"verdict": "indeterminate", "why": "between the pass bar and the fail floor", **detail}


def headline_checks(rule: Dict[str, Any], deltas: Dict[str, Any], baseline: str) -> List[Dict[str, Any]]:
    out = []
    for h in rule.get("headline_checks") or []:
        cl = {"group": h.get("group", "any"), "vs": h.get("vs", "baseline"), "window": h.get("window", "cv"),
              "op": h["op"], "value": h["value"]}
        holds, what = _clause_holds(cl, h["learner"], deltas, baseline)
        out.append({"name": h.get("name", h["learner"]), "holds": holds, "what": f"{h['learner']}: {what}"})
    return out


# ── unit driver ──────────────────────────────────────────────────────────────
def run_spec(spec: Dict[str, Any], *, unit: str = "(inline)") -> Dict[str, Any]:
    """Run one ml_bakeoff block end to end; returns the verdict.json dict."""
    t0 = time.time()
    learners = spec["learners"]
    baseline = spec["baseline"]
    candidates = list(spec.get("candidates") or [])
    controls = list(spec.get("controls") or [])
    for n in [baseline, *candidates, *controls]:
        if n not in learners:
            raise ValueError(f"{n!r} is not in ml_bakeoff.learners")
    metric = spec["metric"]
    better_high = metric_higher_is_better(metric, spec.get("higher_is_better"))
    status = {n: probe_learner(ls["kind"], ls.get("params") or {}) for n, ls in learners.items()}
    gdata = load_groups(spec["dataset"])
    groups = {g: run_group(df, spec, learners, metric, status) for g, df in gdata.items()}
    arm_status: Dict[str, str] = {}
    for n in learners:
        sts = {gr["arms"][n]["status"] for gr in groups.values()}
        arm_status[n] = "ok" if sts == {"ok"} else sorted(sts - {"ok"})[0] if sts - {"ok"} else "ok"
    windows = ["cv"] + (["holdout"] if spec["dataset"].get("holdout_after") else [])
    refs = [baseline, *controls]
    deltas = build_deltas(groups, [n for n in learners if n != baseline], refs, windows, better_high)
    folds_seen = min((len((gr["arms"][baseline].get("windows") or {}).get("cv", {}).get("fold_metric") or [])
                      for gr in groups.values()), default=0)
    rule = spec.get("rule") or {}
    graded = evaluate_rule(rule, deltas, candidates, baseline, arm_status,
                           int(rule.get("min_folds", 3)), folds_seen)
    base_ok = arm_status.get(baseline) == "ok"
    rows = sum(gr["arms"][baseline].get("windows", {}).get("cv", {}).get("n_test_rows", 0)
               for gr in groups.values() if base_ok)
    licences = {n: {"kind": ls["kind"], "licence": LEARNERS[ls["kind"]][1] if ls["kind"] in LEARNERS else "?",
                    "research_only": LEARNERS[ls["kind"]][2] if ls["kind"] in LEARNERS else False,
                    "weights": ls.get("weights_note", "")} for n, ls in learners.items()}
    measurement = {"engine": "scripts/research/ml_bakeoff.py", "metric": metric, "higher_is_better": better_high,
                   "delta_sign": "positive = learner better than reference", "baseline": baseline,
                   "controls": controls, "candidates": candidates, "arm_status": arm_status,
                   "groups": groups, "deltas": deltas, "rule_evaluation": graded,
                   "headline_checks": headline_checks(rule, deltas, baseline),
                   "licences": licences, "n_folds_cv_min_over_groups": folds_seen,
                   "seconds": round(time.time() - t0, 1)}
    if not base_ok:
        return {"verdict": "not_applicable", "read_state": "producer_failed", "n": None,
                "population": f"{unit}: the BASELINE arm {baseline!r} could not run ({arm_status.get(baseline)}) — NEVER MEASURED",
                "note": "baseline unavailable/errored: nothing is graded against a missing baseline", "measurement": measurement}
    if rows == 0:
        return {"verdict": "not_applicable", "read_state": "no_data", "n": 0,
                "population": f"{unit}: no usable folds (dataset too short for cv.folds/embargo)",
                "note": "no_data: looked, the dataset produced zero evaluable test rows", "measurement": measurement}
    unavailable = {n: next((gr["arms"][n].get("reason") for gr in groups.values() if gr["arms"][n].get("reason")), s)
                   for n, s in arm_status.items() if s != "ok"}
    return {"verdict": graded["verdict"], "read_state": "measured", "n": int(rows),
            "population": (f"{len(groups)} group(s) {sorted(groups)}; baseline {baseline}; candidates "
                           f"{candidates}; metric {metric}; n = baseline OOS test rows summed over groups; "
                           f"cv folds per group >= {folds_seen}"),
            "note": graded["why"] + (f" | UNAVAILABLE ARMS: {unavailable}" if unavailable else ""),
            "measurement": measurement}


def load_unit_spec(unit: str, queue_dir: Optional[Path] = None) -> Dict[str, Any]:
    import yaml
    qd = queue_dir or (_REPO / "research" / "queue")
    raw = yaml.safe_load((qd / f"{unit}.yaml").read_text(encoding="utf-8")) or {}
    spec = raw.get("ml_bakeoff")
    if not isinstance(spec, dict):
        raise ValueError(f"{unit}: no `ml_bakeoff:` block in the unit file")
    return spec


def _self_test() -> int:
    spec = {"task": "classification", "metric": "macro_f1",
            "dataset": {"kind": "synthetic", "groups": ["A", "B"], "n": 2400, "signal": 2.0, "ts_col": "ts",
                        "target": "label", "features": ["x0", "x1", "x2", "x3"]},
            "cv": {"folds": 4, "embargo_rows": 10, "min_train_frac": 0.4},
            "baseline": "prior", "controls": [], "candidates": ["logit", "ghost"],
            "learners": {"prior": {"kind": "prior"}, "logit": {"kind": "numpy_logit"},
                         "ghost": {"kind": "tabpfn"}},
            "rule": {"min_folds": 3, "pass_if": [{"group": ["A", "B"], "vs": "baseline", "op": ">=", "value": 0.0}],
                     "fail_if_no_candidate_reaches": [{"group": "any", "vs": "baseline", "op": ">", "value": 0.02}]}}
    v = run_spec(spec, unit="selftest")
    assert v["read_state"] == "measured" and v["verdict"] in ("pass", "indeterminate"), v
    assert v["measurement"]["arm_status"]["logit"] == "ok"
    # an empty window (holdout with no folds) is unmeasured: never a FAIL (RESEARCH-GRADE-BACKLOG)
    nofold = {"n_folds": 0, "mean": None, "ci95": None}
    dl = {"ALL": {"cv": {"c": {"prior": {"n_folds": 5, "mean": -0.01}}}, "holdout": {"c": {"prior": nofold}}}}
    floor = [{"group": ["ALL"], "vs": "baseline", "op": ">=", "value": 0.1, "window": w} for w in ("cv", "holdout")]
    ev = evaluate_rule({"pass_if": floor, "fail_if_no_candidate_reaches": floor}, dl, ["c"], "prior", {"c": "ok"}, 3, 5)
    assert ev["verdict"] == "indeterminate" and "no measured" in ev["why"], ev
    dl["ALL"]["holdout"]["c"]["prior"] = {"n_folds": 1, "mean": -0.01}
    assert evaluate_rule({"pass_if": floor, "fail_if_no_candidate_reaches": floor}, dl, ["c"], "prior",
                         {"c": "ok"}, 3, 5)["verdict"] == "fail"
    print("ml_bakeoff self-test OK:", v["verdict"], v["note"][:80])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--unit")
    ap.add_argument("--out", help="directory to write verdict.json into")
    ap.add_argument("--queue-dir", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.unit or not a.out:
        ap.error("--unit and --out are required")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    # On the 6 GB trainer a bake-off takes the SAME shared heavy-job lock as training; the helper is
    # inert everywhere else (CI, runner, sandbox) and fail-open on infrastructure errors.
    try:
        sys.path.insert(0, str(_REPO))
        from scripts.ml._heavy_queue import take_heavy_queue
        _lock = take_heavy_queue(f"ml-bakeoff:{a.unit}")   # noqa: F841 — held for the process lifetime
    except Exception as e:  # allow-silent: fail-open by design (the helper is inert off-trainer); stderr line printed
        print(f"ml_bakeoff: heavy-queue helper unavailable ({type(e).__name__}); proceeding unlocked", file=sys.stderr)
    try:
        verdict = run_spec(load_unit_spec(a.unit, Path(a.queue_dir) if a.queue_dir else None), unit=a.unit)
    except Exception as e:  # allow-silent: error.txt + stderr + exit 1; script_run derives producer_failed
        (out / "error.txt").write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
        print(f"ml_bakeoff: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"ml_bakeoff {a.unit}: verdict={verdict['verdict']} read_state={verdict['read_state']} n={verdict['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
