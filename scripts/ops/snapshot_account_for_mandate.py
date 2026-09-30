#!/usr/bin/env python3
"""Commit-ready equity reading for the mandate resolver's R-AFFORD clause.

Reads the JSON of ``GET /api/diag/broker_account_status?account_id=<id>`` on
stdin and writes ``comms/mandate_evidence/account_snapshot/<id>.json`` -- the
one file ``scripts/ops/mandate_resolver.py`` reads to decide whether a Stage-2
account can size a leg at all (PI-20260928-E8Y3BGBS-0003)::

    scripts/ops/diag_fetch.sh 'broker_account_status?account_id=alpaca_live' \\
        | python3 scripts/ops/snapshot_account_for_mandate.py

Aggregates only (equity / buying power / cash / multiplier / shorting flag),
the same public class ``broker_account_status`` already serves. It never writes
a number the diag read did not carry: a field the venue did not report is
``null``, never ``0`` ("we did not look" stays distinct from "we looked").

Bybit: ``broker_account_status`` carries ``available_margin`` but no equity, so
``equity_usd`` is set to that available figure and ``equity_basis`` says so.
It understates equity, which can only make R-AFFORD refuse MORE, never fire on
money the account does not have.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "comms/mandate_evidence/account_snapshot"


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def build(diag: dict, account_id: str) -> dict:
    rows = [a for a in diag.get("accounts") or [] if a.get("account_id") == account_id]
    if len(rows) != 1:
        raise SystemExit(f"snapshot: {account_id!r} appears {len(rows)} times in the diag read")
    a = rows[0]
    if a.get("error"):
        raise SystemExit(f"snapshot: diag read for {account_id} errored: {a['error']}")
    out = {"account_id": account_id, "exchange": a.get("exchange"),
           "captured_at": diag.get("captured_at"),
           "source": f"GET /api/diag/broker_account_status?account_id={account_id}"}
    cap = ((a.get("status_flags") or {}).get("capacity")) or {}
    if cap:
        out.update({"equity_usd": _f(cap.get("equity")),
                    "buying_power_usd": _f(cap.get("buying_power")),
                    "cash_usd": _f(cap.get("cash")),
                    "multiplier": cap.get("multiplier"),
                    "equity_basis": "venue equity"})
    else:
        am = a.get("available_margin") or {}
        avail = None if am.get("could_not_look") else _f(am.get("available_usd"))
        out.update({"equity_usd": avail, "buying_power_usd": avail, "cash_usd": None,
                    "multiplier": None,
                    "equity_basis": f"available_margin ({am.get('read_state')}) -- equity not "
                                    "in this endpoint; understates, so R-AFFORD errs to refuse"})
    out["shorting_enabled"] = (a.get("status_flags") or {}).get("shorting_enabled")
    if out["equity_usd"] is None or not out["captured_at"]:
        raise SystemExit(f"snapshot: {account_id} read carries no equity/captured_at -- not written")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--account", help="account id (default: the read's requested_account_id)")
    ap.add_argument("--root", default=str(REPO))
    a = ap.parse_args(argv)
    diag = json.load(sys.stdin)
    acct = a.account or diag.get("requested_account_id")
    if not acct:
        raise SystemExit("snapshot: no --account and the read names no requested_account_id")
    snap = build(diag, acct)
    p = Path(a.root) / OUT_DIR_REL / f"{acct}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    print(f"snapshot: wrote {p.relative_to(a.root)} equity={snap['equity_usd']} "
          f"buying_power={snap['buying_power_usd']} captured_at={snap['captured_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
