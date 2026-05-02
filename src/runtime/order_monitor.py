"""Order-package monitor loop — S-030 PR3
(architecture-audit-2026-05-02 P1-4).

Per CLAUDE.md § Architecture rules § 2 + § 3:
  - Strategies *monitor* open packages — re-enter with fresh candles
    and decide whether to update sl/tp or close.
  - The account unit re-runs ``risk_manager.approve`` on package
    updates and either modifies the live exchange order, closes it,
    or stays out.

This module is the runtime-level glue. It runs once per pipeline
tick (wired from ``src/main.py``'s loop): for each enabled
strategy, read open packages from the DB unit, fetch fresh
candles via the supplied ``ohlcv_fetcher``, dispatch to the
strategy's ``monitor()`` hook, and apply non-None returns to the
DB unit.

## Scope (PR3)

This PR ships the loop and the DB-side updates. The actual
exchange-side modify/close API call is intentionally **deferred to
a follow-up** (the `apply_to_exchange` flag is a hook the next PR
will activate). Reasons:

1. The DB updates give the operator visibility immediately — the
   ``order_packages`` row carries the lifecycle and the linked
   ``trades`` row is closed-out on a close decision. The hourly
   report sees this within ~1 h.
2. The exchange-side modify/close API needs new helpers in
   ``execute.py`` (``modify_open_order(client, order_id, sl, tp)``,
   ``close_open_position(client, symbol, qty)``) that haven't
   been written yet — and they're Tier 2 changes to live order
   routing that deserve their own PR.
3. With the loop split out today, the operator can verify monitor
   decisions in a "shadow mode" (DB updated, exchange untouched)
   for a tick or two before flipping the live wiring.

## Best-effort

The loop never raises. Each step is wrapped:
  - DB read → empty list on failure
  - Candle fetch → None → strategy.monitor() returns None
  - strategy.monitor() raises → caught, logged, treated as "no change"
  - DB write → logged warning
  - One bad row never breaks the rest of the loop.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _StrategyTickSummary:
    open_count: int = 0
    updated_count: int = 0
    closed_count: int = 0
    no_change_count: int = 0
    error_count: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open": self.open_count,
            "updated": self.updated_count,
            "closed": self.closed_count,
            "no_change": self.no_change_count,
            "errors": self.error_count,
            "error_messages": self.errors[:5],
        }


def _load_strategies(strategies: Optional[List[str]]) -> List[str]:
    """Return the list of strategy names to scan.

    Caller-supplied list wins; otherwise default to the production
    STRATEGIES list from the pipeline. The fallback survives even
    when the pipeline import fails (e.g. test harness) — returns an
    empty list rather than crashing.
    """
    if strategies is not None:
        return list(strategies)
    try:
        from src.runtime.pipeline import STRATEGIES
        return list(STRATEGIES)
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: STRATEGIES unavailable: %s", exc)
        return []


def _resolve_db(db_path: Optional[str]):
    """Build a Database instance for the configured journal path."""
    from src.data_layer.database import Database
    path = db_path or os.environ.get("TRADE_JOURNAL_DB") or str(
        _REPO_ROOT / "trade_journal.db"
    )
    return Database(db_path=path)


def _call_strategy_monitor(strategy_name: str, cfg: dict, candles_df,
                           open_pkg: dict) -> Optional[Dict[str, Any]]:
    """Import the strategy module and call its monitor() hook.

    Returns the strategy's verdict, or None if anything goes wrong
    (logged but never raised). Strategies without a monitor()
    function are treated as "no opinion" — no error.
    """
    try:
        mod = importlib.import_module(f"src.units.strategies.{strategy_name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: strategy module %r unavailable: %s",
            strategy_name, exc,
        )
        return None
    monitor_fn = getattr(mod, "monitor", None)
    if monitor_fn is None:
        return None
    try:
        return monitor_fn(cfg, candles_df, open_pkg)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: %s.monitor() raised on pkg %s: %s",
            strategy_name, open_pkg.get("order_package_id"), exc,
        )
        return None


def _apply_update(db, open_pkg: dict, verdict: Dict[str, Any],
                  summary: _StrategyTickSummary) -> None:
    """Translate a non-None monitor verdict into DB writes.

    Verdict shapes:
      - ``{"sl": float}`` or ``{"tp": float}`` → update_order_package
      - ``{"action": "close", "reason": str}`` → close the package
        AND update the linked trade row.

    Each branch is wrapped; one failing write doesn't break the
    rest of the tick.
    """
    pkg_id = open_pkg.get("order_package_id")
    action = (verdict or {}).get("action")
    if action == "close":
        reason = str((verdict or {}).get("reason") or "monitor_close")
        try:
            db.update_order_package(pkg_id, {
                "status": "closed",
                "close_reason": reason,
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: order_packages close write failed for %s: %s",
                pkg_id, exc,
            )
            summary.error_count += 1
            summary.errors.append(f"{pkg_id}: close-write failed")
            return

        # Update the linked trade row, if there is one. The S-029 PR2
        # writer doesn't yet stamp linked_trade_id — that's a
        # follow-up. For now, fall back to "close every trade with
        # status=open whose strategy + symbol matches the package".
        # The fallback is conservative: if no rows match, nothing
        # happens; if multiple match, the close-side trade-row update
        # uses the most recent.
        try:
            close_iso = datetime.now(timezone.utc).isoformat()
            close_updates = {
                "status": "closed",
                "exit_reason": reason,
                "exit_price": (verdict or {}).get("exit_price"),
            }
            close_updates = {k: v for k, v in close_updates.items() if v is not None}

            matched_trade = None
            linked_trade_id = open_pkg.get("linked_trade_id")
            if linked_trade_id:
                db.update_trade(int(linked_trade_id), close_updates)
                # Re-read the row so the exchange-side close has the
                # account_id + position_size.
                rows = db.get_trades(filters={"id": int(linked_trade_id)})
                matched_trade = rows[0] if rows else None
            else:
                # Fallback close-by-symbol-and-strategy.
                matched_trade = _close_trade_by_match(
                    db,
                    strategy=open_pkg.get("strategy_name"),
                    symbol=open_pkg.get("symbol"),
                    updates=close_updates,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: trades close-side update failed for %s: %s",
                pkg_id, exc,
            )
            matched_trade = None

        # Exchange-side close — env-gated. Operator flips
        # MONITOR_APPLY_TO_EXCHANGE=true on the trader's systemd unit
        # when ready to leave shadow mode.
        if _apply_to_exchange_enabled() and matched_trade:
            ex_result = _send_close_to_exchange(matched_trade)
            logger.info(
                "order_monitor: exchange close for pkg=%s account=%s → %s",
                pkg_id, matched_trade.get("account_id"), ex_result,
            )
        summary.closed_count += 1
        return

    # Modification — sl / tp (other keys are silently ignored).
    updates: Dict[str, Any] = {}
    if "sl" in verdict:
        updates["sl"] = float(verdict["sl"])
    if "tp" in verdict:
        updates["tp"] = float(verdict["tp"])
    if not updates:
        # Unknown verdict shape — log and skip.
        logger.warning(
            "order_monitor: unknown verdict shape %r for pkg %s",
            verdict, pkg_id,
        )
        summary.no_change_count += 1
        return

    try:
        db.update_order_package(pkg_id, updates)
        summary.updated_count += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: order_packages update write failed for %s: %s",
            pkg_id, exc,
        )
        summary.error_count += 1
        summary.errors.append(f"{pkg_id}: update-write failed")
        return

    # Exchange-side modify — env-gated. Same shadow-mode contract as
    # the close path above. Looks up the matched trade row to get
    # account_id + symbol; bypasses the exchange call when no trade
    # row matches (the package may have been dispatched but the
    # account_id linkage hasn't been wired in yet).
    if _apply_to_exchange_enabled():
        try:
            rows = db.get_trades(filters={
                "strategy_name": open_pkg.get("strategy_name"),
                "symbol": open_pkg.get("symbol"),
                "status": "open",
            }, limit=1) or []
            if rows:
                ex_result = _send_modify_to_exchange(
                    rows[0],
                    sl=updates.get("sl"),
                    tp=updates.get("tp"),
                )
                logger.info(
                    "order_monitor: exchange modify for pkg=%s account=%s → %s",
                    pkg_id, rows[0].get("account_id"), ex_result,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: exchange modify lookup failed for %s: %s",
                pkg_id, exc,
            )


def _close_trade_by_match(db, *, strategy: Optional[str], symbol: Optional[str],
                          updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Best-effort: find the most-recent open trade row matching the
    strategy + symbol and apply ``updates``. Used when the order
    package row doesn't yet carry a linked_trade_id (the link is a
    follow-up to S-029 PR2).

    Returns the matched trade row before the update (or ``None`` if
    no match) so the caller can decide whether to also send an
    exchange-side close based on its ``account_id`` + ``position_size``.
    """
    if not strategy or not symbol:
        return None
    rows = db.get_trades(
        filters={"strategy_name": strategy, "symbol": symbol, "status": "open"},
        limit=1,
    )
    if not rows:
        return None
    matched = rows[0]
    trade_id = matched.get("id")
    if trade_id is not None:
        db.update_trade(int(trade_id), updates)
    return matched


