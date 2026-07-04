#!/usr/bin/env python3
"""Retroactive, rule-based decision scorer for order packages.

Produces one decision-quality grade per row of ``order_packages`` —
keyed by ``order_package_id`` — written as JSONL to
``comms/claude_strategy_scores.jsonl``. The score belongs to the STRATEGY
DECISION (the order package), not the trade journal: an order package is
the artifact a strategy emits when it decides to act, so it is the right
anchor for "how good was this decision", independent of whether/how it
filled (operator decision 2026-05-25).

This is the *consistent-rubric* path (operator decision 2026-05-25): the
~1.2k packages are ~99% near-identical vwap mean-reversion setups, so a
uniform, reproducible rubric is better feedstock than ad-hoc per-row
paragraphs. The rubric is deliberately strategy-aware and transparent —
read ``_grade_package`` for the exact thresholds.

The grade is independent of dollar P&L: a small win on a no-edge setup
still grades poorly; a stop-out on a textbook setup still grades fairly.
The three categorical labels (entry_quality / exit_quality /
risk_management) are the training-friendly fields; ``decision_grade``
(A..F -> 4..0) is the rolled-up summary, matching the review_journal
family mapping.

Usage:
    python scripts/ops/score_order_packages.py <trade_journal.db> [out.jsonl]
        [--append] [--force-rewrite]

Write modes (BL-20260703-GRADING-COVERAGE-GAP hardening, 2026-07-04):

* ``--append`` — open ``out.jsonl`` in append mode, SKIP every
  ``order_package_id`` already present in it, and write only the missing
  rows (no ``_meta`` header). This is the mode routine backfills must
  use against the canonical ``comms/claude_strategy_scores.jsonl`` —
  it honours the file's APPEND-ONLY contract and can never clobber the
  LLM-authored grades.
* default (rewrite) — the original retroactive full-pass behaviour
  (truncate + regrade everything + fresh ``_meta`` line). As a guard,
  rewriting a path whose basename is ``claude_strategy_scores.jsonl``
  now requires an explicit ``--force-rewrite`` — pointing the default
  mode at the canonical file was the footgun that nearly clobbered
  2,500+ rows during the 2026-07-03 backfill.

The DB path is taken from argv only (no CWD-relative default) so the
canonical-db-resolver guard is satisfied. Read-only on the DB.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = 1
SOURCE = "health-review-retroactive"

_LETTER_TO_SCORE = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def _f(v: Any) -> Optional[float]:
    """Coerce to float or None (handles None, '', NaN, bad strings)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


def _sl_json(blob: Any) -> dict:
    if not blob:
        return {}
    if isinstance(blob, dict):
        return blob
    try:
        d = json.loads(blob)
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _realized_r(direction: str, entry: Optional[float], sl: Optional[float],
                exit_price: Optional[float]) -> Optional[float]:
    """R multiple actually captured at exit (signed by direction)."""
    if entry is None or sl is None or exit_price is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if direction == "long":
        return (exit_price - entry) / risk
    if direction == "short":
        return (entry - exit_price) / risk
    return None


