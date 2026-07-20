"""Dataset-unchanged retrain skip check (MB-20260720-FCPCV-RETRAIN-NOOP).

``run_training_cycle.sh`` calls this once per manifest before training:

    python scripts/ops/dataset_unchanged_check.py <manifest.yaml> \
        <datasets_root> <registry_root>

Prints exactly one token:

- ``SKIP``  — the manifest's pinned dataset ``data.jsonl`` has not been
  modified since the model's newest registered run AND the manifest file
  itself is older than that run: retraining would reproduce the same model
  byte-for-byte (the frozen-pin no-op class — btc-regime-15m-lgbm-fc-pcv-v1
  burned 16 identical nightly runs on a v520 dataset frozen since Jul 1,
  faking ``cross_run_stability=0.0``). The cycle logs it LOUDLY and skips.
- ``TRAIN`` — anything else: dataset rebuilt/refreshed (the nightly v002
  ``--overwrite`` path), manifest edited since the last run (hyperparam or
  pin change), model never trained, no registry entry, or ANY resolution
  error. Fail-open — a guard bug must never starve a legitimate retrain.

Deliberately mtime-based (no hashing): the nightly builder rewrites v002
with ``--overwrite`` every cycle, so a refreshed dataset always carries a
new mtime; a frozen experiment pin never does. A trainer-CODE change alone
does not re-enable training on a frozen pin — touch/bump the manifest (or
refresh the dataset) to force a retrain, which is the honest signal that
the frozen head is being deliberately revised.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import sys


def decide(manifest_path: str, datasets_root: str, registry_root: str) -> str:
    """Return ``SKIP`` or ``TRAIN`` (never raises)."""
    try:
        from pathlib import Path

        from ml.manifest import TrainingManifest

        m = TrainingManifest.from_yaml(Path(manifest_path))
        data = m.dataset.path_under(Path(datasets_root)) / "data.jsonl"
        if not data.is_file():
            return "TRAIN"  # missing dataset is the trainer's error to raise
        # Newest registered run for this model_id. The registry store holds
        # flat per-model JSONs whose FILENAME may not equal model_id — match
        # on content (the store is small; this runs once per manifest).
        last_at = None
        for p in glob.glob(os.path.join(registry_root, "*.json")):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:  # noqa: BLE001 — sibling artifacts are not entries
                continue
            if d.get("model_id") != m.model_id:
                continue
            for r in d.get("runs") or []:
                at = dt.datetime.fromisoformat(str(r["at"]))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=dt.timezone.utc)
                if last_at is None or at > last_at:
                    last_at = at
            break
        if last_at is None:
            return "TRAIN"  # never trained → definitely train
        ds_at = dt.datetime.fromtimestamp(
            data.stat().st_mtime, dt.timezone.utc
        )
        mf_at = dt.datetime.fromtimestamp(
            os.path.getmtime(manifest_path), dt.timezone.utc
        )
        if ds_at > last_at or mf_at > last_at:
            return "TRAIN"  # fresh data or an edited manifest
        return "SKIP"
    except Exception:  # noqa: BLE001 — fail-open, never starve a retrain
        return "TRAIN"


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("TRAIN")
        sys.exit(0)
    print(decide(sys.argv[1], sys.argv[2], sys.argv[3]))
