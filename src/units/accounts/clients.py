"""Per-account exchange client construction (canonical owner).

This module is the single chokepoint for turning an `account` dict
(loaded from `config/accounts.yaml`) into a live exchange client.
Every other layer — Telegram bot, Coordinator, smoke tests — must
call into here instead of reading exchange env vars directly.

Why this lives in the accounts unit:
    The accounts unit owns "what credentials belong to which account".
    Other layers should treat exchange clients as opaque handles
    handed out by the accounts unit. Routing client construction
    through the bot's data_loaders historically meant a Telegram
    handler bug (e.g. forgetting to pass the account dict through)
    silently fell back to the legacy single-key path and pointed
    every account at one wallet — the BUG-030 root cause.

Resolution order (matches the previous data_loaders implementation):

  1. ``account["api_key_env"]`` — env-var name carrying the API key
     (the accounts.yaml contract). Looks up ``os.environ[<api_key_env>]``
     and the matching ``..._SECRET`` (or ``api_secret_env`` when set
     explicitly).
  2. ``account["env_path"]`` — read the canonical key/secret pair from
     a `.env` file. Legacy single-account path; still used by env-
     discovered accounts.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
from src.utils.paths import repo_root as _repo_root  # noqa: E402
REPO_ROOT = _repo_root()


def _read_env_file(env_path: str) -> Dict[str, str]:
    if not env_path or not os.path.exists(env_path):
        return {}
    try:
        from dotenv import dotenv_values  # type: ignore
        values = dotenv_values(env_path)
        return {k: v for k, v in values.items() if v is not None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_read_env_file(%s): %s", env_path, exc)
        return {}


def _derive_secret_env(api_key_env: str, account: Dict[str, Any]) -> str:
    return (
        account.get("api_secret_env")
        or api_key_env.replace("_API_KEY", "_API_SECRET")
    )


def resolve_credentials(account: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Return ``{"api_key": ..., "api_secret": ...}`` or None when missing.

    Pure-data helper: does not import any exchange client library, so it
    is safe to call from environments where pybit / ccxt are not installed
    (tests, lint passes).
    """
    if not isinstance(account, dict):
        return None
    api_key_env = account.get("api_key_env")
    if api_key_env:
        secret_env = _derive_secret_env(api_key_env, account)
        api_key = os.environ.get(api_key_env)
        api_secret = os.environ.get(secret_env)
        if api_key and api_secret:
            return {"api_key": api_key, "api_secret": api_secret}
        return None
    env = _read_env_file(account.get("env_path") or "")
    exchange = str(account.get("exchange", "")).lower()
    if exchange == "binance":
        key, secret = env.get("BINANCE_API_KEY"), env.get("BINANCE_API_SECRET")
    else:
        key, secret = env.get("BYBIT_API_KEY"), env.get("BYBIT_API_SECRET")
    if key and secret:
        return {"api_key": key, "api_secret": secret}
    return None


def bybit_client_for(account: Dict[str, Any]):
    """Return a Bybit HTTP client for *account*, or ``None`` if creds missing."""
    creds = resolve_credentials(account)
    if not creds:
        return None
    from pybit.unified_trading import HTTP  # type: ignore
    demo = str(account.get("demo", "false")).strip().lower() in ("true", "1", "yes")
    if demo:
        # Bybit demo trading (https://api-demo.bybit.com). pybit >= 5.7 supports
        # demo=True natively; set-leverage and order calls route to the demo endpoint.
        return HTTP(demo=True, api_key=creds["api_key"], api_secret=creds["api_secret"])
    testnet = str(os.environ.get("BYBIT_TESTNET", "false")).strip().lower() == "true"
    return HTTP(testnet=testnet, api_key=creds["api_key"], api_secret=creds["api_secret"])


