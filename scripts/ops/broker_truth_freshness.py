#!/usr/bin/env python3
"""Grade the broker-truth ledger's freshness — one red flag, never a desensitized one.

LEDGER-REFRESH (2026-10-10). ``comms/broker_truth_ledger.json`` had no producer:
bybit_2's hand record sat 89 days old (measured 2026-10-10) and nothing raised.
Input is ``GET /api/bot/pnl/broker-truth`` (record ``stale`` + the ``live`` block
fed by the hourly-refreshed transaction-log store). Read-only.

Per-record verdict, never collapsed:
  fresh          record within the stale threshold
  declared_gap   hand record is old BUT it declares the part no producer can
                 refresh (reason recorded) AND the live API block is measured and
                 not silent -- graded, shown, NOT a finding (a permanent alarm
                 is a desensitized one)
  stale          old, no declared gap (or the live block is silent / not measured)
                 -> FINDING
  could_not_look the route or the record's age was unreadable -> never 'fresh'

Exit: 0 clean (fresh / declared_gap) · 1 a stale finding · 3 could_not_look only
· 2 usage. ``--fingerprint`` prints digest + graded state for change-only comments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any, Dict, List


def grade_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    aid = rec.get("account_id")
    out = {"account_id": aid, "as_of": rec.get("as_of"), "age_days": rec.get("as_of_age_days")}
    live = rec.get("live") if isinstance(rec.get("live"), dict) else None
    out["live_state"] = live.get("state") if live else None
    if rec.get("stale") is None:
        return {**out, "verdict": "could_not_look", "why": "record age unreadable (no/garbled as_of)"}
    if not rec["stale"]:
        return {**out, "verdict": "fresh", "why": "within threshold"}
    gaps = rec.get("declared_gaps") or []
    live_ok = bool(live) and live.get("state") == "measured_api"
    if gaps and live_ok:
        return {**out, "verdict": "declared_gap",
                "why": "; ".join(f"{g.get('sub_account')}: {g.get('reason', '')[:90]}" for g in gaps)}
    why = "hand record stale"
    why += ", no declared gap" if not gaps else ", declared gap present"
    why += f", live block {live.get('state') if live else 'absent'}"
    return {**out, "verdict": "stale", "why": why}


def grade(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("error") or "accounts" not in payload:
        return {"records": [], "read": "could_not_look", "rc": 3}
    recs = [grade_record(r) for r in payload.get("accounts") or []]
    if not recs:
        # Present-but-empty ledger is "nobody has reconciled anything", not clean.
        return {"records": [], "read": "empty_ledger", "rc": 1}
    vs = {r["verdict"] for r in recs}
    rc = 1 if "stale" in vs else 3 if "could_not_look" in vs else 0
    return {"records": recs, "read": "read", "rc": rc}


def render(g: Dict[str, Any]) -> str:
    lines = [f"broker-truth ledger freshness: read={g['read']} exit={g['rc']}"]
    for r in g["records"]:
        lines.append(f"  {r['account_id']}: {r['verdict']} (as_of {r['as_of']}, age {r['age_days']}d, "
                     f"live {r['live_state']}) — {r['why']}")
    return "\n".join(lines)


def fingerprint(g: Dict[str, Any]) -> str:
    state = json.dumps({"read": g["read"], "v": [(r["account_id"], r["verdict"], r["live_state"]) for r in g["records"]]}, sort_keys=True)
    return hashlib.sha256(state.encode()).hexdigest()[:12] + "\n" + state


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broker-truth-json", required=True)
    ap.add_argument("--fingerprint", action="store_true")
    a = ap.parse_args(argv)
    try:
        payload = json.load(open(a.broker_truth_json))
    except (OSError, ValueError):
        payload = None
    g = grade(payload)
    print(fingerprint(g) if a.fingerprint else render(g))
    return g["rc"]


if __name__ == "__main__":
    sys.exit(main())
