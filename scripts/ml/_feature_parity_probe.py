#!/usr/bin/env python3
"""Train/serve feature-parity probe for a regime head (MB-20260627-003).

RG4 says the ETH 1h regime head discriminates the vol regime OFFLINE (RG3
0.70-0.73) but is NO_EDGE on the LIVE logged feature rows (RG4 ~0.46). RG3
passes because it feeds the SAME live feature builder clean candles; RG4 reads
the EXACT rows the live runtime logged. So the skew lives in WHAT the live
per-bar scorer fed the builder, visible as a per-feature distribution gap
between the training dataset and the logged-live rows.

This probe makes that gap concrete. For a given head it prints, per feature
column, the training-dataset distribution vs the logged-live distribution
(count / present% / mean / std / min / max; value-counts for categoricals like
vol_bucket), split by shadow-log `stage`. A feature whose live mean/std/range is
off-scale vs training, or whose live presence is low, IS the skew. It also
reports the live predicted-score distribution and (when labels join) the
score↔label point-biserial sign, so a "head emits a near-constant score live"
degeneracy shows up directly.

CORRECTION (2026-07-30) — two defects that made this probe's output misleading:

1. It printed the logged ``score`` under the label ``PREDICTED score(volatile)``.
   That field is ``max(proba.values())`` (the *predicted class's* confidence),
   not P(volatile), so for a 2-class regime head it is ``>= 0.5`` by
   construction. Every regime head therefore read as "pinned to volatile" — a
   fleet sweep found ``frac(score < 0.5) == 0.0000`` across all ~30 regime
   heads, while non-multiclass heads in the same log ranged freely below 0.5.
   This nearly produced a false P1 against the live real-money BTC vol gate.
   The probe now reports the logged value under an honest label AND the real
   ``predict_proba[volatile]`` the gate thresholds, plus the would-gate share.
2. It picked the training dataset as ``sorted(glob(...))[-1]`` — alphabetically
   last, not the manifest's pinned version — so it could compare a head against
   data it never trained on. It now resolves ``ml/configs/<id>.yaml`` and warns
   loudly on any fallback.

Read-only, trainer-side (datasets + registry + the mirrored shadow log live
there). Never touches the order path. No inline python -c (the trainer-vm-diag
relay mis-parses quoted -c), so this is a committed helper the relay calls.

  python scripts/ml/_feature_parity_probe.py \
      --model-id eth-regime-1h-lgbm-v1 --symbol ETHUSDT --timeframe 1h
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The market_features columns the regime heads train on (superset; a given
# manifest uses a subset). Numeric unless listed in _CATEGORICAL.
_NUMERIC_COLS = [
    "rolling_log_return_vol",
    "log_return",
    "log_return_lag_1",
    "log_return_lag_2",
    "parkinson_vol",
    "garman_klass_vol",
    "rogers_satchell_vol",
    "yang_zhang_vol",
    # cross-asset peer block (xasset variant only)
    "xa_peer1_log_return",
    "xa_peer1_rolling_log_return_vol",
    "xa_peer2_log_return",
    "xa_peer2_rolling_log_return_vol",
    "xa_breadth_up",
]
_CATEGORICAL = ["vol_bucket", "hour_of_day", "dayofweek"]

# The class whose probability the live vol verdict thresholds
# (``regime_bar_scoring._VOLATILE_CLASS`` / ``ml_vol_verdict``).
_POSITIVE_CLASS = "volatile"
# ``ML_VOL_VERDICT_THRESHOLD`` default — the gate's calm/volatile cut.
_VOL_THRESHOLD = 0.5


def _p_volatile_for_rows(
    model_id: str, feature_rows: List[Dict[str, Any]],
) -> Optional[List[float]]:
    """Re-run ``predict_proba`` on the logged live rows → **P(volatile)**.

    The logged ``score`` field is **NOT** P(volatile).
    ``ShadowPredictor.predict`` returns the wrapped predictor's ``predict``,
    and for a regime head that is
    ``MulticlassPredictor.predict = max(proba.values())`` — the confidence of
    the *predicted* class, hence ``>= 0.5`` by construction for a 2-class head.
    Printing it as "P(volatile)" made **every** regime head look pinned to
    volatile: a 2026-07-30 fleet sweep found `frac(score < 0.5) == 0.0000` for
    all ~30 regime heads, while the non-multiclass heads in the same log
    (``exit-head-donchian-1h-v1``, ``setup-quality-lgbm-v2``) ranged freely
    below 0.5. That mislabel nearly produced a false P1 against the live
    real-money BTC vol gate.

    The gate reads ``predict_proba[volatile]``
    (``regime_bar_scoring._maybe_publish_p_volatile``), so that is what a parity
    probe must report. Rows are passed through **unmodified**, exactly as the
    live path does, so the predictor's own ``_encode_row`` handles categoricals.

    Returns ``None`` when the predictor can't be resolved (no registry on this
    host) — the caller then reports only the logged max-proba, honestly labelled.
    """
    try:
        from ml.registry.model_registry import ModelRegistry  # noqa: PLC0415
        from ml.shadow import factory as _factory  # noqa: PLC0415
        from ml.shadow.factory import resolve_predictor  # noqa: PLC0415

        reg = ModelRegistry(_factory._resolve_default_registry_root())
        sp = resolve_predictor(model_id, reg, log_path=None)  # no audit writes
        base = getattr(sp, "wrapped", sp)
        proba_fn = getattr(base, "predict_proba", None)
        if proba_fn is None:
            return None
    except Exception:  # noqa: BLE001 — probe degrades, never dies
        return None
    out: List[float] = []
    for row in feature_rows:
        try:
            p = (proba_fn(row) or {}).get(_POSITIVE_CLASS)
        except Exception:  # noqa: BLE001 — one bad row must not kill the sweep
            continue
        if p is not None:
            out.append(float(p))
    return out or None


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _stat_block(vals: List[Optional[float]], n_total: int) -> Dict[str, Any]:
    present = [v for v in vals if v is not None]
    if not present:
        return {"n": n_total, "present_pct": 0.0, "mean": None, "std": None,
                "min": None, "max": None}
    return {
        "n": n_total,
        "present_pct": round(100.0 * len(present) / max(1, n_total), 1),
        "mean": round(statistics.fmean(present), 8),
        "std": round(statistics.pstdev(present), 8) if len(present) > 1 else 0.0,
        "min": round(min(present), 8),
        "max": round(max(present), 8),
    }


def _value_counts(vals: List[Any], n_total: int) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    miss = 0
    for v in vals:
        if v is None or v == "":
            miss += 1
            continue
        counts[str(v)] = counts.get(str(v), 0) + 1
    top = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])
    return {"n": n_total, "missing": miss, "value_counts": top}


def _load_jsonl(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit and len(rows) >= limit:
                break
    return rows


def _manifest_dataset_version(model_id: str) -> Optional[str]:
    """The dataset VERSION this head's manifest pins, or ``None``.

    Read from ``ml/configs/<model_id>.yaml::dataset.version`` — the same block
    ``replay_pregate_fleet._manifest_dataset`` uses.
    """
    cfg = _REPO_ROOT / "ml" / "configs" / f"{model_id}.yaml"
    if not cfg.is_file():
        return None
    try:
        import yaml  # noqa: PLC0415

        doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a probe never dies on a bad manifest
        return None
    version = (doc.get("dataset") or {}).get("version")
    return str(version) if version else None


def _training_rows(
    symbol: str, timeframe: str, model_id: str,
) -> (Optional[str], List[Dict[str, Any]], Optional[str]):
    """Load the head's TRAINING rows, preferring the manifest's pinned version.

    This used to take ``sorted(glob(...))[-1]`` — the *alphabetically last*
    version dir. ``datasets-out/market_features/BTCUSDT/15m`` holds ~14 versions
    (v002…v006, v104, v107, v513…v515, v520, vfmac003), so that silently picked
    ``vfmac003`` regardless of which dataset the head actually trained on, and
    labelled it "TRAINING dataset". Comparing a head's live rows against a
    dataset it never saw makes every distribution line untrustworthy — the
    probe's entire output.

    Returns ``(path, rows, warning_or_None)``; the caller prints the warning so
    a fallback is never silent.
    """
    base = f"datasets-out/market_features/{symbol}/{timeframe}"
    version = _manifest_dataset_version(model_id)
    if version:
        pinned = Path(base) / version / "data.jsonl"
        if pinned.is_file():
            return str(pinned), _load_jsonl(pinned), None
    cands = sorted(glob.glob(f"{base}/*/data.jsonl"))
    if not cands:
        return None, [], f"no market_features dataset on disk under {base}"
    path = cands[-1]
    if version:
        warn = (f"manifest pins dataset version {version!r} but it is ABSENT on "
                f"disk — fell back to {path}. Distributions below are NOT "
                f"necessarily this head's training data.")
    else:
        warn = (f"no dataset version in ml/configs/{model_id}.yaml — fell back "
                f"to newest-on-disk {path}. Distributions below are NOT "
                f"necessarily this head's training data.")
    return path, _load_jsonl(Path(path)), warn


def _shadow_rows(model_id: str) -> (Optional[str], List[Dict[str, Any]]):
    """Shadow-log rows for ONE model id.

    `symbol` was a parameter here and was never read — the filter is `model_id`
    alone. That is correct (model ids are symbol-scoped: `btc-regime-15m-...`
    only ever scores BTC) but the dead parameter was actively misleading: a
    caller passing `--symbol ETHUSDT --model-id btc-regime-15m-...` would get
    BTC rows while believing they had filtered to ETH. Removed rather than
    silently ignored — an argument that looks like a filter and is not is the
    same defect class this probe was corrected for (D/inert-parameter, 2026-07-31).
    """
    for c in ("runtime_logs/shadow_predictions.jsonl",
              "runtime_logs/trainer_mirror/shadow_predictions.jsonl",
              "runtime_logs/trainer_mirror/live/shadow_predictions.jsonl"):
        if Path(c).exists():
            out = []
            with open(c, encoding="utf-8") as fh:
                for line in fh:
                    if model_id not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(row.get("model_id")) == model_id:
                        out.append(row)
            return c, out
    return None, []


def _print_cols(title: str, rows_feat: List[Dict[str, Any]], cols_present: List[str]) -> None:
    n = len(rows_feat)
    print(f"  [{title}] n={n}")
    for col in cols_present:
        if col in _CATEGORICAL:
            vc = _value_counts([r.get(col) for r in rows_feat], n)
            print(f"    {col:34s} miss={vc['missing']:<5d} {vc['value_counts']}")
        else:
            blk = _stat_block([_num(r.get(col)) for r in rows_feat], n)
            print(f"    {col:34s} pres={blk['present_pct']:>5}%  "
                  f"mean={blk['mean']}  std={blk['std']}  "
                  f"min={blk['min']}  max={blk['max']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", required=True)
    a = ap.parse_args()

    print(f"== feature-parity probe: {a.model_id} ({a.symbol}/{a.timeframe}) ==")

    tr_path, tr_rows, tr_warn = _training_rows(a.symbol, a.timeframe, a.model_id)
    print(f"\n-- TRAINING dataset --\n  path={tr_path}  rows={len(tr_rows)}")
    if tr_warn:
        print(f"  !! {tr_warn}")
    if tr_rows:
        # Which of the candidate columns actually appear in the training data.
        keys = set()
        for r in tr_rows[:2000]:
            keys.update(r.keys())
        cols = [c for c in (_CATEGORICAL + _NUMERIC_COLS) if c in keys]
        _print_cols("train", tr_rows, cols)
        # regime_label base rate
        lbls = [str(r.get("regime_label")) for r in tr_rows if r.get("regime_label") is not None]
        vol = sum(1 for x in lbls if x == "volatile")
        print(f"    regime_label: n={len(lbls)} volatile={vol} "
              f"({round(100.0*vol/max(1,len(lbls)),2)}%)")

    sl_path, sh_rows = _shadow_rows(a.model_id)
    print(f"\n-- LIVE shadow-log rows --\n  path={sl_path}  rows={len(sh_rows)}")
    if not sh_rows:
        print("  (no live rows for this model — RG4 unscoreable until it soaks)")
        print("PARITY_PROBE_DONE")
        return 0

    # Group by stage; the feature payload is under `feature_row`.
    by_stage: Dict[str, List[Dict[str, Any]]] = {}
    score_by_stage: Dict[str, List[float]] = {}
    for r in sh_rows:
        st = str(r.get("stage", "?"))
        fr = r.get("feature_row") or {}
        by_stage.setdefault(st, []).append(fr)
        sc = _num(r.get("score"))
        if sc is None:
            # some logs nest the positive-class proba
            pr = r.get("proba") or {}
            sc = _num(pr.get("volatile"))
        if sc is not None:
            score_by_stage.setdefault(st, []).append(sc)

    train_keys = set()
    for r in tr_rows[:2000]:
        train_keys.update(r.keys())

    for st, frs in by_stage.items():
        keys = set()
        for fr in frs[:2000]:
            keys.update(fr.keys())
        cols = [c for c in (_CATEGORICAL + _NUMERIC_COLS) if c in keys or c in train_keys]
        print()
        _print_cols(f"live stage={st}", frs, cols)
        sc = score_by_stage.get(st, [])
        if sc:
            blk = _stat_block(sc, len(sc))
            # NOT P(volatile) — see _p_volatile_for_rows. Labelled explicitly so
            # a reader can never mistake a max-proba confidence for the gate's
            # calm/volatile probability.
            print(f"    {'LOGGED score = max(proba), NOT P(vol)':34s} "
                  f"mean={blk['mean']}  std={blk['std']}  "
                  f"min={blk['min']}  max={blk['max']}  (n={len(sc)})")
        # The number the gate actually thresholds.
        pvol = _p_volatile_for_rows(a.model_id, frs)
        if pvol:
            blk = _stat_block(pvol, len(pvol))
            hot = sum(1 for x in pvol if x >= _VOL_THRESHOLD)
            print(f"    {'P(volatile) [what the GATE reads]':34s} "
                  f"mean={blk['mean']}  std={blk['std']}  "
                  f"min={blk['min']}  max={blk['max']}  (n={len(pvol)})")
            print(f"    {'  -> would gate VOLATILE':34s} "
                  f"{hot}/{len(pvol)} = {round(100.0 * hot / len(pvol), 2)}% "
                  f"(threshold {_VOL_THRESHOLD})")
        else:
            print(f"    {'P(volatile)':34s} unavailable — predictor not "
                  f"resolvable here; only the logged max-proba is shown above")
        # which trained cols are entirely ABSENT from the live row
        missing_cols = sorted(c for c in (_CATEGORICAL + _NUMERIC_COLS)
                              if c in train_keys and c not in keys)
        if missing_cols:
            print(f"    !! cols in TRAIN but absent from LIVE rows: {missing_cols}")

    print("PARITY_PROBE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
