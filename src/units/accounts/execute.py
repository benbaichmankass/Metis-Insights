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
from src.units.accounts.risk import (
    requires_whole_unit_qty,
    size_order_from_cfg,
    whole_unit_qty,
)

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

    # Breakout prop account — manual browser-bridge (no exchange socket). A
    # prop-routed strategy emits a paste-ready DXTrade ticket (Telegram/FCM
    # prop_signal) instead of placing an order; the order package journals but
    # the returned `prop-manual-<uuid>` marks a manual fill, so NO live exchange
    # position is created (design: breakout-poc-manual-bridge). Gated on the
    # CALLER's dry-ness (which already folds in the account `mode:` AND the
    # per-strategy `execution: shadow` gate) — so a shadow strategy / dry account
    # does NOT emit — and evaluated BEFORE the `client is None → is_dry` forcing
    # below (breakout never has a client, so that forcing would otherwise
    # suppress every ticket).
    _exchange = str(account_cfg.get("exchange") or "").strip().lower()
    if _exchange == "breakout":
        if is_dry:
            trade_id = f"dry-{uuid.uuid4().hex[:12]}"
            logger.info("breakout: dry/shadow — ticket NOT emitted: %s %s",
                        getattr(pkg, "strategy", "?"), getattr(pkg, "symbol", "?"))
            # A shadow/dry prop leg emits NO ticket, but the order package logged
            # in Coordinator._log_new_order_package still sits status='open' /
            # linked_trade_id=NULL. Because this branch returns a TRUTHY `dry-`
            # trade_id, the coordinator's BUG-049 no-trade backstop treats the leg
            # as placed and never terminalises the package — so the monitor
            # reconciler mis-stamps it 'orphaned — never executed' at +5min. That
            # red-flag status is wrong for a deliberate shadow/dry non-emit, and it
            # made the shadow prop variants (trend_donchian_{sol,eth}_prop, which
            # are execution: shadow) surface as alarming "orphaned" rows on
            # /api/bot/prop/tickets. Terminalise the package accurately with
            # status='shadow' (a non-'open' status the orphan sweep + the
            # strategy-monocle both ignore, and distinct from a real 'emitted'
            # ticket) — the mirror of the live-branch prop-package contract below.
            # Best-effort: a journal hiccup must never change the (already-decided)
            # no-emit outcome.
            try:
                pkg_id = (getattr(pkg, "meta", None) or {}).get("order_package_id")
                if pkg_id:
                    from src.units.db.database import Database
                    from src.utils.paths import trade_journal_db_path

                    Database(db_path=trade_journal_db_path()).update_order_package(
                        pkg_id, {
                            "status": "shadow",
                            "close_reason": "prop_shadow_no_emit",
                        })
            except Exception as exc:  # noqa: BLE001 — never break the no-emit path
                logger.warning(
                    "execute_pkg: prop shadow package status update failed "
                    "(strategy=%s symbol=%s pkg_id=%s): %s",
                    getattr(pkg, "strategy", "?"), getattr(pkg, "symbol", "?"),
                    (getattr(pkg, "meta", None) or {}).get("order_package_id"), exc,
                )
            return trade_id
        from src.prop.breakout_executor import emit_prop_ticket
        order = {
            "symbol": pkg.symbol, "direction": pkg.direction,
            "side": "Buy" if pkg.direction == "long" else "Sell",
            "entry": pkg.entry, "sl": pkg.sl, "tp": pkg.tp,
            "strategy": getattr(pkg, "strategy", account_id),
            # meta carries the exit structure (e.g. tp2 for a TP1→TP2 ladder) so
            # the P3 observe-only prop-ladder soak can derive the real ExitPlan,
            # AND the order_package_id so emit_prop_ticket can stamp it on the
            # prop_tickets row (the ticket↔package join).
            "meta": (getattr(pkg, "meta", None) or {}),
        }
        trade_id = emit_prop_ticket(
            order, account_cfg,
            timeframe=(getattr(pkg, "meta", None) or {}).get("timeframe"))
        # Prop is a MANUAL bridge: no trades row is written (prop is isolated
        # from the `trades` table by design), so the order package logged in
        # Coordinator._log_new_order_package would sit status='open' /
        # linked_trade_id=NULL and _sweep_unlinked_packages would mis-stamp it
        # 'orphaned — never executed' at +5min. But the ticket WAS emitted, so
        # 'orphaned' is wrong (this is the prop-package orphan bug). Terminate
        # the package lifecycle accurately with status='emitted' (a non-'open'
        # status the orphan sweep + strategy-monocle gate both ignore). Mirrors
        # the BUG-049 dry-branch contract — every open package reaches a
        # terminal, accurate status — without polluting the `trades` table.
        # Best-effort: a journal hiccup must never break the (already-emitted)
        # ticket.
        try:
            pkg_id = (getattr(pkg, "meta", None) or {}).get("order_package_id")
            if pkg_id:
                from src.units.db.database import Database
                from src.utils.paths import trade_journal_db_path

                Database(db_path=trade_journal_db_path()).update_order_package(
                    pkg_id, {
                        "status": "emitted",
                        "close_reason": "prop_ticket_emitted",
                    })
        except Exception as exc:  # noqa: BLE001 — never break the emitted ticket
            logger.warning(
                "execute_pkg: prop package status update failed "
                "(account=%s strategy=%s symbol=%s pkg_id=%s): %s",
                account_id, getattr(pkg, "strategy", "?"),
                getattr(pkg, "symbol", "?"),
                (getattr(pkg, "meta", None) or {}).get("order_package_id"), exc,
            )
        return trade_id

    # ``is_dry`` up to here reflects a GENUINE no-live-order decision — the
    # account ``mode: dry_run`` OR the caller-folded per-strategy
    # ``execution: shadow`` gate (mgc_trend_1h, e.g., is shadow). A missing
    # exchange client is a DIFFERENT condition: the account is live but we
    # couldn't build a client this tick (e.g. the IB gateway is wedged), so we
    # still take the no-order path — but it must NOT be journaled as an
    # intentional dry-run (BL-20260707-MGCTREND-REASON-MISMATCH). Capture which
    # cause we're in so the rejection row carries an honest reason + is_dry that
    # agree with each other.
    _genuinely_dry = is_dry
    _client_unavailable = exchange_client is None
    if _client_unavailable:
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

    # 5b. Whole-share invariant for whole-unit venues (e.g. alpaca bracket
    # orders reject fractionals). ``position_size`` already sizes whole shares
    # on the entry path, but quantize here too — defensively, and on EVERY
    # entry path incl. ``qty_override`` — so the qty we JOURNAL is exactly the
    # qty the client places (it floors to ``max(1, round(qty))``). Without this
    # a fractional qty that reached this point was journaled verbatim while the
    # broker held the rounded whole share → journal-vs-broker drift
    # (BL-20260622-ALPACA-FRACTIONAL-SIZE). Skipped for test orders (smoke
    # tests deliberately place a tiny sub-min qty).
    if (
        not _is_test_order(pkg)
        and qty > 0
        and requires_whole_unit_qty(account_cfg.get("exchange"))
    ):
        qty = whole_unit_qty(qty, min_one=True)

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
        # BUG-049 fix (2026-06-23): a dry/shadow dispatch must TERMINATE the
        # package's lifecycle with a journaled reason. Otherwise the order
        # package logged in Coordinator._log_new_order_package stays
        # status='open' / linked_trade_id=NULL and the monitor reconciler
        # mis-stamps it 'orphaned — never executed' at the 5-min mark
        # (orphaned is a red-flag status, never acceptable). A shadow-execution
        # strategy and a dry_run account both reach this branch; both deliberately
        # place no live order, so the correct record is a non-live rejection row
        # carrying the order_package_id. _sweep_unlinked_packages path (1) then
        # relabels the package 'rejected' from that row instead of orphaning it
        # (path 2). Mirrors the BUG-044 contract: every open package pairs with a
        # journal-row reason. Best-effort — a logging failure must never break the
        # (no-op) dry dispatch.
        #
        # Smoke/test orders (_is_test_order) are EXCLUDED: the smoke path journals
        # its own 'dry_run' row via the coordinator, so a rejection row here would
        # double-log it. Only real shadow/dry decisions (which otherwise orphan)
        # get the rejection row.
        try:
            if not _is_test_order(pkg):
                # Honest reason + is_dry that AGREE (BL-20260707-MGCTREND-
                # REASON-MISMATCH). A genuine dry/shadow decision → is_dry=True
                # + 'dry_run_no_order_placed'; a live dispatch we couldn't place
                # because the client was unavailable (gateway down) → is_dry=
                # False + a distinct 'exchange_client_unavailable_no_order_placed'
                # so it isn't mistaken for an intentional dry-run. (is_dry here
                # writes only notes.is_dry — the is_demo/account_class paper-vs-
                # real column is derived separately from account_cfg, unaffected.)
                if _genuinely_dry:
                    _rej_reason = "dry_run_no_order_placed"
                    _rej_is_dry = True
                else:
                    _rej_reason = "exchange_client_unavailable_no_order_placed"
                    _rej_is_dry = False
                log_rejection_to_journal(
                    pkg, account_cfg,
                    reason=_rej_reason,
                    status="rejected",
                    sized_qty=float(qty or 0.0),
                    is_dry=_rej_is_dry,
                )
        except Exception as exc:  # noqa: BLE001 — never let journaling crash dispatch
            logger.warning(
                "execute_pkg: dry-run rejection-journal write failed "
                "(account=%s strategy=%s symbol=%s): %s",
                account_id, pkg.strategy, pkg.symbol, exc,
            )
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

    # Options-expression accounts (Slice 3b): an Alpaca account may declare it
    # EXPRESSES its orders as defined-risk debit verticals (an account-scoped
    # capability — the strategy stays a pure signal generator). Such an account
    # routes the SAME order package through the options pipeline (chain → select →
    # size → mleg place) instead of the equity bracket. Opens only; reduce-only
    # (close) legs fall through to the equity path (options close/expiry is the
    # Slice-4 monitor). A refusal (no chain / no fit / budget) places NOTHING and
    # journals a rejection row — never a fabricated trade. The branch is inert for
    # any account without an `options:` block (equity path byte-for-byte unchanged).
    _opt_cfg = None
    if not reduce_only:
        from src.units.accounts.options_overlay import account_expresses_options
        _opt_cfg = account_expresses_options(account_cfg)
    if _opt_cfg is not None:
        from src.units.accounts.options_overlay import place_options_expression
        _res = place_options_expression(
            pkg, _opt_cfg, exchange_client=exchange_client, is_dry=is_dry,
        )
        if _res.refused:
            try:
                log_rejection_to_journal(
                    pkg, account_cfg,
                    reason=f"options_expression:{_res.reason}",
                    status="rejected", sized_qty=0.0,
                )
            except Exception as exc:  # noqa: BLE001 — never let journaling crash dispatch
                logger.warning(
                    "execute_pkg: options-expression rejection-journal failed "
                    "(account=%s symbol=%s): %s", account_id, pkg.symbol, exc,
                )
            return f"opt-norfit-{uuid.uuid4().hex[:10]}"
        trade_id = _res.trade_id or f"opt-{uuid.uuid4().hex[:10]}"
        # Journal the spread as the paper-soak row (qty = contracts). Slice 5:
        # persist the leg/strike/defined-risk structure in notes.options so
        # /api/bot/positions + the apps can render it (per-leg live greeks/PnL
        # remain a follow-up — the positions endpoint is connection-free).
        _opt_order = dict(order)
        _opt_order["qty"] = float(_res.contracts)
        _opt_notes = None
        try:
            from src.units.accounts.options_overlay import options_structure_dict
            _opt_notes = {"options": options_structure_dict(_res)}
        except Exception as exc:  # noqa: BLE001 — surfacing detail is best-effort
            logger.warning(
                "execute_pkg: options structure-notes build failed "
                "(symbol=%s): %s", pkg.symbol, exc,
            )
        _log_trade_to_journal(
            pkg, account_cfg, _opt_order, trade_id=trade_id, is_dry=is_dry,
            extra_notes=_opt_notes,
        )
        return trade_id

    # BL-20260721-BYBIT2-XRP-TPSL-LEGCAP: under BYBIT_TPSL_MODE=partial,
    # Bybit creates this order's qty-scoped SL/TP legs as a side effect —
    # the place-order response never returns their orderId. Snapshot the
    # symbol's live conditional orders BEFORE placing so the post-placement
    # diff (below) can identify exactly which leg(s) this entry created.
    # Reduce-only legs set no new SL/TP, so they're excluded. Two extra
    # read-only API calls per qualifying entry; best-effort (an empty/failed
    # snapshot just means no leg gets tracked — see _classify_new_partial_tpsl_legs).
    _tpsl_pre_leg_ids = None
    if (
        not reduce_only
        and _bybit_tpsl_mode() == "partial"
        and str(account_cfg.get("exchange") or "bybit").lower() == "bybit"
        and _bybit_category(account_cfg) != "spot"
    ):
        _tpsl_pre_leg_ids = _partial_tpsl_leg_ids(
            exchange_client, _bybit_category(account_cfg), order["symbol"],
        )

    trade_id = _submit_order(exchange_client, order, account_cfg)

    sl_order_id = tp_order_id = None
    if _tpsl_pre_leg_ids is not None:
        sl_order_id, tp_order_id = _classify_new_partial_tpsl_legs(
            exchange_client, _bybit_category(account_cfg), order["symbol"],
            _tpsl_pre_leg_ids,
        )

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
        sl_order_id=sl_order_id, tp_order_id=tp_order_id,
    )

    # P3 observe-only exit-ladder soak: for a live OPENING order (reduce-only
    # legs are closes, not new exits), log the laddered exit that WOULD be used
    # (the materialized ExitPlan sized to the placed qty) next to the single
    # SL/TP bracket actually placed. Best-effort — never changes or blocks the
    # order; nothing reads it back (graduation to a real laddered exit is the
    # backtest-gated P4).
    if not reduce_only:
        try:
            from src.runtime.exit_ladder_soak import record_exit_ladder_soak
            record_exit_ladder_soak(
                venue="api",
                strategy=pkg.strategy, symbol=pkg.symbol, direction=pkg.direction,
                entry=pkg.entry, sl=pkg.sl, tp=pkg.tp, qty=qty,
                account_id=account_id,
                account_class=str(account_cfg.get("account_class") or ""),
                timeframe=str((getattr(pkg, "meta", None) or {}).get("timeframe") or ""),
                order_meta=(getattr(pkg, "meta", None) or {}),
                extra={"side": side, "exchange": _exchange},
            )
        except Exception as exc:  # noqa: BLE001 — observe-only metadata
            logger.debug("exit_ladder_soak(api) skipped for %s: %s", pkg.symbol, exc)

    # M19 D1 observe-only fc-geometry soak: for a live OPENING order, log the
    # placed SL/TP next to the decision-time quantile-forecast snapshot
    # (forecast_live's fc_* row). The offline fc→geometry backtest failed its
    # reality-calibration anchor (MB-20260705-FC-SLTP-GEOMETRY); this soak is
    # the faithful replacement. Best-effort — never changes or blocks the
    # order; nothing reads it back (counterfactual resolution + the censored
    # flag live trainer-side in scripts/ml/fc_geometry_resolve.py).
    if not reduce_only:
        try:
            from src.runtime.fc_geometry_soak import record_fc_geometry_soak
            record_fc_geometry_soak(
                venue="api",
                strategy=pkg.strategy, symbol=pkg.symbol, direction=pkg.direction,
                entry=pkg.entry, sl=pkg.sl, tp=pkg.tp, qty=qty,
                account_id=account_id,
                account_class=str(account_cfg.get("account_class") or ""),
                timeframe=str((getattr(pkg, "meta", None) or {}).get("timeframe") or ""),
                extra={"side": side, "exchange": _exchange},
            )
        except Exception as exc:  # noqa: BLE001 — observe-only metadata
            logger.debug("fc_geometry_soak(api) skipped for %s: %s", pkg.symbol, exc)

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
        if exchange in ("oanda", "alpaca"):
            # M15 brokers: OandaClient.balance() = account NAV;
            # AlpacaClient.balance() = equity — both USD for the
            # configured practice/paper accounts. Same role as the IB
            # NetLiquidation branch above. Missing before
            # BL-20260611-006: the fallthrough returned 0.0 and the risk
            # gate refused every gold/ETF signal on gate_balance=0.00
            # (trade #2536).
            bal = client.balance() if client is not None else None
            if bal is None:
                # Raise so the outer except catches it with a clear log.
                # Common cause: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
                # (or OANDA_API_KEY / OANDA_ACCOUNT_ID) unset or API
                # unreachable. Without this, None silently becomes 0.0 and
                # every signal is refused as zero_balance with no indication
                # of what actually failed.
                raise RuntimeError(
                    f"{exchange} balance() returned None — credentials missing "
                    f"or API unreachable (check ALPACA_API_KEY_ID / "
                    f"ALPACA_API_SECRET_KEY for alpaca, or OANDA_API_KEY / "
                    f"OANDA_ACCOUNT_ID for oanda)"
                )
            return float(bal)
    except Exception as exc:
        logger.warning("_fetch_balance(%s): %s — defaulting to 0", exchange, exc)
    return 0.0


