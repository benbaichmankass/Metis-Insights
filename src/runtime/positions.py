"""Open-position helpers — read the live net position for an account+symbol,
and (NETTED-SLICES, 2026-10-15) the per-strategy SLICE ledger + admission for
accounts declaring ``position_model: slices`` (see the block at the bottom).

The trade journal (``trade_journal.db::trades``) is the authoritative
record of every live order the executor has placed. Rows transition from
``status='open'`` to ``'closed'`` via the monitor loop; the open subset
sums (signed by direction) to the current net position for an account
and symbol.

This helper exists so the intent-aware dispatch path in
``Coordinator.multi_account_execute`` can pick "are we already at the
desired target?" / "how much delta do we still need?" decisions from a
single read of the journal instead of duplicating the SELECT in
``_has_open_position`` style scattered across modules.

Schema reference
----------------
``CREATE TABLE trades`` columns relevant here (from
``src/units/db/database.py``):

  - id              INTEGER (PK)
  - direction       TEXT      ``"long" | "short"``
  - position_size   REAL      qty in base-coin units (BTC for BTCUSDT)
  - status          TEXT      ``"open"`` for live unfilled positions
  - is_backtest     BOOLEAN   filter to ``0`` (or NULL) for live rows
  - account_id      TEXT
  - symbol          TEXT

A journal-read failure returns ``None`` ("we could not look"), never ``0.0``
("we looked and it is flat"). Until FIX-CA-07 (CA-A02, 2026-09-27) it
returned ``0.0`` and the intent-mode dispatcher then sent a fresh full-size
``open`` on an account that already held the symbol; the dispatcher now
refuses that account for that package with ``net_position_unreadable``.
``current_net_position_qty`` still reads a missing journal file as flat;
``has_open_trade_for_strategy`` reads it as ``None`` (NO-HALT round 2,
2026-10-09 — see its docstring).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time as _time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)


def _trade_journal_path() -> str:
    from src.utils.paths import trade_journal_db_path
    return trade_journal_db_path()


# ---------------------------------------------------------------------------
# Position-netting / per-trade=per-position guard (BL-20260608-DEMOPNL)
# ---------------------------------------------------------------------------
#
# Option A from docs/audits/position-netting-sltp-2026-06-08.md. Two halves:
#
#   1. Monocle (coordinator intent path): suppress a same-direction
#      re-entry (delta action ``open`` / ``increase``) for a
#      ``(strategy, account, symbol)`` while it already holds an open
#      trade — so a netted ADD can't be created, restoring the
#      per-trade = per-position invariant. No pyramiding/scale-in.
#   2. Reconciler (order_monitor close path): require the position to read
#      net-flat across TWO observations (an extra grace tick) before
#      closing a filled trade, so reduce/flip churn and open-positions
#      index lag can't prematurely close a row and free the monocle.
#
# **BASELINE / unconditional** (2026-06-17). This was previously gated by a
# default-OFF ``POSITION_NETTING_GUARD_ENABLED`` flag during its soak. The soak
# + walk-forward validation passed and it ran live on both Bybit accounts from
# 2026-06-08 — until the 2026-06-14 Ampere migration dropped the .env vars and
# the fix silently regressed (paper netting artifacts reappeared on 2026-06-15;
# real-money bybit_2 was exposed via the same gate). Per-trade=per-position is
# the canonical model the whole system assumes (pyramiding was never a feature)
# and the reconciler close-confirm is pure safety, so the guard is now
# unconditional — no env flag to drop. This mirrors how NAKED_POSITION_AUTOPROTECT
# and the MONITOR_RECONCILE_ENABLED gate were removed: a required correctness
# capability must not sit behind a default-off flag (Prime Directive).
# ``RECONCILER_CLOSE_CONFIRM_SECONDS`` remains as the close-confirm tuning knob.


def position_netting_guard_active_for(account_id: Optional[str]) -> bool:
    """Return True — the position-netting / per-trade=per-position guard is
    BASELINE (unconditional) as of 2026-06-17.

    Kept as the single predicate both halves of the guard (monocle +
    reconciler) consult, so the call sites are unchanged; it no longer reads
    any env flag. ``account_id`` is retained for signature stability (the guard
    applies to every account — it is a no-op where it can't apply, e.g.
    brokers that attach SL/TP atomically and never net same-direction adds).
    """
    return True


def has_open_trade_for_strategy(
    account_id: str,
    symbol: str,
    strategy_name: Optional[str],
    *,
    db_path: Optional[str] = None,
) -> Optional[bool]:
    """Return True if ``(strategy_name, account_id, symbol)`` already has an
    open live trade in the journal.

    Strategy-scoped sibling of ``_has_open_position`` (which is
    account+symbol scoped). Used by the netting guard's monocle gate to
    block a same-direction re-entry while the strategy still holds an
    open position — independent of the order_packages status the legacy
    strategy-monocle gate keys on (so a prematurely-closed package can't
    free the gate while the position is genuinely still open).

    A read failure returns ``None`` ("could not look", FIX-CA-07) — NOT
    ``False``: the netting guard must not read a locked journal as "no open
    trade" and let a re-entry through.

    A MISSING journal file also returns ``None`` (NO-HALT round 2,
    2026-10-09). It used to return ``False``, so a journal the resolver could
    not find (wrong data dir, unmounted volume) read as "no open trade" and a
    duplicate entry went through. The trader creates the file at boot
    (``enable_wal_mode``), so a missing file mid-run is "we could not look",
    not "nothing is open". Callers refuse THAT entry for THAT tick and re-read
    on the next one — no latch: the coordinator refuses the add with
    ``net_position_unreadable`` (``note_open_trade_read`` raises one red flag
    after ``OPEN_TRADE_READ_RED_FLAG_AFTER`` consecutive refusals); the pairs
    executor reads the pair as ``unreadable`` (entries refused this tick,
    counted as possibly-open for concurrency).
    """
    if not strategy_name:
        return False
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        logger.warning(
            "has_open_trade_for_strategy: journal file missing (%s) for "
            "account=%s symbol=%s strategy=%s (could not look -> None)",
            path, account_id, symbol, strategy_name,
        )
        return None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE account_id = ? AND symbol = ? AND strategy_name = ? "
                "AND status = 'open' AND COALESCE(is_backtest, 0) = 0",
                (account_id, symbol, strategy_name),
            ).fetchone()
        return bool(row and row[0] > 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "has_open_trade_for_strategy: read failed for account=%s "
            "symbol=%s strategy=%s: %s (could not look -> None)",
            account_id, symbol, strategy_name, exc,
        )
        return None


# ── bounded alerting for the netting guard's "could not look" refusals ─────
# NO-HALT (operator directive 2026-10-09: "There is no halting."): an
# unreadable open-trade read refuses THAT add for THAT dispatch and the next
# dispatch re-reads — no latch. Every refusal already logs a WARN and journals
# a ``net_position_unreadable`` rejection row; this adds ONE red flag when the
# same (account, symbol, strategy) is refused this way on
# OPEN_TRADE_READ_RED_FLAG_AFTER consecutive reads, and ONE recovery notice
# when a read succeeds after a red flag. In-process: a restart re-counts.
OPEN_TRADE_READ_RED_FLAG_AFTER = 3
_open_trade_read_streaks: Dict[str, Dict[str, Any]] = {}


def note_open_trade_read(
    account_id: str,
    symbol: str,
    strategy_name: Optional[str],
    *,
    unreadable: bool,
) -> int:
    """Advance (``unreadable``) or clear the consecutive-unreadable streak for
    ``(account_id, symbol, strategy_name)`` and emit the bounded alerts above.
    Returns the streak length after the call (0 when cleared). Never raises.
    """
    key = f"{account_id}|{symbol}|{strategy_name}"
    try:
        if not unreadable:
            st = _open_trade_read_streaks.pop(key, None)
            if st and st.get("flagged"):
                from src.runtime.outcomes import Level, report
                report(
                    "open_trade_read_unreadable", "recovered", level=Level.WARN,
                    reason=(
                        f"netting guard: open-trade read for {account_id}/{symbol}/"
                        f"{strategy_name} readable again after {st.get('n')} "
                        f"consecutive unreadable reads"
                    ),
                    account_id=account_id, symbol=symbol,
                    strategy=strategy_name, streak=st.get("n"),
                )
            return 0
        st = _open_trade_read_streaks.setdefault(key, {"n": 0, "flagged": False})
        st["n"] = int(st.get("n") or 0) + 1
        n = st["n"]
        if n >= OPEN_TRADE_READ_RED_FLAG_AFTER and not st.get("flagged"):
            st["flagged"] = True
            from src.runtime.outcomes import Level, report
            report(
                "open_trade_read_unreadable", "red_flag", level=Level.CRITICAL,
                reason=(
                    f"RED FLAG — netting guard could not read the trade journal for "
                    f"{account_id}/{symbol}/{strategy_name} on {n} consecutive "
                    f"dispatches (missing or unreadable journal). Each add was "
                    f"refused with net_position_unreadable; the account stays live "
                    f"and every dispatch re-reads. The journal needs a look"
                ),
                account_id=account_id, symbol=symbol,
                strategy=strategy_name, streak=n,
            )
        return n
    except Exception as exc:  # noqa: BLE001 — an alert must never break dispatch
        logger.error("note_open_trade_read failed for %s: %s", key, exc)
        return int((_open_trade_read_streaks.get(key) or {}).get("n") or 0)


def current_net_position_qty(
    account_id: str,
    symbol: str,
    *,
    db_path: Optional[str] = None,
) -> Optional[float]:
    """Return the signed net position qty for an ``(account, symbol)``.

    Sum of ``position_size`` across rows with ``status='open'`` AND
    ``is_backtest=0`` (or NULL), signed by direction:

      - ``direction='long'``  → contributes ``+position_size``
      - ``direction='short'`` → contributes ``-position_size``

    Rows with any other direction value are ignored (defensive — the
    schema constrains direction but legacy data may contain stragglers).

    Parameters
    ----------
    account_id : str
        Per-account key in ``config/accounts.yaml`` (e.g. ``"bybit_2"``).
    symbol : str
        Trading symbol (e.g. ``"BTCUSDT"``).
    db_path : str, optional
        Override the default ``trade_journal.db`` path. ``TRADE_JOURNAL_DB``
        env var takes precedence when this is None.

    Returns
    -------
    float or None
        Signed net qty. ``0.0`` when:
          * no open rows match, OR
          * the trade journal file does not exist (fresh deploy).
        ``None`` when the SELECT fails — "could not look" is not "flat"
        (FIX-CA-07). Not registered in scripts/ci/check_collapsed_states.py:
        that guard keys on string state tokens and a None-vs-0.0 return has
        none; tests/test_net_position_unreadable.py is the detector.

    Notes
    -----
    This intentionally reads the trade journal, not the exchange. The
    journal is what the per-account RiskManager / dispatcher already
    treat as the source of truth (cf. ``_has_open_position`` in
    ``src/core/coordinator.py``). Reconciliation with exchange-side
    state lives elsewhere (``src/runtime/order_monitor.py``) and is
    out of scope for this helper.
    """
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return 0.0

    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT direction, position_size "
                "FROM trades "
                "WHERE account_id = ? AND symbol = ? "
                "AND status = 'open' AND COALESCE(is_backtest, 0) = 0",
                (account_id, symbol),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "current_net_position_qty: read failed for account=%s symbol=%s: %s "
            "(could not look -> None, NOT flat)",
            account_id, symbol, exc,
        )
        return None

    net = 0.0
    for direction, qty in rows:
        if qty is None:
            continue
        try:
            q = float(qty)
        except (TypeError, ValueError):
            continue
        d = (direction or "").lower()
        if d == "long":
            net += q
        elif d == "short":
            net -= q
        # Any other direction value (legacy data) is silently skipped —
        # logging here would be noisy on every dispatch tick.
    return net


def net_positions_by_symbol(*, db_path: Optional[str] = None) -> dict[str, float]:
    """Return signed net qty per symbol aggregated across all live accounts.

    Queries all open, non-backtest trades in the trade journal, summing
    signed qty (long +, short −) per symbol irrespective of account.

    Parameters
    ----------
    db_path : str, optional
        Override the default ``trade_journal.db`` path.

    Returns
    -------
    dict[str, float]
        ``{symbol: net_qty}``.  Only symbols with non-zero net are included.
        Returns an empty dict when the journal doesn't exist or the read fails.
    """
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return {}

    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT symbol, direction, position_size "
                "FROM trades "
                "WHERE status = 'open' AND COALESCE(is_backtest, 0) = 0",
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "net_positions_by_symbol: read failed: %s (treating all as flat)", exc
        )
        return {}

    acc: dict[str, float] = {}
    for symbol, direction, qty in rows:
        if symbol is None or qty is None:
            continue
        try:
            q = float(qty)
        except (TypeError, ValueError):
            continue
        d = (direction or "").lower()
        if d == "long":
            acc[symbol] = acc.get(symbol, 0.0) + q
        elif d == "short":
            acc[symbol] = acc.get(symbol, 0.0) - q
    return acc


def get_existing_position_info(
    account_id: str,
    symbol: str,
    *,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return confidence and age for the most-recent open trade on (account, symbol).

    Used by the confidence-weighted flip override to decide whether a newer
    signal is strong enough to supersede the hold policy.

    Returns a dict ``{"confidence": float|None, "age_hours": float|None}``
    for the most recent open trade, or ``None`` on journal miss / read failure
    (fail-permissive — a read error never blocks a signal).
    """
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT t.created_at, op.confidence "
                "FROM trades t "
                "LEFT JOIN order_packages op ON t.order_package_id = op.id "
                "WHERE t.account_id = ? AND t.symbol = ? "
                "  AND t.status = 'open' AND COALESCE(t.is_backtest, 0) = 0 "
                "ORDER BY t.id DESC LIMIT 1",
                (account_id, symbol),
            ).fetchone()
        if not row:
            return None
        created_at_raw, confidence = row
        age_hours: Optional[float] = None
        if created_at_raw:
            try:
                try:
                    ts = float(created_at_raw) / 1000.0
                except (TypeError, ValueError):
                    import datetime as _dt
                    s = str(created_at_raw).replace("Z", "+00:00")
                    ts = _dt.datetime.fromisoformat(s).timestamp()
                age_hours = (_time.time() - ts) / 3600.0
            except Exception:  # noqa: BLE001
                pass
        return {
            "confidence": float(confidence) if confidence is not None else None,
            "age_hours": age_hours,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_existing_position_info: read failed for account=%s symbol=%s: %s",
            account_id, symbol, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Per-strategy SLICES — the successor to Option A on `position_model: slices`
# accounts (NETTED-SLICES, operator 2026-10-15)
# ---------------------------------------------------------------------------
#
# Operator, verbatim: "We can have different strategies working with netted
# positions as long as we know how much of that position belongs to each
# strategy, and each piece of that position has its own brackets. ... We don't
# need to collapse it, we just need to make sure we know how to allocate
# between the trades correctly."
#
# Option A (above) restored per-trade = per-position by forbidding a netted ADD
# for the SAME strategy. That half stays: a strategy never double-enters. What
# blocked a DIFFERENT strategy was never Option A -- it was the coordinator
# computing the intent delta against the ACCOUNT net (a second strategy's
# same-side signal read `noop at_target`). On a slice account the delta basis
# is the strategy's OWN slice (`strategy_signed_qty`) and `admit_slice` decides
# whether the book can carry one more slice and how big it may be.
#
# THE LEDGER IS THE TRADE JOURNAL. A slice is one open, non-backtest `trades`
# row: strategy_name, direction, position_size (the slice qty), entry_price,
# stop_loss, take_profit_1 and the venue leg ids sl_order_id / tp_order_id that
# carry its own qty-scoped Partial bracket (captured at entry since
# 2026-07-21). No new table. INVARIANT: the venue net on (account, symbol)
# equals the signed sum of open slices; `reconcile` reports three states,
# never collapsed -- reconciled / divergent / unreadable.
#
# Every refusal is per trade, journalled with its reason, and re-asked next
# tick. Nothing here closes, halts or latches ("There is no halting", operator
# 2026-10-09). Repairing a divergent book is the existing netting reconciler's
# job (order_monitor: leg-gone attribution, then FIFO residual).
#
# ⚠️ OPPOSITE SLICES on a ONE-WAY book are refused (`opposite_slice_one_way_book`).
# Bybit sizes position TP/SL to the open position ("adjust the qty according to
# the size of the open position", v5 /position/trading-stop), so an opposite
# slice shrinks the net below the same-side slices' legs and a firing leg
# closes LESS than its slice -- the ledger stops describing the venue. The same
# mechanism stranded 8 of 8 SOL/ETH pairs legs (bybit_position_mode.py, T.2).
# On a HEDGE book (Option B of the 2026-06-08 audit, the existing positionIdx
# plumbing) the opposite slice opens in its own book with its own Partial legs.

POSITION_MODEL_KEY = "position_model"
POSITION_MODEL_SLICES = "slices"
#: Package meta key the per-account allocation writes and admit_slice reads.
ALLOCATION_META_KEY = "slice_allocation"


def account_uses_slices(account_cfg: Optional[Dict[str, Any]]) -> bool:
    """True when the account declares ``position_model: slices``."""
    if not isinstance(account_cfg, dict):
        return False
    return str(account_cfg.get(POSITION_MODEL_KEY) or "").strip().lower() == POSITION_MODEL_SLICES


RECONCILED = "reconciled"
DIVERGENT = "divergent"
UNREADABLE = "unreadable"

#: Relative tolerance for ledger-vs-venue equality. Journal rows record the
#: legalised qty actually sent (``_submit_order`` writes it back), so the two
#: should agree exactly; the tolerance absorbs float repr only.
REL_TOL = 0.005
ABS_TOL = 1e-9


def _journal_path() -> str:
    from src.utils.paths import trade_journal_db_path
    return trade_journal_db_path()


def open_slices(
    account_id: str, symbol: str, *, db_path: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Every open slice on (account, symbol), oldest first. ``None`` = could not look."""
    path = db_path or _trade_journal_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            # SELECT * so a journal predating the sl_order_id/tp_order_id
            # migration still reads (those keys are then simply absent).
            rows = conn.execute(
                "SELECT * FROM trades "
                " WHERE account_id = ? AND symbol = ? AND status = 'open' "
                "   AND COALESCE(is_backtest, 0) = 0 "
                " ORDER BY id ASC",
                (account_id, symbol),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("open_slices: read failed for %s/%s: %s", account_id, symbol, exc)
        return None
    keep = ("id", "strategy_name", "direction", "position_size", "entry_price",
            "stop_loss", "take_profit_1", "sl_order_id", "tp_order_id")
    return [{k: dict(r).get(k) for k in keep} for r in rows]


def _signed(row: Mapping[str, Any]) -> float:
    try:
        q = abs(float(row.get("position_size") or 0.0))
    except (TypeError, ValueError):
        return 0.0
    d = str(row.get("direction") or "").lower()
    return q if d == "long" else -q if d == "short" else 0.0


def ledger_net(slices: Iterable[Mapping[str, Any]]) -> float:
    """Signed sum of the slices (long +, short −)."""
    return sum(_signed(r) for r in slices)


def strategy_signed_qty(slices: Iterable[Mapping[str, Any]], strategy: Optional[str]) -> float:
    """This strategy's OWN signed qty on the book — the delta basis on a slice account."""
    return sum(_signed(r) for r in slices if r.get("strategy_name") == strategy)


def venue_net(positions: Optional[Iterable[Mapping[str, Any]]], symbol: str) -> Optional[float]:
    """Signed venue net for *symbol* from an ``account_open_positions`` list.

    ``None`` in → ``None`` out (could not read). Both hedge books, if present,
    are summed — that is the economic net.
    """
    if positions is None:
        return None
    net = 0.0
    want = str(symbol).upper()
    for p in positions:
        if str(p.get("symbol") or "").upper() != want:
            continue
        try:
            size = abs(float(p.get("size") or 0.0))
        except (TypeError, ValueError):
            return None
        side = str(p.get("side") or "").lower()
        if side in ("buy", "long"):
            net += size
        elif side in ("sell", "short"):
            net -= size
        else:
            # A non-zero row whose side we cannot read makes the net unknown.
            return None
    return net


def reconcile(
    slices: Optional[Iterable[Mapping[str, Any]]],
    positions: Optional[Iterable[Mapping[str, Any]]],
    symbol: str,
) -> Dict[str, Any]:
    """``{state, ledger_net, venue_net, gap}`` — see the module docstring."""
    if slices is None:
        return {"state": UNREADABLE, "side": "journal", "ledger_net": None,
                "venue_net": None, "gap": None}
    rows = list(slices)
    led = ledger_net(rows)
    ven = venue_net(positions, symbol)
    if ven is None:
        return {"state": UNREADABLE, "side": "venue", "ledger_net": led,
                "venue_net": None, "gap": None}
    gap = led - ven
    tol = max(ABS_TOL, REL_TOL * max(abs(led), abs(ven)))
    return {
        "state": RECONCILED if abs(gap) <= tol else DIVERGENT,
        "ledger_net": led, "venue_net": ven, "gap": gap, "n_slices": len(rows),
    }


def held_stop_risk(
    slices: Iterable[Mapping[str, Any]], *, exclude_strategies: Iterable[str] = (),
) -> Optional[float]:
    """Σ qty · loss-side distance to the CURRENT stop, over slices not excluded.

    A stop already on the profit side of entry contributes 0 (trailed past
    break-even). ``None`` when any counted slice has no readable entry or stop:
    an unprotected slice's risk is unmeasured, and an unmeasured risk is never
    read as zero.
    """
    skip = {str(s) for s in exclude_strategies}
    total = 0.0
    for r in slices:
        if str(r.get("strategy_name")) in skip:
            continue
        try:
            q = abs(float(r.get("position_size") or 0.0))
            entry = float(r.get("entry_price"))
            sl = float(r.get("stop_loss"))
        except (TypeError, ValueError):
            return None
        if q <= 0:
            continue
        d = str(r.get("direction") or "").lower()
        if d == "long":
            total += q * max(0.0, entry - sl)
        elif d == "short":
            total += q * max(0.0, sl - entry)
        else:
            return None
    return total


def book_side(slices: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """``long`` / ``short`` / ``None`` (flat or empty) from the ledger net."""
    net = ledger_net(slices)
    if abs(net) <= ABS_TOL:
        return None
    return "long" if net > 0 else "short"


def has_opposite_slices(slices: Iterable[Mapping[str, Any]], side: str) -> bool:
    """True when any open slice runs opposite to *side*."""
    other = "short" if side == "long" else "long"
    return any(str(r.get("direction") or "").lower() == other for r in slices)


def _default_positions_reader(account_cfg):
    from src.units.accounts.clients import account_open_positions
    return account_open_positions(dict(account_cfg))


def _default_tpsl_mode() -> str:
    from src.units.accounts.execute import _bybit_tpsl_mode
    return _bybit_tpsl_mode()


def _default_hedge(account_id: str, symbol: str) -> bool:
    from src.runtime.bybit_position_mode import hedge_mode_enabled
    return hedge_mode_enabled(account_id, symbol)


def slice_qty(
    *,
    sized_qty: float,
    entry: float,
    sl: float,
    weight: float,
    budget_trades: float,
    held_risk: float,
) -> Dict[str, Any]:
    """Turn a weight into a quantity, inside the symbol's risk budget.

    * ``unit_risk = sized_qty · |entry − sl|`` — the RiskManager's one-trade
      risk for this account (the per-trade cap, in quote currency).
    * ``budget = budget_trades · unit_risk``.
    * ``remaining = max(0, budget − held_risk)`` where ``held_risk`` is the
      current stop-risk of open slices held by strategies NOT contending this
      tick (contenders hold nothing of their own — the same-strategy guard).
    * ``qty = min(sized_qty, weight · remaining / |entry − sl|)``.

    Never above ``sized_qty``: a slice cannot exceed the per-trade size the
    RiskManager approved.
    """
    dist = abs(float(entry) - float(sl))
    sized = max(0.0, float(sized_qty))
    w = min(1.0, max(0.0, float(weight)))
    if dist <= 0 or sized <= 0:
        return {"qty": 0.0, "unit_risk": 0.0, "budget": 0.0, "remaining": 0.0,
                "reason": "no_risk_distance" if dist <= 0 else "no_size"}
    unit_risk = sized * dist
    budget = max(0.0, float(budget_trades)) * unit_risk
    remaining = max(0.0, budget - max(0.0, float(held_risk)))
    qty = min(sized, w * remaining / dist)
    return {
        "qty": qty,
        "unit_risk": unit_risk,
        "budget": budget,
        "remaining": remaining,
        "reason": "budget_exhausted" if remaining <= 0 else "allocated",
    }




def admit_slice(
    *,
    account_id: str,
    account_cfg: Mapping[str, Any],
    strategy: str,
    symbol: str,
    direction: str,
    entry: Any,
    sl: Any,
    meta: Optional[Mapping[str, Any]],
    sized_qty: float,
    dry: bool,
    slices_reader: Optional[Callable[[str, str], Optional[list]]] = None,
    positions_reader: Optional[Callable[[Mapping[str, Any]], Optional[Iterable]]] = None,
    tpsl_mode_fn: Optional[Callable[[], str]] = None,
    hedge_fn: Optional[Callable[[str, str], bool]] = None,
    budget_trades: Optional[float] = None,
) -> Dict[str, Any]:
    """May this strategy add a slice to (account, symbol) -- and how big?

    Called by ``Coordinator.multi_account_execute`` for an intent-mode package
    on a slice account, AFTER the RiskManager sized the trade. Returns
    ``{ok, reason, qty, own_signed, detail}``. Checks, in order, each with the
    token it journals:

    1. ``slice_ledger_unreadable`` -- the journal could not be read.
    2. The strategy already holds a slice -> ``ok`` with its own signed qty:
       Option A's guard and ``slice_hold_to_bracket`` handle it.
    3. ``opposite_slice_one_way_book`` -- see the block comment above.
    4. ``slice_requires_partial_tpsl`` -- non-flat Bybit book while
       ``BYBIT_TPSL_MODE`` is not ``partial`` (Full mode would REPLACE the
       first slice's bracket).
    5. ``slice_ledger_divergent`` / ``slice_ledger_venue_unreadable`` -- the
       ledger does not reconcile to the venue (skipped on a dry account).
    6. ``slice_risk_unmeasurable`` -- a non-contending slice has no stop.
    7. ``slice_budget_exhausted`` -- see :func:`slice_qty`.
    """
    detail: Dict[str, Any] = {"position_model": "slices"}
    slices = (slices_reader or open_slices)(account_id, symbol)
    if slices is None:
        return {"ok": False, "reason": "slice_ledger_unreadable", "qty": 0.0,
                "own_signed": None, "detail": detail}
    own = strategy_signed_qty(slices, strategy)
    detail.update({"n_slices": len(slices), "ledger_net": ledger_net(slices),
                   "own_signed": own})
    if abs(own) > ABS_TOL:
        detail["admission"] = "own_slice_open"
        return {"ok": True, "reason": None, "qty": float(sized_qty),
                "own_signed": own, "detail": detail}

    exchange = str(account_cfg.get("exchange") or "").lower()
    if slices:
        if has_opposite_slices(slices, direction):
            hedge = bool((hedge_fn or _default_hedge)(account_id, symbol))
            detail["hedge_book"] = hedge
            if not hedge:
                return {"ok": False, "reason": "opposite_slice_one_way_book",
                        "qty": 0.0, "own_signed": own, "detail": detail}
        if exchange == "bybit":
            mode = str((tpsl_mode_fn or _default_tpsl_mode)() or "")
            detail["tpsl_mode"] = mode
            if mode != "partial":
                return {"ok": False, "reason": "slice_requires_partial_tpsl",
                        "qty": 0.0, "own_signed": own, "detail": detail}

    if dry:
        detail["reconcile"] = "skipped_dry"
    else:
        try:
            positions = (positions_reader or _default_positions_reader)(account_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("admit_slice: venue read failed for %s: %s", account_id, exc)
            positions = None
        rec = reconcile(slices, positions, symbol)
        detail["reconcile"] = rec
        if rec["state"] == UNREADABLE:
            return {"ok": False, "reason": "slice_ledger_venue_unreadable", "qty": 0.0,
                    "own_signed": own, "detail": detail}
        if rec["state"] != RECONCILED:
            return {"ok": False, "reason": "slice_ledger_divergent", "qty": 0.0,
                    "own_signed": own, "detail": detail}

    alloc = ((meta or {}).get(ALLOCATION_META_KEY) or {}).get(account_id) or {}
    weight = float(alloc.get("weight", 1.0) if isinstance(alloc, Mapping) else 1.0)
    contenders = list(alloc.get("contenders") or [strategy]) if isinstance(alloc, Mapping) else [strategy]
    if strategy not in contenders:
        contenders.append(strategy)
    held = held_stop_risk(slices, exclude_strategies=contenders)
    detail.update({"weight": weight, "contenders": sorted(contenders), "held_risk": held,
                   "allocation_basis": (alloc.get("basis") if isinstance(alloc, Mapping) else None)})
    if held is None:
        return {"ok": False, "reason": "slice_risk_unmeasurable", "qty": 0.0,
                "own_signed": own, "detail": detail}
    if budget_trades is None:
        from src.runtime.allocator_ev import load_allocation_config
        budget_trades = float(load_allocation_config().get("symbol_budget_trades", 1.0))
    try:
        sizing = slice_qty(sized_qty=sized_qty, entry=float(entry), sl=float(sl),
                               weight=weight, budget_trades=budget_trades, held_risk=held)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "slice_risk_unmeasurable", "qty": 0.0,
                "own_signed": own, "detail": detail}
    detail["sizing"] = {k: (round(v, 10) if isinstance(v, float) else v)
                        for k, v in sizing.items()}
    detail["budget_trades"] = budget_trades
    if sizing["qty"] <= 0:
        return {"ok": False, "reason": "slice_budget_exhausted", "qty": 0.0,
                "own_signed": own, "detail": detail}
    return {"ok": True, "reason": None, "qty": float(sizing["qty"]),
            "own_signed": own, "detail": detail}
