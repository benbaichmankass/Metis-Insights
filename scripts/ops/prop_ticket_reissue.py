#!/usr/bin/env python3
"""Re-issue ONE suppressed prop ticket: ``suppressed`` -> ``emitted``, rebuilt.

Run by the ``prop-ticket-reissue`` system-action (Tier 2) on the live VM.
DRY-RUN by default: prints the ticket row as it is and as it would be.
``--apply`` takes an SQLite backup of the journal first, then writes.

The case it exists for (PROP-REISSUE, 2026-10-08): tradeify_1's ETH-short
ticket prop-manual-e6badec7ae10 rested as an unfilled LIMIT (``placed``) for
its whole validity while price ran away from it. The one-ticket-per-trade
guard (``_reticket_suppress_reason``) counts a ``placed`` ticket as
outstanding, so the NEXT in-band ETH-short signal (prop-manual-2116662b7d8d)
was journaled ``suppressed``. Once the resting order is cancelled at expiry the
suppressed signal is still a valid setup, but nothing could bring it back.

A ``suppressed`` row carries no qty, side, risk, validity or ticket text
(``emit_prop_ticket`` writes it before sizing). The executor refuses a ticket
with no qty ("ticket carries no positive qty") and reads the entry band from
the ticket TEXT, so flipping the status alone would be refused, or worse.
This script REBUILDS the ticket with the same functions emission uses, in the
same order (``prop_sizing.resolve`` -> ``build_account_leg`` ->
``prop_risk_gate.enforce_ticket_cap`` -> ``_leverage_refusal`` ->
``ticket_to_fields``), with ``signal_time = now``, so its validity is now + the
ticket's normal TTL (``ttl_bars`` x the strategy's timeframe). No new sizing
formula. Placement stays with the executor's own intake: entry band, expiry,
§ 3.3 rule guards, venue rounding and the $-cap all still decide.

Refused, fail-closed (exit 3, nothing written):
  * the account is not in ``config/accounts.yaml`` or its ``mode`` is not
    ``live``; the strategy is not on its roster or its ``execution`` is not
    ``live``;
  * the ticket is missing, belongs to another account, or is not
    ``suppressed``;
  * the ticket is older than ``--max-age-min`` (default 120) or its age
    cannot be read;
  * the suppression it carries does not name a blocking ticket (e.g. an open
    position blocked it), or that ticket is not TERMINAL
    (expired / cancelled / skipped / closed) or belongs to another account;
  * any ``prop_fills`` row references the ticket;
  * the reticket guard, re-run NOW, would still suppress the key;
  * the rebuild skips (sizing skip, risk-gate refusal, size rounds to zero,
    leverage cap).

The UPDATE is guarded on (ticket_id, account_id, status='suppressed'), so a
ticket that moved between the read and the write is not touched, and
re-running after an apply is a clean refusal.
Output is one JSON object with ``before`` / ``after`` rows.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FROM_STATUS = "suppressed"
TO_STATUS = "emitted"
#: Statuses that end a ticket for good: none of them can be a working order.
TERMINAL_STATUSES = ("expired", "cancelled", "skipped", "closed")
DEFAULT_MAX_AGE_MIN = 120

_BLOCKER_RE = re.compile(r"outstanding_ticket:([a-z_]+):\s*(prop-[a-z]+-[0-9a-f]+)")

_COLS = ("ticket_id, account_id, strategy, symbol, direction, side, entry, sl, tp, qty, "
         "risk_usd, signal_time, valid_until, status, order_package_id, message, meta, created_at")

Rebuild = Callable[[dict[str, Any], dict[str, Any], datetime], tuple[dict[str, Any] | None, str]]
GuardCheck = Callable[[str, str, str, datetime], str | None]


def _parse_ts(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT {_COLS} FROM prop_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return dict(r) if r else None


def account_cfg_for(account_id: str, accounts_path: Path | None = None) -> dict[str, Any] | None:
    """The account's ``config/accounts.yaml`` mapping plus ``account_id``.

    The live coordinator hands ``emit_prop_ticket`` a dict carrying the
    account's ``backtest_ruleset`` (which ruleset, so which size and routing)
    and its risk_pct; the raw mapping carries both (``risk.risk_pct``, which
    ``unit_for_account`` reads first). ``data_loaders._load_yaml_accounts``
    drops ``backtest_ruleset``, which would size a tradeify ticket against
    breakout.yaml's $5k, so it is NOT used here. None when unreadable."""
    from src.config.accounts_loader import load_accounts_dict

    acc = load_accounts_dict(accounts_path).get(account_id)
    if not isinstance(acc, dict):
        return None
    return {**acc, "account_id": account_id}


