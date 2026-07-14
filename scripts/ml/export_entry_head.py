#!/usr/bin/env python3
"""M21 E-3 / M18 Phase A — export a P_win ENTRY-head LightGBM artifact.

Trains the gated entry head (signal-bar features, ``first_touch_1r`` label)
on ALL harness entries of an E0 family dataset and writes the same
self-contained JSON artifact shape the exit head uses
(``{model_id, family, tf, stage, features, booster_txt, ...}``) into the
trainer-mirror staging dir; ``publish_trainer_mirror.sh`` delivers it over
the standard trainer→live channel and ``src/runtime/entry_head_pwin.py``
loads it on the live VM.

Trainer-side (Tier-1 tooling). The live consumer is OBSERVE-ONLY at Phase A
(the allocator-soak ``head_p_win`` annotation); any influence on selection
is the backtest-gated Phase B, and the artifact's declared ``stage`` rides
along for that gate exactly like the exit head's.

Usage (trainer):
  .venv/bin/python3 scripts/ml/export_entry_head.py \
      --family-dir runtime_logs/m21_entry_head_r3/<date>/ds/donchian_1h/donchian \
      --tf 1h --model-id entry-pwin-donchian-1h-v1 \
      --out runtime_logs/trainer_mirror/entry_head/entry-pwin-donchian-1h-v1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_entry_head as tehd  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family-dir", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--model-id", default="entry-pwin-donchian-1h-v1")
    ap.add_argument("--stage", default="shadow", choices=["shadow", "advisory"],
                    help="artifact stage; Phase A consumers are observe-only "
                         "regardless — 'advisory' is the Phase-B promotion gate")
    ap.add_argument("--target", default="first_touch_1r",
                    choices=["first_touch_1r", "reaches_2r"])
    ap.add_argument("--evidence", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv[1:])

    tehd.TARGET = a.target
    fam_dir = Path(a.family_dir)
    entries = [e for e in tehd.load_entries(fam_dir / "rows.jsonl")
               if e["source"] == "harness"]
    if not entries:
        print("no harness entries", file=sys.stderr)
        return 1
    missing = [f for f in tehd.FEATURES
               if f != "is_long" and all(e.get(f) is None for e in entries)]
    if missing:
        print(f"dataset lacks signal-bar features {missing} — rebuild with "
              "the post-Phase-A builder first", file=sys.stderr)
        return 2
    model = tehd.train_model(entries)
    symbols = sorted({e.get("symbol") for e in entries if e.get("symbol")})
    artifact = {
        "model_id": a.model_id,
        "kind": "entry_pwin",
        "family": fam_dir.name,
        "tf": a.tf,
        "stage": a.stage,
        "symbols": symbols,
        "features": tehd.FEATURES,
        "target": a.target,
        "booster_txt": model.booster_.model_to_string(),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_entries": len(entries),
        "base_rate": round(
            sum(int(e[a.target]) for e in entries) / len(entries), 4),
        "evidence": a.evidence or ("M21 E-3 rounds 1-2 2026-07-14 "
                                   "(sprint log S-M21-ENTRY-REFINEMENT-2026-07-13)"),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact))
    print(f"{a.model_id}: {len(entries)} entries -> {out} "
          f"({out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
