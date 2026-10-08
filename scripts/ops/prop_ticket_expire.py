#!/usr/bin/env python3
"""Move ONE dead prop ticket on a REST-executed account to ``expired``.

Run by the ``prop-ticket-expire`` system-action (Tier 2) on the live VM.
DRY-RUN by default: prints the ticket row as it is and as it would be.
``--apply`` takes an SQLite backup of the journal first, then writes.

The case it exists for (VELOTRADE-TICKET, 2026-10-07): ETH-short ticket
prop-manual-4fa7266cfcf0 on velotrade_1 waited on its entry band for its
whole validity and was never placed. The trader's expiry prompter then
flipped it to ``expiry_prompted``, which asks a human whether they placed it.
Nobody places tickets on a REST account, so nobody could answer. The reticket
guard counted it as outstanding and suppressed 4 later ETH-short signals. The
only ways to end it were a raw SQL write or the Telegram button. This action
is the in-git path.

The ONLY transitions allowed (anything else is refused, nothing written):

  expiry_prompted                   -> expired
  emitted, valid_until has passed   -> expired

Refused, fail-closed:
  * the account is not REST-executed (``config/prop_platforms.yaml``
    ``accounts.<id>.platform`` in ``API_PLATFORMS``). On a manual or phone
    account a human may have placed the ticket without reporting it, so
    the ❌ button (which asks that human) stays the only path there;
  * the ticket is missing, or belongs to another account;
  * ``emitted`` with a validity that has not passed or cannot be read;
  * any ``prop_fills`` row references the ticket (it may be a position).

The UPDATE is guarded on (ticket_id, account_id, status-as-read), so a ticket
that moved between the read and the write is not touched, and re-running after
an apply is a clean refusal ("status expired is not expirable").
Output is one JSON object with ``before`` / ``after`` rows.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TERMINAL_STATUS = "expired"


def rest_accounts(platforms_path: Optional[Path] = None) -> Set[str]:
    """Accounts whose platform is a REST API (the canonical list lives in
    ``src.prop.platform.API_PLATFORMS``). Unreadable file -> empty set, so
    every account is refused rather than guessed."""
    import yaml

    from src.prop.platform import API_PLATFORMS, PLATFORMS_PATH

    p = Path(platforms_path) if platforms_path else PLATFORMS_PATH
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:  # noqa: BLE001 — refuse everything rather than guess
        return set()
    return {aid for aid, e in (data.get("accounts") or {}).items()
            if isinstance(e, dict) and str(e.get("platform") or "").strip() in API_PLATFORMS}


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(conn: sqlite3.Connection, ticket_id: str) -> Optional[Dict[str, Any]]:
    r = conn.execute(
        "SELECT ticket_id, account_id, strategy, symbol, direction, status, "
        "valid_until, created_at FROM prop_tickets WHERE ticket_id = ?",
        (ticket_id,)).fetchone()
    return dict(r) if r else None


def plan(conn: sqlite3.Connection, *, account_id: str, ticket_id: str,
         rest: Set[str], now: datetime) -> Dict[str, Any]:
    """The decision, without writing: ``{"ok": bool, "why": str, "before": row,
    "to": status}``."""
    out: Dict[str, Any] = {"ok": False, "account_id": account_id, "ticket_id": ticket_id,
                           "before": None, "to": TERMINAL_STATUS}
    if account_id not in rest:
        out["why"] = (f"{account_id} is not a REST-executed account (prop_platforms.yaml); "
                      f"a manual/phone ticket may have been placed by a human -- use the "
                      f"Telegram ❌ button there")
        return out
    row = _row(conn, ticket_id)
    out["before"] = row
    if row is None:
        out["why"] = "ticket not found"
        return out
    if row["account_id"] != account_id:
        out["why"] = f"ticket belongs to {row['account_id']}, not {account_id}"
        return out
    status = str(row.get("status") or "")
    if status == "expiry_prompted":
        pass
    elif status == "emitted":
        vu = _parse_ts(row.get("valid_until"))
        if vu is None:
            out["why"] = "emitted with no readable valid_until (could not look; refused)"
            return out
        if vu > now:
            out["why"] = f"emitted and still valid until {vu.isoformat()}"
            return out
    else:
        out["why"] = f"status {status!r} is not expirable (only expiry_prompted, or emitted past valid_until)"
        return out
    n = conn.execute("SELECT COUNT(*) FROM prop_fills WHERE ticket_id = ?", (ticket_id,)).fetchone()[0]
    if n:
        out["why"] = f"{n} prop_fills row(s) reference this ticket; it may be a position -- refused"
        return out
    out["ok"] = True
    out["why"] = f"{status} -> {TERMINAL_STATUS}"
    return out


def apply(conn: sqlite3.Connection, decision: Dict[str, Any]) -> Dict[str, Any]:
    """Write one guarded UPDATE; return the row after it."""
    before = decision["before"]
    cur = conn.execute(
        "UPDATE prop_tickets SET status = ? WHERE ticket_id = ? AND account_id = ? AND status = ?",
        (TERMINAL_STATUS, before["ticket_id"], before["account_id"], before["status"]))
    if cur.rowcount != 1:
        conn.rollback()
        raise RuntimeError(f"guarded UPDATE matched {cur.rowcount} rows (ticket moved since the read?)")
    conn.commit()
    return _row(conn, before["ticket_id"]) or {}


def _backup(db_path: str) -> str:
    dest = f"{db_path}.bak-prop-ticket-expire-{int(time.time())}"
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--ticket-id", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--platforms", default=None, help="prop_platforms.yaml (tests)")
    ap.add_argument("--now", default=None, help="ISO time (tests)")
    a = ap.parse_args(argv)

    now = _parse_ts(a.now) if a.now else datetime.now(timezone.utc)
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        decision = plan(conn, account_id=a.account, ticket_id=a.ticket_id,
                        rest=rest_accounts(Path(a.platforms) if a.platforms else None), now=now)
    finally:
        conn.close()
    decision["mode"] = "apply" if a.apply else "dry_run"
    if not decision["ok"]:
        print(json.dumps(decision, indent=2, default=str))
        return 3
    if not a.apply:
        decision["after"] = {**decision["before"], "status": TERMINAL_STATUS}
        print(json.dumps(decision, indent=2, default=str))
        return 0
    decision["backup"] = _backup(a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        decision["after"] = apply(conn, decision)
    finally:
        conn.close()
    print(json.dumps(decision, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
