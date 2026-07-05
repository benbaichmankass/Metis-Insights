#!/usr/bin/env python3
"""Replay pre-gate — STAGE 2 (RG4): score over the LOGGED LIVE feature rows.

The train/serve-skew detector. Stage 1 (``replay_pregate_fleet.py``) feeds CLEAN
candles through the live feature function and so only ever exercises the
*healthy* enriched path. Stage 2 takes the **actual feature rows the live runtime
logged** to ``shadow_predictions.jsonl`` — whatever the live path really built,
skew and all — re-runs ``predict_proba`` on each, and scores it vs the realized
forward-vol regime. **Broken down by stage**, so a head that is healthy at
``shadow`` (enriched) but degenerate at ``advisory`` (the bare
``_feature_row_from_pkg`` row, no market_features) shows up as exactly that split
— which is the RG2 finding (``advisory_sizing.py`` doesn't enrich) reproduced
automatically.

This is the stage that catches the failure a candle-replay cannot: it scores the
EXACT row the order path used, so a feature-pipeline gap between train/shadow and
the advisory order path is visible as a per-stage AUC collapse.

Research-only (Tier-1). Reads the shadow log + candle artifact + registry; writes
a JSON report. Never touches the order path.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ml.replay_pregate import _score_block  # noqa: E402
from scripts.ml.replay_pregate_fleet import _load_jsonl  # noqa: E402
from ml.registry.model_registry import ModelRegistry  # noqa: E402
from ml.shadow import factory as _factory  # noqa: E402
from ml.shadow.factory import resolve_predictor  # noqa: E402
from src.runtime.regime_shadow import regime_spec_of  # noqa: E402


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# RG4 must score a head against the SAME regime_label definition it trained on:
# the realized vol-label is ``forward_vol > vol_threshold`` and a wrong threshold
# silently mis-scores the head (a good head reads NO_EDGE — the eth-regime-1h
# 0.46→0.58 finding, MB-20260628-RG4-THRESH). The label threshold is a *build*
# parameter, not a candle property, so it is NOT in the ``market_raw`` candle
# file this script joins against. The build script
# (``scripts/ops/build_trainer_datasets.sh``) is the source of truth:
#   • every Bybit (symbol, tf) ``market_features`` build labels at 0.005
#     (``build_bybit_pair``);
#   • MES uses a DATA-DRIVEN median ``vol_threshold`` (``build_mes_features_tf``
#     → ``mes_median_vt``, e.g. 5m≈0.00056 / 15m≈0.00093), which changes each
#     rebuild and so can only be supplied explicitly (or read from the
#     ``market_features`` metadata).
# So resolution order is, most-authoritative first:
#   1. an explicit ``--vol-threshold`` (the precise path — what
#      ``rg4_vt_sweep.sh`` passes; and the only correct path for MES);
#   2. the matching ``market_features`` dataset's ``metadata.json`` notes, when
#      the build records the threshold there (best-effort, future-proof);
#   3. a per-symbol default keyed off the candle file's ``symbol`` (Bybit→0.005);
#   4. the legacy 0.003 default — only when the symbol is unknown.
# The chosen value + which rule produced it are surfaced in the report
# (``vol_threshold`` / ``vol_threshold_source``) so an RG4 number is never
# silently trusted at the wrong threshold.
_DEFAULT_VOL_THRESHOLD: float = 0.003
# Sentinel for "the caller did not pass --vol-threshold" (distinct from a caller
# that explicitly passes the 0.003 default value).
_VT_UNSET: float = -1.0
# Per-symbol label thresholds mirrored from the build script's source of truth.
# Bybit pairs are labeled at a fixed 0.005 (``build_bybit_pair``). MES is
# intentionally ABSENT — its median threshold is data-driven and must be passed
# explicitly or read from metadata; a hardcoded MES number would go stale.
_SYMBOL_VOL_THRESHOLD: Dict[str, float] = {
    "BTCUSDT": 0.005,
    "ETHUSDT": 0.005,
    "SOLUSDT": 0.005,
}


def _candle_symbol(candle_rows: List[Dict[str, Any]]) -> Optional[str]:
    """Best-effort ``symbol`` of a ``market_raw`` candle set (first row's stamp)."""
    for r in candle_rows:
        sym = r.get("symbol")
        if sym:
            return str(sym).strip().upper()
    return None


def _threshold_from_market_features_meta(candles: str) -> Optional[float]:
    """Try to read the label ``vol_threshold`` from the matching
    ``market_features`` dataset's ``metadata.json``.

    The replay is pointed at a ``market_raw`` candle dir
    (``…/market_raw/<SYM>/<tf>/<version>/data.jsonl``); the realized-label
    threshold lives in the sibling ``market_features`` build. We look up the
    corresponding ``market_features/<SYM>/<tf>/<version>/metadata.json`` and
    accept a numeric ``vol_threshold`` from either a structured field or the
    free-text ``notes`` (``vol_threshold=<x>``). Best-effort + fail-soft: any
    miss returns ``None`` so resolution falls through to the per-symbol default.
    Today the build does not record it, so this is a future-proof hook, not a
    behaviour change.
    """
    try:
        p = Path(candles).resolve()
        parts = list(p.parts)
        if "market_raw" not in parts:
            return None
        i = parts.index("market_raw")
        parts[i] = "market_features"
        meta_path = Path(*parts).parent / "metadata.json"
        if not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never let metadata I/O break scoring
        return None
    val = meta.get("vol_threshold")
    if val is None:
        import re

        m = re.search(r"vol_threshold\s*[=:]\s*([0-9]*\.?[0-9]+)",
                      str(meta.get("notes", "")))
        val = m.group(1) if m else None
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _resolve_vol_threshold(
    candle_rows: List[Dict[str, Any]], candles: str, explicit: float
) -> tuple[float, str]:
    """Resolve the label ``vol_threshold`` per-symbol (see the module note above).

    Returns ``(threshold, source)`` where ``source`` ∈ {``explicit``,
    ``market_features_meta``, ``symbol_default``, ``global_default``} so the
    chosen value is auditable in the report.
    """
    if explicit != _VT_UNSET:
        return explicit, "explicit"
    meta_vt = _threshold_from_market_features_meta(candles)
    if meta_vt is not None:
        return meta_vt, "market_features_meta"
    sym = _candle_symbol(candle_rows)
    if sym and sym in _SYMBOL_VOL_THRESHOLD:
        return _SYMBOL_VOL_THRESHOLD[sym], "symbol_default"
    return _DEFAULT_VOL_THRESHOLD, "global_default"


def _forward_label_map(candle_rows: List[Dict[str, Any]], *, forward_m: int,
                       vol_threshold: float, positive_class: str) -> Dict[datetime, int]:
    """Map each bar's close-time → realized regime label (1 = volatile),
    using the same market_features rule: forward_vol = pstdev(log_returns[
    i+1..i+m]) > vol_threshold."""
    rows = sorted(candle_rows, key=lambda r: str(r.get("ts", "")))
    closes = [float(r.get("close", 0.0) or 0.0) for r in rows]
    ts = [_parse_ts(r.get("ts")) for r in rows]
    lr: List[Optional[float]] = [None]
    for k in range(1, len(closes)):
        p, c = closes[k - 1], closes[k]
        lr.append(math.log(c / p) if (p > 0 and c > 0) else None)
    out: Dict[datetime, int] = {}
    n = len(closes)
    for i in range(n):
        if ts[i] is None or i + forward_m >= n:
            continue
        window = [v for v in lr[i + 1:i + forward_m + 1] if v is not None]
        if len(window) < 2:
            continue
        fvol = statistics.pstdev(window)
        out[ts[i]] = 1 if fvol > vol_threshold else 0
    return out


def _nearest_label(label_map: Dict[datetime, int], when: datetime,
                   sorted_keys: List[datetime], tol_seconds: float) -> Optional[int]:
    """Realized label for the bar whose close is the latest <= `when` and within
    `tol_seconds` (the bar the prediction was made on)."""
    import bisect
    idx = bisect.bisect_right(sorted_keys, when) - 1
    if idx < 0:
        return None
    bar = sorted_keys[idx]
    if (when - bar).total_seconds() > tol_seconds:
        return None
    return label_map.get(bar)


def run(model_id: str, *, shadow_log: str, candles: str, forward_m: int,
        vol_threshold: float, positive_class: str, bar_seconds: float) -> Dict[str, Any]:
    reg = ModelRegistry(_factory._resolve_default_registry_root())
    sp = resolve_predictor(model_id, reg, log_path=None)
    base = getattr(sp, "wrapped", sp)
    if regime_spec_of(base) is None:
        raise SystemExit(f"{model_id}: no regime_spec — stage-2 expects a regime head")

    records = [r for r in _load_jsonl(Path(shadow_log))
               if str(r.get("model_id")) == model_id]
    if not records:
        raise SystemExit(f"no shadow-log records for {model_id}")

    candle_rows = _load_jsonl(Path(candles))
    # Resolve the label threshold per-symbol (MB-20260628-RG4-THRESH) so the
    # realized regime label matches the head's training definition rather than
    # the 0.003 default that mis-scores Bybit (0.005) + MES (data-driven) heads.
    vol_threshold, vol_threshold_source = _resolve_vol_threshold(
        candle_rows, candles, vol_threshold,
    )
    label_map = _forward_label_map(candle_rows, forward_m=forward_m,
                                   vol_threshold=vol_threshold,
                                   positive_class=positive_class)
    sorted_keys = sorted(label_map.keys())
    tol = max(bar_seconds, 1.0)

    by_stage: Dict[str, Dict[str, List]] = {}
    unlabeled = 0
    for rec in records:
        stage = str(rec.get("stage", "?"))
        row = rec.get("feature_row") or {}
        when = _parse_ts(rec.get("predicted_at_utc"))
        if when is None:
            continue
        y = _nearest_label(label_map, when, sorted_keys, tol)
        if y is None:
            unlabeled += 1
            continue
        try:
            # Re-run on the EXACT logged row (numeric coercion mirrors the
            # encoder's missing→NaN, so a bare advisory row degenerates here too).
            proba = base.predict_proba({k: _num(v) for k, v in row.items()})
            p = float(proba.get(positive_class, 0.0))
        except Exception:  # noqa: BLE001
            continue
        b = by_stage.setdefault(stage, {"scores": [], "labels": [], "keys": set(),
                                        "pos_ts": []})
        b["scores"].append(p)
        b["labels"].append(int(y))
        b["keys"].update(row.keys())
        if int(y) == 1:
            b["pos_ts"].append(when.timestamp())

    stages_out = {}
    for stage, b in by_stage.items():
        blk = _score_block(b["scores"], b["labels"])
        blk["n_feature_keys"] = len(b["keys"])
        blk["has_market_features"] = "vol_bucket" in b["keys"] or "yang_zhang_vol" in b["keys"]
        # Power accounting for the shadow→advisory readiness gate
        # (MB-20260705-FC-ADVISORY-READINESS): the rare-class AUC literature says
        # the ABSOLUTE positive count drives estimate quality, and volatile bars
        # cluster — so report the positive count AND the number of distinct
        # positive EPISODES (positive-labeled rows separated by > 2h), the
        # effective sample size a bar count alone overstates.
        pos_ts = sorted(b["pos_ts"])
        episodes = 0
        prev = None
        for t in pos_ts:
            if prev is None or (t - prev) > 7200.0:
                episodes += 1
            prev = t
        blk["n_pos"] = len(pos_ts)
        blk["pos_episodes"] = episodes
        auc = blk.get("auc") or 0.0
        blk["verdict"] = ("TRUSTWORTHY_SIGNAL" if auc >= 0.55
                          else "ANTI_PREDICTIVE" if auc < 0.45 else "NO_EDGE")
        stages_out[stage] = blk
    return {
        "stage": 2, "model_id": model_id, "n_records": len(records),
        "n_unlabeled": unlabeled, "forward_m": forward_m,
        "vol_threshold": vol_threshold,
        "vol_threshold_source": vol_threshold_source, "by_stage": stages_out,
        "note": "Stage 2 re-runs predict_proba on the EXACT logged live rows. A "
                "stage whose has_market_features=false + degenerate AUC is the "
                "train/serve-skew signature (e.g. advisory bare-row scoring).",
    }


def _num(v: Any) -> Any:
    try:
        return float(v)
    except (TypeError, ValueError):
        return v  # leave categoricals (e.g. vol_bucket) as-is for the encoder


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--shadow-log", required=True,
                    help="path to shadow_predictions.jsonl (the LIVE log)")
    ap.add_argument("--candles", required=True,
                    help="market_raw data.jsonl for the realized-label join")
    ap.add_argument("--forward-m", type=int, default=5)
    ap.add_argument(
        "--vol-threshold", type=float, default=_VT_UNSET,
        help="label vol_threshold; when omitted it is resolved per-symbol "
             "(Bybit→0.005, else market_features metadata, else 0.003) so RG4 "
             "scores against the head's own training label (MB-20260628-RG4-THRESH). "
             "Pass a value to force it (e.g. rg4_vt_sweep.sh / MES median).",
    )
    ap.add_argument("--positive-class", default="volatile")
    ap.add_argument("--bar-seconds", type=float, default=3600.0,
                    help="bar length in seconds for the record→bar tolerance")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args()
    report = run(a.model_id, shadow_log=a.shadow_log, candles=a.candles,
                 forward_m=a.forward_m, vol_threshold=a.vol_threshold,
                 positive_class=a.positive_class, bar_seconds=a.bar_seconds)
    out = json.dumps(report, indent=1)
    if a.json_out and a.json_out != "-":
        Path(a.json_out).write_text(out, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
