"""Account execution — units layer (S-008 PR #122).

``execute_pkg`` is the single entry-point the Coordinator calls to run
an OrderPackage through a specific account.  The flow is:

  1. Check pause sentinel (set by ReturnCommands/halt).
  2. Fetch account balance via exchange_client (or use override).
  3. Size the order with the per-account risk manager.
  4. Submit via exchange_client, or dry-run when account mode is dry_run
     or client is None.
  5. Return a trade_id string.

The exchange_client is injected by the Coordinator so tests can pass a
mock without any live connection.  When client is None the function
operates in dry-run mode and logs the would-be order without placing it.
The dry/live toggle is the per-account ``mode: live | dry_run`` field in
``config/accounts.yaml`` — the legacy ``DRY_RUN`` env var is not read
(removed per operator directive 2026-05-03; see BUG-053).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from src.core.coordinator import OrderPackage, is_paused
from src.units.accounts.precision import (
    get_tick_size,
    invalidate_tick_cache,
    live_instrument_diagnostic,
    quantize_price,
)
from src.units.accounts.risk import size_order_from_cfg

logger = logging.getLogger(__name__)


def _is_test_order(pkg: OrderPackage) -> bool:
    return bool(getattr(pkg, "meta", None) and pkg.meta.get("is_test"))


# Bybit V5 categories. ``spot`` for spot trading (BTCUSDT cash market),
# ``linear`` for USDT-margined perpetuals, ``inverse`` for coin-margined
# perpetuals. The codebase only routes spot + linear today; inverse is
# rejected by ``_bybit_category`` until an account explicitly opts in.
_BYBIT_CATEGORY_DEFAULT = "spot"
_BYBIT_VALID_CATEGORIES = {"spot", "linear", "inverse"}

# Common quote currencies used to parse base coin from spot symbols.
_SPOT_QUOTE_CURRENCIES = ("USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD")


def _spot_base_coin(symbol: str) -> str:
    """Extract base coin ticker from a spot symbol (e.g. ``BTCUSDT`` → ``BTC``)."""
    sym = symbol.upper()
    for quote in _SPOT_QUOTE_CURRENCIES:
        if sym.endswith(quote):
            return sym[: -len(quote)]
    return sym[:-4] if len(sym) > 4 else sym





def _bybit_category(account_cfg: dict) -> str:
    """Resolve the Bybit V5 ``category`` for this account.

    Reads ``account_cfg["market_type"]`` (set from
    ``config/accounts.yaml`` per the operator directive 2026-05-06 — the
    fix for the perp-instead-of-spot bug). Default = ``"spot"`` so any
    account that omits the field trades the cash market, matching what
    the operator's wallet holds (BTC + USDT for ``bybit_1``, USDT for
    ``bybit_2``).

    PR 5 (2026-05-10): the historical ``market_type: spot-margin``
    routing label is treated as plain ``spot`` here for safety so a
    stale config still resolves to a valid category. No production
    account uses it post-PR-3.
    """
    raw = str(account_cfg.get("market_type") or _BYBIT_CATEGORY_DEFAULT).strip().lower()
    if raw == "perp" or raw == "perpetual" or raw == "futures":
        raw = "linear"
    if raw == "spot-margin":
        return "spot"
    if raw not in _BYBIT_VALID_CATEGORIES:
        logger.warning(
            "_bybit_category: account=%s has unknown market_type=%r — "
            "defaulting to %s",
            account_cfg.get("account_id"), raw, _BYBIT_CATEGORY_DEFAULT,
        )
        return _BYBIT_CATEGORY_DEFAULT
    return raw


def execute_pkg(
    pkg: OrderPackage,
    account_cfg: dict,
    exchange_client: Optional[Any] = None,
    balance_usdt: Optional[float] = None,
    *,
    dry_run: Optional[bool] = None,
    qty_override: Optional[float] = None,
    reduce_only: bool = False,
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
        Explicit dry-run override (callers like ``Coordinator.multi_account_execute``
        pass the resolved per-account state). When None, the function
        reads ``account_cfg.get("mode")`` directly so a stale process
        cannot accidentally route a real order to the exchange.
        ``DRY_RUN`` env var is no longer consulted (operator directive
        2026-05-03 — per-account RiskManager is the only toggle).
    qty_override : float, optional
        Pre-computed quantity from a stateful per-account RiskManager.
        Skips the ephemeral re-sizing inside this function so the qty
        actually placed matches what the live RiskManager already
        approved (preserves daily-loss-budget state). Used by
        ``Coordinator.multi_account_execute``.
    reduce_only : bool, default False
        When True the order is sent with ``reduceOnly=True`` so Bybit
        treats it as a position-reducing fill (intent-mode delta-aware
        dispatch). The order's SL/TP are NOT forwarded to the exchange
        — reduce-only fills inherit the parent position's risk levels,
        and re-sending TP/SL on a partial close would corrupt the
        existing trading-stop on the position. The trade-journal row is
        stamped with ``setup_type=intent_reduce`` and a notes entry so
        ``/closed`` / hourly-report aggregations can distinguish reduce
        legs from opens. Bybit linear/inverse only — spot accounts do
        not support reduceOnly and the path raises ``ValueError``.

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

    # Reduce-only orders are derivatives-only on Bybit (linear/inverse).
    # Spot reduceOnly is not supported on V5 — fail fast rather than
    # silently dropping the flag and sending a regular order that would
    # OPEN a new position instead of reducing the existing one.
    if reduce_only:
        market_type = str(account_cfg.get("market_type") or "spot").strip().lower()
        if market_type not in {"linear", "inverse"}:
            raise ValueError(
                f"execute_pkg: reduce_only=True requires a derivatives "
                f"account (market_type in linear/inverse); got "
                f"market_type={market_type!r} for account={account_id!r}"
            )

    # 1. Pause check
    if is_paused(account_id):
        raise RuntimeError(
            f"Account '{account_id}' is paused (halt command active). "
            "Resume via coordinator.return_command('resume') before trading."
        )

    # 2. Determine dry-run mode (operator directive 2026-05-03 — the
    # per-account RiskManager is the only authoritative dry/live gate;
    # there is no process-level interlock). When the caller doesn't pass
    # an explicit override we read ``mode`` straight off the account_cfg
    # (accepting "live"/"dry"/"dry_run"/"paper"). Default = live.
    if dry_run is not None:
        is_dry = bool(dry_run)
    else:
        _mode_raw = str(account_cfg.get("mode") or "live").strip().lower()
        is_dry = _mode_raw in {"dry", "dry_run", "dry-run", "paper"}
    if exchange_client is None:
        is_dry = True

    trace_id = getattr(pkg, "trace_id", None) or (pkg.meta or {}).get("trace_id", "?")
    logger.info(
        "[execute_pkg] trace_id=%s account=%s strategy=%s symbol=%s direction=%s dry=%s",
        trace_id, account_id,
        getattr(pkg, "strategy", "?"), getattr(pkg, "symbol", "?"),
        getattr(pkg, "direction", "?"), is_dry,
    )

    # 3. Fetch balance — direction-aware for spot accounts so the sizer
    # never produces a qty exceeding what the account actually holds.
    # Spot sell (short): balance = USD value of held base coin.
    # Spot buy  (long):  balance = available USDT quote balance.
    # Derivatives / no direction context: total portfolio USD value.
    if balance_usdt is None:
        if exchange_client is not None and not is_dry:
            balance_usdt = _fetch_balance(
                exchange_client, account_cfg,
                direction=pkg.direction,
                symbol=pkg.symbol,
            )
        else:
            balance_usdt = float(account_cfg.get("balance_usdt") or 10_000.0)
            logger.debug(
                "execute_pkg: no client — using cfg balance %.2f USDT", balance_usdt
            )

    # 4. Risk-size — honour an explicit override from a stateful caller
    # (Coordinator.multi_account_execute) so the qty that lands at the
    # exchange matches what the live RiskManager already approved
    # (preserves daily-loss-budget state). Used by
    # ``Coordinator.multi_account_execute``.
    if qty_override is not None:
        qty = float(qty_override)
    else:
        qty = size_order_from_cfg(pkg, account_cfg, balance_usdt)

    # 5. Refuse zero-qty orders before hitting the exchange.
    if qty <= 0 and not is_dry and not _is_test_order(pkg):
        raise RuntimeError(
            f"Order refused for {pkg.symbol}: qty=0 after sizing "
            f"(balance={balance_usdt:.2f} USDT, direction={pkg.direction}). "
            f"Account may be under-funded or hold no base coin to sell."
        )

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
        # Reduce-only flag plumbed through to _submit_order. The Bybit
        # branch drops SL/TP and sets ``reduceOnly=True`` on the kwargs
        # — see _submit_order for the dispatch.
        "reduce_only": bool(reduce_only),
    }

    logger.info(
        "execute_pkg: account=%s strategy=%s symbol=%s direction=%s entry=%.4f "
        "sl=%.4f tp=%.4f qty=%.4f dry_run=%s reduce_only=%s",
        account_id, pkg.strategy, pkg.symbol, pkg.direction,
        pkg.entry, pkg.sl, pkg.tp, qty, is_dry, reduce_only,
    )

    # 6. Submit or simulate
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

    # Guard: refuse a live open order without valid SL/TP.
    # Reduce-only legs close an existing position — they intentionally
    # carry no new SL/TP. All other live orders must have both set and
    # positive before they reach the exchange.
    if not reduce_only:
        _sl = order.get("sl")
        _tp = order.get("tp")
        if not (
            isinstance(_sl, (int, float)) and _sl > 0
            and isinstance(_tp, (int, float)) and _tp > 0
        ):
            raise ValueError(
                f"execute_pkg: refusing live order without valid SL/TP "
                f"(account={account_id!r} strategy={pkg.strategy!r} "
                f"symbol={pkg.symbol!r} sl={_sl!r} tp={_tp!r}). "
                "Strategy must populate stop_loss + take_profit before execution."
            )

    trade_id = _submit_order(exchange_client, order, account_cfg)

    # CLAUDE.md § Architecture rules § 3 + architecture-audit-2026-05-02
    # P0-2: every executed trade must land a row in the trade log so
    # ``/last5`` / ``/strategies`` / hourly-report aggregations have
    # something to read. Pre-fix only smoke tests wrote to the journal
    # (via ``Coordinator._log_smoke_to_journal``); live trades silently
    # bypassed it. Best-effort — a journal failure must never crash the
    # order path. Status starts ``open``; the close path (S-030 monitor
    # loop) updates it via ``Database.update_trade``.
    #
    # Reduce-only orders share the same write path but the row is
    # stamped with the ``intent_reduce`` setup-type marker so
    # downstream aggregations can distinguish a reduce leg from an
    # open. The monitor loop already correlates fills by symbol +
    # qty + side, so no extra plumbing is needed there.
    _log_trade_to_journal(
        pkg, account_cfg, order, trade_id=trade_id, is_dry=is_dry,
        intent_reduce=reduce_only,
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
            category = _bybit_category(account_cfg)
            tick = get_tick_size(client, order["symbol"], category)
            kwargs = {
                "category": category,
                "symbol": order["symbol"],
                "side": order["side"],
                "orderType": "Market",
                "qty": str(order["qty"]),
            }
            # Derivatives (linear/inverse) accept SL/TP on Market.
            kwargs["stopLoss"] = quantize_price(order["sl"], tick)
            kwargs["takeProfit"] = quantize_price(order["tp"], tick)
            resp = client.place_order(**kwargs) or {}
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
            return "rejected_too_small:no orderId in response"
        if exchange == "breakout":
            return "rejected_too_small:breakout exchange does not support live smoke yet"
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        logger.info(
            "smoke_test rejected by exchange (success signal): "
            "account=%s reason=%s", order.get("account_id"), reason,
        )
        if exchange == "bybit" and "170134" in reason:
            _log_170134_diagnostic(
                client, order, account_cfg, _bybit_category(account_cfg),
            )
            invalidate_tick_cache(order["symbol"], _bybit_category(account_cfg))
        return f"rejected_too_small:{reason}"
    return f"rejected_too_small:unsupported exchange {exchange}"


# ---------------------------------------------------------------------------
# Exchange helpers (kept thin — heavy logic stays in exchange connectors)
# ---------------------------------------------------------------------------


def _fetch_balance(
    client: Any,
    account_cfg: dict,
    *,
    direction: Optional[str] = None,
    symbol: Optional[str] = None,
) -> float:
    """Fetch balance from the exchange client, direction-aware for spot accounts.

    For Bybit spot accounts the relevant balance depends on trade direction:
    - ``direction='short'`` (sell): USD value of held base coin only.
      Using total portfolio value here would let the sizer produce a qty
      that exceeds actual holdings, causing ErrCode 170131 on submission.
    - ``direction='long'`` (buy): available USDT quote balance.
    - No direction context, or derivatives (linear/inverse): total
      portfolio USD value — original behaviour, unchanged.

    Parameters
    ----------
    client : exchange client
        pybit HTTP or ccxt Bybit/Binance handle.
    account_cfg : dict
        Account config; ``market_type`` key selects spot vs derivatives.
    direction : str, optional
        ``"long"`` or ``"short"`` from the OrderPackage.
    symbol : str, optional
        Spot symbol (e.g. ``"BTCUSDT"``); used to identify base coin.
    """
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
        if exchange in ("interactive_brokers", "ib"):
            # IB account equity in USD — NetLiquidation from the account
            # summary, fed to the sizer the same way as Bybit's USD wallet
            # value. Falls back to available funds when NetLiquidation is
            # absent.
            bal = client.balance() or {}
            return float(
                bal.get("net_liquidation")
                or bal.get("available_funds")
                or 0
            )
    except Exception as exc:
        logger.warning("_fetch_balance(%s): %s — defaulting to 0", exchange, exc)
    return 0.0


def _fetch_linear_available_balance(client: Any) -> Optional[float]:
    """Return USDT availableToWithdraw for a Bybit UNIFIED linear-perp account.

    This is the exact free collateral Bybit will allow for new-position
    initial margin — more accurate than balance × leverage × buffer because
    it reflects existing open positions consuming margin. Returns None on
    any error so the caller can fall back gracefully.
    """
    try:
        resp = client.get_wallet_balance(accountType="UNIFIED") or {}
        wallet_list = (resp.get("result") or {}).get("list") or [{}]
        coins = (wallet_list[0] if wallet_list else {}).get("coin", [])
        for coin in coins:
            if (coin.get("coin") or "").upper() == "USDT":
                raw = coin.get("availableToWithdraw")
                if raw not in (None, "", "null"):
                    return max(0.0, float(raw))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_linear_available_balance: %s", exc)
        return None


def _submit_order(client: Any, order: dict, account_cfg: dict) -> str:
    """Place the order via the exchange client and return a trade_id."""
    exchange = (account_cfg.get("exchange") or "bybit").lower()

    # Velotrade integration phase-2: dispatch to the injected
    # DXtradeClient. The bybit branch's retCode-style error handling
    # is mirrored here so a non-zero retCode surfaces as a RuntimeError
    # the coordinator's diagnostic-ping wrapper can format. A missing
    # client (creds env var not set) raises MissingCredentialsError —
    # the coordinator already treats that as the "account not fully
    # configured" path and emits a ping naming the missing env var.
    # The legacy `breakout` exchange stays inert (deprecated alias).
    if exchange == "velotrade":
        from src.units.accounts.dxtrade_client import (
            DXtradeClient,
            MissingCredentialsError,
        )
        if client is None:
            raise MissingCredentialsError(
                f"velotrade live placement: account "
                f"'{account_cfg.get('account_id') or 'unknown'}' is not "
                f"fully configured (no DXtradeClient injected — "
                f"api_key_env="
                f"{account_cfg.get('api_key_env', '')!r} likely unset)."
            )
        if not isinstance(client, DXtradeClient):
            raise TypeError(
                f"velotrade _submit_order: expected DXtradeClient, got "
                f"{type(client).__name__}"
            )
        try:
            resp = client.place({
                "symbol": order["symbol"],
                "side": order["side"],
                "direction": order.get("direction"),
                "entry": order.get("entry"),
                "sl": order.get("sl"),
                "tp": order.get("tp"),
                "qty": order["qty"],
                "strategy": order.get("strategy"),
            }) or {}
        except NotImplementedError as exc:
            raise RuntimeError(
                f"velotrade _submit_order: DXtrade SDK contract pending — {exc}"
            ) from exc
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(
            f"DXtrade rejected order for {order['symbol']}: {reason}"
        )
    if exchange == "breakout":
        raise RuntimeError(
            "breakout exchange is deprecated; migrate the account to "
            "exchange: velotrade in config/accounts.yaml."
        )

    # Interactive Brokers (MES futures via ib_insync). Dispatches to the
    # injected IBClient, mirroring the velotrade branch's retCode-style
    # contract so a non-zero retCode surfaces as a RuntimeError the
    # coordinator's diagnostic-ping wrapper can format. A missing client
    # (Gateway unreachable or connection params unset) raises
    # IBConnectionError — the coordinator treats that as "account not
    # usable this tick" and pings. IB uses no API keys; the live account
    # runs mode: dry_run so this branch only fires for the paper account
    # (mode: live → IB paper gateway, paper money) until the operator
    # promotes the live account (Tier-3).
    if exchange in ("interactive_brokers", "ib"):
        from src.units.accounts.ib_client import IBClient, IBConnectionError
        if client is None:
            raise IBConnectionError(
                f"IB live placement: account "
                f"'{account_cfg.get('account_id') or 'unknown'}' has no "
                f"IBClient (IB Gateway unreachable, or ib_port/ib_account "
                f"unset in config/accounts.yaml)."
            )
        if not isinstance(client, IBClient):
            raise TypeError(
                f"IB _submit_order: expected IBClient, got "
                f"{type(client).__name__}"
            )
        try:
            resp = client.place({
                "symbol": order["symbol"],
                "side": order["side"],
                "direction": order.get("direction"),
                "entry": order.get("entry"),
                "sl": order.get("sl"),
                "tp": order.get("tp"),
                "qty": order["qty"],
                "strategy": order.get("strategy"),
            }) or {}
        except IBConnectionError as exc:
            raise RuntimeError(f"IB _submit_order: {exc}") from exc
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(
            f"IB rejected order for {order['symbol']}: {reason}"
        )

    try:
        if exchange == "bybit":
            category = _bybit_category(account_cfg)
            tick = get_tick_size(client, order["symbol"], category)
            kwargs = {
                "category": category,
                "symbol": order["symbol"],
                "side": order["side"],
                "orderType": "Market",
                "qty": str(order["qty"]),
            }
            if category == "spot":
                # Bybit V5 spot Market: qty is in base-coin units
                # (marketUnit=baseCoin), and SL/TP are NOT accepted on a
                # spot Market order (retCode 170130, BUG-061). reduceOnly
                # is a derivatives-only concept. Spot exits are enforced
                # by the S-030 monitor loop via close_open_position.
                kwargs["marketUnit"] = "baseCoin"
            elif order.get("reduce_only"):
                # Reduce-only path (intent-mode delta dispatch, S-MSE-2).
                # Skip SL/TP — the parent position already has them set
                # and Bybit refuses TP/SL on a reduceOnly leg
                # (retCode 110076 / 30024 depending on the failure
                # mode). The reduce-only flag itself is the signal to
                # Bybit that this order trims an open position.
                kwargs["reduceOnly"] = True
            else:
                # Derivatives (linear/inverse) accept SL/TP on Market.
                kwargs["stopLoss"] = quantize_price(order["sl"], tick)
                kwargs["takeProfit"] = quantize_price(order["tp"], tick)
            # ErrCode 10001 guard: for Buy (Long) orders Bybit requires
            # SL < last_price at submission time. Fast price drops between
            # signal generation and order arrival can violate this even
            # when the strategy set SL correctly below the entry price.
            # Pre-fetch last_price and abort cleanly rather than letting
            # Bybit reject and increment the exchange_rejected counter.
            if kwargs.get("side") == "Buy" and kwargs.get("stopLoss") and not order.get("reduce_only"):
                try:
                    _ticker = client.get_tickers(
                        category=category, symbol=order["symbol"]
                    )
                    _last = float(
                        ((_ticker.get("result") or {}).get("list") or [{}])[0]
                        .get("lastPrice") or 0
                    )
                    _sl = float(kwargs.get("stopLoss") or 0)
                    if _last > 0 and _sl >= _last:
                        raise RuntimeError(
                            f"ErrCode 10001 pre-check: Buy SL {_sl} >= "
                            f"last_price {_last} for {order['symbol']} — "
                            "aborting; price moved against signal between "
                            "generation and submission."
                        )
                except RuntimeError:
                    raise
                except Exception as _te:
                    logger.warning(
                        "_submit_order: SL pre-check ticker fetch failed "
                        "for %s: %s — proceeding without check",
                        order["symbol"], _te,
                    )
            resp = client.place_order(**kwargs)
            return str((resp.get("result") or {}).get("orderId") or uuid.uuid4().hex)
        if exchange == "binance":
            if not order.get("reduce_only"):
                # SL/TP attachment for Binance requires separate
                # STOP_MARKET + TAKE_PROFIT_MARKET orders; that wiring
                # is not yet implemented. Block live open-position orders
                # until it is, so we never place a naked entry.
                raise NotImplementedError(
                    "Binance live orders are blocked: SL/TP attachment "
                    "(separate stop-market / take-profit-market orders) "
                    "is not yet wired in _submit_order. Implement before "
                    "enabling a Binance account in config/accounts.yaml."
                )
            # Reduce-only (close/trim) legs carry no SL/TP by design.
            resp = client.place_market_order(
                symbol=order["symbol"],
                side=order["side"].upper(),
                amount=order["qty"],
            )
            return str((resp or {}).get("id") or (resp or {}).get("orderId") or uuid.uuid4().hex)
    except Exception as exc:
        logger.error("_submit_order(%s): %s", exchange, exc)
        # BUG-057 reopen (2026-05-06): Bybit still rejects spot SL/TP
        # values quantized to the static-map's 0.01 tick with retCode
        # 170134. On every 170134, log the live instrument filters +
        # the exact SL/TP we sent so the next failure tells us
        # ground-truth precision. Then invalidate the tick cache so the
        # next order forces a fresh live lookup rather than repeating the
        # same bad value.
        if exchange == "bybit" and "170134" in str(exc):
            _log_170134_diagnostic(
                client, order, account_cfg, _bybit_category(account_cfg),
            )
            invalidate_tick_cache(order["symbol"], _bybit_category(account_cfg))
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


def _log_170134_diagnostic(
    client: Any, order: dict, account_cfg: dict, category: str,
) -> None:
    """Emit a structured diagnostic when Bybit rejects with 170134.

    Captures the live ``priceFilter`` + ``lotSizeFilter`` from a fresh
    ``get_instruments_info`` call (no cache) and the exact SL/TP /
    qty we just submitted. Logged at ERROR level with a stable
    ``BUG-057-DIAG`` prefix so the operator can grep journalctl for
    every recurrence. Best-effort — never raises (we're already in
    the failure path).
    """
    try:
        symbol = order.get("symbol")
        sl = order.get("sl")
        tp = order.get("tp")
        try:
            tick = get_tick_size(client, symbol, category)
        except Exception:  # noqa: BLE001
            tick = None
        try:
            sl_quantized = quantize_price(sl, tick) if (sl is not None and tick is not None) else None
        except Exception:  # noqa: BLE001
            sl_quantized = None
        try:
            tp_quantized = quantize_price(tp, tick) if (tp is not None and tick is not None) else None
        except Exception:  # noqa: BLE001
            tp_quantized = None
        live = live_instrument_diagnostic(client, symbol, category)
        logger.error(
            "BUG-057-DIAG | account=%s symbol=%s category=%s "
            "qty=%s sl_raw=%r sl_sent=%r tp_raw=%r tp_sent=%r "
            "static_tick=%s live_priceFilter=%s live_lotSizeFilter=%s "
            "live_status=%s",
            account_cfg.get("account_id") or "unknown",
            symbol, category, order.get("qty"),
            sl, sl_quantized, tp, tp_quantized,
            str(tick) if tick is not None else None,
            (live or {}).get("priceFilter"),
            (live or {}).get("lotSizeFilter"),
            (live or {}).get("status"),
        )
    except Exception as diag_exc:  # noqa: BLE001
        logger.warning("BUG-057-DIAG: diagnostic capture itself failed: %s", diag_exc)


# ---------------------------------------------------------------------------
# Trade-journal writer (architecture-audit-2026-05-02 P0-2)
# ---------------------------------------------------------------------------


def _log_trade_to_journal(
    pkg: OrderPackage,
    account_cfg: dict,
    order: dict,
    *,
    trade_id: Optional[str] = None,
    is_dry: bool = False,
    status: str = "open",
    reason: Optional[str] = None,
    intent_reduce: bool = False,
) -> bool:
    """Insert a row into ``trade_journal.db::trades`` for an executor event.

    Three call patterns:

    - **Successful submission** (default): ``status='open'``, ``trade_id``
      is the exchange order id. Close path (S-030 monitor loop) updates
      via ``Database.update_trade``.
    - **Risk-manager rejection**: ``status='rejected'``,
      ``reason`` ∈ {``account_mode_dry_run``, ``DAILY_LOSS_CAP``,
      ``POSITION_SIZE_CAP``, ``INTRADAY_DRAWDOWN``}. ``trade_id`` is
      synthesised as ``rejected-<uuid>``.
    - **Exchange rejection**: ``status='exchange_rejected'``, ``reason``
      is the exchange error string. ``trade_id`` is synthesised.

    Best-effort — a journal failure must never crash the order path.
    Returns True on a successful insert, False on any error (logged but
    never re-raised). ``is_backtest=0`` for runtime trades; the
    backtester writes its own rows with ``is_backtest=1``.

    The ``TRADE_JOURNAL_DB`` env var overrides the DB path; tests can
    set it to a tmp path to avoid polluting the production journal.
    Tests that don't care about the journal patch this helper directly.
    """
    try:
        import json
        from datetime import datetime, timezone
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path

        path = trade_journal_db_path()
        db = Database(db_path=path)
        if trade_id is None:
            trade_id = f"{status}-{uuid.uuid4().hex[:12]}"
        notes_payload = {
            "trade_id": trade_id,
            "is_dry": bool(is_dry),
            "confidence": float(getattr(pkg, "confidence", 0.0) or 0.0),
            "signal_logic": (pkg.meta or {}).get("signal_logic") or "",
        }
        if reason is not None:
            notes_payload["reason"] = str(reason)
        if intent_reduce:
            # Intent-mode reduce leg (S-MSE-2). Stamped so /closed,
            # hourly-report, and the trade-monocle audit can
            # distinguish reduce legs from new opens without re-
            # parsing the exchange response. The execution_delta meta
            # (action + qty_delta + reason) is already on the pkg via
            # the dispatcher; carry the action token through for
            # diagnostics.
            notes_payload["intent_reduce"] = True
            delta_meta = (pkg.meta or {}).get("execution_delta") or {}
            if delta_meta:
                notes_payload["intent_action"] = delta_meta.get("action")
                notes_payload["intent_target_qty"] = delta_meta.get("target_qty")
                notes_payload["intent_current_qty"] = delta_meta.get("current_qty")
        base_entry_reason = (pkg.meta or {}).get("entry_reason") \
            or f"{pkg.strategy} signal"
        if status != "open" and reason:
            entry_reason = f"{status.upper()}: {reason} | {base_entry_reason}"
        elif intent_reduce:
            entry_reason = f"INTENT_REDUCE: {base_entry_reason}"
        else:
            entry_reason = base_entry_reason
        # Reduce legs land a distinguishing ``setup_type`` so downstream
        # aggregations can filter them out of "new entries" cohorts
        # without joining notes JSON.
        setup_type = "intent_reduce" if intent_reduce else pkg.strategy
        # Pull the package id once: stamped on every trade row (the
        # many-to-one back-reference the orphan reconciler reads via
        # ``_resolve_linked_package_id``), and also fed to the
        # primary-leg ``linked_trade_id`` update below.
        pkg_id = (pkg.meta or {}).get("order_package_id")
        is_demo_account = bool(account_cfg.get("demo", False))
        trade_row_id = db.insert_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry_price": float(pkg.entry),
            "stop_loss": float(pkg.sl),
            "take_profit_1": float(pkg.tp),
            "position_size": float(order.get("qty") or 0.0),
            "setup_type": setup_type,
            "entry_reason": entry_reason[:500],
            "status": status,
            "is_backtest": 0,
            "is_demo": int(is_demo_account),
            "strategy_name": pkg.strategy,
            "account_id": str(
                account_cfg.get("account_id") or account_cfg.get("id") or "unknown"
            ),
            "notes": json.dumps(notes_payload, ensure_ascii=False)[:500],
            "order_package_id": pkg_id,
        })
        # Wire the package → trade link so the strategy_monocle gate
        # (pipeline.py::_has_open_package_for_strategy, linked_only=True)
        # actually finds an open package to gate on.
        #
        # PRIMARY entry only. ``order_packages.linked_trade_id`` has
        # one slot, but a single decision can fan out into multiple
        # trade rows: real-money entry + demo mirror + intent_reduce
        # flip leg + multi-account fanout. Pre-fix, every leg called
        # ``update_order_package(linked_trade_id=<its own id>)`` and
        # only the last writer survived — the rest showed up as
        # ``(unlinked)`` in the orphan-sweep ping and the reconciler
        # could not cascade them. Now: every leg already carries the
        # canonical many-to-one back-reference via the trade row's
        # ``order_package_id`` column (above) so the reconciler can
        # resolve all of them; only the real-money primary entry
        # writes the package's ``linked_trade_id`` slot.
        #
        # Rejection rows still skip this — the trade was never live;
        # gating on it would suppress legitimate retries forever.
        is_primary_entry = (
            status == "open"
            and trade_row_id is not None
            and not intent_reduce
            and not is_demo_account
        )
        if is_primary_entry and pkg_id:
            try:
                db.update_order_package(pkg_id, {
                    "linked_trade_id": int(trade_row_id),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "execute_pkg: linked_trade_id update failed "
                    "(pkg_id=%s trade_id=%s): %s",
                    pkg_id, trade_row_id, exc,
                )
        # Trade-lifecycle ping (TELEGRAM-SPEC §4.2) — live opens only.
        # Best-effort: a ping failure must never touch the order path.
        #
        # A reduce-only leg is a PARTIAL CLOSE of an existing position, not a
        # new open: the intent layer flips its ``direction`` to the opposite
        # (reduce) side while keeping the parent position's entry/SL/TP. Firing
        # the "🟢 TRADE OPENED" ping for it printed a phantom "<SYMBOL> SHORT"
        # carrying the parent LONG's open-side SL/TP (SL below / TP above entry)
        # — impossible for a real short, and alarming to the operator. Suppress
        # the open-ping for reduce legs; the position is being trimmed, not
        # opened (health-review BL-20260531-001).
        if status == "open" and not is_dry and not intent_reduce:
            try:
                from src.runtime.execution_diagnostics import enqueue_trade_open

                enqueue_trade_open(
                    account=str(
                        account_cfg.get("account_id")
                        or account_cfg.get("id")
                        or "unknown"
                    ),
                    strategy=pkg.strategy,
                    symbol=pkg.symbol,
                    side=pkg.direction,
                    qty=order.get("qty"),
                    entry=getattr(pkg, "entry", None),
                    sl=getattr(pkg, "sl", None),
                    tp=getattr(pkg, "tp", None),
                    order_id=trade_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "execute_pkg: trade-open ping failed (symbol=%s): %s",
                    pkg.symbol, exc,
                )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execute_pkg: trade-journal write failed (account=%s strategy=%s "
            "symbol=%s status=%s trade_id=%s): %s",
            account_cfg.get("account_id"), pkg.strategy, pkg.symbol,
            status, trade_id, exc,
        )
        return False


def log_rejection_to_journal(
    pkg: OrderPackage,
    account_cfg: dict,
    *,
    reason: str,
    status: str,
    sized_qty: Optional[float] = None,
) -> bool:
    """Public wrapper: log a refusal event to the trade journal.

    Used by ``Coordinator.multi_account_execute`` from its
    ``except RiskBreach`` and generic exception blocks so every
    refusal lands a row alongside the existing diagnostic ping.

    ``status`` is one of ``"rejected"`` (RiskManager refused) or
    ``"exchange_rejected"`` (exchange returned an error). ``reason``
    is the structured token from ``RiskManager.evaluate`` (for
    ``rejected``) or ``str(exc)`` (for ``exchange_rejected``).

    ``sized_qty`` is the qty the RiskManager produced before the
    refusal — written into ``position_size`` so the operator can see
    the would-be size. Pass ``None`` when sizing was not reached
    (e.g. early-stage refusal); the row records ``position_size=0.0``.

    Wraps the underlying write in a defensive try/except so a
    journal-write failure during failure-handling can never escalate
    to a stack unwind.
    """
    try:
        order = {"qty": float(sized_qty or 0.0), "symbol": pkg.symbol}
        return _log_trade_to_journal(
            pkg, account_cfg, order,
            trade_id=None, is_dry=False,
            status=status, reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "log_rejection_to_journal: write failed (account=%s status=%s "
            "reason=%s): %s",
            account_cfg.get("account_id"), status, reason, exc,
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

    Bybit Unified Trading: ``set_trading_stop(category=<resolved>,
    symbol=…, stopLoss=…, takeProfit=…)`` — only valid for the
    derivatives categories (``linear``/``inverse``). Spot accounts
    return ``ok=False`` with a clear error and rely on the monitor
    loop to enforce SL/TP via a market close. Binance is not yet
    supported (only the live trader's Bybit accounts are wired).

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
        category = _bybit_category(account_cfg)
        if category == "spot":
            # set_trading_stop is derivatives-only; Bybit returns
            # retCode=10001 for it on spot. Spot accounts have no
            # exchange-side SL/TP — the S-030 monitor enforces exits via
            # a market close. Refuse cleanly instead of calling the SDK.
            return {"ok": False, "exchange_response": None,
                    "error": "set_trading_stop is derivatives-only; spot "
                             "accounts have no exchange-side SL/TP (the "
                             "monitor enforces exits via market close)"}
        try:
            tick = get_tick_size(exchange_client, symbol, category)
            kwargs = {"category": category, "symbol": symbol}
            if sl is not None:
                kwargs["stopLoss"] = quantize_price(sl, tick)
            if tp is not None:
                kwargs["takeProfit"] = quantize_price(tp, tick)
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

    *side* is the side of the original entry (``"long"`` or ``"short"``};
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
            category = _bybit_category(account_cfg)
            kwargs = {
                "category": category,
                "symbol": symbol,
                "side": close_side,
                "orderType": "Market",
                "qty": str(qty),
            }
            if category == "spot":
                # Spot has no reduceOnly (derivatives-only); the close is a
                # plain Sell of base coin. qty is base-coin units.
                kwargs["marketUnit"] = "baseCoin"
            else:
                kwargs["reduceOnly"] = True
            resp = exchange_client.place_order(**kwargs) or {}
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
