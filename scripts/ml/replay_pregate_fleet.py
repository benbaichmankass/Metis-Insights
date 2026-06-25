#!/usr/bin/env python3
"""Fleet runner for the replay pre-gate — STAGE 1 (RG3).

Scores EVERY shadow-stage regime head through the LIVE predict code path
(``feature_row_for_predictor`` + ``predict_proba``) against the dataset's OWN
``regime_label`` — joined by timestamp, so there is **true market_features label
parity with zero vol_threshold guessing** (the dataset already carries the exact
label the head trained against).

WHAT STAGE 1 PROVES (and does not):
- A head that scores well here has a sound MODEL + FEATURE FUNCTION + LABEL — it
  discriminates the realized regime when fed clean candles through the live
  feature builder. PASSING is necessary.
- It does NOT prove live-feed fidelity. The acid test (RG2) showed the demoted
  ``btc-regime-1h-lgbm-yz-v1`` head scores AUC ~0.79 here yet failed live — the
  live failure lives in the real-time feature ASSEMBLY, which clean candles
  don't reproduce. That train/serve-skew check is STAGE 2 (RG4: replay over the
  logged live ``shadow_predictions.jsonl`` feature rows). So a 🟢 here is a
  prerequisite, not a promotion.

Research-only (Tier-1). Reads candle + dataset artifacts + the registry; writes
a JSON report to stdout (``--json -``) or a file. Never touches the order path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the single-model harness's scoring + live-path plumbing (RG1/RG2).
from scripts.ml.replay_pregate import _score_block  # noqa: E402
from ml.registry.model_registry import ModelRegistry  # noqa: E402
from ml.shadow import factory as _factory  # noqa: E402
from ml.shadow.factory import (  # noqa: E402
    discover_shadow_stage_model_ids,
    resolve_predictor,
)
from src.runtime.regime_shadow import (  # noqa: E402
    feature_row_for_predictor,
    regime_spec_of,
)


def _datasets_root() -> Path:
    root = os.environ.get("DATASETS_ROOT")
    if root:
        return Path(root)
    for c in (_REPO_ROOT / "datasets-out", _REPO_ROOT / "datasets"):
        if c.is_dir():
            return c
    return _REPO_ROOT / "datasets-out"


def _manifest_dataset(model_id: str) -> Optional[Dict[str, Any]]:
    """Read the model's manifest dataset block (family/symbol/timeframe/version)."""
    cfg = _REPO_ROOT / "ml" / "configs" / f"{model_id}.yaml"
    if not cfg.is_file():
        return None
    doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    return doc.get("dataset")


