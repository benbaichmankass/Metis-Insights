#!/usr/bin/env python3
"""Grade CLOSED trades from diag-relay JSON — the web-session grading path.

WHY THIS EXISTS (operator directive 2026-06-29: "NO SYSTEM REVIEW SESSION IS
COMPLETE WITHOUT CLAUDE SCORES FOR ALL CLOSED TRADES"). The canonical scorer
``score_order_packages.py`` opens the live ``trade_journal.db`` and REWRITES the
whole ``comms/claude_strategy_scores.jsonl`` from it — so it can only run where
the DB file is (the VM, or a desktop session). A **web/PM session** reaches the
VM only through the GET diag relay (``/api/diag/journal?table=trades``), which
returns JSON, not the DB file — so a web-session ``/system-review`` previously
had no way to grade and silently shipped stale grades (the bug this fixes).

This grader closes that gap: feed it the diag ``trades`` JSON (newest closed
trades since the last review) and it APPENDS one decision-grade row per closed
trade to the JSONL, using the SAME rubric as the canonical scorer
(``score_order_packages._grade_package`` — imported, never re-implemented, so the
two paths can't drift). The API join is last-occurrence-wins
(``src/web/api/routers/order_packages.py::_pkg_scores``), so appending a fresh
closed-status grade correctly supersedes an earlier open-status one.

Usage:
    python scripts/ops/grade_closed_trades_from_diag.py <trades.json> \
        [--out comms/claude_strategy_scores.jsonl] [--source <src>] \
        [--since <ISO_TS>] [--include-paper]

``<trades.json>`` is the body of a ``/api/diag/journal?table=trades`` relay pull
(a JSON list of trade rows, or an object with a ``rows``/``trades`` list). Only
rows with ``status='closed'`` and a non-backtest flag are graded; ``--since``
filters by ``closed_at``/``created_at``; paper rows are skipped unless
``--include-paper`` (prop rows never appear in ``trades`` — they are journaled
separately and are not graded here).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_grader():
    """Import ``_grade_package`` from the canonical scorer (single rubric)."""
    path = os.path.join(_HERE, "score_order_packages.py")
    spec = importlib.util.spec_from_file_location("_scorer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "trades", "result"):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def _notes_confidence(notes: Any) -> Optional[float]:
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except (ValueError, TypeError):
            return None
    if isinstance(notes, dict):
        v = notes.get("confidence")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def _trade_to_pkg_row(t: dict) -> dict:
    """Map a diag ``trades`` row to the dict shape ``_grade_package`` expects.

    The trade carries the decision levels (entry_price/stop_loss/take_profit_1),
    the realised pnl/exit, and the confidence in ``notes`` — enough to apply the
    rubric. ``signal_logic`` is reconstructed as ``{confidence}`` (the only field
    the non-vwap rubric reads; vwap's deviation_std path degrades to 'unknown',
    which is correct when the diag row doesn't carry it).
    """
    sig = {}
    conf = _notes_confidence(t.get("notes"))
    if conf is not None:
        sig["confidence"] = conf
    return {
        "order_package_id": t.get("order_package_id"),
        "strategy_name": t.get("strategy_name") or t.get("setup_type"),
        "symbol": t.get("symbol"),
        "direction": t.get("direction"),
        "status": "closed",
        "close_reason": t.get("exit_reason"),
        "linked_trade_id": t.get("id"),
        "signal_logic": json.dumps(sig) if sig else None,
        "entry": t.get("entry_price"),
        "sl": t.get("stop_loss"),
        "tp": t.get("take_profit_1"),
        "created_at": t.get("created_at") or t.get("timestamp"),
        "pnl": t.get("pnl"),
        "exit_price": t.get("exit_price"),
        "exit_reason": t.get("exit_reason"),
    }


def _is_paper(t: dict) -> bool:
    ac = str(t.get("account_class") or "").lower()
    if ac:
        return ac != "real_money"
    return bool(t.get("is_demo"))


def grade(trades: list[dict], grader, *, source: str,
          since: Optional[str], include_paper: bool) -> list[dict]:
    out = []
    for t in trades:
        if str(t.get("status") or "").lower() != "closed":
            continue
        if t.get("is_backtest"):
            continue
        if not t.get("order_package_id"):
            continue  # can't key a grade without the package id
        if not include_paper and _is_paper(t):
            continue
        if since:
            ts = str(t.get("closed_at") or t.get("created_at") or "")
            if ts and ts < since:
                continue
        prow = _trade_to_pkg_row(t)
        g = grader._grade_package(prow)
        rec = {
            "order_package_id": prow["order_package_id"],
            "linked_trade_id": prow["linked_trade_id"],
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewer": "claude",
            "source": source,
            "strategy_name": prow["strategy_name"],
            "symbol": prow["symbol"],
            "direction": prow["direction"],
            "status": "closed",
            "executed": prow["linked_trade_id"] is not None,
            "created_at": prow["created_at"],
            "entry": grader._f(prow["entry"]),
            "sl": grader._f(prow["sl"]),
            "tp": grader._f(prow["tp"]),
            "pnl": grader._f(prow["pnl"]),
            "exit_reason": prow["exit_reason"],
            **g,
        }
        out.append(rec)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trades_json")
    ap.add_argument("--out", default="comms/claude_strategy_scores.jsonl")
    ap.add_argument("--source", default="system-review-diag")
    ap.add_argument("--since", default=None)
    ap.add_argument("--include-paper", action="store_true")
    args = ap.parse_args(argv)

    with open(args.trades_json) as fh:
        payload = json.load(fh)
    trades = _rows(payload)
    grader = _load_grader()
    recs = grade(trades, grader, source=args.source,
                 since=args.since, include_paper=args.include_paper)
    if not recs:
        print("no closed trades to grade (after filters)")
        return 0
    with open(args.out, "a") as fh:
        for rec in recs:
            fh.write(json.dumps(rec) + "\n")
    hist: dict[str, int] = {}
    for r in recs:
        hist[r["decision_grade"]] = hist.get(r["decision_grade"], 0) + 1
    print(f"graded {len(recs)} closed trades -> {args.out}")
    print(f"grade histogram: {dict(sorted(hist.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
