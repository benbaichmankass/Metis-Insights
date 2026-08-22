#!/usr/bin/env python3
# wiring: manual-only — a ONE-OFF backfill over the pre-fix row set, run
# deliberately by an operator or a session once the forward fix is live. It is
# not scheduled and must not be: a repeating relabel would keep rewriting rows the
# forward path already handles, and the population it exists for is finite and
# shrinking. See BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE.
"""Re-derive the exit LABEL on historical rows whose price arrived after the label.

``BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE``. The forward fix
(``order_monitor._sweep_pending_pnl_from_bybit``) only helps NEW closes. This is the
one-off pass over the backlog those closes left behind.

WHY A SEPARATE, STAGED TOOL
---------------------------
Operator direction 2026-08-22: *"stage it annotate-first and split it by provenance —
the 91 broker-truth rows are a much stronger case than the 90 resting on estimated
prices."* So:

* **``--apply`` is required to write.** Default is annotate: it does the whole
  computation and writes a JSONL of exactly the rows it WOULD change, and touches
  nothing. Same shape as ``NETTING_ATTRIBUTION_MODE``'s annotate-first staging.
* **``--provenance`` scopes the write, never the measurement.** Every candidate is
  computed and annotated regardless; only the selected class may be written. A staging
  control that also disables measurement of the thing you are staging toward is
  self-defeating — that exact mistake was corrected in the netting reconciler on
  2026-08-09.

WHAT IT WILL NOT DO
-------------------
* It does **not** re-implement the classifier. It imports
  ``order_monitor._classify_broker_exit`` so this pass and the live path can never drift
  into two answers — the same single-definition rule ``position_telemetry.r_distances``
  follows.
* It **never overwrites a non-generic reason.** Only rows still carrying
  ``reconciler_filled`` (or empty) are candidates.
* It **never grades a reduce leg** (an ``intent_reduce`` bracket can be inverted).
* It records ``exit_reason_prior`` on every row it changes, so the pass is reversible
  from the row itself rather than from this script's log.

MEASURED BASELINE (2026-08-22, state the population when quoting it)
--------------------------------------------------------------------
572 rows carry ``exit_reason='reconciler_filled'``; **395 are gradeable** (an exit price
> 0 AND a linked package with at least one positive level). 172 with no package and 5
with no price are EXCLUDED, not counted either way.

    broker-truth price   n=155   83 sl + 8 tp =  91 (58.7%)   64 between (41.3%)
    estimated or worse   n=240   70 sl + 20 tp = 90 (37.5%)  150 between (62.5%)

⚠️ Quote the **91**, not the 181: the wider figure rests partly on ``local_markprice``,
the FABRICATED class ``src/runtime/provenance.py`` exists to distrust.

USAGE
-----
    python scripts/ops/reclassify_frozen_exit_reasons.py                 # annotate all
    python scripts/ops/reclassify_frozen_exit_reasons.py --provenance broker_truth
    python scripts/ops/reclassify_frozen_exit_reasons.py --provenance broker_truth --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime.order_monitor import _classify_broker_exit  # noqa: E402
from src.utils.paths import trade_journal_db_path  # noqa: E402

#: Sources that are the venue's own record of the fill. Everything else is a
#: reconstruction — see ``src/runtime/provenance.py`` for why the distinction binds.
BROKER_TRUTH_SOURCES = {"bybit_closed_pnl", "exchange_fill"}

GENERIC_REASONS = {"", "reconciler_filled"}


def _decode(blob):
    try:
        v = json.loads(blob) if isinstance(blob, str) else (blob or {})
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


class _DBShim:
    """``_classify_broker_exit`` takes a db with ``.connect()``. Give it exactly that."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def connect(self):
        return sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)


