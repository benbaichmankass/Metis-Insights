"""Closed → exchange-flat invariant reconciler (S-067 follow-up #3).

**Tier 2 / live-order path.** This module ships in **alert-only**
mode for phase-1 — auto-flatten is filed for a follow-up sprint
gated on a per-account flag and one full week of clean alert-only
soak.

See ``docs/claude/closed-flat-invariant.md`` for the full design
memo, including rollout plan and trade-#1049 retrospective.

## Contract

For every ``trade_journal.db::trades`` row that flipped to
``status='closed'`` within the last ``window_seconds``, query the
exchange's open-position list for the matching account. If the
exchange still shows a non-zero position on the same symbol +
side, that's a contract violation:

* The DB says the trade is closed.
* The exchange says the position is still open.

Phase-1 response: log to
``runtime_logs/invariant_violations.jsonl`` and surface a
Telegram alert via ``outcomes.report``. Do NOT flatten the
position — the existing orphan-position reconciler is the
eventual safety net during phase-1 soak.

## Never-raise

This module follows the same never-raise contract as
``runtime_status.write_status``: a malformed account, a Bybit API
outage, or a corrupt JSONL append must NOT propagate up the tick
loop and crash the trader. Failures are caught + logged + skipped.
The orphan reconciler is the eventual safety net.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

_DEFAULT_VIOLATIONS_LOG = runtime_logs_dir() / "invariant_violations.jsonl"

# Anything <= this absolute residual qty is treated as exchange-flat.
# Bybit occasionally returns dust like 1e-9 on a fully-flattened
# position; the existing orphan reconciler uses the same threshold.
_DUST_THRESHOLD = 1e-8

DEFAULT_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class InvariantViolation:
    """One closed→exchange-flat violation."""

    detected_at: str       # ISO-8601 UTC
    trade_id: int
    account_id: str
    symbol: str
    db_status: str         # always 'closed' in phase-1
    exchange_qty: float    # signed; >0 means residual long, <0 short
    phase: str = "alert_only"


# ---------------------------------------------------------------------------
# Side normalisation (mirror of order_monitor._exchange_position_set)
# ---------------------------------------------------------------------------

_DB_SIDE_TO_CANONICAL = {
    "long": "long", "buy": "long",
    "short": "short", "sell": "short",
}
_EXCHANGE_SIDE_TO_CANONICAL = {
    "buy": "long", "long": "long",
    "sell": "short", "short": "short",
}


def _canonical_db_side(direction: Any) -> Optional[str]:
    if not isinstance(direction, str):
        return None
    return _DB_SIDE_TO_CANONICAL.get(direction.strip().lower())


def _canonical_exchange_side(side: Any) -> Optional[str]:
    if not isinstance(side, str):
        return None
    return _EXCHANGE_SIDE_TO_CANONICAL.get(side.strip().lower())


# ---------------------------------------------------------------------------
# Recently-closed query
# ---------------------------------------------------------------------------


def _fetch_recently_closed(
    db, *, cutoff_iso: str,
) -> List[dict[str, Any]]:
    """Query trades that flipped to status='closed' since *cutoff_iso*.

    Accepts a Database wrapper (with ``connect()``), a sqlite3
    Connection (passed through), or a path-like (str / Path).

    Uses ``COALESCE(op.updated_at, notes::closed_at, created_at)``
    precedence — same shape as the trades_closed router's closed-at
    fallback chain. Pre-S-030 trade rows didn't have an explicit
    close timestamp; the reconciler-close path stuffs it into the
    ``notes`` JSON.

    Returns a list of dicts: ``id``, ``account_id``, ``symbol``,
    ``direction``.
    """
    if isinstance(db, sqlite3.Connection):
        conn = db
        owned = False
    elif hasattr(db, "connect"):
        conn = db.connect()
        owned = True
    elif isinstance(db, (str, os.PathLike)):
        # S-CFI-FIX: only path-likes fall through to sqlite3.connect.
        # Anything else used to land here too, and sqlite3 happily
        # opened a database file at the object's repr() — leaving
        # zero-byte files like "<sqlite3.Connection object at 0x...>"
        # at whatever cwd the caller had. PR #658 leaked nine of
        # those into the repo root.
        conn = sqlite3.connect(os.fspath(db))
        owned = True
    else:
        raise TypeError(
            "closed_flat_invariant._fetch_recently_closed: db must be a "
            "sqlite3.Connection, a Database wrapper with .connect(), or "
            f"a path-like (str / os.PathLike); got {type(db).__name__}"
        )
    try:
        conn.row_factory = sqlite3.Row
        # Subquery handles the closed-at fallback. The notes JSON
        # extraction is sqlite3's json_extract — present on every
        # CPython 3.11 stdlib build.
        cur = conn.execute(
            """
            SELECT t.id, t.account_id, t.symbol, t.direction
            FROM trades t
            LEFT JOIN order_packages op ON op.linked_trade_id = t.id
            WHERE t.status = 'closed'
              AND COALESCE(t.is_backtest, 0) = 0
              AND datetime(
                    COALESCE(
                        op.updated_at,
                        json_extract(t.notes, '$.closed_at'),
                        t.created_at
                    )
                  ) >= datetime(?)
            """,
            (cutoff_iso,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# Exchange residual fetch
# ---------------------------------------------------------------------------


def _fetch_exchange_residual(
    account_resolver: Callable[[str], Any],
    account_id: str,
    symbol: str,
    canonical_side: str,
) -> float:
    """Return the signed residual qty for *symbol* on *account_id*.

    +qty = residual long, -qty = residual short, 0.0 = flat (or
    dust). Returns ``0.0`` on any account-resolution / fetch failure
    — never-raise contract.
    """
    account = account_resolver(account_id)
    if account is None:
        logger.warning(
            "closed_flat_invariant: account %s not resolvable (skipping)",
            account_id,
        )
        return 0.0
    fetcher = getattr(account, "open_positions", None)
    if fetcher is None:
        # Some account-builder shapes use ``account_open_positions(acc)``
        # instead. Try that as a fallback before giving up.
        try:
            from src.units.accounts.clients import account_open_positions
            positions = account_open_positions(account)
        except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort exchange fetch; the orphan reconciler is the safety net.
            logger.warning(
                "closed_flat_invariant: account_open_positions(%s) failed: %s",
                account_id, exc,
            )
            return 0.0
    else:
        try:
            positions = fetcher()
        except Exception as exc:  # noqa: BLE001  # allow-silent: best-effort exchange fetch; the orphan reconciler is the safety net.
            logger.warning(
                "closed_flat_invariant: %s.open_positions() failed: %s",
                account_id, exc,
            )
            return 0.0
    return _residual_from_positions(positions, symbol, canonical_side)


def _residual_from_positions(
    positions: Optional[Iterable[Any]],
    symbol: str,
    canonical_side: str,
) -> float:
    """Sum the residual qty for *symbol* + *canonical_side* across
    the position list."""
    if not positions:
        return 0.0
    total = 0.0
    for p in positions:
        if not isinstance(p, dict):
            continue
        if p.get("symbol") != symbol:
            continue
        if _canonical_exchange_side(p.get("side")) != canonical_side:
            continue
        try:
            qty = float(p.get("qty") or p.get("contracts") or 0)
        except (TypeError, ValueError):
            continue
        total += qty
    if abs(total) <= _DUST_THRESHOLD:
        return 0.0
    if canonical_side == "short":
        return -abs(total)
    return abs(total)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def check(
    db,
    account_resolver: Callable[[str], Any],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now: Optional[datetime] = None,
    violations_log: Optional[Path] = None,
    alerter: Optional[Callable[[str, str, dict], None]] = None,
) -> List[InvariantViolation]:
    """Run the invariant check; return any violations detected.

    Phase-1 response per violation:
      1. Append a structured row to *violations_log* (default
         ``runtime_logs/invariant_violations.jsonl``).
      2. Call *alerter* (default: ``outcomes.report``) so the
         operator gets a Telegram message.

    Never raises. Returns an empty list on internal failure.
    """
    try:
        now_dt = now or datetime.now(timezone.utc)
        cutoff = (now_dt - timedelta(seconds=int(window_seconds))).isoformat()
        try:
            rows = _fetch_recently_closed(db, cutoff_iso=cutoff)
        except sqlite3.Error as exc:
            logger.warning(
                "closed_flat_invariant: trades query failed: %s", exc,
            )
            return []
        violations: List[InvariantViolation] = []
        for row in rows:
            canonical = _canonical_db_side(row.get("direction"))
            if canonical is None:
                continue
            account_id = row.get("account_id") or ""
            symbol = row.get("symbol") or ""
            if not account_id or not symbol:
                continue
            residual = _fetch_exchange_residual(
                account_resolver, account_id, symbol, canonical,
            )
            if residual == 0.0:
                continue
            v = InvariantViolation(
                detected_at=now_dt.isoformat(),
                trade_id=int(row["id"]),
                account_id=account_id,
                symbol=symbol,
                db_status="closed",
                exchange_qty=float(residual),
            )
            violations.append(v)
        if violations:
            _emit_violations(violations, violations_log, alerter)
        return violations
    except Exception as exc:  # noqa: BLE001  # allow-silent: never-raise contract — the orphan reconciler is the eventual safety net; we must not crash the tick loop.
        logger.exception(
            "closed_flat_invariant: check() failed (suppressed): %s", exc,
        )
        return []


def _emit_violations(
    violations: List[InvariantViolation],
    violations_log: Optional[Path],
    alerter: Optional[Callable[[str, str, dict], None]],
) -> None:
    log_path = violations_log or _DEFAULT_VIOLATIONS_LOG
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            for v in violations:
                fh.write(json.dumps(asdict(v)) + "\n")
    except OSError as exc:
        logger.warning(
            "closed_flat_invariant: violations_log write failed: %s", exc,
        )

    fn = alerter or _default_alerter()
    if fn is None:
        return
    for v in violations:
        try:
            fn(
                "closed_flat_invariant",
                f"trade #{v.trade_id} closed in DB but {v.symbol} has "
                f"{v.exchange_qty:+g} open on {v.account_id}",
                asdict(v),
            )
        except Exception as exc:  # noqa: BLE001  # allow-silent: alerter must never crash the tick loop; violations_log is the durable record.
            logger.warning(
                "closed_flat_invariant: alerter failed: %s", exc,
            )


def _default_alerter() -> Optional[Callable[[str, str, dict], None]]:
    """Return the production alerter if available, else None."""
    try:
        from src.runtime.outcomes import Level, report

        def _alerter(channel: str, summary: str, payload: dict) -> None:
            report(channel, level=Level.WARN, reason=summary, **payload)

        return _alerter
    except Exception as exc:  # noqa: BLE001  # allow-silent: outcomes import is best-effort; violations still land in the JSONL log.
        logger.debug(
            "closed_flat_invariant: outcomes.report unavailable: %s", exc,
        )
        return None


def is_enabled() -> bool:
    """Return True if the env gate ``CLOSED_FLAT_INVARIANT_ENABLED``
    is set truthy. Default False — the wiring PR (separate Tier-2
    follow-up) will check this gate at the call site."""
    raw = os.environ.get("CLOSED_FLAT_INVARIANT_ENABLED", "false")
    return raw.strip().lower() in ("1", "true", "yes", "on")