# ---------------------------------------------------------------------------
# Exchange-side wiring (S-030 PR4) — env-gated
# ---------------------------------------------------------------------------


def _apply_to_exchange_enabled() -> bool:
    """``MONITOR_APPLY_TO_EXCHANGE`` is the operator-controlled flag
    that flips PR3's "shadow mode" (DB-only) into live mode (also
    talks to the exchange). Defaults to **False** so an unconfigured
    deploy never accidentally modifies live orders. Operator sets the
    env on the trader's systemd unit when ready.
    """
    raw = os.environ.get("MONITOR_APPLY_TO_EXCHANGE", "false")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def _build_account_client(account_id):
    """Resolve an exchange client + cfg for *account_id*.

    Returns ``(client, account_cfg)`` — both may be ``None`` if the
    account isn't found or has missing creds. Best-effort; every step
    wrapped.
    """
    try:
        from src.units.accounts import load_accounts
        from src.units.accounts.clients import (
            bybit_client_for, binance_conn_for,
        )
        for acc in load_accounts():
            if acc.name != account_id:
                continue
            cfg = {
                "account_id": acc.name,
                "exchange": acc.exchange,
                "api_key_env": acc.api_key_env,
            }
            exchange_lc = (acc.exchange or "").lower()
            if exchange_lc == "bybit":
                return bybit_client_for(cfg), cfg
            if exchange_lc == "binance":
                return binance_conn_for(cfg), cfg
            return None, cfg
        return None, None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: client resolution failed for %s: %s",
            account_id, exc,
        )
        return None, None


