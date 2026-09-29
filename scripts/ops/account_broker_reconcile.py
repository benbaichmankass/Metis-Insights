#!/usr/bin/env python3
"""Journal-vs-broker reconciliation for ``alpaca_live``, ``breakout_1``, and
``bybit_2`` (as far as its API allows) — read-only, detector-only.

WHY THIS EXISTS
================
``journalTrust`` (``src/runtime/journal_trust.py``, surfaced on
``/api/bot/trades/closed`` and ``/api/bot/performance``) can only ever flag a
divergence for an account that HAS a broker-truth record in
``comms/broker_truth_ledger.json`` — and that ledger has exactly one entry
(``bybit_2``, hand-populated, itself stale — see
``PI-20260924-ZROYCDY4-0001``). ``alpaca_live`` and ``breakout_1`` return
``no_record`` there today, and per the "collapsed states" rule
(``docs/CLAUDE-RULES-CANONICAL.md``) that means **nobody has reconciled the
account, not that it agrees** — filed as
``OI-20260826-JOURNAL-TRUST-COVERS-ONE-ACCOUNT``, operator-directed
2026-09-28 ("Build automated reconcile") rather than left to a one-time hand
export.

This tool is that automated reconciliation. It checks, per account:

  1. **open positions** — symbol / side / quantity, journal vs broker;
  2. **protective legs** — is each open journal position covered by a
     resting stop / target at the broker;
  3. **realized P&L** — journal vs broker, over the last N closed trades
     WHERE THE BROKER EXPOSES IT (it does not, uniformly — see below).

THREE-STATE, NEVER COLLAPSED (the repo's own rule, applied here)
==================================================================
Per account per check: ``agree`` (a confirmed match) / ``divergent`` (a
confirmed mismatch — a finding) / ``could_not_look`` (a read failed — NOT
graded as agreement) / ``not_exposed`` (the broker genuinely does not surface
this figure through any read this repo has — a scope limit, not a failure).
A ``could_not_look`` or ``not_exposed`` state never contributes "0
divergences" to the summary; each is counted and named separately, the same
discipline ``broker_bracket_reconcile.py`` already applies to IB.

SCOPE LIMITS, STATED RATHER THAN HIDDEN
========================================
  * **bybit_2** — the account's own broker-truth ledger entry is
    ``known_divergent`` (a real, already-tracked SUB-subaccount gap; see
    ``PI-20260924-ZROYCDY4-0001`` / ``OI-20260831-LIVE-WALLET-TRUTH-CANNOT-
    REPRODUCE-THE-LEDGER-WINDOW``). This tool's realized-P&L check for
    bybit_2 reads ``/api/diag/bybit_wallet_truth`` — the SAME
    ``measured_api`` floor that item already documents, not a new or
    independent broker read — so a ``bybit_2`` P&L divergence found here
    inherits that same known-partial-coverage caveat; it is reported, not
    silently trusted as complete.
  * **alpaca_live** — Alpaca equities/crypto exposes no broker-side
    per-trade or aggregate realized-P&L read in this codebase
    (``account_closed_pnl_for_trade`` returns ``None`` for ``alpaca``
    outside the options-activities path, which does not apply to this
    roster; see ``src/units/accounts/clients.py``). The P&L check for
    ``alpaca_live`` is therefore always ``not_exposed`` — a stated scope
    limit, not a defect in this tool.
  * **breakout_1** — the only broker-exposed P&L figure is
    ``realized_today`` (today's cumulative realized P&L on the account
    status snapshot), not a per-trade or last-N figure — DXtrade's manual
    bridge has no trade-history read. Compared against the journal's sum of
    ``prop_fills.pnl`` for fills closed today (UTC). This is a DAILY
    reconciliation for breakout_1, not a last-N-trade one; the render says
    so explicitly rather than implying finer granularity than it has.
  * **breakout_1 broker positions** depend on ``ict-prop-feed`` posting
    ``open_positions`` on its account_status tick — wired in the same PR as
    this tool (``scripts/prop/breakout_login_check.py``). Before that lane's
    feed has posted at least once since the extension deployed, this check
    reads ``could_not_look`` (the field is absent from the stored row), which
    is the honest state, not a bug in the comparator.
  * **breakout_1 positions/orders are a KNOWN BLIND READER for an empty
    result as of 2026-09-29** (PR #14005, open, held for the manager): the
    terminal's grids are header-less body tables paired to a separate,
    always-empty header table, so ``read_positions``/``read_orders``
    returned 0 rows even with a real filled position on the account
    (measured live, #13987/#13992). Until that PR merges AND deploys AND a
    live read confirms non-zero rows, an EMPTY ``open_positions`` read is
    reported ``could_not_look`` (manager instruction 2026-09-29: "state
    'cannot read' explicitly, never 0"), never as agreement/flat — flip
    ``BREAKOUT_POSITIONS_READER_FIXED`` once that is confirmed. A
    NON-EMPTY read is unaffected by this bug and is still reconciled
    normally.

DATA SOURCES (read-only; no order, cancel, or DB write)
=========================================================
  journal   — ``GET /api/bot/positions?include_paper=true`` (open positions,
              declared stop/target); ``GET /api/bot/trades/closed`` (bybit_2
              closed trades); ``GET /api/bot/prop/status`` +
              ``GET /api/bot/prop/fills`` (breakout_1 journal side — the
              prop manual-bridge journal, not ``trades``).
  alpaca    — ``GET /api/diag/alpaca_open_orders?account_id=alpaca_live``
              (token-gated).
  reduce    — ``GET /api/diag/journal?table=trades&limit=1000`` (raw rows,
              token-gated; classifies intent_reduce legs by id, since the
              /trades/closed wire carries no setup_type/notes).
  bybit_2   — ``GET /api/diag/bybit_open_orders?account_id=bybit_2`` +
              ``GET /api/diag/bybit_wallet_truth?account_id=bybit_2&days=N``
              (token-gated).
  breakout_1 — ``GET /api/bot/prop/status?account_id=breakout_1`` (public;
              carries the broker-read snapshot AND, via
              ``rule_distance.open_risk.positions``, the journal side).

Exit codes: 0 = every check on every account agreed (or was a stated scope
limit) · 1 = at least one ``divergent`` finding · 2 = usage/parse error ·
3 = at least one ``could_not_look`` and zero ``divergent`` findings (nothing
was graded wrong, but not everything was graded either).

SCHEDULED CALLER
=================
``.github/workflows/account-broker-reconcile.yml`` — same pattern as
``broker-bracket-reconcile.yml``: a tracking issue rewritten every run,
a comment only when ``fingerprint()`` moves. Silence there means "the same
set of problems as last time", never "no problems".

Usage
-----
    python3 scripts/ops/account_broker_reconcile.py \\
        --journal-positions-json j_pos.json \\
        --journal-closed-json j_closed.json \\
        --alpaca-broker-json alpaca.json \\
        --bybit-broker-json bybit.json \\
        --bybit-wallet-truth-json bybit_wt.json \\
        --prop-status-json prop_status.json \\
        --prop-fills-json prop_fills.json

    python3 scripts/ops/account_broker_reconcile.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The ONE reduce-leg definition: the row-level mirror of
# ``src.web.api._clean_trades.exclude_reduce_leg_predicate`` (setup_type
# ``intent_reduce`` OR the ``notes.intent_reduce`` flag). Imported, never
# re-spelled here.
from src.runtime.bracket_outcome import is_reduce_leg  # noqa: E402

QTY_TOLERANCE = 1e-6
PNL_TOLERANCE_USD = 1.0  # a $1 disagreement is float/fee noise, not a finding

STATES = ("agree", "divergent", "could_not_look", "not_exposed")

# PR #14005 (open, held for the manager as of 2026-09-29) fixes a measured
# blind-reader bug: the Breakout DXtrade terminal's Positions/Orders grids
# are HEADER-LESS body tables paired to a separate, always-empty header
# table, and `EXTRACT_TABLES_JS` only ever read the headed one — so
# `read_positions()`/`read_orders()` return 0 rows EVEN WHEN THE TERMINAL
# HOLDS REAL POSITIONS (measured live, #13987/#13992: a filled 0.01 SOLUSD
# position read as 0 positions from a fresh login). An EMPTY
# `open_positions` read from breakout_1 is therefore NOT currently
# trustworthy evidence of "flat" — manager instruction 2026-09-29: keep
# breakout_1's positions/protection check read-only and state "cannot read"
# explicitly rather than a false, confident 0. A NON-EMPTY read is not
# subject to this bug (the defect is a false negative, never a false row),
# so it is still trusted and reconciled normally.
#
# Flip this to True (and drop the special case below) once #14005 merges
# AND deploys AND a live read confirms non-zero rows on an account known to
# hold a position — not merely on the PR merging, per this repo's own
# "merged != deployed != observed" rule.
BREAKOUT_POSITIONS_READER_FIXED = False


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------

def _side_key(side: Any) -> Optional[str]:
    """Normalise a side/direction string to 'long' | 'short' | None."""
    s = str(side or "").strip().lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _today_utc_date(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# position matching — shared across alpaca_live and bybit_2
# --------------------------------------------------------------------------

def match_positions(
    journal_rows: List[Dict[str, Any]], broker_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-symbol comparison of journal-declared vs broker-reported open
    positions. ``journal_rows`` / ``broker_rows`` are already filtered to one
    account, each row carrying at least ``symbol``, ``side``/``direction``,
    ``qty``/``quantity``.

    Returns one row per symbol seen on EITHER side:
      ``position_match``  — same side, qty within tolerance
      ``qty_mismatch``    — same side, qty differs
      ``side_mismatch``   — both sides have the symbol, opposite side
      ``journal_only``    — the journal declares it open, the broker does not
      ``broker_only``     — the broker holds it, the journal has no open row
    """
    def _index(rows: List[Dict[str, Any]], side_key: str, qty_key: str) -> Dict[str, Dict[str, Any]]:
        idx: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            sym = str(r.get("symbol") or "").strip().upper()
            if not sym:
                continue
            idx[sym] = {
                "side": _side_key(r.get(side_key)),
                # "size" is bybit's own field name (_bybit_position_row,
                # src/units/accounts/clients.py) -- without it every bybit_2
                # broker row read qty=None and matched as qty_mismatch on
                # EVERY run with an open position (REVIEW-14054 finding #2).
                "qty": _num(r.get(qty_key)) or _num(r.get("qty"))
                       or _num(r.get("quantity")) or _num(r.get("size")),
                "raw": r,
            }
        return idx

    j_idx = _index(journal_rows, "side", "qty")
    b_idx = _index(broker_rows, "side", "qty")

    out: List[Dict[str, Any]] = []
    for sym in sorted(set(j_idx) | set(b_idx)):
        j, b = j_idx.get(sym), b_idx.get(sym)
        if j and not b:
            out.append({"symbol": sym, "state": "journal_only",
                        "journal_side": j["side"], "journal_qty": j["qty"],
                        "broker_side": None, "broker_qty": None})
            continue
        if b and not j:
            out.append({"symbol": sym, "state": "broker_only",
                        "journal_side": None, "journal_qty": None,
                        "broker_side": b["side"], "broker_qty": b["qty"]})
            continue
        # both present
        if j["side"] is not None and b["side"] is not None and j["side"] != b["side"]:
            state = "side_mismatch"
        elif (j["qty"] is None) != (b["qty"] is None):
            state = "qty_mismatch"  # one side unmeasured, never silently agree
        elif j["qty"] is not None and b["qty"] is not None and abs(j["qty"] - b["qty"]) > QTY_TOLERANCE:
            state = "qty_mismatch"
        else:
            state = "position_match"
        out.append({"symbol": sym, "state": state,
                    "journal_side": j["side"], "journal_qty": j["qty"],
                    "broker_side": b["side"], "broker_qty": b["qty"]})
    return out


