"""Open-position helpers — read the live net position for an account+symbol.

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

Best-effort: a journal-read failure returns ``0.0`` (treated as flat) and
logs a warning. The dispatcher then falls back to risk-manager sized
qty for the order, which is the same behaviour as today — the delta
path is an optimisation on top of the existing safe default.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def _trade_journal_path() -> str:
    from src.utils.paths import trade_journal_db_path
    return trade_journal_db_path()


# ---------------------------------------------------------------------------
# Position-netting / per-trade SL/TP guard kill-switch (BL-20260608-DEMOPNL)
# ---------------------------------------------------------------------------
#
# Option A from docs/audits/position-netting-sltp-2026-06-08.md. ONE env
# switch gates BOTH halves of the fix so the operator can roll the whole
# thing back with a single env flip + restart (no redeploy), mirroring the
# FLIP_POLICY / REGIME_ROUTER_ENABLED pattern:
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
# **Default OFF** — ships inert. The change is Tier-3 (order path +
# reconciler + monocle); the operator flips ``POSITION_NETTING_GUARD_ENABLED``
# on only after the walk-forward + demo-replay validation is approved.
# Read at call time so the flip takes effect on the next tick.

_GUARD_TRUTHY = frozenset({"1", "true", "yes", "on"})


def position_netting_guard_enabled() -> bool:
    """Return True when the position-netting / per-trade SL/TP guard is on.

    Gated by ``POSITION_NETTING_GUARD_ENABLED`` (default OFF). See the
    module-level note above and the env-var table in CLAUDE.md.
    """
    raw = os.environ.get("POSITION_NETTING_GUARD_ENABLED", "")  # allow-silent: position-netting guard kill-switch (no-pyramiding + close-confirm), NOT a live/dry gate — mirrors REGIME_ROUTER_ENABLED
    return str(raw).strip().lower() in _GUARD_TRUTHY


def position_netting_guard_accounts() -> Optional[frozenset]:
    """Optional account allowlist that NARROWS the guard's scope.

    ``POSITION_NETTING_GUARD_ACCOUNTS`` is a comma-separated list of
    account ids (e.g. ``bybit_1`` or ``bybit_1,bybit_2``). Returns the
    parsed set, or ``None`` when the var is unset/blank.

    **This is a scope refinement, not a second enable gate.** The master
    switch is ``POSITION_NETTING_GUARD_ENABLED`` (default OFF — the one
    gate). When the master is ON, this allowlist defaults **permissive**
    (unset → applies to ALL accounts), so it never strands capability —
    consistent with the Prime Directive's "no hidden default-off second
    gate". Its purpose is the operator-watched **demo-only soak**: set the
    master ON + ``POSITION_NETTING_GUARD_ACCOUNTS=bybit_1`` to activate the
    guard on the demo account alone before widening to live.
    """
    raw = os.environ.get("POSITION_NETTING_GUARD_ACCOUNTS", "")  # allow-silent: guard SCOPE refinement (defaults permissive = all accounts when the master switch is on), not a live/dry gate
    parts = {p.strip() for p in str(raw).split(",") if p.strip()}
    return frozenset(parts) or None


def position_netting_guard_active_for(account_id: Optional[str]) -> bool:
    """Return True when the netting guard should act on *account_id*.

    Master switch ON **and** (no allowlist set → all accounts, or
    *account_id* is in the allowlist). This is the single predicate both
    halves of the guard (monocle + reconciler) consult, so the
    demo-only-soak scoping is enforced identically on each.
    """
    if not position_netting_guard_enabled():
        return False
    allow = position_netting_guard_accounts()
    return allow is None or (account_id is not None and account_id in allow)


def has_open_trade_for_strategy(
    account_id: str,
    symbol: str,
    strategy_name: Optional[str],
    *,
    db_path: Optional[str] = None,
) -> bool:
    """Return True if ``(strategy_name, account_id, symbol)`` already has an
    open live trade in the journal.

    Strategy-scoped sibling of ``_has_open_position`` (which is
    account+symbol scoped). Used by the netting guard's monocle gate to
    block a same-direction re-entry while the strategy still holds an
    open position — independent of the order_packages status the legacy
    strategy-monocle gate keys on (so a prematurely-closed package can't
    free the gate while the position is genuinely still open).

    Best-effort: a missing journal or read failure returns ``False``
    (i.e. "no open trade known" → don't block) — fail-permissive, matching
    every other guard helper so a transient SQLite hiccup never strands a
    live signal.
    """
    if not strategy_name:
        return False
    path = db_path or _trade_journal_path()
    if not os.path.exists(path):
        return False
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
            "symbol=%s strategy=%s: %s (treating as no-open-trade)",
            account_id, symbol, strategy_name, exc,
        )
        return False


def current_net_position_qty(
    account_id: str,
    symbol: str,
    *,
    db_path: Optional[str] = None,
) -> float:
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
    float
        Signed net qty. ``0.0`` when:
          * no open rows match, OR
          * the trade journal file does not exist (fresh deploy), OR
          * the SELECT fails.

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
            "(treating as flat)",
            account_id, symbol, exc,
        )
        return 0.0

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