def _fetch_linear_available_balance(client: Any) -> Optional[float]:
    """Return the trading-available margin (USD) for a Bybit UNIFIED account.

    Reads the **account-level ``totalAvailableBalance``** — Bybit V5's
    "available balance for new positions" figure
    (``totalMarginBalance − haircut − totalInitialMargin``). It already nets
    out the initial margin consumed by existing positions AND open orders, so
    it is exactly the collateral the exchange will allow toward a NEW order's
    initial margin (the sizer multiplies it by leverage to get max notional).

    History (BL-20260701-BYBIT-AVAILABLE-FIELD): this previously read the
    per-coin USDT ``availableToWithdraw``. That was wrong on two counts —
    (1) ``availableToWithdraw`` is a WITHDRAWAL-eligibility figure (funds free
    to move OFF the exchange), governed by different rules than new-order
    margin; (2) Bybit **deprecated** per-coin ``availableToWithdraw`` for
    UNIFIED accounts on 2025-01-09, so it can return a stale/misleading value
    (on the demo endpoint it reports ≈ the full wallet balance). Sizing against
    it let the margin pre-flight cap over-permit, and Bybit then rejected the
    order at submit with ``110007 "ab not enough for new order"``.

    Falls back to the deprecated per-coin ``availableToWithdraw`` ONLY if
    ``totalAvailableBalance`` is absent (older API shape), then None on any
    error so the caller degrades to the buffer fallback gracefully.
    """
    try:
        resp = client.get_wallet_balance(accountType="UNIFIED") or {}
        wallet_list = (resp.get("result") or {}).get("list") or [{}]
        account = wallet_list[0] if wallet_list else {}
        # Preferred: account-level available-for-trading margin.
        raw = account.get("totalAvailableBalance")
        if raw not in (None, "", "null"):
            return max(0.0, float(raw))
        # Legacy fallback: per-coin availableToWithdraw (deprecated for UTA
        # 2025-01-09, but honoured for an old response shape that omits the
        # account-level field).
        for coin in account.get("coin", []) or []:
            if (coin.get("coin") or "").upper() == "USDT":
                raw = coin.get("availableToWithdraw")
                if raw not in (None, "", "null"):
                    return max(0.0, float(raw))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_linear_available_balance: %s", exc)
        return None


