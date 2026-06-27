#!/usr/bin/env python3
"""Point-in-time REPLAY PRE-GATE for shadow regime heads (RG1).

The fast, offline, leakage-/skew-catching pre-gate that compresses ~80% of a
live shadow soak (see docs/research/ml-strategies-deep-review-2026-06-25.md and
the "why soak" discussion). It feeds historical bars to a regime head ONE AT A
TIME, strictly causally, THROUGH THE LIVE PREDICT CODE PATH
(``src.runtime.regime_shadow.feature_row_for_predictor`` +
``MulticlassPredictor.predict_proba``) and scores discrimination/calibration vs
the realized regime label.

WHY THE LIVE CODE PATH (the whole point):
- X (features) = the LIVE path. If a head scores well on the trainer's offline
  features but poorly here, that gap is **train/serve skew** — invisible to a
  vectorized backtest, the thing that turns a "promotable" head into the
  AUC-0.40 live failure.
- y (label)    = the realized forward-vol regime (``range`` / ``volatile``),
  computed forward-only and EMBARGOED so the score is never contaminated by the
  bars the features saw.

This is NOT a substitute for a (shortened) live shadow — it cannot see future
non-stationarity. It IS the fast pre-gate a head must clear before earning a
live-shadow slot.

ACID TEST (RG2): run the demoted ``btc-regime-1h-lgbm-yz-v1`` head. It MUST
score poorly (AUC ~0.5 or below / non-positive brier-lift). If the replay says
it is fine, the harness is UNFAITHFUL (feature-path or label parity) and must be
fixed before trusting any verdict.

Research-only (Tier-1). Reads a candle CSV/parquet + the model registry; writes
a JSON report. Never touches the order path.

LABEL PARITY (RG2 fix, 2026-06-25): the forward-vol → {range,volatile} label now
matches the ``market_features`` family byte-for-byte — ``forward_vol[i] =
pstdev(log_returns[i+1 .. i+m])`` cut at the family's FIXED ``vol_threshold``
(``--vol-threshold``; v002 BTC/1h = ``0.003`` with ``--forward-m 5``). Pass the
per-dataset build value (recorded in the dataset's ``metadata.json``); the old
quantile proxy survives only as a fallback (``--vol-threshold -1``) for a
symbol/timeframe whose build threshold isn't known, and is flagged NON-parity in
the report. With parity restored, brier-lift is meaningful alongside AUC.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]  # scripts/ml/<file> → repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.registry.model_registry import ModelRegistry  # noqa: E402
from ml.shadow import factory as _factory  # noqa: E402
from ml.shadow.factory import resolve_predictor  # noqa: E402
from src.runtime.regime_shadow import (  # noqa: E402
    feature_row_for_predictor,
    regime_spec_of,
)


def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.set_index("timestamp")
        .resample(rule, label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )


def _log_returns(closes: Sequence[float]) -> List[Optional[float]]:
    """``ln(close[k]/close[k-1])`` per bar; index 0 is ``None`` (no prior)."""
    out: List[Optional[float]] = [None]
    for k in range(1, len(closes)):
        p, c = closes[k - 1], closes[k]
        out.append(math.log(c / p) if (p > 0 and c > 0) else None)
    return out


def _forward_vol_series(closes: Sequence[float], m: int) -> List[Optional[float]]:
    """Realized forward-vol per bar, **byte-for-byte parity with the
    ``market_features`` family's ``forward_log_return_vol``** (the label the head
    was trained against): ``forward_vol[i] = pstdev(log_returns[i+1 .. i+m])`` —
    the population stdev of the ``m`` forward log-returns spanning
    ``closes[i .. i+m]``. Strictly forward of bar ``i`` (the features only see
    ``[.. i]``), so it can never leak.

    This replaces the earlier off-by-one window (``rolling_log_return_vol`` over
    ``closes[i+1 .. i+m]``, which dropped the ``i→i+1`` return and used the wrong
    stdev convention) that broke acid-test label parity (RG2)."""
    lr = _log_returns(closes)
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    for i in range(n):
        fidx = i + m
        if fidx >= n:  # embargo tail: no full forward window
            continue
        window = [v for v in lr[i + 1 : fidx + 1] if v is not None]
        if len(window) >= 2:
            out[i] = statistics.pstdev(window)
    return out


def _auc(scores: List[float], labels: List[int]) -> Optional[float]:
    """Rank-based AUC (Mann-Whitney U), no sklearn dependency."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    order = sorted(range(len(scores)), key=lambda k: scores[k])
    ranks = [0.0] * len(scores)
    k = 0
    while k < len(order):
        j = k
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[k]]:
            j += 1
        avg = (k + j) / 2.0 + 1.0  # 1-based average rank for ties
        for t in range(k, j + 1):
            ranks[order[t]] = avg
        k = j + 1
    sum_pos = sum(ranks[idx] for idx, y in enumerate(labels) if y == 1)
    n_pos, n_neg = len(pos), len(neg)
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _brier(scores: List[float], labels: List[int]) -> float:
    return sum((s - y) ** 2 for s, y in zip(scores, labels)) / len(scores)


def _score_block(scores: List[float], labels: List[int]) -> Dict[str, Any]:
    n = len(scores)
    if n == 0:
        return {"n": 0}
    base = sum(labels) / n
    brier = _brier(scores, labels)
    brier_base = base * (1 - base)  # brier of the constant base-rate predictor
    return {
        "n": n,
        "base_rate": round(base, 4),
        "auc": (round(a, 4) if (a := _auc(scores, labels)) is not None else None),
        "brier": round(brier, 5),
        "brier_base": round(brier_base, 5),
        "brier_lift": round(brier_base - brier, 5),  # >0 = beats base rate
        "mean_score": round(sum(scores) / n, 4),
    }