def binance_conn_for(account: Dict[str, Any]):
    """Return a Binance connector for *account*, or ``None`` if creds missing."""
    creds = resolve_credentials(account)
    if not creds:
        return None
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from exchange.binance_connector import BinanceConnector  # type: ignore
    testnet = str(os.environ.get("BINANCE_TESTNET", "false")).strip().lower() == "true"
    return BinanceConnector(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        testnet=testnet,
    )


def velotrade_client_for(account: Dict[str, Any]):
    """Return a DXtrade client for *account*, or ``None`` if creds missing.

    Mirrors :func:`bybit_client_for` and :func:`binance_conn_for` so the
    coordinator's ``multi_account_execute`` client-construction switch
    has a uniform shape: factory returns ``None`` → loader/coordinator
    treats the account as not-configured and emits a diagnostic ping.

    The base URL (sandbox vs prod) is read from
    ``VELOTRADE_BASE_URL`` (account-level override:
    ``account['base_url']``) so the operator can flip between
    environments without touching code. When neither is set, the
    client is constructed with ``base_url=None`` and the eventual SDK
    call site picks the canonical default from the DXtrade contract.

    Returns
    -------
    DXtradeClient | None
        Constructed client when both ``api_key_env`` (e.g.
        ``VELOTRADE_API_KEY_1``) and the matching ``..._SECRET`` env
        var are populated; ``None`` otherwise (account is "not
        fully configured" — downstream code refuses live use and
        pings the operator).
    """
    creds = resolve_credentials(account)
    if not creds:
        return None
    from src.units.accounts.dxtrade_client import DXtradeClient
    base_url = (
        account.get("base_url")
        or os.environ.get("VELOTRADE_BASE_URL")
        or None
    )
    return DXtradeClient(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        base_url=base_url,
    )


# ---------------------------------------------------------------------------
# Per-account exchange-state reads
# ---------------------------------------------------------------------------
#
# These helpers belong to the accounts unit per CLAUDE.md
# § "Architecture rules" § 3 — reading exchange state for a specific
# account is the accounts unit's responsibility, not the UI unit's. They
# previously lived under ``src/units/ui/data_loaders.py`` and were
# called *across* the unit boundary by every consumer (Telegram bot,
# coordinator, monitor loop). Lifting them here lets the upcoming
# BUG-042 monitor-loop reconciler depend on the accounts unit directly,
# closes the wrong-direction import (UI → accounts), and gives every
# caller a single canonical entry point.
#
# Behaviour-preserving lift — the original implementation under
# ``src/units/ui/data_loaders.py::account_open_positions`` is kept as a
# thin delegate so legacy callers continue to work.