def _fetch_linear_total_equity(client: Any) -> Optional[float]:
    """Return total USD cross-margin equity for a Bybit UNIFIED account.

    For a UNIFIED (cross-margin) linear-perp account the position is backed
    by the *total* account equity, not just the free wallet balance — so the
    risk-manager's min-balance gate, daily-loss-budget basis, and margin
    buffer fallback (S-052) all want total equity as the equity figure.
    Reads the account-level ``totalEquity`` from the same
    ``get_wallet_balance(accountType="UNIFIED")`` response shape used by
    ``_fetch_linear_available_balance``, falling back to
    ``totalWalletBalance`` when ``totalEquity`` is absent. Returns None on
    any error / missing field so the caller can fall back to the current
    free-balance behaviour.
    """
    try:
        resp = client.get_wallet_balance(accountType="UNIFIED") or {}
        wallet_list = (resp.get("result") or {}).get("list") or [{}]
        account = wallet_list[0] if wallet_list else {}
        for field in ("totalEquity", "totalWalletBalance"):
            raw = account.get(field)
            if raw not in (None, "", "null"):
                return max(0.0, float(raw))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fetch_linear_total_equity: %s", exc)
        return None


def _submit_order(client: Any, order: dict, account_cfg: dict) -> str:
    """Place the order via the exchange client and return a trade_id."""
    exchange = (account_cfg.get("exchange") or "bybit").lower()

    # Per-exchange live dispatch. Each broker branch mirrors the bybit
    # branch's retCode-style error handling so a non-zero retCode
    # surfaces as a RuntimeError the coordinator's diagnostic-ping
    # wrapper can format. A missing client (creds env var not set)
    # raises MissingCredentialsError — the coordinator already treats
    # that as the "account not fully configured" path and emits a ping
    # naming the missing env var. The legacy `breakout` exchange is
    # deprecated and inert.
    if exchange == "alpaca":
        # M15 Phase 2b — Alpaca Trading API (paper first; daily ETF
        # futures-replacements + SPY intraday per the Phase-0 verdict).
        # Bracket market order: SL/TP legs ride WITH the entry so
        # protection is broker-side from the first fill. Same contract
        # as the oanda branch.
        from src.units.accounts.alpaca_client import (
            AlpacaClient,
            MissingCredentialsError as _AlpacaMissingCreds,
        )
        if client is None:
            raise _AlpacaMissingCreds(
                f"alpaca live placement: account "
                f"'{account_cfg.get('account_id') or 'unknown'}' is not "
                f"fully configured (no AlpacaClient injected — "
                f"ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY likely unset)."
            )
        if not isinstance(client, AlpacaClient):
            raise TypeError(
                f"alpaca _submit_order: expected AlpacaClient, got "
                f"{type(client).__name__}"
            )
        resp = client.place({
            "symbol": order["symbol"],
            "side": order["side"],
            "qty": order["qty"],
            "sl": order.get("sl"),
            "tp": order.get("tp"),
            "strategy": order.get("strategy"),
        }) or {}
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(
            f"Alpaca rejected order for {order['symbol']}: {reason}"
        )
    if exchange == "oanda":
        # M15 Phase 2 — OANDA v20 (practice first; XAU/USD per the
        # Phase-0 verdict). Contract: missing client →
        # MissingCredentialsError naming the env vars;
        # non-zero retCode → RuntimeError the coordinator's
        # diagnostic-ping wrapper formats. SL/TP ride ON the order
        # (stopLossOnFill / takeProfitOnFill) so protection is
        # broker-side from the first fill.
        from src.units.accounts.oanda_client import (
            MissingCredentialsError as _OandaMissingCreds,
            OandaClient,
        )
        if client is None:
            raise _OandaMissingCreds(
                f"oanda live placement: account "
                f"'{account_cfg.get('account_id') or 'unknown'}' is not "
                f"fully configured (no OandaClient injected — "
                f"OANDA_API_TOKEN / OANDA_ACCOUNT_ID likely unset)."
            )
        if not isinstance(client, OandaClient):
            raise TypeError(
                f"oanda _submit_order: expected OandaClient, got "
                f"{type(client).__name__}"
            )
        resp = client.place({
            "symbol": order["symbol"],
            "side": order["side"],
            "qty": order["qty"],
            "sl": order.get("sl"),
            "tp": order.get("tp"),
            "strategy": order.get("strategy"),
        }) or {}
        ret_code = resp.get("retCode")
        if ret_code in (0, "0", None):
            order_id = (resp.get("result") or {}).get("orderId")
            return str(order_id or uuid.uuid4().hex)
        reason = str(resp.get("retMsg") or f"retCode={ret_code}")
        raise RuntimeError(
            f"OANDA rejected order for {order['symbol']}: {reason}"
        )
    if exchange == "breakout":
        # Manual browser-bridge: emit a paste-ready ticket instead of placing.
        # The canonical entry is the execute_pkg breakout branch (§6a); this
        # backstops any caller that reaches _submit_order directly.
        from src.prop.breakout_executor import emit_prop_ticket
        return emit_prop_ticket(order, account_cfg)

    # Interactive Brokers (MES futures via ib_insync). Dispatches to the
    # injected IBClient, mirroring the other broker branches' retCode-style
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
            # Align qty to the symbol's lotSizeFilter the same way price is
            # tick-aligned (BL-20260611-005). The sizer's account-level
            # precision (3dp, BTC-shaped) is not symbol-aware: 14.937 ETH on
            # ETHUSDT's 0.01 qtyStep drew "retCode 10001 Qty invalid" on
            # every eth_pullback_2h order. Floor (never round up — realised
            # risk must not exceed the sized cap) and refuse pre-flight when
            # the floored qty falls below the exchange minimum. Rule unknown
            # (live lookup + static map both miss) → submit unmodified,
            # exactly the pre-fix behaviour. The smoke-test path
            # (_submit_test_order) is intentionally NOT aligned — its qty is
            # meant to be sub-min-lot so the exchange rejects it.
            # Legalize the qty through the single seam
            # (docs/sizing-legalization-DESIGN.md Phase 2). prefer_live=True
            # keeps the live lot rule authoritative (get_lot_rule →
            # cache/live/static), with the InstrumentProfile only an added
            # fallback — a strict superset of the prior get_lot_rule-only path,
            # so the wire qty and the refusal are byte-for-byte unchanged for
            # every symbol that already resolved. Rule unknown → passthrough
            # (submit unmodified), exactly the pre-seam behaviour. The
            # smoke-test path (_submit_test_order) is a different function and
            # never reaches here, so its deliberately sub-min qty is untouched.
            from src.units.accounts.qty_legalize import legalize_qty
            _legal = legalize_qty(
                order["qty"], account_cfg=account_cfg, symbol=order["symbol"],
                client=client, prefer_live=True,
            )
            if not _legal.ok:
                raise RuntimeError(
                    f"qty {order['qty']} for {order['symbol']} is below "
                    f"the exchange lot minimum after step-alignment "
                    f"(qtyStep={_legal.step}, minOrderQty={_legal.venue_min}) — "
                    "refusing pre-flight."
                )
            if float(_legal.qty) != float(order["qty"]):
                logger.warning(
                    "_submit_order: aligning %s qty %s -> %s (qtyStep=%s)",
                    order["symbol"], order["qty"], _legal.qty, _legal.step,
                )
                # Write back so the journal row records what was sent.
                order["qty"] = float(_legal.qty)
            qty_str = _legal.qty_str
            kwargs = {
                "category": category,
                "symbol": order["symbol"],
                "side": order["side"],
                "orderType": "Market",
                "qty": qty_str,
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
                if _bybit_tpsl_mode() == "partial":
                    # Qty-scoped Partial position tpsl (Fix 2 of
                    # BL-20260720-ICTSCALP-PASTSTOP-EXITS): under the default
                    # Full mode a netted same-symbol add REPLACES the whole
                    # position's SL/TP with this order's levels — only the
                    # NEWEST trade's bracket exists, and a fire flattens every
                    # share. Partial mode scopes this order's bracket to its
                    # own qty so each journal trade keeps the protection it
                    # chose. Rollout-gated on BYBIT_TPSL_MODE (default full)
                    # until validate-partial-tpsl passes on the demo venue.
                    kwargs["tpslMode"] = "Partial"
                    kwargs["tpSize"] = qty_str
                    kwargs["slSize"] = qty_str
                    kwargs["tpOrderType"] = "Market"
                    kwargs["slOrderType"] = "Market"
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
# Intent-reduce partial-close (BL-intent-reduce-partial-close)
# ---------------------------------------------------------------------------
#
# In intent mode the multiplexer trims a netted position by issuing a
# ``reduce_only`` leg whose ``direction`` is the OPPOSITE of the current net
# (e.g. a SHORT reduce_only order to trim a net-LONG). Pre-fix the journal
# wrote that reduce as a NEW ``status='open'`` row in the opposite direction —
# which ``current_net_position_qty`` (sum of signed ``position_size`` over open
# rows) read as a phantom short, the reverse reconciler then closed as
# ``reconciler_filled``, and with the reduce row gone the DB net snapped back
# to its pre-reduce value so the intent layer re-issued the SAME reduce every
# tick (infinite churn: real reduce orders + open/close ping spam + DB↔exchange
# divergence).
#
# Correct model: a reduce is a PARTIAL CLOSE of the existing parent position,
# not a new open. ``apply_intent_reduce_partial_close`` consumes the reduce qty
# FIFO across the open parent rows (close fully-consumed rows, shrink the
# partially-consumed one) so the journal net moves to its decremented value and
# the loop stops. PnL on the closed chunks is left NULL with a
# ``pnl_source='deferred_intent_reduce'`` marker, consistent with the repo's
# deferred-PnL contract (the local-PnL sweep / closed-pnl lookup is the single
# source of realised PnL; the synchronous ``_compute_close_pnl`` helper was
# removed 2026-05-18) and the "render null, never a guessed 0" rule.


def apply_intent_reduce_partial_close(
    db: Any,
    *,
    account_id: str,
    symbol: str,
    reduce_direction: str,
    reduce_qty: float,
    fill_price: Optional[float],
    closed_at_iso: str,
) -> dict:
    """Apply an intent-mode reduce as a FIFO partial close of the parent.

    The parent side being reduced is the OPPOSITE of ``reduce_direction``
    (a SHORT reduce leg trims LONG parent rows, and vice-versa). The open
    parent rows for ``(account_id, symbol, parent_side, status='open',
    is_backtest=0)`` are consumed oldest-first (``ORDER BY id ASC``):

      * fully-consumed parent row  → closed (``status='closed'``,
        ``exit_reason='intent_reduce'``, ``pnl``/``pnl_percent`` left NULL),
      * partially-consumed parent  → ``position_size`` decremented, row stays
        open (status NOT passed, so no close ping fires).

    Returns a dict describing the allocation::

        {
            "allocations": [{"parent_id": <int>, "consumed": <float>}, ...],
            "leftover": <float>,          # reduce qty with no parent to absorb
            "no_parent_position": <bool>, # True when there were zero open parents
        }

    No new ``status='open'`` row is created here — that is the whole point of
    the fix (the phantom opposite-direction open is what created the churn).
    The single audit row is written by the caller.
    """
    # Parent = the side being reduced = opposite of the reduce leg's direction.
    rd = (reduce_direction or "").strip().lower()
    parent_side = {"short": "long", "long": "short",
                   "sell": "long", "buy": "short"}.get(rd)
    if parent_side is None:
        # Defensive: an unexpected direction value means we can't safely
        # identify the parent rows. Surface it so the caller falls back.
        raise ValueError(
            f"apply_intent_reduce_partial_close: unmappable reduce_direction="
            f"{reduce_direction!r}"
        )

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, position_size FROM trades "
            "WHERE account_id = ? AND symbol = ? AND direction = ? "
            "AND status = 'open' AND COALESCE(is_backtest, 0) = 0 "
            "ORDER BY id ASC",
            (account_id, symbol, parent_side),
        ).fetchall()
    finally:
        conn.close()

    remaining = float(reduce_qty or 0.0)
    eps = 1e-9
    allocations: list[dict] = []
    no_parent_position = (len(rows) == 0)

    for row in rows:
        if remaining <= eps:
            break
        parent_id = int(row[0])
        parent_qty = float(row[1] or 0.0)
        if parent_qty <= eps:
            continue
        consumed = min(remaining, parent_qty)
        if consumed >= parent_qty - eps:
            # Fully consumed → close this parent chunk. PnL deferred (NULL).
            db.update_trade(parent_id, {
                "status": "closed",
                "exit_price": float(fill_price) if fill_price is not None else None,
                "exit_reason": "intent_reduce",
                "closed_at": closed_at_iso,
                "pnl": None,
                "pnl_percent": None,
            })
        else:
            # Partially consumed → shrink the row, leave it open. Status is
            # NOT passed so update_trade fires no close ping.
            db.update_trade(parent_id, {
                "position_size": parent_qty - consumed,
            })
        allocations.append({"parent_id": parent_id, "consumed": consumed})
        remaining -= consumed

    return {
        "allocations": allocations,
        "leftover": max(remaining, 0.0),
        "no_parent_position": no_parent_position,
    }


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
    extra_notes: Optional[dict] = None,
    sl_order_id: Optional[str] = None,
    tp_order_id: Optional[str] = None,
) -> bool:
    """Insert a row into ``trade_journal.db::trades`` for an executor event.

    Three call patterns:

    - **Successful submission** (default): ``status='open'``, ``trade_id``
      is the exchange order id. Close path (S-030 monitor loop) updates
      via ``Database.update_trade``.
    - **Risk-manager rejection**: ``status='rejected'``,
      ``reason`` ∈ {``account_mode_dry_run``, ``DAILY_LOSS_CAP``,
      ``INTRADAY_DRAWDOWN``}. ``trade_id`` is
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
        from datetime import datetime, timezone
        from src.utils.json_notes import dump_capped
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
        if extra_notes:
            # Caller-supplied structured detail (e.g. the options-expression
            # leg/strike/defined-risk block) merged into the row's notes so
            # /api/bot/positions can surface it without a live broker call.
            try:
                notes_payload.update(extra_notes)
            except Exception:  # noqa: BLE001
                pass
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
        # Paper-vs-real-money CATEGORY from account_class (the single source
        # of truth for the paper/real reporting axis). Coerce an invalid /
        # missing value to "real_money" so a config typo never silently
        # mis-stamps a row. ``is_demo`` is kept in sync (= paper) for
        # back-compat with consumers that haven't moved to account_class.
        # NOTE: this is distinct from account_cfg["demo"] (Bybit-transport
        # endpoint flag) — a real-money Bybit account never carries demo,
        # and bybit_1 is account_class:paper AND demo:true.
        account_class = str(account_cfg.get("account_class") or "real_money").strip().lower()
        if account_class not in ("paper", "real_money"):
            account_class = "real_money"
        is_paper_account = (account_class == "paper")
        resolved_account_id = str(
            account_cfg.get("account_id") or account_cfg.get("id") or "unknown"
        )
        reduce_qty = float(order.get("qty") or 0.0)
        # Intent-mode reduce of a REAL placed order (status would otherwise be
        # 'open'): model it as a FIFO PARTIAL CLOSE of the parent position
        # rather than a new opposite-direction open row (the phantom-short
        # churn bug — see apply_intent_reduce_partial_close above). Rejections /
        # dry-runs pass a non-'open' status and keep the legacy insert path.
        # The whole partial-close block is best-effort: ANY failure logs a
        # warning and falls through to the legacy insert so the order path can
        # never crash.
        if intent_reduce and status == "open":
            try:
                reduce_result = apply_intent_reduce_partial_close(
                    db,
                    account_id=resolved_account_id,
                    symbol=pkg.symbol,
                    reduce_direction=pkg.direction,
                    reduce_qty=reduce_qty,
                    fill_price=float(pkg.entry) if pkg.entry is not None else None,
                    closed_at_iso=datetime.now(timezone.utc).isoformat(),
                )
                # One audit row recording the reduce. status='closed' so
                # insert_trade fires NO trade_opened ping (it only fires for
                # status=='open'); pnl/pnl_percent left NULL (deferred).
                audit_notes = dict(notes_payload)
                audit_notes["intent_reduce_allocations"] = reduce_result["allocations"]
                if reduce_result["leftover"] > 1e-9:
                    audit_notes["over_reduce_leftover"] = reduce_result["leftover"]
                if reduce_result["no_parent_position"]:
                    audit_notes["no_parent_position"] = True
                audit_notes["pnl_source"] = "deferred_intent_reduce"
                now_iso = datetime.now(timezone.utc).isoformat()
                db.insert_trade({
                    "timestamp": now_iso,
                    "symbol": pkg.symbol,
                    "direction": pkg.direction,  # the reduce side
                    "entry_price": float(pkg.entry),
                    "stop_loss": float(pkg.sl),
                    "take_profit_1": float(pkg.tp),
                    "position_size": reduce_qty,
                    "setup_type": "intent_reduce",
                    "entry_reason": entry_reason[:500],
                    "exit_reason": "intent_reduce_executed",
                    "status": "closed",
                    "closed_at": now_iso,
                    "pnl": None,
                    "pnl_percent": None,
                    "is_backtest": 0,
                    "account_class": account_class,
                    "is_demo": int(is_paper_account),
                    "strategy_name": pkg.strategy,
                    "account_id": resolved_account_id,
                    "notes": dump_capped(audit_notes, 2000),
                    "order_package_id": pkg_id,
                })
                # Reduce handled as a partial close — done. The package
                # linked_trade_id slot is owned by the primary entry, not a
                # reduce leg (mirrors the is_primary_entry guard below), so
                # there is nothing else to wire here.
                return True
            except Exception as exc:  # noqa: BLE001 — never crash the order path
                logger.warning(
                    "execute_pkg: intent-reduce partial-close failed "
                    "(account=%s symbol=%s reduce_dir=%s qty=%s); falling back "
                    "to legacy reduce-row insert: %s",
                    resolved_account_id, pkg.symbol, pkg.direction,
                    reduce_qty, exc,
                )
                # Fall through to the legacy insert below (old behaviour).
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
            "account_class": account_class,
            "is_demo": int(is_paper_account),
            "strategy_name": pkg.strategy,
            "account_id": resolved_account_id,
            # Options rows carry a multi-leg structure block, so they need a
            # larger notes budget than the 500-char default (a truncated JSON
            # blob would be unparseable on the read side).
            "notes": dump_capped(notes_payload, 2000 if extra_notes else 500),
            "order_package_id": pkg_id,
            # Slice B / B0 — the broker's entry orderId as a first-class,
            # indexable join key (mirrors notes.trade_id) so the broker-truth
            # cost sweep can tie this trade to its exchange_fills rows exactly.
            # Observability-only; never read on the order path. Synthetic ids on
            # dry/rejection rows simply won't match any real fill.
            "broker_order_id": trade_id,
            # BL-20260721-BYBIT2-XRP-TPSL-LEGCAP — this trade's own Bybit
            # Partial-tpsl leg id(s), captured at entry (execute_pkg's
            # before/after snapshot diff around _submit_order). NULL for
            # non-Bybit / non-partial-mode / reduce-only / ambiguous-diff
            # rows; those fall back to modify_open_order's legacy add-a-leg
            # behaviour. This IS read on the order path — modify_open_order
            # targets it with Bybit's amend_order instead of re-adding.
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
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
            and not is_paper_account
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
                    demo=bool(account_cfg.get("demo")),
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
    is_dry: bool = False,
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

    ``is_dry`` reflects whether the dispatch was a genuine no-live-order
    decision (account ``mode: dry_run`` or a per-strategy
    ``execution: shadow`` gate) — it is written to ``notes.is_dry`` so the
    field agrees with a ``dry_run_no_order_placed`` reason instead of the
    old hardcoded ``False`` that contradicted it
    (BL-20260707-MGCTREND-REASON-MISMATCH). Defaults ``False``; only the
    dry-branch caller passes ``True``. This does NOT touch the
    ``is_demo`` / ``account_class`` paper-vs-real column, which is derived
    separately from ``account_cfg`` inside ``_log_trade_to_journal``.

    Wraps the underlying write in a defensive try/except so a
    journal-write failure during failure-handling can never escalate
    to a stack unwind.
    """
    try:
        order = {"qty": float(sized_qty or 0.0), "symbol": pkg.symbol}
        return _log_trade_to_journal(
            pkg, account_cfg, order,
            trade_id=None, is_dry=is_dry,
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


def _bybit_tpsl_mode() -> str:
    """Resolve ``BYBIT_TPSL_MODE`` ∈ {``full`` (default), ``partial``}.

    Rollout gate (``*_MODE`` pattern, Tier-3 to flip on the live VM) for
    qty-scoped **Partial** position tpsl — Fix 2 of
    BL-20260720-ICTSCALP-PASTSTOP-EXITS. Under Bybit one-way netting the
    default Full mode gives the whole position ONE bracket (the newest
    order's), so older journal trades ride geometry they never chose and a
    single fire flattens every share. ``partial`` makes each order carry its
    own qty-scoped SL/TP. Default ``full`` preserves current behaviour;
    flip only after the ``validate-partial-tpsl`` operator action passes on
    the demo account against the real venue. Unknown values resolve to
    ``full`` (never strand an order on a typo).
    """
    v = str(os.environ.get("BYBIT_TPSL_MODE") or "full").strip().lower()
    return v if v in {"full", "partial"} else "full"


_SL_LEG_TYPES = {"stoploss", "partialstoploss"}
_TP_LEG_TYPES = {"takeprofit", "partialtakeprofit"}


def _partial_tpsl_leg_ids(exchange_client: Any, category: str, symbol: str) -> set:
    """Best-effort snapshot of live Bybit conditional-order ids for *symbol*.

    Used to bracket an entry placed under ``BYBIT_TPSL_MODE=partial`` (a
    before/after diff, since Bybit's inline-SL/TP place response never
    returns the leg's own ``orderId`` — see ``_classify_new_partial_tpsl_legs``).
    Returns an EMPTY set (never ``None``) on any read failure, so a failed
    snapshot degrades to "no new legs detected" rather than raising into
    the order path.
    """
    try:
        resp = exchange_client.get_open_orders(
            category=category, symbol=symbol, orderFilter="StopOrder",
        )
        rows = ((resp or {}).get("result") or {}).get("list") or []
        return {str(o.get("orderId")) for o in rows if o.get("orderId")}
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_partial_tpsl_leg_ids: snapshot failed for %s: %s", symbol, exc,
        )
        return set()


