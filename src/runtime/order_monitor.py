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

from src.utils.paths import repo_root as _repo_root_fn  # noqa: E402
_REPO_ROOT = Path(_repo_root_fn())


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
    from src.units.db.database import Database
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


def _apply_partial_close(
    db,
    open_pkg: dict,
    verdict: Dict[str, Any],
    summary: _StrategyTickSummary,
) -> None:
    """Partial-close path for ``close_qty_pct < 1.0`` verdicts.

    DB-side only (no exchange call — that is PR 3 of the
    strategy-monocle sprint, Tier 2).

    Behaviour
    ---------
    * Reads the linked ``trades`` row to get the current
      ``position_size`` and ``notes`` JSON.
    * Appends a fragment to ``notes.partial_closes``:
      ``{"qty": pct, "reason": str, "ts": iso, ["exit_price": float]}``.
    * Stores ``notes.original_position_size`` on the first partial so
      subsequent calls can compute the remaining fraction correctly.
    * Updates ``trades.position_size`` to
      ``original_position_size * (1 - cumulative_closed_pct)``.
    * Keeps ``order_packages.status = 'open'``.
    * When cumulative closed pct >= 1.0 (sequential partials totalling
      100 %), falls through to a full close of both the package and
      the trade row.
    * No-op (warning logged) when there is no linked trade row or when
      ``linked_trade_id`` is absent (the fallback symbol/strategy match
      is intentionally skipped for partial closes to avoid wrong-row
      updates).
    """
    pkg_id = open_pkg.get("order_package_id")
    close_qty_pct = float((verdict or {}).get("close_qty_pct", 1.0))
    reason = str((verdict or {}).get("reason") or "partial_close")
    exit_price = (verdict or {}).get("exit_price")
    now = datetime.now(timezone.utc).isoformat()

    linked_trade_id = open_pkg.get("linked_trade_id")
    if not linked_trade_id:
        logger.warning(
            "order_monitor: partial-close skipped for pkg=%s — "
            "no linked_trade_id (fallback by symbol not safe for partials)",
            pkg_id,
        )
        summary.no_change_count += 1
        return

    try:
        rows = db.get_trades(filters={"id": int(linked_trade_id)})
        trade = rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: partial-close trade read failed pkg=%s trade=%s: %s",
            pkg_id, linked_trade_id, exc,
        )
        summary.error_count += 1
        summary.errors.append(f"{pkg_id}: partial-close trade-read failed")
        return

    if trade is None:
        logger.warning(
            "order_monitor: partial-close skipped for pkg=%s — "
            "linked trade_id=%s not found",
            pkg_id, linked_trade_id,
        )
        summary.no_change_count += 1
        return

    trade_notes = _decode_notes(trade.get("notes"))
    original_pos = trade_notes.get("original_position_size") or float(
        trade.get("position_size") or 0.0
    )
    partials: list = list(trade_notes.get("partial_closes") or [])
    already_closed_pct = sum(float(p.get("qty", 0)) for p in partials)
    new_total_closed = already_closed_pct + close_qty_pct

    fragment: Dict[str, Any] = {"qty": close_qty_pct, "reason": reason, "ts": now}
    if exit_price is not None:
        fragment["exit_price"] = float(exit_price)
    partials.append(fragment)

    if new_total_closed >= 1.0:
        # Sequential partials reached/exceeded 100 % — full close.
        trade_notes["partial_closes"] = partials
        _full_close_trade_and_package(
            db,
            pkg_id=pkg_id,
            linked_trade_id=int(linked_trade_id),
            reason=reason,
            exit_price=exit_price,
            extra_notes=trade_notes,
            summary=summary,
        )
        return

    # True partial: reduce position_size, keep package open.
    if "original_position_size" not in trade_notes:
        trade_notes["original_position_size"] = original_pos
    trade_notes["partial_closes"] = partials

    remaining_pct = 1.0 - new_total_closed
    new_position_size = round(original_pos * remaining_pct, 8)

    try:
        db.update_trade(int(linked_trade_id), {
            "position_size": new_position_size,
            "notes": json.dumps(trade_notes, ensure_ascii=False)[:2000],
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: partial-close trade write failed pkg=%s trade=%s: %s",
            pkg_id, linked_trade_id, exc,
        )
        summary.error_count += 1
        summary.errors.append(f"{pkg_id}: partial-close trade-write failed")
        return

    logger.info(
        "order_monitor: partial close pkg=%s trade=%s "
        "close_pct=%.3f new_position_size=%.8f remaining_pct=%.3f",
        pkg_id, linked_trade_id, close_qty_pct, new_position_size, remaining_pct,
    )
    summary.updated_count += 1


def _full_close_trade_and_package(
    db,
    *,
    pkg_id: Optional[str],
    linked_trade_id: int,
    reason: str,
    exit_price: Optional[float],
    extra_notes: Optional[Dict[str, Any]] = None,
    summary: _StrategyTickSummary,
) -> None:
    """Close both the ``order_packages`` row and the linked ``trades`` row.

    Extracted so the normal ``action='close'`` path and the
    sequential-partials-reach-100% path share one implementation.
    ``extra_notes`` is merged into the trade's notes JSON before the
    write (used by the sequential-partial path to persist the
    ``partial_closes`` list).
    """
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

    try:
        close_updates: Dict[str, Any] = {
            "status": "closed",
            "exit_reason": reason,
        }
        if exit_price is not None:
            close_updates["exit_price"] = float(exit_price)
        if extra_notes:
            # Read-modify-write the notes field.
            rows = db.get_trades(filters={"id": linked_trade_id})
            existing_notes = _decode_notes(rows[0].get("notes") if rows else None)
            existing_notes.update(extra_notes)
            close_updates["notes"] = json.dumps(existing_notes, ensure_ascii=False)[:2000]
        db.update_trade(linked_trade_id, close_updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: trade close write failed for trade=%s pkg=%s: %s",
            linked_trade_id, pkg_id, exc,
        )

    summary.closed_count += 1


def _apply_update(db, open_pkg: dict, verdict: Dict[str, Any],
                  summary: _StrategyTickSummary) -> None:
    """Translate a non-None monitor verdict into DB writes.

    Verdict shapes:
      - ``{"sl": float}`` or ``{"tp": float}`` → update_order_package
      - ``{"action": "close", "reason": str}`` → full close of package
        AND linked trade row.
      - ``{"action": "close", "close_qty_pct": float, "reason": str,
           "exit_price": float?}``
        → partial close when ``close_qty_pct < 1.0``; full close when
        ``close_qty_pct == 1.0`` (default) or cumulative partials
        reach 100 %.  Invalid pct (≤ 0 or > 1) is rejected with a
        warning.

    Each branch is wrapped; one failing write doesn't break the
    rest of the tick.
    """
    pkg_id = open_pkg.get("order_package_id")
    action = (verdict or {}).get("action")
    if action == "close":
        raw_pct = (verdict or {}).get("close_qty_pct")
        if raw_pct is not None:
            try:
                close_qty_pct = float(raw_pct)
            except (TypeError, ValueError):
                logger.warning(
                    "order_monitor: invalid close_qty_pct=%r for pkg=%s — skipping",
                    raw_pct, pkg_id,
                )
                summary.no_change_count += 1
                return
            if close_qty_pct <= 0.0 or close_qty_pct > 1.0:
                logger.warning(
                    "order_monitor: close_qty_pct=%.4f out of range (0, 1] "
                    "for pkg=%s — skipping",
                    close_qty_pct, pkg_id,
                )
                summary.no_change_count += 1
                return
            if close_qty_pct < 1.0:
                _apply_partial_close(db, open_pkg, verdict, summary)
                return
            # close_qty_pct == 1.0 falls through to full-close below.
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

        # Exchange-side close. Operator directive 2026-05-03: per-account
        # ``RiskManager.dry_run`` is the only dry/live toggle in the
        # codebase. The prior ``MONITOR_APPLY_TO_EXCHANGE`` env-gate
        # violated that contract — DB-only "shadow mode" was a soak-test
        # scaffold from S-052 that survived past its sell-by, and on
        # 2026-05-09 it stranded vwap/bybit_2 trade #1049: monitor
        # fired tp_cross, DB flipped to status='closed', the live BTC
        # position kept consuming margin until the orphan-position
        # reconciler swept it. The gate is gone; live mode is the only
        # mode. Per-account dry_run still suppresses the order at the
        # ``_send_close_to_exchange`` boundary when the account is
        # paper.
        # Realised-PnL booking. The trade-row write above only stamped
        # status/exit_reason/exit_price; the database-layer docstring
        # contract (src/units/db/database.py § "the monitor loop updates
        # it on close") expects pnl + pnl_percent on the same close.
        # The 2026-05-10 layer-2 review surfaced 38 closed trades with
        # pnl=NULL because this second write was never wired. Gross
        # PnL only — fees are settled by the exchange-truth-attribution
        # reconciler, not here.
        exit_price_for_pnl = (verdict or {}).get("exit_price")
        if matched_trade and exit_price_for_pnl is not None:
            pnl_updates = _compute_close_pnl(
                matched_trade, float(exit_price_for_pnl),
            )
            trade_id = matched_trade.get("id")
            if pnl_updates and trade_id is not None:
                try:
                    db.update_trade(int(trade_id), pnl_updates)
                    matched_trade = {**matched_trade, **pnl_updates}
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "order_monitor: trades pnl update failed for %s: %s",
                        pkg_id, exc,
                    )

        if matched_trade:
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

    # Exchange-side modify. The prior shadow-mode env-gate
    # (``MONITOR_APPLY_TO_EXCHANGE``) is gone — see the matching
    # comment in the close path above. Looks up the matched trade row
    # to get account_id + symbol; bypasses the exchange call when no
    # trade row matches (the package may have been dispatched but the
    # account_id linkage hasn't been wired in yet).
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