def protection_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate ``position_match`` rows carrying explicit
    ``has_stop``/``has_target`` (set by the caller, venue-specific) into a
    naked-count, never counting a ``journal_only``/``broker_only``/
    ``*_mismatch`` row as protected — a symbol whose identity is already in
    dispute has nothing to grade a protective leg against.
    """
    checked = [r for r in rows if r["state"] == "position_match" and "has_stop" in r]
    naked = [r for r in checked if not r.get("has_stop") and not r.get("has_target")]
    partial = [r for r in checked if bool(r.get("has_stop")) != bool(r.get("has_target"))]
    return {"checked": len(checked), "naked": [r["symbol"] for r in naked],
            "partial": [r["symbol"] for r in partial]}


# --------------------------------------------------------------------------
# per-account readers — each returns {"state": one of STATES ∪ {"n/a"}, ...}
# --------------------------------------------------------------------------

def _diag_account_row(payload: Optional[Dict[str, Any]], account_id: str) -> Optional[Dict[str, Any]]:
    """Pull one account's row out of a ``/api/diag/*_open_orders`` envelope
    (``{"accounts": [{"account_id": ..., "read_state": ..., "result": ...}]}``).
    ``None`` if the payload itself is missing/unreadable — that is
    ``could_not_look`` at the caller, never an empty result."""
    if not isinstance(payload, dict):
        return None
    for row in payload.get("accounts") or []:
        if isinstance(row, dict) and row.get("account_id") == account_id:
            return row
    return None


def reconcile_alpaca(
    journal_positions: List[Dict[str, Any]],
    alpaca_payload: Optional[Dict[str, Any]],
    account_id: str = "alpaca_live",
) -> Dict[str, Any]:
    row = _diag_account_row(alpaca_payload, account_id)
    if row is None:
        return {"account_id": account_id, "positions_state": "could_not_look",
                "positions": [], "protection_state": "could_not_look",
                "protection": None, "pnl_state": "not_exposed", "pnl": None}

    result = row.get("result")
    read_state = row.get("read_state")
    if read_state != "orders_read" or not isinstance(result, dict) or result.get("positions") is None:
        return {"account_id": account_id, "positions_state": "could_not_look",
                "positions": [], "protection_state": "could_not_look",
                "protection": None, "pnl_state": "not_exposed", "pnl": None}

    broker_positions = result.get("positions") or []
    orders = result.get("orders") or []
    j_rows = [p for p in journal_positions if p.get("account") == account_id]
    matched = match_positions(j_rows, broker_positions)

    # Alpaca carries NO position-level stop/target (account_alpaca_open_orders
    # docstring) — the resting ORDERS are the whole story. A stop/stop_limit
    # order on the symbol is the stop leg; a limit order that is part of a
    # bracket/oco/oto is the target leg.
    def _orders_for(sym: str) -> List[Dict[str, Any]]:
        return [o for o in orders if str(o.get("symbol") or "").upper() == sym]

    for r in matched:
        if r["state"] != "position_match":
            continue
        sym_orders = _orders_for(r["symbol"])
        r["has_stop"] = any(
            str(o.get("order_type") or "").lower() in ("stop", "stop_limit")
            for o in sym_orders)
        r["has_target"] = any(
            str(o.get("order_type") or "").lower() == "limit"
            and str(o.get("order_class") or "").lower() in ("bracket", "oco", "oto")
            for o in sym_orders)

    positions_state = "divergent" if any(
        r["state"] != "position_match" for r in matched) else "agree"
    prot = protection_summary(matched)
    protection_state = "divergent" if (prot["naked"] or prot["partial"]) else (
        "agree" if prot["checked"] else "agree")

    return {"account_id": account_id, "positions_state": positions_state,
            "positions": matched, "protection_state": protection_state,
            "protection": prot,
            # Alpaca equities/crypto exposes no broker realized-P&L read in
            # this codebase (see module docstring) — a stated scope limit.
            "pnl_state": "not_exposed", "pnl": None}


def reduce_leg_ids(raw_trades: Optional[List[Dict[str, Any]]]) -> Optional[set]:
    """Ids of the raw ``trades`` rows (``/api/diag/journal?table=trades`` —
    every column, incl. ``setup_type`` and ``notes``) that
    ``bracket_outcome.is_reduce_leg`` classifies as reduce legs.

    ``/api/bot/trades/closed`` carries neither ``setup_type`` nor ``notes``,
    so a wire row alone cannot be classified; its id is looked up here.
    ``None`` = the raw read was not supplied or unreadable (we could not
    look), distinct from an empty set (we looked; no reduce legs)."""
    if isinstance(raw_trades, dict):
        raw_trades = raw_trades.get("rows")
    if not isinstance(raw_trades, list):
        return None
    return {str(r.get("id")) for r in raw_trades
            if isinstance(r, dict) and is_reduce_leg(r)}


def _is_reduce_row(row: Dict[str, Any], reduce_ids: Optional[set]) -> bool:
    """A closed-trade row is a reduce leg when it carries ``setup_type`` /
    ``notes`` that ``is_reduce_leg`` flags (a raw journal row), or when its id
    is in ``reduce_ids`` (a wire row, classified from the raw read)."""
    if is_reduce_leg(row):
        return True
    return reduce_ids is not None and str(row.get("id")) in reduce_ids


def _row_pnl(row: Dict[str, Any]) -> Optional[float]:
    """``/api/bot/trades/closed`` names the field ``realizedPnl``; a raw
    journal row names it ``pnl``. Reading only ``pnl`` off the wire made
    EVERY row None (first live run, #14113 — n_closes 20, sum None)."""
    if "realizedPnl" in row:
        return _num(row.get("realizedPnl"))
    return _num(row.get("pnl"))


def reconcile_bybit(
    journal_positions: List[Dict[str, Any]],
    journal_closed: List[Dict[str, Any]],
    bybit_payload: Optional[Dict[str, Any]],
    wallet_truth_payload: Optional[Dict[str, Any]],
    account_id: str = "bybit_2",
    pnl_trade_count: int = 20,
    raw_trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    reduce_ids = reduce_leg_ids(raw_trades)
    row = _diag_account_row(bybit_payload, account_id)
    if row is None:
        return {"account_id": account_id, "positions_state": "could_not_look",
                "positions": [], "protection_state": "could_not_look",
                "protection": None, "pnl_state": "could_not_look", "pnl": None}

    result = row.get("result")
    if not isinstance(result, dict) or result.get("positions") is None:
        return {"account_id": account_id, "positions_state": "could_not_look",
                "positions": [], "protection_state": "could_not_look",
                "protection": None, "pnl_state": "could_not_look", "pnl": None}

    broker_positions = result.get("positions") or []
    broker_orders = result.get("orders") or []
    j_rows = [p for p in journal_positions if p.get("account") == account_id]
    matched = match_positions(j_rows, broker_positions)

    for r in matched:
        if r["state"] != "position_match":
            continue
        sym = r["symbol"]
        b = next((p for p in broker_positions
                  if str(p.get("symbol") or "").upper() == sym), {})
        has_stop = _num(b.get("stop_loss")) is not None
        has_target = _num(b.get("take_profit")) is not None
        if not has_stop and not has_target:
            # Full-mode fields are both empty — fall back to checking for a
            # resting Partial-mode order on the symbol rather than declaring
            # NAKED outright; a resting order we did not classify is reported
            # softer than a confirmed-empty protection surface.
            sym_orders = [o for o in broker_orders
                          if str(o.get("symbol") or "").upper() == sym]
            if sym_orders:
                r["has_stop"] = r["has_target"] = None  # unclassified, not naked
                r["partial_mode_orders_present"] = len(sym_orders)
                continue
        r["has_stop"], r["has_target"] = has_stop, has_target

    positions_state = "divergent" if any(
        r["state"] != "position_match" for r in matched) else "agree"
    prot = protection_summary([r for r in matched if r.get("has_stop") is not None
                               or r.get("has_target") is not None])
    protection_state = "divergent" if (prot["naked"] or prot["partial"]) else "agree"

    # Realized P&L: journal sum over the last `pnl_trade_count` bybit_2
    # closes vs the wallet-truth measured_api figure. ⚠️ The wallet-truth
    # read is a fixed `days=N` window set by the caller, NOT the span of
    # these closes — both spans are emitted below so the render states the
    # two populations instead of implying they match. See module docstring
    # for why this is the wallet-truth read, not the stale hand ledger.
    #
    # Reduce legs are dropped BEFORE the NULL check: an intent_reduce leg's
    # pnl is NULL BY DESIGN (BL-20260711 — deferred, never fabricated), so
    # counting it as "we could not look" made the whole comparison refuse
    # (first live run, issue #14113: trade 5702, an eth_pullback_2h reduce
    # leg). Any OTHER NULL-pnl row still yields could_not_look.
    acct_closes = [c for c in journal_closed
                   if c.get("account") == account_id or c.get("account_id") == account_id]
    excluded_reduce = [str(c.get("id")) for c in acct_closes if _is_reduce_row(c, reduce_ids)]
    closes = sorted(
        (c for c in acct_closes if not _is_reduce_row(c, reduce_ids)),
        key=lambda c: c.get("closedAt") or c.get("closed_at") or "",
        reverse=True,
    )[:pnl_trade_count]
    journal_pnl_sum = None
    null_pnl_ids: List[str] = []
    if closes:
        vals = [_row_pnl(c) for c in closes]
        null_pnl_ids = [str(c.get("id")) for c, v in zip(closes, vals) if v is None]
        if not null_pnl_ids:
            journal_pnl_sum = round(sum(vals), 8)
    journal_window = ([closes[-1].get("closedAt") or closes[-1].get("closed_at"),
                       closes[0].get("closedAt") or closes[0].get("closed_at")]
                      if closes else None)

    wt = None
    if isinstance(wallet_truth_payload, dict):
        accounts = wallet_truth_payload.get("accounts")
        if isinstance(accounts, list):
            wt = next((a for a in accounts if a.get("account_id") == account_id), None)
        elif wallet_truth_payload.get("account_id") == account_id:
            wt = wallet_truth_payload

    if not closes:
        pnl_state, pnl = "agree", {"note": "no closed bybit_2 trades in the journal"}
    # WalletTruth.as_dict() (src/runtime/bybit_wallet_truth.py) names its
    # field "state", never "read_state" -- confirmed against the live route
    # (REVIEW-14054 finding #1); reading the wrong key made this permanently
    # could_not_look regardless of what the venue actually reported.
    elif journal_pnl_sum is None or not isinstance(wt, dict) or wt.get("state") != "measured_api":
        pnl_state = "could_not_look"
        pnl = {"journal_pnl_sum": journal_pnl_sum, "wallet_truth": wt,
               "n_closes": len(closes), "null_pnl_trade_ids": null_pnl_ids,
               "reduce_legs_excluded": excluded_reduce,
               "reduce_leg_read": "unavailable" if reduce_ids is None else "read",
               "journal_closed_at_window": journal_window}
    else:
        broker_realized = _num(wt.get("realized_usd"))
        diverges = (broker_realized is None or
                    abs(journal_pnl_sum - broker_realized) > PNL_TOLERANCE_USD)
        pnl_state = "divergent" if diverges else "agree"
        # WalletTruth carries window_start_ms/window_end_ms, never "window"
        # (REVIEW-14054 finding #3).
        pnl = {"journal_pnl_sum": journal_pnl_sum, "broker_realized_usd": broker_realized,
               "n_closes": len(closes),
               "reduce_legs_excluded": excluded_reduce,
               "journal_closed_at_window": journal_window,
               "wallet_truth_window_ms": [wt.get("window_start_ms"), wt.get("window_end_ms")]}

    return {"account_id": account_id, "positions_state": positions_state,
            "positions": matched, "protection_state": protection_state,
            "protection": prot, "pnl_state": pnl_state, "pnl": pnl,
            "known_caveat": "bybit_2's SUB-subaccount ledger gap "
                            "(journalTrust known_divergent) means this account's "
                            "own broker-truth is already documented as partial; "
                            "see PI-20260924-ZROYCDY4-0001."}


def reconcile_breakout(
    prop_status_payload: Optional[Dict[str, Any]],
    prop_fills: List[Dict[str, Any]],
    account_id: str = "breakout_1",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not isinstance(prop_status_payload, dict) or not prop_status_payload.get("present"):
        return {"account_id": account_id, "positions_state": "could_not_look",
                "positions": [], "protection_state": "could_not_look",
                "protection": None, "pnl_state": "could_not_look", "pnl": None}

    status = prop_status_payload.get("status") or {}
    rule_distance = prop_status_payload.get("rule_distance") or {}
    open_risk = rule_distance.get("open_risk") or {}
    j_positions_raw = open_risk.get("positions") or []
    # find_open_prop_positions rows: symbol / direction / qty / entry_price / sl
    j_rows = [{"symbol": p.get("symbol"), "side": p.get("direction"),
               "qty": p.get("qty")} for p in j_positions_raw]

    raw = status.get("raw")
    broker_positions_field: Any = "__missing__"
    if isinstance(raw, str):
        try:
            broker_positions_field = json.loads(raw).get("open_positions", "__missing__")
        except (ValueError, TypeError):
            broker_positions_field = "__missing__"
    elif isinstance(raw, dict):
        broker_positions_field = raw.get("open_positions", "__missing__")

    if broker_positions_field in ("__missing__", None):
        # Either the feed extension has not posted yet (missing key — an old
        # row from before this PR) or the terminal read raised (None, an
        # explicit "we did not look"). Both are could_not_look here; they are
        # distinguishable in the raw payload for a human reading the render.
        return {"account_id": account_id, "positions_state": "could_not_look",
                "positions": [], "protection_state": "could_not_look",
                "protection": None,
                "pnl_state": "could_not_look", "pnl": None,
                "note": "prop_account_status.raw has no open_positions "
                        "(feed not yet posted since the extension, or the "
                        "terminal read failed this tick)"}

    if not broker_positions_field and not BREAKOUT_POSITIONS_READER_FIXED:
        # An EMPTY read is exactly the shape the known blind-reader bug
        # produces (PR #14005) — do not read it as "flat". Report
        # could_not_look rather than a false, confident 0, per the
        # manager's 2026-09-29 instruction. Realized-P&L is unaffected (it
        # comes from the account snapshot, not the positions/orders grids)
        # and is still checked below.
        matched, prot = [], None
        positions_state = protection_state = "could_not_look"
        reader_note = ("breakout_1 positions/orders read is a KNOWN BLIND "
                       "READER for an empty result until PR #14005 merges + "
                       "deploys (measured live: a filled position read as 0 "
                       "rows) — an empty read is reported cannot-read, never "
                       "trusted as flat.")
    else:
        broker_rows = [{"symbol": p.get("symbol"), "side": p.get("side"),
                        "qty": p.get("quantity")} for p in broker_positions_field]
        matched = match_positions(j_rows, broker_rows)
        for r in matched:
            if r["state"] != "position_match":
                continue
            sym = r["symbol"]
            b = next((p for p in broker_positions_field
                      if str(p.get("symbol") or "").upper() == sym), {})
            r["has_stop"] = _num(b.get("stop_loss")) is not None
            r["has_target"] = _num(b.get("take_profit")) is not None

        positions_state = "divergent" if any(
            r["state"] != "position_match" for r in matched) else "agree"
        prot = protection_summary(matched)
        protection_state = "divergent" if (prot["naked"] or prot["partial"]) else "agree"
        reader_note = None

    # Daily realized P&L: broker's realized_today vs journal sum of
    # prop_fills.pnl closed today (UTC). NOT a last-N-trade comparison —
    # see module docstring.
    broker_realized_today = _num(status.get("realized_today"))
    today = _today_utc_date(now)
    todays_closed = [
        f for f in prop_fills
        if str(f.get("status") or "").lower() in ("closed", "filled_closed")
        and str(f.get("closed_at") or "").startswith(today)
    ]
    journal_vals = [_num(f.get("pnl")) for f in todays_closed]
    journal_realized_today = (round(sum(v for v in journal_vals if v is not None), 8)
                              if journal_vals else 0.0)
    if broker_realized_today is None:
        pnl_state = "could_not_look"
        pnl = {"journal_realized_today": journal_realized_today, "broker_realized_today": None}
    else:
        diverges = abs(journal_realized_today - broker_realized_today) > PNL_TOLERANCE_USD
        pnl_state = "divergent" if diverges else "agree"
        pnl = {"journal_realized_today": journal_realized_today,
               "broker_realized_today": broker_realized_today,
               "n_closed_today": len(todays_closed), "scope": "daily, not last-N"}

    result = {"account_id": account_id, "positions_state": positions_state,
              "positions": matched, "protection_state": protection_state,
              "protection": prot, "pnl_state": pnl_state, "pnl": pnl}
    if reader_note:
        result["note"] = reader_note
    return result


# --------------------------------------------------------------------------
# run / render / fingerprint
# --------------------------------------------------------------------------

def run(
    journal_positions: List[Dict[str, Any]],
    journal_closed: List[Dict[str, Any]],
    alpaca_payload: Optional[Dict[str, Any]],
    bybit_payload: Optional[Dict[str, Any]],
    wallet_truth_payload: Optional[Dict[str, Any]],
    prop_status_payload: Optional[Dict[str, Any]],
    prop_fills: List[Dict[str, Any]],
    now: Optional[datetime] = None,
    raw_trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "captured_at": (now or datetime.now(timezone.utc)).isoformat(),
        "accounts": {
            "alpaca_live": reconcile_alpaca(journal_positions, alpaca_payload),
            "breakout_1": reconcile_breakout(prop_status_payload, prop_fills, now=now),
            "bybit_2": reconcile_bybit(journal_positions, journal_closed,
                                       bybit_payload, wallet_truth_payload,
                                       raw_trades=raw_trades),
        },
    }


def _account_states(acct: Dict[str, Any]) -> List[str]:
    return [acct["positions_state"], acct["protection_state"], acct["pnl_state"]]


def grade(result: Dict[str, Any]) -> int:
    """0 clean · 1 findings · 3 an account/check could not be read (and no
    findings) · never both — a divergent finding always outranks a read
    failure elsewhere, same precedence as broker_bracket_reconcile.py."""
    all_states = [s for acct in result["accounts"].values() for s in _account_states(acct)]
    if "divergent" in all_states:
        return 1
    if "could_not_look" in all_states:
        return 3
    return 0


def render(result: Dict[str, Any]) -> str:
    lines = [f"account-broker-reconcile @ {result['captured_at']}"]
    for account_id, acct in result["accounts"].items():
        lines.append(f"\n{account_id}:")
        lines.append(f"  positions: {acct['positions_state']}")
        for r in acct["positions"]:
            if r["state"] != "position_match":
                lines.append(f"    {r['symbol']}: {r['state']} "
                              f"(journal {r['journal_side']}/{r['journal_qty']} "
                              f"vs broker {r['broker_side']}/{r['broker_qty']})")
        lines.append(f"  protection: {acct['protection_state']}"
                     + (f" — naked: {acct['protection']['naked']}, "
                        f"partial: {acct['protection']['partial']}"
                        if acct.get("protection") else ""))
        lines.append(f"  realized_pnl: {acct['pnl_state']}"
                     + (f" — {acct['pnl']}" if acct.get("pnl") else ""))
        if acct.get("known_caveat"):
            lines.append(f"  caveat: {acct['known_caveat']}")
        if acct.get("note"):
            lines.append(f"  note: {acct['note']}")
    n_divergent = sum(1 for acct in result["accounts"].values()
                      for s in _account_states(acct) if s == "divergent")
    n_could_not_look = sum(1 for acct in result["accounts"].values()
                           for s in _account_states(acct) if s == "could_not_look")
    lines.append(f"\n{n_divergent} divergent finding(s) · "
                 f"{n_could_not_look} could-not-look check(s) across "
                 f"{len(result['accounts'])} account(s).")
    return "\n".join(lines)


def fingerprint(result: Dict[str, Any]) -> str:
    """A stable digest of the GRADED STATE (states + symbol-level findings),
    never of ``captured_at`` or exact P&L figures — a figure that merely
    worsens without changing which checks are divergent must not re-fire the
    workflow's comment, matching broker-bracket-reconcile.yml's own design."""
    shape = {}
    for account_id, acct in result["accounts"].items():
        shape[account_id] = {
            "positions_state": acct["positions_state"],
            "protection_state": acct["protection_state"],
            "pnl_state": acct["pnl_state"],
            "position_findings": sorted(
                f"{r['symbol']}:{r['state']}" for r in acct["positions"]
                if r["state"] != "position_match"),
            "naked": sorted((acct.get("protection") or {}).get("naked") or []),
            "partial": sorted((acct.get("protection") or {}).get("partial") or []),
        }
    digest = hashlib.sha256(
        json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]
    return digest + "\n" + json.dumps(shape, indent=1, sort_keys=True)


# --------------------------------------------------------------------------
# self-test — synthetic fixtures, one agree / divergent / could_not_look per
# account (mirrors the pytest file at tests/ops/test_account_broker_reconcile.py,
# kept here too so `--self-test` is runnable with zero test-runner dependency)
# --------------------------------------------------------------------------

def _self_test() -> int:
    failures = []

    def check(name: str, cond: bool) -> None:
        print(f"  [{'ok' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    # alpaca_live: agree
    j_pos = [{"account": "alpaca_live", "symbol": "AAPL", "side": "long", "qty": 10}]
    alpaca_ok = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
                 "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 10}],
                            "orders": [{"symbol": "AAPL", "order_type": "stop"},
                                       {"symbol": "AAPL", "order_type": "limit",
                                        "order_class": "bracket"}]}}]}
    r = reconcile_alpaca(j_pos, alpaca_ok)
    check("alpaca agree: positions", r["positions_state"] == "agree")
    check("alpaca agree: protection", r["protection_state"] == "agree")
    check("alpaca agree: pnl not_exposed", r["pnl_state"] == "not_exposed")

    # alpaca_live: divergent (qty mismatch + naked)
    alpaca_div = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
                  "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 7}],
                             "orders": []}}]}
    r = reconcile_alpaca(j_pos, alpaca_div)
    check("alpaca divergent: qty mismatch", r["positions_state"] == "divergent")

    # alpaca_live: qty/side agree but genuinely naked (matched position, no orders)
    alpaca_naked = {"accounts": [{"account_id": "alpaca_live", "read_state": "orders_read",
                    "result": {"positions": [{"symbol": "AAPL", "side": "long", "qty": 10}],
                               "orders": []}}]}
    r = reconcile_alpaca(j_pos, alpaca_naked)
    check("alpaca naked: positions still agree", r["positions_state"] == "agree")
    check("alpaca naked: protection divergent", r["protection_state"] == "divergent")
    check("alpaca naked: AAPL flagged", "AAPL" in (r["protection"] or {}).get("naked", []))

    # alpaca_live: could_not_look
    r = reconcile_alpaca(j_pos, {"accounts": []})
    check("alpaca could_not_look", r["positions_state"] == "could_not_look")

    # bybit_2: agree
    j_pos_b = [{"account": "bybit_2", "symbol": "BTCUSDT", "side": "long", "qty": 1}]
    j_closed = [{"account": "bybit_2", "symbol": "BTCUSDT", "pnl": 10.0,
                "closedAt": "2026-09-28T00:00:00Z"}]
    # Broker position rows use the REAL _bybit_position_row shape
    # (src/units/accounts/clients.py) -- "size", not "qty", and the raw
    # venue side string ("Buy"/"Sell") -- and wallet-truth rows use the
    # REAL WalletTruth.as_dict() shape (src/runtime/bybit_wallet_truth.py)
    # -- "state" + window_start_ms/window_end_ms, not "read_state"/"window".
    # REVIEW-14054: the OLD invented fixture shapes matched neither and hid
    # both bugs (#1 permanently could_not_look, #2 a constant false
    # qty_mismatch on every open bybit_2 position).
    bybit_ok = {"accounts": [{"account_id": "bybit_2",
                "result": {"positions": [{"symbol": "BTCUSDT", "side": "Buy", "size": 1.0,
                                          "entry_price": 60000.0, "stop_loss": 50000.0,
                                          "take_profit": 70000.0, "tpsl_mode": "Full",
                                          "position_idx": 0}],
                           "orders": []}}]}
    wt_ok = {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
             "realized_usd": 10.0, "window_start_ms": 1758000000000,
             "window_end_ms": 1759000000000}]}
    r = reconcile_bybit(j_pos_b, j_closed, bybit_ok, wt_ok)
    check("bybit agree: positions", r["positions_state"] == "agree")
    check("bybit agree: protection", r["protection_state"] == "agree")
    check("bybit agree: pnl", r["pnl_state"] == "agree")

    # bybit_2: divergent (side mismatch + naked + pnl)
    bybit_div = {"accounts": [{"account_id": "bybit_2",
                 "result": {"positions": [{"symbol": "BTCUSDT", "side": "Sell", "size": 1.0,
                                           "entry_price": 60000.0, "stop_loss": None,
                                           "take_profit": None, "tpsl_mode": None,
                                           "position_idx": 0}],
                            "orders": []}}]}
    wt_div = {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
              "realized_usd": -500.0, "window_start_ms": 1758000000000,
              "window_end_ms": 1759000000000}]}
    r = reconcile_bybit(j_pos_b, j_closed, bybit_div, wt_div)
    check("bybit divergent: side mismatch", r["positions_state"] == "divergent")
    check("bybit divergent: pnl", r["pnl_state"] == "divergent")

    # bybit_2: could_not_look
    r = reconcile_bybit(j_pos_b, j_closed, {"accounts": []}, None)
    check("bybit could_not_look", r["positions_state"] == "could_not_look"
          and r["pnl_state"] == "could_not_look")

    # breakout_1: agree
    prop_ok = {"present": True,
               "status": {"realized_today": 5.0,
                          "raw": json.dumps({"open_positions": [
                              {"symbol": "BTCUSD", "side": "long", "quantity": 0.1,
                               "stop_loss": 60000, "take_profit": 80000}]})},
               "rule_distance": {"open_risk": {"positions": [
                   {"symbol": "BTCUSD", "direction": "long", "qty": 0.1}]}}}
    fills_ok = [{"status": "closed", "closed_at": _today_utc_date() + "T01:00:00Z", "pnl": 5.0}]
    r = reconcile_breakout(prop_ok, fills_ok)
    check("breakout agree: positions", r["positions_state"] == "agree")
    check("breakout agree: protection", r["protection_state"] == "agree")
    check("breakout agree: pnl", r["pnl_state"] == "agree")

    # breakout_1: divergent (broker_only position + pnl mismatch)
    prop_div = {"present": True,
                "status": {"realized_today": 500.0,
                          "raw": json.dumps({"open_positions": [
                              {"symbol": "ETHUSD", "side": "short", "quantity": 1,
                               "stop_loss": None, "take_profit": None}]})},
                "rule_distance": {"open_risk": {"positions": []}}}
    r = reconcile_breakout(prop_div, fills_ok)
    check("breakout divergent: broker_only", r["positions_state"] == "divergent")
    check("breakout divergent: pnl", r["pnl_state"] == "divergent")

    # breakout_1: could_not_look (feed hasn't posted open_positions yet)
    prop_missing = {"present": True,
                    "status": {"realized_today": 0.0, "raw": json.dumps({})},
                    "rule_distance": {"open_risk": {"positions": []}}}
    r = reconcile_breakout(prop_missing, [])
    check("breakout could_not_look", r["positions_state"] == "could_not_look")

    # breakout_1: an EMPTY open_positions read is the known blind-reader
    # shape (PR #14005) — must be could_not_look, never a confident "flat".
    prop_empty_read = {"present": True,
                       "status": {"realized_today": 0.0,
                                  "raw": json.dumps({"open_positions": []})},
                       "rule_distance": {"open_risk": {"positions": []}}}
    r = reconcile_breakout(prop_empty_read, [])
    check("breakout empty-read is could_not_look, not agree",
          r["positions_state"] == "could_not_look"
          and r["protection_state"] == "could_not_look")
    check("breakout empty-read carries the #14005 note", "#14005" in (r.get("note") or ""))

    # fingerprint stability: same graded shape -> same digest even when the
    # exact P&L figures differ (still `agree`, just a different amount) and
    # `captured_at` differs.
    fixed_now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    fills_fixed = [{"status": "closed", "closed_at": "2026-09-28T01:00:00Z", "pnl": 5.0}]
    wt_ok_2 = {"accounts": [{"account_id": "bybit_2", "state": "measured_api",
               "realized_usd": 10.4, "window_start_ms": 1758000000000,
               "window_end_ms": 1759000000000}]}  # still within $1 tolerance of journal 10.0
    all_journal_positions = j_pos + j_pos_b
    res_a = run(all_journal_positions, j_closed, alpaca_ok, bybit_ok, wt_ok, prop_ok,
                fills_fixed, now=fixed_now)
    res_b = run(all_journal_positions, j_closed, alpaca_ok, bybit_ok, wt_ok_2, prop_ok,
                fills_fixed, now=fixed_now)
    check("fingerprint stable across captured_at/exact pnl",
          fingerprint(res_a).split("\n")[0] == fingerprint(res_b).split("\n")[0])
    check("grade() clean run is 0", grade(res_a) == 0)

    print(f"\n{'ALL OK' if not failures else f'{len(failures)} FAILURE(S)'}")
    return 0 if not failures else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load_json(path: Optional[str]) -> Any:
    if not path:
        return None
    with open(path) as fh:
        return json.load(fh)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal-positions-json", help="GET /api/bot/positions?include_paper=true")
    ap.add_argument("--journal-closed-json", help="GET /api/bot/trades/closed?account_id=bybit_2&...")
    ap.add_argument("--journal-trades-raw-json",
                    help="GET /api/diag/journal?table=trades&limit=1000 (raw rows; "
                         "used only to classify reduce legs by id)")
    ap.add_argument("--alpaca-broker-json", help="GET /api/diag/alpaca_open_orders?account_id=alpaca_live")
    ap.add_argument("--bybit-broker-json", help="GET /api/diag/bybit_open_orders?account_id=bybit_2")
    ap.add_argument("--bybit-wallet-truth-json", help="GET /api/diag/bybit_wallet_truth?account_id=bybit_2&...")
    ap.add_argument("--prop-status-json", help="GET /api/bot/prop/status?account_id=breakout_1")
    ap.add_argument("--prop-fills-json", help="GET /api/bot/prop/fills?account_id=breakout_1&limit=...")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the rendered report")
    ap.add_argument("--fingerprint", action="store_true", help="emit only the fingerprint digest + preimage")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not any((args.journal_positions_json, args.alpaca_broker_json,
                args.bybit_broker_json, args.prop_status_json)):
        ap.error("at least one input JSON is required (or use --self-test)")

    try:
        journal_positions = _load_json(args.journal_positions_json) or []
        journal_closed = _load_json(args.journal_closed_json) or []
        alpaca_payload = _load_json(args.alpaca_broker_json)
        bybit_payload = _load_json(args.bybit_broker_json)
        wallet_truth_payload = _load_json(args.bybit_wallet_truth_json)
        prop_status_payload = _load_json(args.prop_status_json)
        prop_fills = _load_json(args.prop_fills_json) or []
        raw_trades = _load_json(args.journal_trades_raw_json)
        if isinstance(prop_fills, dict):
            prop_fills = prop_fills.get("fills") or []
    except (OSError, ValueError) as exc:
        print(f"cannot read input JSON: {exc}", file=sys.stderr)
        return 2

    result = run(journal_positions, journal_closed, alpaca_payload, bybit_payload,
                 wallet_truth_payload, prop_status_payload, prop_fills,
                 raw_trades=raw_trades)

    if args.fingerprint:
        print(fingerprint(result))
        return grade(result)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return grade(result)
    print(render(result))
    return grade(result)


if __name__ == "__main__":
    raise SystemExit(main())