def strategy_gate(strategy: str, strategies_path: Path | None = None) -> tuple[str | None, str | None]:
    """(execution, timeframe) for a strategy in ``config/strategies.yaml``;
    (None, None) when it cannot be read."""
    import yaml

    p = Path(strategies_path) if strategies_path else _REPO_ROOT / "config" / "strategies.yaml"
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:  # noqa: BLE001 — could not look; the caller refuses
        return None, None
    block = data.get("strategies") if isinstance(data.get("strategies"), dict) else data
    e = (block or {}).get(strategy)
    if not isinstance(e, dict):
        return None, None
    return str(e.get("execution") or "live"), (str(e["timeframe"]) if e.get("timeframe") else None)


def rebuild_fields(row: dict[str, Any], account_cfg: dict[str, Any], now: datetime, *,
                   timeframe: str) -> tuple[dict[str, Any] | None, str]:
    """Rebuild the ticket fields with emission's own functions (mirrors
    ``emit_prop_ticket`` from the sizing step to the journal write; parity is
    asserted in tests). ``(fields, "")`` or ``(None, why)``."""
    from src.prop import breakout_executor as be
    from src.prop import prop_risk_gate, prop_sizing
    from src.prop.account_rulesets import unit_for_account
    from src.prop.breakout_notify import ticket_to_fields
    from src.prop.breakout_ticket import BreakoutSignal
    from src.prop.multi_account_ticket import build_account_leg

    account_id = str(row["account_id"])
    symbol, strategy = str(row["symbol"]), str(row["strategy"])
    sig = BreakoutSignal(strategy=strategy, symbol=symbol, direction=str(row["direction"]).lower(),
                         entry=float(row["entry"]), sl=float(row["sl"]), tp=float(row["tp"]),
                         timeframe=timeframe, signal_time=now)
    unit = unit_for_account(account_id, account_cfg)
    routing = be._load_routing(unit.source)
    sizing = prop_sizing.resolve(account_id, ruleset_path=unit.source, risk_pct=unit.risk_pct,
                                 strategy=strategy)
    if sizing.skip_reason:
        return None, f"sizing ({sizing.mode}) skips: {sizing.skip_reason}"
    cvpp = float(be._per_symbol(routing, symbol, "contract_value_usd_per_point", 1.0))
    kw = {"dxtrade_symbol": be._per_symbol(routing, symbol, "dxtrade_symbol", None),
          "contract_value_usd_per_point": cvpp,
          "entry_band_frac": float(routing.get("entry_band_frac") or 0.25),
          "ttl_bars": float(routing.get("ttl_bars") or 1.0)}
    leg = build_account_leg(sig, unit, risk_usd_override=sizing.risk_usd, **kw)
    gate_cap = None
    if leg.decision == "place" and leg.ticket is not None:
        cap = prop_risk_gate.enforce_ticket_cap(
            risk_usd=leg.ticket.risk_usd, cap_usd=sizing.cap_usd, sizing_mode=sizing.mode)
        if cap["action"] == prop_risk_gate.CAP_RESIZED:
            gate_cap = cap
            leg = build_account_leg(sig, unit, risk_usd_override=cap["risk_usd"], **kw)
        elif cap["action"] == prop_risk_gate.CAP_REFUSED:
            return None, f"risk gate refuses: {cap['cause']}"
    if leg.decision != "place" or leg.ticket is None:
        return None, f"leg skips: {leg.reason}"
    lev = be._leverage_refusal(account_id, unit, symbol, leg.ticket.qty_units, sig.entry, cvpp)
    if lev and prop_risk_gate.breach_guards_for(account_id) != "report":
        return None, f"leverage cap refuses: {lev}"
    message = ticket_to_fields(leg.ticket, account_id=account_id,
                               ticket_id=str(row["ticket_id"])).get("text")
    if not message:
        return None, "ticket text did not render (the executor reads the entry band from it)"
    meta: dict[str, Any] = {}
    if sizing.mode != prop_sizing.FLAT or gate_cap is not None or sizing.detail:
        meta = {"sizing_mode": sizing.mode, "sizing": sizing.detail, "risk_gate": gate_cap}
    return {"side": leg.ticket.side, "qty": leg.ticket.qty_units, "risk_usd": leg.ticket.risk_usd,
            "valid_until": leg.ticket.valid_until.isoformat(), "message": message, "meta": meta}, ""