def _f(x: Any, default: float = 0.0) -> float:
    """Best-effort coerce *x* to ``float``. Mirrors the private helper
    that previously lived in ``data_loaders.py`` so the lifted
    ``account_open_positions`` keeps the same numeric semantics.
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _bybit_order_status_lookup(
    client: Any,
    *,
    category: str,
    order_id: str,
) -> Optional[Dict[str, Any]]:
    """Look up a single Bybit order by ``orderId``.

    Tries ``get_open_orders`` first (live / partially filled orders are
    only there) and falls back to ``get_order_history`` for filled,
    cancelled, or rejected ones. Returns the raw inner record dict on
    success, ``None`` when the orderId isn't present in either endpoint
    (genuinely unknown — Bybit denies any record).

    Wrapped at the call site by :func:`account_order_status` which
    converts the raw record into the normalised ``{order_id, status,
    filled_qty, avg_price, exec_time}`` shape and turns API failures
    into ``None`` so callers can distinguish "not found" from "couldn't
    read".
    """
    open_resp = client.get_open_orders(category=category, orderId=order_id) or {}
    open_list = ((open_resp.get("result") or {}).get("list") or [])
    for rec in open_list:
        if str(rec.get("orderId") or "") == str(order_id):
            return rec
    hist_resp = client.get_order_history(category=category, orderId=order_id) or {}
    hist_list = ((hist_resp.get("result") or {}).get("list") or [])
    for rec in hist_list:
        if str(rec.get("orderId") or "") == str(order_id):
            return rec
    return None


def _bybit_closed_pnl_lookup(
    client: Any,
    *,
    category: str,
    symbol: str,
    side: str,
    start_ts_ms: int,
    end_ts_ms: int,
    qty_target: Optional[float] = None,
    qty_tolerance: float = 0.05,
    entry_price_target: Optional[float] = None,
    entry_price_tolerance: float = 0.001,
    opened_at_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Find the Bybit V5 closed-pnl record matching a trade we know
    closed via broker-side SL/TP or external flatten.

    Bybit V5 emits a row on ``/v5/position/closed-pnl`` for every
    position that closes, regardless of whether the close fired from
    the entry order's attached SL/TP, a separate close order, or an
    operator flatten. The row carries the canonical close fill —
    ``avgExitPrice`` (the realised close price) and ``closedPnl``
    (the realised PnL net of fees).

    The reconciler reaches this helper only on the
    "filled entry order + position flat" verdict, where the entry
    order's ``avgPrice`` is the entry fill (not the exit). The
    closed-pnl record is the authoritative exit fill.

    Args:
      * ``category`` — ``linear`` / ``inverse``. ``spot`` and
        ``option`` are not supported by this endpoint and are
        skipped at the public-helper layer.
      * ``symbol`` — exchange symbol the trade ran on.
      * ``side`` — side of the **close** order (i.e. opposite of the
        trade's direction; ``"Sell"`` for a closed long, ``"Buy"``
        for a closed short). Used to disambiguate when multiple
        positions cycled on the same symbol inside the window.
      * ``start_ts_ms`` / ``end_ts_ms`` — search window in epoch
        milliseconds. The trade row's ``created_at`` is the natural
        start (minus a small grace); ``now()`` is the natural end.
        Bybit caps the window at 7 days; the caller is expected to
        not exceed that.
      * ``qty_target`` — optional. When set, records whose ``qty``
        differs from ``qty_target`` by more than ``qty_tolerance``
        (relative) are filtered out. This protects against a
        partial-close cycle accidentally matching.
      * ``entry_price_target`` — optional. When set, records whose
        ``avgEntryPrice`` differs from ``entry_price_target`` by
        more than ``entry_price_tolerance`` (relative, default 10
        bps) are filtered out. Pairs with ``opened_at_ms`` below to
        identify the right trade.
      * ``opened_at_ms`` — optional. When set, records whose
        ``createdTime`` is more than 60 s before ``opened_at_ms``
        are filtered out (a close cannot precede the open of the
        position it closes). Combined with ``entry_price_target``
        this disambiguates trades that share ``(side, qty,
        entry_price)`` but opened at different times — issue #1419
        revealed that on a high-frequency strategy where 5+ trades
        share ``(side, qty, entry±10bps)``, the matcher MUST also
        partition by time-after-open to pair each trade with its
        own close.

    Returns the raw inner record dict for the best match. Selection
    order:
      1. When both ``opened_at_ms`` AND ``entry_price_target`` are
         supplied: prefer the EARLIEST ``createdTime`` (i.e. the
         first close that happened at or after the trade was
         opened). Issue #1419's true diagnosis: among 5 records
         within 10 bps of #1540's entry, the right match is the
         one whose close happened first after the trade opened, not
         the closest entry-price match.
      2. When only ``entry_price_target`` is supplied: prefer the
         closest ``avgEntryPrice`` match, then most-recent by
         ``updatedTime``.
      3. Otherwise most-recent by ``updatedTime`` (preserves the
         pre-2026-05-18 behaviour for the orphan-reconciler path —
         it fires within ~60 s of the close, so most-recent IS the
         right answer).
    Returns ``None`` when no matching record exists or any SDK call
    raises.

    Wrapper at the call site is :func:`account_closed_pnl_for_trade`,
    which performs the account+category checks and converts API
    failures into ``None`` (vs ``{}``) so callers can distinguish.
    """
    try:
        resp = client.get_closed_pnl(
            category=category,
            symbol=symbol,
            startTime=int(start_ts_ms),
            endTime=int(end_ts_ms),
            limit=50,
        ) or {}
    except Exception:  # noqa: BLE001
        # Re-raise; the public wrapper logs + reports the failure
        # with the right account context.
        raise

    records = ((resp.get("result") or {}).get("list") or [])
    if not records:
        return None

    side_str = str(side or "").lower()
    candidates: list = []
    for rec in records:
        rec_side = str(rec.get("side") or "").lower()
        if side_str and rec_side and rec_side != side_str:
            continue
        if qty_target is not None and qty_target > 0:
            rec_qty = _f(rec.get("qty"))
            if rec_qty <= 0:
                continue
            rel_diff = abs(rec_qty - qty_target) / qty_target
            if rel_diff > qty_tolerance:
                continue
        if entry_price_target is not None and entry_price_target > 0:
            rec_entry = _f(rec.get("avgEntryPrice"))
            if rec_entry <= 0:
                continue
            rel_diff = abs(rec_entry - entry_price_target) / entry_price_target
            if rel_diff > entry_price_tolerance:
                continue
        if opened_at_ms is not None:
            # Close cannot precede open. 2 s slack absorbs clock
            # drift between the VM's wall clock (used for
            # ``opened_at_ms``) and Bybit's exec engine — measured
            # to be < 1 s in practice. 60 s would be too generous:
            # consecutive trades spaced minutes apart would
            # inherit each other's closes (issue #1419's
            # consecutive-shorts collapse).
            try:
                rec_created = int(rec.get("createdTime")
                                  or rec.get("updatedTime") or 0)
            except (TypeError, ValueError):
                rec_created = 0
            if rec_created and rec_created + 2_000 < int(opened_at_ms):
                continue
        candidates.append(rec)

    if not candidates:
        return None

    def _ts(rec: Dict[str, Any]) -> int:
        try:
            return int(rec.get("updatedTime") or rec.get("createdTime") or 0)
        except (TypeError, ValueError):
            return 0

    if (entry_price_target is not None and entry_price_target > 0
            and opened_at_ms is not None):
        # Issue #1419 fix: when we have both the entry price and
        # the open time, the right close is the EARLIEST one that
        # happened at or after open. Each trade has exactly one
        # such close; consecutive trades sharing (side, qty, entry)
        # get partitioned by their open timestamps. This is the
        # only ordering that handles all three callers correctly:
        #   * orphan reconciler — closes seconds after open, earliest
        #     match is the one just placed
        #   * live sweep — closes minutes after open, earliest match
        #     is the trade's actual close
        #   * backfill on older trades — earliest match is the close
        #     bound to the trade's open, not a later trade's close
        def _created_ts(rec: Dict[str, Any]) -> int:
            try:
                return int(rec.get("createdTime")
                           or rec.get("updatedTime") or 0)
            except (TypeError, ValueError):
                return 0
        candidates.sort(key=_created_ts)
        return candidates[0]

    if entry_price_target is not None and entry_price_target > 0:
        # entry_price supplied but no opened_at_ms — fall back to
        # closest-entry, most-recent. Adopted 2026-05-18 in the
        # PR-#1417 partial fix.
        def _entry_diff(rec: Dict[str, Any]) -> float:
            rec_entry = _f(rec.get("avgEntryPrice"))
            if rec_entry <= 0:
                return float("inf")
            return abs(rec_entry - entry_price_target) / entry_price_target
        candidates.sort(key=lambda r: (_entry_diff(r), -_ts(r)))
        return candidates[0]

    # Caller didn't supply entry_price_target — preserve the
    # pre-2026-05-18 most-recent-by-updatedTime fallback. Safe for
    # the orphan-reconciler path (which fires within ~60s of the
    # close, so most-recent IS the right answer).
    candidates.sort(key=_ts, reverse=True)
    return candidates[0]


