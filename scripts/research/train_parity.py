#!/usr/bin/env python3
# wiring: research/queue/RQ-20261015-8b93-01.yaml + -02.yaml (run.command) -> research-script-run-trainer.yml (reference) and
#         research-script-run-kaggle-cpu.yml (off-VM); the dispatcher fires both. Row KAGGLE-TRAIN-RUNNER-FIRST.
"""Training-backend PARITY gate: does a manifest trained OFF the trainer VM match the trainer-trained one?

WHY (row KAGGLE-TRAIN-RUNNER-FIRST, operator 2026-10-15 "Train on free runner"). The trainer VM is 1 OCPU and
saturated; the plan (docs/plans/KAGGLE-TRAINING-PIPELINE-DESIGN-2026-10-13.md § 4 step 4) moves market-data
manifests to free off-VM compute. Nothing trained off-VM may enter the registry until the SAME manifest on the
SAME rows gives the SAME model within a tolerance declared BEFORE the run. This script is that measurement.

HOW. One script, run once per backend by two queue units:

  1. reference unit (trainer)   python3 scripts/research/train_parity.py --manifest M --cutoff C --backend trainer ...
  2. off-VM unit (kaggle_cpu)   ... --backend kaggle_cpu --reference-unit <unit 1>   (requires_result: unit 1 pass)

Each run (a) reads the manifest's dataset shard (on Kaggle from $ML_DATA_ROOT, the published metis-market-data copy;
on the trainer from datasets-out/), (b) keeps ONLY rows with ts < --cutoff, byte-for-byte, so a shard the nightly
rebuild has since extended still yields the identical input (its sha256 is recorded and must match across
backends), (c) trains through the production path `ml.experiments.runner.run_experiment(register=False)` -- never
a re-implementation, never the registry -- and (d) records metrics, the per-row predicted class on the eval
split, library versions and platform.

The off-VM run compares itself to the newest reference record (bundled into the kernel via run.bundle_paths) and
applies the rule below. Rule (registered in the unit, restated here so the code and the unit cannot drift --
`RULE` is printed into every verdict):

  INDETERMINATE  either run failed, or the input sha256 / row counts differ (not the same rows: parity undefined)
  PASS           same input AND every count metric (n_eval, support_*) EXACT AND every score metric within
                 ABS_TOL AND eval-prediction agreement >= MIN_AGREEMENT
  FAIL           same input and any of the above out of tolerance

Tier-1 research tooling: writes only --out. Registers nothing, promotes nothing, touches no config.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

SCHEMA = 1
RECORD = "train_parity.json"
ABS_TOL = 0.01          # score metrics (accuracy, f1, precision, recall): |off-VM - trainer| <= 0.01
MIN_AGREEMENT = 0.99    # share of eval rows given the same predicted class
COUNT_PREFIXES = ("n_eval", "support_", "n_train")
RULE = (f"PASS iff same input sha256 + rows, count metrics exact, every score metric |delta| <= {ABS_TOL}, "
        f"eval-prediction agreement >= {MIN_AGREEMENT}; FAIL otherwise on the same input; INDETERMINATE if the "
        "input differs or either run failed")


def _ts_seconds(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) / 1000.0 if float(v) > 1e11 else float(v)      # epoch ms or s
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s) / 1000.0 if float(s) > 1e11 else float(s)
    except ValueError:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def data_root(cli_root: Optional[str]) -> Path:
    """$ML_DATA_ROOT/datasets-out (a Kaggle kernel with the published dataset mounted) else --datasets-root."""
    env = os.environ.get("ML_DATA_ROOT")
    if env:
        return Path(env) / "datasets-out"
    return Path(cli_root or "datasets-out")


def stage(src_dir: Path, dst_dir: Path, cutoff_s: float, time_col: str = "ts") -> Dict[str, Any]:
    """Copy rows with ts < cutoff byte-for-byte into dst_dir/data.jsonl; return the input fingerprint."""
    src = src_dir / "data.jsonl"
    if not src.is_file():
        raise FileNotFoundError(f"dataset absent: {src}")
    dst_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    n_file = n_used = n_badts = 0
    first = last = None
    with src.open("rb") as fin, (dst_dir / "data.jsonl").open("wb") as fout:
        for line in fin:
            if not line.strip():
                continue
            n_file += 1
            try:
                t = _ts_seconds(json.loads(line).get(time_col))
            except ValueError:
                t = None
            if t is None:
                n_badts += 1
                continue
            if t >= cutoff_s:
                continue
            if not line.endswith(b"\n"):
                line += b"\n"
            fout.write(line)
            h.update(line)
            n_used += 1
            first = t if first is None else min(first, t)
            last = t if last is None else max(last, t)
    meta = src_dir / "metadata.json"
    if meta.is_file():
        shutil.copy2(meta, dst_dir / "metadata.json")
    iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat() if t is not None else None  # noqa: E731
    return {"rows_in_file": n_file, "rows_used": n_used, "rows_bad_ts": n_badts, "sha256_used": h.hexdigest(),
            "first_ts": iso(first), "last_ts": iso(last)}


def _versions() -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for mod in ("lightgbm", "numpy", "sklearn", "hmmlearn"):
        try:
            out[mod] = getattr(__import__(mod), "__version__", None)
        except Exception:  # noqa: BLE001  # allow-silent: an absent optional lib is recorded as None in the output, not hidden
            out[mod] = None
    return out


def train_once(manifest_path: Path, root: Path, cutoff: str, workdir: Path) -> Dict[str, Any]:
    from ml.experiments.runner import (  # noqa: PLC0415
        _load_jsonl, _resolve_callable, dataset_projection_columns, run_experiment)
    from ml.experiments.splitters import split as split_rows  # noqa: PLC0415
    from ml.manifest import TrainingManifest  # noqa: PLC0415

    manifest = TrainingManifest.from_yaml(manifest_path)
    rel = Path(manifest.dataset.family) / manifest.dataset.symbol_scope / manifest.dataset.timeframe / manifest.dataset.version
    time_col = str(manifest.evaluator_config.get("time_column") or "ts")
    cutoff_s = _ts_seconds(cutoff)
    if cutoff_s is None:
        raise ValueError(f"unparseable --cutoff {cutoff!r}")
    staged_root = workdir / "datasets-out"
    fp = stage(root / rel, staged_root / rel, cutoff_s, time_col)
    t0 = time.monotonic()
    artifacts, _ = run_experiment(manifest_path=manifest_path, datasets_root=staged_root,
                                  experiments_root=workdir / "experiments", registry_root=workdir / "registry",
                                  register=False)
    wall = time.monotonic() - t0
    model_state = json.loads(artifacts.model_state_path.read_text())
    preds: List[str] = []
    labels: List[str] = list(model_state.get("class_labels") or [])
    if manifest.evaluator_config.get("split_strategy", "holdout") in ("holdout", "time_aware_holdout"):
        evaluator = _resolve_callable(manifest.evaluator)()
        predictor = evaluator._resolve_predictor(model_state)
        target = manifest.evaluator_config.get("target_column") or manifest.trainer_config.get("target_column")
        rows = _load_jsonl(staged_root / rel / "data.jsonl", keep=dataset_projection_columns(manifest))
        _, eval_rows = split_rows(rows, manifest.evaluator_config)
        for r in eval_rows:
            if target and str(r.get(target) or "").strip():
                preds.append(str(predictor.predict_label(r)))
    # one character per eval row (index into class_labels; '?' for an undeclared class) keeps the committed record small
    codes = "".join(str(labels.index(p)) if p in labels and labels.index(p) < 10 else "?" for p in preds)
    return {"dataset": {"rel_path": f"datasets-out/{rel.as_posix()}/data.jsonl", **fp},
            "metrics": {k: float(v) for k, v in artifacts.metrics.items() if isinstance(v, (int, float))},
            "eval_predictions": codes, "class_labels": labels,
            "eval_predictions_sha256": hashlib.sha256(codes.encode()).hexdigest(),
            "model_state_sha256": hashlib.sha256(artifacts.model_state_path.read_bytes()).hexdigest(),
            "train_wall_s": round(wall, 2)}


def compare(ref: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
    """Apply RULE. Returns {verdict, reasons, deltas, agreement}."""
    reasons: List[str] = []
    if ref.get("error") or cur.get("error"):
        return {"verdict": "indeterminate", "reasons": ["a run failed: " + str(ref.get("error") or cur.get("error"))],
                "deltas": {}, "agreement": None}
    rd, cd = ref["dataset"], cur["dataset"]
    if (rd["sha256_used"], rd["rows_used"]) != (cd["sha256_used"], cd["rows_used"]):
        return {"verdict": "indeterminate", "reasons": [
            f"input differs: trainer {rd['rows_used']} rows sha {rd['sha256_used'][:12]} vs off-VM {cd['rows_used']} "
            f"rows sha {cd['sha256_used'][:12]} -- not the same rows, parity undefined"], "deltas": {}, "agreement": None}
    deltas: Dict[str, float] = {}
    for k in sorted(set(ref["metrics"]) | set(cur["metrics"])):
        a, b = ref["metrics"].get(k), cur["metrics"].get(k)
        if a is None or b is None:
            reasons.append(f"metric {k} missing on one side")
            continue
        d = b - a
        deltas[k] = round(d, 6)
        if k.startswith(COUNT_PREFIXES):
            if d != 0:
                reasons.append(f"count {k} differs: {a} vs {b}")
        elif abs(d) > ABS_TOL:
            reasons.append(f"{k} |delta| {abs(d):.4f} > {ABS_TOL}")
    rp, cp = ref.get("eval_predictions") or "", cur.get("eval_predictions") or ""
    if ref.get("class_labels") != cur.get("class_labels"):
        reasons.append(f"class_labels differ: {ref.get('class_labels')} vs {cur.get('class_labels')}")
    agreement = None
    if rp and len(rp) == len(cp):
        agreement = sum(1 for x, y in zip(rp, cp) if x == y) / len(rp)
        if agreement < MIN_AGREEMENT:
            reasons.append(f"eval-prediction agreement {agreement:.4f} < {MIN_AGREEMENT}")
    else:
        reasons.append(f"eval predictions not comparable (trainer {len(rp)} vs off-VM {len(cp)})")
    return {"verdict": "fail" if reasons else "pass", "reasons": reasons, "deltas": deltas, "agreement": agreement}


def latest_reference(unit: str, repo: Path = _REPO) -> Optional[Dict[str, Any]]:
    """Newest successful train_parity.json the reference unit landed under comms/research/<unit>/<run_id>/."""
    best = None
    for p in sorted((repo / "comms" / "research" / unit).glob(f"*/{RECORD}")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if d.get("error"):
            continue
        if best is None or str(d.get("generated_at")) > str(best.get("generated_at")):
            best = d
    return best


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True, help="manifest id under ml/configs/ (no .yaml)")
    ap.add_argument("--cutoff", required=True, help="keep rows with ts < this (ISO-8601 UTC)")
    ap.add_argument("--backend", required=True, help="label recorded in the output (trainer / kaggle_cpu / runner)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--unit", default=None)
    ap.add_argument("--reference-unit", default=None, help="compare against this unit's newest record (off-VM side)")
    ap.add_argument("--datasets-root", default=None, help="default datasets-out (ignored when $ML_DATA_ROOT is set)")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = _REPO / "ml" / "configs" / f"{a.manifest}.yaml"
    rec: Dict[str, Any] = {"schema": SCHEMA, "unit": a.unit, "manifest": a.manifest, "backend": a.backend,
                           "cutoff": a.cutoff, "generated_at": datetime.now(timezone.utc).isoformat(),
                           "platform": {"machine": platform.machine(), "system": platform.system(),
                                        "python": platform.python_version(), "cpus": os.cpu_count()},
                           "versions": _versions(), "rule": RULE}
    unit_file = _REPO / "research" / "queue" / f"{a.unit}.yaml" if a.unit else None
    if unit_file is not None and unit_file.is_file():
        import yaml  # noqa: PLC0415
        declared = ((yaml.safe_load(unit_file.read_text()) or {}).get("train_parity") or {}).get("manifest")
        if declared != a.manifest:   # the router checked the published allowlist against train_parity.manifest
            rec["error"] = f"--manifest {a.manifest!r} != the unit's train_parity.manifest {declared!r}"
    with tempfile.TemporaryDirectory(prefix="train_parity_") as tmp:
        try:
            if rec.get("error"):
                raise RuntimeError(rec["error"])
            rec.update(train_once(manifest_path, data_root(a.datasets_root), a.cutoff, Path(tmp)))
        except Exception as exc:  # noqa: BLE001  # allow-silent: recorded in train_parity.json and graded producer_failed
            rec["error"] = f"{type(exc).__name__}: {exc}"
    (out / RECORD).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")

    if rec.get("error"):
        verdict = {"verdict": "not_applicable", "read_state": "producer_failed",
                   "population": {"n": None, "what": "eval rows"}, "note": rec["error"]}
    elif not a.reference_unit:
        n = int(rec["metrics"].get("n_eval", 0))
        ok = n >= 100 and rec["dataset"]["rows_used"] > 0
        verdict = {"verdict": "pass" if ok else "fail", "read_state": "measured",
                   "population": {"n": n, "what": f"eval rows of {a.manifest} trained on {a.backend}, ts < {a.cutoff}"},
                   "measurement": {"metrics": rec["metrics"], "dataset": rec["dataset"], "versions": rec["versions"],
                                   "platform": rec["platform"], "train_wall_s": rec["train_wall_s"]},
                   "note": f"reference run on {a.backend}: {'usable' if ok else 'unusable (n_eval < 100 or 0 rows)'} "
                           "as the parity reference"}
    else:
        ref = latest_reference(a.reference_unit)
        if ref is None:
            verdict = {"verdict": "not_applicable", "read_state": "no_data",
                       "population": {"n": None, "what": "reference runs"},
                       "note": f"no successful {RECORD} under comms/research/{a.reference_unit}/ in this checkout"}
        else:
            c = compare(ref, rec)
            (out / "parity.json").write_text(json.dumps({**c, "rule": RULE, "reference": {
                k: ref.get(k) for k in ("backend", "generated_at", "versions", "platform", "dataset", "metrics")},
                "candidate": {k: rec.get(k) for k in ("backend", "generated_at", "versions", "platform", "dataset",
                                                       "metrics")}}, indent=1, sort_keys=True) + "\n")
            verdict = {"verdict": c["verdict"], "read_state": "measured",
                       "population": {"n": len(rec.get("eval_predictions") or []),
                                      "what": f"eval rows of {a.manifest}, {ref.get('backend')} vs {a.backend}, ts < {a.cutoff}"},
                       "measurement": {"agreement": c["agreement"], "deltas": c["deltas"],
                                       "train_wall_s": {"reference": ref.get("train_wall_s"), "candidate": rec.get("train_wall_s")}},
                       "note": "; ".join(c["reasons"]) or "all metrics within tolerance"}
    (out / "verdict.json").write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: verdict[k] for k in ("verdict", "read_state", "note") if k in verdict}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
