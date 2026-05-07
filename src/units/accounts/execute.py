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
import os
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

# S-047 T1 / T3: Bybit V5 Spot Margin is a routing identity carried by
# ``market_type: spot-margin`` in ``config/accounts.yaml``. The API
# category is still ``spot``; the spot-margin trait is communicated to
# Bybit via the ``isLeverage=1`` flag on ``place_order`` (T3 D4). This
# string is matched case-insensitively against the routing label.
_SPOT_MARGIN_LABEL = "spot-margin"

# Common quote currencies used to parse base coin from spot symbols.
_SPOT_QUOTE_CURRENCIES = ("USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD")


def _spot_base_coin(symbol: str) -> str:
    """Extract base coin ticker from a spot symbol (e.g. ``BTCUSDT`` → ``BTC``)."""
    sym = symbol.upper()
    for quote in _SPOT_QUOTE_CURRENCIES:
        if sym.endswith(quote):
            return sym[: -len(quote)]
    return sym[:-4] if len(sym) > 4 else sym


def _coin_free(coin_row: dict) -> float:
    """Truly-tradeable balance for a coin row from Bybit V5 UNIFIED wallet.

    Bybit V5 ``walletBalance`` is the *total* coin holding — it INCLUDES
    amounts locked in open orders, recent-deposit holds, and (for UTA)
    cross-margin commitments. A spot Sell submitted at ``walletBalance``
    when any portion is locked returns ErrCode 170131 ("Insufficient
    balance"). The truly-available qty is ``walletBalance − locked``.

    ``availableToWithdraw`` is deprecated for UNIFIED accounts (Bybit V5
    changelog 2024) and returns empty, so we cannot rely on it.

    Falls back to ``walletBalance`` only when ``locked`` is missing/null
    (older response shapes / non-UTA accounts). Floors at zero so a
    momentarily negative free (locked > wallet, possible during cross-
    margin liquidation states) doesn't propagate as a negative cap.
    """
    wallet = float(coin_row.get("walletBalance") or 0)
    locked_raw = coin_row.get("locked")
    if locked_raw in (None, "", "null"):
        return max(0.0, wallet)
    try:
        locked = float(locked_raw)
    except (TypeError, ValueError):
        return max(0.0, wallet)
    return max(0.0, wallet - locked)


def _coin_borrow_usd(coin_row: dict) -> float:
    """USD-equivalent of a coin's *free* borrow capacity (UTA Spot Margin).

    Bybit V5 UTA wallet rows expose ``availableToBorrow`` per coin — the
    remaining borrow line **at the exchange's tier**, which is the
    primitive a leveraged spot order actually consumes against. We
    convert that to USDT terms via the row's ``usdValue / walletBalance``
    price ratio (the same conversion the rest of this module uses) so
    the sizer can compare it directly against ``quote_usdt`` (already
    in USDT).

    Returns 0.0 when ``availableToBorrow`` is missing/empty/zero — that
    matches a Classic-spot account, an account whose web-UI Spot Margin
    toggle is OFF, or a coin not enabled for borrow. Best-effort: any
    parse failure returns 0.0 so the sizer's behaviour falls back to
    "free cash only", never larger than today.
    """
    raw = coin_row.get("availableToBorrow")
    if raw in (None, "", "null"):
        return 0.0
    try:
        borrow_qty = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if borrow_qty <= 0:
        return 0.0
    ticker = (coin_row.get("coin") or "").upper()
    if ticker == "USDT":
        # USDT borrow is already in USDT.
        return borrow_qty
    # Convert base-coin borrow qty to USDT via the row's usdValue ratio.
    wallet_total = float(coin_row.get("walletBalance") or 0)
    usd_total = float(coin_row.get("usdValue") or 0)
    if wallet_total > 0 and usd_total > 0:
        return borrow_qty * (usd_total / wallet_total)
    return 0.0