def _compute_close_pnl(matched_trade: Dict[str, Any],
                       exit_price: float) -> Dict[str, Any]:
    """Realised gross PnL + PnL% for a closed trade row.

    Mirrors the gross-PnL formula in src/backtest/backtester.py so the
    live + backtest accounting line up. Returns {"pnl", "pnl_percent"}
    when the matched row carries entry_price + position_size + a known
    direction; returns {} otherwise so callers can skip the second
    write without raising.

    Fees are not deducted here — the exchange-truth-attribution
    reconciler is responsible for net-of-fees PnL.
    """
    try:
        entry = float(matched_trade["entry_price"])
        size = float(matched_trade["position_size"])
    except (KeyError, TypeError, ValueError):
        return {}
    direction = str(matched_trade.get("direction") or "").lower()
    if direction == "long":
        gross_pnl = (exit_price - entry) * size
    elif direction == "short":
        gross_pnl = (entry - exit_price) * size
    else:
        return {}
    notional = entry * size
    pnl_percent = (gross_pnl / notional * 100.0) if notional else 0.0
    return {
        "pnl": round(gross_pnl, 2),
        "pnl_percent": round(pnl_percent, 4),
    }


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


# ---------------------------------------------------------------------------
# Monitor-loop write-back reconciler — SSOT-from-Bybit (issue #502)
# (CLAUDE.md § Architecture rules § 5 "Live by default + tell-me-if-not")
# ---------------------------------------------------------------------------
#
# Per-orderId reconciliation. Each DB-open trade is matched against
# ITS specific Bybit order via ``account_order_status`` (issue #502).
# That replaces the legacy aggregate ``(symbol, side)`` match, which
# was vulnerable to Bybit's open-positions index lag and ambiguous on
# multi-leg accounts.
#
# Decision matrix (DB-open + Bybit response):
#   order open / partially filled  → leave DB row 'open'
#   order filled, position open    → leave DB row 'open' (cross-check
#                                     via ``account_open_positions``)
#   order filled, position closed  → mark 'closed' with the REAL exit
#                                     price + exec time from order
#                                     history (fixes the PnL gap the
#                                     legacy reconciler-close path
#                                     left as ``exit_price=NULL``)
#   order not found anywhere       → mark 'orphaned' (genuine — Bybit
#                                     denies any record)
#   read failure                   → skip (conservative)
#
# Skip rules (per-row or per-account, no orphan stamp):
#   - account.mode != 'live' (dry-run / paper) — no exchange to read.
#   - account_order_status returned None — don't orphan rows just
#     because we couldn't read.
#   - account in DB but absent from accounts.yaml — operator can clean
#     up manually.
#   - trades whose ``notes.trade_id`` is non-numeric (``rejected-…``,
#     ``exchange_rejected-…``) — they were never live exchange orders.
#   - trades with ``created_at`` newer than ``RECONCILER_GRACE_SECONDS``
#     — backstop in case the order-create response is itself behind
#     on Bybit's side. After SSOT soak the default can drop to ~5 s.
#
# Cascade on close / orphan: the linked ``order_packages`` row is also
# updated (close_reason = 'reconciler_filled' or 'reconciler').
#
# Gated by env var ``MONITOR_RECONCILE_ENABLED`` (default ``false``).

_ORPHAN_PING_CAP = 10

# Default grace window: a freshly-placed trade is not eligible for
# orphan-stamping until ``created_at`` is at least this many seconds in
# the past. Backstop against any residual Bybit order-create race —
# the SSOT path (issue #502) does its own per-orderId lookup that is
# consistent on the create-response side, so after a few days of soak
# the operator can drop this from 60 s to ~5 s. Operator tunes via
# ``RECONCILER_GRACE_SECONDS``.
_DEFAULT_RECONCILER_GRACE_SECONDS = 60

# Bybit V5 ``orderStatus`` values that mean "order is still live on
# the exchange and has not reached a terminal state". A DB row whose
# orderId reports any of these stays ``status='open'`` regardless of
# the position view.
_BYBIT_LIVE_ORDER_STATUSES = frozenset({
    "new", "partiallyfilled", "untriggered", "active", "created", "triggered",
})

# Reconcile-only side of the schema: what we can pull from the trades
# row to match against the exchange snapshot.
_RECONCILE_TRADE_COLS = (
    "id", "account_id", "symbol", "direction", "notes", "created_at"
)


def _reconcile_enabled() -> bool:
    """Read ``MONITOR_RECONCILE_ENABLED`` at call time so an operator
    flag flip takes effect within the next tick without restarting
    the trader. Default ``false`` for PR 2; PR 3 flips it on."""
    raw = os.environ.get("MONITOR_RECONCILE_ENABLED", "false")
    return str(raw).strip().lower() == "true"


def _grace_window_seconds() -> float:
    """Read ``RECONCILER_GRACE_SECONDS`` at call time so an operator
    tweak takes effect on the next tick. Falls back to
    ``_DEFAULT_RECONCILER_GRACE_SECONDS`` on missing / unparseable
    values; clamped to ``>= 0``.
    """
    raw = os.environ.get("RECONCILER_GRACE_SECONDS")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_RECONCILER_GRACE_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_RECONCILER_GRACE_SECONDS)


def _parse_created_at(value: Any) -> Optional[datetime]:
    """Best-effort parse of a ``trades.created_at`` value into a tz-aware
    UTC ``datetime``. Returns ``None`` for missing or unparseable input.

    Handles both formats the schema produces:
      * ``"2026-05-08 08:42:23"`` — SQLite ``CURRENT_TIMESTAMP`` default
        (UTC, no tz suffix).
      * ``"2026-05-08T08:42:23.284050+00:00"`` — explicit ISO 8601 with
        tz, e.g. when the caller stamps ``created_at`` itself.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "T" not in s and " " in s:
        # Normalise SQLite's space separator to ISO-8601's 'T'.
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # SQLite CURRENT_TIMESTAMP is documented as UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_account_cfgs_for_reconcile() -> Dict[str, Dict[str, Any]]:
    """Return ``{account_id: account_cfg_dict}`` from accounts.yaml.

    Account dicts carry the keys ``account_open_positions`` reads
    (``account_id``, ``exchange``, ``api_key_env``, ``api_secret_env``,
    ``mode``) plus ``market_type``. Best-effort — any read failure
    returns an empty dict so the reconciler runs as a no-op rather
    than orphaning trades on a config-load error.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    path = os.path.join(_REPO_ROOT, "config", "accounts.yaml")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_account_cfgs_for_reconcile: %s", exc)
        return {}
    raw = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False:
            continue
        out[str(name)] = {
            "account_id": str(name),
            "exchange": cfg.get("exchange", "bybit"),
            "api_key_env": cfg.get("api_key_env"),
            "api_secret_env": cfg.get("api_secret_env"),
            "mode": cfg.get("mode") or "live",
            "market_type": cfg.get("market_type") or "spot",
        }
    return out


