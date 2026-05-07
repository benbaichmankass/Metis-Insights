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

        # Exchange-side close — env-gated. Operator flips
        # MONITOR_APPLY_TO_EXCHANGE=true on the trader's systemd unit
        # when ready to leave shadow mode.
        if _apply_to_exchange_enabled() and matched_trade:
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

    # Exchange-side modify — env-gated. Same shadow-mode contract as
    # the close path above. Looks up the matched trade row to get
    # account_id + symbol; bypasses the exchange call when no trade
    # row matches (the package may have been dispatched but the
    # account_id linkage hasn't been wired in yet).
    if _apply_to_exchange_enabled():
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


def _apply_to_exchange_enabled() -> bool:
    """``MONITOR_APPLY_TO_EXCHANGE`` is the operator-controlled flag
    that flips PR3's "shadow mode" (DB-only) into live mode (also
    talks to the exchange). Defaults to **False** so an unconfigured
    deploy never accidentally modifies live orders. Operator sets the
    env on the trader's systemd unit when ready.
    """
    raw = os.environ.get("MONITOR_APPLY_TO_EXCHANGE", "false")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


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
# Monitor-loop write-back reconciler — BUG-042 PR 2
# (CLAUDE.md § Architecture rules § 5 "Live by default + tell-me-if-not")
# ---------------------------------------------------------------------------
#
# The trade lifecycle is a two-sided contract: ``execute_pkg`` writes
# ``status='open'`` to the DB on placement, and the exchange
# independently closes positions on TP / SL / manual flatten. Without
# a reconciler the two views silently diverge — closed positions
# linger as ``status='open'`` in the trade journal forever (BUG-041's
# pre-#357 ghost-row pattern). The cleanup notebook (PR #367) is the
# manual one-shot remediation; this is the always-on automated
# equivalent.
#
# Behaviour:
#   1. SELECT id, account_id, symbol, direction FROM trades
#      WHERE status='open' AND is_backtest=0.
#   2. Group by account_id.
#   3. Per account: load the matching account dict from accounts.yaml,
#      skip dry-run accounts (no exchange to read), and call
#      ``account_open_positions``.
#   4. For each DB-open row whose (symbol, side) is NOT in the
#      exchange's open-positions list:
#        - UPDATE trades SET status='orphaned',
#          exit_reason='reconciler', updated_at=NOW()
#        - Cascade: UPDATE order_packages SET status='closed',
#          close_reason='reconciler', updated_at=NOW()
#          WHERE linked_trade_id=?
#        - Enqueue one diagnostic ping per orphan (cap at 10 per
#          tick + one roll-up for the rest).
#
# Skip rules (no orphan sweep on this account):
#   - account.mode != 'live' (dry-run / paper) — no exchange to read.
#   - account_open_positions returned None (creds missing or
#     exchange-side error) — don't orphan rows just because we
#     couldn't read.
#
# Gated by env var ``MONITOR_RECONCILE_ENABLED`` (default ``false``).
# PR 3 of the BUG-042 sprint flips the default to ``true`` after a
# soak window confirms the dry-mode behaviour is stable.

_ORPHAN_PING_CAP = 10

# Reconcile-only side of the schema: what we can pull from the trades
# row to match against the exchange snapshot.
_RECONCILE_TRADE_COLS = (
    "id", "account_id", "symbol", "direction", "notes"
)


def _reconcile_enabled() -> bool:
    """Read ``MONITOR_RECONCILE_ENABLED`` at call time so an operator
    flag flip takes effect within the next tick without restarting
    the trader. Default ``false`` for PR 2; PR 3 flips it on."""
    raw = os.environ.get("MONITOR_RECONCILE_ENABLED", "false")
    return str(raw).strip().lower() == "true"