def run(model_id: str, data: str, *, resample: Optional[str], forward_m: int,
        vol_threshold: Optional[float], vol_quantile: float, window_n: int,
        folds: int, positive_class: str) -> Dict[str, Any]:
    reg = ModelRegistry(_factory._resolve_default_registry_root())
    sp = resolve_predictor(model_id, reg, log_path=None)  # no audit-log writes
    base = getattr(sp, "wrapped", sp)
    spec = regime_spec_of(base) or regime_spec_of(sp)
    if spec is None:
        raise SystemExit(f"{model_id}: no regime_spec — not a regime head?")
    symbol = str(spec.get("symbol"))
    timeframe = str(spec.get("timeframe"))

    df = _load_candles(data)
    if resample:
        df = _resample(df, resample)
    closes = df["close"].astype(float).tolist()
    n = len(df)

    # First pass: forward-vol per bar (label side) with byte-for-byte
    # market_features parity. The volatile cutoff is the family's FIXED
    # `vol_threshold` when supplied (true label parity — RG2 fix); the quantile
    # is only a fallback for a symbol/timeframe whose build threshold isn't known.
    fvol: List[Optional[float]] = _forward_vol_series(closes, forward_m)
    fvol_known = sorted(v for v in fvol if v is not None)
    if not fvol_known:
        raise SystemExit("no forward-vol windows — data too short for forward_m")
    if vol_threshold is not None:
        cutoff = float(vol_threshold)
        label_mode = f"fixed_vol_threshold={vol_threshold}"
    else:
        cutoff = fvol_known[min(len(fvol_known) - 1,
                                int(vol_quantile * len(fvol_known)))]
        label_mode = f"quantile_proxy={vol_quantile} (NOT family parity)"

    rows: List[Dict[str, Any]] = []
    skipped = 0
    for i in range(n):
        if fvol[i] is None:  # embargo tail: no forward label
            continue
        # Causal feature window: only bars [.. i].
        lo = max(0, i - window_n - 5)
        window = df.iloc[lo : i + 1]
        live_row = feature_row_for_predictor(
            base, {}, closes=closes[: i + 1], symbol=symbol,
            timeframe=timeframe, candles_df=window,
        )
        if live_row is None:  # warm-up / vol-uncomputable — live path skips too
            skipped += 1
            continue
        try:
            proba = base.predict_proba(live_row)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            if skipped <= 3:
                print(f"predict_proba err @bar {i}: {exc}", file=sys.stderr)
            continue
        p_pos = float(proba.get(positive_class, 0.0))
        y = 1 if fvol[i] > cutoff else 0
        rows.append({"i": i, "ts": str(df["timestamp"].iloc[i]), "p": p_pos, "y": y})

    if not rows:
        raise SystemExit("0 scored bars — check feature path / window / data")

    scores = [r["p"] for r in rows]
    labels = [r["y"] for r in rows]
    overall = _score_block(scores, labels)

    # Walk-forward folds (sequential, equal-size) — durability/decay check.
    fold_reports = []
    fsz = max(1, len(rows) // folds)
    for f in range(folds):
        a, b = f * fsz, (f + 1) * fsz if f < folds - 1 else len(rows)
        blk = _score_block(scores[a:b], labels[a:b])
        blk["window"] = [str(rows[a]["ts"]), str(rows[b - 1]["ts"])]
        fold_reports.append(blk)

    verdict = "TRUSTWORTHY_SIGNAL" if (overall.get("auc") or 0) >= 0.55 \
        else ("ANTI_PREDICTIVE" if (overall.get("auc") or 1) < 0.45 else "NO_EDGE")
    return {
        "model_id": model_id, "symbol": symbol, "timeframe": timeframe,
        "data": data, "resample": resample, "forward_m": forward_m,
        "label_mode": label_mode, "label_cutoff": round(cutoff, 6),
        "vol_threshold": vol_threshold, "vol_quantile": vol_quantile,
        "positive_class": positive_class,
        "n_scored": len(rows), "n_skipped": skipped,
        "overall": overall, "folds": fold_reports,
        "auc_verdict": verdict,
        "note": "label is market_features-parity when label_mode is "
                "fixed_vol_threshold (forward_vol > vol_threshold → volatile, "
                "forward_vol = pstdev(log_returns[i+1..i+m])); brier_lift is then "
                "meaningful. quantile_proxy mode is NOT family parity (AUC only).",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--resample", default=None)
    ap.add_argument("--forward-m", type=int, default=5,
                    help="forward label window length in bars (market_features "
                         "v002 BTC/1h = 5)")
    ap.add_argument("--vol-threshold", type=float, default=0.003,
                    help="FIXED forward-vol cutoff for the 'volatile' label — "
                         "market_features family parity (v002 BTC/1h = 0.003). "
                         "Pass the per-dataset build value; use --vol-quantile "
                         "(and set this <0) only when the build value is unknown")
    ap.add_argument("--vol-quantile", type=float, default=0.70,
                    help="fallback forward-vol QUANTILE cutoff (NOT family "
                         "parity) — used only when --vol-threshold < 0")
    ap.add_argument("--window-n", type=int, default=20)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--positive-class", default="volatile")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args()
    report = run(
        a.model_id, a.data, resample=a.resample, forward_m=a.forward_m,
        vol_threshold=(a.vol_threshold if a.vol_threshold >= 0 else None),
        vol_quantile=a.vol_quantile, window_n=a.window_n, folds=a.folds,
        positive_class=a.positive_class,
    )
    out = json.dumps(report, indent=1)
    if a.json_out:
        Path(a.json_out).write_text(out, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