def _grade_package(row: dict) -> dict:
    """Apply the rubric. Returns the score fields for one package.

    Strategy-aware. The headline judgement:
      * vwap has NO net-of-fee edge (proven: 362 closes, net -$2.4k, 22%
        win -> demoted to execution:shadow 2026-05-24). So even a clean
        vwap decision is capped at B and the cohort centres on C/D — the
        honest training signal is "vwap setups are low-value".
      * ict_scalp_5m showed positive edge in-sample -> centres on B.
      * trend/turtle/fade/squeeze have ~no executed history; their
        packages are shadow/never-filled -> graded on SETUP quality only,
        exit_quality=unknown.
    """
    strat = (row.get("strategy_name") or "?").lower()
    direction = (row.get("direction") or "").lower()
    status = (row.get("status") or "").lower()
    sl_blob = _sl_json(row.get("signal_logic"))

    entry = _f(row.get("entry"))
    sl = _f(row.get("sl"))
    dev = _f(sl_blob.get("deviation_std"))
    conf = _f(sl_blob.get("confidence"))
    sl_distance = _f(sl_blob.get("sl_distance"))
    atr = _f(sl_blob.get("atr"))

    executed = row.get("linked_trade_id") is not None
    pnl = _f(row.get("pnl"))
    exit_price = _f(row.get("exit_price"))
    exit_reason = (row.get("exit_reason") or row.get("close_reason") or "").lower()
    realized_r = _realized_r(direction, entry, sl, exit_price)

    # ---- entry_quality -----------------------------------------------------
    if strat == "vwap":
        adev = abs(dev) if dev is not None else None
        if adev is None:
            entry_quality = "unknown"
        elif adev >= 2.5:
            entry_quality = "optimal"
        elif adev >= 1.5:
            entry_quality = "acceptable"
        else:
            entry_quality = "early"  # fired below the 1.5σ threshold
    else:
        if conf is None:
            entry_quality = "unknown"
        elif conf >= 0.7:
            entry_quality = "optimal"
        elif conf >= 0.5:
            entry_quality = "acceptable"
        elif conf >= 0.3:
            entry_quality = "late"
        else:
            entry_quality = "should_skip"

    # ---- exit_quality (executed only; shadow/never-filled -> unknown) ------
    if not executed:
        exit_quality = "unknown"
    elif exit_reason in ("sl_hit", "sl_cross") or (pnl is not None and pnl < 0
                                                   and realized_r is not None
                                                   and realized_r <= -0.8):
        exit_quality = "sl_appropriate"
    elif realized_r is not None and pnl is not None and pnl > 0:
        if realized_r >= 0.8:
            exit_quality = "tp_appropriate"
        elif realized_r < 0.25:
            exit_quality = "premature_exit"  # documented vwap micro-edge cross
        else:
            exit_quality = "tp_appropriate"
    elif pnl is not None and pnl <= 0:
        exit_quality = "sl_appropriate"
    else:
        exit_quality = "unknown"

    # ---- risk_management ---------------------------------------------------
    if sl_distance is not None and atr is not None and atr > 0:
        ratio = sl_distance / atr
        if ratio < 0.5:
            risk_management = "sl_too_tight"
        elif ratio > 3.0:
            risk_management = "sl_too_wide"
        else:
            risk_management = "correct"
    elif strat == "vwap":
        # vwap ships sl_std_mult 0.3 — structurally tight (the documented
        # vwap_cross micro-loss mechanism); flag when we can't measure.
        risk_management = "sl_too_tight"
    else:
        risk_management = "unknown"

    # ---- decision_grade ----------------------------------------------------
    if strat == "vwap":
        score = 2  # C baseline: no net edge
        if entry_quality == "optimal":
            score += 1
        if entry_quality in ("early", "should_skip"):
            score -= 1
        if exit_quality == "premature_exit":
            score -= 1
        score = max(0, min(3, score))  # capped at B — never A for a no-edge setup
    elif strat == "ict_scalp_5m":
        score = 3  # B baseline: positive edge in-sample
        if entry_quality == "optimal" and exit_quality in ("tp_appropriate", "optimal"):
            score += 1
        if entry_quality in ("early", "should_skip"):
            score -= 1
        score = max(0, min(4, score))
    else:
        # trend/turtle/fade/squeeze: little/no executed history; grade the
        # SETUP only (exit unknown). Neutral baseline.
        score = 2
        if entry_quality == "optimal":
            score += 1
        if entry_quality in ("early", "should_skip"):
            score -= 1
        score = max(0, min(3, score))

    letter = {4: "A", 3: "B", 2: "C", 1: "D", 0: "F"}[score]

    # ---- rationale (templated from the features) ---------------------------
    bits = [f"{strat} {direction or '?'}"]
    if dev is not None:
        bits.append(f"{abs(dev):.2f}σ from VWAP")
    elif conf is not None:
        bits.append(f"conf {conf:.2f}")
    bits.append(f"entry={entry_quality}")
    if executed:
        rtxt = f"{realized_r:.2f}R" if realized_r is not None else "R n/a"
        bits.append(f"exit={exit_quality}({exit_reason or '?'},{rtxt})")
    else:
        bits.append(f"never filled (status={status})")
    bits.append(f"risk={risk_management}")
    if strat == "vwap":
        bits.append("no net edge -> shadow")
    rationale = "; ".join(bits)[:240]

    if not executed:
        alt = "shadow/unfilled — data-collection only; judge setup, no exit"
    elif strat == "vwap":
        alt = "vwap has no net edge; skip or keep shadow-only"
    elif exit_quality == "premature_exit":
        alt = "let winner run past the micro-edge cross"
    else:
        alt = "none"

    return {
        "decision_grade": letter,
        "decision_grade_score": score,
        "entry_quality": entry_quality,
        "exit_quality": exit_quality,
        "risk_management": risk_management,
        "rationale": rationale,
        "alternative_action": alt[:160],
    }