def _send_close_to_exchange(matched_trade: Dict[str, Any]) -> Dict[str, Any]:
    """Send a reduce-only close order for the matched trade row.

    Returns the helper's result dict. Best-effort — never raises.
    """
    try:
        from src.units.accounts.execute import close_open_position
        client, cfg = _build_account_client(matched_trade.get("account_id"))
        if client is None or cfg is None:
            return {"ok": False, "error": "no_client"}
        return close_open_position(
            client, cfg,
            symbol=matched_trade.get("symbol"),
            side=matched_trade.get("direction"),
            qty=float(matched_trade.get("position_size") or 0.0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: exchange close failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _send_modify_to_exchange(matched_trade: Dict[str, Any], *,
                             sl: Optional[float] = None,
                             tp: Optional[float] = None) -> Dict[str, Any]:
    """Send a SL/TP modify to the exchange for the matched trade row."""
    try:
        from src.units.accounts.execute import modify_open_order
        client, cfg = _build_account_client(matched_trade.get("account_id"))
        if client is None or cfg is None:
            return {"ok": False, "error": "no_client"}
        return modify_open_order(
            client, cfg,
            symbol=matched_trade.get("symbol"),
            sl=sl, tp=tp,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: exchange modify failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_monitor_tick(
    *,
    db_path: Optional[str] = None,
    ohlcv_fetcher: Optional[Callable[[str, Optional[str]], Any]] = None,
    strategies: Optional[List[str]] = None,
    strategy_cfg: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run one monitor tick across every enabled strategy's open
    packages. Returns a per-strategy summary dict.

    Parameters
    ----------
    db_path : str, optional
        Override the trade-journal path. Defaults to
        ``TRADE_JOURNAL_DB`` env var or ``<repo>/trade_journal.db``.
    ohlcv_fetcher : callable, optional
        ``(symbol, timeframe) → DataFrame`` source of fresh candles.
        When None, ``monitor()`` is called with ``candles_df=None`` —
        most strategies' v1 monitor logic returns None on missing
        data, which is the safe default.
    strategies : list[str], optional
        Override the strategy list (tests use this). Defaults to the
        production ``STRATEGIES`` list.
    strategy_cfg : dict, optional
        ``{strategy_name: cfg_dict}`` passed through to each
        ``monitor()`` call. Defaults to an empty cfg per strategy.

    Returns
    -------
    dict
        ``{strategy_name: {open, updated, closed, no_change, errors,
        error_messages}}``. Empty dict on a hard failure (DB
        inaccessible, etc.).
    """
    summaries: Dict[str, Dict[str, Any]] = {}
    try:
        db = _resolve_db(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: DB unavailable: %s", exc)
        return summaries

    cfg_map = strategy_cfg or {}

    for strategy_name in _load_strategies(strategies):
        summary = _StrategyTickSummary()
        try:
            open_rows = db.get_order_packages_by_strategy(
                strategy_name, status="open",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: get_order_packages_by_strategy(%s) failed: %s",
                strategy_name, exc,
            )
            summary.error_count += 1
            summary.errors.append(f"db-read failed: {exc}")
            summaries[strategy_name] = summary.to_dict()
            continue

        summary.open_count = len(open_rows)
        cfg = cfg_map.get(strategy_name, {})
        for row in open_rows:
            # Decode the JSON meta blob into a dict so the strategy's
            # monitor sees a normalised package shape.
            normalised = dict(row)
            meta_raw = normalised.get("meta")
            if isinstance(meta_raw, str) and meta_raw:
                try:
                    normalised["meta"] = json.loads(meta_raw)
                except Exception:  # noqa: BLE001
                    normalised["meta"] = {}

            candles = None
            if ohlcv_fetcher is not None:
                try:
                    candles = ohlcv_fetcher(
                        normalised.get("symbol"),
                        (normalised.get("meta") or {}).get("timeframe"),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "order_monitor: ohlcv_fetcher failed for %s: %s",
                        normalised.get("symbol"), exc,
                    )
                    candles = None

            verdict = _call_strategy_monitor(strategy_name, cfg, candles, normalised)
            if verdict is None:
                summary.no_change_count += 1
                continue

            _apply_update(db, normalised, verdict, summary)

        summaries[strategy_name] = summary.to_dict()
        if summary.updated_count or summary.closed_count:
            logger.info(
                "order_monitor: %s — open=%d updated=%d closed=%d",
                strategy_name, summary.open_count,
                summary.updated_count, summary.closed_count,
            )

    return summaries