def _classify_orphan_close(account_cfg: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Classify a DB-open / exchange-flat orphan to help the operator
    distinguish "operator manually closed" from "fill anomaly /
    exchange-side risk action" at a glance.

    Field reality, 2026-05-08: the live trader (bybit_2) is
    ``market_type: spot-margin`` and runs vwap. Spot-margin has **no
    exchange-side SL/TP path** — Bybit V5's ``set_trading_stop`` is
    derivatives-only (see the modify_open_order docstring), and the
    monitor loop is what enforces brackets via
    ``close_open_position``. So a spot-margin DB-open / exchange-flat
    row is *guaranteed* to be one of:

      1. Operator manually closed via the exchange UI.
      2. Bybit risk engine forced a close (margin call, liquidation,
         exchange-side risk action).

    Either case warrants an "investigate" tag rather than the generic
    "reconciler" reason. SL/TP firing is impossible on this path.

    For derivatives accounts (``market_type: linear`` /
    ``inverse``), an SL/TP fire is a perfectly valid orphan source —
    the strategy gate cleared the exchange-side SL but the DB row
    didn't update. Disambiguating SL/TP-fire vs operator-close on
    derivatives needs an extra exchange API call (Bybit V5
    ``get_closed_pnl``); that's deferred to a follow-up. For now,
    derivatives orphans get ``classification=unknown``.

    Returns
    -------
    dict
        ``{"classification": <tag>, "note": <human-readable>}``. Tags:
          * ``"spot_margin_external_close"`` — operator manual close
            or exchange risk action (no SL/TP path exists).
          * ``"unknown"`` — derivatives or unrecognised market_type;
            operator must check exchange UI to disambiguate.
    """
    market_type = str(account_cfg.get("market_type") or "").strip().lower()
    if market_type == "spot-margin":
        return {
            "classification": "spot_margin_external_close",
            "note": (
                "no exchange-side SL/TP path on spot-margin — operator "
                "manual close or exchange risk action; check exchange UI"
            ),
        }
    return {
        "classification": "unknown",
        "note": (
            "derivatives orphan — could be SL/TP fire OR operator close; "
            "check exchange recent trades to disambiguate"
        ),
    }


def _exchange_position_set(positions: Optional[List[Dict[str, Any]]]) -> set:
    """Convert the exchange's ``account_open_positions`` output to a set
    of ``(symbol, normalised_side)`` tuples for O(1) lookup.

    Side normalisation: bybit returns ``'Buy'`` / ``'Sell'``, the
    trade-journal stores ``'long'`` / ``'short'``. Both representations
    map to the same canonical pair so a match is order-of-magnitude-
    invariant.
    """
    if not positions:
        return set()
    out = set()
    for p in positions:
        sym = p.get("symbol")
        side = str(p.get("side") or "").lower()
        canonical_side = {
            "buy": "long", "long": "long",
            "sell": "short", "short": "short",
        }.get(side)
        if sym and canonical_side:
            out.add((sym, canonical_side))
    return out


_VALID_ORPHAN_POLICIES = {"detect_only", "adopt", "close"}


def _orphan_position_policy() -> str:
    """Read ``ORPHAN_POSITION_POLICY`` at call time.

    One of ``detect_only`` / ``adopt`` / ``close``. Default is
    ``detect_only`` — the safest behaviour for an unknown deployment.
    The live trader's systemd unit sets ``adopt`` per operator
    decision 2026-05-11 (the reverse reconciler should insert a trade
    row so the journal regains visibility, without auto-trading the
    position closed).

    Unknown values fall back to ``detect_only`` rather than raising
    so a typo in the unit file doesn't crash the trader; the audit
    log captures the rejected value.
    """
    raw = str(os.environ.get("ORPHAN_POSITION_POLICY", "detect_only")).strip().lower()
    if raw in _VALID_ORPHAN_POLICIES:
        return raw
    logger.warning(
        "ORPHAN_POSITION_POLICY=%r is not one of %s — falling back to detect_only",
        raw, sorted(_VALID_ORPHAN_POLICIES),
    )
    return "detect_only"


def _reconcile_orphan_exchange_positions(db) -> Dict[str, int]:
    """Reverse reconciler — finds Bybit positions with no journal row.

    Counterpart to :func:`_reconcile_open_trades`:

    * ``_reconcile_open_trades``  →  for each DB-open trade, ask Bybit
      "still alive?"  (catches DB drift: trade row stayed open after
      Bybit closed the position).
    * this function              →  for each Bybit-open position, ask
      DB "do you have a row for this?"  (catches the reverse drift:
      position is live on Bybit but the journal lost track of it —
      the 2026-05-11 incident, trade 1145 BTCUSDT bybit_2 vwap LONG).

    Policy (``ORPHAN_POSITION_POLICY`` env, see :func:`_orphan_position_policy`):

    * ``detect_only`` — emit an operator alert + audit entry, do NOT
      mutate the DB or send any exchange order. Safest starting
      configuration; lets the operator review the alert format and
      decide policy from observed orphans.
    * ``adopt`` — INSERT a new ``trades`` row with status='open',
      ``setup_type='adopted_orphan'``, ``strategy_name='orphan_adopt'``,
      ``entry_price`` = Bybit ``avgPrice``, ``position_size`` =
      Bybit ``size``. SL/TP fields stay NULL — exchange-side
      conditionals are the operator's responsibility on an adopted
      orphan. The bot's forward reconciler picks the row up on the
      next tick and closes it cleanly when Bybit reports the
      position flat (TP/SL/manual fire). The bot's monitor() hook
      will not fire because ``orphan_adopt`` is not a registered
      strategy — that's deliberate; we don't pretend to know the
      entry rationale.
    * ``close`` — submit a market close via ``safe_place_order``
      to flatten the position immediately. Tier-3 sensitive
      (active trading from a reconciler); requires explicit env
      flip after operator review.

    Gated by ``MONITOR_RECONCILE_ENABLED`` (same flag as
    :func:`_reconcile_open_trades`). Best-effort — every step is
    wrapped; one bad position never aborts the sweep.

    Returns
    -------
    dict
        ``{checked_accounts, checked_positions, orphans_found,
        adopted, closed, detect_only, errors}`` — caller emits an
        INFO line whenever any non-zero count surfaces.
    """
    summary = {
        "checked_accounts": 0,
        "checked_positions": 0,
        "orphans_found": 0,
        "adopted": 0,
        "closed": 0,
        "detect_only": 0,
        # Adopted-orphan trade rows whose exchange position has since
        # disappeared (operator closed on Bybit, exchange-side SL/TP
        # fired, etc.). The forward reconciler (_reconcile_open_trades)
        # can't close these because they lack a numeric trade_id in
        # `notes` (we never owned the order). Tracked separately from
        # the policy=close summary key so the operator can distinguish
        # "active-trading close" from "journal cleanup close".
        "closed_disappeared": 0,
        "errors": 0,
    }
    if not _reconcile_enabled():
        return summary

    policy = _orphan_position_policy()
    cfgs = _load_account_cfgs_for_reconcile()
    if not cfgs:
        return summary

    from src.units.accounts.clients import account_open_positions
    from src.runtime.execution_diagnostics import (
        enqueue_exchange_orphan_adoption,
    )

    for aid, cfg in cfgs.items():
        # Reverse reconciler only runs on live accounts. Dry/paper
        # accounts have no real exchange-side positions to orphan, and
        # bybit_client_for() would yield a no-creds client in dry mode.
        if str(cfg.get("mode") or "live").lower() in {"dry", "dry_run", "dry-run", "paper"}:
            continue

        summary["checked_accounts"] += 1
        positions = account_open_positions(cfg)
        if positions is None:
            # Read failure — skip this account ENTIRELY (no adopt + no
            # close-on-disappear). _reconcile_open_trades observed the
            # same condition and bumped skipped_no_creds. Conservative
            # by design: we don't close an adopted_orphan row on the
            # basis of a transient creds-read failure.
            continue

        # Read DB-open trades for this account in one batch so we don't
        # round-trip per position. Done BEFORE the positions=[] short-
        # circuit because we still need this list for the close-on-
        # disappear pass below (an account with zero Bybit positions but
        # an adopted_orphan row still open in the journal is the exact
        # case the close pass exists to handle).
        try:
            conn = db.connect()
            try:
                conn.row_factory = __import__("sqlite3").Row
                open_rows = conn.execute(
                    "SELECT id, symbol, direction, strategy_name FROM trades "
                    "WHERE status='open' AND COALESCE(is_backtest,0)=0 "
                    "  AND account_id=?",
                    (aid,),
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_reconcile_orphan_exchange_positions: open-trades read "
                "failed for account=%s: %s", aid, exc,
            )
            summary["errors"] += 1
            continue

        known: set = set()
        for r in open_rows:
            sym = r["symbol"]
            side = str(r["direction"] or "").lower()
            canonical = {"buy": "long", "long": "long",
                         "sell": "short", "short": "short"}.get(side)
            if sym and canonical:
                known.add((sym, canonical))

        # Build the set of exchange-side (symbol, canonical_side) pairs
        # ONCE so both the adopt pass and the close-on-disappear pass
        # can use it without re-canonicalising.
        exchange_positions: set = set()
        for _p in positions:
            _sym = _p.get("symbol")
            _side_raw = str(_p.get("side") or "").lower()
            _cs = {"buy": "long", "long": "long",
                   "sell": "short", "short": "short"}.get(_side_raw)
            if _sym and _cs:
                exchange_positions.add((_sym, _cs))

        # Close-on-disappear pass: every adopted_orphan row whose
        # (symbol, direction) is NOT in the current exchange positions
        # gets its trade row marked closed. The forward reconciler
        # (_reconcile_open_trades) can't do this because adopted_orphan
        # rows lack a numeric trade_id in `notes` (we don't own the
        # order). exit_price stays NULL — we don't have a Bybit-side
        # fill record for an order we never placed. The operator's
        # exchange-side SL/TP (or manual close) is the source of truth
        # for the actual exit; the journal close is bookkeeping.
        now_iso = datetime.now(timezone.utc).isoformat()
        for r in open_rows:
            if str(r["strategy_name"] or "") != "orphan_adopt":
                continue
            sym = r["symbol"]
            side = str(r["direction"] or "").lower()
            canonical = {"buy": "long", "long": "long",
                         "sell": "short", "short": "short"}.get(side)
            if not sym or not canonical:
                continue
            if (sym, canonical) in exchange_positions:
                # Still alive on Bybit — leave it open.
                continue
            try:
                db.update_trade(int(r["id"]), {
                    "status": "closed",
                    "exit_reason": "adopted_orphan_disappeared",
                    "notes": json.dumps({
                        "closed_at": now_iso,
                        "closed_by": "reverse_reconciler",
                        "closed_reason": (
                            "Bybit no longer reports the adopted position; "
                            "exchange-side SL/TP or manual close took it out"
                        ),
                    }, ensure_ascii=False)[:500],
                })
                summary["closed_disappeared"] += 1
                logger.warning(
                    "_reconcile_orphan_exchange_positions: CLOSED disappeared "
                    "adopted orphan — trade_id=%s account=%s symbol=%s side=%s",
                    r["id"], aid, sym, canonical,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_reconcile_orphan_exchange_positions: close-disappeared "
                    "failed for trade_id=%s account=%s symbol=%s: %s",
                    r.get("id"), aid, sym, exc,
                )
                summary["errors"] += 1

        if not positions:
            # No exchange positions to walk for the adopt pass; the
            # close-on-disappear pass above already ran.
            continue

        for p in positions:
            summary["checked_positions"] += 1
            sym = p.get("symbol")
            side_raw = str(p.get("side") or "").lower()
            canonical_side = {
                "buy": "long", "long": "long",
                "sell": "short", "short": "short",
            }.get(side_raw)
            if not sym or not canonical_side:
                # Unrecognised position shape — skip, don't orphan.
                continue
            if (sym, canonical_side) in known:
                continue

            # Orphan found.
            summary["orphans_found"] += 1
            size = float(p.get("size") or 0.0)
            entry_price = float(p.get("entry_price") or 0.0)

            db_trade_id: Optional[int] = None
            note: Optional[str] = None

            if policy == "adopt":
                try:
                    db_trade_id = _adopt_orphan_position(
                        db=db,
                        account_id=aid,
                        symbol=str(sym),
                        direction=canonical_side,
                        size=size,
                        entry_price=entry_price,
                    )
                    summary["adopted"] += 1
                    logger.warning(
                        "_reconcile_orphan_exchange_positions: ADOPTED "
                        "exchange orphan — account=%s symbol=%s side=%s "
                        "size=%s entry=%s as trade_id=%s",
                        aid, sym, canonical_side, size, entry_price,
                        db_trade_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "_reconcile_orphan_exchange_positions: ADOPT failed "
                        "for account=%s symbol=%s side=%s: %s",
                        aid, sym, canonical_side, exc,
                    )
                    summary["errors"] += 1
                    note = f"adopt failed: {type(exc).__name__}"

            elif policy == "close":
                # Deliberately deferred — the close path is the
                # tier-3-sensitive variant (active trading from a
                # reconciler). It needs an integration test that
                # confirms safe_place_order receives a reduceOnly
                # close at the right size + side, and the operator
                # alert lands BEFORE the close is dispatched in case
                # the close itself fails. Surface as detect_only +
                # note until that wiring lands.
                summary["detect_only"] += 1
                note = (
                    "policy=close requested but the close path is not yet "
                    "implemented — treated as detect_only; see "
                    "src/runtime/order_monitor.py::_reconcile_orphan_exchange_positions"
                )
                logger.warning(
                    "_reconcile_orphan_exchange_positions: close policy "
                    "stub fired — orphan not closed; falling back to "
                    "detect_only for account=%s symbol=%s side=%s",
                    aid, sym, canonical_side,
                )

            else:  # detect_only
                summary["detect_only"] += 1
                logger.warning(
                    "_reconcile_orphan_exchange_positions: DETECTED "
                    "exchange orphan (detect_only) — account=%s symbol=%s "
                    "side=%s size=%s entry=%s",
                    aid, sym, canonical_side, size, entry_price,
                )

            try:
                enqueue_exchange_orphan_adoption(
                    account=aid,
                    symbol=str(sym),
                    side=canonical_side,
                    size=size,
                    entry_price=entry_price,
                    db_trade_id=db_trade_id,
                    policy=policy if policy != "close" else "detect_only",
                    note=note,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_reconcile_orphan_exchange_positions: alert enqueue "
                    "failed for account=%s symbol=%s: %s", aid, sym, exc,
                )

    if (
        summary["orphans_found"]
        or summary["adopted"]
        or summary["closed"]
        or summary["closed_disappeared"]
        or summary["errors"]
    ):
        logger.info(
            "_reconcile_orphan_exchange_positions: accounts=%d positions=%d "
            "orphans=%d adopted=%d closed=%d closed_disappeared=%d "
            "detect_only=%d errors=%d",
            summary["checked_accounts"], summary["checked_positions"],
            summary["orphans_found"], summary["adopted"], summary["closed"],
            summary["closed_disappeared"], summary["detect_only"],
            summary["errors"],
        )
    return summary


def _adopt_orphan_position(
    *,
    db,
    account_id: str,
    symbol: str,
    direction: str,
    size: float,
    entry_price: float,
) -> int:
    """Insert a ``trades`` row tracking an exchange-side orphan position.

    Used by :func:`_reconcile_orphan_exchange_positions` when
    ``ORPHAN_POSITION_POLICY=adopt``. The row is intentionally
    minimal:

    * ``setup_type='adopted_orphan'`` distinguishes it from real
      strategy entries on every dashboard / report.
    * ``strategy_name='orphan_adopt'`` — not a registered strategy,
      so the monitor() loop never fires on it. The forward reconciler
      will close the row when Bybit reports the position flat.
    * ``stop_loss``, ``take_profit_*`` left NULL. The operator's
      exchange-side conditional orders remain the actual risk control;
      the bot does not synthesize stops for a position whose original
      entry rationale it doesn't know.

    Returns the new ``trades.id``.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    notes_payload = json.dumps(
        {
            "adopted_at": now_iso,
            "adopted_by": "reverse_reconciler",
            "adopted_reason": (
                "Bybit reported open position with no matching "
                "trades.status='open' row"
            ),
            "exchange_entry_price": entry_price,
            "exchange_size": size,
        },
        ensure_ascii=False,
    )[:500]
    trade_data = {
        "timestamp": now_iso,
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "position_size": size,
        "setup_type": "adopted_orphan",
        "entry_reason": "reverse_reconciler_adopted_orphan_position",
        "status": "open",
        "notes": notes_payload,
        "is_backtest": 0,
        "strategy_name": "orphan_adopt",
        "account_id": account_id,
    }
    return int(db.insert_trade(trade_data))


def _reconcile_open_trades(db) -> Dict[str, int]:
    """SSOT-from-Bybit reconciler (issue #502).

    Each DB-open trade is reconciled against its specific Bybit order
    via :func:`src.units.accounts.clients.account_order_status`. The
    cross-check against the aggregate position view
    (:func:`account_open_positions`) is only used to disambiguate the
    "order filled, position still open" vs. "order filled, position
    flat" case.

    Returns a summary dict
    ``{checked, orphaned, closed, skipped_dry, skipped_no_creds,
       skipped_no_cfg, skipped_recent, skipped_non_numeric, errors}``
    so the caller (``run_monitor_tick``) can emit an INFO log line
    for every tick that touched at least one row.

    No-op when ``MONITOR_RECONCILE_ENABLED`` is unset or false. Best-
    effort — every step is wrapped; one bad row never aborts the
    sweep.
    """
    summary = {
        "checked": 0,
        "orphaned": 0,
        "closed": 0,
        "skipped_dry": 0,
        "skipped_no_creds": 0,
        "skipped_no_cfg": 0,
        "skipped_recent": 0,
        "skipped_non_numeric": 0,
        "errors": 0,
    }
    if not _reconcile_enabled():
        return summary

    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, notes, created_at "
                "FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_reconcile_open_trades: open-trades read failed: %s", exc)
        summary["errors"] += 1
        return summary

    if not rows:
        return summary

    summary["checked"] = len(rows)
    cfgs = _load_account_cfgs_for_reconcile()

    grace_seconds = _grace_window_seconds()
    now = datetime.now(timezone.utc)
    ripe_rows: List[Any] = []
    for r in rows:
        created = _parse_created_at(r["created_at"])
        if (
            grace_seconds > 0
            and created is not None
            and (now - created).total_seconds() < grace_seconds
        ):
            summary["skipped_recent"] += 1
            continue
        ripe_rows.append(r)

    if not ripe_rows:
        return summary

    by_account: Dict[str, List[Dict[str, Any]]] = {}
    for r in ripe_rows:
        aid = str(r["account_id"] or "unknown")
        by_account.setdefault(aid, []).append(dict(r))

    from src.units.accounts.clients import (
        account_open_positions,
        account_order_status,
    )
    from src.runtime.execution_diagnostics import (
        enqueue_orphan_reconciliation,
        enqueue_orphan_rollup,
    )

    orphan_pings_emitted = 0
    orphan_pings_suppressed = 0

    for aid, trade_rows in by_account.items():
        cfg = cfgs.get(aid)
        if cfg is None:
            summary["skipped_no_cfg"] += len(trade_rows)
            continue
        if str(cfg.get("mode") or "live").lower() in {"dry", "dry_run", "dry-run", "paper"}:
            summary["skipped_dry"] += len(trade_rows)
            continue

        # Lazy positions cross-check cache: at most ONE call to
        # ``account_open_positions`` per account per tick. The sentinel
        # ``...`` means "not fetched yet"; ``None`` means "fetch
        # failed". Subsequent rows on the same account that need the
        # cross-check reuse the cached set.
        positions_cache: Any = ...

        for row in trade_rows:
            trade_id_str = _extract_trade_id_from_notes(row.get("notes"))
            if trade_id_str is None or not _is_numeric_order_id(trade_id_str):
                # Non-numeric (``rejected-…``, ``exchange_rejected-…``,
                # ``dry-…``) or missing — never a live exchange order.
                summary["skipped_non_numeric"] += 1
                continue

            order_status = account_order_status(cfg, trade_id_str)
            if order_status is None:
                # Read failure → skip conservatively.
                summary["skipped_no_creds"] += 1
                continue

            status_str = str(order_status.get("status") or "").lower()
            filled_qty = float(order_status.get("filled_qty") or 0.0)

            if status_str in _BYBIT_LIVE_ORDER_STATUSES:
                # Order still live on Bybit — leave the DB row alone.
                continue

            # Order is in a terminal state OR genuinely unknown.
            #
            # Two paths to "orphan":
            #   * status == 'not_found' — Bybit denies any record of
            #     this orderId.
            #   * terminal state with zero fills — Cancelled / Rejected
            #     before any qty executed; no real position ever opened.
            if status_str == "not_found" or filled_qty <= 0:
                try:
                    _mark_orphaned(db, row)
                    summary["orphaned"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "_reconcile_open_trades: mark_orphaned failed for "
                        "trade_id=%s account=%s symbol=%s: %s",
                        row.get("id"), aid, row.get("symbol"), exc,
                    )
                    summary["errors"] += 1
                    continue
                if orphan_pings_emitted < _ORPHAN_PING_CAP:
                    enqueue_orphan_reconciliation(
                        account=aid,
                        symbol=str(row.get("symbol")),
                        side=str(row.get("direction") or "").lower(),
                        db_trade_id=row.get("id"),
                        linked_package_id=_extract_package_id(row.get("notes")),
                    )
                    orphan_pings_emitted += 1
                else:
                    orphan_pings_suppressed += 1
                continue

            # Filled (or PartiallyFilledCanceled with fills > 0). Cross-
            # check the position to decide between "still in market"
            # and "TP / SL / manual flatten closed it".
            if positions_cache is ...:
                pos = account_open_positions(cfg)
                positions_cache = (
                    None if pos is None else _exchange_position_set(pos)
                )
            if positions_cache is None:
                # Position-read failed → skip conservatively (don't
                # close on a half-known view).
                summary["skipped_no_creds"] += 1
                continue

            sym = row["symbol"]
            side = str(row["direction"] or "").lower()
            if (sym, side) in positions_cache:
                # Order filled, position still open — trade is alive.
                continue

            # Order filled, position flat → trade closed by exchange
            # (TP / SL / manual flatten). Mark closed with REAL exit
            # price + exec time from order history (closes the PnL
            # gap the legacy reconciler-close path left as
            # exit_price=NULL).
            try:
                _close_trade_from_order_status(db, row, order_status)
                summary["closed"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_reconcile_open_trades: close write failed for "
                    "trade_id=%s account=%s symbol=%s: %s",
                    row.get("id"), aid, sym, exc,
                )
                summary["errors"] += 1
                continue

            # Diagnostic ping (per-close cap + roll-up). In the SSOT
            # model "closed" means Bybit reports filled + position
            # flat — i.e. the exchange closed the trade (TP/SL on
            # derivatives, manual / margin-engine action on spot-
            # margin), not the bot's manage loop. Same operator-
            # actionability bar as a legacy "orphan close" so we
            # carry the classification metadata from #544.
            if orphan_pings_emitted < _ORPHAN_PING_CAP:
                cls_info = _classify_orphan_close(cfg)
                enqueue_orphan_reconciliation(
                    account=aid,
                    symbol=str(sym),
                    side=side,
                    db_trade_id=row.get("id"),
                    linked_package_id=_extract_package_id(row.get("notes")),
                    classification=cls_info.get("classification"),
                    classification_note=cls_info.get("note"),
                )
                orphan_pings_emitted += 1
            else:
                orphan_pings_suppressed += 1

    if orphan_pings_suppressed:
        enqueue_orphan_rollup(suppressed_count=orphan_pings_suppressed)

    if (
        summary["orphaned"]
        or summary["closed"]
        or summary["errors"]
    ):
        logger.info(
            "_reconcile_open_trades: checked=%d orphaned=%d closed=%d "
            "skipped_dry=%d skipped_no_creds=%d skipped_no_cfg=%d "
            "skipped_recent=%d skipped_non_numeric=%d errors=%d",
            summary["checked"], summary["orphaned"], summary["closed"],
            summary["skipped_dry"], summary["skipped_no_creds"],
            summary["skipped_no_cfg"], summary["skipped_recent"],
            summary["skipped_non_numeric"], summary["errors"],
        )
    return summary