def _fetch_spot_coin_balances(client: Any, symbol: str) -> dict:
    """Return *free* base-coin qty and free USDT from Bybit UNIFIED wallet.

    Fetches a fresh wallet balance snapshot and computes the truly-
    tradeable amount of the base coin (e.g. BTC for BTCUSDT) and quote
    coin (USDT) — i.e. ``walletBalance − locked`` for each. Using the
    locked-aware figure prevents Bybit ErrCode 170131 ("Insufficient
    balance") on spot Sells when any portion of the holding is locked
    in open orders or recent-deposit holds. Returns zeros on any error
    so the caller can safely cap or refuse rather than propagating an
    exception into the order path.

    Returned dict keys:
        ``base_coin``      — ticker string (e.g. "BTC")
        ``base_qty``       — *free* base coin (walletBalance − locked)
        ``base_usd_value`` — USD value of *free* base coin (sizer input);
                             scaled from the wallet's total ``usdValue``
                             by the free/total ratio so the sizer never
                             treats locked BTC as risk capital.
        ``quote_usdt``     — *free* USDT cash (walletBalance − locked).
                             This is **collateral** for risk-of-ruin
                             math — what an account would actually lose
                             at liquidation.
        ``base_borrow_usd``  — USD-equivalent of the base coin's free
                             borrow capacity (UTA Spot Margin). 0.0 on
                             cash spot or when the toggle is off.
                             Sells on spot-margin can be sized against
                             ``base_usd_value + base_borrow_usd``.
        ``quote_borrow_usd`` — USDT borrow capacity (UTA Spot Margin).
                             0.0 on cash spot or when the toggle is off.
                             Buys on spot-margin can be sized against
                             ``quote_usdt + quote_borrow_usd``.
        ``total_account_usd`` — Bybit ``totalEquity`` for the wallet:
                             free + locked across all coins, in USD,
                             excluding borrow capacity. Used by
                             ``RiskManager.position_size`` for the
                             ``min_balance_usd`` gate ("is this account
                             big enough to bother sizing into?"). None
                             when the field is missing/unparseable so
                             the gate falls back to ``balance_usd`` —
                             same semantics as the pre-S-052 contract.

    The two ``*_borrow_usd`` fields default to 0.0, so callers that
    don't read them keep the pre-S-049 cash-only behaviour byte-for-byte.
    ``total_account_usd`` defaults to None for the same reason.
    """
    base = _spot_base_coin(symbol)
    result: dict = {
        "base_coin": base,
        "base_qty": 0.0,
        "base_usd_value": 0.0,
        "quote_usdt": 0.0,
        "base_borrow_usd": 0.0,
        "quote_borrow_usd": 0.0,
        "total_account_usd": None,
    }
    try:
        resp = client.get_wallet_balance(accountType="UNIFIED") or {}
        wallet_list = (resp.get("result") or {}).get("list") or [{}]
        wallet = wallet_list[0]
        # Top-level totalEquity is the operator's full position-able
        # capital in USD — free + locked across every coin in the
        # wallet, excluding borrow capacity. The min_balance_usd gate
        # is a "is this account big enough?" question and applies to
        # total equity, not free quote-coin (S-052: distinguishes from
        # the sizer's collateral input which remains free USDT).
        te_raw = wallet.get("totalEquity")
        if te_raw not in (None, "", "null"):
            try:
                result["total_account_usd"] = float(te_raw)
            except (TypeError, ValueError):
                pass
        coins = wallet.get("coin", [])
        for coin in coins:
            ticker = (coin.get("coin") or "").upper()
            if ticker == base.upper():
                wallet_total = float(coin.get("walletBalance") or 0)
                free = _coin_free(coin)
                usd_total = float(coin.get("usdValue") or 0)
                result["base_qty"] = free
                # Scale total usdValue to just the free portion so the
                # sizer doesn't risk capital that's actually locked.
                if wallet_total > 0:
                    result["base_usd_value"] = usd_total * (free / wallet_total)
                else:
                    result["base_usd_value"] = 0.0
                result["base_borrow_usd"] = _coin_borrow_usd(coin)
            elif ticker == "USDT":
                result["quote_usdt"] = _coin_free(coin)
                result["quote_borrow_usd"] = _coin_borrow_usd(coin)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_spot_coin_balances(%s): %s", symbol, exc)
    return result


# Safety buffer applied when capping a spot-sell qty to the available
# base-coin balance. Bybit can race us — between our pre-flight read and
# the order matching, ``locked`` can grow (e.g. another submitted order)
# and tip a balance-equal sell into 170131. 0.5% headroom absorbs that
# without materially shrinking realised position size.
_SPOT_SELL_SAFETY_BUFFER = 0.995

# S-049: matching headroom on the BUY side. A spot Buy submitted at
# qty == free_usdt / price still pays exchange fees + slippage on top
# of the notional, which Bybit charges from the same wallet — so a
# qty notional that exactly matches free USDT trips ErrCode 170131
# ("Insufficient balance") even with isLeverage=1 in the request
# (the matching engine validates `availableBalance >= notional + fees`
# before considering borrow capacity). Apply this buffer once at the
# coordinator boundary so both cash-spot and spot-margin sizing leave
# fee headroom; spot-margin's effective borrow is unaffected because
# the buffer also scales the borrow-capacity component the sizer
# consumes.
_SPOT_BUY_SAFETY_BUFFER = 0.995