def _resolve_jsonl(family: str, symbol: str, timeframe: str,
                   version: Optional[str]) -> Optional[Path]:
    """Locate a ``<family>/<symbol>/<timeframe>/<version>/data.jsonl`` artifact,
    falling back to the newest version dir when the exact version is absent."""
    base = _datasets_root() / family / symbol / timeframe
    if version:
        p = base / version / "data.jsonl"
        if p.is_file():
            return p
    if base.is_dir():
        versions = sorted((d for d in base.iterdir() if d.is_dir()),
                          key=lambda d: d.name, reverse=True)
        for d in versions:
            p = d / "data.jsonl"
            if p.is_file():
                return p
    return None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _candles_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "timestamp": [r.get("ts") for r in rows],
            "ts_raw": [str(r.get("ts")) for r in rows],
            "open": [float(r.get("open", 0.0) or 0.0) for r in rows],
            "high": [float(r.get("high", 0.0) or 0.0) for r in rows],
            "low": [float(r.get("low", 0.0) or 0.0) for r in rows],
            "close": [float(r.get("close", 0.0) or 0.0) for r in rows],
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def score_model(model_id: str, reg: ModelRegistry, *, window_n: int,
                folds: int, positive_class: str) -> Dict[str, Any]:
    sp = resolve_predictor(model_id, reg, log_path=None)
    base = getattr(sp, "wrapped", sp)
    spec = regime_spec_of(base) or regime_spec_of(sp)
    if spec is None:
        raise ValueError("no regime_spec — not a (frozen) regime head")
    symbol = str(spec.get("symbol"))
    timeframe = str(spec.get("timeframe"))

    ds = _manifest_dataset(model_id) or {}
    family = str(ds.get("family", "market_features"))
    sym_scope = str(ds.get("symbol_scope", symbol)) or symbol
    tf = str(ds.get("timeframe", timeframe)) or timeframe
    version = ds.get("version")

    feat_path = _resolve_jsonl(family, sym_scope, tf, version)
    if feat_path is None:
        raise ValueError(f"no {family} dataset for {sym_scope}/{tf} (v={version})")
    raw_path = _resolve_jsonl("market_raw", sym_scope, tf, version)
    if raw_path is None:
        raise ValueError(f"no market_raw candles for {sym_scope}/{tf}")

    # True-parity label: the dataset's own regime_label, keyed by raw ts string.
    label_rows = _load_jsonl(feat_path)
    label_map: Dict[str, int] = {
        str(r.get("ts")): (1 if str(r.get("regime_label")) == positive_class else 0)
        for r in label_rows
        if r.get("regime_label") is not None
    }
    if not label_map:
        raise ValueError("dataset has no regime_label rows")

    candle_rows = _load_jsonl(raw_path)
    df = _candles_df(candle_rows)
    closes = df["close"].astype(float).tolist()
    n = len(df)

    scores: List[float] = []
    labels: List[int] = []
    ts_seq: List[str] = []
    skipped = 0
    for i in range(n):
        ts_raw = str(df["ts_raw"].iloc[i])
        y = label_map.get(ts_raw)
        if y is None:  # no parity label for this bar (warm-up / embargo tail)
            continue
        lo = max(0, i - window_n - 5)
        window = df.iloc[lo:i + 1]
        live_row = feature_row_for_predictor(
            base, {}, closes=closes[:i + 1], symbol=symbol,
            timeframe=timeframe, candles_df=window,
        )
        if live_row is None:
            skipped += 1
            continue
        try:
            proba = base.predict_proba(live_row)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        scores.append(float(proba.get(positive_class, 0.0)))
        labels.append(int(y))
        ts_seq.append(ts_raw)

    if not scores:
        raise ValueError("0 scored bars (no ts overlap between candles + labels?)")

    overall = _score_block(scores, labels)
    fold_reports = []
    fsz = max(1, len(scores) // folds)
    for f in range(folds):
        a = f * fsz
        b = (f + 1) * fsz if f < folds - 1 else len(scores)
        blk = _score_block(scores[a:b], labels[a:b])
        blk["window"] = [ts_seq[a], ts_seq[b - 1]]
        fold_reports.append(blk)

    auc = overall.get("auc") or 0.0
    verdict = ("TRUSTWORTHY_SIGNAL" if auc >= 0.55
               else "ANTI_PREDICTIVE" if auc < 0.45 else "NO_EDGE")
    return {
        "model_id": model_id, "symbol": symbol, "timeframe": timeframe,
        "dataset": f"{family}/{sym_scope}/{tf}/{version}",
        "label_mode": "dataset_regime_label_join",
        "n_scored": len(scores), "n_skipped": skipped,
        "overall": overall, "folds": fold_reports, "auc_verdict": verdict,
    }


def run(model_ids: List[str], *, window_n: int, folds: int,
        positive_class: str) -> Dict[str, Any]:
    reg = ModelRegistry(_factory._resolve_default_registry_root())
    if not model_ids:
        discovered = list(discover_shadow_stage_model_ids(reg))
        model_ids = []
        for mid in discovered:
            try:
                sp = resolve_predictor(mid, reg, log_path=None)
                if regime_spec_of(getattr(sp, "wrapped", sp)) is not None:
                    model_ids.append(mid)
            except Exception:  # noqa: BLE001
                continue
    print(f"[fleet] scoring {len(model_ids)} head(s): {', '.join(model_ids)}",
          file=sys.stderr, flush=True)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for n, mid in enumerate(model_ids, 1):
        print(f"[fleet] ({n}/{len(model_ids)}) {mid} ...", file=sys.stderr, flush=True)
        try:
            r = score_model(mid, reg, window_n=window_n, folds=folds,
                            positive_class=positive_class)
            results.append(r)
            ov = r.get("overall") or {}
            print(f"[fleet]   -> {r.get('auc_verdict')} auc={ov.get('auc')} "
                  f"n={r.get('n_scored')}", file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001
            errors.append({"model_id": mid, "error": str(exc)})
            print(f"[fleet]   -> ERROR {exc}", file=sys.stderr, flush=True)
    results.sort(key=lambda r: (r.get("overall") or {}).get("auc") or 0.0,
                 reverse=True)
    return {
        "stage": 1,
        "n_models": len(model_ids),
        "n_scored": len(results),
        "results": results,
        "errors": errors,
        "note": "STAGE 1 (candle-replay, dataset-label parity): validates "
                "model+feature-fn+label. Live-feed fidelity is STAGE 2 (RG4).",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default="",
                    help="comma-separated model ids (blank = all shadow regime heads)")
    ap.add_argument("--window-n", type=int, default=20)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--positive-class", default="volatile")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write report here ('-' = stdout)")
    a = ap.parse_args()
    ids = [m.strip() for m in a.models.split(",") if m.strip()]
    report = run(ids, window_n=a.window_n, folds=a.folds,
                 positive_class=a.positive_class)
    out = json.dumps(report, indent=1)
    if a.json_out and a.json_out != "-":
        Path(a.json_out).write_text(out, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
