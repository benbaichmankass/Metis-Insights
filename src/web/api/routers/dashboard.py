"""S-014 — Dashboard data feed endpoints.

Exposes four read-only endpoints consumed by the Streamlit dashboard.
No authentication is required for GET requests — all data is operational
telemetry with no secrets. Restrict network-level access via firewall.

Contract note (S-061, ict-trading-bot#556): every optional field is
serialized as JSON ``null`` when the source value is missing. The
dashboard distinguishes "really 0" from "not measured" on this — fall-
through to a fabricated ``0`` or ``"unknown"`` here is a contract bug.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.utils.paths import runtime_logs_dir, trade_journal_db_path
from src.web.api._account_read_executor import run_account_read
from src.web.api._asset_class import asset_class_for_symbol
from src.web.api._clean_trades import (
    account_class_wire,
    exclude_reconciler_predicate,
    exclude_superseded_predicate,
    not_paper_predicate,
    paper_predicate,
)
from src.web.api._closed_at import close_time_sql

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["dashboard"])

# Canonical close-time basis for the rolling pnl24h window, epoch-ms-aware.
# The reconciler-filled close path writes ``closed_at`` as a raw epoch-ms
# string; an unguarded ``datetime(closed_at)`` returns NULL and drops the row
# from the 24h window (the "pnl24h wrongly $0 while lifetime is non-zero" bug).
# Mirrors /api/bot/performance + /api/bot/trades/closed — see _closed_at.py.
_PNL24H_CLOSE_TIME_SQL = close_time_sql("closed_at", "op.updated_at", "timestamp")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DB_PATH = Path(trade_journal_db_path())
# All runtime-log paths route through runtime_logs_dir() so DATA_DIR /
# RUNTIME_LOGS_DIR overrides apply consistently with the writers
# (heartbeat.py, signal_audit_logger.py, runtime_status.py — all
# migrated 2026-05-11). Reader/writer divergence here was the
# 2026-05-11 incident family (Signals tab blank, Bot Status stuck
# "stopped"); T2 closes it.
_AUDIT_LOG = runtime_logs_dir() / "signal_audit.jsonl"
_OUTCOMES_LOG = runtime_logs_dir() / "outcomes.jsonl"
_BOT_LOG = _REPO_ROOT / "bot.log"
_HEARTBEAT = runtime_logs_dir() / "heartbeat.txt"
_LOG_TAIL = 100
_SIGNAL_TAIL = 50

# Canonical log-level set the dashboard / Android Logs screen render as
# colour-coded tag chips. Anything outside this set is normalised below.
_LOG_LEVELS = ("info", "warn", "error", "trade")

# trades.direction values seen in the wild and their dashboard-side
# wire equivalents. The dashboard's Position type expects
# "buy"/"sell"; the DB column historically stores "long"/"short".
_SIDE_MAP = {"buy": "buy", "sell": "sell", "long": "buy", "short": "sell"}


def _normalise_side(direction: Any) -> str:
    if not isinstance(direction, str):
        return str(direction or "")
    return _SIDE_MAP.get(direction.strip().lower(), direction.strip().lower())


# Paper / not-paper SQL predicates + the account_class wire helper come from
# the canonical src.web.api._clean_trades module (single source of truth — the
# split logic was duplicated across 8 routers and drifted). P4 of the live-trade
# management contract keeps real and paper performance strictly separate
# (operator directive: never blend), so the paper-side aggregates on ``/stats``
# use _PAPER_PREDICATE while the real-money top-level block keeps the NOT-paper
# one. ``trades`` is unaliased in the stats query → bare-column predicates.
_NOT_PAPER_PREDICATE = not_paper_predicate("")
_PAPER_PREDICATE = paper_predicate("")
# Drop reconciler ``orphan_adopt`` artifacts from the KPI aggregates so
# /stats agrees with /performance (both now exclude them).
_EXCLUDE_RECONCILER = exclude_reconciler_predicate("")
# Drop superseded phantom orphan-flap duplicates (orphan-flap hardening #5).
_EXCLUDE_SUPERSEDED = exclude_superseded_predicate("")
_account_class_wire = account_class_wire


# ---------------------------------------------------------------------------
# Broker-truth unrealised PnL for open positions (2026-06-07)
# ---------------------------------------------------------------------------
#
# ``trades.pnl`` is the REALISED PnL column — filled in only on close.
# Pre-this-PR /positions returned ``COALESCE(pnl, 0)`` for every open
# row, so the dashboard always rendered $0.00 against a live trade.
# The bot's monitor never wrote a per-tick mark/unrealised back to the
# DB (that would put a write on every pipeline tick across every open
# trade — not free).
#
# Fix: at read time, ask the broker. Bybit's /v5/position/list and IB's
# accountSummary already return unrealised_pnl per open position;
# ``src.units.accounts.clients.account_open_positions`` normalises both.
# Match each DB row to the broker's view by (account_id, symbol, side).
# Short TTL cache (10 s) so dashboard poll loops don't hammer Bybit.
#
# Fallback when the broker call fails or returns no matching position:
# leave ``unrealizedPnl=None`` so the renderer treats it as "not
# measured" (Position-shape contract — null != 0). The dashboard's
# existing ``_position_upnl`` client-side fallback computes from the
# last candle close when the API returns null.

# Broker open-position cache for the /positions uPnL enrichment. The TTL was
# 10s, but every consumer (dashboard + Android app + /ws/market) polls
# /positions on a ~30s cadence — LONGER than a 10s TTL — so the cache was COLD
# on every poll and each poll re-opened a broker read per account (an IB read
# client for ib_paper, a Bybit REST call for the others). That put needless,
# repeated load on the IB gateway and made the endpoint slow enough that mobile
# clients hit their read timeout and rendered an empty positions list
# (android-live-trades-blank). A 30s default keeps the cache warm across the
# poll so the gateway is hit at most ~once per TTL per account regardless of how
# many consumers poll. Env-tunable via POSITIONS_CACHE_TTL_S (read at call time
# so the live VM can retune without a redeploy).
_POSITIONS_TTL_DEFAULT_S = 30.0
# On a broker read FAILURE, keep serving the last GOOD position list for up to
# this window instead of dropping straight to "unavailable". This (a) stops a
# transient IB gateway wedge / Bybit blip from blanking the broker-truth uPnL,
# and (b) means a genuinely-wedged gateway is retried at most once per TTL
# (stale served in between) rather than re-hit every request. After the window
# elapses with no good read we honestly return None ("not measured").
_POSITIONS_STALE_OK_DEFAULT_S = 120.0
_BROKER_POSITIONS_CACHE: dict[str, tuple[float, Any]] = {}
# Last SUCCESSFUL (non-None) read per account — the stale-serve fallback source.
_BROKER_POSITIONS_LAST_GOOD: dict[str, tuple[float, Any]] = {}


def _positions_ttl_s() -> float:
    """Broker-positions cache TTL (seconds), env-tunable via
    ``POSITIONS_CACHE_TTL_S``. Falls back to the 30s default on unset/garbage/
    non-positive so a bad value can never disable caching (which would restore
    the gateway-hammering it exists to prevent)."""
    try:
        v = float(os.environ.get("POSITIONS_CACHE_TTL_S", ""))
        return v if v > 0 else _POSITIONS_TTL_DEFAULT_S
    except (TypeError, ValueError):
        return _POSITIONS_TTL_DEFAULT_S


def _positions_stale_ok_s() -> float:
    """How long a failed read may serve the last good positions, env-tunable via
    ``POSITIONS_CACHE_STALE_OK_S``. ``0`` disables stale-serve; garbage/negative
    falls back to the 120s default."""
    try:
        v = float(os.environ.get("POSITIONS_CACHE_STALE_OK_S", ""))
        return v if v >= 0 else _POSITIONS_STALE_OK_DEFAULT_S
    except (TypeError, ValueError):
        return _POSITIONS_STALE_OK_DEFAULT_S


def _broker_positions_for(account_id: str) -> Any:
    """Cached fetch of the broker's open-position list for an account.

    Returns the list ``account_open_positions`` produced, or ``None``
    on read failure / unknown account. Sentinel ``None`` is cached
    too — a logged-out IB Gateway or a bad Bybit key shouldn't be
    retried for every row in a positions response.

    Resilience (2026-07-14): on a read failure the last SUCCESSFUL list is
    served for up to :func:`_positions_stale_ok_s` so a transient gateway wedge
    neither blanks the broker-truth uPnL nor re-hits the wedged gateway more
    than once per TTL.
    """
    now = time.monotonic()
    ttl = _positions_ttl_s()
    cached = _BROKER_POSITIONS_CACHE.get(account_id)
    if cached is not None and (now - cached[0]) < ttl:
        return cached[1]
    try:
        # Lazy import — keeps the router module load cheap and avoids
        # dragging the accounts / exchange clients into every web-api
        # request that doesn't hit /positions.
        from src.runtime.order_monitor import _load_account_cfgs_for_reconcile
        from src.units.accounts.clients import account_open_positions
        cfg_map = _load_account_cfgs_for_reconcile()
        cfg = cfg_map.get(str(account_id))
        positions = account_open_positions(cfg) if cfg is not None else None
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort enrichment of a Tier-1 read endpoint — spans lazy imports + broker SDK calls (Bybit ccxt / IB ib_insync) whose failure modes are open-ended; a thrown exception here would 500 /api/bot/positions and break every dashboard surface that depends on it. The downstream consumer treats the None sentinel as "broker unavailable" and falls back to a computed mark-price PnL.
        logger.warning(
            "dashboard: broker-positions fetch failed for %s: %s",
            account_id, exc,
        )
        positions = None

    if positions is not None:
        # Good read — refresh both the serve cache and the last-good store.
        _BROKER_POSITIONS_CACHE[account_id] = (now, positions)
        _BROKER_POSITIONS_LAST_GOOD[account_id] = (now, positions)
        return positions

    # Read failed (None). Prefer the last good list within the stale window so a
    # transient wedge doesn't blank broker-truth uPnL; cache it for one TTL so
    # the wedged gateway is retried at most once per TTL, not every request.
    # Staleness is measured from the last GOOD read (not this retry), so total
    # served-stale time is bounded by the window regardless of retry count.
    stale_ok = _positions_stale_ok_s()
    last_good = _BROKER_POSITIONS_LAST_GOOD.get(account_id)
    if stale_ok > 0 and last_good is not None and (now - last_good[0]) < stale_ok:
        _BROKER_POSITIONS_CACHE[account_id] = (now, last_good[1])
        return last_good[1]

    _BROKER_POSITIONS_CACHE[account_id] = (now, None)
    return None


# Side normalisation for the broker→DB match. Both sides have used
# multiple conventions over time:
#   * trades.direction: "long" / "short" / "buy" / "sell"
#   * Bybit /position/list "side": "Buy" / "Sell" (sometimes "" on flat)
#   * IB / binance via account_open_positions: "long" / "short"
# Reduce everything to {"long", "short"} for matching.
_BROKER_SIDE_TO_LONG_SHORT = {
    "buy": "long", "long": "long",
    "sell": "short", "short": "short",
}


def _to_long_short(side: Any) -> str:
    if not isinstance(side, str):
        return ""
    return _BROKER_SIDE_TO_LONG_SHORT.get(side.strip().lower(), "")


def _broker_unrealised_for_trade(
    account_id: Any, symbol: Any, direction: Any, qty: Any = None,
) -> tuple[Any, str]:
    """Return ``(unrealised_pnl, source)``.

    ``source`` is one of:
      * ``"broker"`` — broker returned a matching position; value is
        whatever it reported (may be ``0.0`` if price is at exact entry).
      * ``"unavailable"`` — broker read failed (None) OR no matching
        position. Value is ``None`` so the renderer treats it as "not
        measured" and the dashboard's client-side ``_position_upnl``
        fallback computes from the last candle close. Per CLAUDE.md
        Position-shape contract: null != 0.

    Netted-position pro-rating (2026-07-04, operator-reported XRPUSDT
    double count): brokers with one-way position mode (Bybit) report ONE
    netted position per (symbol, side), but the journal can hold SEVERAL
    open trade rows sharing it (two order packages -> two rows -> one
    exchange position). Stamping the position's FULL ``unrealised_pnl``
    onto every row made each row display — and any consumer sum count —
    the whole position once per row. When the caller passes the row's
    ``qty`` and it is smaller than the broker position ``size``, the
    value is pro-rated by the row's share (``upnl * qty / size``, capped
    at 1.0x so a stale over-sized row can never inflate). Source stays
    ``"broker"`` — the number is broker-measured, scaled by the row's
    declared share.
    """
    if not account_id or not symbol or not direction:
        return None, "unavailable"
    positions = _broker_positions_for(str(account_id))
    if not positions:  # None (read fail) or [] (no positions)
        return None, "unavailable"
    want_side = _to_long_short(direction)
    want_symbol = str(symbol).upper()
    for p in positions:
        if str(p.get("symbol", "")).upper() != want_symbol:
            continue
        if _to_long_short(p.get("side")) != want_side:
            continue
        upnl = p.get("unrealised_pnl")
        if upnl is None:
            return None, "unavailable"
        try:
            value = float(upnl)
            try:
                q = float(qty) if qty is not None else None
                size = float(p.get("size")) if p.get("size") is not None else None
            except (TypeError, ValueError):
                q = size = None
            if q is not None and size is not None and size > 0 and q > 0:
                share = min(q / size, 1.0)
                # Only scale when the row genuinely holds a sub-share;
                # a lone row matching the whole position (share ~1)
                # keeps the exact broker number.
                if share < 0.999:
                    value *= share
            return round(value, 2), "broker"
        except (TypeError, ValueError):
            return None, "unavailable"
    return None, "unavailable"


def _local_unrealised_for_trade(
    *, symbol: Any, direction: Any, entry_price: Any, qty: Any,
) -> tuple[Any, str]:
    """Mark-to-market unrealised PnL when the broker can't provide it.

    ``(round(pnl, 2), "markprice_local")`` from the last market close ×
    contract multiplier, or ``(None, "unavailable")`` when no mark is
    available (keeps the null-not-zero contract). Best-effort.
    """
    try:
        from src.runtime.local_pnl import (
            compute_unrealized_pnl,
            contract_value_usd_for,
            last_mark_price,
        )
        mark = last_mark_price(symbol)
        if not mark or mark <= 0:
            return None, "unavailable"
        pnl = compute_unrealized_pnl(
            entry_price=entry_price, mark_price=mark, qty=qty,
            direction=direction,
            contract_value_usd=contract_value_usd_for(symbol),
        )
        if pnl is None:
            return None, "unavailable"
        return round(float(pnl), 2), "markprice_local"
    except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort mark-to-market enrichment of a Tier-1 read endpoint; spans a lazy import + a (possibly blocking IBKR) candle fetch whose failure modes are open-ended. Returns the null-not-zero sentinel so /api/bot/positions never 500s — the renderer treats null as "not measured", exactly the broker-unavailable fallback contract.
        logger.warning(
            "dashboard: local unrealised compute failed for %s: %s",
            symbol, exc,
        )
        return None, "unavailable"


def _resolve_position_pnl(
    account_id: Any, symbol: Any, direction: Any, qty: Any, entry_price: Any,
) -> tuple[Any, str]:
    """Broker-truth unrealised PnL, falling back to the local mark-to-market
    compute — bundles :func:`_broker_unrealised_for_trade` and
    :func:`_local_unrealised_for_trade` into one synchronous unit so a single
    ``/api/bot/positions`` row is resolved with exactly one executor
    round-trip (see ``get_positions`` below).

    Both halves can reach a blocking, event-loop-driving IB call (the broker
    path via ``account_open_positions``; the local-fallback path via
    ``last_mark_price`` -> ``fetch_candles`` for an IB-routed symbol like
    MES/MGC/MHG) — see ``src.web.api._account_read_executor`` for why this
    must run off uvicorn's event-loop thread (BL-20260706-IBCONCURRENCY).
    """
    upnl, upnl_source = _broker_unrealised_for_trade(account_id, symbol, direction, qty)
    if upnl is None and upnl_source == "unavailable":
        upnl, upnl_source = _local_unrealised_for_trade(
            symbol=symbol, direction=direction, entry_price=entry_price, qty=qty,
        )
    return upnl, upnl_source


def _bot_status() -> str:
    from src.runtime.heartbeat import heartbeat_label  # local import keeps router cheap
    if not _HEARTBEAT.exists():
        return "stopped"
    age = time.time() - _HEARTBEAT.stat().st_mtime
    # Thresholds derived from TICK_INTERVAL_SECONDS — see
    # src/runtime/heartbeat.py for the running/paused/stopped convention
    # (matches scripts/check_heartbeat.py grace factor of 2.0).
    return heartbeat_label(age)


# S-067 follow-up #9: vm_health implementation moved to
# src/web/api/_vm_health.py to remove the diag.py / dashboard.py
# fork. Re-exported under the legacy ``_vm_health`` name so
# tests (e.g. tests/test_dashboard_data_contract.py monkeypatching
# ``dashboard_router._vm_health``) keep working without modification.
from src.web.api._vm_health import vm_health as _vm_health  # noqa: E402


def _pnl_stats_for(predicate: str) -> tuple[float, float, int, float]:
    """Returns (pnl24h, totalPnL, openTrades, winRate) over the rows matching
    *predicate* (the funding-class filter — ``_NOT_PAPER_PREDICATE`` for
    real-money, ``_PAPER_PREDICATE`` for paper). Real and paper are never
    blended into one number (operator directive, P4).

    Raises ``sqlite3.Error`` on a structural DB failure (missing
    table / column, locked DB, corrupt file). The early-return-zeroes
    branch fires only when the DB file genuinely does not exist —
    that's a legitimate "no trades yet" case on a fresh install,
    distinct from "DB present but unreadable". ``get_stats`` catches
    the raised error and surfaces it as a 503 so the dashboard renders
    a real outage badge instead of fabricated zero metrics.
    """
    if not _DB_PATH.exists():
        return 0.0, 0.0, 0, 0.0
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                -- Canonical "closed trade" basis: status='closed' (matches
                -- /performance, /trades/closed, /pnl/history). Previously this
                -- used status!='open', which also counted non-closed terminal
                -- rows (cancelled/error) — a divergence from every other
                -- endpoint. openTrades stays status='open'.
                --
                -- pnl24h is a ROLLING 24h window on CLOSE-TIME
                -- (COALESCE(closed_at, op.updated_at, timestamp)) — the SAME
                -- basis /performance?window=24h uses, so the two never
                -- disagree. It previously keyed on substr(created_at,1,10)=today
                -- (the trade's OPEN calendar-day), which reported $0.00 for a
                -- trade closed in the last 24h but opened earlier / before the
                -- UTC midnight roll (the "24h P&L is wrongly 0" bug). The
                -- order_packages LEFT JOIN is pre-aggregated to one row per
                -- trade (MIN(updated_at)) so the close-time fallback matches
                -- /performance and the join can't fan-out the sums.
                SELECT
                    COALESCE(SUM(CASE WHEN status='closed' AND pnl IS NOT NULL
                        AND {_PNL24H_CLOSE_TIME_SQL}
                            >= datetime('now','-24 hours')
                        THEN pnl ELSE 0 END),0),
                    COALESCE(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END),0),
                    COUNT(CASE WHEN status='open' THEN 1 END),
                    COUNT(CASE WHEN status='closed' AND pnl IS NOT NULL THEN 1 END),
                    COUNT(CASE WHEN status='closed' AND pnl>0 THEN 1 END)
                FROM trades
                LEFT JOIN (
                    SELECT linked_trade_id, MIN(updated_at) AS updated_at
                    FROM order_packages
                    WHERE linked_trade_id IS NOT NULL
                    GROUP BY linked_trade_id
                ) op ON op.linked_trade_id = trades.id
                WHERE COALESCE(is_backtest,0)=0
                """
                + predicate
                + _EXCLUDE_RECONCILER
                + _EXCLUDE_SUPERSEDED,
            )
            row = cur.fetchone()
        except sqlite3.Error:
            # S-067: structural failures (missing column, locked DB,
            # corrupt file) used to be silently swallowed under a
            # blanket ``except Exception`` and surfaced to the
            # dashboard as fabricated `(0, 0, 0, 0)`. Log loudly and
            # re-raise so ``get_stats`` can convert to 503.
            logger.exception("dashboard: _pnl_stats sqlite read failed")
            raise
        pnl24h, total_pnl, open_trades, closed, winners = row
        # win-rate denominator is RESOLVED closed trades only (pnl IS NOT NULL),
        # matching /api/bot/performance (performance.py: `AND t.pnl IS NOT NULL`).
        # A closed trade with NULL pnl (reconciler-incomplete: the broker
        # close-pnl lookup failed) carries no win/loss signal, so counting it in
        # the denominator silently deflated the rate — stats read 6.3% while
        # /performance read 25.6% over the same real-money trades (the unresolved
        # rows, not real losses; live diag 2026-06-19). winners (pnl>0) already
        # excludes NULL. The volume of NULL-pnl real-money rows is a separate
        # data-quality follow-up for /health-review.
        win_rate = (winners / closed * 100.0) if closed else 0.0
        return float(pnl24h), float(total_pnl), int(open_trades), round(win_rate, 1)
    finally:
        conn.close()