def _classify_new_partial_tpsl_legs(
    exchange_client: Any, category: str, symbol: str, pre_ids: set,
):
    """Diff a fresh leg snapshot against *pre_ids* to find what this entry created.

    BL-20260721-BYBIT2-XRP-TPSL-LEGCAP: under Partial tpsl mode, Bybit
    creates the qty-scoped SL/TP legs as a side effect of the entry order —
    the place-order response only returns the PARENT order's id, never the
    legs'. This is the capture step that makes the id trackable: called
    right after entry with the pre-entry leg-id set, it returns whichever
    leg(s) are genuinely NEW.

    Returns ``(sl_order_id, tp_order_id)``, each ``None`` unless EXACTLY one
    new leg of that type appeared — an ambiguous diff (0, meaning the read
    or the leg creation failed/lagged, or >1, e.g. a concurrent same-symbol
    entry racing this one) leaves that slot ``None`` rather than guessing,
    so the caller's fallback (today's add-a-leg ``set_trading_stop``
    behaviour in ``modify_open_order``) stays the safety net instead of
    risking a mis-attributed leg id on a LIVE stop.
    """
    try:
        resp = exchange_client.get_open_orders(
            category=category, symbol=symbol, orderFilter="StopOrder",
        )
        rows = ((resp or {}).get("result") or {}).get("list") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_classify_new_partial_tpsl_legs: post-read failed for %s: %s",
            symbol, exc,
        )
        return None, None

    new_sl, new_tp = [], []
    for o in rows:
        oid = str(o.get("orderId") or "")
        if not oid or oid in pre_ids:
            continue
        kind = str(o.get("stopOrderType") or "").lower()
        if kind in _SL_LEG_TYPES:
            new_sl.append(oid)
        elif kind in _TP_LEG_TYPES:
            new_tp.append(oid)
    if len(new_sl) > 1 or len(new_tp) > 1:
        logger.warning(
            "_classify_new_partial_tpsl_legs: ambiguous leg diff for %s "
            "(new_sl=%d new_tp=%d) — leaving id(s) untracked, this trade "
            "falls back to legacy add-a-leg amend behaviour",
            symbol, len(new_sl), len(new_tp),
        )
    sl_id = new_sl[0] if len(new_sl) == 1 else None
    tp_id = new_tp[0] if len(new_tp) == 1 else None
    return sl_id, tp_id