def _live_guard(account_id: str, symbol: str, direction: str, now: datetime) -> str | None:
    from src.prop.breakout_executor import _reticket_suppress_reason

    return _reticket_suppress_reason(account_id, symbol, direction, now=now)


def plan(conn: sqlite3.Connection, *, account_id: str, ticket_id: str, now: datetime,
         account_cfg: dict[str, Any] | None, max_age_min: float,
         strategy_info: Callable[[str], tuple[str | None, str | None]],
         guard: GuardCheck, rebuild: Callable[..., tuple[dict[str, Any] | None, str]]
         ) -> dict[str, Any]:
    """The decision, without writing: ``{"ok", "why", "before", "fields"}``."""
    out: dict[str, Any] = {"ok": False, "account_id": account_id, "ticket_id": ticket_id,
                           "before": None, "to": TO_STATUS}

    def refuse(why: str) -> dict[str, Any]:
        out["why"] = why
        return out

    if account_cfg is None:
        return refuse(f"{account_id} is not in config/accounts.yaml (could not look; refused)")
    mode = str(account_cfg.get("mode") or "")
    if mode != "live":
        return refuse(f"{account_id} mode is {mode or 'unset'!r}, not 'live'")
    row = _row(conn, ticket_id)
    out["before"] = row
    if row is None:
        return refuse("ticket not found")
    if row["account_id"] != account_id:
        return refuse(f"ticket belongs to {row['account_id']}, not {account_id}")
    if str(row.get("status") or "") != FROM_STATUS:
        return refuse(f"status {row.get('status')!r} is not {FROM_STATUS!r}; only a suppressed ticket is re-issued")
    born = _parse_ts(row.get("signal_time")) or _parse_ts(row.get("created_at"))
    if born is None:
        return refuse("ticket age unreadable (no signal_time / created_at; could not look)")
    age = now - born
    if age > timedelta(minutes=max_age_min):
        return refuse(f"ticket is {age} old, over the {max_age_min:g} min re-issue bound")
    m = _BLOCKER_RE.search(str(row.get("message") or ""))
    if not m:
        return refuse(f"suppression names no blocking ticket ({row.get('message')!r}); "
                      f"only a ticket-blocked signal is re-issued")
    blocker_id = m.group(2)
    blocker = _row(conn, blocker_id)
    out["blocker"] = {k: blocker.get(k) for k in ("ticket_id", "account_id", "status", "valid_until")} if blocker else None
    if blocker is None:
        return refuse(f"blocking ticket {blocker_id} not found (could not look; refused)")
    if blocker["account_id"] != account_id:
        return refuse(f"blocking ticket {blocker_id} belongs to {blocker['account_id']}")
    if str(blocker.get("status") or "") not in TERMINAL_STATUSES:
        return refuse(f"blocking ticket {blocker_id} is {blocker.get('status')!r}, not terminal "
                      f"({'/'.join(TERMINAL_STATUSES)})")
    n = conn.execute("SELECT COUNT(*) FROM prop_fills WHERE ticket_id = ?", (ticket_id,)).fetchone()[0]
    if n:
        return refuse(f"{n} prop_fills row(s) reference this ticket; refused")
    roster = account_cfg.get("strategies") or []
    strategy = str(row.get("strategy") or "")
    if strategy not in roster:
        return refuse(f"{strategy} is not on {account_id}'s roster")
    execution, timeframe = strategy_info(strategy)
    if execution != "live":
        return refuse(f"{strategy} execution is {execution!r}, not 'live' (could not look counts as not live)")
    if not timeframe:
        return refuse(f"{strategy} has no readable timeframe in strategies.yaml (the TTL needs it)")
    still = guard(account_id, str(row["symbol"]), str(row["direction"]), now)
    if still:
        return refuse(f"the reticket guard still suppresses this key now: {still}")
    fields, why = rebuild(row, account_cfg, now, timeframe=timeframe)
    if fields is None:
        return refuse(f"rebuild refused: {why}")
    meta = dict(fields.get("meta") or {})
    meta["reissue"] = {"at": now.isoformat(), "blocked_by": blocker_id,
                       "blocker_status": blocker.get("status"), "suppressed_message": row.get("message")}
    out["fields"] = {**fields, "meta": meta}
    out["ok"] = True
    out["why"] = f"{FROM_STATUS} -> {TO_STATUS} (blocker {blocker_id} is {blocker.get('status')})"
    return out


