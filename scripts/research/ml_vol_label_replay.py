#!/usr/bin/env python3
"""Offline replay of the LIVE ML vol axis — per-bar ``calm`` / ``volatile`` labels.

The missing half of the regime harness. ``regime_debt_matrix`` /
``regime_cell_walkforward`` / ``regime_tag_emitted`` measure the **trend** axis
only (ADX chop / transitional / trending). The live router gates on a **2-D**
``(trend, vol)`` cell, so six authored ``trend_vol`` cells that drop real BTC
intents could not be re-measured by any tool — and, worse, a 1-D re-audit of a
strategy that ALSO carries a 2-D cell silently POOLS vol states the live gate
already refuses, so its verdict is not decision-grade
(``BL-20260730-2D-VOL-CELLS-UNAUDITABLE``).

This module closes that gap by reproducing ``intents._decision_vol_regime``
OFFLINE, per bar.

WHAT THE LIVE GATE ACTUALLY DOES (mirrored here, not re-invented)
----------------------------------------------------------------
Under ``REGIME_ML_VERDICT_MODE=use`` (LIVE for BTC since 2026-06-28) the vol
axis of the gate decision comes from ``ml_vol_regime_for_symbol(symbol)``:

1. resolve the **advisory**-stage regime head for the SYMBOL (per-SYMBOL, NOT
   per ``(symbol, timeframe)`` — BTC's single 15m head labels the 1h and 4h
   cells too), preferring the v2 / non-yz head;
2. read ``P(volatile)`` = ``predictor.predict_proba(row)["volatile"]``;
3. label ``volatile`` when ``P(volatile) >= ML_VOL_VERDICT_THRESHOLD`` (0.5),
   else ``calm``; anything unresolvable → ``unknown`` (fail-permissive).

**Do NOT substitute ``src/runtime/regime/vol_detector.py`` here.** The frozen
edge detector is a DIFFERENT label whose own docstring records that the authored
cells "LOSE money under the frozen label" — splitting the harness on it would
reproduce a population live never gates on and would look like a fix while being
a second, opposite mismatch.

HOW THE LABELS ARE PRODUCED
---------------------------
The head's feature row is the ``market_features`` row for that bar (for
``btc-regime-15m-lgbm-fc-pcv-v1``: 7 base columns + the 6 frozen-Chronos
``fc_*`` columns). Those frames are already built on the trainer, with ``fc_*``
joined — so the replay scores the **built dataset** and needs no torch / Chronos
re-run. Head resolution and thresholding go through the router's OWN resolvers
(``discover_advisory_stage_regime_specs`` / ``_ml_vol_verdict_threshold``), so a
head swap (BTC's advisory head was already swapped once, 2026-07-20) is tracked
automatically instead of drifting against a hardcoded id.

TWO FIDELITY CAVEATS — both reported in the output, neither hidden
-----------------------------------------------------------------
1. **In-sample over the head's training window.** The production artifact is fit
   on the full history, so replayed labels for bars inside that window come from
   a model that saw them. The label is a market-state label rather than a
   performance prediction, so this is far weaker than in-sample backtest bias —
   but it is real and is stamped into the manifest as ``in_sample: true``.
2. **Serve-path difference.** Live builds the feature row from live candles
   (``feature_row_for_predictor``); the replay reads the offline builder's row.
   They are designed to agree (S-MLOPT-S17 train/serve parity) but are not the
   same code path.

Caveat 2 (and, empirically, much of 1) is TESTABLE, which is what ``verify``
mode is for: the live gate already writes its resolved label to the audit log
(``regime_ml_vol_shadow`` / ``regime_hard_gate``), so replayed labels can be
compared against live ones over the overlap. **Run ``verify`` before trusting a
label file for cell grading** — an unvalidated replay is exactly the "green but
measuring the wrong population" failure this tool exists to end.

Research only (Tier-1). Reads a built dataset + the model registry; writes a
labels JSONL. Never touches config, the order path, or live state.

Usage::

    # 1. replay (on the trainer, where the registry + built dataset live)
    python scripts/research/ml_vol_label_replay.py replay \\
        --symbol BTCUSDT \\
        --dataset datasets-out/market_features/BTCUSDT/15m/v520/data.jsonl \\
        --out /tmp/btc_vol_labels.jsonl

    # 2. verify against the live gate's own audit rows BEFORE using it
    python scripts/research/ml_vol_label_replay.py verify \\
        --labels /tmp/btc_vol_labels.jsonl --audit /tmp/regime_ml_vol_shadow.jsonl

    # 3. then feed it to the trend harness for a 2-D grade
    python scripts/research/regime_tag_emitted.py --trades ... --data ... \\
        --vol-labels /tmp/btc_vol_labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The label vocabulary is the router's, imported rather than restated so a
# rename cannot silently desync the harness from the gate.
from src.runtime.regime.vol_detector import (  # noqa: E402
    VOL_CALM,
    VOL_UNKNOWN,
    VOL_VOLATILE,
)

_VOLATILE_CLASS = "volatile"  # the head's class label whose prob drives the verdict


# ---------------------------------------------------------------------------
# head resolution — the ROUTER's own resolvers, never a hardcoded model id
# ---------------------------------------------------------------------------


def resolve_advisory_head(
    symbol: str,
    *,
    model_id: Optional[str] = None,
    registry_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the head whose ``P(volatile)`` the LIVE gate would use for ``symbol``.

    Delegates to ``ml_vol_verdict.discover_advisory_stage_regime_specs`` — the
    same advisory-stage, prefer-non-yz resolution the decision path runs — so
    this tool follows a promotion/demotion instead of pinning a stale id.

    ``model_id`` overrides the resolution to replay one specific head (e.g. to
    reproduce the label a since-swapped head produced at authoring time). That
    path reads the registry entry directly and does NOT require advisory stage,
    because a historically-authoritative head may since have been demoted.

    Raises ``RuntimeError`` with a specific reason rather than degrading to
    ``unknown``: the live gate is fail-permissive by design, but a research
    replay that silently produced all-``unknown`` labels would be precisely the
    vacuous-artifact failure mode.
    """
    from pathlib import Path as _Path

    from ml.registry.model_registry import ModelRegistry

    if model_id:
        from ml.shadow.backfill import _instantiate_predictor  # noqa: PLC2701
        from ml.shadow.factory import DEFAULT_REGISTRY_ROOT

        root = _Path(registry_root or DEFAULT_REGISTRY_ROOT)
        registry = ModelRegistry(root)
        entry = registry.get(model_id)
        predictor = _instantiate_predictor(entry, registry_root=root)
        spec = getattr(predictor, "regime_spec", None)
        if not isinstance(spec, Mapping):
            raise RuntimeError(
                f"model {model_id!r} carries no regime_spec — it is not a regime "
                "head and has no P(volatile) to read"
            )
        return {
            "model_id": model_id,
            "predictor": predictor,
            "symbol": spec.get("symbol"),
            "timeframe": spec.get("timeframe"),
            "stage": entry.target_deployment_stage,
            "resolution": "explicit_model_id",
        }

    from src.runtime.regime.ml_vol_verdict import (  # noqa: PLC2701
        _advisory_entry_for_symbol,
        discover_advisory_stage_regime_specs,
    )

    specs = discover_advisory_stage_regime_specs(force=True)
    if not specs:
        raise RuntimeError(
            "no advisory-stage regime heads resolved from the registry — check "
            "the registry root is readable and that a head is at stage "
            "'advisory' (a shadow-stage head is deliberately NOT read here, "
            "mirroring the live gate)"
        )
    entry = _advisory_entry_for_symbol(symbol, specs)
    if entry is None:
        raise RuntimeError(
            f"no ADVISORY regime head covers symbol {symbol!r} — live resolves "
            f"'unknown' for it and the gate keeps the frozen label, so there is "
            f"no ML vol axis to replay. Advisory heads present: "
            f"{sorted({str(v.get('model_id')) for v in specs.values()})}"
        )
    return {
        "model_id": entry.get("model_id"),
        "predictor": entry.get("predictor"),
        "symbol": entry.get("symbol"),
        "timeframe": entry.get("timeframe"),
        "stage": "advisory",
        "resolution": "advisory_for_symbol",
    }


