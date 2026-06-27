#!/usr/bin/env python3
"""Fit per-regime-model alignment calibrators and write/update the
``regime_alignment`` section of the shared ``calibrators.json`` artifact.

Piece 4 of the A+B conviction program
(``docs/research/B-conviction-graduation-DESIGN-2026-06-27.md`` § "c_reg
enabler"). For each regime head, fit a mapping

    (regime head score at signal time, trade direction) -> P(favorable | ...)

from a corpus of historical CLOSED, filled, non-backtest trades, where
**favorable == won (pnl > 0)** — the same label the ``conviction_meta`` dataset
family uses. The corpus comes from ``trade_journal.db`` (closed trades JOINed to
``order_packages.model_scores``, which carries the regime head's signal-time
score). Calibrators are fit per ``(model_id, direction)`` so long and short can
map a regime score to different win-rates; a direction-pooled ``all`` calibrator
is the predict-time fallback.

The artifact is the SAME ``calibrators.json`` the confidence calibrators ship in
(``scripts/ml/fit_confidence_calibrators.py``), under a reserved top-level
``regime_alignment`` key, so it rides the existing trainer-mirror → live path
with no new plumbing. Existing top-level confidence calibrators are preserved
(the file is read-merge-written, not overwritten).

**The real fit runs on the trainer VM** (it has the data). The default fit
method is a stdlib logistic so this script + its unit tests need no
sklearn/numpy; pass ``--method auto`` to use the confidence fitter's richer
method ladder (isotonic/platt/decile by sample size) where sklearn is available.

Usage:
    python3 scripts/ml/fit_regime_alignment_calibrators.py \
        --out-calibrators artifacts/calibration/calibrators.json \
        [--db /data/bot-data/trade_journal.db] [--method logistic|auto] \
        [--min-rows 10] [--out-report artifacts/calibration/regime_alignment_report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.calibration.regime_alignment import (  # noqa: E402
    REGIME_ALIGNMENT_KEY,
    corpus_for_model,
    fit_model_section,
)
from src.runtime.conviction_inputs import classify_head  # noqa: E402


def _decode_json_obj(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def load_corpus_rows(db_path: str) -> list[dict]:
    """Read closed/filled/non-backtest trades joined to their order package.

    Mirrors the ``conviction_meta`` family's row scope. Each returned row carries
    ``model_scores`` (decoded ``{model_id:{stage,score}}``), ``direction``, and
    ``pnl`` — the inputs ``corpus_for_model`` transforms into ``(score, won)``
    pairs per regime model. Read-only (SQLite ``mode=ro``).
    """
    p = Path(db_path)
    if not p.is_file():
        raise FileNotFoundError(f"trade_journal.db not found at {p}")
    uri = f"file:{p.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    rows: list[dict] = []
    try:
        conn.row_factory = sqlite3.Row
        sql = (
            "SELECT op.direction AS direction, op.model_scores AS model_scores, "
            "       t.pnl AS pnl "
            "FROM trades t "
            "JOIN order_packages op ON t.order_package_id = op.order_package_id "
            "WHERE t.status = 'closed' AND t.is_backtest = 0 "
            "  AND t.pnl IS NOT NULL AND t.order_package_id IS NOT NULL"
        )
        for r in conn.execute(sql):
            rows.append({
                "direction": r["direction"],
                "model_scores": _decode_json_obj(r["model_scores"]),
                "pnl": r["pnl"],
            })
    finally:
        conn.close()
    return rows


def regime_model_ids(rows: list[dict]) -> list[str]:
    """All distinct regime-head model_ids that scored any corpus row."""
    seen: set[str] = set()
    for row in rows:
        for model_id in (row.get("model_scores") or {}):
            if classify_head(model_id) == "c_reg":
                seen.add(model_id)
    return sorted(seen)


def fit_artifact(
    rows: list[dict], *, method: str, min_rows: int
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return ``(regime_alignment_section, report)`` for the corpus rows."""
    section: dict[str, dict] = {}
    report: dict[str, dict] = {}
    for model_id in regime_model_ids(rows):
        by_dir = corpus_for_model(rows, model_id)
        counts = {d: len(pairs) for d, pairs in by_dir.items() if pairs}
        fitted = fit_model_section(rows, model_id, method=method, min_rows=min_rows)
        if fitted:
            section[model_id] = fitted
        report[model_id] = {
            "row_counts": counts,
            "directions_fit": sorted(fitted.keys()),
            "methods": {d: fitted[d].get("method") for d in fitted},
        }
    return section, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None,
                    help="trade_journal.db path (default: canonical resolver)")
    ap.add_argument("--out-calibrators", required=True)
    ap.add_argument("--out-report", default=None)
    ap.add_argument("--method", default="logistic",
                    help="logistic (stdlib, default) | auto (sklearn ladder)")
    ap.add_argument("--min-rows", type=int, default=10)
    args = ap.parse_args()

    db_path = args.db
    if db_path is None:
        from src.utils.paths import trade_journal_db_path

        db_path = str(trade_journal_db_path())

    rows = load_corpus_rows(db_path)
    section, report = fit_artifact(rows, method=args.method, min_rows=args.min_rows)

    # Read-merge-write: preserve the confidence calibrators already in the file.
    out_path = args.out_calibrators
    existing: dict = {}
    try:
        existing = json.loads(Path(out_path).read_text())
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, ValueError):
        existing = {}
    existing[REGIME_ALIGNMENT_KEY] = section

    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(existing, fh, indent=2)
    if args.out_report:
        rd = os.path.dirname(args.out_report)
        if rd:
            os.makedirs(rd, exist_ok=True)
        with open(args.out_report, "w") as fh:
            json.dump(report, fh, indent=2)

    n_dir = sum(len(v) for v in section.values())
    print(f"fit regime_alignment for {len(section)} model(s), {n_dir} direction-calibrator(s)")
    for model_id, meta in report.items():
        cnt = meta["row_counts"]
        fit_dirs = meta["directions_fit"]
        print(f"  {model_id}: rows={cnt} fit={fit_dirs}")
    if not section:
        print("  (no regime head scored enough closed trades yet — section empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