def apply(conn: sqlite3.Connection, decision: dict[str, Any]) -> dict[str, Any]:
    """Write one guarded UPDATE; return the row after it."""
    b, f = decision["before"], decision["fields"]
    cur = conn.execute(
        "UPDATE prop_tickets SET status = ?, side = ?, qty = ?, risk_usd = ?, valid_until = ?, "
        "message = ?, meta = ? WHERE ticket_id = ? AND account_id = ? AND status = ?",
        (TO_STATUS, f["side"], f["qty"], f["risk_usd"], f["valid_until"], f["message"],
         json.dumps(f["meta"]), b["ticket_id"], b["account_id"], FROM_STATUS))
    if cur.rowcount != 1:
        conn.rollback()
        raise RuntimeError(f"guarded UPDATE matched {cur.rowcount} rows (ticket moved since the read?)")
    conn.commit()
    return _row(conn, b["ticket_id"]) or {}


def _backup(db_path: str) -> str:
    dest = f"{db_path}.bak-prop-ticket-reissue-{int(time.time())}"
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


def main(argv: list | None = None, *, guard: GuardCheck | None = None,
         rebuild: Callable[..., tuple[dict[str, Any] | None, str]] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--ticket-id", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-age-min", type=float, default=DEFAULT_MAX_AGE_MIN)
    ap.add_argument("--accounts", default=None, help="accounts.yaml (tests)")
    ap.add_argument("--strategies", default=None, help="strategies.yaml (tests)")
    ap.add_argument("--now", default=None, help="ISO time (tests)")
    a = ap.parse_args(argv)

    # The reticket guard and the sizing reads open the journal through the
    # canonical resolver; pin it to the DB this action was given, and restore
    # the caller's value after (an in-process caller, e.g. a test, must not
    # inherit it).
    with mock.patch.dict(os.environ, {"TRADE_JOURNAL_DB": str(Path(a.db).resolve())}):
        return _run(a, guard=guard, rebuild=rebuild)


def _run(a: argparse.Namespace, *, guard: GuardCheck | None,
         rebuild: Callable[..., tuple[dict[str, Any] | None, str]] | None) -> int:
    now = _parse_ts(a.now) if a.now else datetime.now(timezone.utc)
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        decision = plan(
            conn, account_id=a.account, ticket_id=a.ticket_id, now=now,
            account_cfg=account_cfg_for(a.account, Path(a.accounts) if a.accounts else None),
            max_age_min=a.max_age_min,
            strategy_info=lambda s: strategy_gate(s, Path(a.strategies) if a.strategies else None),
            guard=guard or _live_guard, rebuild=rebuild or rebuild_fields)
    finally:
        conn.close()
    decision["mode"] = "apply" if a.apply else "dry_run"
    if not decision["ok"]:
        print(json.dumps(decision, indent=2, default=str))
        return 3
    if not a.apply:
        f = decision["fields"]
        decision["after"] = {**decision["before"], "status": TO_STATUS,
                             **{k: f[k] for k in ("side", "qty", "risk_usd", "valid_until", "message")},
                             "meta": json.dumps(f["meta"])}
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