def live_threshold() -> float:
    """The live ``P(volatile) >= tau`` threshold (``ML_VOL_VERDICT_THRESHOLD``)."""
    from src.runtime.runtime_flags import _ml_vol_verdict_threshold  # noqa: PLC2701

    return float(_ml_vol_verdict_threshold())


def label_for_p(p_volatile: Optional[float], threshold: float) -> str:
    """Map ``P(volatile)`` to the router's label. Identical rule to the gate."""
    if p_volatile is None:
        return VOL_UNKNOWN
    return VOL_VOLATILE if float(p_volatile) >= threshold else VOL_CALM


# ---------------------------------------------------------------------------
# dataset IO
# ---------------------------------------------------------------------------


def iter_dataset_rows(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield each built-dataset row (JSONL). Malformed lines are counted, not skipped silently."""
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{lineno} is not valid JSON ({exc}). Refusing to "
                    "silently drop rows — a labels file with holes would grade "
                    "cells on a partial population."
                ) from exc


def _unwrap(predictor: Any) -> Any:
    """Return the base predictor (a ShadowPredictor wraps the real one)."""
    return getattr(predictor, "wrapped", None) or predictor


def _score_rows(
    predictor: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    batch: bool = True,
    batch_check_n: int = 32,
) -> Tuple[List[Optional[float]], Dict[str, Any]]:
    """Return ``P(volatile)`` per row plus a diagnostics dict.

    Scores in one batched booster call when possible (per-row ``predict_proba``
    over ~100k bars is needlessly slow), then **verifies the batch path against
    the canonical per-row ``predict_proba``** on a sample. A mismatch aborts
    rather than emitting labels from an unvalidated fast path — the batch exists
    for speed, never for a different answer.
    """
    base = _unwrap(predictor)
    proba_fn = getattr(base, "predict_proba", None)
    if proba_fn is None:
        raise RuntimeError(
            f"predictor {type(base).__name__} has no predict_proba — cannot read "
            "P(volatile)"
        )
    class_labels = [str(c) for c in (getattr(base, "class_labels", None) or ())]
    if _VOLATILE_CLASS not in class_labels:
        raise RuntimeError(
            f"head's class labels {class_labels!r} do not include "
            f"{_VOLATILE_CLASS!r} — this is not a 2-class vol regime head, so "
            "there is no P(volatile) to threshold"
        )

    diag: Dict[str, Any] = {"path": "per_row", "batch_verified": False}

    if not batch:
        return [_p_from_proba(proba_fn, r) for r in rows], diag

    try:
        import numpy as np  # noqa: PLC0415

        from ml.predictors.lightgbm import _encode_row  # noqa: PLC2701

        feature_cols = base._feature_columns  # noqa: SLF001
        cat_cols = base._categorical_columns  # noqa: SLF001
        cat_maps = base._cat_mappings  # noqa: SLF001
        booster = base._booster  # noqa: SLF001
        vol_idx = class_labels.index(_VOLATILE_CLASS)

        matrix = np.asarray(
            [_encode_row(r, feature_cols, cat_cols, cat_maps) for r in rows],
            dtype=np.float64,
        )
        out = booster.predict(matrix)
        scores: List[Optional[float]] = [float(o[vol_idx]) for o in out]
        diag["path"] = "batched"
    except Exception as exc:  # noqa: BLE001 — fall back to the canonical path
        diag["batch_fallback_reason"] = f"{type(exc).__name__}: {exc}"
        return [_p_from_proba(proba_fn, r) for r in rows], diag

    # Batch-vs-canonical agreement check on a stride-sampled subset.
    if rows:
        step = max(1, len(rows) // max(1, batch_check_n))
        max_delta = 0.0
        checked = 0
        for i in range(0, len(rows), step):
            ref = _p_from_proba(proba_fn, rows[i])
            got = scores[i]
            if ref is None or got is None:
                continue
            max_delta = max(max_delta, abs(float(ref) - float(got)))
            checked += 1
        diag["batch_check_rows"] = checked
        diag["batch_max_abs_delta"] = max_delta
        if checked and max_delta > 1e-9:
            raise RuntimeError(
                f"batched scoring disagrees with per-row predict_proba "
                f"(max |delta| = {max_delta:.3e} over {checked} sampled rows). "
                "Refusing to emit labels from an unverified fast path — rerun "
                "with --no-batch."
            )
        diag["batch_verified"] = bool(checked)
    return scores, diag


def _p_from_proba(proba_fn: Any, row: Mapping[str, Any]) -> Optional[float]:
    try:
        proba = proba_fn(row)
    except Exception:  # noqa: BLE001 — one unscorable bar must not kill the run
        return None
    if not proba:
        return None
    val = proba.get(_VOLATILE_CLASS)
    return float(val) if val is not None else None


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def run_replay(
    *,
    dataset: Path,
    symbol: str,
    out_path: Path,
    model_id: Optional[str] = None,
    registry_root: Optional[str] = None,
    threshold: Optional[float] = None,
    batch: bool = True,
) -> Dict[str, Any]:
    """Score every dataset bar and write ``{ts, p_volatile, vol_regime}`` JSONL.

    Returns a manifest dict (also written to ``<out>.manifest.json``) carrying
    the head, threshold, row/label counts, and the fidelity flags. The counts
    are the point: a labels file whose ``labelled`` is 0, or whose split is
    100/0, is vacuous and must not be used to grade a cell — so the numbers that
    make the artifact meaningful travel WITH it rather than living only in a
    console log that nobody re-reads.
    """
    head = resolve_advisory_head(
        symbol, model_id=model_id, registry_root=registry_root
    )
    tau = float(threshold) if threshold is not None else live_threshold()

    rows = list(iter_dataset_rows(dataset))
    if not rows:
        raise RuntimeError(
            f"dataset {dataset} yielded 0 rows — nothing to label. A zero-row "
            "labels file would silently grade every cell as 'unknown'."
        )

    scores, score_diag = _score_rows(head["predictor"], rows, batch=batch)

    counts = {VOL_CALM: 0, VOL_VOLATILE: 0, VOL_UNKNOWN: 0}
    missing_ts = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row, p in zip(rows, scores):
            ts = row.get("ts")
            if not ts:
                missing_ts += 1
                continue
            lab = label_for_p(p, tau)
            counts[lab] = counts.get(lab, 0) + 1
            fh.write(json.dumps({
                "ts": str(ts),
                "symbol": str(head.get("symbol") or symbol).upper(),
                "timeframe": str(head.get("timeframe") or ""),
                "model_id": head.get("model_id"),
                "p_volatile": None if p is None else round(float(p), 8),
                "vol_regime": lab,
            }) + "\n")

    labelled = counts[VOL_CALM] + counts[VOL_VOLATILE]
    manifest: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": "scripts/research/ml_vol_label_replay.py",
        "dataset": str(dataset),
        "symbol": str(symbol).upper(),
        "head": {
            "model_id": head.get("model_id"),
            "symbol": head.get("symbol"),
            "timeframe": head.get("timeframe"),
            "stage": head.get("stage"),
            "resolution": head.get("resolution"),
        },
        "threshold": tau,
        "threshold_source": (
            "explicit --threshold" if threshold is not None
            else "ML_VOL_VERDICT_THRESHOLD (live resolver)"
        ),
        "rows_in": len(rows),
        "rows_missing_ts": missing_ts,
        "labelled": labelled,
        "counts": counts,
        "volatile_pct": (
            round(100.0 * counts[VOL_VOLATILE] / labelled, 2) if labelled else None
        ),
        "ts_first": str(rows[0].get("ts")) if rows else None,
        "ts_last": str(rows[-1].get("ts")) if rows else None,
        "scoring": score_diag,
        # Fidelity flags travel with the artifact — see the module docstring.
        "fidelity": {
            "in_sample": True,
            "in_sample_note": (
                "The production artifact is fit on the full history, so labels "
                "for bars inside its training window come from a model that saw "
                "them. Weaker than in-sample backtest bias (this is a "
                "market-state label, not a performance prediction) but real."
            ),
            "serve_path_note": (
                "Live builds the feature row from live candles via "
                "feature_row_for_predictor; this reads the offline builder's "
                "row. Designed to agree (S-MLOPT-S17) but not the same code "
                "path — run `verify` against live audit rows before grading."
            ),
            "live_verified": False,
        },
    }
    Path(str(out_path) + ".manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# verify — does the replay reproduce the LIVE label?
# ---------------------------------------------------------------------------


def load_labels(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load a labels JSONL into ``{ts: row}``."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in iter_dataset_rows(path):
        ts = row.get("ts")
        if ts:
            out[str(ts)] = row
    return out


def _bar_key(ts: str, labels_sorted: Sequence[str]) -> Optional[str]:
    """Return the label bar at/just-before ``ts`` (as-of, never a future bar)."""
    import bisect

    idx = bisect.bisect_right(labels_sorted, ts) - 1
    return labels_sorted[idx] if idx >= 0 else None


def run_verify(*, labels_path: Path, audit_path: Path) -> Dict[str, Any]:
    """Compare replayed labels against the LIVE gate's own recorded label.

    ``audit_path`` is a JSONL of ``regime_ml_vol_shadow`` / ``regime_hard_gate``
    rows (from ``/api/diag/audit_query?event=...``). Each carries the label the
    live gate resolved (``vol_regime_ml``, or ``vol_regime`` when
    ``vol_label_source == "ml"``), so agreement over the overlap is a direct
    empirical test of the serve-path caveat.

    This is the check that turns "the replay looks plausible" into "the replay
    reproduces live". A low agreement rate is a FINDING, not a rounding issue —
    it means the offline feature row differs from the served one.
    """
    labels = load_labels(labels_path)
    if not labels:
        raise RuntimeError(f"{labels_path} carried 0 labels — nothing to verify")
    keys = sorted(labels)

    total = 0
    comparable = 0
    agree = 0
    disagreements: List[Dict[str, Any]] = []
    no_live_label = 0
    no_bar = 0

    for rec in iter_dataset_rows(audit_path):
        total += 1
        ts = rec.get("ts") or rec.get("timestamp") or rec.get("predicted_at_utc")
        live = rec.get("vol_regime_ml")
        if live is None and str(rec.get("vol_label_source") or "") == "ml":
            live = rec.get("vol_regime")
        if not ts or live not in (VOL_CALM, VOL_VOLATILE):
            no_live_label += 1
            continue
        bar = _bar_key(str(ts), keys)
        if bar is None:
            no_bar += 1
            continue
        mine = labels[bar].get("vol_regime")
        if mine not in (VOL_CALM, VOL_VOLATILE):
            no_bar += 1
            continue
        comparable += 1
        if mine == live:
            agree += 1
        elif len(disagreements) < 25:
            disagreements.append({
                "audit_ts": str(ts),
                "label_bar_ts": bar,
                "live": live,
                "replayed": mine,
                "replayed_p_volatile": labels[bar].get("p_volatile"),
                "strategy": rec.get("strategy"),
                "symbol": rec.get("symbol"),
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labels_path": str(labels_path),
        "audit_path": str(audit_path),
        "audit_rows": total,
        "audit_rows_without_ml_label": no_live_label,
        "audit_rows_without_matching_bar": no_bar,
        "comparable": comparable,
        "agree": agree,
        "agreement_pct": (
            round(100.0 * agree / comparable, 2) if comparable else None
        ),
        "disagreement_sample": disagreements,
        # An empty comparison is NOT a pass. Say so in the artifact.
        "verdict": (
            "no_overlap_nothing_verified" if not comparable
            else "verified" if agree == comparable
            else "disagreements_found"
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Replay the live ML vol axis offline (per-bar calm/volatile)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("replay", help="score a built dataset into per-bar vol labels")
    r.add_argument("--symbol", required=True, help="e.g. BTCUSDT (head resolved per-SYMBOL, as live does)")
    r.add_argument("--dataset", required=True, help="market_features data.jsonl the head's features live in")
    r.add_argument("--out", required=True, help="output labels JSONL")
    r.add_argument("--model-id", default=None,
                   help="replay ONE specific head instead of the symbol's current advisory head")
    r.add_argument("--registry-root", default=None)
    r.add_argument("--threshold", type=float, default=None,
                   help="override ML_VOL_VERDICT_THRESHOLD (default: the live resolver)")
    r.add_argument("--no-batch", action="store_true",
                   help="score row-by-row via predict_proba (slower; the canonical path)")
    r.add_argument("--json", action="store_true")

    v = sub.add_parser("verify", help="compare replayed labels against the LIVE gate's audit rows")
    v.add_argument("--labels", required=True)
    v.add_argument("--audit", required=True,
                   help="JSONL of regime_ml_vol_shadow / regime_hard_gate rows")
    v.add_argument("--json", action="store_true")
    v.add_argument("--min-agreement", type=float, default=None,
                   help="exit 1 when agreement%% falls below this (CI/gate use)")

    a = p.parse_args(argv)

    if a.cmd == "replay":
        m = run_replay(
            dataset=Path(a.dataset), symbol=a.symbol, out_path=Path(a.out),
            model_id=a.model_id, registry_root=a.registry_root,
            threshold=a.threshold, batch=not a.no_batch,
        )
        if a.json:
            print(json.dumps(m, indent=2))
        else:
            h = m["head"]
            print(f"head      {h['model_id']} ({h['symbol']} {h['timeframe']}, "
                  f"stage={h['stage']}, via {h['resolution']})")
            print(f"threshold {m['threshold']}  [{m['threshold_source']}]")
            print(f"bars      {m['rows_in']} in -> {m['labelled']} labelled "
                  f"({m['ts_first']} -> {m['ts_last']})")
            print(f"split     calm={m['counts'][VOL_CALM]} "
                  f"volatile={m['counts'][VOL_VOLATILE]} "
                  f"unknown={m['counts'][VOL_UNKNOWN]}  "
                  f"(volatile {m['volatile_pct']}%)")
            print(f"scoring   {m['scoring']}")
            print(f"wrote     {a.out} (+ .manifest.json)")
            print("NOTE: fidelity.live_verified=false — run `verify` against live "
                  "audit rows before grading any cell with this file.")
        # A vacuous artifact must fail loudly, not be reported as a success.
        if m["labelled"] == 0:
            print("ERROR: 0 bars labelled — this file cannot grade anything.",
                  file=sys.stderr)
            return 2
        return 0

    res = run_verify(labels_path=Path(a.labels), audit_path=Path(a.audit))
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"audit rows        {res['audit_rows']} "
              f"(no ML label: {res['audit_rows_without_ml_label']}, "
              f"no matching bar: {res['audit_rows_without_matching_bar']})")
        print(f"comparable        {res['comparable']}")
        print(f"agreement         {res['agree']}/{res['comparable']} "
              f"= {res['agreement_pct']}%")
        print(f"verdict           {res['verdict']}")
        for d in res["disagreement_sample"][:10]:
            print(f"  disagree {d['audit_ts']} live={d['live']} "
                  f"replayed={d['replayed']} p={d['replayed_p_volatile']}")
    if res["verdict"] == "no_overlap_nothing_verified":
        print("ERROR: nothing was compared — this is NOT a pass. Widen the audit "
              "window or check the labels cover the audit period.", file=sys.stderr)
        return 2
    if a.min_agreement is not None and (res["agreement_pct"] or 0.0) < a.min_agreement:
        print(f"ERROR: agreement {res['agreement_pct']}% < required "
              f"{a.min_agreement}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