def _pnl_stats() -> tuple[float, float, int, float]:
    """Real-money (non-paper) ``(pnl24h, totalPnL, openTrades, winRate)``.

    Back-compat wrapper around :func:`_pnl_stats_for` — the top-level
    ``/stats`` block has always excluded paper rows, so this keeps that exact
    behaviour (and signature) for any other caller. The paper-side aggregates
    are computed separately in :func:`get_stats` via ``_PAPER_PREDICATE``.
    """
    return _pnl_stats_for(_NOT_PAPER_PREDICATE)


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: was silently `return []`. Keep the
        # empty-list shape (the dashboard's logs/signals panels
        # branch on length and want to render a "no entries" stub
        # rather than blow up) but log so the next debugging
        # session sees the underlying read failure.
        logger.warning(
            "dashboard: tail_jsonl(%s) read failed: %s: %s",
            path, type(exc).__name__, exc,
        )
        return []
    return [json.loads(raw) for raw in lines[-n:] if raw.strip()]


def _tail_plain_log(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError as exc:
        # S-067 borderline: same shape as _tail_jsonl above.
        logger.warning(
            "dashboard: tail_plain_log(%s) read failed: %s: %s",
            path, type(exc).__name__, exc,
        )
        return []
    entries = []
    for line in raw_lines[-n:]:
        line = line.rstrip()
        if not line:
            continue
        level: str = "info"
        llow = line.lower()
        if "error" in llow:
            level = "error"
        elif "warn" in llow:
            level = "warn"
        elif "trade" in llow or "order" in llow or "filled" in llow:
            level = "trade"
        entries.append(
            {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "message": line,
            }
        )
    return entries


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    try:
        pnl24h, total_pnl, open_trades, win_rate = _pnl_stats_for(
            _NOT_PAPER_PREDICATE
        )
        # P4 (live-trade management contract): real and paper performance are
        # kept strictly separate (operator directive — never blend). The
        # top-level block stays real-money-only (unchanged); the paper-side
        # aggregates ride additively in a ``paper`` sub-block so a consumer
        # renders them as their own section, and the real ``openTrades`` KPI is
        # accompanied by a distinct ``paperOpenTrades`` count rather than a
        # merged number.
        p_pnl24h, p_total, p_open, p_win = _pnl_stats_for(_PAPER_PREDICATE)
    except sqlite3.Error as exc:
        # S-067: the DB is reachable-but-broken. Surface a real outage
        # rather than a fabricated `pnl24h: 0` that an operator would
        # read as "no trades today".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "stats_unavailable",
                "reason": f"db error: {type(exc).__name__}",
            },
        )
    return {
        "pnl24h": round(pnl24h, 2),
        "totalPnL": round(total_pnl, 2),
        "openTrades": open_trades,
        "winRate": win_rate,
        "status": _bot_status(),
        "datasource": "live",
        "vmHealth": _vm_health(),
        # Distinct paper open-count alongside the real-money KPI (never merged).
        "paperOpenTrades": p_open,
        # Full paper-side aggregate block (same shape as the real-money top
        # level) — separate so nothing real+paper is ever summed together.
        "paper": {
            "pnl24h": round(p_pnl24h, 2),
            "totalPnL": round(p_total, 2),
            "openTrades": p_open,
            "winRate": p_win,
        },
    }