def _sweep_unlinked_packages(db) -> int:
    """Mark order_packages with status='open' and no linked_trade_id as
    'orphaned'.

    These are packages the strategy logged before dispatch, but for
    which the risk manager rejected the signal or dispatch failed before
    a trade was ever placed at the broker.  They are invisible to the
    trades-table reconciler (_reconcile_open_trades) but were blocking
    the BUG-046 gate indefinitely (BUG-049).

    Only sweeps rows older than 5 minutes to avoid racing with the
    dispatch pipeline on a package that was just logged and is about to
    be linked.

    Gated by MONITOR_RECONCILE_ENABLED (same flag as _reconcile_open_trades).
    Best-effort — never raises.

    Returns:
        int: number of rows marked orphaned.
    """
    if not _reconcile_enabled():
        return 0
    try:
        conn = db.connect()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE order_packages "
                "SET status = 'orphaned', "
                "    updated_at = ?, "
                "    meta = json_set(COALESCE(meta, '{}'), "
                "        '$.orphaned_at', ?, "
                "        '$.orphaned_by', 'monitor_reconciler', "
                "        '$.orphaned_reason', "
                "        'BUG-049 — no linked_trade_id after 5 min; package was never executed') "
                "WHERE status = 'open' "
                "  AND linked_trade_id IS NULL "
                "  AND datetime(created_at) <= datetime('now', '-5 minutes')",
                (now_iso, now_iso),
            )
            affected = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        if affected:
            logger.info(
                "_sweep_unlinked_packages: orphaned %d unlinked open package(s)",
                affected,
            )
        return affected
    except Exception as exc:  # noqa: BLE001
        logger.warning("_sweep_unlinked_packages: failed: %s", exc)
        return 0


