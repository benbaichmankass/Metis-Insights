"""Account execution — units layer (S-008 PR #122).

``execute_pkg`` is the single entry-point the Coordinator calls to run
an OrderPackage through a specific account.  The flow is:

  1. Check pause sentinel (set by ReturnCommands/halt).
  2. Fetch account balance via exchange_client (or use override).
  3. Size the order with the per-account risk manager.
  4. Submit via exchange_client (or simulate when DRY_RUN=true or client is None).
  5. Return a trade_id string.

The exchange_client is injected by the Coordinator so tests can pass a
mock without any live connection.  When client is None and DRY_RUN is
not explicitly set, the function operates in dry-run mode and logs the
would-be order without placing it.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from src.core.coordinator import OrderPackage, is_paused
from src.units.accounts.risk import size_order_from_cfg

logger = logging.getLogger(__name__)

_DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in {"true", "1", "yes"}


def _is_test_order(pkg: OrderPackage) -> bool:
    return bool(getattr(pkg, "meta", None) and pkg.meta.get("is_test"))


def execute_pkg(
    pkg: OrderPackage,
    account_cfg: dict,
    exchange_client: Optional[Any] = None,
    balance_usdt: Optional[float] = None,
    *,
    dry_run: Optional[bool] = None,
    qty_override: Optional[float] = None,
) -> str:
    """Risk-size and execute *pkg* on the account described by *account_cfg*.

    Parameters
    ----------
    pkg : OrderPackage
        The typed order package from the Coordinator.
    account_cfg : dict
        Account config dict (from units.yaml ``accounts`` section).
        Must contain ``account_id``, ``risk_pct``, and ``exchange``.
    exchange_client : object, optional
        Bybit/Binance client with ``get_wallet_balance()`` and
        ``place_order()`` methods.  When None the call runs in dry-run mode.
    balance_usdt : float, optional
        Balance override — skips the live balance fetch.  Used in tests and
        coordinator-level balance caching.
    dry_run : bool, optional
        Explicit dry-run override.  Defaults to the ``DRY_RUN`` env var.
    qty_override : float, optional
        Pre-computed quantity from a stateful per-account RiskManager.
        Skips the ephemeral re-sizing inside this function so the qty
        actually placed matches what the live RiskManager already
        approved (preserves daily-loss-budget state). Used by
        ``Coordinator.multi_account_execute``.

    Returns
    -------
    str
        trade_id — either the exchange's orderId or a generated UUID in dry-run.

    Raises
    ------
    RuntimeError
        When the account is paused (halt command was issued).
    ValueError
        When required account_cfg fields are missing or pkg is invalid.
    """
    account_id = account_cfg.get("account_id") or account_cfg.get("id") or "unknown"

    # 1. Pause check
    if is_paused(account_id):
        raise RuntimeError(
            f"Account '{account_id}' is paused (halt command active). "
            "Resume via coordinator.return_command('resume') before trading."
        )

    # 2. Determine dry-run mode
    is_dry = dry_run if dry_run is not None else _DRY_RUN
    if exchange_client is None:
        is_dry = True

    # 3. Fetch balance
    if balance_usdt is None:
        if exchange_client is not None and not is_dry:
            balance_usdt = _fetch_balance(exchange_client, account_cfg)
        else:
            balance_usdt = float(account_cfg.get("balance_usdt") or 10_000.0)
            logger.debug(
                "execute_pkg: no client — using cfg balance %.2f USDT", balance_usdt
            )

    # 4. Risk-size — honour an explicit override from a stateful caller
    # (Coordinator.multi_account_execute) so the qty that lands at the
    # exchange matches what the live RiskManager already approved.
    if qty_override is not None:
        qty = float(qty_override)
    else:
        qty = size_order_from_cfg(pkg, account_cfg, balance_usdt)

    side = "Buy" if pkg.direction == "long" else "Sell"
    order = {
        "symbol": pkg.symbol,
        "side": side,
        "direction": pkg.direction,
        "entry": pkg.entry,
        "sl": pkg.sl,
        "tp": pkg.tp,
        "qty": qty,
        "strategy": pkg.strategy,
        "account_id": account_id,
    }

    logger.info(
        "execute_pkg: account=%s strategy=%s symbol=%s direction=%s entry=%.4f "
        "sl=%.4f tp=%.4f qty=%.4f dry_run=%s",
        account_id, pkg.strategy, pkg.symbol, pkg.direction,
        pkg.entry, pkg.sl, pkg.tp, qty, is_dry,
    )

    # 5. Submit or simulate
    if is_dry:
        trade_id = f"dry-{uuid.uuid4().hex[:12]}"
        logger.info("DRY RUN — order not placed: %s → trade_id=%s", order, trade_id)
        return trade_id

    # Smoke-test path: keep exchange rejection in-band so the caller can
    # log it as the success signal instead of unwinding the stack.
    # Bybit returns retCode != 0 (no exception) for below-min-lot qty —
    # see _submit_test_order for the explicit retCode check.
    if _is_test_order(pkg):
        return _submit_test_order(exchange_client, order, account_cfg)

    trade_id = _submit_order(exchange_client, order, account_cfg)

    # CLAUDE.md § Architecture rules § 3 + architecture-audit-2026-05-02
    # P0-2: every executed trade must land a row in the trade log so
    # ``/last5`` / ``/strategies`` / hourly-report aggregations have
    # something to read. Pre-fix only smoke tests wrote to the journal
    # (via ``Coordinator._log_smoke_to_journal``); live trades silently
    # bypassed it. Best-effort — a journal failure must never crash the
    # order path. Status starts ``open``; the close path (S-030 monitor
    # loop) updates it via ``Database.update_trade``.
    _log_trade_to_journal(
        pkg, account_cfg, order, trade_id=trade_id, is_dry=is_dry,
    )
    return trade_id


def _submit_test_order(client: Any, order: dict, account_cfg: dict) -> str:
    """Submit a smoke-test order and surface rejection in-band.

    Returns
    -------
    str
        ``"<exchange-orderId>"`` when Bybit unexpectedly accepts (the
        operator should manually flatten if this happens; the qty is
        meant to be below min-lot), or
        ``"rejected_too_small:<reason>"`` when the exchange rejects.
        The latter is the success path.
    """
    exchange = (account_cfg.get("exchange") or "bybit").lower()
    try:
        if exchange == "bybit":
            resp = client.place_order(
                category="linear",
                symbol=order["symbol"],
                side=order["side"],
                orderType="Market",
                qty=str(order["qty"]),
                stopLoss=str(order["sl"]),
                takeProfit=str(order["tp"]),
            ) or {}
            ret_code = resp.get("retCode")
            if ret_code not in (0, "0", None):
                reason = str(resp.get("retMsg") or f"retCode={ret_code}")
                logger.info(
                    "smoke_test rejected by Bybit (success signal): "
                    "account=%s retCode=%s retMsg=%s",
                    order.get("account_id"), ret_code, reason,
                )
                return f"rejected_too_small:retCode={ret_code} {reason}"
            order_id = (resp.get("result") or {}).get("orderId")
            if order_id:
                logger.warning(
                    "smoke_test ACCEPTED by Bybit unexpectedly (qty=%s should "
                    "be below min-lot): orderId=%s — operator should flatten.",
                    order.get("qty"), order_id,
                )
                return str(order_id)
            return f"rejected_too_small:no orderId in response"
        if exchange == "breakout":
            return f"rejected_too_small:breakout exchange does not support live smoke yet"
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        logger.info(
            "smoke_test rejected by exchange (success signal): "
            "account=%s reason=%s", order.get("account_id"), reason,
        )
        return f"rejected_too_small:{reason}"
    return f"rejected_too_small:unsupported exchange {exchange}"


# ---------------------------------------------------------------------------
# Exchange helpers (kept thin — heavy logic stays in exchange connectors)
# ---------------------------------------------------------------------------


def _fetch_balance(client: Any, account_cfg: dict) -> float:
    """Fetch USDT balance from the exchange client."""
    exchange = (account_cfg.get("exchange") or "bybit").lower()
    try:
        if exchange == "bybit":
            resp = client.get_wallet_balance(accountType="UNIFIED")
            lst = (resp.get("result") or {}).get("list") or []
            coins = lst[0].get("coin", []) if lst else []
            return sum(float(c.get("usdValue") or 0) for c in coins)
        if exchange == "binance":
            bal = client.get_balance() or {}
            usdt = (bal.get("USDT") or {}) if isinstance(bal, dict) else {}
            return float((usdt or {}).get("total") or 0)
    except Exception as exc:
        logger.warning("_fetch_balance(%s): %s — defaulting to 0", exchange, exc)
    return 0.0


def _submit_order(client: Any, order: dict, account_cfg: dict) -> str:
    """Place the order via the exchange client and return a trade_id."""
    exchange = (account_cfg.get("exchange") or "bybit").lower()

    # Velotrade integration: prop-firm stub. Real DXtrade SDK wiring is
    # deferred to a follow-up sprint. Refusing live placement here
    # preserves the live-by-default invariant for Bybit accounts while
    # making any mis-routed Velotrade signal structurally inert. The
    # dispatcher catches the RuntimeError and surfaces it on the result
    # row's ``error`` field plus the diagnostic ping.
    if exchange in ("velotrade", "breakout"):
        raise RuntimeError(
            f"{exchange} live placement not yet implemented; "
            f"prop accounts must run dry-run until SDK wiring lands."
        )

    try:
        if exchange == "bybit":
            resp = client.place_order(
                category="linear",
                symbol=order["symbol"],
                side=order["side"],
                orderType="Market",
                qty=str(order["qty"]),
                stopLoss=str(order["sl"]),
                takeProfit=str(order["tp"]),
            )
            return str((resp.get("result") or {}).get("orderId") or uuid.uuid4().hex)
        if exchange == "binance":
            resp = client.place_order(
                symbol=order["symbol"],
                side=order["side"].upper(),
                order_type="MARKET",
                quantity=order["qty"],
            )
            return str(resp.get("orderId") or uuid.uuid4().hex)
    except Exception as exc:
        logger.error("_submit_order(%s): %s", exchange, exc)
        try:
            from src.runtime.api_reporting import report_api_failure
            report_api_failure(
                exchange=exchange,
                op="place_order",
                account_id=str(account_cfg.get("account_id") or "unknown"),
                error=f"{type(exc).__name__}: {exc}",
                exception=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Order submission failed for {order['symbol']}: {exc}") from exc
    raise ValueError(f"Unsupported exchange: {exchange}")


# ---------------------------------------------------------------------------
# Trade-journal writer (architecture-audit-2026-05-02 P0-2)
# ---------------------------------------------------------------------------


def _log_trade_to_journal(
    pkg: OrderPackage,
    account_cfg: dict,
    order: dict,
    *,
    trade_id: str,
    is_dry: bool,
) -> bool:
    """Insert a row into ``trade_journal.db::trades`` for a freshly-placed
    order. Best-effort — a journal failure must never crash the order
    path. Returns True on a successful insert, False on any error
    (logged but never re-raised).

    The row uses ``status='open'``; the close path (S-030 monitor loop)
    will update via ``Database.update_trade``. ``is_backtest=0`` for
    runtime trades; the backtester writes its own rows with
    ``is_backtest=1``.

    The ``TRADE_JOURNAL_DB`` env var overrides the DB path; tests can
    set it to a tmp path to avoid polluting the production journal.
    Tests that don't care about the journal patch this helper directly.
    """
    try:
        import json
        from datetime import datetime, timezone
        from src.units.db.database import Database

        path = (
            os.environ.get("TRADE_JOURNAL_DB")
            or os.path.join(
                os.path.abspath(os.path.join(os.path.dirname(__file__),
                                             "..", "..", "..")),
                "trade_journal.db",
            )
        )
        db = Database(db_path=path)
        notes_payload = {
            "trade_id": trade_id,
            "is_dry": bool(is_dry),
            "confidence": float(getattr(pkg, "confidence", 0.0) or 0.0),
            "signal_logic": (pkg.meta or {}).get("signal_logic") or "",
        }
        db.insert_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry_price": float(pkg.entry),
            "stop_loss": float(pkg.sl),
            "take_profit_1": float(pkg.tp),
            "position_size": float(order.get("qty") or 0.0),
            "setup_type": pkg.strategy,
            "entry_reason": (pkg.meta or {}).get("entry_reason")
                or f"{pkg.strategy} signal",
            "status": "open",
            "is_backtest": 0,
            "strategy_name": pkg.strategy,
            "account_id": str(
                account_cfg.get("account_id") or account_cfg.get("id") or "unknown"
            ),
            "notes": json.dumps(notes_payload, ensure_ascii=False)[:500],
        })
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execute_pkg: trade-journal write failed (account=%s strategy=%s "
            "symbol=%s trade_id=%s): %s",
            account_cfg.get("account_id"), pkg.strategy, pkg.symbol,
            trade_id, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Exchange-side modify/close helpers — S-030 PR4
# (architecture-audit-2026-05-02 P1-4, follow-up to PR3 monitor loop)
# ---------------------------------------------------------------------------


def modify_open_order(
    exchange_client: Any,
    account_cfg: dict,
    *,
    symbol: str,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
) -> dict:
    """Modify SL/TP on an open position on the account's exchange.

    Bybit Unified Trading: ``set_trading_stop(category='linear',
    symbol=…, stopLoss=…, takeProfit=…)``. Binance is not yet
    supported (only the live trader's Bybit accounts are wired);
    returns ``ok=False`` with a NotImplementedError reason.

    Best-effort. Returns a result dict instead of raising so the
    caller (the monitor loop) can record the outcome on the
    order_packages row without unwinding the loop.

    Returns
    -------
    dict
        ``{"ok": bool, "exchange_response": <raw>, "error": <str|None>}``.
    """
    if exchange_client is None:
        return {"ok": False, "exchange_response": None,
                "error": "no exchange_client (missing creds?)"}
    if sl is None and tp is None:
        return {"ok": False, "exchange_response": None,
                "error": "no sl or tp provided — nothing to modify"}

    exchange = (account_cfg.get("exchange") or "bybit").lower()
    if exchange == "bybit":
        try:
            kwargs = {"category": "linear", "symbol": symbol}
            if sl is not None:
                kwargs["stopLoss"] = str(sl)
            if tp is not None:
                kwargs["takeProfit"] = str(tp)
            resp = exchange_client.set_trading_stop(**kwargs)
            ret_code = (resp or {}).get("retCode")
            if ret_code in (0, "0", None):
                logger.info(
                    "modify_open_order: account=%s symbol=%s sl=%s tp=%s OK",
                    account_cfg.get("account_id"), symbol, sl, tp,
                )
                return {"ok": True, "exchange_response": resp, "error": None}
            err = str((resp or {}).get("retMsg") or f"retCode={ret_code}")
            return {"ok": False, "exchange_response": resp, "error": err}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "modify_open_order: bybit raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": False, "exchange_response": None,
            "error": f"unsupported exchange {exchange!r} (bybit only in v1)"}


def close_open_position(
    exchange_client: Any,
    account_cfg: dict,
    *,
    symbol: str,
    side: str,
    qty: float,
) -> dict:
    """Place a reduce-only market order to flatten an open position.

    *side* is the side of the original entry (``"long"`` or ``"short"``);
    the close order is the opposite side. *qty* is the position size
    to close (typically the size of the original entry).

    Bybit-only for v1. Returns a result dict.
    """
    if exchange_client is None:
        return {"ok": False, "exchange_response": None, "exchange_order_id": None,
                "error": "no exchange_client (missing creds?)"}
    if qty <= 0:
        return {"ok": False, "exchange_response": None, "exchange_order_id": None,
                "error": f"invalid qty {qty}"}

    exchange = (account_cfg.get("exchange") or "bybit").lower()
    direction = (side or "").lower()
    close_side = "Sell" if direction == "long" else "Buy"

    if exchange == "bybit":
        try:
            resp = exchange_client.place_order(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
            ) or {}
            ret_code = resp.get("retCode")
            if ret_code in (0, "0", None):
                order_id = (resp.get("result") or {}).get("orderId")
                logger.info(
                    "close_open_position: account=%s symbol=%s side=%s qty=%s "
                    "→ orderId=%s",
                    account_cfg.get("account_id"), symbol, close_side, qty,
                    order_id,
                )
                return {"ok": True, "exchange_response": resp,
                        "exchange_order_id": order_id, "error": None}
            err = str(resp.get("retMsg") or f"retCode={ret_code}")
            return {"ok": False, "exchange_response": resp,
                    "exchange_order_id": None, "error": err}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "close_open_position: bybit raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "exchange_order_id": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": False, "exchange_response": None, "exchange_order_id": None,
            "error": f"unsupported exchange {exchange!r} (bybit only in v1)"}
