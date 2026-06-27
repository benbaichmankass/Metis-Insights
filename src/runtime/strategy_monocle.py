"""Strategy-monocle gate helpers — extracted from pipeline.py (PR-8 / D1).

One open package per strategy globally (``_has_open_package_for_strategy``)
and a short refusal cooldown after a ``sized_qty=0`` rejection
(``_recent_refusal_for_strategy``).  Both helpers are best-effort: a
DB-read failure returns ``None`` (i.e. "no gate active") rather than
blocking every dispatch on a transient SQLite hiccup.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default cooldown (seconds) after a strategy was internally refused
# (sized_qty=0 from RiskManager) before the dispatcher will re-attempt
# the same strategy. Tuned to one full 5 m candle so VWAP / turtle_soup
# get a fresh bar of market data before retrying — the most common
# transient cause of a sized_qty=0 refusal is Bybit V5 returning
# ``availableToBorrow=0`` for the borrow side of a spot-margin order
# (S-056 / S-058) and that field repopulates on the exchange's own
# cadence, not ours. Pre-fix the strategy_monocle gate only blocked
# on *open* packages, so a refused signal re-fired every minute and
# accumulated 20 ``status='rejected'`` rows over 1 h on 2026-05-10
# (per the trade-journal evidence FU-20260510-002 originally
# mislabelled as a 170131 cluster). Operator override via
# ``STRATEGY_REFUSAL_COOLDOWN_SECONDS`` in the systemd unit.
_DEFAULT_REFUSAL_COOLDOWN_SECONDS = 300


def _refusal_cooldown_seconds() -> int:
    raw = os.environ.get("STRATEGY_REFUSAL_COOLDOWN_SECONDS")
    if raw is None:
        return _DEFAULT_REFUSAL_COOLDOWN_SECONDS
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_REFUSAL_COOLDOWN_SECONDS
    return v if v >= 0 else _DEFAULT_REFUSAL_COOLDOWN_SECONDS


def _has_open_package_for_strategy(
    strategy_name: Optional[str], symbol: Optional[str] = None
) -> Optional[str]:
    """Strategy-monocle gate: return the order_package_id of an existing
    open package for *strategy_name* (scoped to *symbol* when given), or
    ``None`` when no open package exists.

    Operator directive 2026-05-03: a strategy may have **one** open
    package globally — across all accounts that follow it. Once a
    package is logged, the strategy's job is to monitor + update
    that package via ``order_monitor`` until SL/TP hits or the
    strategy decides to close (PRs 2 + 3 of this sprint wire the
    close path).

    Multi-symbol (2026-05-22): "one open package per strategy" is
    **per instrument**. Pass ``symbol`` so an open BTCUSDT package
    does not suppress an MES entry for the same strategy (and vice
    versa). When ``symbol`` is None the query keeps its legacy
    strategy-global scope (single-symbol callers / tests).

    Best-effort — a DB-read failure returns ``None`` (i.e. "no open
    package known"), which means the dispatcher proceeds. The risk
    is creating one extra duplicate package in the DB-read failure
    window; the alternative (refusing the dispatch on every
    DB-read failure) trades a real bug for a hypothetical one.

    The strategy_name is read from ``signal.meta.strategy_name``
    (the canonical attribution source post-BUG-033). When unset
    (multiplexer / unknown), the gate is bypassed — there's no
    canonical name to scope the open-package query to.
    """
    if not strategy_name:
        return None
    try:
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path
        db_path = trade_journal_db_path()
        db = Database(db_path=db_path)
        # 2026-05-09 — dropped ``linked_only=True``. With the filter on,
        # a multi-account dispatch where every account refused on
        # ``zero_exchange_capacity`` left the package row at
        # status='open', linked_trade_id=NULL — and the next tick's gate
        # query filtered it out, letting the dispatch retry every
        # minute. The result was 50+ rejection rows per cluster in
        # ``trades`` until ``_sweep_unlinked_packages`` orphaned the
        # row at +5 min. Treating any open row (linked or not) as
        # gate-blocking turns the rejection cadence from 1/min into
        # 1 per 5-min sweep window.
        rows = db.get_order_packages_by_strategy(
            strategy_name, status="open", limit=1, symbol=symbol,
        )
        if rows:
            return str(rows[0].get("order_package_id") or "")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_has_open_package_for_strategy(%s, symbol=%s): DB read failed — %s",
            strategy_name, symbol, exc,
        )
        return None


def _recent_refusal_for_strategy(
    strategy_name: Optional[str],
    cooldown_seconds: Optional[int] = None,
    symbol: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return ``{"order_package_id", "age_seconds", "cooldown_seconds"}``
    when *strategy_name* has a ``status='rejected'`` order_packages row
    updated within the cooldown window, else ``None``.

    Belt-and-braces companion to ``_has_open_package_for_strategy``.
    The open-package gate already blocks dispatch while a strategy has
    an outstanding live position; this gate blocks dispatch while a
    strategy's most-recent attempt was *internally refused*
    (``sized_qty=0`` → ``log_rejection_to_journal(status='rejected')``
    in coordinator.multi_account_execute). The two together prevent
    both kinds of duplicate dispatch — including the
    sized_qty=0 cascade FU-20260510-002 captured.

    Best-effort — DB-read failure returns ``None`` (i.e. "no
    cooldown known") rather than refusing every dispatch on a
    transient SQLite hiccup. Tradeoff matches the open-package
    helper's contract.
    """
    if not strategy_name:
        return None
    cooldown = cooldown_seconds if cooldown_seconds is not None else _refusal_cooldown_seconds()
    if cooldown <= 0:
        return None
    try:
        from datetime import datetime, timezone
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path
        db_path = trade_journal_db_path()
        db = Database(db_path=db_path)
        rows = db.get_order_packages_by_strategy(
            strategy_name, status="rejected", limit=1, symbol=symbol,
        )
        if not rows:
            return None
        row = rows[0]
        updated = row.get("updated_at") or row.get("created_at")
        if not updated:
            return None
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
        if age_seconds < 0 or age_seconds > cooldown:
            return None
        return {
            "order_package_id": str(row.get("order_package_id") or ""),
            "age_seconds": float(age_seconds),
            "cooldown_seconds": int(cooldown),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_recent_refusal_for_strategy(%s): DB read failed — %s",
            strategy_name, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Bar-close debounce — one entry attempt per CLOSED bar (PERF-20260601-001)
# ---------------------------------------------------------------------------
#
# The open-package gate blocks re-entry only while a package is *open*. When a
# package closes mid-bar (e.g. the reconciler records an exchange-side SL/TP
# fire), the gate frees and the strategy re-fires its still-valid breakout on
# the very next tick — within the SAME bar. On a 2 h strategy this produced a
# re-entry storm (9 packages in ~1 h on 2026-06-01) and a flood of
# ``intent_noop`` rejection rows (the intent layer no-ops the duplicate while
# a net position is already held), polluting the journal and skewing
# per-strategy stats. A bar-close strategy should act AT MOST ONCE per closed
# bar; this gate enforces that by suppressing a second actionable dispatch for
# the same strategy+symbol within the same timeframe bucket as the most recent
# package it already created. Env kill-switch: ``STRATEGY_BAR_DEBOUNCE_DISABLED``.

_TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "1w": 604800,
}


def _timeframe_seconds(timeframe: Optional[str]) -> Optional[int]:
    """Parse a timeframe token (``2h`` / ``15m`` / ``1d``) to seconds.

    Returns ``None`` for unknown / unparseable tokens so the caller can
    fall back to "no debounce" rather than guess a bucket size.
    """
    if not timeframe:
        return None
    tf = str(timeframe).strip().lower()
    if tf in _TIMEFRAME_SECONDS:
        return _TIMEFRAME_SECONDS[tf]
    # generic <int><unit> parse (m/h/d/w) for anything not in the table
    try:
        unit = tf[-1]
        n = int(tf[:-1])
        mult = {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(unit)
        return n * mult if (mult and n > 0) else None
    except (ValueError, IndexError):
        return None


def _strategy_timeframe_seconds(strategy_name: str) -> Optional[int]:
    """Look up *strategy_name*'s configured timeframe (seconds). Best-effort."""
    try:
        from src.units.strategies import load_strategy_config
        cfg = (load_strategy_config() or {}).get(strategy_name) or {}
        return _timeframe_seconds(cfg.get("timeframe"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_strategy_timeframe_seconds(%s): config read failed — %s",
            strategy_name, exc,
        )
        return None


def _strategy_timeframe_label(strategy_name: str) -> Optional[str]:
    """Look up *strategy_name*'s configured timeframe TOKEN (e.g. ``"1h"``).

    The string companion of ``_strategy_timeframe_seconds`` — the regime
    advisory spec map keys on the ``(symbol, timeframe)`` token, not seconds, so
    a string is what the ML-vol verdict needs. Returns ``None`` when the
    strategy has no configured ``timeframe`` or the config can't be read
    (best-effort; the verdict then degrades to ``unknown`` — permissive).
    """
    try:
        from src.units.strategies import load_strategy_config
        cfg = (load_strategy_config() or {}).get(strategy_name) or {}
        tf = cfg.get("timeframe")
        return str(tf).strip() if tf else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_strategy_timeframe_label(%s): config read failed — %s",
            strategy_name, exc,
        )
        return None


def _bar_debounce_disabled() -> bool:
    # The flag below is a kill-switch for an over-trading DEBOUNCE, not a
    # live/dry gate: it only throttles re-entry frequency (one entry per
    # closed bar) and never decides whether a strategy trades live vs dry
    # (that stays accounts.yaml mode + strategies.yaml execution). Mirrors
    # the STRATEGY_REFUSAL_COOLDOWN_SECONDS rollback knob.
    raw = os.environ.get("STRATEGY_BAR_DEBOUNCE_DISABLED", "")  # allow-silent: debounce kill-switch, not a live/dry gate
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _same_bar_entry_for_strategy(
    strategy_name: Optional[str], symbol: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return ``{"order_package_id", "bar_seconds", "last_created_at"}`` when
    *strategy_name* already created a package for *symbol* inside the CURRENT
    timeframe bucket (i.e. it already acted this bar), else ``None``.

    "Current bucket" is ``floor(epoch / bar_seconds)`` — so the first entry of
    a 2 h bar dispatches and every subsequent actionable tick in that same 2 h
    window is suppressed, regardless of whether the first package has since
    closed. Resets cleanly when a new bar opens.

    Best-effort: missing timeframe, kill-switch, or a DB-read failure all
    return ``None`` (no debounce) rather than block dispatch on a hiccup.
    """
    if not strategy_name or _bar_debounce_disabled():
        return None
    bar_seconds = _strategy_timeframe_seconds(strategy_name)
    if not bar_seconds or bar_seconds <= 0:
        return None
    try:
        from datetime import datetime, timezone
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path
        db = Database(db_path=trade_journal_db_path())
        # Any-status, newest-first; scan a few in case the latest-updated row
        # (e.g. an old package re-touched by a trail ratchet) is not the
        # latest-created one.
        rows = db.get_order_packages_by_strategy(
            strategy_name, status=None, limit=5, symbol=symbol,
        )
        if not rows:
            return None
        now_bucket = int(datetime.now(timezone.utc).timestamp() // bar_seconds)
        for row in rows:
            created = row.get("created_at")
            if not created:
                continue
            try:
                ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if int(ts.timestamp() // bar_seconds) == now_bucket:
                return {
                    "order_package_id": str(row.get("order_package_id") or ""),
                    "bar_seconds": int(bar_seconds),
                    "last_created_at": str(created),
                }
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_same_bar_entry_for_strategy(%s, symbol=%s): DB read failed — %s",
            strategy_name, symbol, exc,
        )
        return None
