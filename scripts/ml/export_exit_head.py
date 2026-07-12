#!/usr/bin/env python3
"""M20 E2 — export the exit-head LightGBM artifact for the live shadow.

Trains the E1.5-passing head on ALL harness rows of an E0 family dataset
and writes a single self-contained JSON artifact
(``{model_id, family, tf, stage, features, shape, booster_txt, ...}``) that
``src/runtime/exit_head_shadow.py`` loads on the live VM. Written into the
trainer-mirror staging dir so ``publish_trainer_mirror.sh`` delivers it over
the standard trainer→live channel.

Trainer-side (Tier-1 tooling). The live influence of this model is gated by
stage: the artifact declares ``stage: "shadow"`` and the live scorer is
observe-only regardless — E3 graduation is Tier-3.

Usage (trainer):
  .venv/bin/python3 scripts/ml/export_exit_head.py \
      --family-dir datasets-out/exit_head/1h/donchian --tf 1h \
      --out runtime_logs/trainer_mirror/exit_head/exit-head-donchian-1h-v1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_exit_head import FEATURES, load_rows, train_model  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family-dir", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--model-id", default="exit-head-donchian-1h-v1")
    ap.add_argument("--tau", type=float, default=0.10)
    ap.add_argument("--below-r", type=float, default=0.5)
    ap.add_argument("--stage", default="shadow", choices=["shadow", "advisory"],
                    help="artifact stage; only 'advisory' can influence a live "
                         "exit (operator promotion gate - E3)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv[1:])

    fam_dir = Path(a.family_dir)
    rows = [r for r in load_rows(fam_dir / "rows.jsonl") if r["source"] == "harness"]
    if not rows:
        print("no harness rows", file=sys.stderr)
        return 1
    model = train_model(rows)
    trades = len({r["trade_key"] for r in rows})
    symbols = sorted({r.get("symbol") for r in rows if r.get("symbol")})
    artifact = {
        "model_id": a.model_id,
        "family": fam_dir.name,
        "tf": a.tf,
        "stage": a.stage,
        "symbols": symbols,
        "features": FEATURES,
        "shape": {"policy": "below_half_r", "tau": a.tau, "below_r": a.below_r},
        "booster_txt": model.booster_.model_to_string(),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_rows": len(rows),
        "train_trades": trades,
        "evidence": "docs/research/M20-exit-refinement-2026-07-12.md § 9",
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact))
    print(f"{a.model_id}: {len(rows)} rows / {trades} trades -> {out} "
          f"({out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
