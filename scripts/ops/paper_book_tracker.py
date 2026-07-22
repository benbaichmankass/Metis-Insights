#!/usr/bin/env python3
"""Diversified paper-book tracker — watch the validated 10-cell cohort live.

The 10-cell diversified alt book (config/research/diversified_paper_book.yaml)
was banked 2026-06-18 as robustly +OOS in backtest, with ONE blemish: 2026-YTD
was flat (-1.7R). All 10 cells run enabled+live on the bybit_1 PAPER account, so
the live paper trade book is the cheapest, real evidence for whether that
flatness is early alpha-decay or partial-year noise.

This tool turns the closed-trade rows the /performance-review session already
fetches (`GET /api/bot/trades/closed?account_id=bybit_1&...`) into a dated
snapshot of the book's live paper performance — book + per-cell + per-family
aggregates, a recency split (recent-window vs prior) for the decay watch — and
APPENDS it to a tracker JSONL so the trajectory accrues across reviews. It then
prints the delta vs the previous snapshot and a decay verdict.

Tier-1 research tooling: reads trade rows + a cohort yaml, appends a snapshot,
writes a report. No live path; never touches src/ or config/.

Usage (PM-side / review session):
    # feed the closed-trades JSON (list, or {rows:[...]}/{trades:[...]} envelope)
    python3 scripts/ops/paper_book_tracker.py --trades-json trades.json
    cat trades.json | python3 scripts/ops/paper_book_tracker.py --trades-json -
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import yaml

_REPO = __file__.rsplit("/scripts/", 1)[0]
_DEFAULT_COHORT = f"{_REPO}/config/research/diversified_paper_book.yaml"
_DEFAULT_TRACKER = f"{_REPO}/docs/research/paper-book-tracker.jsonl"


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _norm_rows(raw) -> list[dict]:
    """Accept a bare list or an API envelope ({rows|trades|data:[...]})."""
    if isinstance(raw, dict):
        for k in ("rows", "trades", "data", "closed"):
            if isinstance(raw.get(k), list):
                raw = raw[k]
                break
    return raw if isinstance(raw, list) else []


def _parse_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):  # epoch seconds
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except (OSError, ValueError):
            return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    pnls = [float(_get(r, "pnl", "realizedPnl", "pnl_usd", "realized_usd", default=0.0) or 0.0) for r in rows]
    net = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {"n": n, "net_usd": round(net, 2),
            "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
            "mean_usd": round(net / n, 4) if n else 0.0}


def _cell_of(r: dict) -> str | None:
    # `pattern` is the field GET /api/bot/trades/closed actually emits for the
    # strategy name (src/web/api/routers/trades_closed.py: row["strategy_name"]
    # serialized as `pattern`) — this is the endpoint this tool's own docstring
    # + the /performance-review skill document as the feed source. Without this
    # alias every row's cell resolves to None, which never matches a cohort
    # cell name, so the tracker silently emits an all-zero snapshot instead of
    # erroring (BL-20260722-PAPER-BOOK-TRACKER-PATTERN-FIELD — reproduced with
    # the 2026-07-07T09:40 tracker snapshot, n=0 across every cell, immediately
    # followed 15 min later by a real n=82 snapshot once fed differently).
    return _get(r, "strategy", "strategyName", "strategy_name", "strategy_id", "pattern")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trades-json", required=True, help="closed-trades JSON file, or '-' for stdin")
    p.add_argument("--cohort", default=_DEFAULT_COHORT)
    p.add_argument("--tracker", default=_DEFAULT_TRACKER)
    p.add_argument("--no-append", action="store_true", help="compute + print but don't append a snapshot")
    p.add_argument("--asof", default=None, help="snapshot date override (ISO; default now UTC)")
    a = p.parse_args(argv)

    cohort = yaml.safe_load(open(a.cohort))
    cells = set(cohort["cells"])
    fam_of = {c: fam for fam, cs in cohort.get("families", {}).items() for c in cs}
    watch = cohort.get("watch", {})
    rec_days = int(watch.get("recent_window_days", 30))
    mean_floor = float(watch.get("decay_mean_floor_usd", 0.0))

    raw = sys.stdin.read() if a.trades_json == "-" else open(a.trades_json).read()
    rows = _norm_rows(json.loads(raw))
    # keep only closed cohort rows (status closed if present; cohort by strategy)
    book = []
    for r in rows:
        if _cell_of(r) not in cells:
            continue
        status = str(_get(r, "status", "tradeStatus", default="closed")).lower()
        if status and status not in ("closed", "filled_closed", "close"):
            continue
        book.append(r)

    asof = _parse_ts(a.asof) or datetime.now(timezone.utc)
    cutoff = asof - timedelta(days=rec_days)
    recent = [r for r in book if (_parse_ts(_get(r, "closedAt", "closeTime", "closed_at", "exitTime", "timestamp")) or asof) >= cutoff]
    prior = [r for r in book if r not in recent]

    per_cell = {}
    for c in sorted(cells):
        per_cell[c] = _agg([r for r in book if _cell_of(r) == c])
    per_fam = {}
    for fam in sorted(cohort.get("families", {})):
        per_fam[fam] = _agg([r for r in book if fam_of.get(_cell_of(r)) == fam])

    book_agg = _agg(book)
    recent_agg = _agg(recent)
    prior_agg = _agg(prior)

    # decay watch — recent window net-negative AND mean below floor (a sustained
    # sag, not one flat week). Reported as a flag for a human/Claude read.
    decay_flag = bool(recent_agg["n"] >= 10 and recent_agg["net_usd"] < 0 and recent_agg["mean_usd"] < mean_floor)

    snap = {"asof": asof.isoformat(), "book": book_agg, "recent_window_days": rec_days,
            "recent": recent_agg, "prior": prior_agg, "per_family": per_fam,
            "per_cell": per_cell, "decay_flag": decay_flag,
            "active_cells": sum(1 for c in per_cell.values() if c["n"] > 0)}

    # delta vs previous snapshot
    prev = None
    try:
        with open(a.tracker) as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        if lines:
            prev = json.loads(lines[-1])
    except FileNotFoundError:
        pass

    print(f"=== Diversified paper book ({cohort['name']} @ {cohort['account']}) — {asof.date()} ===")
    print(f"BOOK: n={book_agg['n']}  net=${book_agg['net_usd']}  WR={book_agg['win_rate']}%  "
          f"mean=${book_agg['mean_usd']}  active_cells={snap['active_cells']}/10")
    print(f"  recent {rec_days}d: n={recent_agg['n']} net=${recent_agg['net_usd']} mean=${recent_agg['mean_usd']}  "
          f"| prior: n={prior_agg['n']} net=${prior_agg['net_usd']} mean=${prior_agg['mean_usd']}")
    print(f"  families: trend net=${per_fam.get('trend',{}).get('net_usd')}  "
          f"pullback net=${per_fam.get('pullback',{}).get('net_usd')}")
    if prev:
        d_n = book_agg["n"] - prev["book"]["n"]
        d_net = round(book_agg["net_usd"] - prev["book"]["net_usd"], 2)
        print(f"  Δ since {prev['asof'][:10]}: +{d_n} trades, ${d_net:+.2f} net")
    print("  per-cell:")
    for c in sorted(cells):
        m = per_cell[c]
        print(f"    {c:26} n={m['n']:4} net=${m['net_usd']:9.2f} WR={m['win_rate']:5.1f}% mean=${m['mean_usd']:+.3f}")
    print(f"DECAY WATCH: {'⚠ FLAG — recent window net-negative + below floor' if decay_flag else 'ok (no sustained sag)'} "
          f"[{watch.get('baseline_note','')}]")

    if not a.no_append:
        with open(a.tracker, "a") as fh:
            fh.write(json.dumps(snap) + "\n")
        print(f"\nappended snapshot -> {a.tracker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