# Trade statuses that mean "this trade is no longer live on the
# exchange". A linked order_packages row stuck at status='open' against
# a trade in any of these states is the cascade-leak we have to clear,
# otherwise the strategy-monocle gate
# (pipeline.py::_has_open_package_for_strategy) silently blocks every
# future signal for the strategy.
_TERMINAL_TRADE_STATUSES = (
    "orphaned",
    "exchange_rejected",
    "closed",
    "rejected",
    "rejected_too_small",
)


def _sweep_stuck_linked_packages(db) -> int:
    """Force-close ``order_packages`` rows whose linked trade has
    reached a terminal status but whose own ``status`` is still
    ``'open'``.

    Background — the primary path is ``_mark_orphaned``'s package
    cascade. That cascade is now retried + audit-logged on failure,
    but:

      1. A bug elsewhere (e.g. partial close path forgetting to
         cascade) can still drop a package row in the same stuck
         state.
      2. The strategy-monocle gate at
         ``pipeline.py::_has_open_package_for_strategy`` (and the
         per-strategy ``vwap.py::_has_open_vwap_package``) reads
         ``status='open' AND linked_trade_id IS NOT NULL`` — so a
         single stuck row blocks *every* future signal for that
         strategy.

    This sweep is the second line of defence: idempotent, gated by
    ``MONITOR_RECONCILE_ENABLED``, runs once per monitor tick.

    Returns:
        int: number of rows force-closed this tick.
    """
    if not _reconcile_enabled():
        return 0
    try:
        conn = db.connect()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            placeholders = ",".join("?" * len(_TERMINAL_TRADE_STATUSES))
            conn.execute(
                "UPDATE order_packages "
                "SET status = 'closed', "
                "    close_reason = 'stuck_cascade_recovered', "
                "    updated_at = ?, "
                "    meta = json_set(COALESCE(meta, '{}'), "
                "        '$.stuck_recovered_at', ?, "
                "        '$.stuck_recovered_by', 'monitor_reconciler', "
                "        '$.stuck_recovered_reason', "
                "        'linked trade reached terminal status while package stayed open') "
                "WHERE status = 'open' "
                "  AND linked_trade_id IS NOT NULL "
                f"  AND linked_trade_id IN ("
                f"      SELECT id FROM trades WHERE status IN ({placeholders})"
                f"  )",
                (now_iso, now_iso, *_TERMINAL_TRADE_STATUSES),
            )
            affected = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        if affected:
            logger.info(
                "_sweep_stuck_linked_packages: force-closed %d stuck "
                "linked package(s) — strategy gate cleared",
                affected,
            )
        return affected
    except Exception as exc:  # noqa: BLE001
        logger.warning("_sweep_stuck_linked_packages: failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Stuck-strategy watchdog — last line of defence
# ---------------------------------------------------------------------------
#
# When the orphan reconciler, `_sweep_stuck_linked_packages`, AND the
# strategy's own monitor() loop have all had a chance to clear a
# package and none did, the strategy-monocle gate at
# pipeline.py::_has_open_package_for_strategy stays blocked
# indefinitely — every future signal for that strategy is silently
# dropped. The watchdog catches this terminal-class failure mode by
# escalating a high-priority operator alert AND force-closing the
# stuck row + cascading the linked trade to ``orphaned``.
#
# Idempotent: each affected package is flagged on its first sighting
# (``meta.stuck_alert_emitted_at``); subsequent ticks won't re-fire
# the alert. The force-close itself is naturally idempotent — once
# ``status='closed'`` the row no longer matches the watchdog's
# WHERE clause.
#
# Threshold: ``STUCK_STRATEGY_THRESHOLD_MINUTES`` env var. Default
# 30 — well above the orphan reconciler's grace window (60 s) and
# well above the longest expected monitor tick interval (15 min).
# Operator can tune via env without restart.

_DEFAULT_STUCK_STRATEGY_THRESHOLD_MINUTES = 30


def _stuck_strategy_threshold_minutes() -> float:
    """Read ``STUCK_STRATEGY_THRESHOLD_MINUTES`` at call time so an
    operator can tune the threshold without a trader restart. Falls
    back to the 30-minute default on any unparseable / missing
    value; clamped to ``>= 1`` minute (a sub-minute threshold would
    fight every reconciler tick).
    """
    raw = os.environ.get("STUCK_STRATEGY_THRESHOLD_MINUTES")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_STUCK_STRATEGY_THRESHOLD_MINUTES)
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_STUCK_STRATEGY_THRESHOLD_MINUTES)