def _bybit_category(account_cfg: dict) -> str:
    """Resolve the Bybit V5 ``category`` for this account.

    Reads ``account_cfg["market_type"]`` (set from
    ``config/accounts.yaml`` per the operator directive 2026-05-06 — the
    fix for the perp-instead-of-spot bug). Default = ``"spot"`` so any
    account that omits the field trades the cash market, matching what
    the operator's wallet holds (BTC + USDT for ``bybit_1``, USDT for
    ``bybit_2``).

    Spot-margin accounts (S-047 T1) carry ``market_type: spot-margin``;
    the API category is still ``spot`` — the spot-margin trait is
    communicated via ``isLeverage=1`` (see ``_is_spot_margin``).
    """
    raw = str(account_cfg.get("market_type") or _BYBIT_CATEGORY_DEFAULT).strip().lower()
    if raw == "perp" or raw == "perpetual" or raw == "futures":
        raw = "linear"
    if raw == _SPOT_MARGIN_LABEL:
        return "spot"
    if raw not in _BYBIT_VALID_CATEGORIES:
        logger.warning(
            "_bybit_category: account=%s has unknown market_type=%r — "
            "defaulting to %s",
            account_cfg.get("account_id"), raw, _BYBIT_CATEGORY_DEFAULT,
        )
        return _BYBIT_CATEGORY_DEFAULT
    return raw