def _amend_partial_tpsl_leg(
    exchange_client: Any, category: str, symbol: str, order_id: str,
    *, trigger_price: float,
) -> dict:
    """Amend one specific Bybit Partial-tpsl leg's trigger price IN PLACE.

    The structural fix's core call (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP):
    once a leg's own ``orderId`` is known (see
    ``_classify_new_partial_tpsl_legs``), this is a true Bybit
    ``/v5/order/amend`` — NOT ``set_trading_stop``, which Bybit's own V5
    docs describe as ADD-only under Partial mode. No new leg is created; the
    symbol's leg count never grows from a modify.
    """
    try:
        resp = exchange_client.amend_order(
            category=category, symbol=symbol, orderId=order_id,
            triggerPrice=str(trigger_price),
        )
        ret_code = (resp or {}).get("retCode")
        ok = ret_code in (0, "0", None)
        if not ok:
            logger.warning(
                "_amend_partial_tpsl_leg: amend failed for order_id=%s "
                "symbol=%s: retCode=%s retMsg=%s",
                order_id, symbol, ret_code, (resp or {}).get("retMsg"),
            )
        return {"ok": ok, "exchange_response": resp,
                "error": None if ok else str(
                    (resp or {}).get("retMsg") or f"retCode={ret_code}")}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_amend_partial_tpsl_leg: raised for order_id=%s symbol=%s: %s",
            order_id, symbol, exc,
        )
        return {"ok": False, "exchange_response": None,
                "error": f"{type(exc).__name__}: {exc}"}


