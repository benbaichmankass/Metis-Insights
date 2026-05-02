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

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
    from src.data_layer.database import Database
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


def _apply_update(db, open_pkg: dict, verdict: Dict[str, Any],
                  summary: _StrategyTickSummary) -> None:
    """Translate a non-None monitor verdict into DB writes.

    Verdict shapes:
      - ``{"sl": float}`` or ``{"tp": float}`` → update_order_package
      - ``{"action": "close", "reason": str}`` → close the package
        AND update the linked trade row.

    Each branch is wrapped; one failing write doesn't break the
    rest of the tick.
    """
    pkg_id = open_pkg.get("order_package_id")
    action = (verdict or {}).get("action")
    if action == "close":
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
            close_iso = datetime.now(timezone.utc).isoformat()
            close_updates = {
                "status": "closed",
                "exit_reason": reason,
                "exit_price": (verdict or {}).get("exit_price"),
            }
            close_updates = {k: v for k, v in close_updates.items() if v is not None}

            linked_trade_id = open_pkg.get("linked_trade_id")
            if linked_trade_id:
                db.update_trade(int(linked_trade_id), close_updates)
            else:
                # Fallback close-by-symbol-and-strategy.
                _close_trade_by_match(
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


def _close_trade_by_match(db, *, strategy: Optional[str], symbol: Optional[str],
                          updates: Dict[str, Any]) -> None:
    """Best-effort: find the most-recent open trade row matching the
    strategy + symbol and apply ``updates``. Used when the order
    package row doesn't yet carry a linked_trade_id (the link is a
    follow-up to S-029 PR2).
    """
    if not strategy or not symbol:
        return
    rows = db.get_trades(
        filters={"strategy_name": strategy, "symbol": symbol, "status": "open"},
        limit=1,
    )
    if not rows:
        return
    trade_id = rows[0].get("id")
    if trade_id is not None:
        db.update_trade(int(trade_id), updates)


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

    return summaries
