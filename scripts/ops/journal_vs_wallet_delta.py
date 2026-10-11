#!/usr/bin/env python3
"""Daily journal-vs-wallet delta for a Bybit account (LEDGER-REFRESH).

Joins ``/api/diag/bybit_wallet_truth?by_day=true`` (per-UTC-day wallet delta,
which already NETS fees and funding) with ``/api/bot/trades/closed`` (journal
``realizedPnl`` by ``closedAt`` UTC day). Per day: ``delta = wallet - journal``,
beside the day's fees and funding and the count of journal rows with NULL pnl,
so a widening gap is observed instead of found by audit. A missing daily series
or journal is ``could_not_look`` -- never a 0 delta. Read-only.

Usage: journal_vs_wallet_delta.py --account A --wallet-json wt.json --closed-json c.json
Exit: 0 read · 3 could not look.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


def compute(account: str, wallet: Any, closed: Any) -> Dict[str, Any]:
    row = next((a for a in (wallet or {}).get("accounts", []) if a.get("account_id") == account), None) \
        if isinstance(wallet, dict) else None
    daily = row.get("daily") if row else None
    if not isinstance(daily, list) or not isinstance(closed, list):
        return {"account": account, "state": "could_not_look",
                "why": "wallet daily series or journal closed trades unavailable", "days": []}
    j: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        day = str(t.get("closedAt") or "")[:10]
        if not day:
            continue
        b = j.setdefault(day, {"pnl": 0.0, "n": 0, "null_pnl": 0})
        b["n"] += 1
        if t.get("realizedPnl") is None:
            b["null_pnl"] += 1
        else:
            b["pnl"] += float(t["realizedPnl"])
    # Only days both sides can speak to: the journal page may start mid-window.
    jdays = sorted(j)
    days = []
    for d in daily:
        if not jdays or d["date"] < jdays[0]:
            continue
        jb = j.get(d["date"], {"pnl": 0.0, "n": 0, "null_pnl": 0})
        days.append({"date": d["date"], "wallet": d["wallet_usd"], "journal": round(jb["pnl"], 4),
                     "delta": round(d["wallet_usd"] - jb["pnl"], 4), "fees": d["fees_usd"],
                     "funding": d["funding_usd"], "journal_rows": jb["n"], "null_pnl_rows": jb["null_pnl"]})

    def tot(k: str) -> float:
        return round(sum(x[k] for x in days), 4)

    return {"account": account, "state": "read", "days": days, "n_days": len(days),
            "total": {k: tot(k) for k in ("wallet", "journal", "delta", "fees", "funding")}}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", required=True)
    ap.add_argument("--wallet-json", required=True)
    ap.add_argument("--closed-json", required=True)
    a = ap.parse_args(argv)

    def load(p: str) -> Any:
        try:
            return json.load(open(p))
        except (OSError, ValueError):
            return None
    r = compute(a.account, load(a.wallet_json), load(a.closed_json))
    print(json.dumps(r, indent=1))
    return 0 if r["state"] == "read" else 3


if __name__ == "__main__":
    sys.exit(main())