def modify_open_order(
    exchange_client: Any,
    account_cfg: dict,
    *,
    symbol: str,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    cur_sl: Optional[float] = None,
    cur_tp: Optional[float] = None,
    sl_order_id: Optional[str] = None,
    tp_order_id: Optional[str] = None,
) -> dict:
    """Modify SL/TP on an open position on the account's exchange.

    Wired integrations:
      * **bybit** — Unified Trading ``set_trading_stop(category=<resolved>,
        symbol=…, stopLoss=…, takeProfit=…)`` — only valid for the derivatives
        categories (``linear``/``inverse``). Spot accounts return ``ok=False``
        and rely on the monitor's market-close exit. In-place modify: only the
        ``sl`` / ``tp`` leg(s) actually passed are set (byte-unchanged from v1
        WHEN the caller passes no tracked leg id — see next paragraph).
        **Under ``BYBIT_TPSL_MODE=partial`` with a tracked leg id**
        (``sl_order_id`` / ``tp_order_id``, from ``trades.sl_order_id`` /
        ``.tp_order_id`` — BL-20260721-BYBIT2-XRP-TPSL-LEGCAP): that leg is
        amended in place via ``amend_order`` instead of folded into
        ``set_trading_stop``, so no new leg is added. A leg with no tracked
        id (pre-migration trade, ambiguous entry-time capture, or Full mode)
        still goes through the legacy ``set_trading_stop`` path — unchanged
        behaviour, the safety net.
      * **interactive_brokers / ib** — :meth:`IBClient.modify_protective`
        (cancel the resting OCA legs + re-arm a fresh GTC OCA pair at the
        MERGED levels). IB has no in-place modify, so it needs both effective
        levels: the changed leg (``sl`` / ``tp``) merged with the current value
        of the unchanged one (``cur_sl`` / ``cur_tp``, from the order package)
        so re-arming never drops a leg. Needs ``side`` + ``qty`` (the
        position's side + whole-contract size).
      * **alpaca** — :meth:`AlpacaClient.modify_protective` (PATCH the resting
        bracket leg's ``stop_price`` / ``limit_price`` for whichever of
        ``sl`` / ``tp`` changed — leg-independent, so no ``cur_*`` merge).

    The S2 (BL-20260616-LTMGMT-MODIFY) ``side`` / ``qty`` / ``cur_sl`` /
    ``cur_tp`` kwargs are only consumed by the IB/Alpaca branches; the Bybit
    branch's ``set_trading_stop`` fallback ignores them.

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
            partial_mode = _bybit_tpsl_mode() == "partial"

            # BL-20260721-BYBIT2-XRP-TPSL-LEGCAP structural fix: amend any
            # leg that has a tracked orderId in place, THEN fall through to
            # the legacy set_trading_stop path only for whichever leg (if
            # any) still lacks one. sl/tp are cleared once handled here so
            # the block below never re-adds an already-amended leg.
            amend_results: dict = {}
            if partial_mode:
                if sl is not None and sl_order_id:
                    amend_results["sl"] = _amend_partial_tpsl_leg(
                        exchange_client, category, symbol, sl_order_id,
                        trigger_price=quantize_price(sl, tick),
                    )
                    sl = None
                if tp is not None and tp_order_id:
                    amend_results["tp"] = _amend_partial_tpsl_leg(
                        exchange_client, category, symbol, tp_order_id,
                        trigger_price=quantize_price(tp, tick),
                    )
                    tp = None

            if amend_results and sl is None and tp is None:
                # Every requested leg had a tracked id and was amended above
                # — no set_trading_stop call needed at all.
                failed = [k for k, r in amend_results.items() if not r.get("ok")]
                if failed:
                    return {"ok": False, "exchange_response": amend_results,
                            "error": f"amend_order failed for leg(s): {failed}"}
                logger.info(
                    "modify_open_order: account=%s symbol=%s → amended "
                    "tracked leg(s) %s in place (no add-a-leg call)",
                    account_cfg.get("account_id"), symbol,
                    sorted(amend_results),
                )
                return {"ok": True, "exchange_response": amend_results,
                        "error": None}

            kwargs = {"category": category, "symbol": symbol}
            if sl is not None:
                kwargs["stopLoss"] = quantize_price(sl, tick)
            if tp is not None:
                kwargs["takeProfit"] = quantize_price(tp, tick)
            if partial_mode:
                # Under Partial tpsl the amend must be qty-scoped too —
                # a bare set_trading_stop would target the position-level
                # (Full) slot instead of this trade's own bracket. Needs
                # the caller's qty (the monitor passes the trade's size);
                # without it we fall through to the plain call and log,
                # rather than silently amending the wrong scope. This is
                # the LEGACY add-a-leg path — reached only for a leg with
                # no tracked orderId (see amend_results above).
                if qty is not None and float(qty) > 0:
                    kwargs["tpslMode"] = "Partial"
                    if sl is not None:
                        kwargs["slSize"] = str(qty)
                    if tp is not None:
                        kwargs["tpSize"] = str(qty)
                else:
                    logger.warning(
                        "modify_open_order: BYBIT_TPSL_MODE=partial but no "
                        "qty passed for %s %s — falling back to a position-"
                        "level (Full) amend; caller should forward the "
                        "trade's qty.",
                        account_cfg.get("account_id"), symbol,
                    )
            resp = exchange_client.set_trading_stop(**kwargs)
            ret_code = (resp or {}).get("retCode")
            ok = ret_code in (0, "0", None)
            if amend_results:
                # Mixed outcome: some leg(s) amended above, this leg went
                # through the legacy add-a-leg path. Surface both so the
                # caller's audit trail shows exactly what happened to each.
                combined_response = {**amend_results, "set_trading_stop": resp}
                failed = [k for k, r in amend_results.items() if not r.get("ok")]
                if not ok:
                    failed.append("set_trading_stop")
                if failed:
                    return {"ok": False, "exchange_response": combined_response,
                            "error": f"failed leg(s): {failed}"}
                logger.info(
                    "modify_open_order: account=%s symbol=%s sl=%s tp=%s → "
                    "mixed amend+add-a-leg OK",
                    account_cfg.get("account_id"), symbol, sl, tp,
                )
                return {"ok": True, "exchange_response": combined_response,
                        "error": None}
            if ok:
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

    if exchange in ("interactive_brokers", "ib"):
        # IB has no in-place SL/TP modify — re-arm the GTC OCA protective
        # bracket at the MERGED levels (the changed leg + the current value of
        # the unchanged one so neither stop nor target is dropped).
        eff_sl = sl if sl is not None else cur_sl
        eff_tp = tp if tp is not None else cur_tp
        has_sl = eff_sl is not None and float(eff_sl) > 0
        has_tp = eff_tp is not None and float(eff_tp) > 0
        if not has_sl and not has_tp:
            return {"ok": False, "exchange_response": None,
                    "error": "no effective sl/tp to re-arm (need the changed "
                             "leg or the current value of the unchanged one)"}
        try:
            from src.units.accounts.ib_client import IBClient
            if not isinstance(exchange_client, IBClient):
                return {"ok": False, "exchange_response": None,
                        "error": (f"IB modify: expected IBClient, got "
                                  f"{type(exchange_client).__name__}")}
            resp = exchange_client.modify_protective({
                "symbol": symbol,
                "direction": side,
                "qty": qty,
                "sl": eff_sl if has_sl else None,
                "tp": eff_tp if has_tp else None,
            }) or {}
            ret_code = resp.get("retCode")
            if ret_code in (0, "0", None):
                logger.info(
                    "modify_open_order: account=%s symbol=%s sl=%s tp=%s "
                    "→ IB re-arm OK",
                    account_cfg.get("account_id"), symbol, eff_sl, eff_tp,
                )
                return {"ok": True, "exchange_response": resp, "error": None}
            err = str(resp.get("retMsg") or f"retCode={ret_code}")
            return {"ok": False, "exchange_response": resp, "error": err}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "modify_open_order: IB raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    if exchange == "alpaca":
        # Alpaca bracket legs are independent working orders — PATCH the
        # resting stop/limit leg for whichever of sl/tp the verdict changed.
        # No cur_* merge: an un-touched leg stays exactly as it was.
        try:
            from src.units.accounts.alpaca_client import AlpacaClient
            if not isinstance(exchange_client, AlpacaClient):
                return {"ok": False, "exchange_response": None,
                        "error": (f"alpaca modify: expected AlpacaClient, got "
                                  f"{type(exchange_client).__name__}")}
            resp = exchange_client.modify_protective(symbol, sl=sl, tp=tp) or {}
            ret_code = resp.get("retCode")
            if ret_code in (0, "0", None):
                logger.info(
                    "modify_open_order: account=%s symbol=%s sl=%s tp=%s "
                    "→ alpaca leg replace OK",
                    account_cfg.get("account_id"), symbol, sl, tp,
                )
                return {"ok": True, "exchange_response": resp, "error": None}
            err = str(resp.get("retMsg") or f"retCode={ret_code}")
            return {"ok": False, "exchange_response": resp, "error": err}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "modify_open_order: alpaca raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": False, "exchange_response": None,
            "error": (f"unsupported exchange {exchange!r} "
                      "(wired: bybit, interactive_brokers, alpaca)")}


def close_open_position(
    exchange_client: Any,
    account_cfg: dict,
    *,
    symbol: str,
    side: str,
    qty: float,
    sl_order_id: Optional[str] = None,
    tp_order_id: Optional[str] = None,
) -> dict:
    """Place a reduce-only market order to flatten an open position.

    *side* is the side of the original entry (``"long"`` or ``"short"``};
    the close order is the opposite side. *qty* is the position size
    to close (typically the size of the original entry).

    ``sl_order_id`` / ``tp_order_id`` (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP,
    from ``trades``) are the closing trade's own tracked Bybit Partial-tpsl
    leg(s), if any — the Bybit branch best-effort cancels them AFTER a
    confirmed close so a stale leg never lingers on a now-flat qty chunk. A
    cancel failure (already-cancelled, already-triggered) is logged, not
    surfaced as a close failure — the position IS flat either way. Ignored
    by every other exchange branch.

    Wired integrations (P3 of the live-trade management contract —
    docs/audits/live-trade-management-contract-2026-06-16.md):
      * **bybit** — reduce-only market ``place_order`` (unchanged from v1).
      * **interactive_brokers / ib** — :meth:`IBClient.close` (cancel the
        resting protective bracket/OCA legs + opposing reduce market order
        sized to the live position). IB futures have no reduceOnly flag.
      * **alpaca** — :meth:`AlpacaClient.close` (idempotent native flatten,
        ``DELETE /v2/positions/{symbol}``; a 404 = already-flat = ok).
      * **oanda** — :meth:`OandaClient.close` (idempotent v20 closeout,
        ``PUT /v3/accounts/{id}/positions/{instrument}/close``; no open
        position = ok). S2 (BL-20260616-LTMGMT-OANDA), before go-live.

    Returns a result dict. Best-effort everywhere — a gateway-down /
    API error returns ``{"ok": False, "error": ...}`` so the monitor
    leaves the DB row open and retries next tick (never falsely marks
    closed, never raises).
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
                # BL-20260721-BYBIT2-XRP-TPSL-LEGCAP: this trade's tracked
                # Partial-tpsl leg(s) no longer protect anything once the
                # position is flat — cancel them so they can't linger and
                # count toward the symbol's 20-leg cap. Best-effort; a
                # cancel failure never turns a successful close into one.
                for leg_id in (sl_order_id, tp_order_id):
                    if not leg_id:
                        continue
                    try:
                        cancel_resp = exchange_client.cancel_order(
                            category=category, symbol=symbol, orderId=leg_id,
                        )
                        cancel_code = (cancel_resp or {}).get("retCode")
                        if cancel_code not in (0, "0", None):
                            logger.info(
                                "close_open_position: post-close leg cancel "
                                "for order_id=%s symbol=%s → retCode=%s "
                                "(likely already cancelled/triggered — fine)",
                                leg_id, symbol, cancel_code,
                            )
                    except Exception as leg_exc:  # noqa: BLE001
                        logger.info(
                            "close_open_position: post-close leg cancel "
                            "raised for order_id=%s symbol=%s: %s",
                            leg_id, symbol, leg_exc,
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

    if exchange in ("interactive_brokers", "ib"):
        # IBClient.close: cancel resting protective legs + opposing reduce
        # market order sized to the live position. Bounded + best-effort —
        # never raises, returns its own retCode envelope.
        try:
            from src.units.accounts.ib_client import IBClient
            if not isinstance(exchange_client, IBClient):
                return {"ok": False, "exchange_response": None,
                        "exchange_order_id": None,
                        "error": (f"IB close: expected IBClient, got "
                                  f"{type(exchange_client).__name__}")}
            resp = exchange_client.close(symbol, direction, qty) or {}
            ret_code = resp.get("retCode")
            if ret_code in (0, "0", None):
                order_id = (resp.get("result") or {}).get("orderId")
                logger.info(
                    "close_open_position: account=%s symbol=%s side=%s qty=%s "
                    "→ IB orderId=%s",
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
                "close_open_position: IB raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "exchange_order_id": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    if exchange == "alpaca":
        # AlpacaClient.close: idempotent native flatten (DELETE
        # /v2/positions/{symbol}); a 404 (no open position) maps to
        # retCode 0 in the client. Whole-position flatten only — Alpaca's
        # close-position endpoint closes the entire symbol position, so the
        # qty argument is informational here (partial-close is not wired).
        try:
            from src.units.accounts.alpaca_client import AlpacaClient
            if not isinstance(exchange_client, AlpacaClient):
                return {"ok": False, "exchange_response": None,
                        "exchange_order_id": None,
                        "error": (f"alpaca close: expected AlpacaClient, got "
                                  f"{type(exchange_client).__name__}")}
            resp = exchange_client.close(symbol) or {}
            ret_code = resp.get("retCode")
            if ret_code in (0, "0", None):
                order_id = (resp.get("result") or {}).get("orderId")
                logger.info(
                    "close_open_position: account=%s symbol=%s side=%s qty=%s "
                    "→ alpaca flatten (orderId=%s)",
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
                "close_open_position: alpaca raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "exchange_order_id": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    if exchange == "oanda":
        # OandaClient.close: idempotent v20 closeout (PUT
        # /v3/accounts/{id}/positions/{instrument}/close with longUnits/
        # shortUnits=ALL for the open leg(s)); no open position → retCode 0.
        # Whole-position closeout only — the v20 close-position endpoint flattens
        # the named instrument leg, so qty is informational (partial-close not
        # wired). Wired in S2 (BL-20260616-LTMGMT-OANDA) before oanda_practice
        # leaves dry_run.
        try:
            from src.units.accounts.oanda_client import OandaClient
            if not isinstance(exchange_client, OandaClient):
                return {"ok": False, "exchange_response": None,
                        "exchange_order_id": None,
                        "error": (f"oanda close: expected OandaClient, got "
                                  f"{type(exchange_client).__name__}")}
            resp = exchange_client.close(symbol) or {}
            ret_code = resp.get("retCode")
            if ret_code in (0, "0", None):
                order_id = (resp.get("result") or {}).get("orderId")
                logger.info(
                    "close_open_position: account=%s symbol=%s side=%s qty=%s "
                    "→ oanda closeout (orderId=%s)",
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
                "close_open_position: oanda raised for account=%s symbol=%s: %s",
                account_cfg.get("account_id"), symbol, exc,
            )
            return {"ok": False, "exchange_response": None,
                    "exchange_order_id": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": False, "exchange_response": None, "exchange_order_id": None,
            "error": (f"unsupported exchange {exchange!r} "
                      "(wired: bybit, interactive_brokers, alpaca, oanda)")}
