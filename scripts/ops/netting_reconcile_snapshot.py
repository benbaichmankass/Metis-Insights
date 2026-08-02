#!/usr/bin/env python3
"""Build the same-moment exchange snapshot the netting reconcile engine consumes
(BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED, option (c)+(b), operator-
approved 2026-08-02).

`scripts/ops/reconcile_netting_rows.py` is a pure planner driven by an INJECTED
exchange snapshot — deliberately socket-free so its logic is unit-tested with no
broker. This is the missing live-read half: it reads the OPEN non-pairs journal
groups + the LIVE per-account exchange positions (via ``account_open_positions``,
the SAME primitive ``/api/diag/exchange_positions`` and the live reconciler tick
use) + — for Bybit accounts — the resting protective-leg ids per symbol (for the
engine's (b) leg-fire precision layer), and emits the engine's input JSON:

    {"account/symbol/direction": {"size": <float>, "resting_legs": [<id>, ...]}, ...}

TRANSFORM CONTRACT (``build_snapshot`` — pure, unit-tested):

  * The journal direction is ``long`` / ``short`` (``trades.direction = pkg.direction``);
    the Bybit exchange side is ``Buy`` / ``Sell``. They are matched by a canonical
    form (``_canon_dir``), and the emitted KEY carries the journal's VERBATIM
    direction so it lines up with the engine's ``_group_key`` (which reads the
    same ``trades.direction`` column).
  * An account that reads OK-but-flat for a journal-open symbol/direction → **size 0**
    (the whole group is surplus → the engine closes it, oldest-first).
  * An account that COULD-NOT-READ (``account_open_positions`` → ``None``) → its
    groups are **OMITTED** entirely, so the engine's ``exchange.get(gk) is None``
    fail-safe SKIPS them — never close on an unconfirmed broker read.
  * Pairs-sleeve rows are excluded upstream (they never enter ``open_groups``);
    the engine also excludes ``strategy LIKE 'pairs_%'`` as a second guard.

LIVE READS (``main`` — the socket half, run on the VM by the wrapper):
  Journal groups are read FIRST, then the exchange — so a position opened between
  the two reads (present on the exchange, absent from the earlier journal set)
  can only SHRINK the computed surplus, never inflate it (a conservative race
  bias; the reconcile is idempotent regardless and a later run catches any
  residual). Every broker read is best-effort: a failure yields ``None`` for that
  account (→ omitted → skipped), never a fabricated size.

Usage (the wrapper captures stdout to a temp file it passes to the engine):
    python3 scripts/ops/netting_reconcile_snapshot.py                 # -> JSON on stdout
    python3 scripts/ops/netting_reconcile_snapshot.py --db /path/db --out exch.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.paths import trade_journal_db_path  # noqa: E402

Group = Tuple[str, str, str]


def _canon_dir(value: Any) -> str:
    """Canonicalize a journal direction (long/short) or an exchange side
    (Buy/Sell) to one axis so the two vocabularies match."""
    s = str(value or "").strip().lower()
    if s in ("long", "buy", "b", "l"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return s


def build_snapshot(
    open_groups: Iterable[Group],
    exchange_by_account: Dict[str, Optional[List[Dict[str, Any]]]],
    resting_by_group: Optional[Dict[Group, List[str]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Pure transform. See the module docstring for the full contract.

    ``exchange_by_account`` maps account_id -> ``None`` (could-not-read) | ``[]``
    (flat) | ``[{symbol, side, size}, ...]`` (live). A journal group whose account
    is ``None`` or absent from the map is OMITTED (engine fail-safe skips it).
    """
    resting_by_group = resting_by_group or {}
    out: Dict[str, Dict[str, Any]] = {}
    _sentinel = object()
    for acct, sym, direction in open_groups:
        ex = exchange_by_account.get(acct, _sentinel)
        # None (read failure) OR absent from the read set -> omit so the engine
        # skips the group rather than closing on an unconfirmed/unread broker.
        if ex is None or ex is _sentinel:
            continue
        want = _canon_dir(direction)
        size = 0.0
        for pos in ex:  # ex is a (possibly empty) list of exchange positions
            if str(pos.get("symbol")) == str(sym) and _canon_dir(pos.get("side")) == want:
                try:
                    size = abs(float(pos.get("size") or 0.0))
                except (TypeError, ValueError):
                    size = 0.0
                break
        entry: Dict[str, Any] = {"size": size}
        legs = resting_by_group.get((acct, sym, direction))
        if legs is not None:
            entry["resting_legs"] = list(legs)
        out[f"{acct}/{sym}/{direction}"] = entry
    return out


