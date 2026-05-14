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


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "trade_journal.db")


def _trade_journal_path() -> str:
    return os.environ.get("TRADE_JOURNAL_DB") or _DEFAULT_DB_PATH


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
