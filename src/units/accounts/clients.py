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


def alpaca_client_for(account: Dict[str, Any]):
    """Return an :class:`AlpacaClient` for *account*, or ``None`` if creds missing.

    Alpaca's key pair lives at env names shared with the data connector,
    so this reads env directly (the oanda/ib pattern). ``None`` when
    either is unset → coordinator treats the account as not-configured
    and pings. ``ALPACA_ENV`` selects paper (default) vs live host;
    account-level ``alpaca_env`` / ``base_url`` override.

    Per-account key override (so a paper and a live Alpaca account can run
    CONCURRENTLY — they need distinct credentials): the account may name
    its own env vars via ``api_key_env`` / ``api_secret_env``. Absent →
    the canonical ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY`` pair
    (the paper account's keys, shared with the data connector). So
    ``alpaca_paper`` keeps using the globals unchanged, while a real-money
    ``alpaca_live`` points at e.g. ``ALPACA_API_KEY_ID_LIVE`` /
    ``ALPACA_API_SECRET_KEY_LIVE`` + ``alpaca_env: live``.
    """
    key_env = str(account.get("api_key_env") or "ALPACA_API_KEY_ID")
    secret_env = str(account.get("api_secret_env") or "ALPACA_API_SECRET_KEY")
    api_key = os.environ.get(key_env, "")
    api_secret = os.environ.get(secret_env, "")
    if not api_key or not api_secret:
        return None
    from src.units.accounts.alpaca_client import AlpacaClient
    return AlpacaClient(
        api_key=api_key,
        api_secret=api_secret,
        env=account.get("alpaca_env") or None,
        base_url=account.get("base_url") or None,
    )


def oanda_client_for(account: Dict[str, Any]):
    """Return an :class:`OandaClient` for *account*, or ``None`` if creds missing.

    OANDA auth is a single bearer token + account id (no key+secret
    pair), so this reads ``OANDA_API_TOKEN`` / ``OANDA_ACCOUNT_ID``
    directly from the environment — the ``ib_client_for`` pattern, not
    :func:`resolve_credentials`. ``None`` when either is unset → the
    coordinator treats the account as not-configured and emits the
    diagnostic ping naming the missing env var. ``OANDA_ENV`` selects
    practice (default) vs live host; account-level ``oanda_env`` /
    ``base_url`` override it.
    """
    api_token = os.environ.get("OANDA_API_TOKEN", "")
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
    if not api_token or not account_id:
        return None
    from src.units.accounts.oanda_client import OandaClient
    return OandaClient(
        api_token=api_token,
        account_id=account_id,
        env=account.get("oanda_env") or None,
        base_url=account.get("base_url") or None,
    )


def ib_client_for(
    account: Dict[str, Any],
    *,
    client_id: Optional[int] = None,
    readonly: bool = False,
):
    """Return an :class:`IBClient` for *account*, or ``None`` if unusable.

    Interactive Brokers' TWS API has **no API keys** — authentication is
    the IB Gateway / TWS login session, so :func:`resolve_credentials`
    does not apply. Connection identity is host + port + clientId + the
    IB account code, read from the ``config/accounts.yaml`` entry with
    environment-variable overrides:

      * ``ib_host``       (env ``IB_HOST``)      — default ``127.0.0.1``
      * ``ib_port``       (env ``IB_PORT``)      — ``7496`` live / ``7497`` paper
      * ``ib_account``    (env ``IB_ACCOUNT``)   — e.g. ``U25907316`` / ``DUQ325724``
      * ``ib_client_id``  (env ``IB_CLIENT_ID``) — numeric API client id

    Returns ``None`` when ``ib_port`` is missing (the account cannot be
    reached) so the coordinator treats it as "not usable this tick" and
    refuses + pings, identical to the other ``*_client_for`` factories.
    The returned client is **not** connected yet — the socket opens
    lazily on first order/balance call (or via ``client.connect()``), so
    a down Gateway surfaces as a clean refusal at execution time rather
    than at construction.
    """
    if not isinstance(account, dict):
        return None
    exchange = str(account.get("exchange", "")).lower()
    if exchange not in ("interactive_brokers", "ib"):
        return None

    host = account.get("ib_host") or os.environ.get("IB_HOST") or "127.0.0.1"
    port = account.get("ib_port") or os.environ.get("IB_PORT")
    if not port:
        logger.warning(
            "ib_client_for(%s): no ib_port set (config or IB_PORT env) — "
            "cannot reach IB Gateway.",
            account.get("account_id") or "unknown",
        )
        return None
    account_code = account.get("ib_account") or os.environ.get("IB_ACCOUNT")
    resolved_client_id = (
        client_id
        if client_id is not None
        else (
            account.get("ib_client_id")
            or os.environ.get("IB_CLIENT_ID")
            # Stable per-port default so live (7496) and paper (7497) don't
            # collide on a shared Gateway when client_id is left unset.
            or (int(port) % 1000)
        )
    )
    from src.units.accounts.ib_client import get_ib_client

    return get_ib_client(
        host=str(host),
        port=int(port),
        client_id=int(resolved_client_id),
        account=str(account_code) if account_code else None,
        readonly=bool(readonly),
    )


def _ib_read_client_id() -> int:
    """A read-dedicated, process-unique IB clientId.

    Read probes (hourly-report balance/positions, ``/accounts_status``,
    CLIs) must NEVER reuse the trader's *execution* clientId (496/497): a
    probe opened from a different process with the same clientId is
    rejected by the Gateway as "clientId already in use" and races the
    live execution socket. Keying reads off a high, PID-salted id means
    (a) reads never collide with the execution sockets and (b) two reader
    processes don't collide with each other (the trader's hourly report
    vs. the Telegram bot's ``/accounts_status``). Within one process the
    id is stable, so the connection registry reuses a single read socket.
    """
    return 9000 + (os.getpid() % 900)