def _classify_level(raw: Any) -> str:
    """Map a heterogeneous source token to a canonical log level.

    Audit rows carry the decision in ``result`` (``buy``/``sell``/…) rather
    than a syslog level, and outcomes rows carry ``critical`` — both of
    which the old endpoint collapsed to ``info`` (the "only INFO ever
    shows" bug). Signals map to ``trade``; critical folds into ``error``.
    """
    s = str(raw or "").strip().lower()
    if s in ("buy", "sell", "long", "short", "trade", "order", "fill", "filled"):
        return "trade"
    if s in ("warn", "warning"):
        return "warn"
    if s in ("error", "err", "critical", "crit", "fatal", "exception"):
        return "error"
    if s in _LOG_LEVELS:
        return s
    return "info"


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` / offset / naive) to aware UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _audit_to_entry(e: dict) -> dict[str, Any]:
    ts = e.get("ts", e.get("timestamp", datetime.now(timezone.utc).isoformat()))
    return {
        "id": e.get("id", str(uuid.uuid4())),
        "timestamp": ts,
        "level": _classify_level(e.get("level", e.get("result"))),
        "message": e.get("message", e.get("msg", json.dumps(e))),
        "source": "pipeline",
    }


def _outcome_to_entry(o: dict) -> dict[str, Any]:
    action = o.get("action", "")
    status = o.get("status", "")
    reason = o.get("reason") or ""
    head = " ".join(p for p in (str(action), str(status)) if p).strip()
    message = f"{head}: {reason}".strip(": ").strip() or json.dumps(o)
    return {
        "id": o.get("id", str(uuid.uuid4())),
        "timestamp": o.get("ts", o.get("timestamp", datetime.now(timezone.utc).isoformat())),
        "level": _classify_level(o.get("level")),
        "message": message,
        "source": "outcome",
    }


@router.get("/logs")
def get_logs(
    limit: int = Query(_LOG_TAIL, ge=1, le=1000),
    since: str | None = Query(None, description="ISO-8601 UTC cutoff (oldest kept)"),
    level: str | None = Query(None, description="CSV of levels to keep (info,warn,error,trade)"),
) -> list[dict[str, Any]]:
    """Merged, newest-first log feed for the dashboard / Android Logs screen.

    Sources, merged then sorted by timestamp:
      - ``signal_audit.jsonl`` — pipeline events (signals surface as
        ``trade``, everything else ``info``); falls back to ``bot.log``
        when the audit log is empty.
      - ``outcomes.jsonl`` — operator WARN/ERROR/CRITICAL outcomes
        (``critical`` → ``error``). This is what was missing before, so
        the non-INFO tag chips never matched anything.

    Params: ``limit`` (1..1000), ``since`` (drop older rows — drives the
    app's 24h/7d time-frames), ``level`` (CSV filter). All optional;
    no params ≈ the previous newest-100 behaviour, just with real levels.
    """
    rows: list[dict[str, Any]] = []
    audit = _tail_jsonl(_AUDIT_LOG, max(limit, _LOG_TAIL))
    if audit:
        rows.extend(_audit_to_entry(e) for e in audit)
    else:
        rows.extend(_tail_plain_log(_BOT_LOG, _LOG_TAIL))
    rows.extend(_outcome_to_entry(o) for o in _tail_jsonl(_OUTCOMES_LOG, max(limit, _LOG_TAIL)))

    # Optional level filter (CSV, normalised the same way as the rows).
    if level:
        wanted = {_classify_level(tok) for tok in level.split(",") if tok.strip()}
        if wanted:
            rows = [r for r in rows if r["level"] in wanted]

    # Optional since cutoff — rows with an unparseable ts are kept (better
    # to over-show than silently drop on a malformed timestamp).
    since_dt = _parse_ts(since)
    if since_dt is not None:
        rows = [r for r in rows if (_parse_ts(r["timestamp"]) or since_dt) >= since_dt]

    # Newest first; rows with an unparseable ts sort oldest (epoch).
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    rows.sort(key=lambda r: _parse_ts(r["timestamp"]) or _epoch, reverse=True)
    return rows[:limit]


def _fetch_open_position_rows(effective_include: bool) -> list:
    """Blocking open-positions read, isolated so it can run in a worker thread.

    Runs off uvicorn's event loop via ``asyncio.to_thread`` (see
    ``get_positions``): a synchronous sqlite read in an ``async`` route
    starves every concurrent request while the loop is blocked
    (RISK-3, BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB). Best-effort:
    returns ``[]`` on a missing DB or read error.
    """
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        try:
            # A locked DB waits up to 3s rather than raising immediately.
            conn.execute("PRAGMA busy_timeout=3000")
            cur = conn.cursor()
            # ``pnl`` (realised) is no longer projected into the response
            # — for open rows it is NULL and pre-this-PR the COALESCE
            # to 0 made the dashboard render $0.00 against live trades.
            # ``unrealizedPnl`` is now sourced from the broker
            # (Bybit/IB unrealised_pnl), with ``None`` as the honest
            # fallback when the broker call fails or there is no
            # matching position.
            sql = """
                SELECT id, account_id, symbol, direction, position_size,
                       entry_price, created_at,
                       stop_loss, take_profit_1, strategy_name,
                       COALESCE(is_demo, 0), account_class, notes
                FROM trades
                WHERE status = 'open'
                  AND COALESCE(is_backtest, 0) = 0
            """
            if not effective_include:
                sql += _NOT_PAPER_PREDICATE
            sql += " ORDER BY created_at DESC LIMIT 50"
            cur.execute(sql)
            return cur.fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("dashboard: /positions sqlite read failed")
        return []


@router.get("/positions")
async def get_positions(
    include_paper: bool = Query(False),
    include_demo: bool = Query(False),
) -> list[dict[str, Any]]:
    """Open positions (real-money by default). Each row carries
    ``accountClass`` ("paper" | "real_money") plus the legacy ``isDemo``
    flag; pass ``include_paper=true`` to also include paper-account
    positions alongside real-money. ``include_demo`` is a deprecated alias
    for ``include_paper`` (effective include = include_paper OR include_demo).
    """
    effective_include = include_paper or include_demo
    # Offload the blocking sqlite read to a worker thread so it never runs on
    # uvicorn's event loop (this route stays async for the awaited broker read
    # below). RISK-3 / BL-20260707-HEALTHAPI-ACCTBAL-BLOCKING-DB.
    rows = await asyncio.to_thread(_fetch_open_position_rows, effective_include)
    out: list[dict[str, Any]] = []
    for r in rows:
        # Offloaded to the dedicated single-worker account-read executor
        # (BL-20260706-IBCONCURRENCY): both the broker-truth read and its
        # local mark-to-market fallback can drive a blocking, event-loop-
        # owning IB call, which is unsafe to run directly on this coroutine's
        # thread (uvicorn's already-running loop) — see
        # src.web.api._account_read_executor for the incident writeup. The
        # 10s per-account/per-symbol caches inside each half keep this cheap
        # under repeated polling.
        #
        # Server-side mark-to-market fallback (2026-06-16): when the broker
        # read is unavailable (logged-out IB Gateway, no matching position),
        # compute unrealised PnL from the last market close × the contract
        # multiplier so IBKR/paper open positions show a real number instead
        # of $0.00. The client-side candle fallback was multiplier-blind for
        # futures (MGC=10× / MHG=2500×); this is computed where the canonical
        # contract_value_usd lives. Marked with a distinct source.
        upnl, upnl_source = await run_account_read(
            _resolve_position_pnl, r[1], r[2], r[3], r[4], r[5],
        )
        # Options-expression rows carry a structure block in notes.options
        # (legs / strikes / defined risk). Surface it as a nested ``options``
        # object so the dashboard + Android can render the spread; null for a
        # plain equity/futures/crypto row. Connection-free — decision-time
        # geometry only (per-leg live greeks/PnL are a documented follow-up).
        options_block = None
        try:
            notes_raw = r[12]
            if notes_raw:
                decoded = json.loads(notes_raw)
                if isinstance(decoded, dict) and isinstance(decoded.get("options"), dict):
                    options_block = decoded["options"]
        except (json.JSONDecodeError, TypeError, ValueError):
            options_block = None
        out.append({
            "id": str(r[0]),
            "account": r[1],
            "symbol": r[2],
            "side": _normalise_side(r[3]),
            "qty": r[4],
            "entryPrice": r[5],
            "unrealizedPnl": upnl,
            "unrealizedPnlSource": upnl_source,
            "openedAt": r[6],
            "stopLoss": float(r[7]) if r[7] is not None else None,
            "takeProfit": float(r[8]) if r[8] is not None else None,
            "pattern": r[9] if r[9] else None,
            "isDemo": bool(r[10]),
            "accountClass": _account_class_wire(r[11], r[10]),
            # ``assetClass`` ("crypto"|"index"|"commodity"|"bond"|"equity"|
            # "fx"|"unknown") — coarse reporting bucket for the symbol, so a
            # consumer can group/filter positions by asset group without a
            # symbol map of its own. Reporting-only (never the order path);
            # config-driven via config/instruments.yaml with a heuristic
            # fallback. Never null (worst case "unknown").
            "assetClass": asset_class_for_symbol(r[2]),
            "options": options_block,
        })
    return out


@router.get("/signals")
def get_signals() -> list[dict[str, Any]]:
    raw = _tail_jsonl(_AUDIT_LOG, _SIGNAL_TAIL)
    out = []
    for e in raw:
        side = str(e.get("side", e.get("direction", ""))).lower()
        if side not in ("buy", "sell", "long", "short"):
            continue
        # Pass through missing fields as None — the dashboard treats
        # null as "not provided by the writer" and renders accordingly,
        # versus 0/"unknown" which it would render as a real value.
        # Writer-side fix lives in src/runtime/pipeline.py log_signal().
        pattern = e.get("pattern")
        if pattern in (None, ""):
            pattern = e.get("signal_type")
        confidence = e.get("confidence")
        if confidence is None:
            confidence = e.get("score")
        # The pipeline writes the entry price under any of three field
        # names depending on the call site (src/runtime/pipeline.py:218,
        # :524, :1142). Cover all three so the dashboard never sees a
        # spurious None just because the writer chose a different alias.
        price = e.get("price")
        if price is None:
            price = e.get("entry_price")
        if price is None:
            price = e.get("entry")
        out.append(
            {
                "id": e.get("id", str(uuid.uuid4())),
                "timestamp": e.get("ts", e.get("timestamp", "")),
                "symbol": e.get("symbol", "BTCUSDT"),
                "side": side,
                # ``strategy`` lets the dashboard's overview chart offer
                # per-strategy signal toggles. Nullable: older rows /
                # non-strategy events serialize as null, which the
                # dashboard treats as "always show".
                "strategy": e.get("strategy"),
                "pattern": pattern,
                "confidence": confidence,
                "price": price,
                # Decision geometry the strategy already computed (when
                # present in the audit record) so the chart can DRAW the
                # zones it traded on — never a separately-computed
                # indicator. Generic shape: each zone is {kind, ...}.
                "zones": _signal_zones(e),
            }
        )
    return out


def _signal_zones(e: dict[str, Any]) -> list[dict[str, Any]]:
    """Assemble drawable zones from the geometry a strategy logged.

    Reads ONLY values the signal builder already recorded (e.g. ict_scalp's
    fvg_low/high + sweep_level). Returns ``[]`` when no geometry is present.
    Extend by having a strategy log its own fields and mapping them here."""
    zones: list[dict[str, Any]] = []
    fvg_low = e.get("fvg_low")
    fvg_high = e.get("fvg_high")
    if isinstance(fvg_low, (int, float)) and isinstance(fvg_high, (int, float)):
        lo, hi = sorted((float(fvg_low), float(fvg_high)))
        zones.append({"kind": "fvg", "low": lo, "high": hi})
    sweep_level = e.get("sweep_level")
    if isinstance(sweep_level, (int, float)):
        zones.append({"kind": "sweep", "price": float(sweep_level)})
    return zones