def account_closed_pnl_for_trade(
    account: Dict[str, Any],
    *,
    symbol: str,
    direction: str,
    opened_at_ms: int,
    closed_at_ms: Optional[int] = None,
    qty: Optional[float] = None,
    entry_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Look up Bybit V5 closed-pnl for the position that opened with
    *direction* on *symbol* at ``opened_at_ms`` and closed before
    ``closed_at_ms`` (or now).

    Used by :func:`order_monitor._close_trade_from_order_status` to
    recover the real ``exit_price`` for trades closed by Bybit's
    broker-side SL/TP — the entry order's ``avgPrice`` is the entry
    fill, not the exit fill, and the actual close lives on a
    separate orderId the bot doesn't track. The closed-pnl record
    carries the authoritative ``avgExitPrice`` + ``closedPnl``.

    Return contract:
      * ``{"avg_exit_price", "avg_entry_price", "closed_pnl",
        "qty", "side", "closed_at"}`` on a successful lookup. Both
        prices are floats; ``closed_at`` is the Bybit
        ``updatedTime`` string (epoch ms).
      * ``None`` on **read failure**, unsupported category
        (``spot`` / ``option``), missing creds, or no matching
        record. Mirrors :func:`account_order_status` so the caller
        can keep the existing "leave ``exit_price`` NULL" fallback
        on None.

    Args:
      * ``direction`` — the trade row's ``direction`` (``"long"`` /
        ``"short"``). Internally translated to the **close-side**
        (``"Sell"`` for long, ``"Buy"`` for short).
      * ``opened_at_ms`` — epoch ms when the trade was opened.
        Used as the start of the search window with a small grace
        margin to forgive intra-tick clock skew.
      * ``closed_at_ms`` — epoch ms upper bound. Optional; defaults
        to ``now``. Bybit caps the window at 7 days; the helper
        clamps the start to ``end - 7 days`` to stay valid.
      * ``qty`` — when supplied, filters records whose ``qty``
        differs by more than 5 % (relative). Prevents a partial-
        close cycle from accidentally matching the full close.
      * ``entry_price`` — when supplied, filters records whose
        ``avgEntryPrice`` differs by more than 10 bps (relative).
        THE primary disambiguator on high-frequency strategies
        where every trade shares ``(side, qty)``. See
        :func:`_bybit_closed_pnl_lookup` for the full rationale and
        the 2026-05-18 incident (issue #1411) that motivated it.

    Currently only ``bybit`` (``linear`` / ``inverse``) is wired.
    Spot accounts have no closed-pnl endpoint — they return
    ``None`` (caller stays on the NULL fallback).
    """
    if not isinstance(account, dict) or not symbol or not direction:
        return None
    ex = (account.get("exchange") or "unknown").lower()
    if ex != "bybit":
        return None
    try:
        from src.units.accounts.execute import _bybit_category
        category = _bybit_category(account)
    except Exception:  # noqa: BLE001
        return None
    if category not in ("linear", "inverse"):
        return None

    direction_str = str(direction).lower()
    if direction_str == "long":
        close_side = "Sell"
    elif direction_str == "short":
        close_side = "Buy"
    else:
        return None

    end_ms = int(closed_at_ms) if closed_at_ms else int(
        datetime.now(timezone.utc).timestamp() * 1000
    )
    # 60-second start-window slack absorbs sub-second skew between
    # the bot's wall clock and Bybit's exec timestamps.
    start_ms = max(int(opened_at_ms) - 60_000, end_ms - 7 * 24 * 60 * 60 * 1000)

    try:
        client = bybit_client_for(account)
        if client is None:
            return None
        rec = _bybit_closed_pnl_lookup(
            client,
            category=category,
            symbol=symbol,
            side=close_side,
            start_ts_ms=start_ms,
            end_ts_ms=end_ms,
            qty_target=qty,
            entry_price_target=entry_price,
            opened_at_ms=int(opened_at_ms),
        )
    except Exception as exc:  # noqa: BLE001
        aid = account.get("account_id") or "unknown"
        logger.warning(
            "account_closed_pnl_for_trade(account=%s symbol=%s "
            "direction=%s): %s",
            aid, symbol, direction, exc,
        )
        try:
            from src.runtime.api_reporting import report_api_failure
            report_api_failure(
                exchange=ex, op="get_closed_pnl", account_id=str(aid),
                error=f"{type(exc).__name__}: {exc}", exception=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        return None

    if rec is None:
        return None
    return {
        "avg_exit_price": _f(rec.get("avgExitPrice")),
        "avg_entry_price": _f(rec.get("avgEntryPrice")),
        "closed_pnl": _f(rec.get("closedPnl")),
        "qty": _f(rec.get("qty")),
        "side": str(rec.get("side") or ""),
        "closed_at": rec.get("updatedTime") or rec.get("createdTime"),
    }


def account_order_status(
    account: Dict[str, Any],
    order_id: str,
) -> Optional[Dict[str, Any]]:
    """Look up a single order on the exchange by its order id.

    SSOT-from-Bybit primitive (issue #502): the reconciler uses this
    to ask Bybit "what is the status of THIS order?" rather than
    matching DB-open trades against an aggregate ``(symbol, side)``
    view. Per-orderId reconciliation is robust to open-positions index
    lag and disambiguates multi-leg accounts where two strategies hold
    a position on the same ``(symbol, side)``.

    Return contract:
      * ``{"order_id", "status", "filled_qty", "avg_price",
        "exec_time"}`` on a successful lookup. ``status`` is the raw
        Bybit ``orderStatus`` string (e.g. ``"New"``, ``"PartiallyFilled"``,
        ``"Filled"``, ``"Cancelled"``, ``"Rejected"``).
      * ``None`` on **read failure** (creds missing, network /
        exchange-side error, unsupported exchange) — same conservative
        semantic as :func:`account_open_positions`. The reconciler
        treats ``None`` as "skip this row this tick", never as
        "orphan it".
      * ``{"order_id": ..., "status": "not_found", ...}`` is returned
        when *both* ``get_open_orders`` and ``get_order_history``
        return empty for the orderId. This is the genuine "Bybit
        denies any record of this order" verdict and the reconciler
        is allowed to orphan-stamp on it.

    Currently only ``bybit`` is implemented — ``binance`` returns
    ``None`` so the reconciler skips Binance accounts (they are not
    in production yet).
    """
    if not isinstance(account, dict) or not order_id:
        return None
    ex = (account.get("exchange") or "unknown").lower()
    if ex != "bybit":
        # Binance / others: not yet wired through this primitive. Returning
        # None makes the reconciler skip the row (conservative). When the
        # operator turns on a Binance account, lift this guard and add the
        # connector-side lookup.
        return None
    try:
        client = bybit_client_for(account)
        if client is None:
            return None
        from src.units.accounts.execute import _bybit_category
        category = _bybit_category(account)
        rec = _bybit_order_status_lookup(
            client, category=category, order_id=str(order_id),
        )
        if rec is None:
            return {
                "order_id": str(order_id),
                "status": "not_found",
                "filled_qty": 0.0,
                "avg_price": 0.0,
                "exec_time": None,
            }
        # Bybit V5 surface: cumExecQty / avgPrice / updatedTime are the
        # canonical fill-side fields. Fall back to executed_qty /
        # exec_time on older response shapes.
        return {
            "order_id": str(rec.get("orderId") or order_id),
            "status": str(rec.get("orderStatus") or ""),
            "filled_qty": _f(rec.get("cumExecQty")),
            "avg_price": _f(rec.get("avgPrice")),
            "exec_time": rec.get("updatedTime") or rec.get("createdTime"),
        }
    except Exception as exc:  # noqa: BLE001
        aid = account.get("account_id") or "unknown"
        logger.warning(
            "account_order_status(account=%s order_id=%s): %s",
            aid, order_id, exc,
        )
        try:
            from src.runtime.api_reporting import report_api_failure
            report_api_failure(
                exchange=ex, op="get_order_status", account_id=str(aid),
                error=f"{type(exc).__name__}: {exc}", exception=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        return None


def account_open_positions(
    account: Dict[str, Any],
) -> Optional[list]:
    """Return list of ``{symbol, side, size, entry_price, unrealised_pnl}``
    dicts for the account's exchange-side open positions (``size > 0``).

    ``None`` on any failure path so callers can distinguish "no
    positions" (``[]``) from "could not read" (``None``):

    * non-dict / missing account argument
    * unsupported exchange (anything other than ``bybit`` / ``binance``)
    * missing creds (``bybit_client_for`` / ``binance_conn_for`` returns ``None``)
    * exchange SDK exception (logged + ``report_api_failure``)

    Lifted from ``src/units/ui/data_loaders.py:account_open_positions``
    in the BUG-042 PR 1 foundation step. Behaviour-preserving — the UI
    delegate now imports from here; per-account exchange-state reads
    are the accounts unit's responsibility.
    """
    if not isinstance(account, dict):
        return None
    ex = (account.get("exchange") or "unknown").lower()
    try:
        if ex == "bybit":
            client = bybit_client_for(account)
            if client is None:
                return None
            from src.units.accounts.execute import _bybit_category
            category = _bybit_category(account)
            if category == "spot":
                # Cash spot has no derivative-style positions; "open"
                # trades for spot accounts are tracked by the trade
                # journal / order packages log. ``account_open_positions``
                # is specifically the exchange-side position view — return
                # an empty list so callers do not surface a phantom "no
                # positions" warning derived from a mis-categorised v5
                # ``/position/list`` query.
                #
                # Historical note (PR 5, 2026-05-10): a spot-margin
                # synthesis branch lived here until bybit_2 migrated to
                # linear perpetuals in PR 3 and the spot-margin code
                # paths went dormant. No production account routes
                # through spot now.
                return []
            resp = client.get_positions(category=category, settleCoin="USDT")
            raw = resp.get("result", {}).get("list", []) if isinstance(resp, dict) else []
            out = []
            for p in raw:
                size = _f(p.get("size"))
                if size <= 0:
                    continue
                out.append({
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "size": size,
                    "entry_price": _f(p.get("avgPrice")),
                    "unrealised_pnl": _f(p.get("unrealisedPnl")),
                })
            return out
        if ex == "binance":
            conn = binance_conn_for(account)
            if conn is None:
                return None
            out = []
            for p in (conn.get_positions() or []):
                size = _f(p.get("contracts", p.get("positionAmt")))
                if size == 0:
                    continue
                out.append({
                    "symbol": p.get("symbol"),
                    "side": p.get("side") or ("long" if size > 0 else "short"),
                    "size": abs(size),
                    "entry_price": _f(p.get("entryPrice")),
                    "unrealised_pnl": _f(
                        p.get("unrealizedPnl", p.get("unrealised_pnl"))
                    ),
                })
            return out
        return None
    except Exception as exc:  # noqa: BLE001
        aid = account.get("account_id") or "unknown"
        logger.warning("account_open_positions(%s): %s", aid, exc)
        try:
            from src.runtime.api_reporting import report_api_failure
            report_api_failure(
                exchange=ex, op="get_positions", account_id=str(aid),
                error=f"{type(exc).__name__}: {exc}", exception=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        return None