def ib_read_client_for(account: Dict[str, Any]):
    """Return a **read-only** :class:`IBClient` for balance/position probes.

    Same host/port/account resolution as :func:`ib_client_for`, but with a
    process-unique read clientId (:func:`_ib_read_client_id`) and
    ``readonly=True`` so a probe can never transmit an order. Returns
    ``None`` when the account is not an IB account or ``ib_port`` is unset.
    """
    return ib_client_for(account, client_id=_ib_read_client_id(), readonly=True)


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
    allow_wide_fallback: bool = False,
    allow_partial_aggregate: bool = False,
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
      * ``allow_wide_fallback`` — optional, **demo-only** escape hatch
        (BL-20260601-001). When the strict ``(side, qty, entry_price,
        opened_at)`` chain leaves zero candidates AND this flag is set,
        re-filter on ``side`` alone (within the same time window) and
        return the most-recent close. Bybit DEMO closed-pnl rows carry
        placeholder / zeroed ``avgEntryPrice`` and looser ``qty``
        rounding, so the live-money disambiguators over-filter and
        strand the realised PnL as NULL (5/5 demo ``htf_pullback``
        closes in the 2026-06-08 window). LIVE accounts must NOT set
        this — they keep the strict NULL-on-no-match contract that
        the #1411 / #1419 fixes depend on.

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
    # ``pool`` = records passing EVERYTHING EXCEPT the single-record qty match
    # (side + close-after-open + entry-price). It's the candidate set for the
    # partial-close aggregation fallback (BL-20260620 / live orphan-with-no-pnl):
    # a position that scaled in / closed in several partial fills has no single
    # closed-pnl record matching the full qty, so the strict matcher strands its
    # PnL as NULL → the watchdog orphans it. ``rej_*`` count why records dropped
    # so the no-match path can log a diagnosable reason.
    pool: list = []
    rej_side = rej_qty = rej_entry = rej_time = 0
    for rec in records:
        rec_side = str(rec.get("side") or "").lower()
        if side_str and rec_side and rec_side != side_str:
            rej_side += 1
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
                rej_time += 1
                continue
        if entry_price_target is not None and entry_price_target > 0:
            rec_entry = _f(rec.get("avgEntryPrice"))
            if rec_entry <= 0 or (
                abs(rec_entry - entry_price_target) / entry_price_target
                > entry_price_tolerance
            ):
                rej_entry += 1
                continue
        # Passed side + time + entry — eligible for the aggregation pool.
        pool.append(rec)
        if qty_target is not None and qty_target > 0:
            rec_qty = _f(rec.get("qty"))
            if rec_qty <= 0 or abs(rec_qty - qty_target) / qty_target > qty_tolerance:
                rej_qty += 1
                continue
        candidates.append(rec)

    def _ts(rec: Dict[str, Any]) -> int:
        try:
            return int(rec.get("updatedTime") or rec.get("createdTime") or 0)
        except (TypeError, ValueError):
            return 0

    def _created_ts(rec: Dict[str, Any]) -> int:
        try:
            return int(rec.get("createdTime") or rec.get("updatedTime") or 0)
        except (TypeError, ValueError):
            return 0

    if not candidates:
        # Diagnostic (observe-only): WHY did the strict single-match find
        # nothing? Surfaces the failing filter so a recurrence is diagnosable
        # without re-pulling the raw API (BL-20260620 live orphan-no-pnl).
        if records:
            logger.info(
                "closed_pnl no strict single-match: symbol=%s side=%s records=%d "
                "pool(side+time+entry)=%d rej_side=%d rej_time=%d rej_entry=%d "
                "rej_qty=%d qty_target=%s entry_target=%s",
                symbol, side, len(records), len(pool), rej_side, rej_time,
                rej_entry, rej_qty, qty_target, entry_price_target,
            )
        # Partial-close aggregation (BL-20260620, live): a position that scaled
        # in / closed in several partial fills has no single closed-pnl record
        # matching the full qty (rej_qty), but the pool (side+time+entry) sums to
        # it. Reconstruct ONE synthetic close — qty-weighted avgExit/avgEntry +
        # summed closedPnl — instead of orphaning the trade with NULL pnl. The
        # strict single-match was tried first (unchanged), so this cannot regress
        # the #1411/#1419 disambiguation. Guarded: requires a qty target and the
        # accumulated earliest-after-open legs to land within 10% of it, so closes
        # from other positions can't be swept in.
        if allow_partial_aggregate and qty_target and qty_target > 0 and pool:
            legs = sorted((r for r in pool if _f(r.get("qty")) > 0), key=_created_ts)
            acc_qty = 0.0
            chosen: list = []
            for r in legs:
                chosen.append(r)
                acc_qty += _f(r.get("qty"))
                if acc_qty >= qty_target * 0.999:
                    break
            if chosen and acc_qty > 0 and abs(acc_qty - qty_target) / qty_target <= 0.10:
                sum_pnl = sum(_f(r.get("closedPnl")) for r in chosen)
                w_exit = sum(_f(r.get("avgExitPrice")) * _f(r.get("qty"))
                             for r in chosen) / acc_qty
                entries = [_f(r.get("avgEntryPrice")) for r in chosen]
                w_entry = (sum(e * _f(r.get("qty")) for e, r in zip(entries, chosen))
                           / acc_qty if all(e > 0 for e in entries) else 0.0)
                last = max(chosen, key=_ts)
                logger.info(
                    "closed_pnl aggregated %d partial closes for %s %s: qty~%.8f "
                    "(target %.8f) exit~%.4f pnl=%.6f",
                    len(chosen), symbol, side, acc_qty, qty_target, w_exit, sum_pnl,
                )
                return {
                    "avgExitPrice": w_exit,
                    "avgEntryPrice": w_entry,
                    "closedPnl": sum_pnl,
                    "qty": acc_qty,
                    "side": chosen[-1].get("side") or side,
                    "updatedTime": last.get("updatedTime") or last.get("createdTime"),
                    "createdTime": chosen[0].get("createdTime"),
                    "_aggregated_legs": len(chosen),
                }
        # BL-20260601-001 — demo-fallback wide lookup. On a Bybit DEMO
        # account the strict (side + qty±5% + avgEntryPrice±10bps +
        # createdTime≥opened_at) chain returns no match far more often
        # than on live: the demo venue's closed-pnl rows carry
        # placeholder / zeroed avgEntryPrice and looser qty rounding, so
        # the entry-price + qty disambiguators (added for the #1411 /
        # #1419 LIVE-money collapse) over-filter and strand the realised
        # PnL as NULL. Demo has no live-money disambiguation requirement,
        # so widen to (side + closed-at-window) only and take the most
        # recent matching close. LIVE accounts never reach this branch —
        # they keep the strict NULL-on-no-match contract #1411 / #1419
        # are built on.
        if not allow_wide_fallback:
            return None
        for rec in records:
            rec_side = str(rec.get("side") or "").lower()
            if side_str and rec_side and rec_side != side_str:
                continue
            candidates.append(rec)
        if not candidates:
            return None
        candidates.sort(key=_ts, reverse=True)
        return candidates[0]

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