def _load_open_groups(conn: sqlite3.Connection) -> List[Group]:
    """DISTINCT (account_id, symbol, direction) over OPEN, non-backtest,
    non-pairs journal rows — the groups the engine will reconcile."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    strat_col = "strategy_name" if "strategy_name" in cols else (
        "strategy" if "strategy" in cols else None)
    where = ["status='open'"]
    if "is_backtest" in cols:
        where.append("(is_backtest=0 OR is_backtest IS NULL)")
    if strat_col:
        # Pairs-sleeve rows are the pairs_executor's own state — never reconciled
        # here (mirrors reconcile_netting_rows._is_pairs).
        where.append(f"({strat_col} IS NULL OR {strat_col} NOT LIKE 'pairs\\_%' ESCAPE '\\')")
    q = ("SELECT DISTINCT account_id, symbol, direction FROM trades WHERE "
         + " AND ".join(where))
    groups: List[Group] = []
    for r in conn.execute(q):
        acct, sym, direction = r[0], r[1], r[2]
        if acct is None or sym is None or direction is None:
            continue
        groups.append((str(acct), str(sym), str(direction)))
    return groups


def _bybit_resting_legs(acc_cfg: Dict[str, Any], symbols: Iterable[str]) -> Dict[str, List[str]]:
    """{symbol: [resting protective-leg orderId, ...]} for a Bybit account, or an
    empty map on any failure (best-effort — the engine falls back to FIFO when a
    group has no resting-leg data)."""
    try:
        from src.units.accounts.clients import bybit_client_for
        from src.units.accounts.execute import _bybit_category
        client = bybit_client_for(acc_cfg)
        if client is None:
            return {}
        category = _bybit_category(acc_cfg)
    except Exception:  # noqa: BLE001 - best-effort read; no legs -> FIFO fallback
        return {}
    out: Dict[str, List[str]] = {}
    for sym in sorted(set(symbols)):
        try:
            resp = client.get_open_orders(category=category, symbol=sym, orderFilter="StopOrder")
            legs = ((resp or {}).get("result") or {}).get("list") or []
            out[sym] = [str(o.get("orderId")) for o in legs if o.get("orderId")]
        except Exception:  # noqa: BLE001 - per-symbol read failure -> skip that symbol
            continue
    return out


def _collect_live(db_path: Path) -> Dict[str, Dict[str, Any]]:
    """Read journal groups + live exchange positions + resting legs and build the
    engine snapshot. Journal FIRST, then exchange (conservative race bias)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        open_groups = _load_open_groups(conn)
    finally:
        conn.close()

    accounts_in_scope = {g[0] for g in open_groups}
    from src.units.ui.data_loaders import account_open_positions, list_accounts
    try:
        all_accounts = list_accounts() or []
    except Exception:  # noqa: BLE001
        all_accounts = []
    acc_by_id = {(a or {}).get("account_id"): a for a in all_accounts}

    exchange_by_account: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    resting_by_group: Dict[Group, List[str]] = {}
    for acct in sorted(accounts_in_scope):
        acc_cfg = acc_by_id.get(acct)
        if acc_cfg is None:
            # Account no longer in config -> can't confirm -> omit (fail-safe).
            exchange_by_account[acct] = None
            continue
        try:
            positions = account_open_positions(acc_cfg)
        except Exception:  # noqa: BLE001 - read failure -> None -> engine skips
            positions = None
        exchange_by_account[acct] = positions
        # (b) precision: resting protective legs, Bybit only, best-effort.
        if positions is not None and str(acc_cfg.get("exchange") or "").lower() == "bybit":
            syms = {g[1] for g in open_groups if g[0] == acct}
            legs_by_sym = _bybit_resting_legs(acc_cfg, syms)
            for g in open_groups:
                if g[0] == acct and g[1] in legs_by_sym:
                    resting_by_group[g] = legs_by_sym[g[1]]

    return build_snapshot(open_groups, exchange_by_account, resting_by_group)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="trade_journal.db (default: canonical resolver)")
    ap.add_argument("--out", default=None, help="write JSON here (default: stdout)")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else Path(trade_journal_db_path())
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    snapshot = _collect_live(db_path)
    text = json.dumps(snapshot, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {len(snapshot)} group(s) to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