def scan(db_path: Path):
    """Return every candidate with its verdict. Reads only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, symbol, direction, exit_price, exit_reason, setup_type, notes, "
        "       account_id, closed_at "
        "  FROM trades "
        " WHERE status = 'closed' AND COALESCE(is_backtest, 0) = 0 "
        "   AND exit_reason IN ('reconciler_filled', '')"
    )]
    conn.close()

    shim = _DBShim(db_path)
    out = []
    for r in rows:
        notes = _decode(r.get("notes"))
        if str(r.get("exit_reason") or "").strip() not in GENERIC_REASONS:
            continue
        is_reduce = (
            str(r.get("setup_type") or "").strip().lower() == "intent_reduce"
            or bool(notes.get("intent_reduce"))
        )
        src = str(notes.get("exit_price_source") or "")
        verdict = _classify_broker_exit(
            shim, r, r.get("exit_price"), is_reduce_leg=is_reduce)
        out.append({
            "trade_id": r["id"],
            "account_id": r.get("account_id"),
            "symbol": r.get("symbol"),
            "direction": r.get("direction"),
            "closed_at": r.get("closed_at"),
            "exit_price": r.get("exit_price"),
            "exit_price_source": src or None,
            "provenance": "broker_truth" if src in BROKER_TRUTH_SOURCES
                          else "estimated_or_worse",
            "is_reduce_leg": is_reduce,
            "verdict": verdict,
            # `exit_reason_source` present means the classifier ALREADY ran on this
            # row at close time. Reported, not filtered — it is the marker whose
            # absence made the 181/181 signature readable.
            "classifier_ran_at_close": bool(notes.get("exit_reason_source")),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="override the resolved journal path")
    ap.add_argument("--provenance", choices=["broker_truth", "estimated_or_worse", "all"],
                    default="all",
                    help="scope the WRITE (never the measurement); default all")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it nothing is modified.")
    ap.add_argument("--out", default=None, help="annotate JSONL path")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else Path(trade_journal_db_path())
    if not db_path.exists():
        print(f"journal not found: {db_path}", file=sys.stderr)
        return 2

    cands = scan(db_path)
    gradeable = [c for c in cands if c["verdict"]]
    by = {}
    for c in cands:
        k = c["provenance"]
        by.setdefault(k, {"n": 0, "sl": 0, "tp": 0, "between": 0, "reduce": 0})
        by[k]["n"] += 1
        if c["is_reduce_leg"]:
            by[k]["reduce"] += 1
        if c["verdict"] in ("sl", "tp"):
            by[k][c["verdict"]] += 1
        else:
            by[k]["between"] += 1

    print(f"journal : {db_path}")
    print(f"scanned : {len(cands)} rows still carrying the generic reason")
    for k, v in sorted(by.items()):
        would = v["sl"] + v["tp"]
        pct = f"{would / v['n']:.1%}" if v["n"] else "n/a"
        print(f"  {k:20} n={v['n']:4}  would relabel {would:4} ({pct})"
              f"  [sl {v['sl']} · tp {v['tp']} · between {v['between']}"
              f" · reduce-excluded {v['reduce']}]")

    out_path = Path(args.out) if args.out else (
        Path("runtime_logs") / "exit_reason_reclassify_annotate.jsonl")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for c in cands:
                fh.write(json.dumps(c) + "\n")
        print(f"annotate: {out_path} ({len(cands)} rows)")
    except Exception as exc:  # noqa: BLE001
        print(f"annotate write failed (continuing): {exc}", file=sys.stderr)

    targets = [c for c in gradeable
               if args.provenance == "all" or c["provenance"] == args.provenance]
    print(f"in scope for --provenance={args.provenance}: {len(targets)} of "
          f"{len(gradeable)} relabellable")

    if not args.apply:
        print("\nANNOTATE ONLY — nothing was written. Re-run with --apply to write.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    n = 0
    try:
        for c in targets:
            cur = conn.execute("SELECT notes, exit_reason FROM trades WHERE id = ?",
                               (c["trade_id"],)).fetchone()
            if cur is None:
                continue
            # Re-check under the write connection: the row may have been closed
            # properly by the forward fix between the scan and now.
            if str(cur["exit_reason"] or "").strip() not in GENERIC_REASONS:
                continue
            notes = _decode(cur["notes"])
            notes["exit_reason_prior"] = cur["exit_reason"]
            notes["exit_reason_source"] = "price_vs_pkg_bracket"
            notes["exit_reason_backfilled_at"] = now
            conn.execute(
                "UPDATE trades SET exit_reason = ?, notes = ? WHERE id = ?",
                (c["verdict"], json.dumps(notes), c["trade_id"]))
            n += 1
        conn.commit()
    finally:
        conn.close()
    print(f"APPLIED: {n} rows relabelled (provenance={args.provenance})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