# ---------------------------------------------------------------------------
# PnL source — declared per integration (canonical)
# ---------------------------------------------------------------------------
#
# Every account resolves its realised PnL the same way: **prefer broker truth,
# fall back to local compute**. Whether an integration *can* provide broker
# truth is a property of the integration, not a per-account preference — it
# depends on whether that exchange exposes (and we have wired) an authoritative
# closed-pnl reader in ``account_closed_pnl_for_trade`` below.
#
# This set is the single source of truth for that capability. Membership is the
# contract: an exchange in the set has a wired broker closed-pnl reader (its
# trades' PnL is recovered fee-accurate from the broker by
# ``order_monitor._sweep_pending_pnl_from_bybit``); an exchange NOT in the set
# has no reader, so its trades' PnL is computed locally
# (``order_monitor._sweep_local_pnl_for_unpriced`` → ``src.runtime.local_pnl``)
# from entry/exit/qty × the per-contract multiplier.
#
# **Default is local** (the universal fallback) — a new integration that wires
# no reader automatically gets correct local PnL with no extra step, so a
# missing declaration never strands a trade at $0.00 (Prime Directive: no
# default-off capability gate). Adding broker truth for a new exchange is the
# explicit opt-in: extend ``account_closed_pnl_for_trade`` to handle it AND add
# the exchange string here. The `new-broker` skill carries this as a wiring
# step ("declare the integration's PnL source").
BROKER_PNL_READER_EXCHANGES: frozenset[str] = frozenset({"bybit"})


def exchange_has_broker_pnl_reader(exchange: Any) -> bool:
    """True when *exchange* has a wired authoritative broker closed-pnl reader
    (i.e. `account_closed_pnl_for_trade` can return real broker PnL for it)."""
    return str(exchange or "").strip().lower() in BROKER_PNL_READER_EXCHANGES


def account_has_broker_pnl_reader(account: Optional[Dict[str, Any]]) -> bool:
    """True when *account*'s integration provides broker-truth PnL; False means
    its realised PnL is resolved by the local-compute fallback."""
    if not isinstance(account, dict):
        return False
    return exchange_has_broker_pnl_reader(account.get("exchange"))