def _load_account_cfgs_for_reconcile() -> Dict[str, Dict[str, Any]]:
    """Return ``{account_id: account_cfg_dict}`` from accounts.yaml.

    Account dicts carry the keys ``account_open_positions`` reads
    (``account_id``, ``exchange``, ``api_key_env``, ``api_secret_env``,
    ``mode``). Best-effort — any read failure returns an empty dict so
    the reconciler runs as a no-op rather than orphaning trades on a
    config-load error.
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
        }
    return out


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


def _reconcile_open_trades(db) -> Dict[str, int]:
    """Sweep DB-open trades whose exchange counterpart is flat.

    Returns a summary dict
    ``{checked, orphaned, skipped_dry, skipped_no_creds, skipped_no_cfg,
       errors}`` so the caller (run_monitor_tick) can emit an INFO log
    line for every tick that touched at least one row.

    No-op when ``MONITOR_RECONCILE_ENABLED`` is unset or false. Best-
    effort — every step is wrapped; one bad row never aborts the
    sweep.
    """
    summary = {
        "checked": 0,
        "orphaned": 0,
        "skipped_dry": 0,
        "skipped_no_creds": 0,
        "skipped_no_cfg": 0,
        "errors": 0,
    }
    if not _reconcile_enabled():
        return summary

    # 1. Pull open trade rows. Inline SQL — no Database helper exists
    # for "all open across accounts" and writing one would be premature
    # API surface for a single caller.
    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, notes "
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

    # 2. Group by account_id.
    by_account: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        aid = str(r["account_id"] or "unknown")
        by_account.setdefault(aid, []).append(dict(r))

    # 3. Per-account exchange snapshot + orphan sweep.
    from src.units.accounts.clients import account_open_positions
    from src.runtime.execution_diagnostics import (
        enqueue_orphan_reconciliation,
        enqueue_orphan_rollup,
    )

    orphan_pings_emitted = 0
    orphan_pings_suppressed = 0

    for aid, trade_rows in by_account.items():
        cfg = cfgs.get(aid)
        if cfg is None:
            # Account in DB but not in accounts.yaml — could be a
            # disabled / removed account. Don't orphan its rows; the
            # operator can clean up manually.
            summary["skipped_no_cfg"] += len(trade_rows)
            continue
        if str(cfg.get("mode") or "live").lower() in {"dry", "dry_run", "dry-run", "paper"}:
            summary["skipped_dry"] += len(trade_rows)
            continue

        positions = account_open_positions(cfg)
        if positions is None:
            # Creds missing / exchange-side error → skip (don't orphan
            # rows just because we couldn't read).
            summary["skipped_no_creds"] += len(trade_rows)
            continue

        live_set = _exchange_position_set(positions)
        for row in trade_rows:
            sym = row["symbol"]
            side = str(row["direction"] or "").lower()
            if (sym, side) in live_set:
                # Still live on exchange — leave it alone.
                continue
            # Orphan match: DB-open + exchange-flat.
            try:
                _mark_orphaned(db, row)
                summary["orphaned"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_reconcile_open_trades: mark_orphaned failed for "
                    "trade_id=%s account=%s symbol=%s: %s",
                    row.get("id"), aid, sym, exc,
                )
                summary["errors"] += 1
                continue

            # Diagnostic ping (per-orphan cap + roll-up).
            if orphan_pings_emitted < _ORPHAN_PING_CAP:
                enqueue_orphan_reconciliation(
                    account=aid,
                    symbol=str(sym),
                    side=side,
                    db_trade_id=row.get("id"),
                    linked_package_id=_extract_package_id(row.get("notes")),
                )
                orphan_pings_emitted += 1
            else:
                orphan_pings_suppressed += 1

    if orphan_pings_suppressed:
        enqueue_orphan_rollup(suppressed_count=orphan_pings_suppressed)

    if summary["orphaned"] or summary["errors"]:
        logger.info(
            "_reconcile_open_trades: checked=%d orphaned=%d skipped_dry=%d "
            "skipped_no_creds=%d skipped_no_cfg=%d errors=%d",
            summary["checked"], summary["orphaned"], summary["skipped_dry"],
            summary["skipped_no_creds"], summary["skipped_no_cfg"],
            summary["errors"],
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
    if pkg_id:
        try:
            db.update_order_package(pkg_id, {
                "status": "closed",
                "close_reason": "reconciler",
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_mark_orphaned: package cascade failed for pkg_id=%s "
                "linked to trade_id=%s: %s",
                pkg_id, row.get("id"), exc,
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
                    candles = ohlcv_fetcher(
                        normalised.get("symbol"),
                        (normalised.get("meta") or {}).get("timeframe"),
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

    # BUG-049: sweep order_packages that are status='open' but have no
    # linked_trade_id (never executed). Gated by the same
    # MONITOR_RECONCILE_ENABLED flag as _reconcile_open_trades.
    try:
        _sweep_unlinked_packages(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: unlinked-pkg sweep raised: %s", exc)

    return summaries