def _existing_ids(out_path: str) -> set:
    """order_package_ids already present in ``out_path`` (empty set if absent)."""
    ids: set = set()
    try:
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    oid = json.loads(line).get("order_package_id")
                except (ValueError, TypeError):
                    continue
                if oid:
                    ids.add(oid)
    except OSError:
        pass
    return ids


def main() -> int:
    argv = list(sys.argv[1:])
    append = "--append" in argv
    force_rewrite = "--force-rewrite" in argv
    argv = [a for a in argv if a not in ("--append", "--force-rewrite")]
    if not argv:
        print("usage: score_order_packages.py <trade_journal.db> [out.jsonl] "
              "[--append] [--force-rewrite]", file=sys.stderr)
        return 2
    db_path = argv[0]
    out_path = argv[1] if len(argv) > 1 else "comms/claude_strategy_scores.jsonl"
    import os
    if (not append and not force_rewrite
            and os.path.basename(out_path) == "claude_strategy_scores.jsonl"
            and os.path.exists(out_path)):
        print("REFUSING to rewrite the canonical append-only scores file "
              f"({out_path}). Use --append to add only missing packages, or "
              "--force-rewrite if a full retroactive re-grade is intended.",
              file=sys.stderr)
        return 3
    skip_ids = _existing_ids(out_path) if append else set()
    reviewed_at = datetime.now(timezone.utc).isoformat()

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT op.order_package_id, op.strategy_name, op.symbol, op.direction,
               op.status, op.close_reason, op.linked_trade_id, op.signal_logic,
               op.entry, op.sl, op.tp, op.created_at,
               t.pnl AS pnl, t.exit_price AS exit_price,
               t.exit_reason AS exit_reason, t.position_size AS position_size
        FROM order_packages op
        LEFT JOIN trades t ON t.id = op.linked_trade_id
        ORDER BY op.created_at, op.order_package_id
        """
    ).fetchall()

    n = 0
    skipped = 0
    grade_hist: dict[str, int] = {}
    with open(out_path, "a" if append else "w") as fh:
        if not append:
            fh.write(json.dumps({
                "_meta": "Per-ORDER-PACKAGE Claude decision scores (strategy-decision "
                         "scores), keyed by order_package_id -> trade_journal.db::"
                         "order_packages. Retroactive consistent-rubric pass + future "
                         "/health-review appends. APPEND-ONLY after this line. Rubric: "
                         "scripts/ops/score_order_packages.py::_grade_package. "
                         f"schema_version={SCHEMA_VERSION}",
            }) + "\n")
        for r in rows:
            if append and r["order_package_id"] in skip_ids:
                skipped += 1
                continue
            d = dict(r)
            g = _grade_package(d)
            grade_hist[g["decision_grade"]] = grade_hist.get(g["decision_grade"], 0) + 1
            rec = {
                "order_package_id": d.get("order_package_id"),
                "linked_trade_id": d.get("linked_trade_id"),
                "reviewed_at": reviewed_at,
                "reviewer": "claude",
                "source": SOURCE,
                "strategy_name": d.get("strategy_name"),
                "symbol": d.get("symbol"),
                "direction": d.get("direction"),
                "status": d.get("status"),
                "executed": d.get("linked_trade_id") is not None,
                "created_at": d.get("created_at"),
                "entry": _f(d.get("entry")),
                "sl": _f(d.get("sl")),
                "tp": _f(d.get("tp")),
                "pnl": _f(d.get("pnl")),
                "exit_reason": (d.get("exit_reason") or d.get("close_reason")),
                **g,
            }
            fh.write(json.dumps(rec) + "\n")
            n += 1

    mode = "appended" if append else "scored"
    print(f"{mode} {n} order packages -> {out_path}"
          + (f" (skipped {skipped} already present)" if append else ""))
    print(f"grade histogram: {dict(sorted(grade_hist.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