# ---------------------------------------------------------------------------
# Per-integration MANAGEMENT capability declaration (P2 of the live-trade
# management contract — docs/audits/live-trade-management-contract-2026-06-16.md)
# ---------------------------------------------------------------------------
#
# Sibling of ``BROKER_PNL_READER_EXCHANGES`` above: a declaration of which
# post-entry / live-management operations each integration ACTUALLY implements
# TODAY. This is the canonical answer to "can the order-monitor apply this
# verdict op to this account's exchange?" — replacing the scattered ``== "bybit"``
# branches with one resolver.
#
# Operations (the verdict-application + reconciliation primitives the monitor
# drives while a trade is live):
#   * ``modify``         — adjust SL/TP of an open position
#                          (``execute.modify_open_order`` — bybit only in v1).
#   * ``close``          — full reduce-only close
#                          (``execute.close_open_position`` — bybit only in v1).
#   * ``partial_close``  — partial reduce-only close (same ``close_open_position``
#                          helper, sub-position qty — bybit only in v1).
#   * ``order_status``   — per-order status lookup
#                          (``account_order_status`` — ``if ex != "bybit": return None``).
#   * ``open_positions`` — exchange-side open-positions snapshot
#                          (``account_open_positions`` — wired for bybit
#                          + interactive_brokers; NOT alpaca/oanda).
#
# This map reflects CURRENT WIRED REALITY (verified against the code). P3
# wired IB/Alpaca ``close``; S2 (BL-20260616-LTMGMT-MODIFY) wired IB/Alpaca
# ``modify``. Wiring a primitive extends BOTH the implementing code AND this
# map (mirroring how adding a broker PnL reader extends both the dispatch and
# the reader set). OANDA management is still unwired (later item, before it
# leaves dry_run).
#
# **Safe default is "unsupported".** An integration absent from this map (or an
# op absent from its set) resolves to "not supported" — so an op that hasn't
# been wired fails honestly (``unsupported_op``) rather than misleadingly
# (``no_client``) or by silently doing nothing. Unlike the PnL-reader set (where
# the default — local compute — is a real universal fallback), there is NO
# universal modify/close fallback today, so "unsupported" is the truthful
# default until P3 wires the missing integrations.
EXCHANGE_MANAGEMENT_CAPS: dict[str, frozenset[str]] = {
    "bybit": frozenset(
        {"modify", "close", "partial_close", "order_status", "open_positions"}
    ),
    # interactive_brokers (ib_paper, live): account_open_positions reads IB
    # positions; P3 (live-trade management contract) added IBClient.close
    # (cancel resting bracket/OCA legs + opposing reduce market order) wired
    # through execute.close_open_position + order_monitor._build_account_client,
    # so ``close`` is supported. S2 (BL-20260616-LTMGMT-MODIFY) added
    # ``modify`` (trailing-SL re-arm): IBClient.modify_protective cancels the
    # resting OCA legs + re-places a fresh GTC OCA pair at the merged SL/TP via
    # place_protective, wired through execute.modify_open_order. ``order_status``
    # is not wired (IBClient.status exists but account_order_status returns None
    # for IB).
    "interactive_brokers": frozenset({"modify", "close", "open_positions"}),
    "ib": frozenset({"modify", "close", "open_positions"}),  # alias seen in account_open_positions
    # alpaca (alpaca_paper, live): P3 wired the native idempotent flatten
    # (AlpacaClient.close → DELETE /v2/positions/{symbol}) through
    # execute.close_open_position, and account_open_positions now has an alpaca
    # branch — so ``close`` + ``open_positions`` are supported. S2
    # (BL-20260616-LTMGMT-MODIFY) added ``modify``: AlpacaClient.modify_protective
    # PATCHes the resting bracket legs (stop_price / limit_price) for whichever
    # of SL/TP the verdict changed. ``partial_close`` is not wired (the flatten
    # endpoint closes the whole position).
    "alpaca": frozenset({"modify", "close", "open_positions"}),
    # oanda (oanda_practice, currently dry_run): S2 (BL-20260616-LTMGMT-OANDA)
    # wired ``close`` (OandaClient.close → v20 PUT positions/{instrument}/close,
    # via execute.close_open_position + order_monitor._build_account_client) +
    # ``open_positions`` (account_open_positions oanda branch). Done BEFORE any
    # go-live so promoting oanda_practice off dry_run never recreates the
    # unmanaged-live-position gap. ``modify`` / ``partial_close`` / ``order_status``
    # remain unwired (OANDA SL/TP modify is a later follow-up; the static entry
    # bracket + close cover the live-management baseline).
    "oanda": frozenset({"close", "open_positions"}),
    # breakout (BreakoutAPI): an EXCHANGE_MAP stub integration with no
    # management primitives wired (no modify/close/order_status/open_positions).
    # Declared explicitly as the empty set so the P5 CI guard
    # (test_ltmgmt_p5_contract_ci) confirms every EXCHANGE_MAP integration has
    # made a *conscious* management-caps declaration rather than silently
    # defaulting to "supports nothing" — a missing declaration is the gap the
    # guard exists to catch.
    "breakout": frozenset(),
}

_EMPTY_CAPS: frozenset[str] = frozenset()


def exchange_management_caps(exchange: Any) -> frozenset[str]:
    """Return the set of management ops *exchange* supports today.

    Pure, never raises. Unknown / falsy exchange → empty set (the safe
    "supports nothing" default). See :data:`EXCHANGE_MANAGEMENT_CAPS`.
    """
    return EXCHANGE_MANAGEMENT_CAPS.get(
        str(exchange or "").strip().lower(), _EMPTY_CAPS
    )


def account_supports_management(
    account: Optional[Dict[str, Any]], op: str
) -> bool:
    """True when *account*'s integration implements management op *op* today.

    *account* may be the loaded account dict or the order-monitor's resolved
    cfg dict — both carry ``exchange``. Pure, never raises; safe default is
    ``False`` (unsupported) on a missing/unknown account or op.
    """
    if not isinstance(account, dict) or not op:
        return False
    return str(op).strip().lower() in exchange_management_caps(
        account.get("exchange")
    )