def _watchdog_stuck_strategies(db) -> Dict[str, int]:
    """Detect + recover packages stuck at ``status='open'`` AND
    ``linked_trade_id IS NOT NULL`` for longer than the configured
    threshold.

    For each stuck package, cross-check with the exchange-side
    position view via :func:`account_open_positions` (cached per
    account per tick) before deciding what to do:

      * **Position alive at exchange** (the ``(symbol, direction)``
        pair shows up in the exchange's position list, including
        the spot-margin synthesised view from
        ``walletBalance > 0`` / ``borrowAmount > 0``) → **defer**.
        The trade is patient, not stuck — vwap holds for hours
        waiting for mean reversion. Stamp the meta to silence
        future ticks; emit the alert ONCE on first sighting (with
        ``auto_cleared=False`` so the operator knows we did NOT
        cascade); leave the package + trade rows alone.

      * **Position flat at exchange** (read succeeded, no matching
        position) → genuine orphan. Force-close the package
        (``status='closed'``, ``close_reason='stuck_strategy_watchdog'``),
        cascade the linked trade row to ``status='orphaned'``, emit
        the high-priority alert. Same behaviour as before this
        check was added.

      * **Position read failed** (creds missing / network /
        exchange error) → defer conservatively. Better to leave a
        stale package one more tick than force-clear blind on a
        half-known view of the world.

    Pre-2026-05-09 the watchdog blindly force-cleared after 30 min
    regardless of position state, which produced a feedback loop
    on vwap/bybit_2 (mean-reversion holds longer than 30 min →
    every signal got force-cleared at 30 min → BTC accumulated as
    orphaned residue → next signal dispatched against the smaller
    USDT cash → repeat). See #574 / #582.

    Operator-confirmed (2026-05-08): full automatic reset is
    approved when the trade is genuinely orphaned. The position-
    aware refinement (2026-05-09) keeps that automatic-reset
    contract — only the ``position-alive`` branch is new.

    The whole helper is gated by ``MONITOR_RECONCILE_ENABLED``.

    Returns a summary
    ``{checked, alerted, auto_cleared, deferred_position_alive,
       skipped_position_read_failed, errors}`` so the caller can
    log a per-tick line when non-zero.
    """
    summary = {
        "checked": 0,
        "alerted": 0,
        "auto_cleared": 0,
        "deferred_position_alive": 0,
        "skipped_position_read_failed": 0,
        "errors": 0,
    }
    if not _reconcile_enabled():
        return summary

    threshold_minutes = _stuck_strategy_threshold_minutes()

    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT order_package_id, strategy_name, symbol, direction, "
                "       linked_trade_id, updated_at, meta "
                "FROM order_packages "
                "WHERE status = 'open' "
                "  AND linked_trade_id IS NOT NULL "
                "  AND datetime(updated_at) <= datetime('now', ? || ' minutes')",
                (f"-{int(threshold_minutes)}",),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_watchdog_stuck_strategies: read failed: %s", exc)
        summary["errors"] += 1
        return summary

    if not rows:
        return summary

    summary["checked"] = len(rows)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Lazy import — keeps the module load cheap and avoids a circular
    # via execution_diagnostics's own log path.
    from src.runtime.execution_diagnostics import enqueue_stuck_strategy_alert
    from src.units.accounts.clients import account_open_positions

    # Account cfgs + per-account positions cache. We need cfgs to call
    # ``account_open_positions``; the cache prevents N redundant API
    # calls when several stuck packages share the same account.
    # Sentinel ``...`` means "not fetched"; ``None`` means "fetch
    # failed".
    cfgs = _load_account_cfgs_for_reconcile()
    positions_cache: Dict[str, Any] = {}

    for row in rows:
        pkg_id = row["order_package_id"]
        strategy = row["strategy_name"]
        symbol = row["symbol"]
        direction = str(row["direction"] or "").lower()
        trade_id = row["linked_trade_id"]
        meta = _decode_notes(row["meta"])
        already_alerted = bool(meta.get("stuck_alert_emitted_at"))

        # Look up the linked trade FIRST — we need its account_id
        # for the position cross-check and we'll need the row again
        # for the cascade write below. One DB read; two uses.
        trade_row = None
        try:
            db_conn = db.connect()
            try:
                db_conn.row_factory = __import__("sqlite3").Row
                trade_row = db_conn.execute(
                    "SELECT id, status, notes, account_id "
                    "FROM trades WHERE id=?",
                    (trade_id,),
                ).fetchone()
            finally:
                db_conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_watchdog_stuck_strategies: trade lookup failed for "
                "trade_id=%s: %s",
                trade_id, exc,
            )
            summary["errors"] += 1
            # Without the trade row we can't position-check; defer
            # rather than force-clearing blind.
            continue

        # Position cross-check: if Bybit reports the package's
        # (symbol, direction) is still alive on the exchange, the
        # trade is NOT actually stuck — just patient (vwap waits
        # hours for mean reversion to bring price back to VWAP). A
        # blind force-clear here cascades a perfectly good trade to
        # ``orphaned`` and strands the position at the exchange,
        # which is the bug surfaced in #574 / #582.
        #
        # Read failure or missing cfg → defer conservatively (don't
        # force-clear on a half-known view of the world). The
        # operator can act manually if Bybit stays unreachable.
        aid = str(trade_row["account_id"] or "") if trade_row else ""
        cfg = cfgs.get(aid) if aid else None

        position_alive: Optional[bool] = None  # None = unknown / skip
        if cfg is not None and direction:
            if aid not in positions_cache:
                positions_cache[aid] = account_open_positions(cfg)
            pos = positions_cache[aid]
            if pos is None:
                position_alive = None  # read failure → conservative
            else:
                live_set = _exchange_position_set(pos)
                position_alive = (str(symbol), direction) in live_set

        if position_alive is True:
            # Trade is alive at the exchange — leave the package
            # alone. Emit the alert ONCE so the operator knows the
            # strategy hasn't progressed (e.g. price never reached
            # SL/TP for vwap), but do NOT cascade.
            summary["deferred_position_alive"] += 1
            try:
                # Stamp the meta so subsequent ticks skip the alert.
                if not already_alerted:
                    updated_meta = dict(meta)
                    updated_meta["stuck_alert_emitted_at"] = now_iso
                    updated_meta["stuck_position_alive_seen_at"] = now_iso
                    db.update_order_package(pkg_id, {"meta": updated_meta})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_watchdog_stuck_strategies: meta-stamp failed for "
                    "pkg_id=%s: %s",
                    pkg_id, exc,
                )
                summary["errors"] += 1
            if not already_alerted:
                try:
                    enqueue_stuck_strategy_alert(
                        strategy=str(strategy or "unknown"),
                        symbol=str(symbol or "?"),
                        order_package_id=str(pkg_id),
                        db_trade_id=trade_id,
                        stuck_minutes=int(threshold_minutes),
                        auto_cleared=False,
                    )
                    summary["alerted"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "_watchdog_stuck_strategies: alert enqueue failed "
                        "for pkg_id=%s: %s",
                        pkg_id, exc,
                    )
                    summary["errors"] += 1
            continue

        if position_alive is None:
            # Read failure (or no cfg). Defer to next tick — better
            # to let a stale package linger one more cycle than to
            # force-clear blind on an exchange we can't see.
            summary["skipped_position_read_failed"] += 1
            continue

        # Position is genuinely flat at the exchange — true orphan.
        # Force-close the package + cascade the trade as before.
        try:
            updated_meta = dict(meta)
            updated_meta.setdefault("stuck_alert_emitted_at", now_iso)
            updated_meta["stuck_force_cleared_at"] = now_iso
            updated_meta["stuck_force_cleared_by"] = "stuck_strategy_watchdog"
            db.update_order_package(pkg_id, {
                "status": "closed",
                "close_reason": "stuck_strategy_watchdog",
                "meta": updated_meta,
            })
            summary["auto_cleared"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_watchdog_stuck_strategies: package force-close failed "
                "for pkg_id=%s: %s",
                pkg_id, exc,
            )
            summary["errors"] += 1

        # Cascade the linked trade if it's still open.
        try:
            if trade_row and str(trade_row["status"]) == "open":
                trade_notes = _decode_notes(trade_row["notes"])
                trade_notes.update({
                    "orphaned_at": now_iso,
                    "orphaned_by": "stuck_strategy_watchdog",
                    "orphaned_reason": (
                        "watchdog — package stuck > "
                        f"{int(threshold_minutes)} min; gate was blocked"
                    ),
                })
                db.update_trade(int(trade_row["id"]), {
                    "status": "orphaned",
                    "exit_reason": "stuck_strategy_watchdog",
                    "notes": json.dumps(trade_notes, ensure_ascii=False)[:500],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_watchdog_stuck_strategies: trade cascade failed for "
                "trade_id=%s: %s",
                trade_id, exc,
            )
            summary["errors"] += 1

        # Emit the alert on first sighting.
        if not already_alerted:
            try:
                enqueue_stuck_strategy_alert(
                    strategy=str(strategy or "unknown"),
                    symbol=str(symbol or "?"),
                    order_package_id=str(pkg_id),
                    db_trade_id=trade_id,
                    stuck_minutes=int(threshold_minutes),
                    auto_cleared=True,
                )
                summary["alerted"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_watchdog_stuck_strategies: alert enqueue failed "
                    "for pkg_id=%s: %s",
                    pkg_id, exc,
                )
                summary["errors"] += 1

    if (
        summary["auto_cleared"]
        or summary["alerted"]
        or summary["deferred_position_alive"]
        or summary["skipped_position_read_failed"]
    ):
        logger.info(
            "_watchdog_stuck_strategies: checked=%d alerted=%d "
            "auto_cleared=%d deferred_position_alive=%d "
            "skipped_position_read_failed=%d errors=%d (threshold=%d min)",
            summary["checked"], summary["alerted"], summary["auto_cleared"],
            summary["deferred_position_alive"],
            summary["skipped_position_read_failed"],
            summary["errors"], int(threshold_minutes),
        )
    return summary


# ---------------------------------------------------------------------------
# PR 5 (2026-05-10): the spot-margin orphan reconcilers (S-055 borrow-
# orphan + S-060 position-orphan) lived here. They only operated on
# spot-margin accounts (``_is_spot_margin_cfg`` filter) which no longer
# exist post-PR-3. Both reconciler loops and their helpers
# (``_is_spot_margin_cfg``, ``_account_has_recent_trade``,
# ``_open_trades_for_account``, ``_open_trade_backs_borrow``,
# ``_open_trade_backs_position``, ``_emit_borrow_orphan_audit``,
# ``_emit_position_orphan_audit``, ``_build_client_for_cfg``,
# ``_reconcile_orphan_borrows``, ``_reconcile_orphan_positions``)
# were removed. The standard ``_reconcile_open_trades`` reconciler
# (linear-perp orphan detection) is unaffected.
# ---------------------------------------------------------------------------


def _extract_package_id(notes_raw: Optional[str]) -> Optional[str]:
    """Pull ``order_package_id`` out of the trades.notes JSON blob if
    present. Best-effort — returns None on any decode failure."""
    if not notes_raw:
        return None
    try:
        notes = json.loads(notes_raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(notes, dict):
        return None
    return notes.get("order_package_id") or notes.get("trade_id")


def _extract_trade_id_from_notes(notes_raw: Optional[str]) -> Optional[str]:
    """Pull the exchange's order id out of ``trades.notes.trade_id``.

    Returns the stripped string when present, ``None`` otherwise.
    """
    if not notes_raw:
        return None
    try:
        notes = json.loads(notes_raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(notes, dict):
        return None
    tid = notes.get("trade_id")
    if tid is None:
        return None
    s = str(tid).strip()
    return s or None


def _is_numeric_order_id(trade_id: str) -> bool:
    """A real Bybit V5 orderId is a digit-only string (UUID-shaped on
    a few endpoints, but the executor stamps the digit form). The
    journal also stores synthesised ids like ``rejected-<hex>`` and
    ``dry-<hex>`` for never-placed orders — those are non-numeric and
    must be skipped by the SSOT reconciler.
    """
    return bool(trade_id) and trade_id.isdigit()


def _close_trade_from_order_status(
    db, row: Dict[str, Any], order_status: Dict[str, Any],
) -> None:
    """Mark a trade row 'closed' using the real fill price + exec time
    from Bybit's order history. Cascades the linked ``order_packages``
    row (close_reason='reconciler_filled').

    Replaces the legacy reconciler-close path that left
    ``exit_price=NULL`` and forced downstream PnL math to depend on
    ghost-row cleanup.
    """
    avg_price = float(order_status.get("avg_price") or 0.0)
    exec_time = order_status.get("exec_time")
    closed_at = (
        str(exec_time) if exec_time
        else datetime.now(timezone.utc).isoformat()
    )
    notes = _decode_notes(row.get("notes"))
    notes.update({
        "closed_at": closed_at,
        "closed_by": "monitor_reconciler",
        "closed_reason":
            "reconciler — Bybit reports order filled and position flat",
    })
    updates: Dict[str, Any] = {
        "status": "closed",
        "exit_reason": "reconciler_filled",
        "notes": json.dumps(notes, ensure_ascii=False)[:500],
    }
    if avg_price > 0:
        updates["exit_price"] = avg_price
    db.update_trade(int(row["id"]), updates)

    pkg_id = _extract_package_id(row.get("notes"))
    if pkg_id:
        try:
            db.update_order_package(pkg_id, {
                "status": "closed",
                "close_reason": "reconciler_filled",
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_close_trade_from_order_status: package cascade failed "
                "for pkg_id=%s linked to trade_id=%s: %s",
                pkg_id, row.get("id"), exc,
            )


def _mark_orphaned(db, row: Dict[str, Any]) -> None:
    """Mark a trade as orphaned + cascade the linked order_packages row.

    Both writes are best-effort — a failure on the package cascade
    does not undo the trade-row update. The ``trades`` schema has no
    ``updated_at`` column (only ``created_at``), so the timestamp
    trail rides on the ``notes`` JSON instead — mirrors the existing
    ``notebooks/operator/cleanup_ghost_trades.ipynb`` markers
    (``orphaned_at`` / ``orphaned_by`` / ``orphaned_reason``) so an
    operator can grep / SQL on the same field for both manual and
    automated sweeps.

    The package cascade is retried once (two attempts total) before
    giving up. A final failure writes a sticky ``orphan_cascade_failed``
    audit row via ``log_signal`` — without it the strategy-monocle gate
    in ``pipeline.py::_has_open_package_for_strategy`` stays stuck open
    and every future signal for the strategy is silently blocked.
    The companion ``_sweep_stuck_linked_packages`` watchdog is the
    second line of defence.
    """
    now = datetime.now(timezone.utc).isoformat()
    notes = _decode_notes(row.get("notes"))
    notes.update({
        "orphaned_at": now,
        "orphaned_by": "monitor_reconciler",
        "orphaned_reason": "reconciler — DB-open trade not present in exchange open-positions",
    })
    db.update_trade(int(row["id"]), {
        "status": "orphaned",
        "exit_reason": "reconciler",
        "notes": json.dumps(notes, ensure_ascii=False)[:500],
    })
    pkg_id = _extract_package_id(row.get("notes"))
    if not pkg_id:
        return

    last_exc: Optional[BaseException] = None
    for attempt in (1, 2):
        try:
            db.update_order_package(pkg_id, {
                "status": "closed",
                "close_reason": "reconciler",
            })
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "_mark_orphaned: package cascade failed (attempt %d/2) "
                "for pkg_id=%s linked to trade_id=%s: %s",
                attempt, pkg_id, row.get("id"), exc,
            )

    if last_exc is not None:
        _emit_orphan_cascade_failed_audit(
            pkg_id=pkg_id,
            trade_id=row.get("id"),
            account_id=row.get("account_id"),
            symbol=row.get("symbol"),
            direction=row.get("direction"),
            error=str(last_exc),
        )


def _emit_orphan_cascade_failed_audit(
    *,
    pkg_id: str,
    trade_id: Any,
    account_id: Any,
    symbol: Any,
    direction: Any,
    error: str,
) -> None:
    """Sticky audit row for a package cascade that failed twice.

    Without this row the cascade loss is silent: the trade is marked
    ``orphaned`` but the package row stays ``status='open'``, leaving
    the strategy-monocle gate stuck and every future signal blocked
    until manual intervention. Best-effort — never raises.
    """
    try:
        from src.utils.signal_audit_logger import log_signal
        log_signal({
            "event": "outcome",
            "action": "orphan_cascade_failed",
            "status": "failed",
            "order_package_id": pkg_id,
            "db_trade_id": trade_id,
            "account_id": account_id,
            "symbol": symbol,
            "direction": direction,
            "reason": (
                "package cascade failed twice — strategy gate may stay "
                "stuck until _sweep_stuck_linked_packages or operator "
                "clears it"
            ),
            "error": error,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_emit_orphan_cascade_failed_audit: write failed for pkg_id=%s: %s",
            pkg_id, exc,
        )


def _decode_notes(notes_raw: Optional[str]) -> Dict[str, Any]:
    """Best-effort decode of a ``trades.notes`` JSON blob; returns an
    empty dict on missing / malformed content."""
    if not notes_raw:
        return {}
    try:
        loaded = json.loads(notes_raw)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


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
                    # Pass strategy_name so the fetcher can fall back to
                    # the per-strategy timeframe from strategies.yaml
                    # when ``meta.timeframe`` is missing — needed for
                    # legacy package rows written before the meta key
                    # was added (2026-05-09). Without the fallback those
                    # rows never receive candles and monitor() can't
                    # emit a close verdict.
                    candles = ohlcv_fetcher(
                        normalised.get("symbol"),
                        (normalised.get("meta") or {}).get("timeframe"),
                        strategy_name,
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

    # BUG-042 PR 2: write-back reconciler. No-op when
    # MONITOR_RECONCILE_ENABLED is false (the default for PR 2);
    # PR 3 of the sprint flips that on after a soak window.
    try:
        recon = _reconcile_open_trades(db)
        if recon.get("orphaned") or recon.get("errors"):
            summaries["__reconciler__"] = recon
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: reconciler raised: %s", exc)

    # 2026-05-11 incident PR: reverse reconciler. Walks the OTHER
    # direction — every exchange-side open position is checked for a
    # matching trades.status='open' row, and an orphan (Bybit-known,
    # journal-unknown) is either alerted, ADOPTed into the journal,
    # or market-closed depending on ORPHAN_POSITION_POLICY. Same
    # MONITOR_RECONCILE_ENABLED gate; runs after the forward reconciler
    # so the journal mutations from forward-orphan closures don't
    # produce spurious reverse-orphan adoptions on the same tick.
    try:
        reverse_recon = _reconcile_orphan_exchange_positions(db)
        if (
            reverse_recon.get("orphans_found")
            or reverse_recon.get("closed_disappeared")
            or reverse_recon.get("errors")
        ):
            summaries["__reverse_reconciler__"] = reverse_recon
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: reverse reconciler raised: %s", exc,
        )

    # BUG-049: sweep order_packages that are status='open' but have no
    # linked_trade_id (never executed). Gated by the same
    # MONITOR_RECONCILE_ENABLED flag as _reconcile_open_trades.
    try:
        _sweep_unlinked_packages(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: unlinked-pkg sweep raised: %s", exc)

    # Sweep order_packages that are status='open' AND linked to a trade
    # that has already reached a terminal status (orphaned,
    # exchange_rejected, closed, rejected, rejected_too_small). These
    # are the cascade-leak rows that keep the strategy-monocle gate
    # stuck and silently block every future signal for the strategy.
    # Gated by MONITOR_RECONCILE_ENABLED (helper checks).
    try:
        _sweep_stuck_linked_packages(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: stuck-linked-pkg sweep raised: %s", exc)

    # Last line of defence: stuck-strategy watchdog. Catches packages
    # the orphan reconciler + linked-package sweep both missed (e.g.
    # the linked trade is genuinely status='open' but the strategy
    # somehow can't progress). Force-clears the package + cascades
    # the trade row + emits a high-priority operator alert.
    # Gated by MONITOR_RECONCILE_ENABLED (helper checks).
    try:
        watchdog_summary = _watchdog_stuck_strategies(db)
        if watchdog_summary.get("alerted") or watchdog_summary.get("errors"):
            summaries["__stuck_strategy_watchdog__"] = watchdog_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: stuck-strategy watchdog raised: %s", exc,
        )

    # PR 5 (2026-05-10): the S-055 borrow-orphan and S-060
    # position-orphan reconciler calls lived here. They only swept
    # spot-margin accounts (none exist post-PR-3) and were deleted
    # alongside their loop bodies.

    # S-067 follow-up #3 Phase-2: closed → exchange-flat invariant check.
    # Gated by ``CLOSED_FLAT_INVARIANT_ENABLED`` env (default false).
    # Alert-only — promotion to auto-flatten is a separate Tier-2 PR after
    # a 7-day soak. The helper never raises; the orphan reconciler above
    # remains the eventual safety net during the soak window. See
    # ``docs/claude/closed-flat-invariant.md`` for the full design.
    from src.runtime._closed_flat_wiring import maybe_run_closed_flat_check
    maybe_run_closed_flat_check(db, summaries)
