"""Dashboard stats builder — dashboards unit (S-008 PR #123).

Produces the enriched stats dict that the Coordinator's dashboard_stats()
method returns.  Designed to be called without live exchange connections
(exchange-level fields degrade gracefully to None).

Output shape
------------
{
    "strategies": [
        {
            "strategy":      str,
            "service":       str,
            "model":         str | None,
            "signals_today": int,
            "pnl":           float,    # today's closed PnL from trade journal
            "open_pos":      int,      # open trades in journal
            "paused":        bool,     # coordinator pause sentinel
            "status":        str,      # "paused" | "active"
        },
        ...
    ],
    "accounts": [
        {
            "account_id":      str,
            "exchange":        str,
            "paused":          bool,
            "balance_usdt":    float | None,
            "open_positions":  list | None,
            "last_trade":      dict | None,
            "strategies":      list[str],
        },
        ...
    ],
    "alerts": list[dict],   # pending alerts from the global queue
    "generated_at": str,    # ISO-8601 UTC
}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def build_stats(
    accounts: List[Dict[str, Any]],
    paused_account_ids: set,
    paused_strategy_names: set,
    *,
    strategy_rows: Optional[List[Dict[str, Any]]] = None,
    exchange_clients: Optional[Dict[str, Any]] = None,
    alert_snapshot: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the unified dashboard stats dict.

    Parameters
    ----------
    accounts : list[dict]
        Account configs from units.yaml / data_loaders.
    paused_account_ids : set[str]
        Account IDs currently halted.
    paused_strategy_names : set[str]
        Strategy names currently paused (future use; strategies always run
        per spec, but the field is included for completeness).
    strategy_rows : list[dict], optional
        Pre-fetched strategy dashboard rows (from data_loaders).
        When None, fetched lazily.
    exchange_clients : dict[str, client], optional
        Mapping account_id → exchange client.  When absent, exchange-level
        fields (balance, open_positions) are None.
    alert_snapshot : list[dict], optional
        Alerts to include.  When None, reads from the global queue.

    Returns
    -------
    dict
        Unified stats dict (see module docstring for shape).
    """
    from src.bot.data_loaders import (
        strategy_dashboard_data,
        account_open_positions,
        account_balance,
        account_last_trade,
    )
    from src.units.dashboards.alerts import list_alerts

    def _swallow(action: str, reason: str, exc: BaseException) -> None:
        """Report a swallowed dashboard exception. Never raises.

        Per-fingerprint dedup in outcomes.report keeps a flapping
        loader from spamming the operator on every dashboard render.
        """
        try:
            from src.runtime.outcomes import Level, report as _report
            _report(action, reason, level=Level.WARN,
                    reason_text=f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass

    # --- Strategies ----------------------------------------------------------
    if strategy_rows is None:
        try:
            strategy_rows = strategy_dashboard_data()
        except Exception as exc:  # noqa: BLE001
            _swallow("dashboard_stats", "strategy_data_failed", exc)
            strategy_rows = []

    enriched_strategies = []
    for row in (strategy_rows or []):
        name = row.get("strategy", "")
        paused = name in paused_strategy_names
        enriched_strategies.append({
            **row,
            "paused": paused,
            "status": "paused" if paused else row.get("status", "active"),
        })

    # --- Accounts ------------------------------------------------------------
    clients = exchange_clients or {}
    enriched_accounts = []
    for acc in accounts:
        aid = acc.get("account_id") or acc.get("id") or ""
        paused = aid in paused_account_ids
        client = clients.get(aid)

        balance: Optional[float] = None
        open_positions: Optional[List] = None
        last_trade: Optional[Dict] = None

        if client is not None:
            try:
                bal_result = account_balance({**acc, "account_id": aid})
                balance = bal_result.get("total_usdt") if bal_result else None
            except Exception as exc:  # noqa: BLE001
                _swallow("dashboard_stats", "balance_failed", exc)
                balance = None
            try:
                open_positions = account_open_positions({**acc, "account_id": aid})
            except Exception as exc:  # noqa: BLE001
                _swallow("dashboard_stats", "positions_failed", exc)
                open_positions = None

        try:
            last_trade = account_last_trade({**acc, "account_id": aid})
        except Exception as exc:  # noqa: BLE001
            _swallow("dashboard_stats", "last_trade_failed", exc)
            last_trade = None

        enriched_accounts.append({
            "account_id": aid,
            "exchange": acc.get("exchange", "unknown"),
            "paused": paused,
            "balance_usdt": balance,
            "open_positions": open_positions,
            "last_trade": last_trade,
            "strategies": list(acc.get("strategies") or []),
        })

    # --- Alerts --------------------------------------------------------------
    alerts = alert_snapshot if alert_snapshot is not None else list_alerts()

    return {
        "strategies": enriched_strategies,
        "accounts": enriched_accounts,
        "alerts": alerts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