def account_closed_pnl_for_trade(
    account: Dict[str, Any],
    *,
    symbol: str,
    direction: str,
    opened_at_ms: int,
    closed_at_ms: Optional[int] = None,
    qty: Optional[float] = None,
    entry_price: Optional[float] = None,
    reduce_leg: bool = False,
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
      * ``reduce_leg`` — set for an intent-mode reduce / close leg
        (S-MSE-2, ``setup_type='intent_reduce'``). Such a row's
        ``direction`` is the close-order side (e.g. ``long`` = a
        buy-to-reduce on a held short) and its ``entry_price`` is the
        primary leg's *intended* entry, NOT the reduced position's
        actual entry — so both the close-side translation and the
        ``entry_price`` filter point at the wrong record and strand
        the realised PnL as NULL (BL-20260601-001, verified live trade
        #2491). When set, the lookup matches by absolute position
        movement (``qty`` + close-window + ``opened_at``) and skips the
        unreliable ``side`` / ``entry_price`` disambiguators. Normal
        trades leave it ``False`` and keep the strict #1411 / #1419
        contract.

    Currently only ``bybit`` (``linear`` / ``inverse``) is wired.
    Spot accounts have no closed-pnl endpoint — they return
    ``None`` (caller stays on the NULL fallback).
    """
    if not isinstance(account, dict) or not symbol or not direction:
        return None
    ex = (account.get("exchange") or "unknown").lower()
    # Only integrations declared to have a broker closed-pnl reader (the
    # canonical BROKER_PNL_READER_EXCHANGES set) are handled here; everything
    # else resolves PnL via the local-compute fallback. Currently this reader
    # implements Bybit; adding another broker means extending the dispatch
    # below AND adding its exchange to that set.
    if not account_has_broker_pnl_reader(account):
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

    # Demo accounts opt into the wide (side + window) fallback when the
    # strict disambiguator chain finds nothing — see BL-20260601-001 and
    # ``_bybit_closed_pnl_lookup``'s ``allow_wide_fallback`` docstring.
    # The parse mirrors ``bybit_client_for`` so a string "true"/"1"/"yes"
    # all count as demo; live accounts stay on the strict contract.
    is_demo = str(account.get("demo", "false")).strip().lower() in (
        "true", "1", "yes",
    )

    # BL-20260608-DEMOPNL / BL-20260620-CLOSEDPNL-LOOKUP-MISMATCH-DEMO:
    # Bybit's closed-pnl endpoint does NOT return reliable per-trade records for
    # the DEMO / testnet account — distinct demo trades on the same symbol share
    # / mis-map records, and the wide fallback below booked the SAME -864.45 onto
    # two separate SOL paper trades. There is no trustworthy broker-truth realised
    # PnL for demo, so DON'T guess from this lookup: return None here (both
    # writers — the monitor close path and ``_sweep_pending_pnl_from_bybit`` —
    # funnel through this function, so a single early return stops both from
    # booking the wrong record). The row then stays "unpriced" and the universal
    # local-compute sweep (``order_monitor._sweep_local_pnl_for_unpriced``:
    # entry × exit × qty × contract_value_usd, multiplier-aware, or a
    # mark-to-market exit) resolves it deterministically — the same local path
    # IBKR/Alpaca/OANDA already use. Real-money (non-demo) Bybit is untouched and
    # keeps the strict broker-truth contract below.
    if is_demo:
        return None

    # Reduce-leg lookup (BL-20260601-001 prong 2): the journal direction
    # is the close-order side, not the reduced position's side, and the
    # recorded entry is the primary leg's intent — both unreliable. Match
    # by qty + close-window only (skip side + entry filters).
    lookup_side = "" if reduce_leg else close_side
    lookup_entry_price = None if reduce_leg else entry_price

    try:
        client = bybit_client_for(account)
        if client is None:
            return None
        rec = _bybit_closed_pnl_lookup(
            client,
            category=category,
            symbol=symbol,
            side=lookup_side,
            start_ts_ms=start_ms,
            end_ts_ms=end_ms,
            qty_target=qty,
            entry_price_target=lookup_entry_price,
            opened_at_ms=int(opened_at_ms),
            allow_wide_fallback=False,
            # LIVE non-reduce lookups: reconstruct a partial/scaled close from
            # its legs when no single record matches the full qty, instead of
            # stranding the PnL NULL → orphaning the trade (BL-20260620). The
            # demo wide-fallback was removed (demo returns early above —
            # BL-20260608-DEMOPNL); reduce legs match on qty+window only and
            # must not aggregate across sides.
            allow_partial_aggregate=(not reduce_leg),
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


def account_exec_type_for_close(
    account: Dict[str, Any],
    symbol: str,
    *,
    end_ms: Optional[int] = None,
) -> Optional[str]:
    """Return the Bybit ``execType`` of the most recent execution for
    *symbol* in the 10-minute window ending at *end_ms* (or now).

    Returns the raw ``execType`` string — e.g. ``"BustTrade"`` (liquidation /
    demo margin call), ``"AdlTrade"`` (auto-deleverage), ``"Trade"`` (normal
    order fill) — when a record is found, or ``None`` when the window is empty
    (possible platform reset / data gap) or on any read failure.

    Used by the reconciler's ``broker_close_unclassified`` path (``exit_price_source:
    "entry_order_avg_price_unreliable"``) to distinguish demo-account margin calls
    from real manual / out-of-bracket closes from platform resets, so the operator
    ping carries an accurate classification instead of the generic "unknown" tag.

    Only ``bybit`` ``linear`` / ``inverse`` is wired; other exchanges return
    ``None`` immediately (best-effort, never raises).
    """
    if not isinstance(account, dict) or not symbol:
        return None
    ex = (account.get("exchange") or "").lower()
    if ex != "bybit":
        return None
    try:
        from src.units.accounts.execute import _bybit_category
        category = _bybit_category(account)
    except Exception:  # noqa: BLE001
        return None
    if category not in ("linear", "inverse"):
        return None
    try:
        client = bybit_client_for(account)
        if client is None:
            return None
        _end = int(end_ms) if end_ms else int(
            datetime.now(timezone.utc).timestamp() * 1000
        )
        _start = _end - 10 * 60 * 1000  # 10-minute look-back window
        resp = client.get_executions(
            category=category,
            symbol=str(symbol).upper(),
            startTime=int(_start),
            endTime=int(_end),
            limit=5,
        ) or {}
        records = ((resp.get("result") or {}).get("list") or [])
        if not records:
            return None
        # Bybit returns newest first; take the execType of the most recent entry.
        return str(records[0].get("execType") or "") or None
    except Exception as exc:  # noqa: BLE001
        aid = account.get("account_id") or "unknown"
        logger.warning(
            "account_exec_type_for_close(account=%s symbol=%s): %s",
            aid, symbol, exc,
        )
        return None


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

    Currently only ``bybit`` is implemented — other exchanges return
    ``None`` so the reconciler skips those accounts.
    """
    if not isinstance(account, dict) or not order_id:
        return None
    ex = (account.get("exchange") or "unknown").lower()
    if ex != "bybit":
        # Other exchanges: not wired through this primitive. Returning
        # None makes the reconciler skip the row (conservative).
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


def _alpaca_pos_in_scope(pos: Dict[str, Any], account: Dict[str, Any]) -> bool:
    """True if *pos* belongs to this bot-account's asset class (shared-account isolation).

    A shared Alpaca paper login can back BOTH an equity bot-account (alpaca_paper)
    and an options-expression bot-account (alpaca_options_paper). Each must see only
    its own positions or the reverse reconciler would adopt the other's legs as
    phantom orphans. Options-expressing account → ``us_option`` only; any other
    account → everything that is NOT ``us_option`` (so equity + unknown asset_class
    pass, preserving legacy behaviour when no options are present). Pure; never
    raises — an import/lookup failure defaults to the equity (non-options) view.
    """
    ac = str(pos.get("asset_class") or "").lower()
    try:
        from src.units.accounts.options_overlay import account_expresses_options
        expresses_options = account_expresses_options(account) is not None
    except Exception:  # noqa: BLE001 — never let this gate a position read
        expresses_options = False
    return ac == "us_option" if expresses_options else ac != "us_option"


def _bybit_configured_symbols(account: Dict[str, Any]) -> list:
    """Return the account's configured instrument list for the per-symbol
    position cross-check (BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND).

    Prefers the ``symbols`` key already on the passed cfg dict (the reverse
    reconciler's cfgs carry it). Falls back to loading it from
    ``accounts.yaml`` by ``account_id`` for callers that hand-build a
    reduced cfg (e.g. ``order_monitor._build_account_client``). Best-effort:
    any failure returns ``[]`` so the cross-check simply no-ops rather than
    raising — the primary settleCoin read is unaffected.
    """
    syms = account.get("symbols")
    if isinstance(syms, (list, tuple)) and syms:
        return list(syms)
    aid = account.get("account_id")
    if not aid:
        return []
    try:
        from src.config.accounts_loader import load_accounts_dict
        cfg = load_accounts_dict().get(aid) or {}
        resolved = cfg.get("symbols")
        return list(resolved) if isinstance(resolved, (list, tuple)) else []
    except Exception:  # noqa: BLE001
        return []


def account_open_positions(
    account: Dict[str, Any],
) -> Optional[list]:
    """Return list of ``{symbol, side, size, entry_price, unrealised_pnl}``
    dicts for the account's exchange-side open positions (``size > 0``).

    ``None`` on any failure path so callers can distinguish "no
    positions" (``[]``) from "could not read" (``None``):

    * non-dict / missing account argument
    * unsupported exchange (anything other than ``bybit`` /
      ``interactive_brokers`` / ``alpaca`` / ``oanda``)
    * missing creds (``bybit_client_for`` /
      ``alpaca_client_for`` / ``oanda_client_for`` returns ``None``)
    * exchange SDK exception (logged + ``report_api_failure``)
    * **IB only:** an EMPTY snapshot from a Gateway that is NOT verified
      logged-in (``net_liquidation`` not populated). A logged-out-but-
      connected IB Gateway reports ``connected=true`` yet ``positions()``
      returns ``[]`` — indistinguishable from genuinely flat — so the
      empty case is gated on ``net_liquidation`` populated. A NON-empty
      IB snapshot is itself proof of a live session and is returned as-is
      with no extra health round-trip.

    Lifted from ``src/units/ui/data_loaders.py:account_open_positions``
    in the BUG-042 PR 1 foundation step. Behaviour-preserving — the UI
    delegate now imports from here; per-account exchange-state reads
    are the accounts unit's responsibility.
    """
    if not isinstance(account, dict):
        return None
    ex = (account.get("exchange") or "unknown").lower()
    try:
        if ex in ("interactive_brokers", "ib"):
            # Dry IB accounts (ib_live) are never dialled from the read
            # path — the live gateway socket stays closed until promotion,
            # mirroring the coordinator which never constructs a client for
            # a dry account. Return None ("could not read") rather than a
            # false empty list.
            mode = str(account.get("mode") or "live").lower()
            if mode != "live":
                return None
            client = ib_read_client_for(account)
            if client is None:
                return None
            from src.units.accounts.ib_client import IBConnectionError
            try:
                positions = client.positions()
            except IBConnectionError as exc:
                # A down/evicted Gateway is an expected, recurring state —
                # fail quietly (log only) instead of routing through the
                # generic report_api_failure below, which would emit a
                # WARN+ outcome / Telegram ping on every read.
                logger.warning(
                    "account_open_positions(%s): IB gateway unreachable: %s",
                    account.get("account_id") or "unknown", exc,
                )
                return None
            # A NON-empty IB snapshot is proof of a live, logged-in session
            # (a logged-out Gateway can't return positions), so trust it and
            # return as-is — no extra health round-trip on the common path.
            #
            # An EMPTY snapshot is the ambiguous case: a logged-out-but-
            # connected Gateway reports ``connected=true`` yet ``portfolio()``
            # returns ``[]`` — indistinguishable from a genuinely-flat account.
            # Verify the session is truly logged in via ``net_liquidation``
            # populated (the SAME signal the IB-gateway watchdog uses for
            # "truly logged in"; ``balance()`` reads the account summary, which
            # a logged-out Gateway can't satisfy → ``net_liquidation`` 0.0 or
            # an ``IBConnectionError``). Only when net_liq is populated is the
            # empty ``[]`` a trustworthy "genuinely flat"; otherwise return
            # ``None`` ("couldn't read — skip conservatively") so the reverse
            # reconciler's ``if positions is None: continue`` guard prevents a
            # false-close of a genuinely-open IB row during a sustained
            # gateway logout (BL — logged-out gateway false-close).
            if positions:
                return positions
            try:
                net_liq = client.balance().get("net_liquidation")
            except IBConnectionError as exc:
                logger.warning(
                    "account_open_positions(%s): IB empty snapshot but "
                    "balance() unreachable (treating as read-failure, not "
                    "flat): %s",
                    account.get("account_id") or "unknown", exc,
                )
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "account_open_positions(%s): IB empty snapshot but "
                    "balance() health-read failed (treating as read-failure, "
                    "not flat): %s",
                    account.get("account_id") or "unknown", exc,
                )
                return None
            if not net_liq:
                # net_liquidation None / 0.0 → Gateway is connected but not
                # verified logged-in. Do NOT trust the empty snapshot.
                logger.warning(
                    "account_open_positions(%s): IB empty snapshot with "
                    "net_liquidation=%r — gateway not verified logged-in; "
                    "returning None (read-failure) not [] (flat).",
                    account.get("account_id") or "unknown", net_liq,
                )
                return None
            # Verified logged-in AND genuinely flat.
            return []
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
            out: list = []
            seen: set = set()

            def _emit(p: Dict[str, Any]) -> None:
                size = _f(p.get("size"))
                if size <= 0:
                    return
                sym = p.get("symbol")
                if sym in seen:
                    return
                seen.add(sym)
                out.append({
                    "symbol": sym,
                    "side": p.get("side"),
                    "size": size,
                    "entry_price": _f(p.get("avgPrice")),
                    "unrealised_pnl": _f(p.get("unrealisedPnl")),
                })

            for p in raw:
                _emit(p)

            # BL-20260713-BYBIT2-BTC-SETTLECOIN-BLIND: a single
            # ``settleCoin=USDT`` position/list page can silently omit a
            # configured symbol's residual position — a live 0.001 BTCUSDT on
            # real-money bybit_2 was absent from the settleCoin list while the
            # symbol-scoped read (and the terminal) saw it, so the reconciler
            # read the account as flat and FALSE-CLOSED the journal row,
            # leaving the position invisible on /positions and unprotected. A
            # symbol-scoped ``get_positions(symbol=…)`` always returns that
            # symbol's exact position, so cross-check every configured symbol
            # not already surfaced. Best-effort: a per-symbol read error is
            # logged and skipped (the primary settleCoin read still stands) so
            # a transient hiccup never nukes the whole read into ``None``.
            for sym in _bybit_configured_symbols(account):
                if not isinstance(sym, str) or not sym or sym in seen:
                    continue
                try:
                    r2 = client.get_positions(category=category, symbol=sym)
                    lst2 = (
                        r2.get("result", {}).get("list", [])
                        if isinstance(r2, dict)
                        else []
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "account_open_positions(%s): per-symbol cross-check "
                        "for %s failed: %s",
                        account.get("account_id") or "unknown", sym, exc,
                    )
                    continue
                for p in lst2:
                    _emit(p)
            return out
        if ex == "alpaca":
            # Dry alpaca accounts are never dialled from the read path
            # (mirrors the IB branch above) — return None ("could not read")
            # rather than a false empty list.
            mode = str(account.get("mode") or "live").lower()
            if mode != "live":
                return None
            client = alpaca_client_for(account)
            if client is None:
                # No creds → can't read (factory returns None on unset keys).
                return None
            raw_positions = client.positions()
            if raw_positions is None:
                # Read failure (network / non-2xx / missing creds) — return
                # None ("could not read") so the reverse reconciler's
                # ``if positions is None: continue`` guard skips this account
                # rather than treating a failed read as a flat account and
                # false-closing live rows (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE).
                return None
            # Shared-paper-account isolation (Slice-3b): a single Alpaca paper
            # login can back BOTH the equity bot-account (alpaca_paper) and the
            # options-expression bot-account (alpaca_options_paper). Keep only the
            # positions belonging to THIS account's asset class so the reverse
            # reconciler never adopts the other account's legs as phantom orphans.
            # Filter BEFORE the empty-check so the balance-verified-empty guard +
            # the monitor's fresh-fill grace protect each account-class view
            # independently. No-op when no options legs are present (the common
            # case): a non-options account keeps everything that isn't us_option.
            raw_positions = [p for p in raw_positions if _alpaca_pos_in_scope(p, account)]
            if not raw_positions:
                # EMPTY but successful read — the ambiguous case, exactly like
                # the IB branch above. A just-placed bracket-MARKET order whose
                # fill hasn't propagated to /v2/positions yet reads empty here
                # while NOT being flat. Verify the session is genuinely live via
                # balance() (account equity populated) before trusting [] as
                # "flat"; otherwise return None ("couldn't confirm — skip
                # conservatively"). The order_monitor's fresh-fill grace is the
                # belt to this suspenders: even a verified-live empty read won't
                # close a too-young row.
                #
                # BL-20260707-RECONCILER-MASS-FALSE-CLOSE: the original check
                # here was ``if not bal:``, which is FALSE for a negative
                # balance (a negative float is truthy in Python) — so a deeply
                # negative account (alpaca_paper, ~-$67,946) sailed straight
                # through as "verified live" and an empty positions() read got
                # trusted as genuinely flat, even though several positions
                # (SPY/SLV/QQQ/TLT/GLD/IWM/IEF) were still open on the broker.
                # position_snapshot_reconciler then mass-closed all of them
                # with a fabricated local-mark PnL; the reverse reconciler
                # re-adopted the same still-open positions ~2h later. Two
                # fixes: (1) ``bal is None`` correctly distinguishes "balance
                # read failed" from "balance is a real number, even 0 or
                # negative"; (2) a NEGATIVE balance is itself an anomalous
                # account state — verifying liveness via balance() only proves
                # the account is reachable, not that the positions() list is
                # accurate, and trusting an ambiguous empty read is highest-
                # stakes exactly when equity is already wrong. So a negative
                # balance does NOT auto-verify; treat it the same as an
                # unreadable balance (return None — skip conservatively rather
                # than mass-close rows on a read that can't be fully trusted).
                bal = client.balance()
                if bal is None:
                    logger.warning(
                        "account_open_positions(%s): alpaca empty snapshot but "
                        "balance() unreadable (None) — treating as read-failure, "
                        "not flat; returning None.",
                        account.get("account_id") or "unknown",
                    )
                    return None
                if bal < 0:
                    logger.warning(
                        "account_open_positions(%s): alpaca empty snapshot with "
                        "NEGATIVE balance()=%.2f — anomalous account state, not "
                        "trusting the empty read as genuinely flat; returning "
                        "None (read-failure, skip) rather than risk a mass "
                        "false-close (BL-20260707-RECONCILER-MASS-FALSE-CLOSE).",
                        account.get("account_id") or "unknown", bal,
                    )
                    return None
                logger.info(
                    "account_open_positions(%s): alpaca empty snapshot verified "
                    "via balance()=%.2f — trusting as genuinely flat.",
                    account.get("account_id") or "unknown", bal,
                )
                return []
            out = []
            # AlpacaClient.positions() emits
            # ``[{symbol, side, qty, avg_price, unrealized_pnl}]`` — normalise
            # to the canonical ``{symbol, side, size, entry_price,
            # unrealised_pnl}`` shape every other consumer speaks. ``side`` is
            # already ``buy``/``sell``; map to ``long``/``short`` to match the
            # IB/Bybit rows.
            for p in raw_positions:
                size = _f(p.get("qty"))
                if size <= 0:
                    continue
                raw_side = str(p.get("side") or "").lower()
                side = "long" if raw_side in ("buy", "long") else "short"
                out.append({
                    "symbol": p.get("symbol"),
                    "side": side,
                    "size": size,
                    "entry_price": _f(p.get("avg_price")),
                    "unrealised_pnl": _f(p.get("unrealized_pnl")),
                })
            return out
        if ex == "oanda":
            # Dry oanda accounts (oanda_practice is dry_run today) are never
            # dialled from the read path — return None ("could not read")
            # rather than a false empty list, exactly like the IB/alpaca
            # branches. This is what makes the reverse reconciler skip OANDA
            # rows while dry (never close on a None snapshot) and gives it
            # real coverage the moment the account is promoted to live.
            mode = str(account.get("mode") or "live").lower()
            if mode != "live":
                return None
            client = oanda_client_for(account)
            if client is None:
                # No creds (OANDA_API_TOKEN / OANDA_ACCOUNT_ID unset) → can't
                # read; factory returns None.
                return None
            raw_positions = client.positions()
            if raw_positions is None:
                # Read failure — return None ("could not read") so the reverse
                # reconciler skips this account rather than false-closing live
                # rows on a transient outage (BL-20260622-ALPACA-SNAPSHOT-
                # FALSECLOSE). Forward protection for the live-promotion path.
                return None
            out = []
            # OandaClient.positions() emits
            # ``[{symbol, side, qty, avg_price, unrealized_pnl}]`` (side is
            # buy/sell). Normalise to the canonical
            # ``{symbol, side, size, entry_price, unrealised_pnl}`` shape with
            # side mapped to long/short, like the alpaca branch.
            for p in raw_positions:
                size = _f(p.get("qty"))
                if size <= 0:
                    continue
                raw_side = str(p.get("side") or "").lower()
                side = "long" if raw_side in ("buy", "long") else "short"
                out.append({
                    "symbol": p.get("symbol"),
                    "side": side,
                    "size": size,
                    "entry_price": _f(p.get("avg_price")),
                    "unrealised_pnl": _f(p.get("unrealized_pnl")),
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


# Integrations that expose a cheap, authoritative PER-SYMBOL open/flat check
# (distinct from the batch ``account_open_positions`` list). Today only Alpaca
# (``GET /v2/positions/{symbol}`` → 404=flat / 2xx=open). IB/OANDA read the
# whole portfolio in one call and have no per-symbol endpoint wired, so they
# keep the batch-LIST reconcile path unchanged (RISK-1 blast-radius bound).
_POSITION_PRESENCE_EXCHANGES: frozenset[str] = frozenset({"alpaca"})


def supports_position_presence(account: Optional[Dict[str, Any]]) -> bool:
    """True when *account*'s integration can positively confirm a SINGLE
    symbol's open/flat state via :func:`account_position_present`.

    Gate for the reconciler's per-symbol absence-close confirmation: where this
    is True the reconciler REQUIRES a broker-confirmed ``flat`` before closing a
    row that vanished from the batch snapshot; where it is False the existing
    2-observation batch-LIST behaviour is preserved (no regression for IB/OANDA,
    which have no per-symbol endpoint). Pure, never raises.
    """
    if not isinstance(account, dict):
        return False
    return str(account.get("exchange") or "").strip().lower() in _POSITION_PRESENCE_EXCHANGES


def account_position_present(
    account: Dict[str, Any], symbol: str
) -> Optional[bool]:
    """POSITIVE per-symbol open/flat confirmation for *symbol* on *account*.

    Three-valued (mirrors :meth:`AlpacaClient.position_present`):
      * ``True``  — the position is OPEN on the broker.
      * ``False`` — CONFIRMED FLAT (a broker 404 for the symbol).
      * ``None``  — could NOT confirm (dry account / missing creds / read
                    failure / **unsupported integration**).

    The reverse reconciler requires ``is False`` before closing a
    strategy-attributed row that is absent from the batch ``positions()``
    snapshot — so a partial/stale LIST (some rows visible, one omitted) or a
    transient read failure can no longer false-close a still-open position, and
    the ≥3 "reset" batch never amplifies one bad read into N false closes
    (RISK-1, BL-20260707-ALPACA-PAPER-NEGATIVE-EQUITY). ``None`` for any
    integration without a per-symbol endpoint (see
    :func:`supports_position_presence`), so callers gate on that first.
    """
    if not isinstance(account, dict) or not symbol:
        return None
    ex = (account.get("exchange") or "unknown").lower()
    try:
        if ex == "alpaca":
            # Dry accounts are never dialled from the read path (mirrors
            # account_open_positions) — can't/shouldn't confirm.
            mode = str(account.get("mode") or "live").lower()
            if mode != "live":
                return None
            client = alpaca_client_for(account)
            if client is None:
                return None
            return client.position_present(symbol)
        # No per-symbol presence endpoint wired for this integration.
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "account_position_present(%s, %s): %s",
            account.get("account_id") or "unknown", symbol, exc,
        )
        return None
