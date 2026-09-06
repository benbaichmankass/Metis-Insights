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

THE ``family`` TOKEN IS WHAT THE CONSUMING GUARD CHECKS — DECLARE IT.
``family`` defaults to the ``--family-dir`` basename, which is a TRAINING-ROUND
DIRECTORY NAME this repo does not control. That is correct only while the
directory happens to be named the same word the consuming unit declares, and it
fails silently-in-effect when it is not: the live in-distribution guard refuses
the artifact and scores nothing.

Measured 2026-09-06 (MI-154): the surviving E0 scalp rounds are laid out PER LEG
— ``runtime_logs/m20_exit_head/scalp_5m_20260814T151003Z/`` holds
``ict_scalp_sol_5m/``, ``ict_scalp_xrp_5m/``, ``ict_scalp_avax_5m/`` — so the
DERIVED token is ``ict_scalp_sol_5m``, while the ict_scalp consumer declares
``family="ict_scalp"`` and accepts only ``{"ict_scalp", "scalp"}``. Exporting a
scalp head without ``--family ict_scalp`` therefore produces an artifact that is
published, loaded, and then refused.

Pass ``--family`` whenever the round directory is not already the consumer's
token. Omitting it is byte-for-byte the legacy behaviour, so no existing round
moves. The CLI line states which basis was used (``declared`` vs
``derived_from_dir``) so a mismatch is diagnosable at EXPORT time rather than
only from a WARNING on the live VM hours later.

Usage (trainer):
  .venv/bin/python3 scripts/ml/export_exit_head.py \
      --family-dir datasets-out/exit_head/1h/donchian --tf 1h \
      --out runtime_logs/trainer_mirror/exit_head/exit-head-donchian-1h-v1.json

  # a per-leg round dir whose name is NOT the consumer's family token:
  .venv/bin/python3 scripts/ml/export_exit_head.py \
      --family-dir runtime_logs/m20_exit_head/scalp_15m_.../ict_scalp_sol_15m \
      --tf 15m --family ict_scalp \
      --out runtime_logs/trainer_mirror/exit_head/exit-head-ict_scalp-15m-v1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_exit_head as teh  # noqa: E402
from train_exit_head import load_rows, train_model  # noqa: E402


def training_window(rows):
    """The DATA bound of a training row set: {train_start, train_end, coverage}.

    BL-20260808-EXIT-HEAD-MANIFEST-RECORDS-NO-TRAINING-WINDOW. The exported
    artifact used to carry only ``trained_at`` — the wall-clock moment of
    FITTING, which is not the data bound and must never be substituted for it. A
    head fitted on 2026-07-12 could have used six months or three years of
    history, and nothing in the artifact said which, so a downstream replay could
    not state its own in-sample fraction (that is exactly what happened to the
    first measured replay, issue #8653).

    The window was always derivable here: every row carries ``bar_t``, the epoch
    seconds of its in-trade bar (written by ``build_exit_head_dataset.py``), so
    min/max over it IS the bound.

    HONEST-NULL, with its own coverage metric. Rows with no usable ``bar_t``
    yield ``None`` rather than a manufactured date, and ``train_window_coverage``
    reports the fraction that actually carried one — so a partially-stamped
    dataset cannot pass as a fully-measured window (the instrument-before-finding
    rule: a new measurement ships with the metric that says how much of it is
    real).
    """
    bar_ts = []
    for r in rows:
        t = r.get("bar_t")
        if t is None:
            continue
        try:
            bar_ts.append(int(t))
        except (TypeError, ValueError):
            continue

    def _iso(epoch_s):
        return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()

    return {
        "train_start": _iso(min(bar_ts)) if bar_ts else None,
        "train_end": _iso(max(bar_ts)) if bar_ts else None,
        "train_window_coverage": (round(len(bar_ts) / len(rows), 4)
                                  if rows else None),
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family-dir", required=True)
    ap.add_argument("--tf", required=True)
    ap.add_argument("--family", default=None,
                    help="family token to stamp on the artifact. DEFAULTS to the "
                         "--family-dir basename (unchanged legacy behaviour). Pass "
                         "it when the round's directory name is not the token the "
                         "consuming unit declares - see the module docstring.")
    ap.add_argument("--model-id", default="exit-head-donchian-1h-v1")
    ap.add_argument("--tau", type=float, default=0.10)
    ap.add_argument("--below-r", type=float, default=0.5)
    ap.add_argument("--stage", default="shadow", choices=["shadow", "advisory"],
                    help="artifact stage; only 'advisory' can influence a live "
                         "exit (operator promotion gate - E3)")
    # P4.2/P4.3 — export the retargeted peak-is-in head (extended features).
    ap.add_argument("--target", default="holding_pays",
                    choices=["holding_pays", "peak_is_in"])
    ap.add_argument("--features", default="base", choices=["base", "extended"])
    ap.add_argument("--policy", default="below_half_r",
                    choices=["below_half_r", "peak_full", "peak_winner"],
                    help="shape recorded on the artifact; inert at shadow "
                         "stage (the scorer only logs scores)")
    ap.add_argument("--evidence", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv[1:])

    teh.TARGET = a.target
    if a.features == "extended":
        teh.FEATURES = teh.FEATURES + teh.FEATURES_EXH
    FEATURES = teh.FEATURES

    fam_dir = Path(a.family_dir)
    # The family token is what the consuming unit's in-distribution guard checks
    # the artifact against. It is DECLARED when --family is given and DERIVED from
    # the directory name otherwise; the two are different facts and the CLI line
    # below says which was used, so a refused artifact can be diagnosed from the
    # export output rather than from the live WARNING.
    family = a.family if a.family else fam_dir.name
    family_basis = "declared" if a.family else "derived_from_dir"
    rows = [r for r in load_rows(fam_dir / "rows.jsonl") if r["source"] == "harness"]
    if not rows:
        print("no harness rows", file=sys.stderr)
        return 1
    model = train_model(rows)
    trades = len({r["trade_key"] for r in rows})
    symbols = sorted({r.get("symbol") for r in rows if r.get("symbol")})

    window = training_window(rows)

    artifact = {
        "model_id": a.model_id,
        "family": family,
        "tf": a.tf,
        "stage": a.stage,
        "symbols": symbols,
        "features": FEATURES,
        "target": a.target,
        "shape": {"policy": a.policy, "tau": a.tau, "below_r": a.below_r},
        "booster_txt": model.booster_.model_to_string(),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        # The DATA bound, distinct from `trained_at` above. Null when unknowable
        # from the rows — never inferred from the fitting time.
        "train_start": window["train_start"],
        "train_end": window["train_end"],
        "train_window_coverage": window["train_window_coverage"],
        "train_dataset": str(fam_dir / "rows.jsonl"),
        "train_rows": len(rows),
        "train_trades": trades,
        "evidence": a.evidence or "docs/research/M20-exit-refinement-2026-07-12.md § 9",
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact))
    print(f"{a.model_id}: {len(rows)} rows / {trades} trades -> {out} "
          f"({out.stat().st_size // 1024} KiB) "
          # provenance: family_basis - whether `family` was DECLARED via --family
          # or DERIVED from the --family-dir basename
          f"family={family!r} ({family_basis})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