def _is_spot_margin(account_cfg: dict) -> bool:
    """Return True when *account_cfg* declares ``market_type: spot-margin``.

    S-047 T3 (D4): the routing identity is carried by the ``market_type``
    field on the account row in ``config/accounts.yaml``. Spot-margin
    accounts share the Bybit V5 ``spot`` category but pass
    ``isLeverage=1`` on ``place_order`` so the wallet can borrow base
    coin (short) or quote coin (long with leverage) against USDT
    collateral. Non-spot-margin accounts return False and follow the
    existing cash-spot code path unchanged. Per
    ``docs/sprint-plans/S-047-bybit2-spot-margin.md`` § 5b this is a
    routing label, not a gate.
    """
    raw = str(account_cfg.get("market_type") or "").strip().lower()
    return raw == _SPOT_MARGIN_LABEL


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

    # 5. Spot-sell pre-flight balance guard.
    # The coordinator-level sizer may use total portfolio balance (USDT +
    # BTC converted) to compute qty. For a spot Sell the account must
    # actually hold that qty in *free* base coin (walletBalance − locked);
    # using total balance — or even walletBalance with a non-zero locked —
    # causes Bybit ErrCode 170131 ("Insufficient balance"). This guard
    # fetches the live free base-coin balance, applies a small safety
    # buffer to absorb race conditions between read and submission, caps
    # qty if over, and refuses outright when no free base coin is held.
    #
    # S-047 T3 (D4): spot-margin accounts skip this pre-flight — they
    # can borrow base coin to sell, so a zero free-BTC balance is not a
    # refusal condition. RiskManager (T2 D3) owns sizing decisions for
    # spot-margin from USDT collateral; if the borrow ceiling is hit at
    # the exchange, retCode 110095 surfaces via ``report_api_failure``
    # — same handling as any other exchange retCode (no new gate).
    if (
        not is_dry
        and exchange_client is not None
        and _bybit_category(account_cfg) == "spot"
        and not _is_spot_margin(account_cfg)
        and pkg.direction == "short"
        and not _is_test_order(pkg)
    ):
        _spot_bal = _fetch_spot_coin_balances(exchange_client, pkg.symbol)
        _available_base = _spot_bal["base_qty"]
        _safe_available = _available_base * _SPOT_SELL_SAFETY_BUFFER
        _min_qty = float(
            (account_cfg.get("risk") or {}).get("min_qty")
            or account_cfg.get("min_qty")
            or 0.001
        )
        _qty_precision = int(
            (account_cfg.get("risk") or {}).get("qty_precision")
            or account_cfg.get("qty_precision")
            or 3
        )
        if _safe_available < _min_qty:
            raise RuntimeError(
                f"Spot sell refused for {pkg.symbol}: insufficient free "
                f"{_spot_bal['base_coin']} balance "
                f"(free {_available_base:.6f} × buffer "
                f"{_SPOT_SELL_SAFETY_BUFFER}, min_qty {_min_qty}). "
                f"Wallet may be locked in open orders or recent deposits."
            )
        if qty > _safe_available:
            from src.units.accounts.risk import _floor_to_step
            _capped = _floor_to_step(_safe_available, _qty_precision)
            logger.warning(
                "execute_pkg: spot sell qty capped %.6f → %.6f %s "
                "(free=%.6f, buffer=%.4f, account=%s symbol=%s)",
                qty, _capped, _spot_bal["base_coin"],
                _available_base, _SPOT_SELL_SAFETY_BUFFER,
                account_id, pkg.symbol,
            )
            qty = _capped
            if qty < _min_qty:
                raise RuntimeError(
                    f"Spot sell refused for {pkg.symbol}: floored free "
                    f"{_spot_bal['base_coin']} ({_available_base:.6f}) "
                    f"rounds to {qty:.6f} which is below min_qty {_min_qty}."
                )

    # 6. Refuse zero-qty orders before hitting the exchange.
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
    }

    logger.info(
        "execute_pkg: account=%s strategy=%s symbol=%s direction=%s entry=%.4f "
        "sl=%.4f tp=%.4f qty=%.4f dry_run=%s",
        account_id, pkg.strategy, pkg.symbol, pkg.direction,
        pkg.entry, pkg.sl, pkg.tp, qty, is_dry,
    )

    # 7. Submit or simulate
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
                # Spot place_order interprets qty as base-coin by default
                # for Sell and quote-coin for market Buy; risk sizing
                # produces a base-coin qty so pin marketUnit accordingly.
                # Bybit V5 spot only accepts ``stopLoss``/``takeProfit``
                # on Limit orders — passing them on a Market order
                # returns retCode 170130 ("Data sent for parameter '' is
                # not valid"). The S-030 monitor loop enforces SL/TP for
                # spot via ``close_open_position`` instead. See BUG-061.
                kwargs["marketUnit"] = "baseCoin"
                if _is_spot_margin(account_cfg):
                    # S-047 T3 (D4): route this order through Bybit V5
                    # Spot Margin so the wallet can borrow against its
                    # USDT collateral (BTC borrow on Sell, USDT borrow
                    # on leveraged Buy). Until the operator flips the
                    # web-UI Spot Margin toggle, retCode 110007 surfaces
                    # via report_api_failure — no new gate.
                    kwargs["isLeverage"] = 1
            else:
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
    category = _bybit_category(account_cfg)
    try:
        if exchange == "bybit":
            if category == "spot" and direction and symbol:
                spot = _fetch_spot_coin_balances(client, symbol)
                if direction == "short":
                    return spot["base_usd_value"]
                return spot["quote_usdt"]
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
                # See _submit_test_order for the marketUnit rationale.
                # SL/TP on spot Market is rejected by Bybit V5 with
                # retCode 170130 — the S-030 monitor loop enforces them
                # via ``close_open_position`` for spot. See BUG-061.
                kwargs["marketUnit"] = "baseCoin"
                if _is_spot_margin(account_cfg):
                    # S-047 T3 (D4): route through Bybit V5 Spot Margin.
                    # See _submit_test_order for the rationale.
                    kwargs["isLeverage"] = 1
            else:
                # Derivatives (linear/inverse) accept SL/TP on Market.
                kwargs["stopLoss"] = quantize_price(order["sl"], tick)
                kwargs["takeProfit"] = quantize_price(order["tp"], tick)
            resp = client.place_order(**kwargs)
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

        path = (
            os.environ.get("TRADE_JOURNAL_DB")
            or os.path.join(
                os.path.abspath(os.path.join(os.path.dirname(__file__),
                                             "..", "..", "..")),
                "trade_journal.db",
            )
        )
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
        base_entry_reason = (pkg.meta or {}).get("entry_reason") \
            or f"{pkg.strategy} signal"
        if status != "open" and reason:
            entry_reason = f"{status.upper()}: {reason} | {base_entry_reason}"
        else:
            entry_reason = base_entry_reason
        db.insert_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry_price": float(pkg.entry),
            "stop_loss": float(pkg.sl),
            "take_profit_1": float(pkg.tp),
            "position_size": float(order.get("qty") or 0.0),
            "setup_type": pkg.strategy,
            "entry_reason": entry_reason[:500],
            "status": status,
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
            # Bybit's ``set_trading_stop`` is a derivatives-only endpoint;
            # spot SL/TP modifications go through conditional-order amend
            # flows that the bot does not yet track. The S-030 monitor
            # loop enforces SL/TP for spot accounts via a close order
            # rather than an exchange-side bracket update.
            return {"ok": False, "exchange_response": None,
                    "error": "set_trading_stop not supported for spot — "
                             "monitor loop must close via close_open_position"}
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
                # Spot has no derivative-style positions to "reduce";
                # closing a long is just a market sell of held base coin.
                kwargs["marketUnit"] = "baseCoin"
                if _is_spot_margin(account_cfg):
                    # S-047 T3 (D4): closing a spot-margin position
                    # repays the borrow line — the close order must
                    # also route through Spot Margin (isLeverage=1) so
                    # Bybit recognises it as a borrow settlement
                    # instead of a fresh cash-spot order. T4 (vwap
                    # monitor close logic) is the primary caller for
                    # bybit_2.
                    kwargs["isLeverage"] = 1
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